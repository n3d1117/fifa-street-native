#include "common/CocoaTools.h"
#include "common/HTTPDownloader.h"
#include "common/HostSys.h"
#include "common/WindowInfo.h"
#include "pcsx2/CDVD/CDVDdiscReader.h"

#include <libkern/OSCacheControl.h>

namespace CocoaTools
{
bool CreateMetalLayer(WindowInfo*) { return false; }
void DestroyMetalLayer(WindowInfo*) {}
std::optional<float> GetViewRefreshRate(const WindowInfo&) { return std::nullopt; }
void MarkHelpMenu(void*) {}
std::optional<std::string> GetBundlePath() { return std::nullopt; }
std::optional<std::string> GetNonTranslocatedBundlePath() { return std::nullopt; }
std::optional<std::string> MoveToTrash(std::string_view) { return std::nullopt; }
bool DelayedLaunch(std::string_view) { return false; }
bool ShowInFinder(std::string_view) { return false; }
std::optional<std::string> GetResourcePath() { return std::nullopt; }
void* CreateWindow(std::string_view, uint32_t, uint32_t) { return nullptr; }
void DestroyWindow(void*) {}
void GetWindowInfoFromWindow(WindowInfo*, void*) {}
void RunCocoaEventLoop(bool) {}
void StopMainThreadEventLoop() {}
}

std::unique_ptr<HTTPDownloader> HTTPDownloader::Create(std::string) { return {}; }

namespace Common
{
bool PlaySoundAsync(const char*) { return false; }
}

IOCtlSrc::IOCtlSrc(std::string filename) : m_filename(std::move(filename)) {}
IOCtlSrc::~IOCtlSrc() = default;
bool IOCtlSrc::Reopen(Error*) { return false; }
bool IOCtlSrc::DiscReady() { return false; }
u32 IOCtlSrc::GetSectorCount() const { return 0; }
const std::vector<toc_entry>& IOCtlSrc::ReadTOC() const { return m_toc; }
bool IOCtlSrc::ReadSectors2048(u32, u32, u8*) const { return false; }
bool IOCtlSrc::ReadSectors2352(u32, u32, u8*) const { return false; }
bool IOCtlSrc::ReadTrackSubQ(cdvdSubQ*) const { return false; }
u32 IOCtlSrc::GetLayerBreakAddress() const { return 0; }
s32 IOCtlSrc::GetMediaType() const { return 0; }
void IOCtlSrc::SetSpindleSpeed(bool) const {}
std::vector<std::string> GetOpticalDriveList() { return {}; }
void GetValidDrive(std::string&) {}

extern "C" void __clear_cache(void* start, void* end)
{
    sys_icache_invalidate(start, static_cast<size_t>(static_cast<char*>(end) - static_cast<char*>(start)));
}
