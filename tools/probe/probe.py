#!/usr/bin/env python3
"""Bounded native run with isolated saves; writes summary.json and run.log into --out.

Merges the former run-native-check / diagnose-native / probe-first-level scripts.
  --route first-mission  scripted new-game route through process-local diagnostic input
                         (OCR guard on the language menu before the first key)
  --profile              macOS `sample` of the GameThread after --profile-after seconds
  --gpu-capture          bounded Metal frame capture (TARZAN_CAPTURE_*)
  --app BUNDLE           exercise an installed .app through its real launcher
With no route the run ends as soon as the language menu is confirmed; --seconds gets 15 s of grace for it.
Stops on the first fault marker unless --keep-running-on-failure. Never certifies gameplay:
`reached` only says which milestone was observed. A metric whose log line is missing is null.
Exit 0 if the target milestone was reached with no crash and the save dir is unchanged, else 1.
"""
import argparse
import collections
import hashlib
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import where as symbols  # noqa: E402

TITLE_GRACE = 15
TOOLS_OUT = ROOT / "build/probe-tools"
REAL_SUPPORT = Path.home() / "Library/Application Support/TarzanFreeridePS2"
DEFAULT_RUNNER = ROOT / "build/game-metal/ps2recomp/ps2xRuntime/ps2EntryRunner"
ELF = ROOT / "extracted/disc/SLES_503.50"
CRASH_MARKERS = ("Error during program execution", "[terminate]", "No exact recompiled function",
                 "Failed to initialize", "Failed to load ELF", "[guest-branch:missing-target]",
                 "[tarzan-memory-fault]")
FAULT_MARKERS = CRASH_MARKERS + ("[tarzan-iop-budget] entry=",)
TOOL_FRAMEWORKS = {
    "capture-window": ["-framework", "Cocoa", "-framework", "CoreGraphics", "-framework", "ImageIO"],
    "ocr-window": ["-framework", "Cocoa", "-framework", "Vision", "-framework", "ImageIO"],
    "send-native-key": ["-framework", "Cocoa", "-framework", "ApplicationServices", "-framework", "Carbon"],
}
LOGICAL_KEY = {7: 5, 36: 17, 125: 2, 126: 1, 8: 7}
# (second, macOS key code): the validated menu route, new-game variant with three name characters.
ROUTE_FIRST_MISSION = [(47, 7), (53, 36), (61, 7), (68, 7), (75, 7), (82, 7),
                       (89, 7), (90, 7), (91, 7), (98, 7), (105, 7)]


def tool(name):
    """Compile tools/probe/<name>.m into build/probe-tools/ when missing or stale."""
    src, out = ROOT / f"tools/probe/{name}.m", TOOLS_OUT / name
    if not out.is_file() or src.stat().st_mtime > out.stat().st_mtime:
        TOOLS_OUT.mkdir(parents=True, exist_ok=True)
        subprocess.run(["xcrun", "clang", "-fobjc-arc", "-mmacosx-version-min=11.0", str(src),
                        *TOOL_FRAMEWORKS[name], "-o", str(out)], check=True, capture_output=True)
    return out


def save_hash(support):
    """sha256 over the memory card files (the saves) of a user dir; None when the dir is absent."""
    support = Path(support)
    if not support.is_dir():
        return None
    h = hashlib.sha256()
    for p in sorted(p for p in support.glob("memcards*/**/*") if p.is_file()):
        h.update(str(p.relative_to(support)).encode())
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def english_menu_present(lines):
    def has(text):
        return any(l.get("text", "").strip().casefold() == text and l.get("confidence", 0) >= 0.5 for l in lines)
    others = {l.get("text", "").strip().casefold() for l in lines if l.get("confidence", 0) >= 0.5} & \
        {"français", "francais", "español", "italiano", "deutsch"}
    return has("language") and has("english") and len(others) >= 2


