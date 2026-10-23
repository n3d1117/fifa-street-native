"""Unit tests for the USB device harness profile selection and run summaries."""
import datetime as dt
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools/device"))
import device


class DeviceHarnessTests(unittest.TestCase):
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
        self.assertEqual(metrics["fps"], 2.0)
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
