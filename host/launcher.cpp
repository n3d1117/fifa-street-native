#include "tarzan-app.hpp"
#include "metal-host.hpp"
#include "experimental-settings.hpp"
#include "ps2_runtime.h"
#include "runtime/ps2_host_arena.h"

#include <array>
#include <cstring>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include <mach-o/dyld.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
void configureRuntimeArena(const std::filesystem::path& elfPath)
{
    // Runtime callbacks must not share the game's heap or its stack at RAM's top.
    constexpr uint32_t arenaBegin = 0x180000u;
    constexpr uint32_t arenaEnd = 0x200000u;
    std::ifstream file(elfPath, std::ios::binary);
    std::array<unsigned char, 52> header{};
    if (!file.read(reinterpret_cast<char*>(header.data()), header.size()) ||
        std::memcmp(header.data(), "\177ELF", 4) != 0 || header[4] != 1 || header[5] != 1)
        throw std::runtime_error("Runtime arena requires a valid little-endian ELF32 file");
    const auto u16 = [](const unsigned char* p) { return uint32_t(p[0]) | (uint32_t(p[1]) << 8); };
    const auto u32 = [](const unsigned char* p) {
        return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
    };
    const uint32_t offset = u32(header.data() + 28);
    const uint32_t stride = u16(header.data() + 42);
    const uint32_t count = u16(header.data() + 44);
    if (stride < 32 || count == 0)
        throw std::runtime_error("ELF has no valid program headers for runtime arena validation");
    for (uint32_t index = 0; index < count; ++index)
    {
        std::array<unsigned char, 32> program{};
        file.seekg(uint64_t(offset) + uint64_t(index) * stride);
        if (!file.read(reinterpret_cast<char*>(program.data()), program.size()))
            throw std::runtime_error("ELF program header is truncated");
        if (u32(program.data()) != 1u || u32(program.data() + 20) == 0u)
            continue;
        const uint32_t start = u32(program.data() + 8) & 0x1fffffffu;
        const uint64_t end = uint64_t(start) + u32(program.data() + 20);
        if (start < arenaEnd && end > arenaBegin)
            throw std::runtime_error("Game ELF overlaps the reserved runtime arena");
    }
    // Tarzan's libc malloc is HLE-bound to guestMalloc; retain that shared
    // allocator and isolate only runtime callback stacks below the executable.
    ps2SetRuntimeArena(0u, 0u, arenaBegin, arenaEnd);
}

// When this executable is Contents/MacOS/<name> of an app bundle, the game
// data ships in Contents/Resources/game. Empty path when not in a bundle.
std::filesystem::path bundledGameElf()
{
    uint32_t size = 0;
    _NSGetExecutablePath(nullptr, &size);
    std::string exe(size, '\0');
    if (size == 0 || _NSGetExecutablePath(exe.data(), &size) != 0)
        return {};
    const std::filesystem::path macos = std::filesystem::path(exe.c_str()).lexically_normal().parent_path();
    const std::filesystem::path elf = macos.parent_path() / "Resources" / "game" / "SLES_503.50";
    std::error_code ec;
    if (macos.filename() == "MacOS" && std::filesystem::exists(elf, ec))
        return elf;
    return {};
}

// A GUI launch (LaunchServices) hands us /dev/null for stdout/stderr; keep the
// runtime log in the user directory instead. Terminals and pipes are left alone.
void redirectLogsIfDetached(const std::filesystem::path &userDirectory)
{
    struct stat devNull{}, err{};
    if (stat("/dev/null", &devNull) != 0 || fstat(STDERR_FILENO, &err) != 0 ||
        !S_ISCHR(err.st_mode) || err.st_rdev != devNull.st_rdev)
        return;
    std::error_code ec;
    const std::filesystem::path logs = userDirectory / "Logs";
    std::filesystem::create_directories(logs, ec);
    const int fd = open((logs / "native.log").c_str(), O_WRONLY | O_CREAT | O_APPEND, 0600);
    if (fd < 0)
        return;
    dup2(fd, STDOUT_FILENO);
    dup2(fd, STDERR_FILENO);
    close(fd);
}

