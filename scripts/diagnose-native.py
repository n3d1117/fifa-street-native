#!/usr/bin/env python3
"""Run and summarize a bounded native probe, reusing run-native-check.py."""
import argparse
import collections
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BASE_RUNNER = ROOT / "scripts/run-native-check.py"


def timing_summary(log_path):
    events_path = log_path.parent / "events.jsonl"
    progress = []
    rpc = collections.defaultdict(list)
    if events_path.exists():
        rows = events_path.read_text().splitlines()
        events = []
        for index, row in enumerate(rows):
            if not row.strip():
                continue
            try:
                events.append(json.loads(row))
            except json.JSONDecodeError:
                if index != len(rows) - 1:
                    raise
                # A running probe may be midway through writing the last record.
    else:
        events = [{"line": line, "seconds": None}
                  for line in log_path.read_text(errors="replace").splitlines()] if log_path.exists() else []
    max_movie_frame = 0
    for event in events:
            line = event["line"]
            if "[tarzan-mpeg]" in line:
                match = re.search(r"(?:served|frame)=(\d+)", line)
                if match:
                    frame = int(match.group(1))
                    max_movie_frame = max(max_movie_frame, frame)
                    if event["seconds"] is not None and (not progress or frame != progress[-1][1]):
                        progress.append((event["seconds"], frame))
            match = re.search(r"\[tarzan-iop-rpc\].*sid=(0x\w+).*fn=(0x\w+).*us=(\d+)", line)
            if match:
                rpc[(match.group(1), match.group(2))].append(int(match.group(3)))
    # Split movie restarts; never report these decode/presentation counters as gameplay FPS.
    segments = []
    segment = []
    for point in progress:
        if segment and point[1] < segment[-1][1]:
            segments.append(segment)
            segment = []
        segment.append(point)
    if segment:
        segments.append(segment)
    movies = []
    for segment in segments:
        first, last = segment[0], segment[-1]
        elapsed = last[0] - first[0]
        movies.append({"first_frame": first[1], "last_frame": last[1],
                       "observed_seconds": round(elapsed, 3),
                       "observed_movie_fps": round((last[1] - first[1]) / elapsed, 2)
                       if elapsed >= 1 and last[1] > first[1] else None})
    calls = []
    for (sid, function), durations in rpc.items():
        calls.append({"sid": sid, "function": function, "sampled_calls": len(durations),
                      "median_us": statistics.median(durations), "max_us": max(durations),
                      "sampled_total_us": sum(durations)})
    return {"movies": movies, "max_movie_frame_observed": max_movie_frame,
            "rpc_sampled_timings": sorted(calls, key=lambda c: c["max_us"], reverse=True),
            "note": "Movie counters are not gameplay FPS. RPC logging is bounded; totals describe logged calls only."}


def summarize(log_path, sample_path=None):
    text = log_path.read_text(errors="replace") if log_path and log_path.exists() else ""
    lines = text.splitlines()
    failed = [line[:1000] for line in lines if any(token in line.lower() for token in (
        "[guest-branch:missing-target]", "[error]", "fatal", "error during program execution",
        "no exact recompiled function", "failed to load", "failed to initialize", "[launcher]"))]
    asset = [line[:1000] for line in lines if re.search(
        r"(load-emulated|asset|cdrom|read.*(file|sector)|mounted|loading\s+(segment|file))", line, re.I)]
    mpeg = [line[:1000] for line in lines if "[tarzan-mpeg]" in line]
    frame_queues = [line[:1000] for line in mpeg if re.search(r"(queued=|frames=|frame-schedule|demux-ring)", line)]
    event_counts = collections.Counter()
    target_counts = collections.defaultdict(collections.Counter)
    event_re = re.compile(r"\[tarzan-video\]\s+#\d+\s+(\S+).*?target=(0x[0-9a-fA-F]+)")
    for line in lines:
        match = event_re.search(line)
        if match:
            event_counts[match.group(1)] += 1
            target_counts[match.group(2).lower()][match.group(1)] += 1
    # Also count event records that use a named event field rather than target calls.
    for line in lines:
        if "[tarzan-video]" in line and not event_re.search(line):
            match = re.search(r"\[tarzan-video\]\s+(\S+)", line)
            if match:
                event_counts[match.group(1)] += 1

    guest_counts = collections.Counter()
    if sample_path and sample_path.exists():
        sample_lines = sample_path.read_text(errors="replace").splitlines()
        in_game_thread = False
        for line in sample_lines:
            if re.search(r"Thread_\d+:\s*GameThread", line):
                in_game_thread = True
                continue
            if in_game_thread and re.search(r"Thread_\d+:", line):
                break
            if in_game_thread and "(in ps2EntryRunner)" in line and "_0x" in line:
                match = re.search(r"\+\s*(\d+)\s+(.+?)\s+\(in ps2EntryRunner\)", line)
                if match:
                    guest_counts[match.group(2).strip()] += int(match.group(1))
    top_guest = [{"samples": count, "function": function}
                 for function, count in guest_counts.most_common(20)]
    return {
        "failed_branches": failed[-30:],
        "cd_asset_progress": asset[-80:],
        "mpeg_lines": mpeg[-100:],
        "mpeg_frame_queue_lines": frame_queues[-80:],
        "video_event_counts": {"events": dict(event_counts), "by_target":
                               {target: dict(counts) for target, counts in target_counts.items()}},
        "memory_faults": [line[:1000] for line in lines if "[tarzan-memory-fault]" in line][-30:],
        "iop_budget_exhaustions": [line for line in lines if "[tarzan-iop-budget] entry=" in line][:8],
        "iop_wait_timeouts": [line for line in lines if "outside a thread timed out" in line][:8],
        "timing": timing_summary(log_path),
        "vif_irq_records": [line[:1000] for line in lines
                            if "[tarzan-vif1]" in line and "irq=1" in line][-64:],
        "sample_top_guest_functions": top_guest[:20],
        "gameplay_verified": False,
    }


