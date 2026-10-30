#!/usr/bin/env python3
"""USB device signing, install, data transfer and run summaries for ./rx device."""
import argparse
import bisect
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
_TUNNEL_INFO = {}


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
        stderr = e.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        detail = f": {str(stderr)[-1000:]}" if stderr else ""
        raise Failed(f"timed out after {timeout}s: {args[0]}{detail}") from e
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


def configured_udid():
    """Default device when more than one is paired (iPhone plus Apple TV)."""
    if os.environ.get("RX_DEVICE_UDID"):
        return os.environ["RX_DEVICE_UDID"].strip()
    try:
        data = json.loads(CONFIG.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return (data.get("udid") or "").strip() or None


def choose_udid(requested=None):
    devices = tunnel_devices()
    aliases = {}
    resolved = {}
    unresolved = []
    if isinstance(devices, dict):
        for alias, endpoints in devices.items():
            found = False
            for endpoint in endpoints if isinstance(endpoints, list) else []:
                try:
                    address, port = endpoint["tunnel-address"], endpoint["tunnel-port"]
                    if not isinstance(address, str) or not address or not 0 < int(port) < 65536:
                        continue
                    key = (address, int(port))
                    if key not in resolved:
                        result = pmd("remote", "rsd-info", "--rsd", address, str(port),
                                     timeout=15, check=False)
                        resolved[key] = json.loads(result.stdout)["Properties"] if not result.returncode else {}
                    info = resolved[key]
                    identity = info["UniqueDeviceID"]
                    product = info["ProductType"]
                    if not isinstance(identity, str) or not identity or not isinstance(product, str) or not product:
                        continue
                except (Failed, ValueError, KeyError, TypeError):
                    continue
                if alias == identity or re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", alias):
                    aliases[alias] = identity
                aliases[identity] = identity
                _TUNNEL_INFO[identity] = info
                found = True
                break
            if not found:
                unresolved.append(alias)
    ids = sorted(set(aliases.values()))
    requested = requested or configured_udid()
    if requested:
        if requested not in aliases:
            raise Missing(
                f"device {requested} is not resolved by tunneld -> check its paired tunnel, then retry"
            )
        return aliases[requested]
    if len(ids) != 1 or unresolved:
        raise Missing(
            f"tunneld resolves {len(ids)} devices ({len(unresolved)} unavailable) -> pass --udid from: {', '.join(ids) or '(none)'}"
        )
    return ids[0]


def global_device_args(udid):
    return ["--tunnel", udid]


def product_type(udid):
    if udid in _TUNNEL_INFO:
        return str(_TUNNEL_INFO[udid].get("ProductType", ""))
    devices = pmd("usbmux", "list", timeout=15).stdout
    try:
        rows = json.loads(devices)
        row = next((d for d in rows if d.get("UniqueDeviceID") == udid), {})
    except (json.JSONDecodeError, TypeError):
        row = {}
    return str(row.get("ProductType", ""))


def default_app(udid):
    if product_type(udid).startswith("AppleTV"):
        return ROOT / "build/embedded/appletvos/FIFA Street PS2.app"
    return DEFAULT_APP


def remote_paths(udid):
    """Container-relative directories the app writes to, as in host/ios-main.mm.

    tvOS denies Documents and Application Support inside the app's own container,
    so the app uses Library/Caches for everything. On iOS the user directory (and
    therefore the isolated run directories and perf logs the app writes beside
    them) lives in Application Support; only pushed game data goes to Documents.
    """
    if product_type(udid).startswith("AppleTV"):
        return {"user": "Library/Caches", "isolated": "Library/Caches", "perf": "Library/Caches",
                "game": "Library/Caches"}
    return {"user": "Library/Application Support", "isolated": "Library/Application Support",
            "perf": "Library/Application Support", "game": "Documents"}


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


def install_prepared(app, lean, udid, stage=None, bundle=None, force=False):
    """Install a signed app, reusing the device hash cache when possible."""
    app = Path(app)
    src_hash = source_hash(app, lean)
    state = state_read()
    prior = state.get("install", {})
    if (
        not force
        and prior.get("hash") == src_hash
        and prior.get("lean") == lean
        and prior.get("udid") == udid
    ):
        return 0.0, False
    if stage is None or bundle is None:
        stage, bundle = sign_stage(app, lean, udid)
    started = time.monotonic()
    pmd("apps", "install", str(stage), *global_device_args(udid), timeout=900)
    elapsed = time.monotonic() - started
    state["install"] = {
        "hash": src_hash,
        "lean": lean,
        "bundle": bundle,
        "source": str(app),
        "udid": udid,
    }
    # A reinstall can land in a new app container, so the pushed game data is gone
    # even when the files themselves did not change. Forget the pushed manifest so
    # ./rx device push-data sends it again instead of skipping everything.
    state.pop("game_manifest", None)
    state.pop("game_manifest_target", None)
    state_write(state)
    return elapsed, True


def do_install(args):
    udid = choose_udid(args.udid)
    app = Path(args.app or default_app(udid))
    elapsed, installed = install_prepared(app, args.lean, udid, force=args.force)
    if installed:
        print(f"installed {app} ({'lean' if args.lean else 'full'}; {elapsed:.2f}s)")
    else:
        print("installed app unchanged; skipped")


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
            "apps", "push", bundle, str(staged_game), remote_paths(udid)["game"],
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


def current_launch_log(log):
    starts = list(re.finditer(r"^\[settings\] videoMode=", log, re.MULTILINE))
    return log[starts[-1].start():] if starts else log


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
    log = current_launch_log(log)
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
    gameplay_start = min(confirmed) if confirmed else None
    start = gameplay_start
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
        "shots_count": sum(
            1 for shot in (root / "shots").glob("*.png")
            if shot.is_file() and shot.stat().st_size > 0
        ),
        "gpu": metric_summary(
            fps_rows,
            ["Device Utilization %", "Renderer Utilization %", "Tiler Utilization %"],
            "gpu",
        ),
        "process": metric_summary(
            sys_rows, ["cpuUsage", "physFootprint", "memory", "threads"], "process"
        ),
        "gameplay_window_start": start,
        "time_to_play_s": (gameplay_start - launch_time) if gameplay_start is not None and launch_time is not None else None,
        "hud_clock": hud_clock_progress(log),
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
        if r.get("type") not in ("summary", "presented", "presented_summary")
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
    launch_ref, draws_ref, clock_ref = ledger_control_reference()
    out["metrics"] = run_metrics(root)
    if out["time_to_play_s"] is not None:
        out["metrics"]["time_to_play_s"] = out["time_to_play_s"]
    out["validity"] = evaluate_validity(out, launch_ref, draws_ref, clock_ref)
    (root / "summary.json").write_text(json.dumps(out, indent=2) + "\n")
    print_summary(out)
    return out


def hud_clock_progress(log):
    log = current_launch_log(log)
    samples = [(float(clock), float(wall)) for clock, wall in re.findall(
        r"\[autopilot\] clock_seconds=([-+0-9.eE]+) wall_seconds=([-+0-9.eE]+)", log
    )]
    last_reset = 0
    for index in range(1, len(samples)):
        if samples[index][1] < samples[index - 1][1]:
            last_reset = index
    samples = samples[last_reset:]
    rates = []
    for previous, current in zip(samples, samples[1:]):
        guest_delta = current[0] - previous[0]
        wall_delta = current[1] - previous[1]
        if guest_delta > 0.5 and 0 < wall_delta <= 5:
            rates.append(guest_delta / wall_delta)
    if len(rates) < 5:
        return {"expected_guest_seconds_per_wall_second": 1.0, "rate": None, "samples": len(samples)}
    return {"expected_guest_seconds_per_wall_second": 1.0, "rate": statistics.median(rates),
            "samples": len(samples), "active_intervals": len(rates)}


def ledger_control_reference():
    ledger = ROOT / "build/device-ab/ledger.jsonl"
    runs = []
    if ledger.is_file():
        for line in ledger.read_text(errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            runs.extend(run for run in entry.get("runs", [])
                        if run.get("arm") == "control" and run.get("validity", {}).get("valid"))
    launch = [run["metrics"]["time_to_play_s"] for run in runs
              if isinstance(run.get("metrics", {}).get("time_to_play_s"), (int, float))]
    draws = [run["metrics"]["draws_per_gameplay_frame_p50"] for run in runs
             if isinstance(run.get("metrics", {}).get("draws_per_gameplay_frame_p50"), (int, float))]
    clocks = [run["metrics"]["hud_clock_guest_s_per_wall_s"] for run in runs
              if isinstance(run.get("metrics", {}).get("hud_clock_guest_s_per_wall_s"), (int, float))]
    return (statistics.median(launch) if launch else None,
            statistics.median(draws) if draws else None,
            statistics.median(clocks) if clocks else None)


def evaluate_validity(summary, control_launch_s=None, control_draws=None, control_clock_rate=None, check_clock=True):
    reasons = []
    time_to_play = summary.get("time_to_play_s")
    if time_to_play is None:
        reasons.append("gameplay start time unavailable")
    else:
        if time_to_play > 45:
            reasons.append(f"launch-to-gameplay {time_to_play:.1f}s exceeds 45s")
        if control_launch_s and time_to_play > 1.5 * control_launch_s:
            reasons.append(f"launch-to-gameplay {time_to_play:.1f}s exceeds 1.5x control median {control_launch_s:.1f}s")
    clock = summary.get("hud_clock", {})
    rate = clock.get("rate")
    expected_rate = control_clock_rate if isinstance(control_clock_rate, (int, float)) else 1.0
    if check_clock and rate is None:
        reasons.append("HUD clock progression unavailable over 5s")
    elif check_clock and abs(rate - expected_rate) > 0.1 * expected_rate:
        reasons.append(f"HUD clock rate {rate:.3f} guest s/wall s differs from expected {expected_rate:.3f} by over 10%")
    if summary.get("shots_count", 0) == 0:
        reasons.append("shots_count == 0")
    metrics = summary.get("metrics", {})
    draws = metrics.get("draws_per_gameplay_frame_p50")
    if control_draws and isinstance(draws, (int, float)) and draws < 0.5 * control_draws:
        reasons.append(f"median draws/frame {draws:.1f} is below 50% of control {control_draws:.1f}")
    return {"valid": not reasons, "reasons": reasons, "hud_clock_expected_guest_seconds_per_wall_second": 1.0,
            "hud_clock_rate": rate, "hud_clock_expected_rate": expected_rate,
            "control_launch_to_gameplay_median_s": control_launch_s,
            "control_draws_per_frame_median": control_draws}


def gameplay_rows(run_dir):
    rows = []
    for path in sorted((Path(run_dir) / "perf").glob("**/*.jsonl")):
        rows.extend(r for r in jsonl(path) if isinstance(r, dict))
    return [r for r in rows if r.get("type") != "summary" and r.get("gameplay") is True]


def active_gameplay_rows(rows):
    clocked = [row for row in rows if isinstance(_metric_value(row, "game_clock_s"), (int, float))
               and _metric_value(row, "game_clock_s") >= 0]
    if not clocked:
        return rows
    active = []
    previous = None
    last_advance = None
    for row in clocked:
        clock = _metric_value(row, "game_clock_s")
        when = _metric_value(row, "t")
        if previous is None or clock > previous or clock < previous:
            last_advance = when
        if last_advance is not None and when is not None and when - last_advance <= 2.5:
            active.append(row)
        previous = clock
    return active or rows


def _metric_value(row, name):
    """Find a numeric perf field, including fields nested by subsystem/thread."""
    if name in row and isinstance(row[name], (int, float)) and not isinstance(row[name], bool):
        return row[name]
    found = []
    def visit(value, path=""):
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            current = f"{path}.{key}" if path else key
            if (key == name or current.endswith("." + name)) and isinstance(item, (int, float)) and not isinstance(item, bool):
                found.append(item)
            elif isinstance(item, dict):
                visit(item, current)
    visit(row)
    return found[-1] if found else None


def _thread_cpu(row, thread):
    values = []
    def visit(value, path=""):
        if isinstance(value, dict):
            for key, item in value.items():
                here = f"{path}.{key}" if path else key
                if key == "thread_cpu_s" and isinstance(item, dict):
                    for name, seconds in item.items():
                        if thread is None or name == thread:
                            if isinstance(seconds, (int, float)):
                                values.append(float(seconds))
                elif key == "thread_cpu_s" and isinstance(item, (int, float)) and thread is None:
                    values.append(float(item))
                elif key in ("GameThread", "VU1GSWorker") and thread == key and isinstance(item, (int, float)):
                    values.append(float(item))
                elif isinstance(item, dict):
                    visit(item, here)
    visit(row)
    return values[-1] if values else None


def active_thread_cores(rows, summaries):
    snapshots = [row for row in summaries if isinstance(row.get("t"), (int, float))
                 and isinstance(row.get("thread_cpu_s"), dict)]
    snapshots.sort(key=lambda row: row["t"])
    times = [row["t"] for row in snapshots]
    weighted, durations = {}, {}
    for row in rows:
        when, duration = _metric_value(row, "t"), _metric_value(row, "present_dt_ms")
        if when is None or duration is None or duration <= 0:
            continue
        cursor = when - duration / 1000.0
        index = max(1, bisect.bisect_right(times, cursor))
        while index < len(snapshots) and snapshots[index - 1]["t"] < when:
            first, last = snapshots[index - 1], snapshots[index]
            elapsed = last["t"] - first["t"]
            overlap = min(when, last["t"]) - max(cursor, first["t"])
            if elapsed > 0 and overlap > 0:
                before, after = first["thread_cpu_s"], last["thread_cpu_s"]
                for name in before.keys() & after.keys():
                    delta = after[name] - before[name]
                    if delta < 0:
                        continue
                    weighted[name] = weighted.get(name, 0.0) + delta / elapsed * overlap
                    durations[name] = durations.get(name, 0.0) + overlap
            index += 1
    cores = {name: value / durations[name] for name, value in weighted.items()}
    result = {}
    for name, key in (("GameThread", "gamethread_cores"), ("VU1GSWorker", "vu1gsworker_cores"),
                      ("VU1StageWorker", "vu1stageworker_cores"), ("GSStageWorker", "gsstageworker_cores")):
        if name in cores:
            result[key] = cores[name]
    if cores:
        result["process_cores"] = sum(cores.values())
    return result


def run_metrics(run_dir):
    root = Path(run_dir)
    rows = active_gameplay_rows(gameplay_rows(root))
    metrics = {}
    times = [v for r in rows for v in [_metric_value(r, "t")] if v is not None]
    intervals = [_metric_value(row, "present_dt_ms") for row in rows]
    intervals = [value for value in intervals if value is not None and value > 0]
    if intervals:
        metrics["fps"] = 1000.0 * len(intervals) / sum(intervals)
    frame_fields = {
        "present_dt_ms": ("p50", "p95", "p99"), "cpu_game_ms": ("p50", "p95"),
        "vu1_ms": ("p50", "p95"), "gs_submit_ms": ("p50",),
        "drain_wait_ms": ("p50", "p95"),
        "gpu_ms": ("p50", "p95"), "present_wait_ms": ("p50",),
        "draws": ("p50",), "ee_ms": ("p50", "p95"), "iop_ms": ("p50", "p95"),
        "spu2_ms": ("p50", "p95"), "other_ms": ("p50", "p95"),
        "ring_full_wait_ms": ("p50", "p95"), "vu1_exec_ms": ("p50", "p95"),
        "vif_unpack_ms": ("p50", "p95"), "xgkick_ms": ("p50", "p95"),
    }
    for field, quantiles in frame_fields.items():
        vals = [v for r in rows for v in [_metric_value(r, field)] if v is not None]
        for q in quantiles:
            if vals:
                metrics[f"{field}_{q}"] = percentile(vals, {"p50": 50, "p95": 95, "p99": 99}[q])
    if "draws_p50" in metrics:
        metrics["draws_per_gameplay_frame_p50"] = metrics["draws_p50"]
    split_fields = ("ee_ms", "iop_ms", "spu2_ms", "drain_wait_ms", "ring_full_wait_ms", "other_ms",
                    "vu1_exec_ms", "vif_unpack_ms", "xgkick_ms", "gs_submit_ms")
    for label, selected in (
        ("on_time", [r for r in rows if (_metric_value(r, "present_dt_ms") or 0) <= 18]),
        ("late", [r for r in rows if (_metric_value(r, "present_dt_ms") or 0) > 18]),
    ):
        for field in split_fields:
            vals = [v for r in selected for v in [_metric_value(r, field)] if v is not None]
            for q in (50, 95):
                if vals:
                    metrics[f"{field}_{label}_p{q}"] = percentile(vals, q)
    drains = sum(r.get("vsync_drains", 0) for r in rows)
    if drains:
        metrics["vsync_worker_busy_pct"] = 100 * sum(r.get("vsync_worker_busy", 0) for r in rows) / drains
    drain_cpu_rows = [r for r in rows if isinstance(r.get("drain_wait_ms"), (int, float))
                      and isinstance(r.get("cpu_game_ms"), (int, float))]
    cpu_ms = sum(r["cpu_game_ms"] for r in drain_cpu_rows)
    if cpu_ms > 0:
        metrics["drain_wait_to_cpu_game_pct"] = 100 * sum(r["drain_wait_ms"] for r in drain_cpu_rows) / cpu_ms
    present = [v for r in rows for v in [_metric_value(r, "present_dt_ms")] if v is not None]
    if present:
        metrics["late_frames_over_18_pct"] = 100 * sum(v > 18 for v in present) / len(present)
        metrics["late_frames_over_25_pct"] = 100 * sum(v > 25 for v in present) / len(present)
    perf_rows = []
    for path in sorted((root / "perf").glob("**/*.jsonl")):
        perf_rows.extend(r for r in jsonl(path) if isinstance(r, dict))
    summaries = sorted((r for r in perf_rows if r.get("type") == "summary"), key=lambda r: r.get("t", 0))
    for field in ("audio_underruns", "pipeline_compiles"):
        vals = [_metric_value(r, field) for r in rows]
        vals = [v for v in vals if v is not None]
        if vals:
            metrics[field + "_delta_gameplay"] = (sum(vals) if field == "pipeline_compiles" else vals[-1] - vals[0])
        run_vals = [_metric_value(r, field) for r in summaries]
        run_vals = [v for v in run_vals if v is not None]
        if run_vals:
            metrics[field + "_delta_run"] = run_vals[-1] - run_vals[0] if len(run_vals) > 1 else run_vals[-1]
    metrics.update(active_thread_cores(rows, summaries))
    summary = json.loads((root / "summary.json").read_text()) if (root / "summary.json").exists() else {}
    if isinstance(summary.get("time_to_play_s"), (int, float)):
        metrics["time_to_play_s"] = summary["time_to_play_s"]
    clock_rate = summary.get("hud_clock", {}).get("rate")
    if isinstance(clock_rate, (int, float)):
        metrics["hud_clock_guest_s_per_wall_s"] = clock_rate
    if "thermal_state_max" in summary:
        metrics["thermal_state_max"] = summary["thermal_state_max"]
    bt = summary.get("battery_temperature", {})
    if "delta" in bt:
        metrics["battery_temp_delta_c"] = bt["delta"]
    offsets = [r["wall_time"] - r["t"] for r in summaries if isinstance(r.get("wall_time"), (int, float)) and isinstance(r.get("t"), (int, float))]
    if times and offsets:
        start_host, end_host = min(times) + statistics.median(offsets), max(times) + statistics.median(offsets)
        gpu_rows = [r for r in jsonl(root / "gpu.jsonl") if start_host <= r.get("_host_time", -1) <= end_host]
        util = [r.get("Device Utilization %") for r in gpu_rows if isinstance(r.get("Device Utilization %"), (int, float))]
        if util:
            metrics["gpu_device_utilization_mean_pct"] = statistics.mean(util)
        ca = [r.get("CoreAnimationFramesPerSecond") for r in gpu_rows if isinstance(r.get("CoreAnimationFramesPerSecond"), (int, float))]
        if ca:
            metrics["core_animation_fps_median"] = statistics.median(ca)
            metrics["core_animation_fps_p5"] = percentile(ca, 5)
        footprints = [r.get("physFootprint") for r in jsonl(root / "sysmon.jsonl")
                      if start_host <= r.get("_host_time", -1) <= end_host and isinstance(r.get("physFootprint"), (int, float))]
        if footprints:
            metrics["phys_footprint_peak"] = max(footprints)
    return metrics


def aggregate_metrics(run_metrics_list):
    keys = sorted(set().union(*(m.keys() for m in run_metrics_list))) if run_metrics_list else []
    result = {}
    for key in keys:
        vals = [m[key] for m in run_metrics_list if key in m and isinstance(m[key], (int, float))]
        if vals:
            result[key] = statistics.median(vals)
    return result


def compare_metrics(control_runs, candidate_runs):
    control, candidate = aggregate_metrics(control_runs), aggregate_metrics(candidate_runs)
    common = sorted(control.keys() & candidate.keys())
    table, verdicts = {}, {}
    for key in common:
        c, n = control[key], candidate[key]
        vals = [m[key] for m in control_runs if key in m]
        noise = max(vals) - min(vals) if vals else 0
        delta = n - c
        threshold = noise
        higher_is_better = key == "fps" or key.startswith("core_animation_fps_")
        verdict = "no-change" if abs(delta) <= threshold else ("better" if (delta > 0 if higher_is_better else delta < 0) else "worse")
        table[key] = {"control": c, "candidate": n, "delta": delta,
                      "delta_pct": (delta / c * 100) if c else None, "control_noise": noise}
        verdicts[key] = verdict
    return table, verdicts


def abba_order(pairs):
    return [arm for i in range(pairs) for arm in (("control", "candidate") if i % 2 == 0 else ("candidate", "control"))]


def env_pairs(values):
    env = {}
    for value in values:
        if "=" not in value or not value.split("=", 1)[0]:
            raise Failed(f"environment override must be KEY=VALUE: {value}")
        key, val = value.split("=", 1)
        env[key] = val
    return env


def device_battery_temp(udid):
    result = pmd("diagnostics", "battery", "single", *global_device_args(udid), timeout=15, check=False)
    try:
        obj = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    value = obj.get("Temperature", obj.get("VirtualTemperature"))
    return float(value) if isinstance(value, (int, float)) else None


def cooldown(udid, target, max_wait=180):
    start = time.monotonic()
    if target is None:
        return {"seconds": 0, "temperature_c": None, "target_c": None,
                "timed_out": False, "unavailable": True}
    while True:
        temp = device_battery_temp(udid)
        waited = time.monotonic() - start
        if temp is None:
            return {"seconds": waited, "temperature_c": None, "target_c": target,
                    "timed_out": False, "unavailable": True}
        if abs(temp - target) <= 1.0:
            return {"seconds": waited, "temperature_c": temp, "target_c": target, "timed_out": False}
        if waited >= max_wait:
            return {"seconds": waited, "temperature_c": temp, "target_c": target, "timed_out": True}
        time.sleep(min(10, max_wait - waited))


def _battery_start(path):
    vals = [r.get("Temperature", r.get("VirtualTemperature")) for r in jsonl(Path(path) / "battery.jsonl")]
    vals = [v for v in vals if isinstance(v, (int, float))]
    return vals[0] if vals else None


def render_ab_text(data):
    clock_rates = [run.get("validity", {}).get("hud_clock_expected_rate") for run in data.get("runs", [])]
    clock_rates = [rate for rate in clock_rates if isinstance(rate, (int, float))]
    clock_target = statistics.median(clock_rates) if clock_rates else 1.0
    clock_line = f"HUD clock active-rate target: {clock_target:.3f} guest s/wall s (control data; 10% tolerance)"
    if data["kind"] == "baseline":
        lines = [f"baseline: {data['label']} ({data['pairs']} runs)", clock_line, f"{'Metric':<36} {'Median':>12}"]
        lines.extend(f"INVALID RUN {run['arm']} {run['run_dir']}: {'; '.join(run['validity']['reasons'])}"
                     for run in data.get("runs", []) if not run.get("validity", {}).get("valid"))
        lines.extend(f"{key:<36} {value:>12.3f}" for key, value in data["metrics"].items())
        return "\n".join(lines) + "\n"
    lines = [f"{data['kind']}: {data['label']} ({data['pairs']} runs)",
             clock_line,
             f"{'Metric':<36} {'Control':>10} {'Candidate':>10} {'Delta':>10} {'Delta%':>9} {'Noise':>10}"]
    lines.extend(f"INVALID RUN {run['arm']} {run['run_dir']}: {'; '.join(run['validity']['reasons'])}"
                 for run in data.get("runs", []) if not run.get("validity", {}).get("valid"))
    for key, row in data["metrics"].items():
        pct = "n/a" if row["delta_pct"] is None else f"{row['delta_pct']:.1f}%"
        lines.append(f"{key:<36} {row['control']:>10.3f} {row['candidate']:>10.3f} {row['delta']:>10.3f} {pct:>9} {row['control_noise']:>10.3f}")
    verdict = ", ".join(f"{k}: {v}" for k, v in data["verdicts"].items()) or "no comparable metrics"
    lines.extend(("Verdict: " + verdict, ""))
    if data.get("install_seconds"):
        lines.insert(-1, f"Install median: {statistics.median(data['install_seconds']):.2f}s")
    if data.get("installed_at_end"):
        lines.insert(-1, f"Installed at end: {data['installed_at_end']}")
    return "\n".join(lines)


def run_experiment(args, kind):
    runs = args.pairs if kind == "ab" else args.runs
    if args.seconds < 1 or args.warmup < 0 or runs < 1:
        raise Failed("runs/pairs and seconds must be positive, warmup non-negative")
    label = re.sub(r"[^A-Za-z0-9_.-]+", "-", args.label).strip("-") or "run"
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ab_dir = ROOT / "build/device-ab" / f"{stamp}-{label}"
    ab_dir.mkdir(parents=True, exist_ok=False)
    udid = choose_udid(args.udid)
    prepared_apps = {}
    if kind == "ab":
        arms = {"control": env_pairs(args.control), "candidate": env_pairs(args.candidate)}
        order = abba_order(args.pairs)
        control_app = getattr(args, "control_app", None)
        candidate_app = getattr(args, "candidate_app", None)
        if bool(control_app) != bool(candidate_app):
            raise Failed("--control-app and --candidate-app must be supplied together")
        if control_app:
            prepared_by_path = {}
            for arm, app in (("control", control_app), ("candidate", candidate_app)):
                app = Path(app).expanduser().resolve()
                if app not in prepared_by_path:
                    prepared_by_path[app] = (app, *sign_stage(app, True, udid))
                prepared_apps[arm] = prepared_by_path[app]
    else:
        arms = {"baseline": env_pairs(args.env)}
        order = ["baseline"] * args.runs
    run_dirs, values, run_records, waits, install_seconds = [], {arm: [] for arm in arms}, [], [], []
    first_temp = None
    for index, arm in enumerate(order):
        if index:
            waits.append(cooldown(udid, first_temp))
        run_args = argparse.Namespace(seconds=args.seconds, warmup=args.warmup,
            label=f"{label}-{arm}-{index + 1:02d}", breakdown=getattr(args, "breakdown", False),
            env=[f"{k}={v}" for k, v in arms[arm].items()], no_match=False,
            shots_every=max(1, args.seconds + args.warmup - 1), app=str(prepared_apps[arm][0]) if arm in prepared_apps else None,
            udid=udid, _stamp=stamp,
            no_device_telemetry=getattr(args, "no_device_telemetry", False))
        if arm in prepared_apps:
            app, stage, bundle = prepared_apps[arm]
            elapsed, _installed = install_prepared(app, True, udid, stage, bundle)
            install_seconds.append(elapsed)
        run_dir = do_run(run_args)
        if first_temp is None and not getattr(args, "no_device_telemetry", False):
            first_temp = _battery_start(run_dir)
        run_dirs.append(str(run_dir.relative_to(ROOT)))
        summary = json.loads((run_dir / "summary.json").read_text()) if (run_dir / "summary.json").is_file() else {}
        run_records.append({"arm": arm, "run_dir": str(run_dir.relative_to(ROOT)),
                            "metrics": run_metrics(run_dir), "summary": summary})
    installed_at_end = None
    if prepared_apps:
        head_app = Path(default_app(udid)).resolve()
        prepared = next((v for v in prepared_apps.values() if v[0] == head_app), None)
        if prepared is None:
            stage, bundle = sign_stage(head_app, True, udid)
        else:
            _app, stage, bundle = prepared
        install_prepared(head_app, True, udid, stage, bundle)
        installed_at_end = str(head_app)
    historical_launch, historical_draws, historical_clock = ledger_control_reference()
    current_controls = [r for r in run_records if r["arm"] == "control"]
    if not current_controls:
        current_controls = [r for r in run_records if r["arm"] == "baseline"]
    current_control_valid = [r for r in current_controls
                             if evaluate_validity(r["summary"], historical_launch, None, check_clock=False)["valid"]]
    launch_values = ([historical_launch] if historical_launch is not None else []) + [
        r["metrics"]["time_to_play_s"] for r in current_controls
        if isinstance(r["metrics"].get("time_to_play_s"), (int, float))]
    control_launch = statistics.median(launch_values) if launch_values else None
    draw_values = ([historical_draws] if historical_draws is not None else []) + [
        r["metrics"]["draws_per_gameplay_frame_p50"] for r in current_control_valid
        if isinstance(r["metrics"].get("draws_per_gameplay_frame_p50"), (int, float))]
    control_draws = statistics.median(draw_values) if draw_values else None
    clock_values = ([historical_clock] if historical_clock is not None else []) + [
        r["metrics"]["hud_clock_guest_s_per_wall_s"] for r in current_control_valid
        if isinstance(r["metrics"].get("hud_clock_guest_s_per_wall_s"), (int, float))]
    control_clock = statistics.median(clock_values) if clock_values else None
    for record in run_records:
        summary = record["summary"]
        if record["metrics"].get("time_to_play_s") is not None:
            summary["time_to_play_s"] = record["metrics"]["time_to_play_s"]
        summary["metrics"] = record["metrics"]
        record["validity"] = evaluate_validity(summary, control_launch, control_draws, control_clock)
        if record["run_dir"]:
            summary_path = ROOT / record["run_dir"] / "summary.json"
            if summary_path.is_file():
                summary["validity"] = record["validity"]
                summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        record.pop("summary")
        if record["validity"]["valid"]:
            values[record["arm"]].append(record["metrics"])
    if kind == "ab":
        if len(values["control"]) < 2 or len(values["candidate"]) < 2:
            deficient = [f"{arm} has {len(values[arm])}/2 valid runs" for arm in ("control", "candidate")
                         if len(values[arm]) < 2]
            reasons = [reason for record in run_records if not record["validity"]["valid"]
                       for reason in record["validity"]["reasons"]]
            verdicts = {"experiment": "INVALID: " + "; ".join(deficient + reasons[:2])}
            metrics = {}
        else:
            metrics, verdicts = compare_metrics(values["control"], values["candidate"])
    else:
        metrics, verdicts = aggregate_metrics(values["baseline"]), {}
    data = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "kind": kind, "label": label,
            "control": arms.get("control", arms.get("baseline")), "candidate": arms.get("candidate"),
            "pairs": args.pairs if kind == "ab" else args.runs, "seconds": args.seconds,
            "warmup": args.warmup,
            "git_sha": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                capture_output=True, text=True, check=False).stdout.strip(),
            "metrics": metrics, "verdicts": verdicts, "run_dirs": run_dirs, "runs": run_records, "cooldowns": waits,
            "install_seconds": install_seconds, "installed_at_end": installed_at_end}
    (ab_dir / "ab.json").write_text(json.dumps(data, indent=2) + "\n")
    (ab_dir / "ab.txt").write_text(render_ab_text(data))
    ledger = ROOT / "build/device-ab/ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as f:
        f.write(json.dumps(data, separators=(",", ":")) + "\n")
    print((ab_dir / "ab.txt").read_text(), end="")


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
    if data.get("validity", {}).get("valid") is False:
        row("Validity", "INVALID: " + "; ".join(data["validity"].get("reasons", [])))
    row(
        "Samples GPU/sysmon/battery",
        " / ".join(str(data["samples"][k]) for k in ("gpu", "sysmon", "battery")),
    )
    row("Screenshots", data.get("shots_count", 0))


