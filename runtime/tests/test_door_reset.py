"""Tests for 0.17.0 (d): an architect look resets the area's count. With a look in the window (latest at T), trouble
counts only from T: asks recorded at or after T, run folders dated on or after T's day. No look: as 0.16.1."""
import json
import subprocess
import time
import unittest
from datetime import datetime

import test_door_area
from test_door import BAKEHOUSE


def iso(t):
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


class DoorReset(test_door_area.DoorArea):
    def setUp(self):
        super().setUp()
        self.t = int(time.time() - 3 * 86400)  # the look, 3 days ago, to the second

    def look(self, task="A2", t=None):
        self.architect(task, days_ago=(time.time() - (t or self.t)) / 86400)
        rows = (self.records() / "spend" / "ledger.jsonl").read_text().splitlines()
        last = json.loads(rows[-1])
        last["ts"] = iso(t or self.t)  # exactly T, whatever the clock did between the two reads
        (self.records() / "spend" / "ledger.jsonl").write_text("\n".join(rows[:-1] + [json.dumps(last)]) + "\n")

    def ask_at(self, task, t, **fields):
        ledger = self.records() / "decisions" / "ledger.jsonl"
        ledger.parent.mkdir(exist_ok=True)
        with ledger.open("a") as out:
            out.write(json.dumps({"kind": "ask", "id": f"d{time.time_ns()}", "ts": iso(t), "task": task,
                                  "question": "q", **fields}) + "\n")

    def folder_day(self, t):
        return time.strftime("%Y-%m-%d", time.localtime(t))

    def busy_before(self):
        """A1 to A3 troubled 5 days ago: before the look."""
        for n in (1, 2, 3):
            self.commit(f"A{n}: x", "src/a.py")
            self.trouble(f"A{n}", days_ago=5)

    def text(self, *args):
        return subprocess.run([str(BAKEHOUSE), "door", *args], cwd=self.repo, capture_output=True, text=True,
                              env=self.env).stdout

    def test_trouble_all_before_the_look_routes_fast(self):
        self.busy_before()
        self.assertEqual(self.route("src/a.py"), ("look-around", ["look-visits"]), "no look: as 0.16.1")
        self.look()
        out = self.door("src/a.py")
        self.assertEqual((out["route"], [r["rule"] for r in out["reasons"]]),
                         ("fast", ["fast-files", "fast-visits", "fast-limit", "reset"]))
        self.assertEqual(out["countedFrom"], iso(self.t))
        reset = out["reasons"][-1]
        self.assertEqual((reset["workOrders"], reset["since"]), (["A2"], iso(self.t)))
        self.assertIn(f"trouble counted from {iso(self.t)}", self.text("src/a.py"))

    def test_trouble_before_the_look_and_some_after_routes_normal(self):
        self.busy_before()
        for wo in ("A4", "A5"):
            self.commit(f"{wo}: x", "src/a.py")
            self.trouble(wo, days_ago=1)
        self.look()
        self.assertEqual(self.route("src/a.py"), ("normal", ["fast-visits", "reset"]))

    def test_trouble_after_the_look_keeps_look_around(self):
        self.busy()  # troubled 1 day ago: after the look
        self.look()
        out = self.door("src/a.py")
        self.assertEqual([r["rule"] for r in out["reasons"]], ["look-visits", "look-covered"])
        self.assertEqual((out["reasons"][-1]["since"], out["countedFrom"]), (iso(self.t), iso(self.t)))
        self.assertIn(f"covered by an architect look in 14 days (A2): trouble counted from {iso(self.t)}",
                      self.text("src/a.py"))

    def test_an_ask_at_exactly_t_counts(self):
        self.commit("A1: x", "src/a.py")
        self.look()
        self.ask_at("A1", self.t - 1, fixRound=2)
        self.assertEqual(self.route("src/a.py")[0], "fast", "an ask a second before T does not count")
        self.ask_at("A1", self.t, fixRound=2)
        self.assertEqual(self.route("src/a.py"), ("look-around", ["look-fix-round", "look-covered"]))

    def test_an_altitude_ask_before_t_does_not_count(self):
        self.commit("A1: x", "src/a.py")
        self.ask_at("A1", self.t - 60, layer="out", owners=1)
        self.assertEqual(self.route("src/a.py"), ("look-around", ["look-altitude"]))
        self.look()
        self.assertEqual(self.route("src/a.py")[0], "fast")

    def test_a_run_folder_dated_t_s_day_counts(self):
        self.commit("A1: x", "src/a.py")
        self.look()
        run = self.repo / "notes" / "done" / f"{self.folder_day(self.t - 86400)}-A1-review"
        run.mkdir(parents=True)
        (run / "findings-fix2.md").write_text("# Findings\n")
        self.assertEqual(self.route("src/a.py")[0], "fast", "a folder dated the day before T's does not count")
        run.rename(run.with_name(f"{self.folder_day(self.t)}-A1-review"))
        self.assertEqual(self.route("src/a.py"), ("look-around", ["look-fix-round", "look-covered"]))

    def test_the_latest_look_is_t(self):
        self.busy_before()
        self.commit("A4: x", "src/a.py")
        self.look("A1", t=self.t - 86400 * 4)  # before A1 to A3's trouble
        self.assertEqual(self.route("src/a.py")[0], "look-around")
        self.look("A4")
        out = self.door("src/a.py")
        self.assertEqual((out["route"], out["countedFrom"], out["reasons"][-1]["workOrders"]),
                         ("fast", iso(self.t), ["A4"]))

    def test_a_look_on_a_quiet_area_changes_nothing_but_counted_from(self):
        self.commit("A1: x", "src/a.py")
        self.look("A1")
        out = self.door("src/a.py")
        self.assertEqual((out["route"], [r["rule"] for r in out["reasons"]], out["countedFrom"]),
                         ("fast", ["fast-files", "fast-visits", "fast-limit"], iso(self.t)))

    def test_no_look_counted_from_is_null(self):
        self.busy()
        out = self.door("src/a.py")
        self.assertEqual((out["route"], out["countedFrom"]), ("look-around", None))
        self.assertNotIn("since", json.dumps(out["reasons"]))


def load_tests(loader, tests, pattern):
    """Only this file's own tests, not the ones inherited."""
    return unittest.TestSuite(DoorReset(n) for n in sorted(n for n in DoorReset.__dict__ if n.startswith("test_")))


if __name__ == "__main__":
    unittest.main()
