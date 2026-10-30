// SPDX-License-Identifier: GPL-3.0-or-later
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <TargetConditionals.h>
#include <mach/mach.h>
#include <mach/thread_info.h>
#include <mach-o/dyld.h>
#include <dlfcn.h>
#if defined(__arm64__)
#include <mach/arm/thread_status.h>
#endif
#if TARGET_OS_IPHONE
#include <os/proc.h>
#endif

#include "perf-log.hpp"
#include "spu2-host.hpp"
#include "rrv_pcsx2_gs_bridge.h"
#include "runtime/ps2_perf.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <memory>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <thread>
#include <string>
#include <vector>
#include <unordered_map>
#include <pthread.h>
#include <pthread/qos.h>

namespace {
double monotonic() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
std::string hexString(uint64_t value) {
    char text[32];
    std::snprintf(text, sizeof(text), "%llx", static_cast<unsigned long long>(value));
    return text;
}
double threadCpu(thread_t thread) {
    thread_basic_info_data_t info{};
    mach_msg_type_number_t count = THREAD_BASIC_INFO_COUNT;
    if (!thread || thread_info(thread, THREAD_BASIC_INFO, reinterpret_cast<thread_info_t>(&info), &count) != KERN_SUCCESS)
        return -1;
    return info.user_time.seconds + info.system_time.seconds +
        (info.user_time.microseconds + info.system_time.microseconds) * 1e-6;
}
// A slot is reserved before its single Metal callback is installed. Callback
// threads publish different slots; the writer alone recycles ready slots.
struct PresentedQueue : std::enable_shared_from_this<PresentedQueue> {
    static constexpr size_t capacity = 4096;
    static_assert(std::atomic<uint32_t>::is_always_lock_free);
    struct Event {
        uint64_t id = 0, field = 0;
        double submitted = 0, presented = 0;
    };
    struct Slot {
        std::atomic<uint32_t> state{0}; // free, reserved, ready
        Event event;
    };
    std::array<Slot, capacity> slots;
    std::mutex enrollment;
    bool accepting = true;
    size_t cursor = 0;
    std::atomic<uint64_t> attempted{0}, registered{0}, lost{0};