struct LaunchOptions
{
    std::filesystem::path elfPath;
    std::filesystem::path userDirectory;
    bool userDirectorySpecified = false;
};

std::filesystem::path defaultUserDirectory()
{
    const char *home = std::getenv("HOME");
    if (!home || home[0] == '\0')
    {
        throw std::runtime_error("HOME is not set; pass --user-dir explicitly");
    }
    return std::filesystem::path(home) / "Library" / "Application Support" / "TarzanFreeridePS2";
}

// A relative default ELF is looked up from the executable's directory upward,
// then from the working directory upward, so no checkout path is baked in.
std::filesystem::path resolveDefaultElf(const std::filesystem::path &relative)
{
    if (relative.is_absolute())
    {
        return relative;
    }
    std::vector<std::filesystem::path> starts;
    uint32_t size = 0;
    _NSGetExecutablePath(nullptr, &size);
    std::string exe(size, '\0');
    if (size != 0 && _NSGetExecutablePath(exe.data(), &size) == 0)
    {
        starts.push_back(std::filesystem::path(exe.c_str()).lexically_normal().parent_path());
    }
    std::error_code ec;
    starts.push_back(std::filesystem::current_path(ec));
    for (std::filesystem::path dir : starts)
    {
        while (!dir.empty())
        {
            std::filesystem::path candidate = dir / relative;
            if (std::filesystem::exists(candidate, ec))
            {
                return candidate;
            }
            std::filesystem::path parent = dir.parent_path();
            if (parent == dir)
            {
                break;
            }
            dir = parent;
        }
    }
    return relative;
}

void printUsage(const char *program)
{
    std::cerr << "Usage: " << program << " [--user-dir DIRECTORY] [GAME.ELF]\n";
}

bool parseOptions(int argc, char *argv[], LaunchOptions &options)
{
    for (int i = 1; i < argc; ++i)
    {
        const std::string argument = argv[i] ? argv[i] : "";
        if (argument == "--help" || argument == "-h")
        {
            printUsage(argv[0]);
            return false;
        }
        if (argument == "--user-dir")
        {
            if (options.userDirectorySpecified || i + 1 >= argc || !argv[i + 1] ||
                argv[i + 1][0] == '\0' || std::string(argv[i + 1]).starts_with('-'))
            {
                throw std::runtime_error("--user-dir requires one non-empty directory and may be specified once");
            }
            options.userDirectory = argv[++i];
            options.userDirectorySpecified = true;
            continue;
        }
        if (argument.starts_with('-'))
        {
            throw std::runtime_error("Unknown option: " + argument);
        }
        if (!options.elfPath.empty())
        {
            throw std::runtime_error("Only one game ELF may be specified");
        }
        options.elfPath = argument;
    }

    if (options.elfPath.empty())
        options.elfPath = bundledGameElf();
#if defined(PS2X_DEFAULT_BOOT_ELF)
    if (options.elfPath.empty())
    {
        options.elfPath = resolveDefaultElf(PS2X_DEFAULT_BOOT_ELF);
    }
#endif
    if (options.elfPath.empty())
    {
        printUsage(argv[0]);
        throw std::runtime_error("No game ELF was specified");
    }
    if (options.userDirectory.empty())
    {
        options.userDirectory = defaultUserDirectory();
    }
    return true;
}

void setupTerminateLogger()
{
    std::set_terminate([]()
                       {
                           std::cerr << "[terminate] unhandled exception" << std::endl;
                           const std::exception_ptr error = std::current_exception();
                           if (error)
                           {
                               try
                               {
                                   std::rethrow_exception(error);
                               }
                               catch (const std::exception &exception)
                               {
                                   std::cerr << "[terminate] " << exception.what() << std::endl;
                               }
                               catch (...)
                               {
                                   std::cerr << "[terminate] non-standard exception" << std::endl;
                               }
                           }
                           std::abort(); });
}
}


