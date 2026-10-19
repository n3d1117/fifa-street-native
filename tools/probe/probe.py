#!/usr/bin/env python3
"""Bounded native boot run with isolated saves; writes summary.json and run.log into --out.

  --save DIR     seed a fresh isolated user dir from DIR/memcards
  --user-dir D   use this user dir instead of a scratch one
  --profile      macOS `sample` of the GameThread after --profile-after seconds
  --app BUNDLE   exercise an installed .app (the app binary is the game itself; same isolated user dir and diagnostics)
  --quit-at-end  with --app: end the run with an AppleEvent quit (like Dock/Cmd-Q) and require exit 0 within 30 s
Stops on the first fault marker unless --keep-running-on-failure. `reached` only says which milestone was observed:
running (alive at --seconds), exited (clean exit before it) or crash. A metric whose log line is missing is null.
Exit 0 if the process reached "running" with no crash and the real save dir is unchanged, else 1; 2 if a prerequisite is missing.
"""
import argparse
import collections
import hashlib
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import game  # noqa: E402
import where as symbols  # noqa: E402

TOOLS_OUT = ROOT / "build/probe-tools"
REAL_SUPPORT = Path.home() / "Library/Application Support" / game.GAME["executable"]
DEFAULT_RUNNER = ROOT / "build/game-metal/ps2recomp/ps2xRuntime/ps2EntryRunner"
ELF = game.ELF
CRASH_MARKERS = ("Error during program execution", "[terminate]", "No exact recompiled function",
                 "Failed to initialize", "Failed to load ELF", "[guest-branch:missing-target]",
                 "[memory-fault]")
FAULT_MARKERS = CRASH_MARKERS + ("[iop-budget] entry=",)
TOOL_FRAMEWORKS = {
    "capture-window": ["-framework", "Cocoa", "-framework", "CoreGraphics", "-framework", "ImageIO"],
}
BLACK_SAMPLE_START, BLACK_SAMPLE_EVERY = 10.0, 5.0


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


