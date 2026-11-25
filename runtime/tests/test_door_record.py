"""Tests for 0.17.0 (a): `bakehouse door --task <id>` records its own route, one door event through ops-event.sh; never
with --as-of, never with paths only; a write that fails keeps the exit code and the route and says how to record it."""
import json
import subprocess
import unittest
from datetime import datetime

import test_door
from test_door import BAKEHOUSE


class DoorRecord(test_door.Door):
    def run_door(self, *args):
        return subprocess.run([str(BAKEHOUSE), "door", *args], cwd=self.repo, capture_output=True, text=True,
                              env=self.env)

    def doors(self):
        lines = (self.records() / "ops" / "events.jsonl").read_text().splitlines()
        return [e for e in map(json.loads, lines) if e["kind"] == "door"]

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
        self.assertTrue(line.endswith(f'; record it: ops-event.sh door A7 door - - - - - fast "{reasons}"'), line)
        j = subprocess.run([str(BAKEHOUSE), "door", "--json", "--task", "A7", "src/a.py"], cwd=self.repo,
                           capture_output=True, text=True, env=self.env)
        self.assertEqual((j.returncode, json.loads(j.stdout)["recorded"], json.loads(j.stdout)["route"]),
                         (0, False, "fast"))
        self.assertEqual(self.doors(), [])


def load_tests(loader, tests, pattern):
    """Only this file's own tests, not the ones inherited from test_door.Door."""
    return unittest.TestSuite(DoorRecord(n) for n in sorted(n for n in DoorRecord.__dict__ if n.startswith("test_")))


if __name__ == "__main__":
    unittest.main()
