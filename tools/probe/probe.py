#!/usr/bin/env python3
"""Bounded native boot run with isolated saves; writes summary.json and run.log into --out.

  --save DIR     seed a fresh isolated user dir from DIR/memcards
  --user-dir D   use this user dir instead of a scratch one (relative: under build/user-dirs/)
  --profile      macOS `sample` after --profile-after seconds (from confirmed gameplay on a route)
  --app BUNDLE   exercise an installed .app (the app binary is the game itself; same isolated user dir and diagnostics)
  --quit-at-end  with --app: end the run with an AppleEvent quit (like Dock/Cmd-Q) and require exit 0 within 30 s
Stops on the first fault marker unless --keep-running-on-failure. `reached` only says which milestone was observed:
running (alive at --seconds), exited (clean exit before it) or crash. A metric whose log line is missing is null.
Exit 0 if the process reached "running" with no crash and the real save dir is unchanged, else 1; 2 if a prerequisite is missing.
"""
import argparse
import collections
import ctypes
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
import threading
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
# Route steps: minimum delay, diagnostic key bit, hold, and an OCR/log guard.
# Keys: Cross=5, Square=9, Down=2, Start=17; left stick left/right=19/20.
ROUTES = {"match": {
    "steps": [(10, 5, .12, "DEUTSCH"),
              (16, 5, .3, "log:DATA/FEMOVIES/INTRO.MPC"),
              (2, 17, .12, "PRESS START"),
              (2, 5, .12, "SAVE TYPE"),
              (2, 2, .12, "DO YOU WISH.*SAVE"), (1.5, 5, .12, "DO YOU WISH.*SAVE"),
              (2, 2, .12, "FRIENDLY"), (1.5, 5, .12, "FRIENDLY"),
              (2, 5, .12, "PICK SIDES"),
              (2, 5, .12, "PICK SIDES|SELECT TEAM"), (2, 5, .12, "SELECT TEAM"),
              (2, 5, .12, "SELECT TEAM|STARTING FOUR"), (2, 9, .12, "STARTING FOUR"),
              (2, 5, .12, "STARTING FOUR"), (2, 9, .12, "STARTING FOUR"),
              (2, 5, .12, "STARTING FOUR|SELECT VENUE"),
              (2, 5, .12, "SELECT VENUE|MY RULES"), (2, 5, .12, "MY RULES")],
    "play_steps": [(3, 20, 12.0), (1, 19, 12.0)], "tail": 35, "timeout": 240,
    "scene_marker": "DATA/AUDIO/PBPDAT_E.BIG"}}
# --match: the language Cross is the only menu input; the host's PS2X_BOOT_MATCH hook starts the match once the
# front end has loaded (docs/internals.md "Boot to match"). Intro skip and play steps are shared with `match`.
# The language screen starts reading the pad some time after it first renders, so a press that lands in that window is lost; `repeat_until_gone`
# presses again while a capture taken 2 s or more after the press still shows the guard label.
ROUTES["match-boot"] = dict(ROUTES["match"], steps=[(4, 5, .12, "DEUTSCH")], repeat_until_gone=True, timeout=90, sample_start=3.0, sample_every=2.0, intro_every=2.0)

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
    vu = re.findall(r"\[vu1rec\] \w+: .*?fallback=(\d+) ", text)
    if vu:
        out["vu_fallbacks"] = int(vu[-1])
    game = re.findall(r"\[gs-game\] frames=\d+ over=[0-9.]+s game_fps=([0-9.]+)", text)
    if game:
        out["fps_game_series"] = [float(g) for g in game]  # one value per 5 s window
        out["fps_game"] = max(out["fps_game_series"])  # best window; replaced by the gameplay median once play is confirmed
    native = re.findall(r"\[vu1rec\] \w+: mscal=(\d+) native=(\d+) fallback=(\d+) \(noprogram=(\d+)\) "
                        r"forced=(\d+) mscnt=(\d+) nativeFail=(\d+) \| diff calls=(\d+) "
                        r"mismatch=(\d+) interpBudget=(\d+)", text)
    if native:
        out["vu1"] = dict(zip(("mscal", "native_calls", "interpreter_fallbacks", "no_program",
                               "forced_interpreter", "mscnt", "native_failures", "diff_calls",
                               "mismatch", "interpreter_budget_stops"),
                              map(int, native[-1])))
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


