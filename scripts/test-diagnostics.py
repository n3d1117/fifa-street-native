#!/usr/bin/env python3
"""Check failure classification and early termination without launching the game."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("diagnostics", ROOT / "scripts/diagnose-native.py")
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


def classify(directory, result):
    (directory / "result.json").write_text(json.dumps(result))
    with contextlib.redirect_stdout(io.StringIO()):
        code = diagnostics.save_report(directory)
    return code, json.loads((directory / "diagnosis.json").read_text())


with tempfile.TemporaryDirectory(prefix="tarzan-diagnostics-") as temporary:
    directory = Path(temporary)
    log = directory / "stdout.log"
    log.write_text("normal progress\n")
    code, report = classify(directory, {"timed_out": True, "exit_code": -9})
    assert code == 0 and report["status"] == "bounded_run"
    code, report = classify(directory, {"timed_out": False, "exit_code": -11})
    assert code == 1 and report["status"] == "failed"
    log.write_text("[tarzan-iop-budget] entry=0x100 pc=0x104 instructions=2000000\n")
    code, report = classify(directory, {"timed_out": True, "exit_code": -9})
    assert code == 1 and len(report["iop_budget_exhaustions"]) == 1

    # Repeated pictures do not inflate FPS; a movie reset starts a new interval.
    records = [(1, "[tarzan-mpeg] is-end served=25"),
               (2, "[tarzan-mpeg] get-picture frame=50"),
               (2.5, "[tarzan-mpeg] get-picture frame=50"),
               (3, "[tarzan-mpeg] get-picture frame=0"),
               (4, "[tarzan-mpeg] is-end served=25"),
               (4, "[tarzan-iop-rpc] sid=0x1234 fn=0x15 us=100"),
               (4.1, "[tarzan-iop-rpc] sid=0x1234 fn=0x15 us=300")]
    (directory / "events.jsonl").write_text("".join(json.dumps({"seconds": t, "line": line}) + "\n"
                                                    for t, line in records))
    timing = diagnostics.timing_summary(log)
    assert [movie["observed_movie_fps"] for movie in timing["movies"]] == [25, 25]
    assert timing["rpc_sampled_timings"][0]["median_us"] == 200

    # An executable emitting a failure must be stopped before its nominal timeout.
    runner = directory / "fake-runner"
    runner.write_text("#!/usr/bin/env python3\nimport time\n"
                      "print('[tarzan-iop-budget] entry=0x100 instructions=2000000', flush=True)\n"
                      "time.sleep(30)\n")
    runner.chmod(0o700)
    completed = subprocess.run([sys.executable, str(ROOT / "scripts/run-native-check.py"),
                                "--runner", str(runner), "--seconds", "10",
                                "--label", "diagnostic-selftest", "--stop-on-failure"],
                               capture_output=True, text=True, timeout=10)
    launch = json.loads(completed.stdout.splitlines()[0])
    run = json.loads((Path(launch["directory"]) / "result.json").read_text())
    assert completed.returncode == 1 and run["diagnostic_stop"] and not run["timed_out"]
    assert run["elapsed_seconds"] < 5 and run["runner_sha256"]
    assert (Path(launch["directory"]) / "launch.json").exists()

print("Diagnostics: timeout/crash/budget classification, movie timing, RPC timing, and early stop passed.")
