"""Tests for the device experiment HTML fragment renderer."""
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools/device"))
import report


class ReportTests(unittest.TestCase):
    def test_fragment_structure_and_css_tokens(self):
        fragment = report.render(
            [{"ts": "2026-01-01T00:00:00Z", "kind": "baseline", "label": "baseline",
              "git_sha": "0123456789abcdef", "control": {}, "metrics": {"fps": 52.0},
              "run_dirs": ["build/device-runs/example"]}],
            [{"id": "baseline", "title": "Fixture baseline", "category": "measurement",
              "status": "baseline", "ab_label": "baseline"}],
            {"items": []}, updated="fixture time")
        self.assertNotRegex(fragment.lower(), r"<(?:html|body)(?:\s|>)")
        self.assertIn("<title>FIFA iPhone Perf Lab</title>", fragment)
        css = fragment.split("<style>", 1)[1].split("</style>", 1)[0]
        outside_tokens = re.sub(r":root\s*\{[^}]*\}|@media[^{}]*\{\s*:root[^{}]*\{[^}]*\}\s*\}|:root\[data-theme=\"dark\"\]\s*\{[^}]*\}", "", css)
        for declaration in re.findall(r"([\w-]+)\s*:\s*([^;{}]+)", outside_tokens):
            prop, value = declaration
            if prop in {"color", "background", "background-color", "border", "border-color", "outline", "fill", "stroke"}:
                self.assertIn("var(", value, f"non-token color declaration: {prop}: {value}")


if __name__ == "__main__":
    unittest.main()