class ProcThreadInfo(ctypes.Structure):
    _fields_ = [("user_time", ctypes.c_uint64), ("system_time", ctypes.c_uint64),
                ("cpu_usage", ctypes.c_int32), ("policy", ctypes.c_int32),
                ("run_state", ctypes.c_int32), ("flags", ctypes.c_int32),
                ("sleep_time", ctypes.c_int32), ("curpri", ctypes.c_int32),
                ("priority", ctypes.c_int32), ("maxpriority", ctypes.c_int32),
                ("name", ctypes.c_char * 64)]


_PROC_PIDLISTTHREADS = 6
_PROC_PIDTHREADINFO = 5
_PROC_PIDTHREADID64INFO = 15
_PROC_PIDLISTTHREADIDS = 28
_PROC_INFO = None
_RUSAGE_INFO = None


def process_rusage(pid):
    """Read macOS RUSAGE_INFO_V4 counters; unavailable platforms return None."""
    global _RUSAGE_INFO
    if sys.platform != "darwin":
        return None
    if _RUSAGE_INFO is None:
        try:
            _RUSAGE_INFO = ctypes.CDLL("/usr/lib/libproc.dylib").proc_pid_rusage
            _RUSAGE_INFO.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
            _RUSAGE_INFO.restype = ctypes.c_int
        except OSError:
            _RUSAGE_INFO = False
    if not _RUSAGE_INFO:
        return None
    # rusage_info_v4: 16-byte UUID followed by 35 uint64 fields. V4 is flavor 4.
    storage = (ctypes.c_uint64 * 37)()
    if _RUSAGE_INFO(pid, 4, ctypes.byref(storage)) != 0:
        return None
    return {"pkg_idle_wakeups": storage[4], "interrupt_wakeups": storage[5],
            "billed_energy": storage[33]}


def rusage_rates(before, after, seconds):
    if before is None or after is None or seconds <= 0:
        return {"energy_per_s": None, "wakeups_per_s": None}
    energy = max(0, after["billed_energy"] - before["billed_energy"])
    wakeups = max(0, after["pkg_idle_wakeups"] - before["pkg_idle_wakeups"])
    wakeups += max(0, after["interrupt_wakeups"] - before["interrupt_wakeups"])
    return {"energy_per_s": round(energy / seconds, 3),
            "wakeups_per_s": round(wakeups / seconds, 3)}


def proc_info():
    """Load libproc lazily so non-macOS runs can still use the probe."""
    global _PROC_INFO
    if _PROC_INFO is None:
        try:
            _PROC_INFO = ctypes.CDLL("/usr/lib/libproc.dylib")
            _PROC_INFO.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                                ctypes.c_void_p, ctypes.c_int]
            _PROC_INFO.proc_pidinfo.restype = ctypes.c_int
        except OSError:
            _PROC_INFO = False
    return _PROC_INFO or None


def read_thread_info(libproc, pid, flavor, thread_id):
    info = ProcThreadInfo()
    result = libproc.proc_pidinfo(pid, flavor, thread_id, ctypes.byref(info), ctypes.sizeof(info))
    return info if result == ctypes.sizeof(info) else None


def list_thread_ids(libproc, pid, flavor):
    capacity = 64
    while capacity <= 4096:
        ids = (ctypes.c_uint64 * capacity)()
        size = libproc.proc_pidinfo(pid, flavor, 0, ctypes.byref(ids), ctypes.sizeof(ids))
        if size <= 0:
            return []
        count = size // ctypes.sizeof(ctypes.c_uint64)
        if count < capacity:
            return list(ids[:count])
        capacity *= 2
    return list(ids[:count])


def thread_cpu_snapshot(pid):
    """Return thread CPU nanoseconds and names, keyed by the kernel thread ID."""
    libproc = proc_info()
    if not libproc:
        return {}
    handles = list_thread_ids(libproc, pid, _PROC_PIDLISTTHREADS)
    handle_threads = {}
    for handle in handles:
        info = read_thread_info(libproc, pid, _PROC_PIDTHREADINFO, handle)
        if info is None:
            continue
        name = info.name.split(b"\0", 1)[0].decode(errors="replace").strip()
        handle_threads[str(handle)] = {"cpu_ns": int(info.user_time) + int(info.system_time), "name": name}

    thread_ids = list_thread_ids(libproc, pid, _PROC_PIDLISTTHREADIDS)
    if thread_ids:
        threads = {}
        for thread_id in thread_ids:
            info = read_thread_info(libproc, pid, _PROC_PIDTHREADID64INFO, thread_id)
            if info is None:
                continue
            name = info.name.split(b"\0", 1)[0].decode(errors="replace").strip()
            threads[str(thread_id)] = {"cpu_ns": int(info.user_time) + int(info.system_time), "name": name}
        if threads:
            return threads
    return handle_threads


