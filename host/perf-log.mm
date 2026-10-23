// SPDX-License-Identifier: GPL-3.0-or-later
#import <Foundation/Foundation.h>
#include <TargetConditionals.h>
#include <mach/mach.h>
#include <mach/thread_info.h>
#if TARGET_OS_IPHONE
#include <os/proc.h>
#endif

#include "perf-log.hpp"
#include "spu2-host.hpp"
#include "rrv_pcsx2_gs_bridge.h"
#include "runtime/ps2_perf.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <thread>
#include <vector>
#include <unordered_map>
#include <pthread.h>
#include <pthread/qos.h>

namespace {
double monotonic() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
double threadCpu(thread_t thread) {
    thread_basic_info_data_t info{};
    mach_msg_type_number_t count = THREAD_BASIC_INFO_COUNT;
    if (!thread || thread_info(thread, THREAD_BASIC_INFO, reinterpret_cast<thread_info_t>(&info), &count) != KERN_SUCCESS)
        return -1;
    return info.user_time.seconds + info.system_time.seconds +
        (info.user_time.microseconds + info.system_time.microseconds) * 1e-6;
}
struct Row {
    RrvPcsx2GsPerfFrame gs;
    double cpu = -1, vu1 = 0, submit = 0;
    uint64_t underruns = 0, fallbacks = 0;
    bool gameplay = false;
};
struct PerfLog {
    std::mutex mutex;
    std::condition_variable wake;
    std::vector<Row> pending;
    std::unordered_map<uint64_t, Row> inFlight;
    std::thread writer;
    std::atomic<bool> running{true};
    std::atomic<thread_t> gameThread{MACH_PORT_NULL};
    double previousCpu = -1, previousVu = 0, previousSubmit = 0;
    uint64_t dropped = 0;
    FILE* file = nullptr;
    bool (*gameplay)() = nullptr;
    Ps2PerfSnapshot (*snapshot)() = nullptr;

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
    void run() {
        pthread_setname_np("PerfLog");
        double last = monotonic();
        uint64_t lastCompiles = 0, lastFallbacks = 0;
        @autoreleasepool { threads(); }
        for (;;) {
            std::vector<Row> rows;
            rows.reserve(8192);
            uint64_t lost;
            bool stopping;
            {
                std::unique_lock lock(mutex);
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
                        @"cpu_game_ms": row.cpu < 0 ? (id)[NSNull null] : @(row.cpu),
                        @"draws": @(f.draws), @"render_passes": @(f.render_passes), @"tex_uploads": @(f.tex_uploads),
                        @"tex_upload_bytes": @(f.tex_upload_bytes), @"readbacks": @(f.readbacks),
                        @"pipeline_compiles": @(f.pipeline_compiles), @"vu1_ms": @(row.vu1),
                        @"gs_submit_ms": @(row.submit), @"present_wait_ms": @(f.present_wait_ms),
                        @"audio_underruns": @(row.underruns)});
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
                write(@{@"type": @"summary", @"t": @(now), @"wall_time": @([[NSDate date] timeIntervalSince1970]), @"thermal_state": @([[NSProcessInfo processInfo] thermalState]),
                    @"os_proc_available_memory": available, @"phys_footprint": footprint,
                    @"thread_cpu_s": threads(), @"thread_qos": threadQos(), @"audio_underruns": @(ps2x_spu2_host_underruns()),
                    @"pipeline_compiles": @(lastCompiles), @"vu_fallbacks": @(lastFallbacks),
                    @"fps": @(rows.size() / std::max(1e-6, now - last)), @"dropped_frames": @(lost)});
                std::fflush(file);
                last = now;
            }
            if (stopping) break;
        }
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
        row.underruns = ps2x_spu2_host_underruns();
        const auto runtime = snapshot();
        const double cpu = threadCpu(gameThread.load(std::memory_order_acquire));
        const double submit = host::perf::gsSubmitSeconds.load(std::memory_order_relaxed);
        if (cpu >= 0 && previousCpu >= 0) row.cpu = std::max(0.0, cpu - previousCpu) * 1000;
        previousCpu = cpu;
        row.vu1 = std::max(0.0, runtime.vu1_seconds - previousVu) * 1000;
        previousVu = runtime.vu1_seconds;
        row.submit = std::max(0.0, submit - previousSubmit) * 1000;
        previousSubmit = submit;
        row.fallbacks = runtime.vu_fallbacks;
        if (inFlight.size() < 8192) inFlight.emplace(frame.game_frame, row);
        else ++dropped;
    }
};
PerfLog* s_log = nullptr;
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
#if TARGET_OS_IPHONE
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
    s_log->pending.reserve(8192);
    s_log->writer = std::thread([log = s_log] { log->run(); });
    rrv_pcsx2_gs_bridge_set_perf_callback(frameCallback, s_log);
    std::fprintf(stderr, "[perf] frames -> %s\n", (directory / "frames.jsonl").c_str());
}
void ps2x_perf_stop() {
    if (!s_log) return;
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
