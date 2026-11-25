"""Tests for 0.17.0 (a): `bakehouse door --task <id>` records its own route, one door event through ops-event.sh; never
with --as-of, never with paths only; a write that fails keeps the exit code and the route and says how to record it."""
import json
import shlex
import subprocess
import unittest
from datetime import datetime

import test_door
from test_door import BAKEHOUSE, ROOT, TREE

KEPT = "kept look-around: the door now gives fast; a work order keeps the route it started with"


class DoorRecord(test_door.Door):
    def run_door(self, *args):
        return subprocess.run([str(BAKEHOUSE), "door", *args], cwd=self.repo, capture_output=True, text=True,
                              env=self.env)

    def doors(self):
        lines = (self.records() / "ops" / "events.jsonl").read_text().splitlines()
        return [e for e in map(json.loads, lines) if e["kind"] == "door"]

    def by_hand(self, route, task="A7"):
        p = subprocess.run([str(ROOT / "runtime" / "ops-event.sh"), "door", task, "door", "-", "-", "-", "-", "-", route,
                            "raised from fast: x"], cwd=self.repo, capture_output=True, text=True,
                           env={**self.env, "OPS_REPO": str(self.repo)})
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_task_records_one_event_with_the_route_and_reasons(self):
        self.commit("A1: x", "src/a.py")
        p = self.run_door("--task", "A7", "src/a.py")
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertTrue(p.stdout.startswith("door: fast: "), p.stdout)
        (ev,) = self.doors()
        self.assertEqual((ev["task"], ev["purpose"], ev["outcome"], ev["note"]),
                         ("A7", "door", "fast", p.stdout.splitlines()[0][len("door: fast: "):]))

    def test_json_says_recorded(self):
        out = self.door("--task", "A7", "src/many")
        self.assertEqual((out["route"], out["recorded"]), ("normal", True))
        (ev,) = self.doors()
        self.assertEqual((ev["task"], ev["outcome"]), ("A7", "normal"))

    def test_as_of_records_none(self):
        asof = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        out = self.door("--task", "A7", "--as-of", asof, "src/a.py")
        self.assertFalse(out["recorded"])
        self.assertEqual(self.doors(), [])

    def test_paths_only_records_none(self):
        out = self.door("src/a.py")
        self.assertFalse(out["recorded"])
        self.assertEqual(self.doors(), [])
        self.assertEqual(self.run_door("src/a.py").stderr, "")
        self.assertEqual(self.doors(), [])

    def test_with_prediction_records_too(self):
        moves = self.tmp / "moves.prediction.json"
        moves.write_text(json.dumps({"noMoves": True}))
        out = self.door("--task", "A7", "--prediction", str(moves), "src/a.py")
        self.assertEqual((out["route"], out["recorded"]), ("fast", True))
        self.assertEqual([e["outcome"] for e in self.doors()], ["fast"])

    def test_a_write_that_fails_keeps_the_exit_code_and_the_route(self):
        self.commit("A1: x", "src/a.py")
        quiet = self.run_door("src/a.py")
        (self.records() / "ops" / ".events.jsonl.lock").mkdir()  # the writer cannot take its lock: it fails
        p = self.run_door("--task", "A7", "src/a.py")
        self.assertEqual((p.returncode, p.stdout), (0, quiet.stdout))
        reasons = quiet.stdout.splitlines()[0][len("door: fast: "):]
        (line,) = p.stderr.splitlines()
        self.assertTrue(line.startswith("bakehouse door: the route was not recorded ("), line)
        self.assertTrue(line.endswith(f"; record it: ops-event.sh door A7 door - - - - - fast {shlex.quote(reasons)}"), line)
        j = subprocess.run([str(BAKEHOUSE), "door", "--json", "--task", "A7", "src/a.py"], cwd=self.repo,
                           capture_output=True, text=True, env=self.env)
        self.assertEqual((j.returncode, json.loads(j.stdout)["recorded"], json.loads(j.stdout)["route"]),
                         (0, False, "fast"))
        self.assertEqual(self.doors(), [])

    def test_a_lower_route_keeps_the_latest_one(self):
        self.by_hand("look-around")  # a PM raise, or an earlier look-around
        p = self.run_door("--task", "A7", "src/a.py")
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        first, kept = p.stdout.splitlines()[:2]
        self.assertTrue(first.startswith("door: fast: "), first)
        self.assertEqual(kept, f"door: {KEPT}")
        ev = self.doors()[-1]
        self.assertEqual(ev["outcome"], "look-around")
        self.assertTrue(ev["note"].startswith(KEPT), ev["note"])
        out = self.door("--task", "A7", "src/a.py")
        self.assertEqual((out["route"], out["kept"], out["recorded"]), ("fast", "look-around", True))
        self.assertEqual([e["outcome"] for e in self.doors()], ["look-around"] * 3)

    def test_a_higher_or_equal_route_is_recorded_as_now(self):
        self.by_hand("fast")
        out = self.door("--task", "A7", "src/many")
        self.assertEqual((out["route"], out["kept"]), ("normal", None))
        out = self.door("--task", "A7", "src/many")
        self.assertEqual((out["route"], out["kept"]), ("normal", None))
        self.assertEqual([e["outcome"] for e in self.doors()], ["fast", "normal", "normal"])
        self.assertFalse(self.doors()[-1]["note"].startswith("kept"))

    def test_kept_is_null_with_nothing_to_record(self):
        self.by_hand("look-around")
        self.assertIsNone(self.door("src/a.py")["kept"])

    def test_a_binding_not_owned_tries_no_write(self):
        repo = self.project("plain", {k: v for k, v in TREE.items() if k != "owned"})
        self.commit("start", "src/a.py", repo=repo, days_ago=30)
        p = subprocess.run([str(BAKEHOUSE), "door", "--task", "A7", "src/a.py"], cwd=repo, capture_output=True,
                           text=True, env=self.env)
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertTrue(p.stdout.startswith("door: fast: "), p.stdout)
        out = self.door("--task", "A7", "src/a.py", cwd=repo)
        self.assertEqual((out["recorded"], out["kept"]), (False, None))
        self.assertEqual((self.records(repo) / "ops" / "events.jsonl").read_text(), "")


def load_tests(loader, tests, pattern):
    """Only this file's own tests, not the ones inherited from test_door.Door."""
    return unittest.TestSuite(DoorRecord(n) for n in sorted(n for n in DoorRecord.__dict__ if n.startswith("test_")))


if __name__ == "__main__":
    unittest.main()