def parse_sample(path):
    """Parse sample's per-thread call tree into self counts and thread labels."""
    threads, current, stack = [], None, []
    for line in Path(path).read_text(errors="replace").splitlines() if Path(path).is_file() else []:
        heading = re.match(r"^\s*(\d+)\s+Thread_([^\s:]+):?(?:\s+(.*?))?\s*$", line)
        if heading:
            thread_id = heading[2]
            current = {"id": thread_id, "name": (heading[3] or "").strip() or f"Thread_{thread_id}",
                       "nodes": [], "self": collections.Counter(), "samples": int(heading[1])}
            threads.append(current)
            stack = []
            continue
        if current is None:
            continue
        node = re.match(r"^\s*\+[\s!:+|]*(\d+)\s+(.+)$", line)
        if not node:
            continue
        indent, count, label = node.start(1), int(node[1]), node[2].strip()
        symbol = re.sub(r"\s+\(in .+$", "", label).strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        entry = {"count": count, "symbol": symbol, "children": 0}
        if stack:
            stack[-1][1]["children"] += count
        current["nodes"].append(entry)
        stack.append((indent, entry))
        current["samples"] = max(current["samples"], count)
    total = sum(thread["samples"] for thread in threads)
    for thread in threads:
        for node in thread["nodes"]:
            own = max(0, node["count"] - node["children"])
            if own and node["symbol"]:
                thread["self"][merge_profile_symbol(node["symbol"])] += own
    hot = {}
    guest_top = []
    names = collections.Counter(thread["name"] for thread in threads)
    waits = {"mach_msg_trap", "__workq_kernreturn", "semaphore_wait_trap",
             "__psynch_cvwait", "__semwait_signal"}
    for thread in threads:
        name = thread["name"]
        if names[name] > 1:
            name = f"{name} (Thread_{thread['id']})"
        if re.fullmatch(r"Thread_\d+", name):
            worker_symbol = next((symbol for symbol in thread["self"]
                                  if "vu1rec_" in symbol), None)
            if worker_symbol:
                name = f"worker ({worker_symbol})"
        thread["display_name"] = name
        wait_dominates = any(symbol in waits and count / max(thread["samples"], 1) >= 0.95
                             for symbol, count in thread["self"].items())
        if total and thread["samples"] / total > 0.02 and not wait_dominates:
            hot[name] = [[symbol, round(count * 100 / thread["samples"], 2)]
                                    for symbol, count in thread["self"].most_common(12)]
        if re.search(r"GameThread", thread["name"], re.I):
            guest_top = [[symbol, count] for symbol, count in thread["self"].most_common()
                         if "_0x" in symbol][:5]
    return threads, hot, guest_top


def merge_profile_symbol(symbol):
    """Remove template arguments and parameter lists while retaining the symbol name."""
    symbol = symbol.replace("operator()", "operator__call__")
    while True:
        reduced = re.sub(r"<[^<>]*>|\([^()]*\)", "", symbol)
        if reduced == symbol:
            break
        symbol = reduced
    return re.sub(r"\s+", " ", symbol).strip().replace("operator__call__", "operator()")


