// SPDX-FileCopyrightText: 2002-2026 PCSX2 Dev Team
// SPDX-License-Identifier: GPL-3.0-or-later
// Host callbacks required by the PCSX2 GS library, adapted from pcsx2-gsrunner.
#include "rrv_pcsx2_gs_bridge.h"
#include "common/Assertions.h"
#include "common/Console.h"
#include "common/ProgressCallback.h"
#include "common/SmallString.h"
#include "pcsx2/Achievements.h"
#include "pcsx2/GS/GS.h"
#include "pcsx2/Host.h"
#include "pcsx2/ImGui/FullscreenUI.h"
#include "pcsx2/ImGui/ImGuiFullscreen.h"
#include "pcsx2/ImGui/ImGuiManager.h"
#include "pcsx2/Input/InputManager.h"
#include "pcsx2/VMManager.h"
#include <algorithm>
#include <cstring>
#include <mutex>
#include <optional>

namespace {
std::mutex s_surface_mutex;
std::optional<WindowInfo> s_native_surface;
}

extern "C" void rrv_pcsx2_gs_bridge_initialize_host_console()
{
    Log::SetConsoleOutputLevel(LOGLEVEL_ERROR);
}

extern "C" int rrv_pcsx2_gs_bridge_configure_native_surface(
    uint32_t kind, uint32_t flags, void* native_view, void* native_layer,
    uint32_t width_pixels, uint32_t height_pixels, float backing_scale,
    char* error, uint32_t error_capacity)
{
    if (kind != 1u || !native_view || !native_layer || width_pixels == 0u ||
        height_pixels == 0u || backing_scale <= 0.0f)
    {
        if (error && error_capacity)
            std::snprintf(error, error_capacity, "invalid macOS direct-present surface");
        return 0;
    }
    std::lock_guard<std::mutex> lock(s_surface_mutex);
    WindowInfo info{};
    info.type = WindowInfo::Type::MacOS;
    info.window_handle = native_view;
    info.surface_handle = native_layer;

    info.surface_width = width_pixels;
    info.surface_height = height_pixels;
    info.surface_scale = backing_scale;
    s_native_surface = info;
    (void)flags;
    return 1;
}

extern "C" void rrv_pcsx2_gs_bridge_clear_native_surface()
{
    std::lock_guard<std::mutex> lock(s_surface_mutex);
    s_native_surface.reset();
}

void Host::CommitBaseSettingChanges()
{

}

void Host::LoadSettings(SettingsInterface& si, std::unique_lock<std::mutex>& lock)
{
}

void Host::CheckForSettingsChanges(const Pcsx2Config& old_config)
{
}

bool Host::RequestResetSettings(bool folders, bool core, bool controllers, bool hotkeys, bool ui)
{

	return false;
}

void Host::SetDefaultUISettings(SettingsInterface& si)
{

}

bool Host::LocaleCircleConfirm()
{

	return false;
}

std::unique_ptr<ProgressCallback> Host::CreateHostProgressCallback()
{
	return ProgressCallback::CreateNullProgressCallback();
}

void Host::ReportInfoAsync(const std::string_view title, const std::string_view message)
{
	if (!title.empty() && !message.empty())
		INFO_LOG("ReportInfoAsync: {}: {}", title, message);
	else if (!message.empty())
		INFO_LOG("ReportInfoAsync: {}", message);
}

void Host::ReportErrorAsync(const std::string_view title, const std::string_view message)
{
	if (!title.empty() && !message.empty())
		ERROR_LOG("ReportErrorAsync: {}: {}", title, message);
	else if (!message.empty())
		ERROR_LOG("ReportErrorAsync: {}", message);
}

void Host::OpenURL(const std::string_view url)
{

}

bool Host::CopyTextToClipboard(const std::string_view text)
{
	return false;
}

std::string Host::GetTextFromClipboard()
{
	return std::string();
}

void Host::BeginTextInput()
{

}

void Host::EndTextInput()
{

}

std::optional<WindowInfo> Host::GetTopLevelWindowInfo()
{ return Host::AcquireRenderWindow(false); }

void Host::OnInputDeviceConnected(const std::string_view identifier, const std::string_view device_name) {}

void Host::OnInputDeviceDisconnected(const InputBindingKey key, const std::string_view identifier)
{
}

void Host::SetMouseMode(bool relative_mode, bool hide_cursor)
{
}

