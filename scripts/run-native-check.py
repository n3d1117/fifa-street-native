#!/usr/bin/env python3
"""Bounded native boot probe with isolated saves and a reviewable result.

Launching the GUI executable requires normal macOS application permissions.
This checks boot progress, never treats a running window as proof of gameplay.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import time

ROOT = Path(__file__).resolve().parents[1]


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_snapshot():
    paths = ("integration/launcher.cpp", "integration/metal-gs-adapter.cpp",
             "integration/spu2-host.cpp", "integration/spu2-host.hpp",
             "source/PS2Recomp-metal/ps2xRuntime/include/ps2_runtime.h",
             "source/PS2Recomp-metal/ps2xRuntime/include/runtime/ps2_host_arena.h",
             "source/PS2Recomp-metal/ps2xRuntime/src/lib/ps2_runtime.cpp",
             "source/PS2Recomp-metal/ps2xIOP/src/emulator/iop_emulator.cpp",
             "source/PS2Recomp-metal/ps2xIOP/src/emulator/services/iop_rpc.cpp")
    return {name: fingerprint(ROOT / name) for name in paths if (ROOT / name).is_file()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--label", default="native-boot")
    parser.add_argument("--stop-on-failure", action="store_true",
                        help="stop promptly on a missing branch, memory fault, or exhausted IOP call")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 120:
        parser.error("--seconds must be between 1 and 120")
    if not args.label.replace("-", "").replace("_", "").isalnum():
        parser.error("--label must contain letters, numbers, hyphens or underscores")
    runner = args.runner.resolve(strict=True)
    elf = ROOT / "extracted/disc/SLES_503.50"
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = ROOT / "runtime/checks" / (stamp + "-" + args.label)
    run_dir.mkdir(parents=True)
    command = [str(runner), "--user-dir", str(run_dir / "user"), str(elf)]
    result = {"command": command, "elf_sha256": fingerprint(elf),
              "runner_sha256": fingerprint(runner), "source_snapshot": source_snapshot(),
              "diagnostic_environment": {name: os.environ[name] for name in (
                  "PS2X_WALLCLOCK", "PS2X_IOP_IDLE_FASTPATH", "TARZAN_TRACE_MPEG",
                  "TARZAN_TRACE_VIDEO", "TARZAN_TRACE_IOP_RPC", "TARZAN_TRACE_FAULTS",
                  "TARZAN_IOP_DUMP", "TARZAN_CAPTURE_DIR") if name in os.environ},
              "label": args.label, "log": str(run_dir / "stdout.log"),
              "gameplay_verified": False, "timed_out": False, "diagnostic_stop": False}
    bridge = ROOT / "build/metal-bridge/pcsx2/pcsx2-gs-bridge/librrv-pcsx2-gs-bridge.dylib"
    if bridge.is_file():
        result["metal_bridge_artifact"] = {"path": str(bridge), "sha256": fingerprint(bridge)}
    markers = ("Error during program execution", "[terminate]", "fatal", "No exact recompiled function",
               "Failed to initialize", "Failed to load ELF", "[guest-branch:missing-target]",
               "[tarzan-memory-fault]", "[tarzan-iop-budget] entry=", "[launcher]")
    started = time.monotonic()
    started_wall = time.time()
    with (run_dir / "stdout.log").open("wb") as output, (run_dir / "events.jsonl").open("w") as events:
        process = subprocess.Popen(command, cwd=run_dir, stdout=output, stderr=subprocess.STDOUT)
        result["pid"] = process.pid
        (run_dir / "launch.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"pid": process.pid, "directory": str(run_dir)}), flush=True)
        deadline = started + args.seconds
        with (run_dir / "stdout.log").open("r", errors="replace") as reader:
            pending = ""
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    result["timed_out"] = True
                    break
                chunk = reader.read()
                pending += chunk
                complete = pending.split("\n")
                pending = complete.pop()
                observed = "\n".join(complete) + pending
                for line in complete:
                    if any(tag in line for tag in ("[tarzan-mpeg] is-end", "[tarzan-mpeg] get-picture",
                                                  "[tarzan-iop-rpc]", "[tarzan-iop-budget] entry=",
                                                  "[guest-branch:missing-target]", "Metal diagnostic capture saved")):
                        events.write(json.dumps({"seconds": round(time.monotonic() - started, 4),
                                                 "line": line}) + "\n")
                events.flush()
                if args.stop_on_failure:
                    if any(marker in observed for marker in markers):
                        result["diagnostic_stop"] = True
                        break
                try:
                    process.wait(timeout=min(0.25, max(0.001, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    result["exit_code"] = process.returncode
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["crash_report"] = None
    if process.returncode < 0 and not result["timed_out"] and not result["diagnostic_stop"]:
        # macOS writes symbolicated reports asynchronously. Match our PID so a
        # previous runner crash can never be attributed to this check.
        reports = Path.home() / "Library/Logs/DiagnosticReports"
        for attempt in range(10):
            for report in reports.glob("ps2EntryRunner*.crash"):
                if report.stat().st_mtime < started_wall - 5:
                    continue
                header = report.read_text(errors="replace")[:2000]
                if f"ps2EntryRunner [{process.pid}]" in header:
                    saved = run_dir / report.name
                    shutil.copy2(report, saved)
                    result["crash_report"] = str(saved)
                    break
            if result["crash_report"]:
                break
            time.sleep(0.5)
    log = (run_dir / "stdout.log").read_text(errors="replace")
    result["failure_lines"] = [line[:1000] for line in log.splitlines()
                               if any(marker in line for marker in markers)][-30:]
    (run_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return int(bool(result["failure_lines"]) or (not result["timed_out"] and process.returncode != 0))


if __name__ == "__main__":
    raise SystemExit(main())