def parse_log(text):
    """Derive every metric from lines the runtime already logs. Missing -> absent key."""
    out = {}
    vu = re.findall(r"\[vu1rec\] \w+: .*?fallback=(\d+) .*?\(DISPFB2 ([0-9.]+)\)", text)
    if vu:
        out["vu_fallbacks"] = int(vu[-1][0])
        out["fps_dispfb2"] = round(float(vu[-1][1]), 1) if float(vu[-1][1]) > 0 else None
    under = re.findall(r"\[spu2-health\].*?underrun=(\d+)", text)
    if under:
        out["audio_underruns"] = sum(int(n) for n in under)
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
    for pat in (r"\[memory-fault\].*? pc=0x([0-9a-f]+)", r"guest PC 0x([0-9a-f]+)",
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


_EXITED_WATCHES = set()


def child_exited(exit_watch):
    """Check without reaping so wait4 can collect this child's resource usage.
    max_events must be positive or kqueue returns nothing, and the NOTE_EXIT event
    is delivered once, so the answer is remembered until the watch is closed."""
    if exit_watch.fileno() in _EXITED_WATCHES:
        return True
    if exit_watch.control(None, 1, 0):
        _EXITED_WATCHES.add(exit_watch.fileno())
        return True
    return False


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
    """Window capture black-frame count (capture-window prints the frame's mean luma)."""

    def __init__(self, out):
        self.dir = out / "frames"
        self.ok = True
        try:
            self.cap = tool("capture-window")
        except (subprocess.CalledProcessError, OSError):
            self.ok = False
        self.frames = self.black = 0

    def sample(self, pid, timeout=8):
        if not self.ok:
            return
        try:
            r = subprocess.run([str(self.cap), str(pid), str(self.dir), "1"], capture_output=True, text=True, timeout=timeout)
        except (subprocess.TimeoutExpired, OSError):
            return
        m = re.search(r"luma=([0-9.]+)", r.stdout) if r.returncode == 0 else None
        if m:
            self.frames += 1
            self.black += float(m[1]) < 0.02


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, help="run directory (default build/runs/<UTC>)")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--user-dir", type=Path)
    ap.add_argument("--save", type=Path, help="fixture directory containing a non-empty memcards/ subtree")
    ap.add_argument("--runner", type=Path)
    ap.add_argument("--app", type=Path)
    ap.add_argument("--quit-at-end", action="store_true")
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--profile-after", type=float, default=10)
    ap.add_argument("--keep-running-on-failure", action="store_true")
    args = ap.parse_args()
    if args.quit_at_end and not args.app:
        ap.error("--quit-at-end needs --app")
    if args.runner and args.app:
        ap.error("--runner and --app are mutually exclusive")
    if args.save and args.user_dir:
        ap.error("--save and --user-dir cannot be combined")
    fixture_cards = None
    if args.save:
        fixture = args.save.resolve()
        fixture_cards = fixture / "memcards"
        if not fixture.is_dir() or not fixture_cards.is_dir() or not any(p.is_file() for p in fixture_cards.rglob("*")):
            print(f"save fixture missing or empty: {fixture} (copy a user dir's memcards/ subtree into it)", file=sys.stderr)
            return 2

    out = (args.out or ROOT / "build/runs" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())).resolve()
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "run.log"
    scratch = None
    if args.user_dir:
        user_dir = args.user_dir.resolve()
        user_dir.mkdir(parents=True, exist_ok=True)
    else:
        scratch = user_dir = Path(tempfile.mkdtemp(prefix="rx-run-"))
    card_files = 0
    if args.save:
        shutil.copytree(fixture_cards, user_dir / "memcards", dirs_exist_ok=True)
        card_files = sum(1 for p in (user_dir / "memcards").rglob("*") if p.is_file())
    explicit_real = bool(args.user_dir) and user_dir == REAL_SUPPORT.resolve()
    before = save_hash(REAL_SUPPORT)

    env = os.environ.copy()
    env.setdefault("PS2X_TRACE_FAULTS", "1")
    env.setdefault("PS2X_TRACE_SPU2", "1")
    if args.app:
        app = args.app.resolve(strict=True)
        command = [str(app / "Contents/MacOS" / game.GAME["executable"]), "--user-dir", str(user_dir)]
    else:
        runner = (args.runner or DEFAULT_RUNNER).resolve(strict=True)
        if not ELF.is_file():
            print(f"{ELF.relative_to(ROOT)} missing -> ./rx disc <iso>", file=sys.stderr)
            return 2
        command = [str(runner), "--user-dir", str(user_dir), str(ELF)]

    notes = []
    started = time.monotonic()
    logf = log_path.open("wb")
    if args.save:
        logf.write(f"[probe] seeded {card_files} memory-card files from fixture into {user_dir / 'memcards'}\n".encode())
        logf.flush()
    # Own process group (not a new session: the terminal keeps its file-access grant) so nothing the game spawns outlives the run; probe death (SIGTERM, SIGHUP, a
    # closed pipe) unwinds through the same cleanup as a timeout.
    def _unwind(signum, _frame):
        raise KeyboardInterrupt
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _unwind)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT,
                            preexec_fn=os.setpgrp)
    exit_watch = select.kqueue()
    exit_watch.control([select.kevent(proc.pid, filter=select.KQ_FILTER_PROC,
                                      flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE,
                                      fflags=select.KQ_NOTE_EXIT)], 0, 0)
    cap = Capture(out)
    deadline = started + args.seconds
    killed_by_us = fault_stop = False
    profiled = None
    next_black = started + BLACK_SAMPLE_START
    seen_len = 0
    try:
        while time.monotonic() < deadline and not child_exited(exit_watch):
            now = time.monotonic()
            text_now = log_path.read_text(errors="replace")
            if not args.keep_running_on_failure and any(m in text_now[seen_len:] for m in FAULT_MARKERS):
                fault_stop = True
                break
            seen_len = len(text_now)
            if args.profile and profiled is None and now - started >= args.profile_after:
                profiled = sample_game_thread(proc.pid, out / "game-thread.sample") or []
            if now >= next_black:
                cap.sample(proc.pid)
                next_black = time.monotonic() + BLACK_SAMPLE_EVERY
            time.sleep(0.2)
    finally:
        quit_ok = None
        if args.quit_at_end and not child_exited(exit_watch):
            subprocess.run(["osascript", "-e", f'tell application id "{game.GAME["bundle_id"]}" to quit'],
                           capture_output=True, timeout=30)  # the app cancels terminate and shuts down itself
            end = time.monotonic() + 30
            while time.monotonic() < end and not child_exited(exit_watch):
                time.sleep(0.01)
            quit_ok = child_exited(exit_watch)
            if not quit_ok:
                notes.append("quit did not exit within 30 s")
        exited_early = child_exited(exit_watch) and not args.quit_at_end and time.monotonic() < deadline
        killed = None
        if not child_exited(exit_watch):
            killed_by_us = True
            killed = "term"
            os.killpg(proc.pid, signal.SIGTERM)  # the GS dependency can swallow SIGTERM: escalate
            end = time.monotonic() + 2
            while time.monotonic() < end and not child_exited(exit_watch):
                time.sleep(0.01)
            if not child_exited(exit_watch):
                killed = "kill"
                os.killpg(proc.pid, signal.SIGKILL)
        _, status, usage = os.wait4(proc.pid, 0)
        proc.returncode = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -os.WTERMSIG(status)
        if args.quit_at_end and quit_ok:
            quit_ok = proc.returncode == 0
        _EXITED_WATCHES.discard(exit_watch.fileno())
        exit_watch.close()
        cpu_user_s, cpu_sys_s = round(usage.ru_utime, 3), round(usage.ru_stime, 3)
        try:  # reap stragglers in the group, then prove it is empty
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        else:
            time.sleep(0.2)
            notes.append("killed stragglers in the game's process group")
        gone = subprocess.run(["pgrep", "-g", str(proc.pid)], capture_output=True).returncode != 0
        if not gone:
            notes.append(f"process group {proc.pid} still alive after SIGKILL")
        logf.close()

    elapsed = round(time.monotonic() - started, 1)
    text = log_path.read_text(errors="replace")
    metrics = parse_log(text)
    crash = crash_from(text, proc.returncode, killed_by_us)
    report = None
    if proc.returncode and proc.returncode < 0 and not killed_by_us:
        reports = Path.home() / "Library/Logs/DiagnosticReports"
        for _ in range(6):  # macOS writes crash reports asynchronously; match our pid only
            found = [r for pattern in ("ps2EntryRunner*.crash", f"{game.GAME['executable']}*.crash")
                     for r in reports.glob(pattern) if f"[{proc.pid}]" in r.read_text(errors="replace")[:2000]]
            if found:
                shutil.copy2(found[0], out / found[0].name)
                report = found[0].name
                break
            time.sleep(0.5)

    if crash or fault_stop:
        reached = "crash"
    elif exited_early:
        reached = "exited"
    else:
        reached = "running"
    changed = save_hash(REAL_SUPPORT) != before and not explicit_real
    target = "running"
    summary = {
        "reached": reached, "target": target, "seconds": elapsed,
        "fps_dispfb2": metrics.get("fps_dispfb2"), "cpu_user_s": cpu_user_s, "cpu_sys_s": cpu_sys_s,
        "vu_fallbacks": metrics.get("vu_fallbacks"), "audio_underruns": metrics.get("audio_underruns"),
        "crash": crash, "stubs": metrics["stubs"], "faults": metrics["faults"],
        "black_frames": f"{cap.black}/{cap.frames}" if cap.frames else None,
        "save_dir_changed": changed, "exit_code": proc.returncode, "profile_top": profiled,
        "killed": killed, "notes": notes, "crash_report": report, "log": str(log_path), "user_dir": str(user_dir), "quit_clean": quit_ok,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if scratch:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0 if reached == target and not changed and not summary["audio_underruns"] and quit_ok is not False else 1


if __name__ == "__main__":
    sys.exit(main())
