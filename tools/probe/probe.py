#!/usr/bin/env python3
"""Bounded native boot run with isolated saves; writes summary.json and run.log into --out.

  --save DIR     seed a fresh isolated user dir from DIR/memcards
  --user-dir D   use this user dir instead of a scratch one
  --profile      macOS `sample` after --profile-after seconds (from confirmed gameplay on a route)
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
import struct
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
# Scripted input routes: (seconds since the previous step, key bit[, hold seconds]) over the diagnostic input FIFO
# (5 Cross, 9 Square, 2 Down, 17 Start; bits 19-22 left-stick left/right/up/down). The title screen needs the
# language menu and all intro movies first; menu waits are measured, not guarded. match_play requires an advancing HUD clock.
ROUTES = {"match": {
    "steps": [(16, 5), (66, 17), (8, 5), (6, 2), (1.5, 5), (8, 2), (1.5, 5), (6, 5), (4, 5), (5, 5), (3, 5), (4, 9),
              (3, 5), (3, 9), (2, 5), (5, 5), (5, 5)],
    "play_steps": [(3, 20, 12.0), (1, 19, 12.0)], "tail": 35, "timeout": 540,
    "scene_marker": "DATA/AUDIO/PBPDAT_E.BIG"}}

FAULT_MARKERS = CRASH_MARKERS + ("[iop-budget] entry=",)
TOOL_FRAMEWORKS = {
    "capture-window": ["-framework", "Cocoa", "-framework", "CoreGraphics", "-framework", "ImageIO", "-framework", "Vision"],
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
        out["fps_dispfb2"] = round(float(vu[-1][1]), 1)
    presents = re.findall(r"\[gs-present\] frames=(\d+) over=([0-9.]+)s presents/s=([0-9.]+)", text)
    if presents:
        out["gpu_presents_per_sec"] = round(float(presents[-1][2]), 1)
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
    """Window capture, black-frame count, and advancing match-clock detection."""

    def __init__(self, out, preserve=False, started=None):
        self.dir = out / "frames"
        self.preserve = preserve
        self.samples = 0
        self.ok = True
        try:
            self.cap = tool("capture-window")
        except (subprocess.CalledProcessError, OSError):
            self.ok = False
        self.frames = self.black = 0
        self.match_clock = None
        self._previous_clock = None
        self._clock_advances = 0
        self.gameplay_confirmed = False
        self.started = time.monotonic() if started is None else started
        self.clock_observations = []

    def sample(self, pid, timeout=8):
        if not self.ok:
            return
        requested_at = time.monotonic()
        frame_dir = self.dir
        if self.preserve:
            frame_dir = self.dir / f"sample-{self.samples:03d}"
        self.samples += 1
        try:
            r = subprocess.run([str(self.cap), str(pid), str(frame_dir), "1"], capture_output=True, text=True, timeout=timeout)
        except (subprocess.TimeoutExpired, OSError):
            return
        m = re.search(r"luma=([0-9.]+)", r.stdout) if r.returncode == 0 else None
        if m:
            self.frames += 1
            self.black += float(m[1]) < 0.02
        clock = None
        for line in r.stdout.splitlines():
            match = re.fullmatch(r"ocr=([0-9.]+),([0-9.]+):([0-9.]+),([0-9.]+):(.*)", line)
            if not match:
                continue
            x, y = float(match[1]), float(match[2])
            if not 0.42 <= x <= 0.58 or not 0.78 <= y <= 0.91:
                continue
            label = match[5].strip().upper().replace("O", "0").replace("I", "1")
            clock_match = re.fullmatch(r"(\d{1,2})[.:](\d{2})", label)
            if clock_match and int(clock_match[2]) < 60:
                clock = int(clock_match[1]) * 60 + int(clock_match[2])
                break
        if clock is not None:
            self.match_clock = clock
            self.clock_observations.append({"wall_seconds": round(requested_at - self.started, 3),
                                            "hud_clock_seconds": clock})
            if self._previous_clock is not None:
                if clock > self._previous_clock:
                    self._clock_advances += 1
                elif clock < self._previous_clock:
                    self._clock_advances = 0
            self._previous_clock = clock
            if self._clock_advances >= 2:
                self.gameplay_confirmed = True

    def clock_progress(self):
        observations = self.clock_observations
        result = {"observations": observations, "hud_clock_seconds_delta": None,
                  "wall_seconds_delta": None, "hud_clock_seconds_per_wall_second": None,
                  "timing": "capture request times; HUD clock rate, intended game speed unverified"}
        if len(observations) >= 2:
            wall_delta = observations[-1]["wall_seconds"] - observations[0]["wall_seconds"]
            clock_delta = observations[-1]["hud_clock_seconds"] - observations[0]["hud_clock_seconds"]
            result.update(hud_clock_seconds_delta=clock_delta, wall_seconds_delta=round(wall_delta, 3))
            if wall_delta > 0 and clock_delta >= 0:
                result["hud_clock_seconds_per_wall_second"] = round(clock_delta / wall_delta, 4)
        return result


def guest_progress(text, requests):
    """Associate requested state dumps with their counters, using signal times as approximate wall times."""
    blocks = re.findall(r"\[state-begin\]\n(.*?)\[state-end\]", text, re.S)
    snapshots = []
    for request in requests:
        index = request["block_index"]
        if index >= len(blocks):
            continue
        counters = re.search(r"^ee cycle=(\d+).*? vsync_tick=(\d+)$", blocks[index], re.M)
        if counters:
            snapshots.append({"requested_wall_seconds": request["wall_seconds"],
                              "ee_cycle": int(counters[1]), "vblank_tick": int(counters[2])})
    result = {"timing": "SIGUSR1 request-time approximation; runtime snapshots follow about one second of PC sampling",
              "snapshots": snapshots, "wall_seconds_delta": None, "vblank_ticks_delta": None,
              "ee_cycles_delta": None, "vblank_ticks_per_wall_second": None,
              "ee_cycles_per_wall_second": None}
    if len(snapshots) >= 2:
        first, last = snapshots[0], snapshots[-1]
        wall_delta = last["requested_wall_seconds"] - first["requested_wall_seconds"]
        vblank_delta = last["vblank_tick"] - first["vblank_tick"]
        cycle_delta = last["ee_cycle"] - first["ee_cycle"]
        result.update(wall_seconds_delta=round(wall_delta, 3), vblank_ticks_delta=vblank_delta,
                      ee_cycles_delta=cycle_delta)
        if wall_delta > 0 and vblank_delta >= 0 and cycle_delta >= 0:
            result.update(vblank_ticks_per_wall_second=round(vblank_delta / wall_delta, 3),
                          ee_cycles_per_wall_second=round(cycle_delta / wall_delta, 1))
    return result


def state_block(text):
    """The runtime's [state-begin]..[state-end] block, with sampled pcs resolved to generated function names."""
    lines = []
    for block in re.findall(r"\[state-begin\]\n(.*?)\[state-end\]", text, re.S)[-1:]:
        lines = block.strip().splitlines()
    if not lines:
        return None
    starts = []
    for path in (ROOT / "generated" / game.ID).glob("sub_*_0x*.cpp"):
        match = re.match(r"(sub_[0-9A-Fa-f]+)_0x([0-9a-f]+)\.cpp$", path.name)
        if match:
            starts.append((int(match[2], 16), match[1]))
    starts.sort()
    def name(pc):
        best = None
        for start, label in starts:
            if start > pc:
                break
            best = label
        return best or "?"
    out = []
    for line in lines:
        if line.startswith("ee pc samples"):
            parts = []
            for tok in line.split(":", 1)[1].split():
                pc, count = tok.rsplit("x", 1)
                parts.append(f"{pc}({name(int(pc, 16))}) x{count}")
            line = line.split(":", 1)[0] + ": " + ", ".join(parts)
        elif re.search(r" pc=0x([0-9a-f]+)", line) and line.startswith("ee thread"):
            line += f"  [{name(int(re.search(r' pc=0x([0-9a-f]+)', line).group(1), 16))}]"
        out.append(line)
    return out


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
    ap.add_argument("--route", choices=sorted(ROUTES), help="scripted input: language menu -> front end -> quick match")
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
    fifo_path = out / "input.fifo"
    if args.route:
        os.mkfifo(fifo_path)
        env["PS2X_DIAGNOSTIC_INPUT_FIFO"] = str(fifo_path)
        env.setdefault("PS2X_TRACE_CD_IO", "1")
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
    route = ROUTES.get(args.route)
    cap = Capture(out, preserve=bool(route), started=started)
    if route:
        args.seconds = max(args.seconds, route.get("timeout", 0))
    deadline = started + args.seconds
    route_deadline = deadline
    steps, step_at, release_at, fifo_fd = list(route["steps"]) if route else [], started, 0.0, None
    play_steps, play_step_at = list(route.get("play_steps", [])) if route else [], None
    if route:
        step_at = started + steps[0][0]
    killed_by_us = fault_stop = False
    state_asked = False
    state_requests = []
    gameplay_at = None
    profiled = None
    gameplay_input_key = None
    next_black = started + BLACK_SAMPLE_START
    seen_len = 0
    last_log_check = 0.0
    text_now = ""
    def request_state():
        requested_at = time.monotonic()
        current_text = log_path.read_text(errors="replace")
        if state_requests and current_text.count("[state-end]") <= state_requests[-1]["block_index"]:
            return False
        state_requests.append({"wall_seconds": round(requested_at - started, 3),
                               "block_index": current_text.count("[state-begin]")})
        os.kill(proc.pid, signal.SIGUSR1)
        return True

    try:
        while (time.monotonic() < deadline or
               (route and gameplay_at is not None and (play_steps or release_at) and
                time.monotonic() < route_deadline)) and not child_exited(exit_watch):
            now = time.monotonic()
            if now - last_log_check >= 0.2:
                last_log_check = now
                text_now = log_path.read_text(errors="replace")
                if not args.keep_running_on_failure and any(m in text_now[seen_len:] for m in FAULT_MARKERS):
                    fault_stop = True
                    break
                seen_len = len(text_now)
            if route:
                if gameplay_at is None and cap.gameplay_confirmed:
                    gameplay_at = now
                    deadline = min(route_deadline, gameplay_at + route["tail"])
                    state_asked = False
                    request_state()
                if play_steps and play_step_at is None and cap.gameplay_confirmed:
                    play_step_at = now + play_steps[0][0]
                if fifo_fd is None:
                    try:
                        fifo_fd = os.open(fifo_path, os.O_WRONLY | os.O_NONBLOCK)
                    except OSError:
                        pass
                if fifo_fd is not None and release_at and now >= release_at:
                    os.write(fifo_fd, struct.pack("<I", 0))
                    release_at = 0.0
                if fifo_fd is not None and steps and now >= step_at and not release_at:
                    step = steps.pop(0)
                    _, key = step[:2]
                    hold = step[2] if len(step) > 2 else 0.12
                    os.write(fifo_fd, struct.pack("<I", 1 << key))
                    release_at = now + hold
                    if steps:
                        step_at = now + steps[0][0]
                elif fifo_fd is not None and play_steps and play_step_at is not None and now >= play_step_at and not release_at:
                    step = play_steps.pop(0)
                    _, key, hold = step
                    os.write(fifo_fd, struct.pack("<I", 1 << key))
                    gameplay_input_key = key
                    release_at = now + hold
                    deadline = min(route_deadline, max(deadline, release_at + 0.2))
                    if play_steps:
                        play_step_at = now + play_steps[0][0]
            profile_at = gameplay_at if route else started
            if (args.profile and profiled is None and profile_at is not None and
                    now - profile_at >= args.profile_after):
                profiled = sample_game_thread(proc.pid, out / "game-thread.sample") or []
            if not state_asked and args.seconds >= 8 and now >= deadline - 3.5:
                state_asked = request_state()
            if now >= next_black:
                cap.sample(proc.pid)
                if route and play_steps and play_step_at is None and cap.gameplay_confirmed:
                    play_step_at = time.monotonic() + play_steps[0][0]
                next_black = time.monotonic() + BLACK_SAMPLE_EVERY
            time.sleep(0.04 if route else 0.2)
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
    state = state_block(text)
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
    scene = None
    commentary_open = False
    if route:
        commentary_open = route["scene_marker"] in text
        scene = "match_play" if cap.gameplay_confirmed else ("commentary_open" if commentary_open else f"menus ({len(route['steps']) - len(steps)} of {len(route['steps'])} steps sent)")
        if crash or fault_stop:
            scene = "crash"
    summary = {
        "reached": reached, "target": target, "seconds": elapsed,
        "fps_dispfb2": metrics.get("fps_dispfb2"),
        "gpu_presents_per_sec": metrics.get("gpu_presents_per_sec"),
        "cpu_user_s": cpu_user_s, "cpu_sys_s": cpu_sys_s,
        "vu_fallbacks": metrics.get("vu_fallbacks"), "audio_underruns": metrics.get("audio_underruns"),
        "crash": crash, "stubs": metrics["stubs"], "faults": metrics["faults"],
        "black_frames": f"{cap.black}/{cap.frames}" if cap.frames else None,
        "save_dir_changed": changed, "exit_code": proc.returncode, "profile_top": profiled,
        "profile_sample": str(out / "game-thread.sample") if (out / "game-thread.sample").is_file() else None,
        "profile_measurement": "sampled wall stacks across all threads; not per-thread CPU usage" if profiled is not None else None,
        "scene": scene, "commentary_open": commentary_open if route else None,
        "gameplay_confirmed": cap.gameplay_confirmed if route else None,
        "match_clock_seconds": cap.match_clock if route else None,
        "match_clock_progress": cap.clock_progress() if route else None,
        "guest_progress": guest_progress(text, state_requests) if route else None,
        "gameplay_input_key_bit": gameplay_input_key,
        "state": state, "killed": killed, "notes": notes, "crash_report": report, "log": str(log_path), "user_dir": str(user_dir), "quit_clean": quit_ok,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if scratch:
        shutil.rmtree(scratch, ignore_errors=True)
    scene_ok = not route or scene == "match_play"
    return 0 if reached == target and scene_ok and not changed and not summary["audio_underruns"] and quit_ok is not False else 1


if __name__ == "__main__":
    sys.exit(main())