int tarzan_run(const TarzanConfig *config)
{
    try
    {
        if (!config || !config->elf_path || !config->user_dir)
            throw std::runtime_error("tarzan_run needs an ELF path and a user directory");
        std::error_code error;
        const std::filesystem::path elfPath =
            std::filesystem::absolute(config->elf_path, error).lexically_normal();
        if (error)
            throw std::runtime_error("Unable to resolve game ELF path: " + error.message());
        const std::filesystem::path userDirectory =
            std::filesystem::absolute(config->user_dir, error).lexically_normal();
        if (error)
            throw std::runtime_error("Unable to resolve user directory: " + error.message());

        redirectLogsIfDetached(userDirectory);
        // Settings: the user's file plus the documented debug overrides, loaded once.
        tarzan_experiments_configure(userDirectory.c_str());
        PS2HostConfig hostConfig;
        // Pace hardware time from the host clock. Estimated block costs must
        // neither slow it down nor accelerate audio during movie playback.
        hostConfig.wallClock = tarzan_experiments_wall_clock() != 0;
        hostConfig.vsyncHz = tarzan_experiments_high_fps() ? uint32_t(tarzan_experiments_target_fps()) : 0u;
        // Tarzan's relocated IOP modules end below 0x80000. Reserve that
        // region and leave room for the native 320 KiB movie sound buffer.
        // The IOP loader checks every relocated module against this boundary.
        hostConfig.iopHeapFloor = 0x80000u;
        ps2SetHostConfig(hostConfig);
        // The arena is only a range handed to the runtime allocator; it needs no
        // fresh process image, just to be set before the runtime initializes.
        configureRuntimeArena(elfPath);
        // Never destroyed: teardown order of the runtime's threads is not worth
        // risking after the window is gone; the process exits right after.
        PS2Runtime &runtime = *new PS2Runtime;
        if (!runtime.initialize("TarzanFreeride PS2 Native macOS"))
        {
            std::cerr << "Failed to initialize PS2 runtime" << std::endl;
            return 1;
        }
        if (!runtime.loadELF(elfPath.string()))
        {
            std::cerr << "Failed to load game ELF: " << elfPath << std::endl;
            return 1;
        }

        const std::filesystem::path memoryCardDirectory = userDirectory / "memcards";
        std::filesystem::create_directories(memoryCardDirectory, error);
        if (error)
            throw std::runtime_error("Unable to create memory card directory: " + error.message());

        // loadELF derives paths from the ELF directory, so apply the user-owned
        // memory card location after loading it and before the runtime starts.
        PS2Runtime::IoPaths ioPaths = PS2Runtime::getIoPaths();
        ioPaths.mcRoot = memoryCardDirectory;
        PS2Runtime::setIoPaths(ioPaths);

        std::cout << "Game ELF: " << elfPath << std::endl;
        std::cout << "Memory cards: " << memoryCardDirectory << std::endl;
        runtime.run();
        std::cout.flush();
        std::cerr.flush();
        return 0;
    }
    catch (const std::exception &error)
    {
        std::cerr << "[launcher] " << error.what() << std::endl;
    }
    catch (...)
    {
        std::cerr << "[launcher] unknown fatal exception" << std::endl;
    }
    std::cout.flush();
    std::cerr.flush();
    return 1;
}

void tarzan_stop(void)
{
    tarzan_metal_host_request_close();
}

int main(int argc, char *argv[])
{
    setupTerminateLogger();
    int status = 1;
    try
    {
        LaunchOptions options;
        if (!parseOptions(argc, argv, options))
            return 0;
        const std::string elf = options.elfPath.string(), user = options.userDirectory.string();
        TarzanConfig config{elf.c_str(), user.c_str()};
        status = tarzan_run(&config);
    }
    catch (const std::exception &error)
    {
        std::cerr << "[launcher] " << error.what() << std::endl;
    }
    std::cout.flush();
    std::cerr.flush();
    std::_Exit(status);
}