def capture_sample(pid, output_path):
    sample = shutil.which("sample")
    if not sample:
        return {"requested": True, "available": False, "reason": "sample utility not found"}
    # Apple's sample runs for the requested interval and writes directly to the file.
    cmd = [sample, str(pid), "2", "-file", str(output_path)]
    started = time.monotonic()
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=5, check=False, text=True)
        return {"requested": True, "available": True, "exit_code": result.returncode,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "stderr": result.stderr[-1000:]}
    except subprocess.TimeoutExpired:
        return {"requested": True, "available": True, "timed_out": True,
                "elapsed_seconds": round(time.monotonic() - started, 3)}


def save_report(run_dir, extra=None):
    result_path = run_dir / "result.json"
    result = json.loads(result_path.read_text()) if result_path.exists() else {}
    sample_path = run_dir / "game-thread.sample"
    report = summarize(run_dir / "stdout.log", sample_path if sample_path.exists() else None)
    report.update(result)
    report["crash"] = None
    crash_path = Path(result["crash_report"]) if result.get("crash_report") else None
    if crash_path and crash_path.exists():
        crash_text = crash_path.read_text(errors="replace")
        exception = re.search(r"^Exception Type:\s*(.+)$", crash_text, re.M)
        thread = re.search(r"^Thread (\d+) Crashed.*\n((?:[^\n]+\n)+)", crash_text, re.M)
        report["crash"] = {"exception": exception.group(1) if exception else None,
                           "thread": int(thread.group(1)) if thread else None,
                           "stack": thread.group(2).splitlines()[:12] if thread else [],
                           "report": str(crash_path)}
    if extra:
        report.update(extra)
    log_text = (run_dir / "stdout.log").read_text(errors="replace")
    report["feature_failures"] = []
    if report.get("require_spu2") and "PCSX2 2.8.2 SPU2 core installed" not in log_text:
        report["feature_failures"].append("SPU2 startup was required but not observed")
    bad = any(report[key] for key in ("failed_branches", "memory_faults", "iop_budget_exhaustions", "iop_wait_timeouts"))
    bad = bad or bool(result.get("failure_lines")) or bool(report["feature_failures"])
    if result_path.exists() and not result.get("timed_out") and result.get("exit_code") != 0:
        bad = True
    report["status"] = "failed" if bad else "bounded_run" if result.get("timed_out") else "exited" if result_path.exists() else "running"
    report["gameplay_verified"] = False
    report["generated_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    report["run_directory"] = str(run_dir)
    report_path = run_dir / "diagnosis.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"diagnosis": str(report_path), "status": report["status"],
                      "movies": report["timing"]["movies"],
                      "slowest_rpc": report["timing"]["rpc_sampled_timings"][:3],
                      "budget_exhaustions": len(report["iop_budget_exhaustions"]),
                      "memory_faults": len(report["memory_faults"]),
                      "failed_branches": len(report["failed_branches"]),
                      "crash": report["crash"],
                      "feature_failures": report["feature_failures"],
                      "gameplay_verified": False}, indent=2))
    return int(bad)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", type=Path)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--label", default="native-diagnostic")
    parser.add_argument("--trace", choices=("video", "mpeg", "both"), default="mpeg")
    parser.add_argument("--analyze", type=Path, help="summarize an existing run directory without launching")
    parser.add_argument("--keep-running-on-failure", action="store_true")
    parser.add_argument("--iop-dump", action="store_true",
                        help="save IOP RAM once if a guest call exhausts its instruction budget")
    parser.add_argument("--require-spu2", action="store_true",
                        help="fail unless the native SPU2 core reports successful installation")
    parser.add_argument("--sample-after", type=float, default=10,
                        help="seconds after launch to take the CPU sample (default: 10)")
    parser.add_argument("--sample", action="store_true", help="capture one short GameThread sample after launch")
    parser.add_argument("--gpu-capture", action="store_true",
                        help="request bounded Metal diagnostic frame capture when the runner has the Metal adapter")
    parser.add_argument("--capture-frames", type=int, default=30,
                        help="maximum captured frames (1..120; only used with --gpu-capture)")
    parser.add_argument("--capture-start-field", type=int, default=0,
                        help="first GS field eligible for capture (0 or greater)")
    parser.add_argument("--capture-interval", type=int, default=1,
                        help="capture every Nth eligible GS field (1 or greater)")
    args = parser.parse_args()
    if args.analyze:
        return save_report(args.analyze.resolve(strict=True))
    if not args.runner:
        parser.error("--runner is required unless --analyze is supplied")
    if not 0 <= args.sample_after <= args.seconds:
        parser.error("--sample-after must be between zero and --seconds")
    if not 1 <= args.seconds <= 60:
        parser.error("--seconds must be between 1 and 60")
    if not args.label.replace("-", "").replace("_", "").isalnum():
        parser.error("--label must contain letters, numbers, hyphens or underscores")
    if not 1 <= args.capture_frames <= 120:
        parser.error("--capture-frames must be between 1 and 120")
    if args.capture_start_field < 0:
        parser.error("--capture-start-field must be zero or greater")
    if args.capture_interval < 1:
        parser.error("--capture-interval must be at least 1")
    runner = args.runner.resolve(strict=True)
    env = os.environ.copy()
    env["TARZAN_TRACE_FAULTS"] = "1"
    env["TARZAN_TRACE_IOP_RPC"] = "1"
    dump_path = None
    if args.iop_dump:
        dump_stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        dump_path = ROOT / "runtime/checks" / (dump_stamp + "-" + args.label + "-iop-ram.bin")
        env["TARZAN_IOP_DUMP"] = str(dump_path)
    env["TARZAN_TRACE_VIDEO"] = "1" if args.trace in ("video", "both") else "0"
    env["TARZAN_TRACE_MPEG"] = "1" if args.trace in ("mpeg", "both") else "0"
    env.pop("TARZAN_CAPTURE_DIR", None)
    env.pop("TARZAN_CAPTURE_FRAMES", None)
    env.pop("TARZAN_CAPTURE_START_FIELD", None)
    env.pop("TARZAN_CAPTURE_INTERVAL", None)
    capture_info = {"requested": args.gpu_capture, "frames": 0}
    if args.gpu_capture:
        capture_stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        capture_dir = ROOT / "runtime/checks" / ("capture-" + capture_stamp + "-" + args.label)
        capture_dir.mkdir(parents=True)
        env["TARZAN_CAPTURE_DIR"] = str(capture_dir)
        env["TARZAN_CAPTURE_FRAMES"] = str(args.capture_frames)
        env["TARZAN_CAPTURE_START_FIELD"] = str(args.capture_start_field)
        env["TARZAN_CAPTURE_INTERVAL"] = str(args.capture_interval)
        capture_info.update({"directory": str(capture_dir), "frames": args.capture_frames,
                             "start_field": args.capture_start_field,
                             "interval_fields": args.capture_interval})
    cmd = [sys.executable, str(BASE_RUNNER), "--runner", str(runner),
           "--seconds", str(args.seconds), "--label", args.label]
    if not args.keep_running_on_failure:
        cmd.append("--stop-on-failure")
    child = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
    first = child.stdout.readline()
    if first:
        print(first, end="", flush=True)
    try:
        run_info = json.loads(first)
    except (json.JSONDecodeError, TypeError):
        child.terminate()
        child.wait(timeout=5)
        raise RuntimeError("run-native-check.py did not emit its launch record")
    run_dir = Path(run_info["directory"])
    sample_info = {"requested": args.sample, "available": False}
    if args.sample:
        time.sleep(args.sample_after)
        if child.poll() is None:
            sample_info = capture_sample(run_info["pid"], run_dir / "game-thread.sample")
        else:
            sample_info["reason"] = "probe ended before the requested sample time"
    tail = child.communicate(timeout=args.seconds + 15)[0]
    return save_report(run_dir, {"label": args.label, "trace": args.trace,
                                 "sample": sample_info, "gpu_capture": capture_info,
                                 "require_spu2": args.require_spu2,
                                 "iop_ram_dump": str(dump_path) if dump_path and dump_path.exists() else None})


if __name__ == "__main__":
    raise SystemExit(main())