def capture_screenshot(udid, dest):
    pmd(
        "developer", "dvt", "screenshot", "--tunnel", udid, str(dest), timeout=25
    )
    if not dest.is_file() or dest.stat().st_size == 0:
        raise Failed(f"screenshot command returned without writing {dest}")
    with dest.open("rb") as image:
        if image.read(8) != b"\x89PNG\r\n\x1a\n":
            raise Failed(f"screenshot command wrote an invalid PNG: {dest}")


def stop_collector(process):
    if process is None:
        return
    try:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    finally:
        if process.stdout is not None:
            process.stdout.close()


def stream(cmd, output, stop, ready_event=None, device=False):
    output.parent.mkdir(parents=True, exist_ok=True)
    p = None
    try:
        with output.open("w") as f, output.with_suffix(output.suffix + ".stderr.log").open("w") as errors:
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
    except Exception as e:
        with output.open("a") as f:
            f.write(json.dumps({"_error": str(e), "_host_time": time.time()}) + "\n")
    finally:
        stop_collector(p)


def stream_sysmon(cmd, output, stop):
    raw = output.with_suffix(".raw.jsonl")
    errors = output.with_suffix(output.suffix + ".stderr.log")
    output.parent.mkdir(parents=True, exist_ok=True)
    offset = 0
    p = None
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
    except Exception as e:
        with output.open("a") as f:
            f.write(json.dumps({"_error": str(e), "_host_time": time.time()}) + "\n")
    finally:
        try:
            stop_collector(p)
        finally:
            raw.unlink(missing_ok=True)