    void complete(Slot* slot, double at) noexcept {
        slot->event.presented = at;
        slot->state.store(2, std::memory_order_release);
    }
    void record(void* opaque, uint64_t field) {
        std::lock_guard lock(enrollment);
        if (!accepting) return;
        const uint64_t presentId = attempted.fetch_add(1, std::memory_order_relaxed) + 1;
        Slot* slot = nullptr;
        for (size_t i = 0; i < capacity; ++i) {
            Slot& candidate = slots[cursor++ % capacity];
            uint32_t free = 0;
            if (candidate.state.compare_exchange_strong(free, 1, std::memory_order_acquire)) {
                slot = &candidate;
                break;
            }
        }
        if (!slot) {
            lost.fetch_add(1, std::memory_order_relaxed);
            return;
        }
        slot->event = {presentId, field, monotonic(), 0};
        registered.fetch_add(1, std::memory_order_relaxed);
        const auto owner = shared_from_this();
        id<MTLDrawable> drawable = (__bridge id<MTLDrawable>)opaque;
        if (@available(macOS 10.15.4, iOS 10.3, tvOS 10.2, *)) {
            [drawable addPresentedHandler:^(id<MTLDrawable> shown) {
                owner->complete(slot, shown.presentedTime);
            }];
        }
    }
    void close() {
        std::lock_guard lock(enrollment);
        accepting = false;
    }
    std::vector<Event> drain() {
        std::vector<Event> events;
        events.reserve(capacity);
        for (Slot& slot : slots) {
            if (slot.state.load(std::memory_order_acquire) != 2) continue;
            events.push_back(slot.event);
            slot.state.store(0, std::memory_order_release);
        }
        std::sort(events.begin(), events.end(), [](const Event& a, const Event& b) {
            const double ta = std::isfinite(a.presented) ? a.presented : 0;
            const double tb = std::isfinite(b.presented) ? b.presented : 0;
            return ta == tb ? a.id < b.id : ta < tb;
        });
        return events;
    }
};
struct Row {
    RrvPcsx2GsPerfFrame gs;
    double cpu = -1, vu1 = 0, submit = 0, drainWait = 0, audioAdvanceGap = 0, streamFullWait = 0;
    double ee = 0, iop = 0, spu2 = 0, vifUnpack = 0, xgkick = 0, other = 0;
    uint64_t audioRingFrames = 0;
    uint64_t underruns = 0, fallbacks = 0, vsyncDrains = 0, vsyncWorkerBusy = 0;
    bool gameplay = false;
};
struct PerfLog {
    struct NativeThread {
        thread_t port;
        std::string key;
        std::unordered_map<std::string, uint64_t> sites;
        std::unordered_map<uint64_t, std::string> resolved;
        uint64_t skipped = 0, errors = 0;
    };
    std::mutex mutex;
    std::condition_variable wake;
    std::vector<Row> pending;
    std::shared_ptr<PresentedQueue> presented;
    uint64_t presentedCollected = 0, presentedZero = 0, presentedInvalid = 0, presentedOutOfOrder = 0;
    double latestPresented = 0;
    std::unordered_map<uint64_t, Row> inFlight;
    std::thread writer;
    std::atomic<bool> running{true};
    std::atomic<thread_t> gameThread{MACH_PORT_NULL};
    double previousCpu = -1, previousVu = 0, previousSubmit = 0, previousDrainWait = 0, previousStreamFullWait = 0;
    double previousEe = 0, previousIop = 0, previousSpu2 = 0, previousVifUnpack = 0, previousXgkick = 0;
    uint64_t previousVsyncDrains = 0, previousVsyncWorkerBusy = 0;
    uint64_t dropped = 0;
    FILE* file = nullptr;
    bool (*gameplay)() = nullptr;
    Ps2PerfSnapshot (*snapshot)() = nullptr;
    bool nativeSampling = false;
    int nativeIntervalMs = 20;
    std::vector<NativeThread> nativeThreads;