def title_screen_present(lines):
    """Language menu, or the title screen ("TARZAN" logo plus a localized "PRESS START" line), ignoring the window title."""
    texts = [l.get("text", "").casefold() for l in lines if l.get("confidence", 0) >= 0.5
             and "native" not in l.get("text", "").casefold()]
    return english_menu_present(lines) or (any("tarzan" in t for t in texts) and any("start" in t for t in texts))


def split_underruns(text):
    """(steady, boundary, overruns, saw_health). Boundary = the first health line after a PSS movie's audio stream
    starts (`iop-audio-rpc fn=12` following a .PSS open), and the
    health line containing or following an `iop-audio-rpc fn=11 ... init=0` (movie EOF flush)."""
    steady = boundary = overruns = 0
    seen = False
    pss_open = after_start = after_flush = False
    for line in text.splitlines():
        if re.search(r"\[sceCdSearchFile:ok\] path=\"[^\"]*\.PSS", line):
            pss_open = True
        elif pss_open and re.search(r"\[iop-audio-rpc\] fn=12 ", line):
            pss_open, after_start = False, True
        elif re.search(r"\[iop-audio-rpc\] fn=11 .*\binit=0\b", line):
            after_flush = True
        else:
            m = re.search(r"\[tarzan-spu2-health\].*?underrun=(\d+) overrun=(\d+)", line)
            if m:
                seen = True
                overruns += int(m[2])
                if after_start or after_flush:
                    boundary += int(m[1])
                else:
                    steady += int(m[1])
                after_start = after_flush = False
    return steady, boundary, overruns, seen


def parse_log(text):
    """Derive every metric from lines the runtime already logs. Missing -> absent key."""
    out = {}
    vu = re.findall(r"\[vu1rec\] \w+: .*?fallback=(\d+) .*?nativeFail=(\d+) .*?\(DISPFB2 ([0-9.]+)\)", text)
    if vu:
        out["vu_fallbacks"] = int(vu[-1][0])
        out["vu_native_fail"] = int(vu[-1][1])
        # The latest periodic line describes the current phase (movies run at 50, gameplay at 25).
        out["fps_dispfb2"] = round(float(vu[-1][2]), 1) if float(vu[-1][2]) > 0 else None
    steady, boundary, overruns, seen = split_underruns(text)
    if seen:
        out["audio_underruns"] = steady
        out["audio_boundary_underruns"] = boundary
        out["audio_overruns"] = overruns
    out["vu0_active"] = "[vu0rec] image=" in text or "[vu0rec] calls=" in text
    stubs = collections.Counter(re.findall(r"Unimplemented PS2 stub called\. name=(\S+)", text))
    for m in re.finditer(r"\[Syscall TODO\] encoded=0x([0-9a-f]+) v1=0x([0-9a-f]+)", text):
        stubs[f"syscall:0x{int(m[1], 16) or int(m[2], 16):x}"] += 1
    out["stubs"] = [[n, c] for n, c in stubs.most_common(5)]
    out["faults"] = [l[:300] for l in text.splitlines() if any(m in l for m in FAULT_MARKERS)][:10]
    return out


def crash_from(text, returncode, killed_by_us):
    """Describe how the process died, or None. Guest PC comes from the runtime's own fault lines."""
    sig = signal.Signals(-returncode).name if returncode and returncode < 0 and not killed_by_us else None
    pc = None
    for pat in (r"\[tarzan-memory-fault\].*? pc=0x([0-9a-f]+)", r"guest PC 0x([0-9a-f]+)",
                r"\[guest-branch:missing-target\].*?\btarget=0x([0-9a-f]+)"):
        m = re.search(pat, text)
        if m:
            pc = int(m[1], 16)
            break
    term = "[terminate]" in text
    if not (sig or term or any(m in text for m in CRASH_MARKERS)):
        return None
    sym = symbols.symbol_at(pc) if pc is not None else None
    return {"signal": sig or ("terminate" if term else "fault"),
            "pc": f"0x{pc:x}" if pc is not None else None, "symbol": sym[0] if sym else None}