def collect_profile(pid, out, window, result):
    """Sample the whole process while measuring per-thread CPU over one wall window."""
    exe = shutil.which("sample")
    start = thread_cpu_snapshot(pid)
    started = time.monotonic()
    sampler = None
    if exe:
        try:
            sampler = subprocess.Popen([exe, str(pid), str(int(window)), "-file", str(out / "process.sample")],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
    time.sleep(window)
    ended = time.monotonic()
    finish = thread_cpu_snapshot(pid)
    if sampler:
        try:
            sampler.wait(timeout=10)
        except subprocess.TimeoutExpired:
            sampler.terminate()
            sampler.wait()
    threads, hot, guest_top = parse_sample(out / "process.sample")
    sample_by_tid = {}
    for thread in threads:
        raw_id = thread["id"]
        try:
            tid = str(int(raw_id, 0))
        except ValueError:
            continue
        # sample lists a thread again under each dispatch queue it briefly ran; keep its main section.
        if tid not in sample_by_tid or thread["samples"] > sample_by_tid[tid]["samples"]:
            sample_by_tid[tid] = thread
    cpus = []
    tids = list(start) + [tid for tid in finish if tid not in start]
    for tid in tids:
        before = start.get(tid, {"cpu_ns": 0, "name": ""})
        after = finish.get(tid, before)
        delta_ns = max(0, after["cpu_ns"] - before["cpu_ns"])
        name = after["name"] or before["name"]
        sample_thread = sample_by_tid.get(tid)
        if sample_thread:
            candidate = next((symbol for symbol, _ in sample_thread["self"].most_common()
                              if "vu1rec_" in symbol), None)
            if candidate:
                name = f"worker ({candidate})"
            elif sample_thread["name"] != f"Thread_{sample_thread['id']}":
                name = name or sample_thread["name"]
        name = name or f"tid {tid}"
        cpus.append({"name": name, "cpu_per_s": round(delta_ns / 1e9 / max(ended - started, 0.001), 4)})
    cpus.sort(key=lambda item: item["cpu_per_s"], reverse=True)
    result.update(profile_threads=cpus, profile_hot=hot, profile_top=guest_top,
                  profile_sample=str(out / "process.sample") if (out / "process.sample").is_file() else None,
                  profile_measurement="per-thread CPU deltas and whole-process sample over the same wall window")


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
        self.labels = []

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
        if self.preserve:
            (frame_dir / "capture.txt").write_text(r.stdout)
        m = re.search(r"luma=([0-9.]+)", r.stdout) if r.returncode == 0 else None
        if m:
            self.frames += 1
            self.black += float(m[1]) < 0.02
        clock = None
        self.labels = []
        for line in r.stdout.splitlines():
            match = re.fullmatch(r"ocr=([0-9.]+),([0-9.]+):([0-9.]+),([0-9.]+):(.*)", line)
            if not match:
                continue
            self.labels.append(match[5].strip().upper())
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
    ap.add_argument("--match", action="store_true",
                    help="boot straight into live match play (PS2X_BOOT_MATCH hook, isolated user dir only); implies --route match-boot")
    args = ap.parse_args()
    if args.quit_at_end and not args.app:
        ap.error("--quit-at-end needs --app")
    if args.runner and args.app:
        ap.error("--runner and --app are mutually exclusive")
    if args.save and args.user_dir:
        ap.error("--save and --user-dir cannot be combined")
    if args.match:
        if args.route and args.route != "match-boot":
            ap.error("--match implies --route match-boot")
        if args.app:
            ap.error("--match is a diagnostic of the checkout runner, not the installed app")
        if args.user_dir and args.user_dir.resolve() == REAL_SUPPORT.resolve():
            ap.error("--match needs an isolated user dir, never the real save directory")
        args.route = "match-boot"
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
        user_dir = (args.user_dir if args.user_dir.is_absolute() else ROOT / "build/user-dirs" / args.user_dir).resolve()
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
    if args.match:
        env["PS2X_BOOT_MATCH"] = "venue=3"
    else:
        env.pop("PS2X_BOOT_MATCH", None)
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
    proc = subprocess.Popen(command, cwd=user_dir, env=env, stdout=logf, stderr=subprocess.STDOUT,
                            preexec_fn=os.setpgrp)
    rusage_start = process_rusage(proc.pid)
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
    repeat = None
    play_steps, play_step_at = list(route.get("play_steps", [])) if route else [], None
    if route:
        step_at = started + steps[0][0]
    killed_by_us = fault_stop = False
    state_asked = False
    state_requests = []
    gameplay_at = None
    profiled = None
    profile_thread = None
    profile_result = {}
    gameplay_input_key = None
    route_inputs = []
    intro_skip_at = None
    next_black = started + ((route or {}).get("sample_start", BLACK_SAMPLE_START))
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
                guard_ok = False
                if steps:
                    guard = steps[0][3]
                    guard_ok = (guard[4:] in text_now if guard.startswith("log:") else
                                bool(re.search(guard, "\n".join(cap.labels))))
                if fifo_fd is not None and steps and now >= step_at and not release_at and guard_ok:
                    step = steps.pop(0)
                    _, key = step[:2]
                    hold = step[2]
                    route_inputs.append({"wall_seconds": round(now - started, 3), "key_bit": key, "guard": step[3]})
                    os.write(fifo_fd, struct.pack("<I", 1 << key))
                    release_at = now + hold
                    if route.get("repeat_until_gone"):
                        repeat = (step[3], key, hold, now, cap.samples)
                    if steps:
                        step_at = now + steps[0][0]
                elif (fifo_fd is not None and repeat and not steps and not release_at and cap.samples > repeat[4] and
                      now - repeat[3] >= 2.0 and re.search(repeat[0], "\n".join(cap.labels))):
                    route_inputs.append({"wall_seconds": round(now - started, 3), "key_bit": repeat[1], "guard": repeat[0] + " (still shown 2 s after the press)"})
                    os.write(fifo_fd, struct.pack("<I", 1 << repeat[1]))
                    release_at = now + repeat[2]
                    repeat = (repeat[0], repeat[1], repeat[2], now, cap.samples)
                elif (fifo_fd is not None and not steps and not release_at and cap.match_clock is None and
                      route["scene_marker"] in text_now and
                      re.search(r"MAR[5S]E[I1].*L", "\n".join(cap.labels)) and
                      (intro_skip_at is None or now >= intro_skip_at + route.get("intro_every", 5))):
                    os.write(fifo_fd, struct.pack("<I", 1 << 5))
                    release_at = now + 1.0
                    intro_skip_at = now
                    route_inputs.append({"wall_seconds": round(now - started, 3), "key_bit": 5,
                                         "guard": "match intro MARSEILLE + commentary data open"})
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
                profiled = True
                deadline = max(deadline, now + 17.0)
                if route:
                    route_deadline = max(route_deadline, deadline)
                profile_thread = threading.Thread(target=collect_profile,
                                                  args=(proc.pid, out, 15.0, profile_result), daemon=True)
                profile_thread.start()
            if not state_asked and args.seconds >= 8 and now >= deadline - 3.5:
                state_asked = request_state()
            if now >= next_black:
                cap.sample(proc.pid)
                if route and play_steps and play_step_at is None and cap.gameplay_confirmed:
                    play_step_at = time.monotonic() + play_steps[0][0]
                next_black = time.monotonic() + (route or {}).get("sample_every", BLACK_SAMPLE_EVERY)
            time.sleep(0.04 if route else 0.2)
    finally:
        if profile_thread:
            profile_thread.join()
        rusage_end = process_rusage(proc.pid)
        rusage_duration = time.monotonic() - started
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
    fps_game = metrics.get("fps_game")
    series = metrics.get("fps_game_series") or []
    if gameplay_at is not None:
        # Windows fully after confirmed play; goal replays draw elsewhere and read low, so take the median.
        play = series[int((gameplay_at - started) // 5) + 1:]
        if play:
            fps_game = sorted(play)[len(play) // 2]
    summary = {
        "reached": reached, "target": target, "seconds": elapsed,
        "fps_game": fps_game,
        "fps_game_series": metrics.get("fps_game_series"),
        "gpu_presents_per_sec": metrics.get("gpu_presents_per_sec"),
        "cpu_user_s": cpu_user_s, "cpu_sys_s": cpu_sys_s,
        **rusage_rates(rusage_start, rusage_end, rusage_duration),
        "vu_fallbacks": metrics.get("vu_fallbacks"), "audio_underruns": metrics.get("audio_underruns"),
        "vu1": metrics.get("vu1"),
        "crash": crash, "stubs": metrics["stubs"], "faults": metrics["faults"],
        "black_frames": f"{cap.black}/{cap.frames}" if cap.frames else None,
        "save_dir_changed": changed, "exit_code": proc.returncode,
        "profile_top": profile_result.get("profile_top"),
        "profile_threads": profile_result.get("profile_threads", []),
        "profile_hot": profile_result.get("profile_hot", {}),
        "profile_sample": profile_result.get("profile_sample"),
        "profile_measurement": profile_result.get("profile_measurement"),
        "route_inputs": route_inputs if route else None,
        "route_waiting_for": steps[0][3] if route and steps else None,
        "gameplay_wall_seconds": round(gameplay_at - started, 3) if gameplay_at is not None else None,
        "time_to_play_s": cap.clock_observations[0]["wall_seconds"] if route and cap.clock_observations else None,
        "boot_match": re.findall(r"\[boot-match\] .*", text)[:4] if args.match else None,
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
    vu1 = metrics.get("vu1", {})
    vu_ok = not any(vu1.get(key, 0) for key in ("mismatch", "interpreter_budget_stops"))
    if env.get("PS2X_VU1_DIFF", "0") not in ("", "0"):
        vu_ok = vu_ok and vu1.get("diff_calls", 0) > 0
    return 0 if reached == target and scene_ok and vu_ok and not changed and not summary["audio_underruns"] and quit_ok is not False else 1


if __name__ == "__main__":
    sys.exit(main())