    void releaseNativeThreads() {
        for (const auto& thread : nativeThreads)
            mach_port_deallocate(mach_task_self(), thread.port);
        nativeThreads.clear();
    }
    void refreshNativeThreads() {
        releaseNativeThreads();
        thread_act_array_t list = nullptr;
        mach_msg_type_number_t count = 0;
        if (task_threads(mach_task_self(), &list, &count) != KERN_SUCCESS) return;
        for (mach_msg_type_number_t i = 0; i < count; ++i) {
            thread_extended_info_data_t info{};
            mach_msg_type_number_t size = THREAD_EXTENDED_INFO_COUNT;
            bool retained = false;
            if (thread_info(list[i], THREAD_EXTENDED_INFO, reinterpret_cast<thread_info_t>(&info), &size) == KERN_SUCCESS &&
                (std::strcmp(info.pth_name, "GameThread") == 0 || std::strcmp(info.pth_name, "VU1GSWorker") == 0)) {
                thread_identifier_info_data_t identity{};
                size = THREAD_IDENTIFIER_INFO_COUNT;
                if (thread_info(list[i], THREAD_IDENTIFIER_INFO, reinterpret_cast<thread_info_t>(&identity), &size) == KERN_SUCCESS) {
                    nativeThreads.push_back({list[i], std::string(info.pth_name) + "#" + std::to_string(identity.thread_id), {}});
                    retained = true;
                }
            }
            if (!retained) mach_port_deallocate(mach_task_self(), list[i]);
        }
        vm_deallocate(mach_task_self(), reinterpret_cast<vm_address_t>(list), count * sizeof(thread_t));
    }
    void sampleNative() {
#if defined(__arm64__)
        for (auto& thread : nativeThreads) {
            thread_basic_info_data_t info{};
            mach_msg_type_number_t size = THREAD_BASIC_INFO_COUNT;
            if (thread_info(thread.port, THREAD_BASIC_INFO, reinterpret_cast<thread_info_t>(&info), &size) != KERN_SUCCESS) {
                ++thread.errors;
                continue;
            }
            if (info.run_state != TH_STATE_RUNNING || info.suspend_count != 0) {
                ++thread.skipped;
                continue;
            }
            arm_thread_state64_t state{};
            size = ARM_THREAD_STATE64_COUNT;
            if (thread_suspend(thread.port) != KERN_SUCCESS) {
                ++thread.errors;
                continue;
            }
            const auto captured = thread_get_state(thread.port, ARM_THREAD_STATE64, reinterpret_cast<thread_state_t>(&state), &size);
            // Resume before touching allocators or locks the sampled thread may own.
            const auto resumed = thread_resume(thread.port);
            if (captured == KERN_SUCCESS && resumed == KERN_SUCCESS) {
                const uint64_t pc = static_cast<uint64_t>(arm_thread_state64_get_pc(state));
                auto known = thread.resolved.find(pc);
                if (known == thread.resolved.end()) {
                    Dl_info info{};
                    std::string site = "?";
                    if (dladdr(reinterpret_cast<void*>(pc), &info) != 0) {
                        const char* path = info.dli_fname ? info.dli_fname : "?";
                        const char* slash = std::strrchr(path, '/');
                        site = slash ? slash + 1 : path;
                        site += "+0x" + hexString(pc - reinterpret_cast<uint64_t>(info.dli_fbase));
                        if (info.dli_sname) site += " " + std::string(info.dli_sname);
                    }
                    known = thread.resolved.emplace(pc, std::move(site)).first;
                }
                ++thread.sites[known->second];
            } else {
                ++thread.errors;
            }
        }
#endif
    }
    NSDictionary* nativeHistogram() {
        NSMutableDictionary* result = [NSMutableDictionary dictionary];
        for (const auto& thread : nativeThreads) {
            NSMutableDictionary* sites = [NSMutableDictionary dictionary];
            for (const auto& [site, count] : thread.sites)
                sites[[NSString stringWithUTF8String:site.c_str()]] = @(count);
            result[[NSString stringWithUTF8String:thread.key.c_str()]] =
                @{@"sites": sites, @"not_running": @(thread.skipped), @"errors": @(thread.errors)};
        }
        return result;
    }
    NSDictionary* nativeImages() {
        NSMutableDictionary* result = [NSMutableDictionary dictionary];
        for (uint32_t i = 0; i < _dyld_image_count(); ++i) {
            const char* path = _dyld_get_image_name(i);
            if (path && std::strstr(path, "/FIFA Street PS2.app/")) {
                NSString* name = [[NSString stringWithUTF8String:path] lastPathComponent];
                result[name] = [NSString stringWithFormat:@"0x%llx",
                    static_cast<unsigned long long>(reinterpret_cast<uintptr_t>(_dyld_get_image_header(i)))];
            }
        }
        return result;
    }

