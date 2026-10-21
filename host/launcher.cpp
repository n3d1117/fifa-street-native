#include "app.hpp"
#include "metal-host.hpp"
#include "settings.hpp"
#include "ps2_runtime.h"
#include "runtime/ps2_host_arena.h"

#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <exception>
#include <fstream>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include <mach-o/dyld.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

bool ps2x_boot_match_install(PS2Runtime &runtime, const std::filesystem::path &elfPath,
                             const std::filesystem::path &userDir, const std::filesystem::path &realUserDir);
bool ps2x_ra_watch_install(PS2Runtime &runtime, const std::filesystem::path &elfPath);
bool ps2x_poll_idle_install(PS2Runtime &runtime, const std::filesystem::path &elfPath);
bool ps2x_video_mode_install(PS2Runtime&, const std::filesystem::path&, host::VideoMode);
bool ps2x_cheats_install(PS2Runtime &runtime, const std::filesystem::path &elfPath, bool unlockAllVenues);

namespace
{
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
    const std::filesystem::path elf = macos.parent_path() / "Resources" / "game" /
        std::filesystem::path(PS2X_DEFAULT_BOOT_ELF).filename();
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
    return std::filesystem::path(home) / "Library" / "Application Support" / PS2X_APP_EXECUTABLE;
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

// PT_LOAD ranges of a 32-bit little-endian ELF.
std::vector<std::pair<uint32_t, uint32_t>> elfLoadSegments(const std::filesystem::path &elf)
{
    std::vector<std::pair<uint32_t, uint32_t>> segments;
    std::ifstream in(elf, std::ios::binary);
    unsigned char header[52];
    if (!in.read(reinterpret_cast<char *>(header), sizeof(header)))
        return segments;
    uint32_t phoff;
    uint16_t phentsize, phnum;
    std::memcpy(&phoff, header + 28, 4);
    std::memcpy(&phentsize, header + 42, 2);
    std::memcpy(&phnum, header + 44, 2);
    for (uint16_t i = 0; i < phnum; ++i)
    {
        unsigned char entry[32];
        in.seekg(static_cast<std::streamoff>(phoff) + static_cast<std::streamoff>(i) * phentsize);
        if (!in.read(reinterpret_cast<char *>(entry), sizeof(entry)))
            break;
        uint32_t type, vaddr, memsz;
        std::memcpy(&type, entry, 4);
        std::memcpy(&vaddr, entry + 8, 4);
        std::memcpy(&memsz, entry + 20, 4);
        if (type == 1u && memsz != 0u)
            segments.emplace_back(vaddr, vaddr + memsz);
    }
    return segments;
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
    if (options.elfPath.empty())
    {
        options.elfPath = resolveDefaultElf(PS2X_DEFAULT_BOOT_ELF);
    }
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


int ps2x_app_run(const Ps2xAppConfig *config)
{
    try
    {
        if (!config || !config->elf_path || !config->user_dir)
            throw std::runtime_error("ps2x_app_run needs an ELF path and a user directory");
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
        host::configure_settings(userDirectory.c_str());
        const auto pipelineCache = userDirectory / "gs-metal-pipelines.txt";
        if (!std::getenv("RRV_PCSX2_GS_PIPELINE_CACHE"))
            setenv("RRV_PCSX2_GS_PIPELINE_CACHE", pipelineCache.c_str(), 1);
        PS2HostConfig hostConfig;
        // Pace hardware time from the host clock. Estimated block costs must
        // neither slow it down nor accelerate audio during movie playback.
        hostConfig.wallClock = host::active_settings().wallClock;
        ps2SetHostConfig(hostConfig);
        // Runtime-owned RAM (callback stacks, RPC packets, HLE buffers) lives below the ELF, never in the game's heap or stack.
        PS2KernelArena arena;
        if (!ps2KernelArena(elfLoadSegments(elfPath), arena))
            throw std::runtime_error("No kernel RAM range below the ELF's load segments");
        ps2SetRpcPacketPoolBase(arena.rpcPoolBase);
        ps2SetRuntimeArena(arena.heapBase, arena.heapLimit, arena.stackFloor, arena.stackTop);
        // Never destroyed: teardown order of the runtime's threads is not worth
        // risking after the window is gone; the process exits right after.
        PS2Runtime &runtime = *new PS2Runtime;
        if (!runtime.initialize(PS2X_APP_NAME))
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

        {
            // Diagnostic only: inert unless `./rx run --match` set PS2X_BOOT_MATCH.
            std::filesystem::path realUserDirectory;
            try { realUserDirectory = defaultUserDirectory(); } catch (const std::exception &) {}
            ps2x_boot_match_install(runtime, elfPath, userDirectory, realUserDirectory);
        }
        ps2x_video_mode_install(runtime, elfPath, host::active_settings().videoMode);
        ps2x_poll_idle_install(runtime, elfPath);
        ps2x_ra_watch_install(runtime, elfPath);
        ps2x_cheats_install(runtime, elfPath, host::active_settings().unlockAllVenues);
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

void ps2x_app_stop(void)
{
    ps2x_metal_host_request_close();
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
        Ps2xAppConfig config{elf.c_str(), user.c_str()};
        status = ps2x_app_run(&config);
    }
    catch (const std::exception &error)
    {
        std::cerr << "[launcher] " << error.what() << std::endl;
    }
    std::cout.flush();
    std::cerr.flush();
    std::_Exit(status);
}
