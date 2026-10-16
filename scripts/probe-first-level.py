#!/usr/bin/env python3
"""Repeat the known saved-game route; records evidence, never certifies gameplay.

Requires existing save A and macOS Accessibility permission. Uses normal input;
--skip-cutscene is optional so the movie readiness bug can be observed directly.
Additional diagnostics are supplied through the inherited environment.
"""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--label', required=True)
parser.add_argument('--skip-cutscene', action='store_true')
parser.add_argument('--fast', action='store_true', help='debug-only movie bypass and shorter saved-game route')
parser.add_argument('--keep-story', action='store_true', help='with --fast, skip only the logo movie and retain the mission story')
parser.add_argument('--capture-story-audio', action='store_true', help='arm configured PCM capture when the real mission movie starts producing audio')
parser.add_argument('--seconds', type=int, default=180)
parser.add_argument('--hold-ms', type=int, default=150)
parser.add_argument('--route-captures', action='store_true', help='capture each menu before and after input to diagnose route timing')
args = parser.parse_args()
if args.keep_story and not args.fast:
    parser.error('--keep-story requires --fast')
if args.capture_story_audio:
    if not args.keep_story or not os.environ.get('TARZAN_TRACE_PCM') or not os.environ.get('TARZAN_TRACE_PCM_TRIGGER'):
        parser.error('--capture-story-audio requires --keep-story and both PCM capture environment paths')
    os.environ['TARZAN_TRACE_SPU2'] = '1'
if not args.label.replace('-', '').replace('_', '').isalnum():
    parser.error('label must use letters, numbers, - or _')
if args.seconds < (45 if args.fast else 85):
    parser.error('allow at least 85 seconds for the route')


def english_menu_present(lines):
    language = any(line.get('text', '').strip().casefold() == 'language'
                   and line.get('confidence', 0) >= 0.6 for line in lines)
    english = any(line.get('text', '').strip().casefold() == 'english'
                  and line.get('confidence', 0) >= 0.6 for line in lines)
    return language and english


def wait_for_english_menu(process, started, out, capture, result, first_second):
    ocr = ROOT / 'build/ocr-window'
    deadline = time.monotonic() + 45.0
    frames = []
    previous_match_elapsed = None
    frame_index = 0

    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        frame_started = time.monotonic()
        frame_dir = out / 'navigation-ocr' / ('frame-%03d' % frame_index)
        frame_index += 1
        frame = {'elapsed_seconds': round(frame_started - started, 3), 'path': str(frame_dir / 'window-000.png')}
        remaining = max(0.1, deadline - time.monotonic())
        try:
            captured = subprocess.run([str(capture), str(process.pid), str(frame_dir), '1'],
                                      check=False, capture_output=True, text=True, timeout=remaining)
            frame['capture_returncode'] = captured.returncode
            if captured.returncode == 0 and (frame_dir / 'window-000.png').is_file():
                remaining = max(0.1, deadline - time.monotonic())
                recognized = subprocess.run([str(ocr), str(frame_dir / 'window-000.png')],
                                            check=False, capture_output=True, text=True, timeout=remaining)
                frame['ocr_returncode'] = recognized.returncode
                if recognized.returncode == 0:
                    lines = json.loads(recognized.stdout)
                    frame['lines'] = lines
                    frame['english_menu_found'] = english_menu_present(lines)
                else:
                    frame['ocr_error'] = recognized.stderr.strip()
            else:
                frame['capture_error'] = captured.stderr.strip()
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as error:
            frame['error'] = str(error)

        frame_finished = time.monotonic()
        frame['finished_elapsed_seconds'] = round(frame_finished - started, 3)
        frames.append(frame)
        if frame.get('english_menu_found'):
            if previous_match_elapsed is not None and frame_started - previous_match_elapsed >= 0.5:
                guard_elapsed = frame_finished - (started + first_second)
                route_delay = max(0.0, frame_finished - started - first_second)
                result['navigation_guard'] = {
                    'status': 'english_menu_confirmed',
                    'required_labels': ['LANGUAGE', 'ENGLISH'],
                    'minimum_confidence': 0.6,
                    'confirming_frame_elapsed_seconds': [round(previous_match_elapsed, 3), frame['elapsed_seconds']],
                    'elapsed_seconds': round(guard_elapsed, 3),
                    'frames': frames,
                    'route_delay_seconds': round(route_delay, 3),
                }
                result['navigation_failed'] = False
                return route_delay
            previous_match_elapsed = frame_started
        else:
            previous_match_elapsed = None

        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.5, remaining))

    elapsed = time.monotonic() - (started + first_second)
    result['navigation_failed'] = True
    result['navigation_guard'] = {
        'status': 'english_menu_not_confirmed',
        'required_labels': ['LANGUAGE', 'ENGLISH'],
        'minimum_confidence': 0.6,
        'elapsed_seconds': round(max(0.0, elapsed), 3),
        'frames': frames,
    }
    result['error'] = 'navigation_failed: English language menu was not confirmed on two OCR frames'
    raise RuntimeError(result['error'])


