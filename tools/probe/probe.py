#!/usr/bin/env python3
"""Bounded native run with isolated saves; writes summary.json and run.log into --out.

Merges the former run-native-check / diagnose-native / probe-first-level scripts.
  --route first-mission  scripted new-game route through process-local diagnostic input
                         (OCR guard on the language menu before the first key)
  --route missions       needs unlockAllMissions (written into its own isolated user dirs, this run only): new game, then every
                         mission on the Story Mode map in order: select, wait for gameplay (OCR guards), ~45 s scripted play,
                         quit to the map through the pause menu. Per-mission table in summary.json ("missions").
  --save DIR             seed a fresh isolated user dir from DIR/memcards
  --profile              macOS `sample` of the GameThread after --profile-after seconds
  --gpu-capture          bounded Metal frame capture (TARZAN_CAPTURE_*)
  --app BUNDLE           exercise an installed .app (the app binary is the game itself; same isolated user dir and diagnostics)
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
import plistlib
import re
import shutil
import select
import signal
import struct
import subprocess
import sys
import tempfile
import time
from difflib import SequenceMatcher
from string import capwords
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
PAD = {"up": 1, "down": 2, "left": 3, "right": 4, "cross": 5, "circle": 7, "square": 9, "triangle": 11, "start": 17}
PLAY_SECONDS = 45
PERIODIC_RE = re.compile(r"\[vu1rec\] periodic: mscal=\d+ native=\d+ fallback=(\d+) .*?nativeFail=(\d+) .*?\(DISPFB2 ([0-9.]+)\).*? over ([0-9.]+)s")
MOVIE_RE = re.compile(r'\[sceCdSearchFile:ok\] path="[^"]*?([^\\"]+\.PSS)|sceCdSearchFile failed: [^\n]*?([^\\\n]+\.PSS)|'
                      r"\[iop-audio-rpc\] fn=11 [^\n]*?\binit=0\b")
VU_FALLBACK_RE = re.compile(r"\[vu1rec\] VU1 image ([0-9a-f]{16})(?: entry (0x[0-9a-f]+))? has no native code|"
                            r"\[vu1rec\] native program failed[^\n]*?image=([0-9a-f]{16}) entry=(0x[0-9a-f]+)")
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
    starts (`iop-audio-rpc fn=12` following a .PSS open); the health line containing or following an
    `iop-audio-rpc fn=11 ... init=0` (movie EOF flush); and, by signature, any silent interval (rms=0: the burst is
    stream start/flush/load padding and cannot be an audible glitch), however late it lands under load."""
    steady = boundary = overruns = 0
    seen = False
    pss_open = after_start = False
    after_flush = False
    for line in text.splitlines():
        if re.search(r"\[sceCdSearchFile:ok\] path=\"[^\"]*\.PSS", line):
            pss_open = True
        elif pss_open and re.search(r"\[iop-audio-rpc\] fn=12 ", line):
            pss_open, after_start = False, True
        elif re.search(r"\[iop-audio-rpc\] fn=11 .*\binit=0\b", line):
            after_flush = True
        else:
            m = re.search(r"\[tarzan-spu2-health\].*?underrun=(\d+) overrun=(\d+).*?rms=([0-9.]+)", line)
            if m:
                seen = True
                overruns += int(m[2])
                silent = float(m[3]) == 0.0
                if after_start or silent or after_flush:
                    boundary += int(m[1])
                else:
                    steady += int(m[1])
                after_start = False
                after_flush = False
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


def write_run_settings(user_dir, *, skip_logos=False, unlock_missions=False):
    path = Path(user_dir) / "experimental-settings.plist"
    try:
        settings = plistlib.loads(path.read_bytes()) if path.exists() else {}
    except (plistlib.InvalidFileException, OSError):
        settings = {}
    if skip_logos is not None:
        settings["skipLogos"] = bool(skip_logos)
    if unlock_missions:
        settings["unlockAllMissions"] = True
    path.write_bytes(plistlib.dumps(settings))


def gameplay_start(samples, target=25):
    return next((samples[i][0] for i in range(len(samples) - 1)
                 if samples[i + 1][0] - samples[i][0] <= 10
                 and abs(samples[i + 1][1] - samples[i][1]) <= target * 0.25), None)


