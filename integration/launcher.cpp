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

#if defined(PS2X_DEFAULT_BOOT_ELF)
    if (options.elfPath.empty())
    {
        options.elfPath = PS2X_DEFAULT_BOOT_ELF;
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

int main(int argc, char *argv[])
{
    setupTerminateLogger();
    try
    {
        LaunchOptions options;
        if (!parseOptions(argc, argv, options))
        {
            return 0;
        }

        std::error_code error;
        options.elfPath = std::filesystem::absolute(options.elfPath, error).lexically_normal();
        if (error)
        {
            throw std::runtime_error("Unable to resolve game ELF path: " + error.message());
        }
        options.userDirectory = std::filesystem::absolute(options.userDirectory, error).lexically_normal();
        if (error)
        {
            throw std::runtime_error("Unable to resolve user directory: " + error.message());
        }

        if (setenv("TARZAN_PS2_USER_DIR", options.userDirectory.c_str(), 1) != 0)
            throw std::runtime_error("Unable to configure keyboard settings directory");
        // Use KillzoneRecomp's real-time cycle floor for interactive play;
        // preserve an explicit override for deterministic diagnostic runs.
        if (!std::getenv("PS2X_WALLCLOCK") && setenv("PS2X_WALLCLOCK", "1", 0) != 0)
            throw std::runtime_error("Unable to configure real-time game timing");
        // Tarzan's relocated IOP modules end below 0x80000. Reserve that
        // region and leave room for the native 320 KiB movie sound buffer.
        // The IOP loader checks every relocated module against this boundary.
        if (!std::getenv("PS2X_IOP_HEAP_BASE") && setenv("PS2X_IOP_HEAP_BASE", "0x80000", 0) != 0)
            throw std::runtime_error("Unable to configure Tarzan IOP heap");
        configureRuntimeArena(options.elfPath);
        PS2Runtime runtime;
        if (!runtime.initialize("TarzanFreeride PS2 Native macOS"))
        {
            std::cerr << "Failed to initialize PS2 runtime" << std::endl;
            return 1;
        }
        if (!runtime.loadELF(options.elfPath.string()))
        {
            std::cerr << "Failed to load game ELF: " << options.elfPath << std::endl;
            return 1;
        }

        const std::filesystem::path memoryCardDirectory = options.userDirectory / "memcards";
        std::filesystem::create_directories(memoryCardDirectory, error);
        if (error)
        {
            throw std::runtime_error("Unable to create memory card directory: " + error.message());
        }

        // loadELF derives paths from the ELF directory, so apply the user-owned
        // memory card location after loading it and before the runtime starts.
        PS2Runtime::IoPaths ioPaths = PS2Runtime::getIoPaths();
        ioPaths.mcRoot = memoryCardDirectory;
        PS2Runtime::setIoPaths(ioPaths);

        std::cout << "Game ELF: " << options.elfPath << std::endl;
        std::cout << "Memory cards: " << memoryCardDirectory << std::endl;
        runtime.run();
        std::cout.flush();
        std::cerr.flush();
        std::_Exit(0);
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
    std::_Exit(1);
}
