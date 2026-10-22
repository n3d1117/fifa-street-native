"""Unit tests for the USB device harness profile selection and run summaries."""
import datetime as dt
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools/device"))
import device


class DeviceHarnessTests(unittest.TestCase):
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
            self.assertEqual(result["in_app_perf"]["avg_guest_fps"], 55)
            self.assertEqual(result["in_app_perf"]["guest_fps"]["mean"], 55)
            self.assertTrue((root / "summary.json").is_file())


if __name__ == "__main__":
    unittest.main()