def gameplay_fps(samples, target=25, window_seconds=60):
    """Average periodic DISPFB2 samples from a stable, movie-free window of the confirmed playable scene."""
    start = gameplay_start(samples, target)
    if start is None:
        return None
    window = [fps for t, fps in samples if start <= t <= start + window_seconds]
    return round(sum(window) / len(window), 1) if window else None


def scene_check(cap, pid, press):
    """One OCR frame of the live window: tutorial-card (pressing cross to dismiss it), playable or unknown."""
    lines = cap.grab(pid)
    if lines is None:
        return "unknown"
    good = [l for l in lines if l.get("confidence", 0) >= 0.45 and "native" not in l.get("text", "").casefold()]
    kind = screen_kind([l["text"].strip().casefold() for l in good], good)
    if kind == "popup":
        press()
        return "tutorial-card"
    return "playable" if kind == "other" else "unknown"


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


class Game:
    """One game process with a diagnostic input FIFO, an incrementally parsed log and window OCR."""

    def __init__(self, out, tag, user_dir, capture_dir):
        self.out, self.user_dir = out, user_dir
        self.log_path = out / f"run-{tag}.log"
        fifo = out / f"input-{tag}.fifo"
        os.mkfifo(fifo, 0o600)
        self.fifo_fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
        env = os.environ.copy()
        env.setdefault("TARZAN_TRACE_FAULTS", "1")
        env.setdefault("TARZAN_TRACE_SPU2", "1")
        env["TARZAN_DIAGNOSTIC_INPUT_FIFO"] = str(fifo)
        env["PS2X_VU1_CAPTURE"] = str(capture_dir)
        env["PS2X_VU1_CAPTURE_EVERY"] = "1000000"  # first 8 MSCALs per (program, entry) only
        runner = DEFAULT_RUNNER.resolve(strict=True)
        self.logf = self.log_path.open("wb")
        self.started = time.monotonic()
        self.proc = subprocess.Popen([str(runner), "--user-dir", str(user_dir), str(ELF)], cwd=ROOT, env=env,
                                     stdout=self.logf, stderr=subprocess.STDOUT)
        self.exit_watch = select.kqueue()
        self.exit_watch.control([select.kevent(self.proc.pid, filter=select.KQ_FILTER_PROC,
                                               flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE,
                                               fflags=select.KQ_NOTE_EXIT)], 0, 0)
        self.cap = Capture(out)
        self.pos, self.tail = 0, ""
        self.periodic = []  # (monotonic, log position, fallback, nativeFail, fps)
        self.movies = []  # dicts: name, opened/missing, seconds, ended
        self.killed = False
        self.cpu_user_s = self.cpu_sys_s = 0.0
        self.returncode = None

    def alive(self):
        return not self.exit_watch.control(None, 0, 0)

    def poll(self):
        """Parse log text appended since the last call."""
        with self.log_path.open("rb") as f:
            f.seek(self.pos)
            chunk = f.read().decode(errors="replace")
        if not chunk:
            return
        now, old = time.monotonic(), len(self.tail)
        text = self.tail + chunk
        for m in PERIODIC_RE.finditer(text):
            if m.end() > old:
                self.periodic.append((now, self.pos, int(m[1]), int(m[2]), float(m[3])))
        for m in MOVIE_RE.finditer(text):
            if m.end() <= old:
                continue
            if m[1] or m[2]:
                self.movies.append({"name": m[1] or m[2], "opened": bool(m[1]), "t0": now, "ended": None, "pos": self.pos})
            elif self.movies and self.movies[-1]["opened"] and self.movies[-1]["ended"] is None:
                self.movies[-1]["ended"] = now
        self.pos += len(chunk.encode())
        self.tail = text[-300:]

    def text(self, a=0, b=None):
        with self.log_path.open("rb") as f:
            f.seek(a)
            return (f.read() if b is None else f.read(max(0, b - a))).decode(errors="replace")

    def mask(self, bits):
        os.write(self.fifo_fd, struct.pack("<I", sum(1 << PAD[b] for b in bits)))

    def press(self, *names, hold=0.15):
        self.mask(names)
        time.sleep(hold)
        self.mask(())

    def cpu_user(self):
        r = subprocess.run(["ps", "-o", "utime=", "-p", str(self.proc.pid)], capture_output=True, text=True)
        m = re.fullmatch(r"\s*(\d+):([0-9.]+)\s*", r.stdout)
        return int(m[1]) * 60 + float(m[2]) if m else None

    def screen(self):
        """(texts casefolded, raw lines) of the live window at confidence >= 0.45, or ([], None) when capture failed."""
        lines = self.cap.grab(self.proc.pid)
        if lines is None:
            return [], None
        good = [l for l in lines if l.get("confidence", 0) >= 0.45 and "native" not in l.get("text", "").casefold()]
        return [l["text"].strip().casefold() for l in good], good

    def stop(self):
        if self.alive():
            self.killed = True
            self.proc.send_signal(signal.SIGTERM)  # the GS dependency can swallow SIGTERM: escalate
            end = time.monotonic() + 2
            while time.monotonic() < end and self.alive():
                time.sleep(0.01)
            if self.alive():
                self.proc.kill()
        _, status, usage = os.wait4(self.proc.pid, 0)
        self.returncode = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -os.WTERMSIG(status)
        self.cpu_user_s, self.cpu_sys_s = round(usage.ru_utime, 3), round(usage.ru_stime, 3)
        _EXITED_WATCHES.discard(self.exit_watch.fileno())
        self.exit_watch.close()
        self.logf.close()
        os.close(self.fifo_fd)
        self.poll()


