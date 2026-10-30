"""Unit tests for the USB device harness profile selection and run summaries."""
import datetime as dt
from contextlib import ExitStack
import json
import pathlib
import subprocess
import threading
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools/device"))
import device
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools/probe"))
import probe


class DeviceHarnessTests(unittest.TestCase):
    def test_remote_pairing_uses_physical_identity_and_tv_paths(self):
        endpoints = [{"tunnel-address": "fd00::1", "tunnel-port": 5000}]
        properties = {"UniqueDeviceID": "physical-tv", "ProductType": "AppleTV14,1"}
        reply = SimpleNamespace(stdout=json.dumps({"Properties": properties}), returncode=0)
        for requested in ("66682080-B7F2-44C6-B7CD-A176ECF457B8", "physical-tv", None):
            with self.subTest(requested=requested), \
                 patch.dict(device._TUNNEL_INFO, {}, clear=True), \
                 patch.object(device, "configured_udid", return_value=None), \
                 patch.object(device, "tunnel_devices", return_value={"66682080-B7F2-44C6-B7CD-A176ECF457B8": endpoints}), \
                 patch.object(device, "pmd", return_value=reply) as pmd:
                identity = device.choose_udid(requested)
                self.assertEqual(identity, "physical-tv")
                self.assertEqual(device.global_device_args(identity), ["--tunnel", "physical-tv"])
                self.assertEqual(device.remote_paths(identity)["game"], "Library/Caches")
                self.assertEqual(device.default_app(identity),
                                 device.ROOT / "build/embedded/appletvos/FIFA Street PS2.app")
                self.assertEqual(pmd.call_count, 1)

    def test_tunnel_aliases_deduplicate_and_never_select_a_different_device(self):
        endpoints = [{"tunnel-address": "fd00::1", "tunnel-port": 5000}]
        reply = SimpleNamespace(stdout=json.dumps({"Properties": {
            "UniqueDeviceID": "physical-tv", "ProductType": "AppleTV14,1"}}), returncode=0)
        with patch.dict(device._TUNNEL_INFO, {}, clear=True), \
             patch.object(device, "configured_udid", return_value=None), \
             patch.object(device, "tunnel_devices", return_value={
                 "66682080-B7F2-44C6-B7CD-A176ECF457B8": endpoints, "physical-tv": endpoints}), \
             patch.object(device, "pmd", return_value=reply):
            self.assertEqual(device.choose_udid(), "physical-tv")
            with self.assertRaises(device.Missing):
                device.choose_udid("different-tv")

    def test_tunnel_identity_requires_a_successful_rsd_response(self):
        endpoints = [{"tunnel-address": "fd00::1", "tunnel-port": 5000}]
        for stdout, code in (("invalid", 0), ("{}", 0),
                             ('{"Properties":{"UniqueDeviceID":""}}', 0),
                             ('{"Properties":{"UniqueDeviceID":"physical-tv"}}', 0),
                             ('{"Properties":{"UniqueDeviceID":"wrong"}}', 1)):
            with self.subTest(stdout=stdout, code=code), \
                 patch.dict(device._TUNNEL_INFO, {}, clear=True), \
                 patch.object(device, "configured_udid", return_value=None), \
                 patch.object(device, "tunnel_devices", return_value={"66682080-B7F2-44C6-B7CD-A176ECF457B8": endpoints}), \
                 patch.object(device, "pmd", return_value=SimpleNamespace(stdout=stdout, returncode=code)):
                with self.assertRaises(device.Missing):
                    device.choose_udid("66682080-B7F2-44C6-B7CD-A176ECF457B8")

    def test_stale_tunnel_cannot_mask_explicit_target_or_enable_auto_selection(self):
        reply = SimpleNamespace(stdout=json.dumps({"Properties": {
            "UniqueDeviceID": "physical-tv", "ProductType": "AppleTV14,1"}}), returncode=0)
        def pmd(*args, **kwargs):
            if "stale" in args:
                raise device.Failed("timed out")
            return reply
        with patch.dict(device._TUNNEL_INFO, {}, clear=True), \
             patch.object(device, "configured_udid", return_value=None), \
             patch.object(device, "tunnel_devices", return_value={
                 "offline": [None, {}, {"tunnel-address": "stale", "tunnel-port": 5000}],
                 "physical-tv": [{"tunnel-address": "healthy", "tunnel-port": 5001}]}), \
             patch.object(device, "pmd", side_effect=pmd):
            self.assertEqual(device.choose_udid("physical-tv"), "physical-tv")
            with self.assertRaises(device.Missing):
                device.choose_udid()

    def test_stale_physical_tunnel_alias_cannot_redirect_target(self):
        reply = SimpleNamespace(stdout=json.dumps({"Properties": {
            "UniqueDeviceID": "other-tv", "ProductType": "AppleTV14,1"}}), returncode=0)
        with patch.dict(device._TUNNEL_INFO, {}, clear=True), \
             patch.object(device, "tunnel_devices", return_value={"physical-tv": [
                 {"tunnel-address": "fd00::1", "tunnel-port": 5000}]}), \
             patch.object(device, "pmd", return_value=reply):
            with self.assertRaises(device.Missing):
                device.choose_udid("physical-tv")

    def test_run_can_skip_device_telemetry_and_keep_capture_outputs(self):
        for quiet in (False, True):
            with self.subTest(no_device_telemetry=quiet), tempfile.TemporaryDirectory() as temp:
                root = pathlib.Path(temp)
                threads = []

                def thread(**kwargs):
                    result = Mock()
                    result.start.side_effect = lambda: kwargs["target"](*kwargs["args"])
                    threads.append(result)
                    return result

                def pmd(*args, **kwargs):
                    return SimpleNamespace(stdout="pid 123" if args[:3] ==
                        ("developer", "dvt", "launch") else "{}" if args[:2] ==
                        ("diagnostics", "battery") else "", returncode=0)

                args = SimpleNamespace(seconds=3, warmup=0, udid="tv", app=None,
                    label="quiet", no_match=False, env=[], shots_every=1,
                    no_pipeline_cache=True, no_device_telemetry=quiet)
                with ExitStack() as stack:
                    stack.enter_context(patch.object(device, "ROOT", root))
                    stack.enter_context(patch.object(device, "choose_udid", return_value="tv"))
                    stack.enter_context(patch.object(device, "default_app", return_value=root / "app"))
                    stack.enter_context(patch.object(device, "info_plist", return_value={"CFBundleExecutable": "game"}))
                    stack.enter_context(patch.object(device, "state_read", return_value={}))
                    stack.enter_context(patch.object(device, "installed_bundle", return_value="game.bundle"))
                    commands = stack.enter_context(patch.object(device, "pmd", side_effect=pmd))
                    stack.enter_context(patch.object(device, "remote_paths",
                        return_value={"isolated": "Library/Caches", "perf": "Library/Caches"}))
                    graphics = stack.enter_context(patch.object(device, "stream"))
                    sysmon = stack.enter_context(patch.object(device, "stream_sysmon"))
                    popen = stack.enter_context(patch.object(device.subprocess, "Popen"))
                    event = stack.enter_context(patch.object(device.threading, "Event"))
                    stack.enter_context(patch.object(device.threading, "Thread", side_effect=thread))
                    stack.enter_context(patch.object(device.time, "sleep"))
                    stack.enter_context(patch.object(device.time, "monotonic", side_effect=[0, 1.5, 1.5, 4]))
                    screenshot = stack.enter_context(patch.object(device, "capture_screenshot"))
                    summarize = stack.enter_context(patch.object(device, "summarize", return_value={"shots_count": 1}))
                    stack.enter_context(patch("builtins.print"))
                    out = device.do_run(args)
                popen.assert_not_called()
                self.assertEqual(len(threads), 0 if quiet else 2)
                self.assertEqual(event.return_value.wait.call_count, 0 if quiet else 1)
                self.assertEqual(graphics.call_count, 0 if quiet else 1)
                self.assertEqual(sysmon.call_count, 0 if quiet else 1)
                battery = [call for call in commands.call_args_list if call.args[:2] == ("diagnostics", "battery")]
                self.assertEqual(len(battery), 0 if quiet else 1)
                screenshot.assert_called_once()
                summarize.assert_called_once_with(out)
                pulls = [call for call in commands.call_args_list if call.args[:2] == ("apps", "pull")]
                self.assertEqual(len(pulls), 2)
                metadata = json.loads((out / "run.json").read_text())
                self.assertEqual(metadata["device_telemetry"], not quiet)
                self.assertIn("PS2X_PERF_LOG=1", metadata["env"])
                self.assertEqual((out / "battery.jsonl").exists(), not quiet)

    def test_device_telemetry_option_cli_defaults_and_dispatch(self):
        commands = {
            "run": ["--seconds", "1"],
            "ab": ["--control", "K=0", "--candidate", "K=1", "--pairs", "1", "--seconds", "1", "--label", "quiet"],
            "baseline": ["--runs", "1", "--seconds", "1", "--label", "quiet"],
            "coverage": [],
        }
        for command, argv in commands.items():
            for quiet in (False, True):
                with self.subTest(command=command, quiet=quiet), \
                     patch.object(device, "do_run") as run, \
                     patch.object(device, "do_coverage") as coverage, \
                     patch.object(device, "run_experiment") as experiment:
                    self.assertEqual(device.main([command, *argv, *(["--no-device-telemetry"] if quiet else [])]), 0)
                selected = run if command == "run" else coverage if command == "coverage" else experiment
                self.assertEqual(selected.call_args.args[0].no_device_telemetry, quiet)

    def test_experiments_and_coverage_propagate_no_device_telemetry(self):
        for kind in ("ab", "baseline", "coverage"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                root = pathlib.Path(temp)
                runs = []

                def run(args):
                    runs.append(args)
                    out = root / "run" / str(len(runs))
                    out.mkdir(parents=True)
                    (out / "summary.json").write_text('{"metrics":{}}')
                    return out

                args = SimpleNamespace(pairs=1, runs=2, seconds=1, warmup=0,
                    label="quiet", control=["K=0"], candidate=["K=1"], env=[],
                    control_app=None, candidate_app=None, udid="tv", no_device_telemetry=True,
                    no_match=False, shots_every=1, app=None)
                with patch.object(device, "ROOT", root), \
                     patch.object(device, "choose_udid", return_value="tv"), \
                     patch.object(device, "do_run", side_effect=run), \
                     patch.object(device, "run_metrics", return_value={"fps": 60}), \
                     patch.object(device, "_battery_start") as battery_start, \
                     patch.object(device, "device_battery_temp") as battery_temperature, \
                     patch.object(device, "installed_bundle_id", return_value="game.bundle"), \
                     patch.object(device, "record_target", return_value="/coverage.txt"), \
                     patch.object(device, "coverage_record_remote", return_value="coverage.txt"), \
                     patch.object(device, "pmd", return_value=SimpleNamespace(returncode=0)), \
                     patch("builtins.print"):
                    if kind == "coverage":
                        device.do_coverage(args)
                    else:
                        device.run_experiment(args, kind)
                self.assertEqual(len(runs), 1 if kind == "coverage" else 2)
                self.assertTrue(all(run.no_device_telemetry for run in runs))
                battery_start.assert_not_called()
                battery_temperature.assert_not_called()

    def test_summary_uses_only_latest_launch_in_appended_log(self):
        old = "[settings] videoMode=ntsc scale=3\nthermalState=3\n" + "\n".join(
            f"[autopilot] clock_seconds={seconds} wall_seconds={seconds}"
            for seconds in range(0, 14, 2)) + "\n"
        current = "[settings] videoMode=ntsc scale=1\nthermalState=1\n"
        appended = old + current
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "native.log").write_text(appended)
            with patch.object(device, "ledger_control_reference", return_value=(None, None, None)), \
                 patch.object(device, "print_summary"):
                result = device.summarize(root)
            self.assertEqual((root / "native.log").read_text(), appended)
        self.assertEqual(result["thermal_state_max"], 1)
        self.assertIsNone(result["hud_clock"]["rate"])
        self.assertEqual(result["hud_clock"]["samples"], 0)
        self.assertTrue(any("HUD clock progression unavailable" in reason
                            for reason in result["validity"]["reasons"]))
        self.assertEqual(device.hud_clock_progress(appended)["samples"], 0)
        self.assertEqual(device.current_launch_log(appended), current)
        self.assertEqual(device.current_launch_log("log without startup marker"), "log without startup marker")

    def test_cooldown_skips_unavailable_temperature_without_sleep(self):
        for target, observed in ((None, 20), (20, None)):
            with self.subTest(target=target, observed=observed), \
                 patch.object(device, "device_battery_temp", return_value=observed) as temperature, \
                 patch.object(device.time, "sleep") as sleep, \
                 patch.object(device.time, "monotonic", side_effect=[0, 1, 181]):
                result = device.cooldown("tv", target)
            sleep.assert_not_called()
            self.assertTrue(result["unavailable"])
            self.assertFalse(result["timed_out"])
            if target is None:
                temperature.assert_not_called()

    def test_collectors_reap_children_after_read_failure(self):
        real_popen = subprocess.Popen
        for collector in (device.stream, device.stream_sysmon):
            with self.subTest(collector=collector.__name__), tempfile.TemporaryDirectory() as temp:
                children = []

                def launch(*args, **kwargs):
                    child = real_popen(*args, **kwargs)
                    children.append(child)
                    return child

                output = pathlib.Path(temp) / "samples.jsonl"
                try:
                    with patch.object(device, "cli", return_value=sys.executable), \
                         patch.object(device.subprocess, "Popen", side_effect=launch), \
                         patch.object(device.select, "select", side_effect=OSError("collector read failed")):
                        collector(["-c", "import time; time.sleep(60)"], output, threading.Event())
                    self.assertEqual(len(children), 1)
                    self.assertIsNotNone(children[0].poll())
                    self.assertTrue(children[0].stdout.closed)
                    self.assertIn("collector read failed", output.read_text())
                    self.assertFalse(output.with_suffix(".raw.jsonl").exists())
                finally:
                    for child in children:
                        if child.poll() is None:
                            child.kill()
                        child.wait(timeout=3)
                        child.stdout.close()

    def test_run_stops_collectors_on_interrupt_and_kill_failure(self):
        for failure in ("interrupt", "kill"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                root = pathlib.Path(temp)
                events = []
                threads = []

                def event():
                    result = Mock()
                    events.append(result)
                    return result

                def thread(**kwargs):
                    result = Mock()
                    result.kwargs = kwargs
                    threads.append(result)
                    return result

                def pmd(*args, **kwargs):
                    if args[:3] == ("developer", "dvt", "launch"):
                        return SimpleNamespace(stdout="pid 123")
                    if args[:3] == ("developer", "dvt", "kill") and failure == "kill":
                        raise device.Failed("device kill timed out")
                    return SimpleNamespace(stdout="")

                def sleep(seconds):
                    if seconds == 0.25:
                        raise KeyboardInterrupt()

                args = SimpleNamespace(seconds=1, warmup=0, udid="tv", app=None,
                    label="cleanup", no_match=False, env=[], shots_every=0,
                    no_pipeline_cache=True)
                with patch.object(device, "ROOT", root), \
                     patch.object(device, "choose_udid", return_value="tv"), \
                     patch.object(device, "default_app", return_value=root / "app"), \
                     patch.object(device, "info_plist", return_value={"CFBundleExecutable": "game"}), \
                     patch.object(device, "state_read", return_value={}), \
                     patch.object(device, "installed_bundle", return_value="game.bundle"), \
                     patch.object(device, "pmd", side_effect=pmd), \
                     patch.object(device.threading, "Event", side_effect=event), \
                     patch.object(device.threading, "Thread", side_effect=thread), \
                     patch.object(device.time, "sleep", side_effect=sleep), \
                     patch.object(device.time, "monotonic", side_effect=None if failure == "interrupt" else [0, 2], return_value=0):
                    with self.assertRaises(KeyboardInterrupt if failure == "interrupt" else device.Failed):
                        device.do_run(args)
                self.assertEqual(len(threads), 2)
                events[0].set.assert_called_once_with()
                for collector in threads:
                    collector.start.assert_called_once_with()
                    collector.join.assert_called_once_with(timeout=8)
                sysmon_args = threads[1].kwargs["args"][0]
                self.assertNotIn("--keep-monitoring", sysmon_args)
                self.assertEqual(sysmon_args[sysmon_args.index("--duration") + 1], "31000")

    def test_abba_order_and_control_noise_verdict(self):
        self.assertEqual(device.abba_order(3), ["control", "candidate", "candidate", "control", "control", "candidate"])
        table, verdicts = device.compare_metrics(
            [{"fps": 50.0, "present_dt_ms_p50": 20.0}, {"fps": 51.0, "present_dt_ms_p50": 19.0}],
            [{"fps": 55.0, "present_dt_ms_p50": 17.0}, {"fps": 55.0, "present_dt_ms_p50": 17.0}],
        )
        self.assertEqual(table["fps"]["control_noise"], 1.0)
        self.assertEqual(verdicts, {"fps": "better", "present_dt_ms_p50": "better"})
        _, stable = device.compare_metrics([{"fps": 50.0}, {"fps": 51.0}], [{"fps": 51.0}])
        self.assertEqual(stable["fps"], "no-change")

    def test_ab_installs_prepared_apps_per_run_and_restores_head_build(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            control = root / "control.app"
            candidate = root / "candidate.app"
            head = root / "head.app"
            prepared = []
            installs = []
            runs = []
            install_times = iter((1.0, 0.5, 1.5, 2.0, 0.25))

            def sign(app, lean, udid):
                prepared.append((pathlib.Path(app), lean, udid))
                return root / (pathlib.Path(app).stem + ".signed"), "example.game"

            def install(app, lean, udid, stage, bundle):
                installs.append((pathlib.Path(app), lean, udid, pathlib.Path(stage), bundle))
                return next(install_times), True

            def do_run(args):
                runs.append(args)
                out = root / "run" / str(len(runs))
                out.mkdir(parents=True)
                return out

            args = SimpleNamespace(pairs=2, runs=2, seconds=1, warmup=0, label="apps",
                control=["ARM=control"], candidate=["ARM=candidate"], env=[],
                control_app=str(control), candidate_app=str(candidate), udid="device")
            with patch.object(device, "ROOT", root), \
                 patch.object(device, "choose_udid", return_value="device"), \
                 patch.object(device, "sign_stage", side_effect=sign), \
                 patch.object(device, "install_prepared", side_effect=install), \
                 patch.object(device, "do_run", side_effect=do_run), \
                 patch.object(device, "run_metrics", return_value={"fps": 50}), \
                 patch.object(device, "_battery_start", return_value=20), \
                 patch.object(device, "cooldown", return_value={"seconds": 0}), \
                 patch.object(device, "default_app", return_value=head):
                device.run_experiment(args, "ab")

            self.assertEqual(len(prepared), 3)
            self.assertTrue(all(row[1:] == (True, "device") for row in prepared))
            self.assertEqual([pathlib.Path(run.app) for run in runs],
                [control.resolve(), candidate.resolve(), candidate.resolve(), control.resolve()])
            self.assertEqual([row[0] for row in installs[:4]],
                [control.resolve(), candidate.resolve(), candidate.resolve(), control.resolve()])
            self.assertEqual(installs[-1][0], head.resolve())
            data = json.loads(next((root / "build/device-ab").glob("*/ab.json")).read_text())
            self.assertEqual(data["install_seconds"], [1.0, 0.5, 1.5, 2.0])
            self.assertEqual(data["installed_at_end"], str(head.resolve()))
            report = next((root / "build/device-ab").glob("*/ab.txt")).read_text()
            self.assertIn("Install median: 1.25s", report)
            self.assertIn(f"Installed at end: {head.resolve()}", report)
            self.assertEqual(runs[0].shots_every, 1)
            self.assertIn("Verdict: experiment: INVALID:", report)

    def test_hud_clock_rate_and_validity_guards(self):
        log = "\n".join(
            f"[autopilot] clock_seconds={seconds:.1f} wall_seconds={wall:.1f}"
            for seconds, wall in ((10, 2), (12, 4), (14, 6), (16, 8), (18, 10), (20, 12), (22, 14))
        )
        clock = device.hud_clock_progress(log)
        self.assertEqual(clock["rate"], 1.0)
        self.assertEqual(clock["expected_guest_seconds_per_wall_second"], 1.0)
        summary = {"time_to_play_s": 20, "hud_clock": clock, "shots_count": 1,
                   "metrics": {"draws_per_gameplay_frame_p50": 20}}
        self.assertTrue(device.evaluate_validity(summary, 20, 30)["valid"])
        summary["metrics"]["draws_per_gameplay_frame_p50"] = 14
        result = device.evaluate_validity(summary, 20, 30)
        self.assertFalse(result["valid"])
        self.assertTrue(any("50% of control" in reason for reason in result["reasons"]))
        self.assertFalse(device.evaluate_validity(summary, 20, 30, 0.8)["valid"])
        self.assertTrue(device.evaluate_validity({**summary, "hud_clock": {"rate": 0.8},
                                                  "metrics": {"draws_per_gameplay_frame_p50": 20}},
                                                  20, 30, 0.8)["valid"])
        self.assertTrue(any("HUD clock rate" in reason for reason in
                            device.evaluate_validity({**summary, "hud_clock": {"rate": 0.7}}, 20, 30)["reasons"]))
        self.assertTrue(any("45s" in reason for reason in
                            device.evaluate_validity({**summary, "time_to_play_s": 46}, 20, 30)["reasons"]))

    def test_probe_reports_zero_underruns_without_trace_sampling(self):
        text = "[spu2] SDL playback input=SDL_AUDIO_F32LE/2/48000Hz\n"
        self.assertEqual(probe.parse_log(text)["audio_underruns"], 0)
        text += "[spu2-underrun] frames=7 ring_occupancy_frames=0\n"
        self.assertEqual(probe.parse_log(text)["audio_underruns"], 7)

    def test_run_metrics_draw_count_and_late_frame_breakdown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            perf = root / "perf"; perf.mkdir()
            rows = []
            for index, (dt_ms, ee_ms, draws) in enumerate(((16, 2, 10), (17, 3, 20), (20, 8, 30), (25, 10, 40))):
                rows.append({"t": index, "gameplay": True, "present_dt_ms": dt_ms,
                             "cpu_game_ms": ee_ms + 1, "ee_ms": ee_ms, "draws": draws,
                             "iop_ms": 1, "spu2_ms": 0.5, "other_ms": 0.5,
                             "vu1_exec_ms": 1, "vif_unpack_ms": 0.1, "xgkick_ms": 0.2,
                             "gs_submit_ms": 0.4})
            (perf / "frames.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            metrics = device.run_metrics(root)
        self.assertEqual(metrics["draws_per_gameplay_frame_p50"], 25)
        self.assertEqual(metrics["ee_ms_on_time_p50"], 2.5)
        self.assertEqual(metrics["ee_ms_late_p50"], 9)

    def test_active_gameplay_rows_include_frames_between_clock_updates(self):
        rows = [{"t": float(index), "game_clock_s": float(index)}
                for index in range(4)]
        rows.extend({"t": float(index), "game_clock_s": 3.0} for index in range(4, 20))
        active = device.active_gameplay_rows(rows)
        self.assertGreater(len(active), 4)
        self.assertLess(len(active), len(rows))

    def test_fps_excludes_removed_clock_gaps_from_duration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "perf").mkdir()
            rows = [{"t": index / 60, "gameplay": True,
                     "game_clock_s": min(index / 60, 1) if index < 600 else index / 60,
                     "present_dt_ms": 1000 / 60} for index in range(720)]
            (root / "perf/frames.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            self.assertLess(len(device.active_gameplay_rows(rows)), len(rows))
            self.assertAlmostEqual(device.run_metrics(root)["fps"], 60)

    def test_thread_cores_weight_only_retained_active_frames(self):
        snapshots = [{"t": when, "thread_cpu_s": {"GameThread": cpu}}
                     for when, cpu in [(0, 0), (1, 0.5), (10, 0.5), (11, 1.0)]]
        rows = [{"t": 0.5, "present_dt_ms": 20}, {"t": 10.5, "present_dt_ms": 20}]
        cores = device.active_thread_cores(rows, snapshots)
        self.assertAlmostEqual(cores["gamethread_cores"], 0.5)
        self.assertAlmostEqual(cores["process_cores"], 0.5)
        self.assertEqual(device.active_thread_cores(rows, []), {})
        snapshots = [{"t": when, "thread_cpu_s": {"GameThread": cpu}}
                     for when, cpu in [(0, 0), (1, 1), (2, 1)]]
        crossing = [{"t": 1.5, "present_dt_ms": 1000}]
        self.assertAlmostEqual(device.active_thread_cores(crossing, snapshots)["process_cores"], 0.5)

    def test_ab_app_arguments_must_be_paired(self):
        with tempfile.TemporaryDirectory() as temp:
            args = SimpleNamespace(pairs=1, runs=1, seconds=1, warmup=0, label="apps",
                control=[], candidate=[], env=[], control_app="control.app",
                candidate_app=None, udid="device")
            with patch.object(device, "ROOT", pathlib.Path(temp)), \
                 patch.object(device, "choose_udid", return_value="device"):
                with self.assertRaisesRegex(device.Failed, "must be supplied together"):
                    device.run_experiment(args, "ab")

    def test_run_metrics_filters_frames_to_gameplay_window(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            perf = root / "perf"
            perf.mkdir()
            rows = [
                {"t": 0, "gameplay": False, "present_dt_ms": 100, "audio_underruns": 0},
                {"t": 1, "gameplay": True, "present_dt_ms": 16, "cpu_game_ms": 5,
                 "vu1_ms": 2, "gs_submit_ms": 3, "gpu_ms": 4, "present_wait_ms": 1,
                 "audio_underruns": 1, "pipeline_compiles": 2},
                {"t": 2, "gameplay": True, "present_dt_ms": 20, "cpu_game_ms": 7,
                 "vu1_ms": 4, "gs_submit_ms": 5, "gpu_ms": 6, "present_wait_ms": 2,
                 "audio_underruns": 3, "pipeline_compiles": 1},
                {"t": 3, "type": "summary", "thread_cpu_s": {"GameThread": 2, "VU1GSWorker": 1}},
                {"t": 4, "type": "summary", "thread_cpu_s": {"GameThread": 4, "VU1GSWorker": 2},
                 "audio_underruns": 4, "pipeline_compiles": 5},
            ]
            (perf / "frames.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            metrics = device.run_metrics(root)
        self.assertAlmostEqual(metrics["fps"], 1000 / 18)
        self.assertEqual(metrics["present_dt_ms_p50"], 18.0)
        self.assertEqual(metrics["late_frames_over_18_pct"], 50.0)
        self.assertEqual(metrics["audio_underruns_delta_gameplay"], 2)
        self.assertEqual(metrics["audio_underruns_delta_run"], 4)
        self.assertEqual(metrics["pipeline_compiles_delta_gameplay"], 3)

    def test_profile_selection_matches_identity_and_udid(self):
        now = dt.datetime.now(dt.timezone.utc)
        identity = "A" * 40
        cert = bytes(range(32))
        identity = __import__("hashlib").sha1(cert).hexdigest().upper()
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "profile.mobileprovision"
            profile = {"ExpirationDate": now + dt.timedelta(days=10),
                       "DeveloperCertificates": [cert], "ProvisionedDevices": ["device-1"],
                       "Entitlements": {"application-identifier": "TEAM.example.game"}}
            path.write_bytes(__import__("plistlib").dumps(profile))
            expiry, selected, found, bundle = device.select_profile(temp, "device-1", identity, now)
            self.assertEqual(selected, path)
            self.assertEqual(found["Entitlements"], profile["Entitlements"])
            self.assertEqual(bundle, "example.game")
            with self.assertRaises(device.Failed):
                device.select_profile(temp, "other-device", identity, now)

    def test_summary_reads_gpu_battery_and_generic_perf_samples(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "shots").mkdir()
            (root / "shots/0000.png").write_bytes(b"\x89PNG\r\n\x1a\nimage")
            (root / "shots/0001.png").write_bytes(b"")
            (root / "gpu.jsonl").write_text(
                '{"Device Utilization %":20,"CoreAnimationFramesPerSecond":60,"_host_time":10}\n'
                '{"Device Utilization %":40,"CoreAnimationFramesPerSecond":30,"_host_time":20}\n')
            (root / "sysmon.jsonl").write_text('{"cpuUsage":10,"_host_time":10}\n{"cpuUsage":30,"_host_time":20}\n')
            (root / "battery.jsonl").write_text('{"Temperature":30}\n{"Temperature":34}\n')
            perf = root / "perf"; perf.mkdir()
            (perf / "frames.jsonl").write_text(
                '{"type":"frame","guest_fps":50,"draws":2}\n'
                '{"type":"frame","guest_fps":60,"draws":4}\n'
                '{"type":"summary","frames":2,"avg_guest_fps":55}\n')
            result = device.summarize(root)
            self.assertEqual(result["gpu"]["gpu_Device Utilization %_mean"], 30)
            self.assertEqual(result["core_animation_fps"]["median"], 45)
            self.assertEqual(result["battery_temperature"]["delta"], 4)
            self.assertEqual(result["in_app_summary"]["avg_guest_fps"], 55)
            self.assertEqual(result["in_app_perf"]["guest_fps"]["mean"], 55)
            self.assertEqual(result["shots_count"], 1)
            self.assertTrue((root / "summary.json").is_file())

    def test_capture_screenshot_checks_output_and_reports_tunnel_command(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = pathlib.Path(temp) / "shot.png"
            png = b"\x89PNG\r\n\x1a\nimage"

            def screenshot(*args, **kwargs):
                self.assertEqual(args, ("developer", "dvt", "screenshot", "--tunnel", "device", str(dest)))
                self.assertEqual(kwargs, {"timeout": 25})
                dest.write_bytes(png)

            with patch.object(device, "pmd", side_effect=screenshot):
                device.capture_screenshot("device", dest)
            self.assertEqual(dest.read_bytes(), png)

            dest.unlink()
            with patch.object(device, "pmd"):
                with self.assertRaisesRegex(device.Failed, "without writing"):
                    device.capture_screenshot("device", dest)

    def test_presented_events_do_not_change_existing_frame_summaries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "perf").mkdir()
            path = root / "perf/frames.jsonl"
            rows = [
                {"t": 100, "gameplay": True, "game_frame": 1, "present_dt_ms": 16.7,
                 "draws": 100, "pipeline_compiles": 0},
                {"type": "summary", "t": 101, "wall_time": 1001,
                 "pipeline_compiles": 0, "audio_underruns": 0},
            ]
            with patch.object(device, "print_summary"):
                path.write_text("".join(json.dumps(row) + "\n" for row in rows))
                control = device.summarize(root)
                rows.extend([
                    {"type": "presented", "present_id": 1, "game_frame": 999999,
                     "submitted_t": 100, "presented_time": 500},
                    {"type": "presented_summary", "t": 1000000, "registered": 1,
                     "collected": 1, "lost": 0, "uncollected": 0, "final": False},
                ])
                path.write_text("".join(json.dumps(row) + "\n" for row in rows))
                candidate = device.summarize(root)
            for key in ("in_app_perf", "in_app_summary", "metrics"):
                self.assertEqual(candidate[key], control[key])


    def test_h2_summary_uses_device_clock_and_separates_counters(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "run.json").write_text(json.dumps({"launch_host_time": 1000}))
            (root / "gpu.jsonl").write_text(
                '{"CoreAnimationFramesPerSecond":30,"_host_time":1000.5}\n'
                '{"CoreAnimationFramesPerSecond":60,"_host_time":1002}\n')
            perf = root / "perf"
            perf.mkdir()
            rows = [
                {"t": 5000.5, "gameplay": False, "audio_underruns": 0, "pipeline_compiles": 1},
                {"t": 5001, "gameplay": True, "audio_underruns": 0, "pipeline_compiles": 0},
                {"type": "summary", "t": 5002, "wall_time": 1002,
                 "audio_underruns": 0, "pipeline_compiles": 1, "thermal_state": 0},
            ]
            (perf / "frames.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            result = device.summarize(root)
            self.assertEqual(result["gameplay_window_start"], 1001)
            self.assertEqual(result["core_animation_fps"]["median"], 60)
            self.assertEqual(result["in_app_summary"]["pipeline_compiles"], 1)
            self.assertEqual(result["in_app_perf"]["pipeline_compiles"]["mean"], 0)

    def test_data_push_creates_tree_and_scopes_cache_to_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            app = root / "game.app"
            game = app / "game"
            (game / "DATA").mkdir(parents=True)
            (game / "DATA/chunk.bin").write_bytes(b"guest data")
            (game / ".lbn-map").write_bytes(b"disc layout")
            (app / "Info.plist").write_bytes(__import__("plistlib").dumps({"CFBundleIdentifier": "example.game"}))
            state = {"game_manifest": device.game_manifest(game),
                     "game_manifest_target": {"udid": "other", "bundle": "example.signed"}}
            transfers = []

            def transfer(*args, **kwargs):
                self.assertEqual(args[:2], ("apps", "push"))
                self.assertEqual(args[4], "Documents")
                tree = pathlib.Path(args[3])
                self.assertEqual(tree.name, "game")
                self.assertEqual((tree / "DATA/chunk.bin").read_bytes(), b"guest data")
                self.assertTrue((tree / ".lbn-map").is_file())
                self.assertTrue((tree / ".rx-manifest.json").is_file())
                transfers.append(args)

            with patch.object(device, "ROOT", root), patch.object(device, "choose_udid", return_value="current"), \
                 patch.object(device, "installed_bundle", return_value="example.signed"), \
                 patch.object(device, "product_type", return_value="iPhone12,1"), \
                 patch.object(device, "state_read", return_value=state), patch.object(device, "state_write"), \
                 patch.object(device, "pmd", side_effect=transfer):
                device.do_push(SimpleNamespace(udid=None, app=str(app)))
            self.assertEqual(len(transfers), 1)
            self.assertEqual(state["game_manifest_target"], {"udid": "current", "bundle": "example.signed"})

    def test_h2_summary_refuses_stale_perf_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / "run.json").write_text(json.dumps({"launch_host_time": 2000}))
            perf = root / "perf"
            perf.mkdir()
            (perf / "frames.jsonl").write_text(
                '{"t":5000,"gameplay":true,"draws":10}\n'
                '{"type":"summary","t":5001,"wall_time":1001}\n')
            result = device.summarize(root)
            self.assertIsNone(result["gameplay_window_start"])
            self.assertNotIn("in_app_perf", result)


if __name__ == "__main__":
    unittest.main()