def do_run(args):
    device_telemetry = not getattr(args, "no_device_telemetry", False)
    if args.seconds < 1 or args.warmup < 0:
        raise Failed("seconds must be positive and warmup non-negative")
    udid = choose_udid(args.udid)
    app = Path(args.app or default_app(udid))
    info = info_plist(app)
    state = state_read()
    bundle = installed_bundle(udid, info, state.get("install", {}).get("bundle"))
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = re.sub(r"[^A-Za-z0-9_.-]+", "-", args.label or "run").strip("-") or "run"
    stamp = getattr(args, "_stamp", None) or stamp
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
    env += ["PS2X_PERF_LOG=1", f"PS2X_PERF_BREAKDOWN={int(getattr(args, 'breakdown', False))}"]
    # A manual playtest keeps the real user directory: the app writes its log and
    # perf rows under the default container locations, which the pull below
    # already handles when no PS2X_USER_DIR is set.
    if not getattr(args, "real_user_dir", False):
        env.append(f"PS2X_USER_DIR=rx-run-{stamp}-{label}")
    env += args.env
    run_user_name = dict(value.split("=", 1) for value in env).get("PS2X_USER_DIR")
    if not getattr(args, "no_pipeline_cache", False):
        # The learned-selector cache lives outside the per-run user directory so
        # it accumulates across runs; point the app at it and seed it first.
        cache_path = seed_pipeline_cache(udid, run_user_name)
        if cache_path:
            env.append(f"RRV_PCSX2_GS_PIPELINE_CACHE={cache_path}")
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
    threads = []
    try:
        (out / "run.json").write_text(
            json.dumps(
                {
                    "udid": udid,
                    "bundle": bundle,
                    "pid": pid,
                    "launch_host_time": launch_host_time,
                    "seconds": args.seconds,
                    "warmup_s": args.warmup,
                    "device_telemetry": device_telemetry,
                    "env": env,
                },
                indent=2,
            )
            + "\n"
        )
        # DVT's process launch can outlive the initial RemoteXPC service
        # transition.
        time.sleep(2)
        if device_telemetry:
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
            threads.append(gpu_thread)
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
                        "--duration",
                        str(math.ceil((args.seconds + args.warmup + 30) * 1000)),
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
            threads.append(sys_thread)
        began = launch_monotonic
        deadline = began + args.warmup + args.seconds
        next_battery = began
        shots = 0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if device_telemetry and now >= next_battery:
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
                    capture_screenshot(udid, dest)
                except Exception as e:
                    (out / "shots" / f"{shots:04d}.error").write_text(str(e))
                shots += 1
            time.sleep(0.25)
    finally:
        stop.set()
        try:
            pmd("developer", "dvt", "kill", "--tunnel", udid, str(pid), timeout=20, check=False)
        finally:
            for thread in threads:
                thread.join(timeout=8)
    effective_env = dict(value.split("=", 1) for value in env)
    user_name = effective_env.get("PS2X_USER_DIR")
    paths = remote_paths(udid)
    remote = (
        f"{paths['isolated']}/{user_name}/Logs/native.log" if user_name
        else f"{paths['user']}/{info['CFBundleExecutable']}/Logs/native.log"
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
    # Fetch the per-frame perf directory when the app has one.
    perf = out / "perf"
    perf.mkdir(exist_ok=True)
    pmd(
        "apps",
        "pull",
        bundle,
        f"{paths['perf']}/perf",
        str(perf),
        *global_device_args(udid),
        timeout=180,
        check=False,
    )
    if not getattr(args, "no_pipeline_cache", False):
        learned = pull_pipeline_cache(udid, effective_env.get("PS2X_USER_DIR"))
        if learned:
            print(f"learned {learned} new pipeline selectors -> {learned_cache_path(udid).relative_to(ROOT)}")
    data = summarize(out)
    print(
        f"run: {out.relative_to(ROOT)}; pid {pid}; native.log {'pulled' if pulled.returncode==0 else 'unavailable'}"
    )
    if data["shots_count"] == 0:
        print("WARNING: no screenshots captured (check --shots-every and shots/*.error)")
    return out


MANIFEST = ROOT / "bridge/gs/pipelines-fifa.txt"


def container_path(udid, bundle):
    """Absolute container directory of the installed app (needed for file writes)."""
    result = pmd("apps", "query", bundle, *global_device_args(udid), timeout=60, check=False)
    try:
        record = json.loads(result.stdout).get(bundle) or {}
    except json.JSONDecodeError:
        record = {}
    container = record.get("Container")
    if not container:
        raise Failed(f"cannot resolve the app container for {bundle} -> ./rx device install first")
    return container


def record_target(udid, bundle):
    """Absolute path the app should record pipeline keys to.

    RRV_PCSX2_GS_PIPELINE_RECORD is opened by the GS bridge itself, so the app
    sandbox needs an absolute container path; a relative one resolves against the
    read-only bundle directory and silently never records. It lives beside the
    learned-selector cache because that directory exists on every platform: on
    tvOS the app cannot create the perf directory the recorder would need.
    """
    return f"{container_path(udid, bundle)}/{coverage_record_remote(udid)}"


def coverage_record_remote(udid):
    """Container-relative coverage file, in the directory that holds game data."""
    return f"{remote_paths(udid)['game']}/rx-coverage-pipelines.txt"


def learned_cache_path(udid):
    """Host-side union of the Metal selectors the device has learned so far."""
    import pathlib
    return ROOT / "build" / "device" / f"pipelines-{udid}.txt"


def installed_bundle_id(udid):
    """Bundle id of the installed game (never a system app that matches too)."""
    records = app_records(udid)
    for key, value in records.items():
        if value.get("ApplicationType") == "User" and value.get("CFBundleExecutable") == "FIFAStreetPS2":
            return key
    for key, value in records.items():
        if value.get("CFBundleExecutable") == "FIFAStreetPS2":
            return key
    raise Failed("no installed app found -> ./rx device install first")


def pipeline_cache_remote(udid, absolute=False):
    """Shared learned-selector file, outside the per-run user directory.

    Documents is the container directory push and pull handle reliably on every
    platform (tvOS included); the app opens the file itself, so
    RRV_PCSX2_GS_PIPELINE_CACHE needs the absolute container form.
    """
    relative = f"{remote_paths(udid)['game']}/rx-learned-pipelines.txt"
    if not absolute:
        return relative
    return f"{container_path(udid, installed_bundle_id(udid))}/{relative}"


def seed_pipeline_cache(udid, user_name):
    """Push the learned-selector union and return the path the app must write to.

    The app appends every selector it compiles to RRV_PCSX2_GS_PIPELINE_CACHE and
    prewarms the same file at the next launch, so the run has to point the app at
    a file outside its per-run user directory and harvest it afterwards.

    The push is best effort: a device that has not learned anything yet has no
    union to send, and the app creates the file itself on the first compile. Only
    the path matters, so it is always returned.
    """
    if not user_name:
        return None
    cache = learned_cache_path(udid)
    remote = pipeline_cache_remote(udid, absolute=True)
    if not cache.is_file() or not cache.stat().st_size:
        return remote
    for _attempt in range(3):
        result = pmd("apps", "push", installed_bundle_id(udid), str(cache),
                     pipeline_cache_remote(udid), *global_device_args(udid),
                     timeout=120, check=False)
        if result.returncode == 0:
            return remote
        time.sleep(1.0)
    print("WARNING: could not seed the learned pipeline cache; this run may recompile", file=sys.stderr)
    return remote


def pull_pipeline_cache(udid, user_name):
    """Merge the selectors this run learned back into the host union."""
    cache = learned_cache_path(udid)
    if not user_name:
        return 0
    for _attempt in range(3):
        with tempfile.TemporaryDirectory() as tmp:
            result = pmd("apps", "pull", installed_bundle_id(udid),
                         pipeline_cache_remote(udid), tmp,
                         *global_device_args(udid), timeout=120, check=False)
            pulled = list(Path(tmp).rglob("*.txt")) if result.returncode == 0 else []
            if pulled:
                cache.parent.mkdir(parents=True, exist_ok=True)
                if not cache.is_file():
                    shutil.copyfile(pulled[0], cache)
                    return sum(1 for line in cache.read_text().splitlines() if line.startswith("vs="))
                added, _total, _sections = merge_pipeline_keys(pulled[0], cache)
                return added
        time.sleep(1.0)
    return 0


def merge_pipeline_keys(recorded, manifest=MANIFEST):
    """Merge a recorder file into the shipped manifest, per feature section.

    The recorder writes `# features=...` sections and `vs=...` selectors. Keys
    already present in the matching section are skipped; a section that the
    manifest does not have yet is appended at the end. Returns
    (added, total_selectors, sections_touched).
    """
    import collections
    import pathlib as _pathlib

    incoming = collections.OrderedDict()
    current = None
    for line in _pathlib.Path(recorded).read_text().splitlines():
        if line.startswith("# features="):
            current = line[len("# features="):]
            incoming.setdefault(current, [])
        elif line.startswith("vs=") and current is not None:
            incoming[current].append(line)
    if not incoming:
        return 0, sum(1 for l in manifest.read_text().splitlines() if l.startswith("vs=")), []

    lines = manifest.read_text().splitlines()
    added = 0
    touched = []
    for tag, keys in incoming.items():
        header = f"# features={tag}"
        if header in lines:
            start = lines.index(header) + 1
            end = start
            while end < len(lines) and not lines[end].startswith("# features=") and not lines[end].startswith("# recorded from"):
                end += 1
            existing = {l for l in lines[start:end] if l.startswith("vs=")}
            fresh = [k for k in dict.fromkeys(keys) if k not in existing]
            lines[end:end] = fresh
        else:
            fresh = list(dict.fromkeys(keys))
            lines.extend(["# recorded from a ./rx device coverage run", header, *fresh])
        added += len(fresh)
        touched.append((tag, len(fresh)))
    manifest.write_text("\n".join(lines) + "\n")
    total = sum(1 for l in lines if l.startswith("vs="))
    return added, total, touched


def do_coverage(args):
    """Record the Metal pipelines a device run reaches and merge them in."""
    udid = choose_udid(args.udid)
    bundle = installed_bundle_id(udid)
    env = list(args.env)
    target = record_target(udid, bundle)
    env.append(f"RRV_PCSX2_GS_PIPELINE_RECORD={target}")
    run_args = argparse.Namespace(
        seconds=args.seconds, breakdown=False, warmup=args.warmup, label=args.label or "coverage",
        env=env, no_match=args.no_match, shots_every=args.shots_every, app=args.app, udid=udid,
        no_device_telemetry=getattr(args, "no_device_telemetry", False))
    run_dir = do_run(run_args)
    pulled = Path(run_dir) / "perf" / "coverage.txt"
    pulled.parent.mkdir(exist_ok=True)
    pmd("apps", "pull", bundle, coverage_record_remote(udid), str(pulled),
        *global_device_args(udid), timeout=120, check=False)
    candidates = [pulled] if pulled.is_file() and pulled.stat().st_size else []
    if not candidates:
        summary = json.loads((Path(run_dir) / "summary.json").read_text())
        compiles = (summary.get("metrics") or {}).get("pipeline_compiles_delta_run") or 0
        print(f"no selectors were recorded (nothing new at these settings); run dir {run_dir}")
        if compiles:
            print(f"WARNING: the run compiled {compiles:.0f} pipelines but recorded no file; "
                  f"the app could not write {target}")
        return
    recorded = candidates[0]
    added, total, touched = merge_pipeline_keys(recorded)
    for tag, count in touched:
        print(f"  {count:4d} new selectors for features {tag}")
    summary = json.loads((Path(run_dir) / "summary.json").read_text())
    metrics = summary.get("metrics") or {}
    print(f"coverage: {added} new selectors, manifest now {total} "
          f"(fps {metrics.get('fps', 0):.2f}, compiles {metrics.get('pipeline_compiles_delta_run', 0):.0f})")
    if added:
        print("next: ./rx ipa --platform ios && ./rx device install --lean")


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
    r.add_argument("--breakdown", action="store_true", help="enable sampled execution timing")
    r.add_argument("--warmup", type=float, default=0)
    r.add_argument("--label")
    r.add_argument("--env", action="append", default=[])
    r.add_argument("--no-match", action="store_true")
    r.add_argument("--shots-every", type=int, default=20)
    r.add_argument("--app")
    r.add_argument("--udid")
    r.add_argument("--no-pipeline-cache", action="store_true",
                   help="do not seed/persist the learned Metal pipeline cache")
    r.add_argument("--real-user-dir", action="store_true",
                   help="use the app's default user directory instead of an isolated one")
    s = sub.add_parser("summarize")
    s.add_argument("run_dir")
    c = sub.add_parser("compare")
    c.add_argument("runA")
    c.add_argument("runB")
    ab = sub.add_parser("ab")
    ab.add_argument("--control", action="append", required=True)
    ab.add_argument("--candidate", action="append", required=True)
    ab.add_argument("--control-app")
    ab.add_argument("--candidate-app")
    ab.add_argument("--pairs", type=int, required=True)
    ab.add_argument("--seconds", type=int, required=True)
    ab.add_argument("--label", required=True)
    ab.add_argument("--breakdown", action="store_true", help="enable sampled execution timing")
    ab.add_argument("--warmup", type=float, default=0)
    ab.add_argument("--udid")
    ab.add_argument("--no-pipeline-cache", action="store_true")
    cov = sub.add_parser("coverage")
    cov.add_argument("--seconds", type=int, default=480)
    cov.add_argument("--label")
    cov.add_argument("--env", action="append", default=[])
    cov.add_argument("--no-match", action="store_true")
    cov.add_argument("--shots-every", type=int, default=60)
    cov.add_argument("--warmup", type=float, default=0)
    cov.add_argument("--app")
    cov.add_argument("--udid")
    cov.add_argument("--no-pipeline-cache", action="store_true")
    baseline = sub.add_parser("baseline")
    baseline.add_argument("--runs", type=int, required=True)
    baseline.add_argument("--seconds", type=int, required=True)
    baseline.add_argument("--label", required=True)
    baseline.add_argument("--env", action="append", default=[])
    baseline.add_argument("--breakdown", action="store_true", help="enable sampled execution timing")
    baseline.add_argument("--warmup", type=float, default=0)
    baseline.add_argument("--udid")
    baseline.add_argument("--no-pipeline-cache", action="store_true")
    for command_parser in (r, ab, cov, baseline):
        command_parser.add_argument("--no-device-telemetry", action="store_true",
            help="keep in-app perf; skip continuous DVT graphics/sysmon and battery polling")
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
        elif args.sub == "coverage":
            do_coverage(args)
        elif args.sub in ("ab", "baseline"):
            run_experiment(args, args.sub)
        return 0
    except Missing as e:
        print(f"MISSING: {e}", file=sys.stderr)
        return 2
    except (Failed, OSError, ValueError, KeyError) as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