def sample_game_thread(pid, path):
    exe = shutil.which("sample")
    if not exe:
        return None
    try:
        subprocess.run([exe, str(pid), "2", "-file", str(path)], capture_output=True, timeout=8)
    except subprocess.TimeoutExpired:
        return None
    counts, inside = collections.Counter(), False
    for line in Path(path).read_text(errors="replace").splitlines() if Path(path).exists() else []:
        if re.search(r"Thread_\d+:\s*GameThread", line):
            inside = True
            continue
        if inside and re.search(r"Thread_\d+:", line):
            break
        m = re.search(r"\+\s*(\d+)\s+(.+?)\s+\(in ps2EntryRunner\)", line) if inside and "_0x" in line else None
        if m:
            counts[m[2].strip()] += int(m[1])
    return [[n, c] for n, c in counts.most_common(5)]


class Capture:
    """Window capture + OCR + black-frame count, sharing one scratch dir."""

    def __init__(self, out):
        self.dir = out / "frames"
        self.ok = True
        try:
            self.cap, self.ocr = tool("capture-window"), tool("ocr-window")
        except (subprocess.CalledProcessError, OSError):
            self.ok = False
        self.frames = self.black = 0

    def grab(self, pid, timeout=8):
        """OCR lines of the live window, or None. Counts black frames from capture-window's luma line."""
        if not self.ok:
            return None
        try:
            r = subprocess.run([str(self.cap), str(pid), str(self.dir), "1"], capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                return None
            m = re.search(r"luma=([0-9.]+)", r.stdout)
            if m:
                self.frames += 1
                self.black += float(m[1]) < 0.02
            o = subprocess.run([str(self.ocr), str(self.dir / "window-000.png")], capture_output=True, text=True, timeout=timeout)
            return json.loads(o.stdout) if o.returncode == 0 else None
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, help="run directory (default build/runs/<UTC>)")
    ap.add_argument("--seconds", type=int)
    ap.add_argument("--route", choices=("none", "first-mission"), default="none")
    ap.add_argument("--user-dir", type=Path)
    ap.add_argument("--runner", type=Path)
    ap.add_argument("--app", type=Path)
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--profile-after", type=float, default=10)
    ap.add_argument("--gpu-capture", action="store_true")
    ap.add_argument("--capture-frames", type=int, default=30)
    ap.add_argument("--capture-start-field", type=int, default=0)
    ap.add_argument("--capture-interval", type=int, default=1)
    ap.add_argument("--keep-running-on-failure", action="store_true")
    ap.add_argument("--hold-ms", type=int, default=150)
    ap.add_argument("--desktop-input", action="store_true",
                    help="synthetic desktop key events instead of the diagnostic FIFO (needs Accessibility)")
    args = ap.parse_args()
    if args.runner and args.app:
        ap.error("--runner and --app are mutually exclusive")
    if args.route != "none" and args.app:
        ap.error("--route needs the runner's diagnostic input; it cannot drive --app")
    seconds = args.seconds or (150 if args.route == "first-mission" else 60)
    if args.route == "first-mission" and seconds < 140:
        ap.error("--route first-mission needs --seconds >= 140")
    if not 1 <= args.capture_frames <= 120 or args.capture_interval < 1 or args.capture_start_field < 0:
        ap.error("invalid capture settings")

    out = (args.out or ROOT / "build/runs" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())).resolve()
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "run.log"
    scratch = None
    if args.user_dir:
        user_dir = args.user_dir.resolve()
        user_dir.mkdir(parents=True, exist_ok=True)
    else:
        scratch = user_dir = Path(tempfile.mkdtemp(prefix="tz-run-"))
    if args.route != "none" and any(user_dir.iterdir()):
        ap.error("--route first-mission needs an empty --user-dir (new game)")
    explicit_real = bool(args.user_dir) and user_dir == REAL_SUPPORT.resolve()
    before = save_hash(REAL_SUPPORT)

    env = os.environ.copy()
    env.setdefault("TARZAN_TRACE_FAULTS", "1")
    env.setdefault("TARZAN_TRACE_SPU2", "1")
    for k in ("TARZAN_CAPTURE_DIR", "TARZAN_CAPTURE_FRAMES", "TARZAN_CAPTURE_START_FIELD", "TARZAN_CAPTURE_INTERVAL"):
        env.pop(k, None)
    if args.gpu_capture:
        cdir = out / "gpu-capture"
        cdir.mkdir(exist_ok=True)
        env.update(TARZAN_CAPTURE_DIR=str(cdir), TARZAN_CAPTURE_FRAMES=str(args.capture_frames),
                   TARZAN_CAPTURE_START_FIELD=str(args.capture_start_field),
                   TARZAN_CAPTURE_INTERVAL=str(args.capture_interval))
    fifo_fd = None
    if args.route != "none" and not args.desktop_input:
        fifo = out / "input.fifo"
        os.mkfifo(fifo, 0o600)
        fifo_fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
        env["TARZAN_DIAGNOSTIC_INPUT_FIFO"] = str(fifo)

    offsets = {}
    if args.app:
        app = args.app.resolve(strict=True)
        command = [str(app / "Contents/MacOS/TarzanFreeridePS2Launcher")]
        # The launcher always uses the real support dir (a throwaway HOME breaks PCSX2's data dir), so the
        # save-dir hash below is the guard; its own log there is read from the offset recorded now.
        log = REAL_SUPPORT / "Logs/native.log"
        offsets[log] = log.stat().st_size if log.exists() else 0
    else:
        runner = (args.runner or DEFAULT_RUNNER).resolve(strict=True)
        if not ELF.is_file():
            print("extracted/disc/SLES_503.50 missing -> ./tz disc <iso>", file=sys.stderr)
            return 2
        command = [str(runner), "--user-dir", str(user_dir), str(ELF)]

    notes, keys = [], []
    started = time.monotonic()
    logf = log_path.open("wb")
    proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT)
    cap = Capture(out)

    def log_text():
        if not args.app:
            return log_path.read_text(errors="replace")
        for c, off in offsets.items():
            if c.exists() and c.stat().st_size > off:
                with c.open("rb") as f:
                    f.seek(off)
                    return f.read().decode("utf-8", "replace")
        return ""

    def send_key(key):
        if fifo_fd is not None:
            os.write(fifo_fd, struct.pack("<I", 1 << LOGICAL_KEY[key]))
            time.sleep(args.hold_ms / 1000)
            os.write(fifo_fd, struct.pack("<I", 0))
            return 0
        subprocess.run(["osascript", "-e", 'tell application "System Events" to set frontmost of first process '
                        f'whose unix id is {proc.pid} to true'], capture_output=True, timeout=5)
        time.sleep(.15)
        return subprocess.run([str(tool("send-native-key")), "--pid", str(proc.pid), "--key-code", str(key),
                               "--hold-ms", str(args.hold_ms)], capture_output=True).returncode

    route = ROUTE_FIRST_MISSION if args.route == "first-mission" else []
    # Without a route the target is the title menu, which appears ~28-30 s into a cold boot: allow grace for it.
    deadline = started + seconds + (0 if route else TITLE_GRACE)
    reached = None
    killed_by_us = fault_stop = nav_failed = False
    menu_at = last_hit = None
    next_ocr, profiled = 0.0, None
    route_i, route_delay, seen_len = 0, 0.0, 0
    try:
        while time.monotonic() < deadline and proc.poll() is None:
            now = time.monotonic() - started
            text_now = log_text()
            if not args.keep_running_on_failure and any(m in text_now[seen_len:] for m in FAULT_MARKERS):
                fault_stop = True
                break
            seen_len = len(text_now)
            if args.profile and profiled is None and now >= args.profile_after:
                profiled = sample_game_thread(proc.pid, out / "game-thread.sample") or []
            # Language-menu guard: two OCR hits >= 0.5 s apart. It is the title milestone and gates the first route key.
            want_menu = (route and route_i == 0 and now >= route[0][0]) or (not route and reached is None)
            if want_menu and now >= next_ocr:
                lines = cap.grab(proc.pid)
                next_ocr = time.monotonic() - started + 1.0
                if lines is not None and (english_menu_present(lines) if route else title_screen_present(lines)):
                    t = time.monotonic()
                    if last_hit is not None and t - last_hit >= 0.5:
                        menu_at = round(now, 1)
                        if not route:
                            reached = "title"
                            break
                        route_delay = max(0.0, now - route[0][0])
                    last_hit = last_hit or t
                else:
                    last_hit = None
                if route and menu_at is None and now - route[0][0] > 45:
                    nav_failed = True
                    notes.append("English language menu not confirmed on two OCR frames")
                    break
            if route and route_i < len(route) and (menu_at is not None or route_i > 0) \
                    and now >= route[route_i][0] + route_delay:
                rc = send_key(route[route_i][1])
                keys.append({"second": route[route_i][0], "elapsed": round(time.monotonic() - started, 1), "rc": rc})
                route_i += 1
            time.sleep(0.2)
        if route and route_i == len(route) and proc.poll() is None and not fault_stop:
            cap.grab(proc.pid)
    finally:
        if proc.poll() is None:
            killed_by_us = True
            proc.send_signal(signal.SIGTERM)  # the GS dependency can swallow SIGTERM: escalate
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        logf.close()
        if fifo_fd is not None:
            os.close(fifo_fd)

    elapsed = round(time.monotonic() - started, 1)
    text = log_text()
    if args.app:
        log_path.write_text(text)
    metrics = parse_log(text)
    crash = crash_from(text, proc.returncode, killed_by_us)
    report = None
    if proc.returncode and proc.returncode < 0 and not killed_by_us:
        for _ in range(6):  # macOS writes crash reports asynchronously; match our pid only
            found = [r for r in (Path.home() / "Library/Logs/DiagnosticReports").glob("ps2EntryRunner*.crash")
                     if f"[{proc.pid}]" in r.read_text(errors="replace")[:2000]]
            if found:
                shutil.copy2(found[0], out / found[0].name)
                report = found[0].name
                break
            time.sleep(0.5)

    if crash is None and route and route_i == len(route) and not nav_failed:
        if metrics["vu0_active"] or metrics.get("fps_dispfb2", 0) >= 15:
            reached = "first-mission"
    if crash:
        reached = "crash"
    elif reached is None:
        reached = "timeout"
    changed = save_hash(REAL_SUPPORT) != before and not explicit_real
    target = "first-mission" if route else "title"
    summary = {
        "reached": reached, "target": target, "seconds": elapsed,
        "fps_dispfb2": metrics.get("fps_dispfb2"), "presents_per_s": None,
        "vu_fallbacks": metrics.get("vu_fallbacks"), "audio_underruns": metrics.get("audio_underruns"),
        "audio_boundary_underruns": metrics.get("audio_boundary_underruns"),
        "audio_overruns": metrics.get("audio_overruns"), "vu_native_fail": metrics.get("vu_native_fail"),
        "crash": crash, "stubs": metrics["stubs"], "faults": metrics["faults"],
        "black_frames": f"{cap.black}/{cap.frames}" if cap.frames else None,
        "save_dir_changed": changed, "exit_code": proc.returncode, "profile_top": profiled,
        "menu_confirmed_s": menu_at, "keys": keys, "notes": notes, "crash_report": report,
        "log": str(log_path),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    shutil.rmtree(scratch, ignore_errors=True) if scratch else None
    steady_audio = bool(summary["audio_underruns"])
    return 0 if reached == target and not changed and not steady_audio else 1


if __name__ == "__main__":
    sys.exit(main())