    NSDictionary* threads() {
        thread_act_array_t list = nullptr;
        mach_msg_type_number_t count = 0;
        NSMutableDictionary* result = [NSMutableDictionary dictionary];
        if (task_threads(mach_task_self(), &list, &count) != KERN_SUCCESS) return result;
        for (mach_msg_type_number_t i = 0; i < count; ++i) {
            thread_extended_info_data_t info{};
            mach_msg_type_number_t size = THREAD_EXTENDED_INFO_COUNT;
            if (thread_info(list[i], THREAD_EXTENDED_INFO, reinterpret_cast<thread_info_t>(&info), &size) == KERN_SUCCESS && info.pth_name[0]) {
                NSString* name = [NSString stringWithUTF8String:info.pth_name];
                const double cpu = threadCpu(list[i]);
                if (name && cpu >= 0) {
                    const double previous = [result[name] doubleValue];
                    result[name] = @(previous + cpu);
                }
                if (std::strcmp(info.pth_name, "GameThread") == 0 && gameThread.load() == MACH_PORT_NULL) {
                    if (mach_port_mod_refs(mach_task_self(), list[i], MACH_PORT_RIGHT_SEND, 1) == KERN_SUCCESS)
                        gameThread.store(list[i], std::memory_order_release);
                }
            }
            mach_port_deallocate(mach_task_self(), list[i]);
        }
        vm_deallocate(mach_task_self(), reinterpret_cast<vm_address_t>(list), count * sizeof(thread_t));
        return result;
    }
    NSDictionary* threadQos() {
        thread_act_array_t list = nullptr;
        mach_msg_type_number_t count = 0;
        NSMutableDictionary* result = [NSMutableDictionary dictionary];
        if (task_threads(mach_task_self(), &list, &count) != KERN_SUCCESS) return result;
        for (mach_msg_type_number_t i = 0; i < count; ++i) {
            thread_extended_info_data_t info{};
            mach_msg_type_number_t size = THREAD_EXTENDED_INFO_COUNT;
            if (thread_info(list[i], THREAD_EXTENDED_INFO, reinterpret_cast<thread_info_t>(&info), &size) == KERN_SUCCESS && info.pth_name[0]) {
                pthread_t thread = pthread_from_mach_thread_np(list[i]);
                qos_class_t qos = QOS_CLASS_UNSPECIFIED;
                int relativePriority = 0;
                if (thread && pthread_get_qos_class_np(thread, &qos, &relativePriority) == 0) {
                    NSString* value = qos == QOS_CLASS_USER_INTERACTIVE ? @"user_interactive" :
                        qos == QOS_CLASS_USER_INITIATED ? @"user_initiated" :
                        qos == QOS_CLASS_DEFAULT ? @"default" :
                        qos == QOS_CLASS_UTILITY ? @"utility" :
                        qos == QOS_CLASS_BACKGROUND ? @"background" : @"unspecified";
                    result[[NSString stringWithUTF8String:info.pth_name]] = value;
                }
            }
            mach_port_deallocate(mach_task_self(), list[i]);
        }
        vm_deallocate(mach_task_self(), reinterpret_cast<vm_address_t>(list), count * sizeof(thread_t));
        return result;
    }
    void write(NSDictionary* object) {
        NSData* bytes = [NSJSONSerialization dataWithJSONObject:object options:0 error:nil];
        if (!bytes) return;
        std::fwrite([bytes bytes], 1, [bytes length], file);
        std::fputc('\n', file);
    }
    void writePresented(bool stopping) {
        if (!presented) return;
        const auto events = presented->drain();
        for (const auto& event : events) {
            const bool valid = std::isfinite(event.presented) && event.presented >= 0;
            const bool outOfOrder = valid && event.presented > 0 && event.presented < latestPresented;
            if (outOfOrder) ++presentedOutOfOrder;
            if (!valid) ++presentedInvalid;
            else if (event.presented == 0) ++presentedZero;
            else latestPresented = std::max(latestPresented, event.presented);
            ++presentedCollected;
            // submitted_t is steady_clock; presented_time is Metal's host
            // clock. Match CPU windows by game_frame, not by absolute epochs.
            write(@{@"type": @"presented", @"present_id": @(event.id), @"game_frame": @(event.field),
                @"submitted_t": @(event.submitted), @"presented_time": valid ? @(event.presented) : (id)[NSNull null],
                @"out_of_order": @(outOfOrder)});
        }
        const uint64_t registered = presented->registered.load(std::memory_order_relaxed);
        write(@{@"type": @"presented_summary", @"t": @(monotonic()), @"final": @(stopping),
            @"attempted": @(presented->attempted.load(std::memory_order_relaxed)), @"registered": @(registered),
            @"lost": @(presented->lost.load(std::memory_order_relaxed)), @"collected": @(presentedCollected),
            @"zero": @(presentedZero), @"invalid": @(presentedInvalid), @"out_of_order": @(presentedOutOfOrder),
            @"uncollected": @(registered - presentedCollected)});
    }
    void run() {
        pthread_setname_np("PerfLog");
        double last = monotonic();
        uint64_t lastCompiles = 0, lastFallbacks = 0;
        @autoreleasepool {
            threads();
            if (nativeSampling) refreshNativeThreads();
        }
        for (;;) {
            if (nativeSampling) {
                while (running.load() && monotonic() - last < 1.0) {
                    sampleNative();
                    std::unique_lock lock(mutex);
                    wake.wait_for(lock, std::chrono::milliseconds(nativeIntervalMs), [&] { return !running.load(); });
                }
            }
            std::vector<Row> rows;
            rows.reserve(8192);
            uint64_t lost;
            bool stopping;
            {
                std::unique_lock lock(mutex);
                if (!nativeSampling)
                    wake.wait_for(lock, std::chrono::seconds(1), [&] { return !running.load(); });
                rows.swap(pending);
                lost = dropped;
                stopping = !running.load();
            }
            @autoreleasepool {
                std::sort(rows.begin(), rows.end(), [](const Row& a, const Row& b) { return a.gs.t < b.gs.t; });
                for (const Row& row : rows) {
                    const auto& f = row.gs;
                    write(@{@"t": @(f.t), @"present_dt_ms": @(f.present_dt_ms), @"game_frame": @(f.game_frame),
                        @"gameplay": @(row.gameplay), @"gpu_ms": @(f.gpu_ms),
                        @"game_clock_s": @(host::perf::guestClockSeconds.load(std::memory_order_relaxed)),
                        @"cpu_game_ms": row.cpu < 0 ? (id)[NSNull null] : @(row.cpu),
                        @"draws": @(f.draws), @"render_passes": @(f.render_passes), @"tex_uploads": @(f.tex_uploads),
                        @"tex_upload_bytes": @(f.tex_upload_bytes), @"readbacks": @(f.readbacks),
                        @"pipeline_compiles": @(f.pipeline_compiles), @"vu1_ms": @(row.vu1),
                        @"gs_submit_ms": @(row.submit), @"present_wait_ms": @(f.present_wait_ms),
                        @"stream_full_wait_ms": @(row.streamFullWait), @"drain_wait_ms": @(row.drainWait), @"vsync_drains": @(row.vsyncDrains),
                        @"ring_full_wait_ms": @(row.streamFullWait),
                        @"ee_ms": @(row.ee), @"iop_ms": @(row.iop), @"spu2_ms": @(row.spu2), @"other_ms": @(row.other),
                        @"vu1_exec_ms": @(row.vu1), @"vif_unpack_ms": @(row.vifUnpack), @"xgkick_ms": @(row.xgkick),
                        @"vsync_worker_busy": @(row.vsyncWorkerBusy),
                        @"audio_underruns": @(row.underruns), @"audio_ring_frames": @(row.audioRingFrames),
                        @"audio_advance_gap_ms": @(row.audioAdvanceGap)});
                    lastCompiles += f.pipeline_compiles;
                    lastFallbacks = row.fallbacks;
                }
                const double now = monotonic();
                task_vm_info_data_t vm{};
                mach_msg_type_number_t size = TASK_VM_INFO_COUNT;
                id footprint = [NSNull null];
                if (task_info(mach_task_self(), TASK_VM_INFO, reinterpret_cast<task_info_t>(&vm), &size) == KERN_SUCCESS)
                    footprint = @(vm.phys_footprint);
                id available = [NSNull null];
#if TARGET_OS_IPHONE
                if (@available(iOS 13.0, tvOS 13.0, *)) available = @(os_proc_available_memory());
#endif
                NSMutableDictionary* summary = [NSMutableDictionary dictionaryWithDictionary:@{@"type": @"summary", @"t": @(now), @"wall_time": @([[NSDate date] timeIntervalSince1970]), @"thermal_state": @([[NSProcessInfo processInfo] thermalState]),
                    @"os_proc_available_memory": available, @"phys_footprint": footprint,
                    @"thread_cpu_s": threads(), @"thread_qos": threadQos(), @"audio_underruns": @(ps2x_spu2_host_underruns()),
                    @"pipeline_compiles": @(lastCompiles), @"vu_fallbacks": @(lastFallbacks),
                    @"fps": @(rows.size() / std::max(1e-6, now - last)), @"dropped_frames": @(lost)}];
                if (nativeSampling) {
                    summary[@"native_pc"] = nativeHistogram();
                    summary[@"native_pc_interval_ms"] = @(nativeIntervalMs);
                    summary[@"native_pc_images"] = nativeImages();
                    if (!stopping) refreshNativeThreads();
                }
                write(summary);
                writePresented(stopping);
                std::fflush(file);
                last = now;
            }
            if (stopping) break;
        }
        releaseNativeThreads();
        std::fclose(file);
    }
    void frame(const RrvPcsx2GsPerfFrame& frame) {
        if (!running.load(std::memory_order_acquire)) return;
        std::lock_guard lock(mutex);
        if (!running.load()) return;
        if (frame.completed) {
            const auto found = inFlight.find(frame.game_frame);
            if (found == inFlight.end()) return;
            Row row = found->second;
            inFlight.erase(found);
            row.gs = frame;
            if (pending.size() < 8192) pending.push_back(row);
            else ++dropped;
            return;
        }
        Row row{};
        row.gs = frame;
        row.gameplay = gameplay();
        const auto audio = ps2x_spu2_host_perf();
        row.underruns = audio.underruns;
        row.audioRingFrames = audio.ringFrames;
        row.audioAdvanceGap = audio.advanceGapMs;
        const auto runtime = snapshot();
        const double cpu = threadCpu(gameThread.load(std::memory_order_acquire));
        const double submit = host::perf::gsSubmitSeconds.load(std::memory_order_relaxed);
        if (cpu >= 0 && previousCpu >= 0) row.cpu = std::max(0.0, cpu - previousCpu) * 1000;
        previousCpu = cpu;
        row.vu1 = std::max(0.0, runtime.vu1_seconds - previousVu) * 1000;
        previousVu = runtime.vu1_seconds;
        row.submit = std::max(0.0, submit - previousSubmit) * 1000;
        previousSubmit = submit;
        row.drainWait = std::max(0.0, runtime.drain_wait_seconds - previousDrainWait) * 1000;
        previousDrainWait = runtime.drain_wait_seconds;
        row.streamFullWait = std::max(0.0, runtime.stream_full_wait_seconds - previousStreamFullWait) * 1000;
        previousStreamFullWait = runtime.stream_full_wait_seconds;
        const double eeInclusive = std::max(0.0, runtime.ee_seconds - previousEe) * 1000;
        previousEe = runtime.ee_seconds;
        row.iop = std::max(0.0, runtime.iop_seconds - previousIop) * 1000;
        previousIop = runtime.iop_seconds;
        row.spu2 = std::max(0.0, audio.mixSeconds - previousSpu2) * 1000;
        previousSpu2 = audio.mixSeconds;
        row.vifUnpack = std::max(0.0, runtime.vif_unpack_seconds - previousVifUnpack) * 1000;
        previousVifUnpack = runtime.vif_unpack_seconds;
        row.xgkick = std::max(0.0, runtime.xgkick_seconds - previousXgkick) * 1000;
        previousXgkick = runtime.xgkick_seconds;
        row.ee = std::max(0.0, eeInclusive - row.iop - row.spu2 - row.drainWait - row.streamFullWait);
        row.other = std::max(0.0, row.cpu - row.ee - row.iop - row.spu2 - row.drainWait - row.streamFullWait);
        row.vsyncDrains = runtime.vsync_drains - previousVsyncDrains;
        previousVsyncDrains = runtime.vsync_drains;
        row.vsyncWorkerBusy = runtime.vsync_worker_busy - previousVsyncWorkerBusy;
        previousVsyncWorkerBusy = runtime.vsync_worker_busy;
        row.fallbacks = runtime.vu_fallbacks;
        if (inFlight.size() < 8192) inFlight.emplace(frame.game_frame, row);
        else ++dropped;
    }
};
PerfLog* s_log = nullptr;
void registerPresented(void* drawable, uint64_t field) {
    if (s_log && s_log->presented) s_log->presented->record(drawable, field);
}
void frameCallback(const RrvPcsx2GsPerfFrame* frame, void* context) {
    if (frame && frame->struct_size == sizeof(*frame) && context)
        static_cast<PerfLog*>(context)->frame(*frame);
}
}

