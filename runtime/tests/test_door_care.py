"""Tests for 0.16.0's door rules (runtime/bakehouse_door.py), care levels: only troubled work orders count, and serious
findings and fix rounds count in the window only. A troubled work order is a work order here that, inside the window,
asked a fix round of 2 or more, or has a run folder with a serious review finding. Standard library only; each test
builds a scratch project in a temporary folder."""
import json
import subprocess
import time
import unittest
from datetime import datetime

import test_door
from test_door import BAKEHOUSE, finding


class Care(unittest.TestCase):
    # The scratch-project helpers of test_door.Door, without its tests.
    setUp, tearDown, git, project, records = (test_door.Door.setUp, test_door.Door.tearDown, test_door.Door.git,
                                              test_door.Door.project, test_door.Door.records)
    commit, day, review, ask, door, route = (test_door.Door.commit, test_door.Door.day, test_door.Door.review,
                                             test_door.Door.ask, test_door.Door.door, test_door.Door.route)

    def override(self, **door):
        (self.repo / ".team" / "decision-config.json").write_text(json.dumps({"door": door}))

    def visits(self, *args):
        """(the route, the fast-visits or look-visits reason's figure and work orders)."""
        out = self.door(*args)
        r = [r for r in out["reasons"] if r["rule"] in ("fast-visits", "look-visits")]
        return out["route"], (r[0]["figure"], r[0]["workOrders"]) if r else None

    # ---------- troubled ----------

    def test_untroubled_work_orders_are_not_counted(self):
        for wo in ("A1", "A2", "A3"):
            self.commit(f"{wo}: x", "src/a.py")
        self.assertEqual(self.visits("src/a.py"), ("fast", (0, [])), "3 here, none troubled: not look-visits")

    def test_a_fix_round_2_ask_in_the_window_counts(self):
        self.override(lookFixRound=5)
        self.commit("A1: x", "src/a.py")
        self.commit("A2: x", "src/a.py")
        self.ask("A1", fixRound=1)
        self.assertEqual(self.visits("src/a.py"), ("fast", (0, [])), "fix round 1 is not troubled")
        self.ask("A1", fixRound=2)
        self.assertEqual(self.visits("src/a.py"), ("fast", (1, ["A1"])))
        self.ask("A2", fixRound=2)
        self.assertEqual(self.visits("src/a.py"), ("normal", (2, ["A2", "A1"])), "2 troubled: above fastMaxVisits 1")
        self.commit("A3: x", "src/a.py")
        self.ask("A3", fixRound=3)
        self.assertEqual(self.route("src/a.py"), ("look-around", ["look-visits"]))

    def test_a_fix_round_2_ask_outside_the_window_does_not(self):
        self.commit("A1: x", "src/a.py")
        self.ask("A1", days_ago=20, fixRound=3)
        self.assertEqual(self.visits("src/a.py"), ("fast", (0, [])), "not troubled, and not look-fix-round")

    def test_a_findings_fix_file_outside_the_window_does_not(self):
        self.commit("A1: x", "src/a.py")
        self.review("A1", "", days_ago=20, file="findings-fix3.md")
        self.assertEqual(self.visits("src/a.py"), ("fast", (0, [])))
        self.review("A1", "", days_ago=2, file="findings-fix2.md")
        self.assertEqual(self.route("src/a.py"), ("look-around", ["look-fix-round"]))

    def test_a_serious_finding_in_the_window_counts(self):
        self.override(lookFindingOrders=5)
        self.commit("A1: x", "src/a.py")
        self.commit("A2: x", "src/a.py")
        self.review("A1", finding("F1", "P2", "src/z.py:3"))  # sites on another file: fast-findings stays out
        self.assertEqual(self.visits("src/a.py"), ("fast", (0, [])), "P2 is not serious")
        self.review("A1", finding("F2", "P1", "src/z.py:3"))
        self.assertEqual(self.visits("src/a.py"), ("fast", (1, ["A1"])), "a serious finding at any site: troubled")
        self.review("A2", finding("F1", "P0", "src/z.py:5"))
        self.assertEqual(self.visits("src/a.py"), ("normal", (2, ["A2", "A1"])))

    def test_a_serious_finding_outside_the_window_does_not(self):
        self.commit("A1: x", "src/a.py")
        self.commit("A2: x", "src/a.py")
        self.review("A1", finding("F1", "P1", "src/a.py:3"), days_ago=20)
        self.review("A2", finding("F1", "P0", "src/a.py:5"), days_ago=20)
        out = self.door("src/a.py")
        self.assertEqual(out["route"], "fast", "old findings: not troubled, and not look-findings")
        self.assertEqual(out["ground"]["paths"][0]["findings"]["count"], 2, "ground still shows them")

    def test_a_finding_with_no_site_does_not_stop_fast(self):
        self.review("A7", "## F1 (P0): bad\nNo site here.\n")
        out = self.door("src/a.py")
        self.assertEqual(out["route"], "fast")
        self.assertEqual(out["ground"]["findingsUnread"], 1, "the unread count is still printed")
        self.assertNotIn("fast-findings", [r["rule"] for r in out["reasons"]])
        self.assertNotIn("fast-fix-round", [r["rule"] for r in out["reasons"]])

    def test_a_finding_on_the_paths_by_a_work_order_not_here_stops_fast(self):
        # owner, 2026-11-24 11:10: fast keeps no serious finding on these paths in the window, by any work order
        self.review("A5", finding("F1", "P1", "src/a.py:3"))
        out = self.door("src/a.py")
        self.assertEqual((out["route"], [r["rule"] for r in out["reasons"]]), ("normal", ["fast-findings"]))
        self.assertEqual((out["reasons"][0]["figure"], out["reasons"][0]["workOrders"]), (1, ["A5"]))
        self.assertEqual(out["ground"]["paths"][0]["workOrders"], [], "A5 is not here")
        text = subprocess.run([str(BAKEHOUSE), "door", "src/a.py"], cwd=self.repo, capture_output=True, text=True,
                              env=self.env)
        self.assertIn("serious review findings on these paths in 14 days (A5)", text.stdout)
        self.assertEqual(self.route("src/b.py")[0], "fast", "a finding on another file does not stop fast")

    def test_a_finding_on_the_paths_older_than_the_window_does_not_stop_fast(self):
        self.review("A5", finding("F1", "P1", "src/a.py:3"), days_ago=20)
        self.assertEqual(self.route("src/a.py"), ("fast", ["fast-files", "fast-visits", "fast-limit"]))

    def test_the_fast_reasons(self):
        self.assertEqual(self.route("src/a.py", "src/b.py"), ("fast", ["fast-files", "fast-visits", "fast-limit"]))

    # ---------- --as-of ----------

    def test_as_of_hides_a_later_ask_and_a_later_run_folder(self):
        self.override(lookFixRound=5, lookFindingOrders=5)
        self.commit("A1: x", "src/a.py", days_ago=6)
        self.commit("A2: x", "src/a.py", days_ago=6)
        self.ask("A1", days_ago=1, fixRound=2)
        self.review("A2", finding("F1", "P1", "src/a.py:3"), days_ago=1)
        self.assertEqual(self.visits("src/a.py"), ("normal", (2, ["A2", "A1"])))
        before = datetime.fromtimestamp(time.time() - 4 * 86400).astimezone().isoformat(timespec="seconds")
        self.assertEqual(self.visits("--as-of", before, "src/a.py"), ("fast", (0, [])))

    # ---------- the text ----------

    def test_the_text_says_troubled(self):
        self.override(lookFixRound=5)
        self.commit("A1: x", "src/a.py")
        self.ask("A1", fixRound=2)
        text = subprocess.run([str(BAKEHOUSE), "door", "src/a.py"], cwd=self.repo, capture_output=True, text=True,
                              env=self.env).stdout
        self.assertIn("1 troubled work order here in 14 days (A1) [fastMaxVisits 1]", text)


if __name__ == "__main__":
    unittest.main()
