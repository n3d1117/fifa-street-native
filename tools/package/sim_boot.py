#!/usr/bin/env python3
"""Boot one Apple simulator, install the embedded app, and capture launch evidence."""
import argparse
import os
from pathlib import Path
import plistlib
import re
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import game


def run(args, capture=False):
    return subprocess.run([str(x) for x in args], check=True, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None).stdout if capture else ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("platform", choices=["iphonesimulator", "appletvsimulator"])
    parser.add_argument("--seconds", type=int, default=45)
    args = parser.parse_args()
    base = ROOT / "build/embedded" / args.platform
    app = base / (game.GAME["app_name"] + ".app")
    logs = ROOT / "build/embedded/logs"
    logs.mkdir(parents=True, exist_ok=True)
    if args.platform == "iphonesimulator":
        device_name = "iPhone 12 Pro Max"
    else:
        devices = run(["xcrun", "simctl", "list", "devices", "available"], True)
        if re.search(r"^\s+Apple TV 4K \(2nd generation\) \([^)]*\) \(Shutdown\)$", devices, re.M):
            device_name = "Apple TV 4K (2nd generation)"
        else:
            device_name = "Apple TV 4K"
        if not re.search(r"^\s+" + re.escape(device_name) + r" \([^)]*\) \(Shutdown\)$", devices, re.M):
            types = run(["xcrun", "simctl", "list", "devicetypes"], True)
            runtimes = run(["xcrun", "simctl", "list", "runtimes"], True)
            type_id = "com.apple.CoreSimulator.SimDeviceType.Apple-TV-4K-2nd-generation-4K"
            runtime_id = "com.apple.CoreSimulator.SimRuntime.tvOS-14-5"
            if type_id not in types or runtime_id not in runtimes:
                raise RuntimeError("Apple TV 4K tvOS 14.5 simulator runtime/device type is unavailable")
            run(["xcrun", "simctl", "create", device_name, type_id, runtime_id])

    devices = run(["xcrun", "simctl", "list", "devices", "available"], True)
    device_matches = re.findall(r"^\s+" + re.escape(device_name) + r" \(([0-9A-Fa-f-]{36})\)", devices, re.M)
    if len(device_matches) != 1:
        raise RuntimeError(f"expected one {device_name} simulator, found {len(device_matches)}")
    device_id = device_matches[0]
    listed = run(["xcrun", "simctl", "list", "devices", "booted"], True)
    ids = re.findall(r"\(([0-9A-Fa-f-]{36})\) \(Booted\)", listed)
    for booted_id in ids:
        run(["xcrun", "simctl", "shutdown", booted_id])
    # simctl boot can exit 0 and leave the device shut down (Xcode 12.5.1, cold start); retry until listed as booted.
    for _ in range(30):
        subprocess.run(["xcrun", "simctl", "boot", device_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        if re.search(re.escape(device_id) + r"\) \(Booted\)", run(["xcrun", "simctl", "list", "devices", "booted"], True)):
            break
    else:
        raise RuntimeError(f"{device_name} simulator did not boot")
    simulator_app = Path(os.environ["DEVELOPER_DIR"]) / "Applications/Simulator.app"
    run(["open", "-a", simulator_app])
    run(["xcrun", "simctl", "bootstatus", device_id, "-b"])
    with (app / "Info.plist").open("rb") as stream:
        bundle = plistlib.load(stream)["CFBundleIdentifier"]
    run(["xcrun", "simctl", "install", device_id, app])
    data = ""
    for _ in range(60):
        result = subprocess.run(["xcrun", "simctl", "get_app_container", device_id, bundle, "data"],
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode == 0:
            data = result.stdout.strip()
            break
        time.sleep(0.5)
    if not data:
        raise RuntimeError("simulator app data container was not ready after install: " + result.stderr.strip())
    stem = "ios" if args.platform == "iphonesimulator" else "tvos"
    screenshot = logs / f"{stem}-simulator.png"
    console = logs / f"{stem}-simulator-console.log"
    screenshot.unlink(missing_ok=True)
    with console.open("wb") as output:
        process = subprocess.Popen(["xcrun", "simctl", "launch", "--console-pty", device_id, bundle],
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        time.sleep(args.seconds)
        capture = subprocess.run(["xcrun", "simctl", "io", "booted", "screenshot", str(screenshot)],
                                 text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if capture.returncode != 0 or not screenshot.is_file():
            detail = (capture.stdout + capture.stderr).strip()
            raise RuntimeError("simctl screenshot failed: " + detail)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    print(f"screenshot={screenshot}")
    print(f"console={console}")
    print(f"data={data}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print("sim boot: " + str(error), file=sys.stderr)
        raise SystemExit(1)