void ps2x_perf_start(const std::filesystem::path& userDirectory,
    bool (*gameplay)(), Ps2PerfSnapshot (*snapshot)()) {
    const char* requested = std::getenv("PS2X_PERF_LOG");
    if (!requested || std::strcmp(requested, "1") || s_log) return;
    std::filesystem::path directory;
#if TARGET_OS_TV
    // tvOS has no writable Documents; the user directory sits in Library/Caches.
    directory = userDirectory.parent_path() / "perf";
#elif TARGET_OS_IPHONE
    if (const char* isolated = std::getenv("PS2X_USER_DIR"); isolated && *isolated)
        directory = userDirectory.parent_path() / "perf";
    else {
        NSURL* documents = [[NSFileManager defaultManager] URLForDirectory:NSDocumentDirectory
            inDomain:NSUserDomainMask appropriateForURL:nil create:YES error:nil];
        directory = std::filesystem::path([[documents path] fileSystemRepresentation]) / "perf";
    }
#else
    directory = userDirectory / "perf";
#endif
    std::error_code error;
    std::filesystem::create_directories(directory, error);
    FILE* output = error ? nullptr : std::fopen((directory / "frames.jsonl").c_str(), "w");
    if (!output) { std::fprintf(stderr, "[perf] cannot open %s\n", directory.c_str()); return; }
    s_log = new PerfLog;
    host::perf::enabled = true;
    s_log->file = output;
    s_log->gameplay = gameplay;
    s_log->snapshot = snapshot;
#if defined(__arm64__)
    const char* nativeSampling = std::getenv("PS2X_NATIVE_PC_SAMPLING");
    s_log->nativeSampling = nativeSampling && std::strcmp(nativeSampling, "1") == 0;
    if (const char* interval = std::getenv("PS2X_NATIVE_PC_SAMPLING_MS")) {
        const int parsed = std::atoi(interval);
        if (parsed >= 2 && parsed <= 1000) s_log->nativeIntervalMs = parsed;
    }
#endif
    const char* actual = std::getenv("PS2X_PERF_PRESENTED");
    if (!actual || std::strcmp(actual, "0") != 0) {
        if (@available(macOS 10.15.4, iOS 10.3, tvOS 10.2, *))
            s_log->presented = std::make_shared<PresentedQueue>();
    }
    s_log->pending.reserve(8192);
    s_log->writer = std::thread([log = s_log] { log->run(); });
    rrv_pcsx2_gs_bridge_set_perf_callback(frameCallback, s_log);
    if (s_log->presented) rrv_pcsx2_gs_bridge_set_presented_register(registerPresented);
    std::fprintf(stderr, "[perf] frames -> %s\n", (directory / "frames.jsonl").c_str());
}
void ps2x_perf_stop() {
    if (!s_log) return;
    rrv_pcsx2_gs_bridge_set_presented_register(nullptr);
    if (s_log->presented) s_log->presented->close();
    {
        std::lock_guard lock(s_log->mutex);
        if (!s_log->running.exchange(false)) return;
        s_log->dropped += s_log->inFlight.size();
        s_log->inFlight.clear();
    }
    rrv_pcsx2_gs_bridge_set_perf_callback(nullptr, nullptr);
    s_log->wake.notify_one();
    s_log->writer.join();
}