void Host::SetMouseLock(bool state)
{
}

std::optional<WindowInfo> Host::AcquireRenderWindow(bool recreate_window)
{
    std::lock_guard<std::mutex> lock(s_surface_mutex);
    return s_native_surface;
}

void Host::ReleaseRenderWindow()
{
}

void Host::BeginPresentFrame()
{  }

void Host::RequestResizeHostDisplay(s32 width, s32 height)
{
}

void Host::OnVMStarting()
{
}

void Host::OnVMStarted()
{
}

void Host::OnVMDestroyed()
{
}

void Host::OnVMPaused()
{
}

void Host::OnVMResumed()
{
}

void Host::OnGameChanged(const std::string& title, const std::string& elf_override, const std::string& disc_path,
	const std::string& disc_serial, u32 disc_crc, u32 current_crc)
{
}

void Host::OnPerformanceMetricsUpdated()
{
}

void Host::OnSaveStateLoading(const std::string_view filename)
{
}

void Host::OnSaveStateLoaded(const std::string_view filename, bool was_successful)
{
}

void Host::OnSaveStateSaved(const std::string_view filename)
{
}

void Host::RunOnCPUThread(std::function<void()> function, bool block /* = false */)
{
	pxFailRel("Not implemented");
}

void Host::RefreshGameListAsync(bool invalidate_cache)
{
}

void Host::CancelGameListRefresh()
{
}

void Host::OnGameListFavoriteChanged(const std::string& path)
{
}

bool Host::IsFullscreen()
{
	return false;
}

void Host::SetFullscreen(bool enabled)
{
}

void Host::OnCaptureStarted(const std::string& filename)
{
}

void Host::OnCaptureStopped()
{
}

void Host::RequestExitApplication(bool allow_confirm)
{
}

void Host::RequestExitBigPicture()
{
}

void Host::RequestVMShutdown(bool allow_confirm, bool allow_save_state, bool default_save_state)
{
	VMManager::SetState(VMState::Stopping);
}

void Host::OnAchievementsLoginSuccess(const char* username, u32 points, u32 sc_points, u32 unread_messages)
{

}

void Host::OnAchievementsLoginRequested(Achievements::LoginRequestReason reason)
{

}

void Host::OnAchievementsHardcoreModeChanged(bool enabled)
{

}

void Host::OnAchievementsRefreshed()
{

}

bool Host::InBatchMode()
{
	return false;
}

bool Host::InNoGUIMode()
{
	return false;
}

bool Host::ShouldPreferHostFileSelector()
{
	return false;
}

void Host::OpenHostFileSelectorAsync(std::string_view title, bool select_directory, FileSelectorCallback callback,
	FileSelectorFilters filters, std::string_view initial_directory)
{
	callback(std::string());
}

int Host::LocaleSensitiveCompare(std::string_view lhs, std::string_view rhs)
{
	const int res = std::strncmp(lhs.data(), rhs.data(), std::min(lhs.size(), rhs.size()));
	if (res != 0)
		return res;
	return lhs.size() > rhs.size() ? 1 : (lhs.size() < rhs.size() ? -1 : 0);
}

std::optional<u32> InputManager::ConvertHostKeyboardStringToCode(const std::string_view str)
{
	return std::nullopt;
}

std::optional<std::string> InputManager::ConvertHostKeyboardCodeToString(u32 code)
{
	return std::nullopt;
}

const char* InputManager::ConvertHostKeyboardCodeToIcon(u32 code)
{
	return nullptr;
}

BEGIN_HOTKEY_LIST(g_host_hotkeys)
END_HOTKEY_LIST()

void Host::PumpMessagesOnCPUThread() {}

s32 Host::Internal::GetTranslatedStringImpl(
	const std::string_view context, const std::string_view msg, char* tbuf, size_t tbuf_space)
{
	if (msg.size() > tbuf_space)
		return -1;
	else if (msg.empty())
		return 0;

	std::memcpy(tbuf, msg.data(), msg.size());
	return static_cast<s32>(msg.size());
}

std::string Host::TranslatePluralToString(const char* context, const char* msg, const char* disambiguation, int count)
{
	TinyString count_str = TinyString::from_format("{}", count);

	std::string ret(msg);
	for (;;)
	{
		std::string::size_type pos = ret.find("%n");
		if (pos == std::string::npos)
			break;

		ret.replace(pos, pos + 2, count_str.view());
	}

	return ret;
}