out = ROOT / 'runtime/checks' / args.label
out.mkdir(parents=True, exist_ok=False)
runner = ROOT / 'build/game-metal/ps2recomp/ps2xRuntime/ps2EntryRunner'
if args.fast:
    os.environ['TARZAN_DEBUG_SKIP_MOVIES'] = '1'
    if args.keep_story:
        os.environ['TARZAN_DEBUG_KEEP_STORY'] = '1'
command = [str(runner), '--user-dir', str(Path.home() / 'Library/Application Support/TarzanFreeridePS2'),
           str(ROOT / 'extracted/disc/SLES_503.50')]
with (out / 'stdout.log').open('wb') as log:
    process = subprocess.Popen(command, cwd=ROOT, env=os.environ.copy(), stdout=log, stderr=subprocess.STDOUT)
    started = time.monotonic()
    result = {'pid': process.pid, 'command': command, 'started': time.time(),
              'gameplay_verified': False, 'skip_cutscene': args.skip_cutscene, 'debug_skip_movies': args.fast, 'inputs': []}
    (out / 'launch.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({'pid': process.pid, 'output': str(out)}), flush=True)
    route = [(47,7), (53,36), (60,125), (61,7), (68,7), (75,7), (82,7)]
    if args.fast:
        # Allow initialization/fade-in headroom for diagnostic builds too.
        route = [(20,7), (27,36), (33,125), (34,7), (42,7), (49,7), (56,7)]
    elif args.skip_cutscene:
        route.append((100,36))
    try:
        route_delay = 0.0
        capture = Path('/tmp/tarzan-window-capture')
        for index, (second, key) in enumerate(route):
            target_second = second + route_delay
            while time.monotonic() - started < target_second and process.poll() is None:
                time.sleep(.2)
            if process.poll() is not None:
                break
            if index == 0:
                if not capture.is_file() or not (ROOT / 'build/ocr-window').is_file():
                    result['navigation_failed'] = True
                    result['error'] = 'navigation_failed: window capture tool or OCR helper is unavailable'
                    result['navigation_guard'] = {'status': 'tool_unavailable', 'elapsed_seconds': 0.0, 'frames': []}
                    raise RuntimeError(result['error'])
                route_delay = wait_for_english_menu(process, started, out, capture, result, second)
            if args.route_captures and capture.is_file():
                subprocess.run([str(capture), str(process.pid), str(out / ('route-%03d-before' % second)), '1'], check=False)
            subprocess.run(['osascript', '-e', 'tell application "System Events" to set frontmost of first process whose unix id is %d to true' % process.pid], check=True, capture_output=True)
            sent = subprocess.run([str(ROOT / 'build/send-native-key'), '--pid', str(process.pid),
                                   '--key-code', str(key), '--hold-ms', str(args.hold_ms)], capture_output=True, text=True)
            actual_elapsed = time.monotonic() - started
            result['inputs'].append({'second': second, 'scheduled_elapsed_seconds': round(target_second, 3),
                                     'actual_elapsed_seconds': round(actual_elapsed, 3),
                                     'key': key, 'returncode': sent.returncode})
            print('input', second, key, sent.returncode, 'elapsed', round(actual_elapsed, 3), flush=True)
            if args.route_captures and capture.is_file():
                time.sleep(.5)
                subprocess.run([str(capture), str(process.pid), str(out / ('route-%03d-after' % second)), '1'], check=False)
        story_seen = False
        capture_armed = False
        while time.monotonic() - started < args.seconds and process.poll() is None:
            if args.capture_story_audio and not capture_armed:
                with (out / 'stdout.log').open('rb') as audio_log:
                    audio_log.seek(0, 2)
                    audio_log.seek(max(0, audio_log.tell() - 16384))
                    recent = audio_log.read().decode('utf-8', errors='replace')
                story_seen = story_seen or 'W1_1_GM.PSS' in recent
                levels = re.findall(r'\[tarzan-spu2-health\][^\n]*rms=([0-9.]+)', recent)
                if story_seen and levels and float(levels[-1]) >= .05:
                    Path(os.environ['TARZAN_TRACE_PCM_TRIGGER']).touch()
                    capture_armed = True
                    result['story_audio_capture'] = {'armed_elapsed_seconds': round(time.monotonic() - started, 3),
                                                     'rms_at_trigger': float(levels[-1])}
                    subprocess.run([str(capture), str(process.pid), str(out / 'story-audio-trigger'), '1'], check=False)
                    print('story audio capture armed', result['story_audio_capture'], flush=True)
            time.sleep(.2)
        capture = Path('/tmp/tarzan-window-capture')
        if capture.is_file() and process.poll() is None:
            subprocess.run([str(capture), str(process.pid), str(out / 'window'), '3'], check=False)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        result.update(exit_code=process.returncode, elapsed_seconds=round(time.monotonic() - started, 3))
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
