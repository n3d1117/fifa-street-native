#!/usr/bin/env python3
"""Validate shared sources, host code, focused tests, then the game executable.

No GUI is launched unless --run is explicitly supplied. Failures stop the
pipeline immediately and retain the exact command and log for review.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
NINJA = ROOT.parent / "tarzan-native/tools/build/ninja-1.13.1/ninja"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--label", default="validated-menu-transition")
    args = parser.parse_args()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = ROOT / "logs" / (stamp + "-validation")
    directory.mkdir(parents=True)
    steps = [
        ("source-mirrors", [sys.executable, "scripts/check-runtime-mirrors.py"]),
        ("original-diff", ["git", "-C", "source/PS2Recomp", "diff", "--check"]),
        ("metal-diff", ["git", "-C", "source/PS2Recomp-metal", "diff", "--check"]),
        ("diagnostic-selftest", [sys.executable, "scripts/test-diagnostics.py"]),
        # Catch host/API/link errors before paying for translated game compilation.
        ("host-build", [str(NINJA), "-C", "build/game-metal", "-j2",
                        "ps2_runtime", "tarzanMetalHostSmoke"]),
        ("test-build", [str(NINJA), "-C", "build/runtime-tests", "-j2", "ps2x_tests",
                        "ps2_iop_emulator_tests", "ps2_iop_import_tests",
                        "ps2_iop_compatibility_tests", "ps2_iop_import_version_tests"]),
        ("runtime-regressions", ["bash", "scripts/test-native-regressions.sh"]),
    ]
    for name in ("ps2_iop_emulator_tests", "ps2_iop_import_tests",
                 "ps2_iop_compatibility_tests", "ps2_iop_import_version_tests"):
        steps.append((name, [str(ROOT / "build/runtime-tests/ps2xIOP" / name)]))
    steps.append(("game-build", [str(NINJA), "-C", "build/game-metal", "-j2", "ps2EntryRunner"]))
    report = {"started_utc": stamp, "steps": [], "gameplay_verified": False}
    report_path = directory / "validation.json"
    for name, command in steps:
        log = directory / (name + ".log")
        with log.open("w") as output:
            result = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
        report["steps"].append({"name": name, "command": command,
                                "exit_code": result.returncode, "log": str(log)})
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(f"{'PASS' if result.returncode == 0 else 'FAIL'} {name}: {log}", flush=True)
        if result.returncode:
            return result.returncode
    runner = ROOT / "build/game-metal/ps2recomp/ps2xRuntime/ps2EntryRunner"
    report["runner_sha256"] = hashlib.sha256(runner.read_bytes()).hexdigest()
    report["status"] = "build_and_focused_tests_passed"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    if args.run:
        return subprocess.run([sys.executable, "scripts/diagnose-native.py", "--runner", str(runner),
                               "--seconds", str(args.seconds), "--label", args.label,
                               "--sample", "--sample-after", str(min(15, args.seconds)),
                               "--iop-dump", "--require-spu2", "--gpu-capture",
                               "--capture-frames", "4", "--capture-start-field", "1800",
                               "--capture-interval", "600"], cwd=ROOT).returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
