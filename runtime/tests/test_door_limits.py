"""Tests for 0.16.0's hard limits (runtime/bakehouse_door.py): the binding's careLimits; fast-limit, which a path that
matches careLimits.paths (or a folder holding a tracked file that does) fails; and --prediction, which raises a fast
route to normal when a predicted key matches careLimits.predictionKeys. Standard library only; each test builds a
scratch project in a temporary folder."""
import json
import subprocess
import unittest

import test_door
from test_door import BAKEHOUSE, TREE


class Limits(unittest.TestCase):
    # The scratch-project helpers of test_door.Door, without its tests.
    setUp, tearDown, git, project, records = (test_door.Door.setUp, test_door.Door.tearDown, test_door.Door.git,
                                              test_door.Door.project, test_door.Door.records)
    commit, day, review, ask, door, route = (test_door.Door.commit, test_door.Door.day, test_door.Door.review,
                                             test_door.Door.ask, test_door.Door.door, test_door.Door.route)

    def limits(self, value):
        (self.repo / ".team" / "binding.json").write_text(json.dumps({**TREE, "careLimits": value}))

    def run_door(self, *args):
        return subprocess.run([str(BAKEHOUSE), "door", *args], cwd=self.repo, capture_output=True, text=True,
                              env=self.env)

    def prediction(self, *keys):
        f = self.tmp / "moves.prediction.json"
        f.write_text(json.dumps({"moves": [{"what": k, "delta": 1} for k in keys], "mustNotMove": ["other:*"]}))
        return str(f)

    def reason(self, out, rule):
        return next(r for r in out["reasons"] if r["rule"] == rule)

    # ---------- careLimits.paths ----------

    def test_no_limits_passes(self):
        self.assertEqual(self.route("src/a.py")[0], "fast")
        self.assertIn("fast-limit", self.route("src/a.py")[1])

    def test_a_matching_path_fails_fast_limit(self):
        self.commit("start pricing", "src/pricing/rates.py", "src/pricing/notes.txt", days_ago=30)
        self.limits({"paths": ["src/pricing/**/*.py"]})
        out = self.door("src/pricing/rates.py")
        self.assertEqual((out["route"], [r["rule"] for r in out["reasons"]]), ("normal", ["fast-limit"]))
        self.assertEqual(self.reason(out, "fast-limit")["limits"], [["src/pricing/rates.py", "src/pricing/**/*.py"]])
        self.assertIn("src/pricing/**/*.py", self.run_door("src/pricing/rates.py").stdout)
        self.assertEqual(self.route("src/pricing/notes.txt")[0], "fast", "no match")
        self.assertEqual(self.route("src/a.py")[0], "fast")

    def test_a_folder_holding_a_matching_tracked_file_fails(self):
        self.commit("start pricing", "src/pricing/rates.py", "src/pricing/notes.txt", days_ago=30)
        self.limits({"paths": ["**/rates.py"]})
        out = self.door("src/pricing")
        self.assertEqual((out["route"], [r["rule"] for r in out["reasons"]]), ("normal", ["fast-limit"]))
        self.assertEqual(self.reason(out, "fast-limit")["limits"], [["src/pricing/rates.py", "**/rates.py"]])

    def test_a_new_file_that_matches_fails(self):
        self.limits({"paths": ["src/keys/*"]})
        self.assertEqual(self.route("src/keys/new.py"), ("normal", ["fast-limit"]))

    def test_bad_shapes_exit_2_naming_the_key(self):
        for value, named in (("src", "careLimits"), ({"paths": "src/**"}, "careLimits.paths"),
                             ({"paths": [3]}, "careLimits.paths"), ({"predictionKeys": [None]},
                                                                   "careLimits.predictionKeys"),
                             ({"predictionKeys": {"a": 1}}, "careLimits.predictionKeys")):
            self.limits(value)
            p = self.run_door("src/a.py")
            self.assertEqual(p.returncode, 2, (value, p.stdout))
            self.assertIn(named, p.stderr)
            self.assertNotIn("Traceback", p.stderr)

    # ---------- --prediction ----------

    def test_a_matching_key_raises_fast_to_normal(self):
        self.limits({"predictionKeys": ["premium:*", "hash:*"]})
        out = self.door("--prediction", self.prediction("count:notes", "premium:case-1"), "src/a.py")
        self.assertEqual(out["route"], "normal")
        r = self.reason(out, "raised-prediction")
        self.assertEqual((r["key"], r["pattern"]), ("premium:case-1", "premium:*"))
        text = self.run_door("--prediction", self.prediction("premium:case-1"), "src/a.py").stdout
        self.assertTrue(text.startswith("door: normal: "), text)
        self.assertIn("premium:case-1", text)
        self.assertIn("premium:*", text)

    def test_a_key_that_does_not_match_leaves_fast(self):
        self.limits({"predictionKeys": ["premium:*"]})
        self.assertEqual(self.route("--prediction", self.prediction("count:notes", "other:x"), "src/a.py")[0], "fast")
        self.limits({"paths": ["src/keys/*"]})
        self.assertEqual(self.route("--prediction", self.prediction("premium:case-1"), "src/a.py")[0], "fast",
                         "no predictionKeys: nothing raises")

    def test_it_never_changes_normal_or_look_around(self):
        self.limits({"predictionKeys": ["premium:*"]})
        moves = self.prediction("premium:case-1")
        self.assertEqual(self.route("--prediction", moves, "src/many"), ("normal", ["fast-files"]))
        self.commit("A1: x", "src/a.py")
        self.ask("A1", fixRound=2)
        self.assertEqual(self.route("--prediction", moves, "src/a.py"), ("look-around", ["look-fix-round"]))

    def test_a_prediction_that_cannot_be_read_exits_2(self):
        self.limits({"predictionKeys": ["premium:*"]})
        for text in (None, "not json", json.dumps({"moves": "x"})):
            f = self.tmp / "bad.json"
            if text is None:
                f.unlink(missing_ok=True)
            else:
                f.write_text(text)
            p = self.run_door("--prediction", str(f), "src/a.py")
            self.assertEqual(p.returncode, 2, (text, p.stdout))
            self.assertIn("--prediction", p.stderr)
            self.assertNotIn("Traceback", p.stderr)


if __name__ == "__main__":
    unittest.main()