def screen_kind(texts, lines):
    """Classify an OCR frame: map, mode, pause, confirm, popup or other."""
    has = lambda t: t in texts
    if map_mission(lines):
        return "map"
    if has("story mode") and any("challenges" in t for t in texts):
        return "mode"
    if has("yes") and has("no"):
        return "confirm"
    if has("quit level") or (has("continue") and any(t.endswith("go back") for t in texts)):
        return "pause"
    if has("hall of fame"):
        return "options"
    if has("continue"):
        return "popup"
    if any(t.startswith("select") for t in texts):
        return "maplike" if any("challenges" in t for t in texts) else "briefing"
    return "other"


def map_mission(lines):
    """(name, type, confidence) of the highlighted map entry: the two lines just below the Story Mode title (the title
    sits at the top of the map in the first world and at the bottom in later ones)."""
    story = next((l for l in lines or [] if l["text"].strip().casefold() == "story mode"), None)
    if not story:
        return None
    sy = story["bbox"]["y"]
    names = sorted((l for l in lines if sy - 0.2 < l["bbox"]["y"] < sy - 0.005 and re.search(r"[a-z]{4}", l["text"].casefold())
                    and not re.search(r"challenges|\bback\b|select|^terk\W*$", l["text"].casefold())), key=lambda l: -l["bbox"]["y"])
    if not names:
        return None
    pair = names[:2]
    return (capwords(pair[0]["text"].strip()), capwords(pair[1]["text"].strip()) if len(pair) > 1 else "",
            sum(l["confidence"] for l in pair) / len(pair))


def read_map(game):
    """Best of two OCR frames for the highlighted map entry, or None."""
    best = None
    for i in range(2):
        cur = map_mission(game.screen()[1])
        best = cur if cur and (not best or cur[2] > best[2]) else best
        time.sleep(1.0)
    return best


def wait_screen(game, kinds, timeout, want=None, confirm=2):
    """Poll OCR every ~1 s until the frame is one of `kinds` (and want(texts) holds) on `confirm` consecutive frames."""
    end, hits = time.monotonic() + timeout, 0
    while time.monotonic() < end:
        game.poll()
        if not game.alive():
            return None
        texts, lines = game.screen()
        kind = screen_kind(texts, lines)
        hits = hits + 1 if lines is not None and kind in kinds and (want is None or want(texts)) else 0
        if hits >= confirm:
            return kind, texts, lines
        time.sleep(1.0)
    return None


def boot_to_map(game, deadline):
    """Language menu to the Story Mode map with a new game (three-character name), each step guarded by OCR."""
    steps = [
        ("language", lambda t: "language" in t and "english" in t, "cross"),
        ("title", lambda t: any("press start" in x for x in t), "start"),
        ("main menu", lambda t: "main menu" in t, "cross"),
        ("memory card", lambda t: any("memory card" in x for x in t), "cross"),
        ("file select", lambda t: "game file select" in t, "cross"),
        ("name entry", lambda t: "new game" in t and any("tuv" in x or "mno" in x for x in t), None),
    ]
    for name, pred, key in steps:
        if not wait_screen(game, ("other", "briefing"), max(5, min(70, deadline - time.monotonic())), pred):
            return f"boot stopped before the {name} screen"
        if key:
            game.press(key)
        else:
            for _ in range(4):  # three characters, then END
                game.press("cross")
                time.sleep(1.5)
    if not wait_screen(game, ("mode",), 30):
        return "no Story Mode / Terk's Challenges screen after the name"
    game.press("cross")
    if not wait_screen(game, ("map",), 40):
        return "no Story Mode map"
    return None


