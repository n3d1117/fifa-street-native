#!/usr/bin/env python3
"""USB device signing, install, data transfer and run summaries for ./rx device."""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import plistlib
import re
import select
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_APP = ROOT / "build/embedded/iphoneos/FIFA Street PS2.app"
CONFIG = Path.home() / ".config/rx/device.json"
STATE = ROOT / "build/device/state.json"
TUNNEL_URL = os.environ.get("RX_TUNNEL_URL", "http://127.0.0.1:49151")


class Missing(Exception):
    pass


class Failed(Exception):
    pass


def cli():
    path = os.environ.get(
        "RX_PYMOBILEDEVICE3",
        str(Path.home() / ".local/share/rx-device-venv/bin/pymobiledevice3"),
    )
    if not Path(path).is_file():
        raise Missing(
            f"pymobiledevice3 CLI missing at {path} -> install it in the rx-device-venv or set RX_PYMOBILEDEVICE3"
        )
    return path


def command(args, timeout=30, check=True, text=True):
    env = os.environ.copy()
    # macOS Python sets this for its own interpreter launcher; inheriting it can
    # make a different venv interpreter start with the wrong executable path.
    env.pop("__PYVENV_LAUNCHER__", None)
    try:
        p = subprocess.run(
            [str(x) for x in args],
            capture_output=True,
            text=text,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as e:
        raise Failed(f"timed out after {timeout}s: {args[0]}") from e
    if check and p.returncode:
        out = p.stderr if text else b""
        raise Failed(
            f"command failed ({p.returncode}): {' '.join(map(str,args))}: {str(out)[-1000:]}"
        )
    return p


def pmd(*args, timeout=30, check=True):
    return command([cli(), *args], timeout=timeout, check=check)


def tunnel_devices():
    try:
        with urllib.request.urlopen(TUNNEL_URL, timeout=4) as response:
            return json.loads(response.read())
    except Exception as e:
        raise Missing(
            f"tunneld unavailable at {TUNNEL_URL} -> start the user-managed tunneld"
        ) from e


def choose_udid(requested=None):
    devices = tunnel_devices()
    ids = sorted(devices) if isinstance(devices, dict) else []
    if requested:
        if requested not in ids:
            raise Missing(
                f"device {requested} is not listed by tunneld -> connect and pair it, then retry"
            )
        return requested
    if len(ids) != 1:
        raise Missing(
            f"tunneld lists {len(ids)} devices -> pass --udid from: {', '.join(ids) or '(none)'}"
        )
    return ids[0]


def global_device_args(udid):
    return ["--udid", udid]


def default_app(udid):
    devices = pmd("usbmux", "list", timeout=15).stdout
    try:
        rows = json.loads(devices)
        row = next((d for d in rows if d.get("UniqueDeviceID") == udid), {})
    except (json.JSONDecodeError, TypeError):
        row = {}
    if str(row.get("ProductType", "")).startswith("AppleTV"):
        return ROOT / "build/embedded/appletvos/FIFA Street PS2.app"
    return DEFAULT_APP


def app_records(udid):
    result = pmd("apps", "list", *global_device_args(udid), timeout=60, check=False)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    if isinstance(data, list):
        return {
            v.get("CFBundleIdentifier"): v
            for v in data
            if isinstance(v, dict) and v.get("CFBundleIdentifier")
        }
    return {}


def installed_bundle(udid, info, preferred=None):
    records = app_records(udid)
    if preferred in records:
        return preferred
    executable = info.get("CFBundleExecutable")
    display = info.get("CFBundleDisplayName") or info.get("CFBundleName")
    matches = [
        bundle
        for bundle, row in records.items()
        if row.get("CFBundleExecutable") == executable
    ]
    if not matches and display:
        matches = [
            bundle
            for bundle, row in records.items()
            if row.get("CFBundleDisplayName", row.get("CFBundleName")) == display
        ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise Failed(
            f"multiple installed apps match {display or executable}; select the device with --udid"
        )
    raise Failed(
        f"no installed app matches {display or executable}; run ./rx device install first"
    )


def config():
    data = {}
    if CONFIG.exists():
        try:
            data = json.loads(CONFIG.read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise Failed(f"invalid {CONFIG}: {e}") from e
    keychain = os.environ.get("RX_SIGN_KEYCHAIN", data.get("keychain"))
    identity = os.environ.get("RX_SIGN_IDENTITY", data.get("identity"))
    password = data.get("keychain_password")
    if not keychain or not identity:
        raise Missing(
            f"signing config missing in {CONFIG} -> set RX_SIGN_KEYCHAIN/RX_SIGN_IDENTITY and keychain_password there"
        )
    return {
        "keychain": str(Path(keychain).expanduser()),
        "identity": identity.upper(),
        "password": password,
    }


def valid_identity(cfg):
    result = command(
        ["security", "find-identity", "-v", "-p", "codesigning", cfg["keychain"]],
        timeout=15,
        check=False,
    )
    if result.returncode or cfg["identity"] not in result.stdout.upper():
        raise Failed(
            f"identity {cfg['identity']} is not a valid codesigning identity in {cfg['keychain']}"
        )
    return True


def profile_id(profile):
    appid = profile.get("Entitlements", {}).get("application-identifier", "")
    return appid.split(".", 1)[1] if "." in appid else None


def read_profile(path):
    raw = Path(path).read_bytes()
    try:
        return plistlib.loads(raw)
    except (ValueError, plistlib.InvalidFileException):
        decoded = command(
            ["security", "cms", "-D", "-i", str(path)], timeout=20, check=False
        )
        if decoded.returncode:
            raise ValueError("not a decoded plist or CMS provisioning profile")
        return plistlib.loads(decoded.stdout.encode())


def select_profile(directory, udid, identity, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    matches = []
    for path in Path(directory).glob("**/*"):
        if not path.is_file():
            continue
        try:
            data = read_profile(path)
            expiry = data.get("ExpirationDate")
            if not expiry or expiry.replace(tzinfo=dt.timezone.utc) <= now:
                continue
            certs = data.get("DeveloperCertificates", [])
            if not any(
                hashlib.sha1(cert).hexdigest().upper() == identity.upper()
                for cert in certs
            ):
                continue
            if udid not in data.get("ProvisionedDevices", []) and not data.get(
                "ProvisionsAllDevices"
            ):
                continue
            bundle = profile_id(data)
            if not bundle:
                continue
            matches.append((expiry, path, data, bundle))
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
    if not matches:
        raise Failed(
            "no unexpired provisioning profile matches this identity and device; create/install a matching profile"
        )
    return max(matches, key=lambda x: x[0])


def info_plist(app):
    try:
        return plistlib.loads((Path(app) / "Info.plist").read_bytes())
    except (OSError, ValueError) as e:
        raise Failed(f"invalid app bundle {app}: {e}") from e


def source_hash(app, lean):
    app = Path(app)
    h = hashlib.sha256()
    paths = [
        app / "Info.plist",
        app / info_plist(app).get("CFBundleExecutable", "FIFAStreetPS2"),
    ]
    fw = app / "Frameworks"
    if fw.exists():
        paths.extend(
            p
            for p in fw.rglob("*")
            if p.is_file()
            and (p.suffix in (".dylib", ".framework") or ".framework/" in str(p))
        )
    for path in sorted(set(paths)):
        if path.is_file():
            h.update(str(path.relative_to(app)).encode() + b"\0")
            with path.open("rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
    h.update(b"lean=1" if lean else b"lean=0")
    return h.hexdigest()


def state_read():
    try:
        return json.loads(STATE.read_text()) if STATE.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def state_write(data):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, STATE)


def do_doctor(udid_arg):
    exe = cli()
    udid = choose_udid(udid_arg)
    listed = pmd("usbmux", "list", timeout=15).stdout
    if udid not in listed:
        raise Failed(f"device {udid} is not paired/listed by usbmuxd")
    status = pmd(
        "amfi",
        "developer-mode-status",
        *global_device_args(udid),
        timeout=20,
        check=False,
    )
    cfg = config()
    valid_identity(cfg)
    cache = ROOT / "build/device/profiles"
    cache.mkdir(parents=True, exist_ok=True)
    pmd("provision", "dump", str(cache), *global_device_args(udid), timeout=60)
    expiry, _path, profile, bundle = select_profile(cache, udid, cfg["identity"])
    days = (
        expiry.replace(tzinfo=dt.timezone.utc) - dt.datetime.now(dt.timezone.utc)
    ).total_seconds() / 86400
    installed = app_records(udid)
    app = default_app(udid)
    info = info_plist(app) if (app / "Info.plist").is_file() else {}
    installed_info = pmd(
        "apps", "query", bundle, *global_device_args(udid), timeout=30, check=False
    )
    try:
        version = (
            json.loads(installed_info.stdout)
            .get(bundle, {})
            .get("CFBundleShortVersionString", "unknown")
        )
    except (json.JSONDecodeError, AttributeError):
        version = "unknown"
    print(f"device: {udid} (paired)")
    print(
        f"developer mode: {status.stdout.strip() or ('available' if status.returncode == 0 else 'unknown')}"
    )
    print(f"signing identity: {cfg['identity']} valid")
    print(
        f"profile: {bundle}, {days:.1f} days left"
        + (" (WARN: expires within 2 days)" if days < 2 else "")
    )
    print(
        f"app: {bundle} {'installed' if bundle in installed else 'not installed'}, version {version}"
    )


def sign_stage(app, lean, udid):
    cfg = config()
    valid_identity(cfg)
    cache = ROOT / "build/device/profiles"
    cache.mkdir(parents=True, exist_ok=True)
    pmd("provision", "dump", str(cache), *global_device_args(udid), timeout=60)
    expiry, profile_path, profile, bundle = select_profile(cache, udid, cfg["identity"])
    source = Path(app).resolve()
    if not (source / "Info.plist").is_file():
        raise Missing(
            f"app bundle missing: {source} -> have the other worker build the embedded app"
        )
    stage = ROOT / "build/device/stage/Payload" / source.name
    stage.parent.mkdir(parents=True, exist_ok=True)
    if stage.exists():
        shutil.rmtree(stage)
    command(["cp", "-cR", str(source), str(stage)], timeout=600)
    if lean:
        shutil.rmtree(stage / "game", ignore_errors=True)
    command(
        [
            "/usr/libexec/PlistBuddy",
            "-c",
            f"Set :CFBundleIdentifier {bundle}",
            str(stage / "Info.plist"),
        ],
        timeout=10,
    )
    shutil.copy2(profile_path, stage / "embedded.mobileprovision")
    if cfg.get("password"):
        command(
            ["security", "unlock-keychain", "-p", cfg["password"], cfg["keychain"]],
            timeout=15,
        )
    fw = stage / "Frameworks"
    if fw.exists():
        for p in sorted(fw.iterdir(), key=lambda x: (x.is_dir(), x.name)):
            if p.suffix in (".dylib", ".framework"):
                command(
                    [
                        "codesign",
                        "-f",
                        "-s",
                        cfg["identity"],
                        "--keychain",
                        cfg["keychain"],
                        str(p),
                    ],
                    timeout=180,
                )
    ent = stage.parent / "entitlements.plist"
    ent.write_bytes(plistlib.dumps(profile.get("Entitlements", {})))
    command(
        [
            "codesign",
            "-f",
            "-s",
            cfg["identity"],
            "--keychain",
            cfg["keychain"],
            "--entitlements",
            str(ent),
            str(stage),
        ],
        timeout=300,
    )
    return stage, bundle


def do_install(args):
    udid = choose_udid(args.udid)
    app = Path(args.app or default_app(udid))
    src_hash = source_hash(app, args.lean)
    state = state_read()
    prior = state.get("install", {})
    if (
        not args.force
        and prior.get("hash") == src_hash
        and prior.get("lean") == args.lean
        and prior.get("udid") == udid
    ):
        print("installed app unchanged; skipped")
        return
    stage, bundle = sign_stage(app, args.lean, udid)
    pmd("apps", "install", str(stage), *global_device_args(udid), timeout=900)
    state["install"] = {
        "hash": src_hash,
        "lean": args.lean,
        "bundle": bundle,
        "source": str(app),
        "udid": udid,
    }
    state_write(state)
    print(f"installed {bundle} ({'lean' if args.lean else 'full'})")


def hash_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def game_manifest(root):
    root = Path(root)
    return {
        str(p.relative_to(root)): {"size": p.stat().st_size, "sha1": hash_file(p)}
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def do_push(args):
    udid = choose_udid(args.udid)
    app = Path(args.app or default_app(udid))
    game = app / "game"
    if not game.is_dir():
        raise Missing(
            f"game data missing at {game} -> build a full app or provide --app"
        )
    state = state_read()
    manifest = game_manifest(game)
    bundle = installed_bundle(
        udid, info_plist(app), state.get("install", {}).get("bundle")
    )
    target = {"udid": udid, "bundle": bundle}
    previous = (
        state.get("game_manifest", {})
        if state.get("game_manifest_target") == target else {}
    )
    changed = [rel for rel, meta in manifest.items() if previous.get(rel) != meta]
    staging_root = ROOT / "build/device"
    staging_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="data-", dir=staging_root) as temporary:
        staged_game = Path(temporary) / "game"
        staged_game.mkdir()
        for rel in changed:
            destination = staged_game / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(game / rel, destination)
            except OSError:
                shutil.copy2(game / rel, destination)
        (staged_game / ".rx-manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        )
        pmd(
            "apps", "push", bundle, str(staged_game), "Documents",
            *global_device_args(udid), timeout=900,
        )
    state["game_manifest"] = manifest
    state["game_manifest_target"] = target
    state_write(state)
    print(f"pushed game data: {len(changed)} changed of {len(manifest)} files")


def percentile(values, pct):
    values = sorted(
        float(v)
        for v in values
        if isinstance(v, (int, float)) and math.isfinite(float(v))
    )
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    at = (len(values) - 1) * pct / 100
    lo = int(at)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (at - lo)


def jsonl(path):
    rows = []
    if Path(path).exists():
        content = Path(path).read_text(errors="replace")
        try:
            value = json.loads(content)
            return value if isinstance(value, list) else [value]
        except json.JSONDecodeError:
            pass
        for line in content.splitlines():
            try:
                value = json.loads(line)
                rows.extend(value if isinstance(value, list) else [value])
            except json.JSONDecodeError:
                pass
    return rows


def metric_summary(rows, keys, prefix):
    result = {}
    for key in keys:
        vals = [
            r[key]
            for r in rows
            if isinstance(r, dict) and isinstance(r.get(key), (int, float))
        ]
        if vals:
            result[f"{prefix}_{key}_mean"] = statistics.mean(vals)
            result[f"{prefix}_{key}_p95"] = percentile(vals, 95)
            if key in ("physFootprint", "memory"):
                result[f"{prefix}_{key}_peak"] = max(vals)
    return result


def summarize(run_dir):
    root = Path(run_dir)
    perf = []
    try:
        run_meta = json.loads((root / "run.json").read_text())
    except (OSError, json.JSONDecodeError):
        run_meta = {}
    for p in (root / "perf").glob("**/*.jsonl") if (root / "perf").exists() else []:
        perf.extend(jsonl(p))
    log = (
        (root / "native.log").read_text(errors="replace")
        if (root / "native.log").exists()
        else ""
    )
    launch_time = run_meta.get("launch_host_time")
    summaries = [r for r in perf if r.get("type") == "summary" and "wall_time" in r]
    if summaries and launch_time is not None:
        fresh = [r for r in summaries if r["wall_time"] >= launch_time - 5]
        if not fresh:
            perf = []
        summaries = fresh
    offsets = [r["wall_time"] - r["t"] for r in summaries if "t" in r]
    clock_offset = statistics.median(offsets) if offsets else None

    def host_time(row):
        for key in ("_host_time", "host_time", "timestamp"):
            if isinstance(row.get(key), (int, float)):
                return row[key]
        if clock_offset is not None and isinstance(row.get("t"), (int, float)):
            return row["t"] + clock_offset
        return None

    confirmed = []
    for row in perf:
        if (
            row.get("gameplay") is True
            or row.get("gameplay_confirmed")
            or row.get("event") in ("gameplay_confirmed", "gameplay-start")
            or row.get("type") in ("gameplay_confirmed", "gameplay-start")
        ):
            when = host_time(row)
            if when is not None and (launch_time is None or when >= launch_time):
                confirmed.append(when)
    start = min(confirmed) if confirmed else None
    if start is not None and launch_time is not None:
        start = max(start, launch_time + run_meta.get("warmup_s", 0))
    fps_rows = [r for r in jsonl(root / "gpu.jsonl") if "_error" not in r]
    sys_rows = [r for r in jsonl(root / "sysmon.jsonl") if "_error" not in r]
    bat = [r for r in jsonl(root / "battery.jsonl") if "_error" not in r]
    if start is not None:
        fps_rows = [r for r in fps_rows if float(r.get("_host_time", 0)) >= start]
        sys_rows = [r for r in sys_rows if float(r.get("_host_time", 0)) >= start]
    else:
        if fps_rows:
            cutoff = max(r.get("_host_time", 0) for r in fps_rows) - 30
            fps_rows = [r for r in fps_rows if r.get("_host_time", 0) >= cutoff]
            sys_rows = [r for r in sys_rows if r.get("_host_time", 0) >= cutoff]
    out = {
        "run": str(root),
        "gpu": metric_summary(
            fps_rows,
            ["Device Utilization %", "Renderer Utilization %", "Tiler Utilization %"],
            "gpu",
        ),
        "process": metric_summary(
            sys_rows, ["cpuUsage", "physFootprint", "memory", "threads"], "process"
        ),
        "gameplay_window_start": start,
        "samples": {"gpu": len(fps_rows), "sysmon": len(sys_rows), "battery": len(bat)},
    }
    fps = [
        r.get("CoreAnimationFramesPerSecond")
        for r in fps_rows
        if isinstance(r.get("CoreAnimationFramesPerSecond"), (int, float))
    ]
    if fps:
        out["core_animation_fps"] = {
            "median": statistics.median(fps),
            "p5": percentile(fps, 5),
            "min": min(fps),
        }
    temps = [
        r.get("Temperature", r.get("VirtualTemperature"))
        for r in bat
        if isinstance(r.get("Temperature", r.get("VirtualTemperature")), (int, float))
    ]
    if temps:
        out["battery_temperature"] = {
            "start": temps[0],
            "end": temps[-1],
            "delta": temps[-1] - temps[0],
        }
    thermal = []
    for row in perf:
        for k, v in row.items():
            if "thermal" in k.lower() and isinstance(v, (int, float)):
                thermal.append(v)
    m = re.findall(r"thermal(?:State| state)[=: ]+([0-9]+)", log, re.I)
    thermal += [int(v) for v in m]
    if thermal:
        out["thermal_state_max"] = max(thermal)
    numeric = {}
    summary_numbers = {}

    def number_fields(obj, prefix=""):
        found = {}
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in ("type", "timestamp", "t", "_host_time", "wall_time"):
                    continue
                name = f"{prefix}.{key}" if prefix else str(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    found[name] = value
                elif isinstance(value, dict):
                    found.update(number_fields(value, name))
        return found

    for row in perf:
        if row.get("type") == "summary":
            summary_numbers.update(number_fields(row))
    frame_rows = [
        r
        for r in perf
        if r.get("type") not in ("summary",)
        and any(
            isinstance(v, (int, float))
            for k, v in r.items()
            if k not in ("timestamp", "t")
        )
    ]
    if start is not None:
        frame_rows = [
            r for r in frame_rows
            if host_time(r) is not None and host_time(r) >= start
        ]
    for row in frame_rows:
        for k, v in number_fields(row).items():
            numeric.setdefault(k, []).append(v)
    for k, vals in list(numeric.items()):
        if isinstance(vals, list):
            numeric[k] = {"mean": statistics.mean(vals), "p95": percentile(vals, 95)}
    if numeric:
        out["in_app_perf"] = numeric
    if summary_numbers:
        out["in_app_summary"] = summary_numbers
    (root / "summary.json").write_text(json.dumps(out, indent=2) + "\n")
    print_summary(out)
    return out


def print_summary(data):
    print("Metric                         Value")
    print("------------------------------------")

    def row(k, v):
        print(f"{k:<30} {v}")

    for key, value in data.get("gpu", {}).items():
        row(key, f"{value:.2f}")
    for key, value in data.get("process", {}).items():
        row(key, f"{value:.2f}")
    ca = data.get("core_animation_fps", {})
    if ca:
        row(
            "CoreAnimation FPS median/p5/min",
            " / ".join(f"{ca[k]:.1f}" for k in ("median", "p5", "min")),
        )
    bt = data.get("battery_temperature", {})
    if bt:
        row(
            "Battery temp start/end/delta",
            " / ".join(f"{bt[k]:.2f}" for k in ("start", "end", "delta")),
        )
    if "thermal_state_max" in data:
        row("Thermal state max", data["thermal_state_max"])
    row(
        "Samples GPU/sysmon/battery",
        " / ".join(str(data["samples"][k]) for k in ("gpu", "sysmon", "battery")),
    )


def stream(cmd, output, stop, ready_event=None, device=False):
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("w") as f:
            errors = output.with_suffix(output.suffix + ".stderr.log").open("w")
            env = os.environ.copy()
            env.pop("__PYVENV_LAUNCHER__", None)
            p = subprocess.Popen(
                [cli(), *cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env
            )
            buffer = ""
            decoder = json.JSONDecoder()

            def write_obj(obj):
                if isinstance(obj, list):
                    for item in obj:
                        write_obj(item)
                elif isinstance(obj, dict):
                    obj["_host_time"] = time.time()
                    f.write(json.dumps(obj) + "\n")
                    f.flush()
                    if ready_event:
                        ready_event.set()

            def parse_buffer(final=False):
                nonlocal buffer
                while buffer.strip():
                    start = len(buffer) - len(buffer.lstrip())
                    try:
                        obj, end = decoder.raw_decode(buffer, start)
                    except json.JSONDecodeError:
                        if buffer[start : start + 1] not in ("{", "[") or final:
                            newline = buffer.find("\n", start)
                            if newline < 0 and not final:
                                break
                            newline = len(buffer) - 1 if newline < 0 else newline
                            errors.write(buffer[start : newline + 1])
                            errors.flush()
                            buffer = buffer[newline + 1 :]
                            continue
                        break
                    write_obj(obj)
                    buffer = buffer[end:]

            while not stop.is_set():
                readable, _, _ = select.select([p.stdout], [], [], 0.5)
                if not readable:
                    if p.poll() is not None:
                        break
                    continue
                data = os.read(p.stdout.fileno(), 65536)
                if not data:
                    if p.poll() is not None:
                        break
                    continue
                buffer += data.decode(errors="replace")
                parse_buffer()
            parse_buffer(final=True)
            p.terminate()
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
            errors.close()
    except Exception as e:
        with output.open("a") as f:
            f.write(json.dumps({"_error": str(e), "_host_time": time.time()}) + "\n")


def stream_sysmon(cmd, output, stop):
    raw = output.with_suffix(".raw.jsonl")
    errors = output.with_suffix(output.suffix + ".stderr.log")
    output.parent.mkdir(parents=True, exist_ok=True)
    offset = 0
    try:
        env = os.environ.copy()
        env.pop("__PYVENV_LAUNCHER__", None)
        p = subprocess.Popen(
            [cli(), *cmd, "--output", str(raw)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        with output.open("w") as f, errors.open("w") as err:
            while not stop.is_set():
                if raw.exists():
                    with raw.open("rb") as source:
                        source.seek(offset)
                        data = source.read()
                        offset = source.tell()
                    for line in data.decode(errors="replace").splitlines():
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        obj["_host_time"] = time.time()
                        f.write(json.dumps(obj) + "\n")
                        f.flush()
                if p.poll() is not None:
                    break
                readable, _, _ = select.select([p.stdout], [], [], 0.2)
                if readable:
                    line = os.read(p.stdout.fileno(), 4096)
                    if line:
                        err.write(line.decode(errors="replace"))
                        err.flush()
            if raw.exists():
                with raw.open("rb") as source:
                    source.seek(offset)
                    for line in source:
                        try:
                            obj = json.loads(line)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        obj["_host_time"] = time.time()
                        f.write(json.dumps(obj) + "\n")
                        f.flush()
            p.terminate()
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
    except Exception as e:
        with output.open("a") as f:
            f.write(json.dumps({"_error": str(e), "_host_time": time.time()}) + "\n")
    finally:
        raw.unlink(missing_ok=True)


def do_run(args):
    if args.seconds < 1 or args.warmup < 0:
        raise Failed("seconds must be positive and warmup non-negative")
    udid = choose_udid(args.udid)
    app = Path(args.app or default_app(udid))
    info = info_plist(app)
    state = state_read()
    bundle = installed_bundle(udid, info, state.get("install", {}).get("bundle"))
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = re.sub(r"[^A-Za-z0-9_.-]+", "-", args.label or "run").strip("-") or "run"
    out = ROOT / "build/device-runs" / f"{stamp}-{label}"
    out.mkdir(parents=True, exist_ok=False)
    (out / "shots").mkdir()
    # Launch once to obtain the pid; DVT process listing is used to stop any
    # previous instance by bundle/name.
    processes = pmd(
        "developer",
        "dvt",
        "sysmon",
        "process",
        "single",
        *global_device_args(udid),
        "--key",
        "pid",
        "--key",
        "name",
        timeout=30,
        check=False,
    ).stdout
    for line in processes.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("name") == info.get("CFBundleExecutable"):
            pmd(
                "developer",
                "dvt",
                "kill",
                "--tunnel",
                udid,
                str(obj.get("pid")),
                timeout=20,
                check=False,
            )
    env = [] if args.no_match else ["PS2X_BOOT_MATCH=venue=3", "PS2X_AUTOPILOT=match"]
    env += ["PS2X_PERF_LOG=1", f"PS2X_USER_DIR=rx-run-{stamp}"] + args.env
    launch = ["developer", "dvt", "launch", "--tunnel", udid]
    for e in env:
        launch += ["--env", e]
    launch_monotonic = time.monotonic()
    launch_host_time = time.time()
    result = pmd(*launch, bundle, timeout=60)
    match = re.search(r"pid\s+(\d+)", result.stdout, re.I)
    if not match:
        raise Failed(
            f"launch output did not include process pid: {result.stdout.strip()}"
        )
    pid = int(match.group(1))
    stop = threading.Event()
    (out / "run.json").write_text(
        json.dumps(
            {
                "udid": udid,
                "bundle": bundle,
                "pid": pid,
                "launch_host_time": launch_host_time,
                "seconds": args.seconds,
                "warmup_s": args.warmup,
                "env": env,
            },
            indent=2,
        )
        + "\n"
    )
    # DVT's process launch can outlive the initial RemoteXPC service
    # transition.
    time.sleep(2)
    gpu_ready = threading.Event()
    gpu_thread = threading.Thread(
        target=stream,
        args=(
            ["developer", "dvt", "graphics", "--tunnel", udid],
            out / "gpu.jsonl",
            stop,
            gpu_ready,
        ),
        daemon=True,
    )
    gpu_thread.start()
    gpu_ready.wait(timeout=5)
    sys_thread = threading.Thread(
        target=stream_sysmon,
        args=(
            [
                "developer",
                "dvt",
                "sysmon",
                "process",
                "monitor",
                "process",
                "--filter",
                f"pid={pid}",
                "--choose",
                "first",
                "--keep-monitoring",
                "--key",
                "pid",
                "--key",
                "cpuUsage",
                "--key",
                "physFootprint",
                "--key",
                "threadCount",
                "--tunnel",
                udid,
            ],
            out / "sysmon.jsonl",
            stop,
        ),
        daemon=True,
    )
    sys_thread.start()
    threads = [gpu_thread, sys_thread]
    began = launch_monotonic
    deadline = began + args.warmup + args.seconds
    next_battery = began
    shots = 0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_battery:
            try:
                b = pmd(
                    "diagnostics",
                    "battery",
                    "single",
                    *global_device_args(udid),
                    timeout=15,
                    check=False,
                )
                obj = json.loads(b.stdout)
                with (out / "battery.jsonl").open("a") as f:
                    obj["_host_time"] = time.time()
                    f.write(json.dumps(obj) + "\n")
            except Exception as e:
                with (out / "battery.jsonl").open("a") as f:
                    f.write(
                        json.dumps({"_error": str(e), "_host_time": time.time()}) + "\n"
                    )
            next_battery = now + 10
        if args.shots_every > 0 and now - began >= (shots + 1) * args.shots_every:
            dest = out / "shots" / f"{shots:04d}.png"
            try:
                pmd(
                    "developer",
                    "dvt",
                    "screenshot",
                    "--tunnel",
                    udid,
                    str(dest),
                    timeout=25,
                )
            except Exception as e:
                (out / "shots" / f"{shots:04d}.error").write_text(str(e))
            shots += 1
        time.sleep(0.25)
    pmd("developer", "dvt", "kill", "--tunnel", udid, str(pid), timeout=20, check=False)
    stop.set()
    for t in threads:
        t.join(timeout=5)
    effective_env = dict(value.split("=", 1) for value in env)
    user_name = effective_env.get("PS2X_USER_DIR")
    remote = (
        f"Documents/{user_name}/Logs/native.log" if user_name
        else f"Library/Application Support/{info['CFBundleExecutable']}/Logs/native.log"
    )
    pulled = pmd(
        "apps",
        "pull",
        bundle,
        remote,
        str(out / "native.log"),
        *global_device_args(udid),
        timeout=90,
        check=False,
    )
    # Fetch the per-frame perf directory when the app has one in Documents.
    perf = out / "perf"
    perf.mkdir(exist_ok=True)
    pmd(
        "apps",
        "pull",
        bundle,
        "Documents/perf",
        str(perf),
        *global_device_args(udid),
        timeout=180,
        check=False,
    )
    data = summarize(out)
    print(
        f"run: {out.relative_to(ROOT)}; pid {pid}; native.log {'pulled' if pulled.returncode==0 else 'unavailable'}"
    )


def do_compare(a, b):
    x = json.loads((Path(a) / "summary.json").read_text())
    y = json.loads((Path(b) / "summary.json").read_text())
    flat = {}

    def walk(prefix, obj, d):
        for k, v in obj.items():
            name = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                walk(name, v, d)
            elif isinstance(v, (int, float)):
                d[name] = v

    walk("", x, flat)
    other = {}
    walk("", y, other)
    print(f"{'Metric':<38} {'A':>12} {'B':>12} {'Delta':>12} {'Delta %':>10}")
    for k, v in flat.items():
        if k not in other:
            continue
        delta = other[k] - v
        pct = (delta / v * 100) if v else None
        print(
            f"{k:<38} {v:>12.3f} {other[k]:>12.3f} {delta:>12.3f} {pct:>9.1f}%"
            if pct is not None
            else f"{k:<38} {v:>12.3f} {other[k]:>12.3f} {delta:>12.3f} {'n/a':>10}"
        )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="sub", required=True)
    d = sub.add_parser("doctor")
    d.add_argument("--udid")
    i = sub.add_parser("install")
    i.add_argument("--app")
    i.add_argument("--lean", action="store_true")
    i.add_argument("--force", action="store_true")
    i.add_argument("--udid")
    p = sub.add_parser("push-data")
    p.add_argument("--app")
    p.add_argument("--udid")
    r = sub.add_parser("run")
    r.add_argument("--seconds", type=int, required=True)
    r.add_argument("--warmup", type=float, default=0)
    r.add_argument("--label")
    r.add_argument("--env", action="append", default=[])
    r.add_argument("--no-match", action="store_true")
    r.add_argument("--shots-every", type=int, default=20)
    r.add_argument("--app")
    r.add_argument("--udid")
    s = sub.add_parser("summarize")
    s.add_argument("run_dir")
    c = sub.add_parser("compare")
    c.add_argument("runA")
    c.add_argument("runB")
    args = ap.parse_args(argv)
    try:
        if args.sub == "doctor":
            do_doctor(args.udid)
        elif args.sub == "install":
            do_install(args)
        elif args.sub == "push-data":
            do_push(args)
        elif args.sub == "run":
            do_run(args)
        elif args.sub == "summarize":
            summarize(args.run_dir)
        elif args.sub == "compare":
            do_compare(args.runA, args.runB)
        return 0
    except Missing as e:
        print(f"MISSING: {e}", file=sys.stderr)
        return 2
    except (Failed, OSError, ValueError, KeyError) as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
