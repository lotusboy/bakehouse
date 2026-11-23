"""Tests for the 0.15.0 review's findings on `bakehouse door`: F1 a path outside the code roots is never
fast; F2 a serious finding with no readable Site line stops fast; F4 an unknown door setting,
F5 a project config that is not an object, F7 fastMaxVisits not below lookVisits: each exit 2."""
import json
import subprocess
import unittest

import test_door
from test_door import BAKEHOUSE, finding


class DoorReview(test_door.Door):
    def run_door(self, *args):
        return subprocess.run([str(BAKEHOUSE), "door", *args], cwd=self.repo, capture_output=True, text=True,
                              env=self.env)

    def override(self, cfg):
        (self.repo / ".team" / "decision-config.json").write_text(json.dumps(cfg))

    def test_f1_outside_the_code_roots_is_not_fast(self):
        for n in range(1, 6):
            self.commit(f"A{n}: x", "docs/x.md")
        way, rules = self.route("docs/x.md")
        self.assertEqual((way, rules), ("normal", ["fast-counted"]))
        self.assertIn("not counted", self.run_door("docs/x.md").stdout)

    def test_f2_an_unreadable_serious_finding_stops_fast(self):
        self.review("A7", "## F1 (P0): bad\nNo site here.\n")
        way, rules = self.route("src/a.py")
        self.assertEqual((way, rules), ("normal", ["fast-findings"]))
        self.assertIn("no Site line", self.run_door("src/a.py").stdout)

    def test_f2_a_readable_finding_elsewhere_still_fast(self):
        self.review("A7", finding("F1", "P0", "src/b.py:1"))
        self.assertEqual(self.route("src/a.py")[0], "fast")

    def test_f4_an_unknown_door_setting_is_refused(self):
        self.override({"door": {"fastMaxFile": 0}})
        p = self.run_door("src/a.py")
        self.assertEqual(p.returncode, 2, p.stdout)
        self.assertIn("fastMaxFile", p.stderr)

    def test_f5_a_config_that_is_not_an_object_is_exit_2(self):
        self.override([])
        p = self.run_door("src/a.py")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_f7_fast_visits_must_be_below_look_visits(self):
        self.override({"door": {"fastMaxVisits": 3, "lookVisits": 3}})
        p = self.run_door("src/a.py")
        self.assertEqual(p.returncode, 2, p.stdout)
        self.assertIn("fastMaxVisits", p.stderr)
        self.override({"door": {"fastMaxVisits": 2, "lookVisits": 3}})
        self.assertEqual(self.run_door("src/a.py").returncode, 0)


def load_tests(loader, tests, pattern):
    """Only this file's own tests, not the ones inherited from test_door.Door."""
    own = sorted(n for n in DoorReview.__dict__ if n.startswith("test_"))
    return unittest.TestSuite(DoorReview(n) for n in own)


if __name__ == "__main__":
    unittest.main()