def same_name(a, b):
    return SequenceMatcher(None, a.casefold(), b.casefold()).ratio() > 0.8


def mission_slice_stats(game, t0, t1, pos0, pos1):
    ps = [p for p in game.periodic if t0 <= p[0] <= t1]
    base = next((p for p in reversed(game.periodic) if p[0] < t0), None)
    text = game.text(pos0, pos1)
    out = parse_log(text)
    return {"fallbacks": (ps[-1][2] - (base[2] if base else 0)) if ps else None,
            "native_fail": (ps[-1][3] - (base[3] if base else 0)) if ps else None,
            "vu1_programs": sorted({f"{m[0] or m[2]}@{m[1] or m[3] or 'image'}" for m in VU_FALLBACK_RE.finditer(text)}),
            "stubs": out["stubs"], "faults": out["faults"]}


def play_mission(game, rec, deadline):
    """Select the highlighted mission and play it; returns when back on the map or when the game must be restarted."""
    t_sel, pos_sel = time.monotonic(), game.pos
    nmovies = len(game.movies)
    game.press("cross")
    popups = quiet = 0
    reached = healthy = False
    result = card = map_since = None
    while not reached and result is None:
        game.poll()
        now = time.monotonic()
        movie = game.movies[-1] if len(game.movies) > nmovies else None
        in_movie = bool(movie and movie["opened"] and movie["ended"] is None)
        if not game.alive():
            result = "crash"
        elif now > deadline:
            result = "stopped at the --seconds limit"
        elif now - t_sel > 300 or (in_movie and now - movie["t0"] > 150):
            result = "no gameplay within 300 s" if not in_movie else "movie did not end within 150 s"
        elif not in_movie and game.periodic and now - game.periodic[-1][0] > 60:
            result = "hang (no VU1 stats for 60 s)"
        elif in_movie:
            time.sleep(1.0)
        else:
            texts, lines = game.screen()
            kind = screen_kind(texts, lines)
            if lines is None:
                time.sleep(1.0)
            elif kind in ("popup", "briefing"):
                game.press("cross")  # tutorial card, or a character card shown over the map
                popups += 1
                quiet = 0
                map_since = None
                if kind == "briefing":
                    card = " ".join(t for t in texts if not t.startswith("select"))
            elif kind in ("map", "maplike"):
                quiet = 0
                map_since = map_since or now
                if popups == 0 and now - t_sel > 60:
                    result = "mission did not start from the map"
                elif card and now - map_since > 15:  # the card sent us back to the map: a gate the unlock override does not cover
                    result = f'locked by a requirement card: "{card}"'
                    healthy = True
            elif kind == "options":
                game.press("triangle")
            elif kind == "other":
                map_since = None
                quiet += 1
                steady = len(game.periodic) >= 2 and all(23 <= p[4] <= 27 for p in game.periodic[-2:]) \
                    and now - game.periodic[-1][0] < 8  # loading and transitions present at 50 fields/s
                reached = (popups > 0 and quiet >= 2) or (quiet >= 6 and steady)
            time.sleep(1.0)
    rec["reached"] = reached
    if result is None:
        t_play, p_play, cpu_play = time.monotonic(), game.pos, game.cpu_user()
        tick = 0
        while time.monotonic() - t_play < PLAY_SECONDS and game.alive():
            t = time.monotonic() - t_play
            bits = {"up"}
            if tick % 12 < 2: bits.add("cross")
            if 5 <= tick % 20 < 7: bits.add("square")
            if 10 <= tick % 35 < 12: bits.add("circle")
            if int(t // 6) % 4 == 1: bits.add("left")
            if int(t // 6) % 4 == 3: bits.add("right")
            game.mask(bits)
            tick += 1
            time.sleep(0.1)
        game.mask(())
        game.poll()
        t_end = time.monotonic()
        rec["play_seconds"] = round(t_end - t_play, 1)
        fps = [p[4] for p in game.periodic if t_play + 5.5 <= p[0] <= t_end]
        rec["fps_gameplay"] = round(sum(fps) / len(fps), 1) if fps else None
        rec["fps_dispfb2"] = round(sum(fps) / len(fps), 1) if fps else None
        rec["fps_min"] = round(min(fps), 1) if fps else None
        cpu_end = game.cpu_user()
        rec["cpu_user_s"] = round(cpu_end - cpu_play, 1) if cpu_play is not None and cpu_end is not None else None
        rec["underruns"] = parse_log(game.text(p_play, game.pos)).get("audio_underruns")
        if not game.alive():
            result = "crash"
        else:
            result = return_to_map(game, deadline, rec)
    rec["result"] = result or "ok"
    if result:
        texts, _ = game.screen()
        rec["last_screen"] = texts[:12]
        shutil.copy2(game.out / "frames/window-000.png", game.out / f"fail-{rec['index'] + 1:02d}.png")
    rec["seconds"] = round(time.monotonic() - t_sel, 1)
    game.poll()
    rec.update(mission_slice_stats(game, t_sel, time.monotonic(), pos_sel, game.pos))
    rec["movies"] = [{"name": m["name"], "played": m["opened"],
                      "to_end": bool(m["ended"]) if m["opened"] else None,
                      "seconds": round(m["ended"] - m["t0"], 1) if m["ended"] else None} for m in game.movies[nmovies:]]
    return result is not None and not healthy


def return_to_map(game, deadline, rec):
    """Pause menu > Quit Level > Yes, handling stray popups; None on success, else a failure description."""
    end = min(deadline, time.monotonic() + 90)
    quit_sent = None
    last_start = 0.0
    while time.monotonic() < end:
        game.poll()
        if not game.alive():
            return "crash"
        texts, lines = game.screen()
        kind = screen_kind(texts, lines)
        now = time.monotonic()
        if kind == "map":
            if not quit_sent:
                rec["note"] = "the level ended by itself before Quit Level"
            return None
        if kind == "pause" and not quit_sent:
            game.press("down")
            time.sleep(0.5)
            game.press("cross")
        elif kind == "confirm":
            game.press("up")
            time.sleep(0.5)
            game.press("cross")
            quit_sent = now
        elif kind == "popup":
            game.press("cross")
        elif kind == "options":
            game.press("triangle")
        elif kind == "other" and not quit_sent and now - last_start > 10:
            game.press("start")
            last_start = now
        elif kind == "maplike" and now - last_start > 10:
            game.press("circle")  # the Terk's Challenges map (a stray Circle); Circle switches back to Story Mode
            last_start = now
            last_start = now
        time.sleep(1.0)
    return "could not quit to the map (pause menu not reached)" if not quit_sent else "map did not return after Quit Level"


def run_missions(args):
    seconds = args.seconds or 3600
    out = (args.out or ROOT / "build/runs" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())).resolve()
    out.mkdir(parents=True, exist_ok=True)
    capture = out / "vu1-snapshots"
    capture.mkdir(exist_ok=True)
    if not ELF.is_file():
        print("extracted/disc/SLES_503.50 missing -> ./tz disc <iso>", file=sys.stderr)
        return 2
    before = save_hash(REAL_SUPPORT)
    started = time.monotonic()
    deadline = started + seconds
    missions, notes, games = [], [], []
    names, idx, launches, done = [], 0, 0, False
    while not done and time.monotonic() < deadline:
        launches += 1
        user_dir = out / f"user-dir-{launches}"
        user_dir.mkdir()
        write_run_settings(user_dir, skip_logos=True, unlock_missions=True)
        game = Game(out, f"{launches}", user_dir, capture)
        games.append(game)
        try:
            err = boot_to_map(game, deadline)
            for _ in range(max(0, idx - 1) if not err else 0):  # after a restart: walk back to the last played entry
                game.press("right")
                time.sleep(2.5)
            if err:
                notes.append(f"game {launches}: {err}")
                missions.append({"index": idx, "name": "?", "result": err, "reached": False})
                break
            restart = False
            while not restart and time.monotonic() < deadline:
                if idx > 0:
                    here = read_map(game)  # a level that ends by itself can leave the cursor on another entry
                    at = next((i for i, n in enumerate(names) if here and same_name(n, here[0])), None)
                    for _ in range(abs(idx - 1 - at) if at is not None else 0):
                        game.press("right" if at < idx - 1 else "left")
                        time.sleep(2.5)
                    game.press("right")
                    time.sleep(2.0)
                cur = read_map(game)
                if cur is None and wait_screen(game, ("map",), 20):
                    cur = read_map(game)
                if cur is None:
                    missions.append({"index": idx, "name": "?", "result": "map name not readable", "reached": False})
                    done = True
                elif idx > 0 and same_name(cur[0], names[-1]):
                    done = True  # Right no longer advances: end of the map
                elif idx >= 24:
                    notes.append("stopped at 24 missions")
                    done = True
                if done:
                    break
                names.append(cur[0])
                rec = {"index": idx, "name": cur[0], "type": cur[1]}
                missions.append(rec)
                restart = play_mission(game, rec, deadline)
                rec["restarted"] = restart
                idx += 1
        finally:
            game.stop()
    missing_movies = sorted({m["name"] for g in games for m in g.movies if not m["opened"]})
    for g in games:
        g.poll()
    crash = None
    for g in games:
        c = crash_from(g.text(), g.returncode, g.killed)
        crash = crash or c
    # A mission behind a requirement card the unlock override does not cover is a recorded finding, not a failure.
    all_ok = bool(missions) and done and all(m.get("result") == "ok" or str(m.get("result")).startswith("locked") for m in missions)
    summary = {
        "reached": "missions" if all_ok else ("crash" if crash else "missions-incomplete"), "target": "missions",
        "seconds": round(time.monotonic() - started, 1),
        "fps_gameplay": min((m["fps_gameplay"] for m in missions if m.get("fps_gameplay")), default=None),
        "fps_dispfb2": min((m["fps_dispfb2"] for m in missions if m.get("fps_dispfb2")), default=None),
        "cpu_user_s": round(sum(g.cpu_user_s for g in games), 3), "cpu_sys_s": round(sum(g.cpu_sys_s for g in games), 3),
        "vu_fallbacks": sum(m.get("fallbacks") or 0 for m in missions),
        "audio_underruns": None, "audio_boundary_underruns": None, "audio_overruns": None, "vu_native_fail": sum(m.get("native_fail") or 0 for m in missions),
        "crash": crash, "stubs": [], "faults": [f for m in missions for f in m.get("faults", [])][:10],
        "black_frames": None, "save_dir_changed": save_hash(REAL_SUPPORT) != before, "exit_code": 0,
        "profile_top": None, "menu_confirmed_s": None, "keys": [], "notes": notes, "crash_report": None,
        "log": str(games[0].log_path), "user_dir": str(out), "quit_clean": None,
        "missions": missions, "locked": [m["name"] for m in missions if str(m.get("result")).startswith("locked")],
        "missing_movies": missing_movies, "vu1_snapshots": str(capture),
        "snapshot_files": len(list(capture.glob("snap_*.bin"))), "games_launched": launches,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return 0 if all_ok and not summary["save_dir_changed"] else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, help="run directory (default build/runs/<UTC>)")
    ap.add_argument("--seconds", type=int)
    ap.add_argument("--no-skip-logos", action="store_true", help="control run for measuring the isolated logo-skip boot saving")
    ap.add_argument("--fps-target", type=int, choices=(60, 120), help="opt into a high-FPS target in this isolated run")
    ap.add_argument("--measure", action="store_true", help="collect a 60-second steady gameplay window")
    ap.add_argument("--route", choices=("none", "first-mission", "missions"), default="none")
    ap.add_argument("--user-dir", type=Path)
    ap.add_argument("--save", type=Path, help="fixture directory containing a non-empty memcards/ subtree")
    ap.add_argument("--runner", type=Path)
    ap.add_argument("--app", type=Path)
    ap.add_argument("--quit-at-end", action="store_true",
                    help="with --app: end the run with an AppleEvent quit (like Dock/Cmd-Q) and require exit 0 within 30 s")
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
    if args.fps_target:
        os.environ["TARZAN_EXPERIMENT_FPS"] = str(args.fps_target)
    if args.route == "missions":
        if args.app or args.save or args.user_dir or args.runner or args.desktop_input:
            ap.error("--route missions uses its own isolated user dirs and the build runner; no --app/--save/--user-dir/--runner/--desktop-input")
        return run_missions(args)
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
    seconds = args.seconds or ((240 if args.measure else 180) if args.route == "first-mission" else 60)
    if args.route == "first-mission" and seconds < 140:
        ap.error("--route first-mission needs --seconds >= 140")
    if not 1 <= args.capture_frames <= 120 or args.capture_interval < 1 or args.capture_start_field < 0:
        ap.error("invalid capture settings")

    out = (args.out or ROOT / "build/runs" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())).resolve()
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "run.log"
    scratch = None
    if args.save:
        scratch = user_dir = Path(tempfile.mkdtemp(prefix="tz-run-"))
        shutil.copytree(fixture_cards, user_dir / "memcards")
        card_files = sum(1 for p in (user_dir / "memcards").rglob("*") if p.is_file())
    elif args.user_dir:
        user_dir = args.user_dir.resolve()
        user_dir.mkdir(parents=True, exist_ok=True)
    elif args.route == "first-mission":
        # Keep route saves with the run output so they can seed local regression fixtures.
        user_dir = out / "user-dir"
        user_dir.mkdir(parents=True, exist_ok=True)
    else:
        scratch = user_dir = Path(tempfile.mkdtemp(prefix="tz-run-"))
    # A settings file may be pre-seeded; anything else means saved games exist.
    if args.route != "none" and any(p.name != "experimental-settings.plist" for p in user_dir.iterdir()):
        ap.error("--route first-mission needs a --user-dir without saves (new game); only experimental-settings.plist is allowed")
    explicit_real = bool(args.user_dir) and user_dir == REAL_SUPPORT.resolve()
    if not args.user_dir or not explicit_real:
        write_run_settings(user_dir, skip_logos=(args.route == "none" and not args.no_skip_logos))
    elif args.fps_target:
        ap.error("--fps-target requires an isolated user directory")
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

    if args.app:
        app = args.app.resolve(strict=True)
        command = [str(app / "Contents/MacOS/TarzanFreeridePS2"), "--user-dir", str(user_dir)]
    else:
        runner = (args.runner or DEFAULT_RUNNER).resolve(strict=True)
        if not ELF.is_file():
            print("extracted/disc/SLES_503.50 missing -> ./tz disc <iso>", file=sys.stderr)
            return 2
        command = [str(runner), "--user-dir", str(user_dir), str(ELF)]

    notes, keys = [], []
    started = time.monotonic()
    logf = log_path.open("wb")
    if args.save:
        logf.write(f"[probe] seeded {card_files} memory-card files from fixture into {user_dir / 'memcards'}\n".encode())
        logf.flush()
    proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT)
    exit_watch = select.kqueue()
    exit_watch.control([select.kevent(proc.pid, filter=select.KQ_FILTER_PROC,
                                      flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE,
                                      fflags=select.KQ_NOTE_EXIT)], 0, 0)
    cap = Capture(out)

    def log_text():
        return log_path.read_text(errors="replace")

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
    scan_pos, active_movie, gameplay_samples = 0, False, []
    scene, scene_seen, scene_ok_at, scene_ok, next_scene = None, None, None, 0, 0.0
    try:
        while time.monotonic() < deadline and not child_exited(exit_watch):
            now = time.monotonic() - started
            text_now = log_text()
            new_text = text_now[scan_pos:]
            scan_pos = len(text_now)
            for line in new_text.splitlines():
                if re.search(r'\[sceCdSearchFile:ok\] path="[^"]*\.PSS', line):
                    active_movie = True
                elif re.search(r"\[iop-audio-rpc\] fn=11 .*\binit=0\b", line):
                    active_movie = False
                pm = PERIODIC_RE.search(line)
                if pm and not active_movie and float(pm[4]) <= 10 and (not route or route_i == len(route)):
                    t = time.monotonic() - started
                    if route and scene_ok_at is None and scene_seen is None:
                        scene_seen = t  # first movie-free sample after the route: start checking the scene
                    elif not route or (scene_ok_at is not None and t - float(pm[4]) >= scene_ok_at):
                        gameplay_samples.append((t, float(pm[3])))
            if route and scene_seen is not None and scene_ok_at is None and now >= next_scene:
                scene = scene_check(cap, proc.pid, lambda: send_key(7))
                next_scene = now + 1.0
                if scene == "playable":
                    scene_ok += 1
                    if scene_ok == 2:
                        scene_ok_at = now
                else:
                    scene_ok = 0
                if scene_ok_at is None and now - scene_seen > 60:
                    scene_ok_at = now  # report the scene that blocked confirmation and measure anyway
            window_seconds = 60 if args.measure else 20
            steady_start = gameplay_start(gameplay_samples, args.fps_target or 25)
            if route and steady_start is not None and now >= steady_start + window_seconds:
                break
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
        if route and route_i == len(route) and not child_exited(exit_watch) and not fault_stop:
            cap.grab(proc.pid)
    finally:
        quit_ok = None
        if args.quit_at_end and not child_exited(exit_watch):
            subprocess.run(["osascript", "-e", 'tell application id "org.rrv.tarzan-freeride-ps2" to quit'],
                           capture_output=True, timeout=30)  # the app cancels terminate and shuts down itself
            try:
                end = time.monotonic() + 30
                while time.monotonic() < end and not child_exited(exit_watch):
                    time.sleep(0.01)
                quit_ok = child_exited(exit_watch)
                if not quit_ok:
                    notes.append("quit did not exit within 30 s")
            except subprocess.TimeoutExpired:
                quit_ok = False
                notes.append("quit did not exit within 30 s")
        if not child_exited(exit_watch):
            killed_by_us = True
            proc.send_signal(signal.SIGTERM)  # the GS dependency can swallow SIGTERM: escalate
            end = time.monotonic() + 2
            while time.monotonic() < end and not child_exited(exit_watch):
                time.sleep(0.01)
            if not child_exited(exit_watch):
                proc.kill()
        _, status, usage = os.wait4(proc.pid, 0)
        proc.returncode = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -os.WTERMSIG(status)
        if args.quit_at_end and quit_ok:
            quit_ok = proc.returncode == 0
        _EXITED_WATCHES.discard(exit_watch.fileno())
        exit_watch.close()
        cpu_user_s = round(usage.ru_utime, 3)
        cpu_sys_s = round(usage.ru_stime, 3)
        logf.close()
        if fifo_fd is not None:
            os.close(fifo_fd)

    elapsed = round(time.monotonic() - started, 1)
    text = log_text()
    metrics = parse_log(text)
    crash = crash_from(text, proc.returncode, killed_by_us)
    report = None
    if proc.returncode and proc.returncode < 0 and not killed_by_us:
        for _ in range(6):  # macOS writes crash reports asynchronously; match our pid only
            found = [r for pattern in ("ps2EntryRunner*.crash", "TarzanFreeridePS2*.crash")
                     for r in (Path.home() / "Library/Logs/DiagnosticReports").glob(pattern)
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
        "fps_gameplay": gameplay_fps(gameplay_samples, args.fps_target or 25,
                                     60 if args.measure else 20),
        "scene": scene if not route else ("playable" if scene_ok >= 2 else scene or "unknown"),
        "fps_dispfb2": metrics.get("fps_dispfb2"),
        "cpu_user_s": cpu_user_s, "cpu_sys_s": cpu_sys_s,
        "vu_fallbacks": metrics.get("vu_fallbacks"), "audio_underruns": metrics.get("audio_underruns"),
        "audio_boundary_underruns": metrics.get("audio_boundary_underruns"),
        "audio_overruns": metrics.get("audio_overruns"), "vu_native_fail": metrics.get("vu_native_fail"),
        "crash": crash, "stubs": metrics["stubs"], "faults": metrics["faults"],
        "black_frames": f"{cap.black}/{cap.frames}" if cap.frames else None,
        "save_dir_changed": changed, "exit_code": proc.returncode, "profile_top": profiled,
        "menu_confirmed_s": menu_at, "keys": keys, "notes": notes, "crash_report": report,
        "log": str(log_path), "user_dir": str(user_dir), "quit_clean": quit_ok,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    shutil.rmtree(scratch, ignore_errors=True) if scratch else None
    steady_audio = bool(summary["audio_underruns"])
    return 0 if reached == target and not changed and not steady_audio and quit_ok is not False else 1


if __name__ == "__main__":
    sys.exit(main())
