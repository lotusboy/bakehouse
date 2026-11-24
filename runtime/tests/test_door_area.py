"""Tests for `bakehouse door`'s one architect look per area (owner's yes, 2026-11-23): a look-around is covered when a
work order here, or the work order at the door, has an architect run in the window; and the boundaries the 0.15.0
mutants found untested: exactly fastMaxFiles files, an old finding by one work order, a path git does not track."""
import json
import time
import unittest
from datetime import datetime

import test_door
from test_door import finding


class DoorArea(test_door.Door):
    def architect(self, task, days_ago=1, kind="architect"):
        ts = datetime.fromtimestamp(time.time() - days_ago * 86400).astimezone().isoformat(timespec="seconds")
        ledger = self.records() / "spend" / "ledger.jsonl"
        ledger.parent.mkdir(exist_ok=True)
        with ledger.open("a") as out:
            out.write(json.dumps({"ts": ts, "event": "agent", "agent": f"a{time.time_ns()}", "label": "architect: x",
                                  "tokens": 30000, "wave": None, "kind": kind, "source": "harness",
                                  "task": task, "model": "opus", "note": None, "corrects": False,
                                  "backfilled": False}) + "\n")

    def busy(self):
        for n in (1, 2, 3):
            self.commit(f"A{n}: x", "src/a.py")
            self.trouble(f"A{n}")  # 0.16.0: was 3 visits gives look-around, now 3 troubled visits (owner's care-level rules)

    def covered(self, *args):
        out = self.door(*args)
        self.assertEqual(out["route"], "look-around")
        return [r["workOrders"] for r in out["reasons"] if r["rule"] == "look-covered"]

    def test_a_recent_look_here_covers_it(self):
        self.busy()
        self.assertEqual(self.covered("src/a.py"), [])
        self.architect("A2", days_ago=3)
        self.assertEqual(self.covered("src/a.py"), [["A2"]])
        self.assertIn("architect look", self.run_text("src/a.py"))

    def test_another_kind_of_run_does_not(self):
        self.busy()
        self.architect("A2", kind="agent")
        self.assertEqual(self.covered("src/a.py"), [])

    def test_an_old_look_does_not(self):
        self.busy()
        self.architect("A2", days_ago=20)
        self.assertEqual(self.covered("src/a.py"), [])

    def test_a_look_elsewhere_does_not(self):
        self.busy()
        self.commit("A9: x", "src/b.py")
        self.architect("A9")
        self.assertEqual(self.covered("src/a.py"), [])

    def test_the_work_order_at_the_door_counts(self):
        self.busy()
        self.architect("A7")
        self.assertEqual(self.covered("--task", "A7", "src/a.py"), [["A7"]])

    def test_as_of_before_the_look(self):
        self.busy()
        self.architect("A2", days_ago=0)
        asof = datetime.fromtimestamp(time.time() - 3600 * 6).strftime("%Y-%m-%dT%H:%M:%S")
        self.assertEqual(self.covered("--as-of", asof, "src/a.py"), [])

    def test_a_look_does_not_change_fast(self):
        self.architect("A1")
        self.commit("A1: x", "src/a.py")
        self.assertEqual(self.route("src/a.py")[0], "fast")

    # ---------- boundaries the mutants found untested ----------

    def test_exactly_fast_max_files_is_fast(self):
        self.assertEqual(self.route("src/a.py", "src/b.py", "src/many/one.py")[0], "fast")

    def test_an_old_finding_by_one_work_order_is_still_fast(self):
        self.review("A2", finding("F1", "P1", "src/a.py:3"), days_ago=40)
        self.assertEqual(self.route("src/a.py")[0], "fast")

    def test_a_path_git_does_not_track_counts_as_one(self):
        self.assertEqual(self.route("src/a.py", "src/b.py", "src/many/one.py", "src/new.py"),
                         ("normal", ["fast-files"]))

    def run_text(self, *args):
        import subprocess
        return subprocess.run([str(test_door.BAKEHOUSE), "door", *args], cwd=self.repo, capture_output=True,
                              text=True, env=self.env).stdout


def load_tests(loader, tests, pattern):
    """Only this file's own tests, not the ones inherited from test_door.Door."""
    return unittest.TestSuite(DoorArea(n) for n in sorted(n for n in DoorArea.__dict__ if n.startswith("test_")))


if __name__ == "__main__":
    unittest.main()
