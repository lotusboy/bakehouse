"""Tests for 0.11.0: decision triggers shown and recorded, the altitude trigger, and altitude clusters by area
(docs/PROJECT-TEAM.md, "Decision triggers"). `decision.sh ask --change` declares that the question proposes a
code change on --task (`--fix-round` implies it); it then needs --layer in|out, --owners N and --area <name>.
Every trigger that applies prints one line `decision: <name> trigger: <why>`, pass or refuse, and the ask event
records them in `triggers`. Standard library only; every test writes to a scratch project.
python3 -m unittest runtime/tests/test_altitude_triggers.py
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_gate_events import RESULT, Scratch  # noqa: E402
import test_writer_files as twf  # noqa: E402 - a module, so its tests are not collected here again

ARCH = "architect check of the owning layer first"
OPTIONS = ("go on", "stop")


class Ask(Scratch):
    """A scratch project with one owned binding; questions in "process", which needs no --read."""

    def setUp(self):
        super().setUp()
        self.decisions = self.records / "decisions" / "ledger.jsonl"

    def ask(self, *extra, q="Change the reader to keep the column order?", task="T1", options=OPTIONS, ts=None,
            ok=True):
        args = [str(self.rt / "decision.sh"), "ask", "--category", "process", "--question", q]
        for o in options:
            args += ["--option", o]
        args += ["--recommendation", options[0], "--confidence", "0.6", "--evidence-kind", "measured",
                 "--evidence", "WIP.md"]
        if task:
            args += ["--task", task]
        p = self.run_([*args, *extra], env={"DECISION_TS": ts} if ts else None)
        if ok:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def change(self, layer, owners, area, *extra, **kw):
        return self.ask("--change", "--layer", layer, "--owners", str(owners), "--area", area, *extra, **kw)

    def rows(self):
        return [json.loads(x) for x in self.decisions.read_text().splitlines()] if self.decisions.exists() else []

    def lines(self, p, name=None):
        """The trigger lines of a run's stderr (of one trigger, when name is given)."""
        return [x for x in p.stderr.splitlines()
                if x.startswith("decision: ") and " trigger: " in x
                and (name is None or x.startswith(f"decision: {name} trigger: "))]

    def seal(self, task, ts):
        """A sealed event for task in the ops ledger, as predict-gate.sh seal writes one: the work order opens."""
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.open("a") as f:
            f.write(json.dumps({"ts": ts, "kind": "sealed", "task": task, "round": "first"}) + "\n")

    def architect(self, agent, task):
        p = self.run_([str(self.rt / "spend.sh"), "record", "--agent", agent, "--label", f"job {agent}",
                       "--tokens", "100", "--no-ops", "--kind", "architect", "--task", task, *RESULT])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


class Change(Ask):
    # test 1
    def test_change_needs_layer_owners_and_area_and_a_fix_round_implies_it(self):
        p = self.ask("--change", ok=False)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        for flag in ("--layer", "--owners", "--area"):
            self.assertIn(flag, p.stderr)
        self.assertEqual(self.refusals()[-1]["gate"], "decision:change")
        p = self.ask("--change", "--layer", "in", ok=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("--owners", p.stderr)
        self.assertNotIn("--layer", p.stderr.splitlines()[-1])
        # --fix-round implies --change
        p = self.ask("--fix-round", "1", "--upstream", "the upstream writer drops the column order", ok=False)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        for flag in ("--layer", "--owners", "--area"):
            self.assertIn(flag, p.stderr)
        self.assertEqual(self.rows(), [])
        self.ask("--fix-round", "1", "--upstream", "the upstream writer drops the column order",
                 "--layer", "in", "--owners", "1", "--area", "reader")
        row = self.rows()[-1]
        self.assertEqual((row["change"], row["fixRound"], row["layer"]), (True, 1, "in"))

    def test_change_needs_a_task_and_the_flags_need_change(self):
        p = self.ask("--change", "--layer", "in", "--owners", "1", "--area", "reader", task=None, ok=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("--change needs --task", p.stderr)
        p = self.ask("--layer", "out", "--owners", "2", "--area", "reader", ok=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("go with --change", p.stderr)
        p = self.ask("--change", "--layer", "in", "--owners", "0", "--area", "reader", ok=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("--owners counts from 1", p.stderr)
        p = self.ask("--change", "--layer", "in", "--owners", "1", "--area", "  ", ok=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("--area", p.stderr)
        self.assertEqual(self.rows(), [])

    # test 11
    def test_change_without_area_refuses_naming_it(self):
        p = self.ask("--change", "--layer", "in", "--owners", "1", ok=False)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        last = p.stderr.splitlines()[-1]
        self.assertIn("--area", last)
        self.assertNotIn("--layer", last)
        self.assertNotIn("--owners", last)
        self.assertEqual(self.rows(), [])

    # test 2
    def test_layer_in_and_one_owner_fire_nothing_and_are_recorded(self):
        p = self.change("in", 1, "reader")
        self.assertEqual(self.lines(p), [])
        row = self.rows()[-1]
        self.assertEqual({k: row[k] for k in ("change", "layer", "owners", "area", "triggers")},
                         {"change": True, "layer": "in", "owners": 1, "area": "reader", "triggers": []})


class Altitude(Ask):
    # test 3
    def test_the_trigger_names_which_and_refuses_without_an_architect_option(self):
        for layer, owners, why in (("out", 1, "layer out"), ("in", 2, "2 places own the rule"),
                                   ("out", 3, "both (layer out, 3 places own the rule)")):
            with self.subTest(why):
                p = self.change(layer, owners, f"area {layer}{owners}", ok=False)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertEqual(self.lines(p), [f"decision: altitude trigger: {why}"])
                last = p.stderr.splitlines()[-1]
                self.assertIn("architect check", last)
                self.assertIn("spend.sh record --kind architect --task T1", last)
                self.assertEqual(self.refusals()[-1]["gate"], "decision:altitude")
        self.assertEqual(self.rows(), [])

    # test 4
    def test_an_architect_check_option_lets_it_through_and_the_line_still_prints(self):
        for n, (layer, owners) in enumerate((("out", 1), ("in", 2), ("out", 2))):
            p = self.change(layer, owners, f"area {n}", options=("go on", ARCH), task=f"T{n}")
            self.assertEqual(len(self.lines(p, "altitude")), 1, p.stderr)
            self.assertEqual(self.rows()[-1]["triggers"], ["altitude"])
            self.assertEqual((self.rows()[-1]["layer"], self.rows()[-1]["owners"]), (layer, owners))

    def test_an_option_that_declines_the_architect_check_does_not_offer_one(self):
        for option in ("go on without an architect check", "skip the architect check", "no architect check"):
            with self.subTest(option):
                p = self.change("out", 1, "reader", options=("stop", option), ok=False)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.change("out", 1, "reader", options=("stop", "Architect's check of the reader layer"))

    # test 5
    def test_an_architect_run_recorded_after_the_work_order_opened_lets_it_through(self):
        self.seal("T1", "2000-01-01T00:00:00+00:00")
        p = self.change("out", 1, "reader", ok=False)
        self.assertEqual(p.returncode, 1, "no architect run yet")
        self.architect("ar1", "T1")
        p = self.change("out", 1, "reader")
        self.assertEqual(self.lines(p), ["decision: altitude trigger: layer out"])
        self.assertEqual(self.rows()[-1]["triggers"], ["altitude"])
        # a run recorded before the work order opened does not count
        self.architect("ar2", "T2")
        self.seal("T2", "2099-01-01T00:00:00+00:00")
        p = self.change("out", 1, "writer", task="T2", ok=False)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        # nor does one for a work order that never opened (no seal)
        self.architect("ar3", "T3")
        p = self.change("out", 1, "parser", task="T3", ok=False)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("no sealed event", p.stderr.splitlines()[-1])

    # test 6
    def test_no_task_or_no_change_brings_no_new_behaviour(self):
        p = self.ask(task=None)
        self.assertEqual(self.lines(p), [])
        self.assertNotIn("decision: note:", p.stderr)
        self.assertEqual(self.rows()[-1]["triggers"], [])
        self.assertFalse(self.rows()[-1]["change"])
        p = self.ask()  # the word "change" on a task, no --change: one note
        self.assertEqual(self.lines(p), [])
        self.assertEqual(p.stderr.count("decision: note: the question mentions a change on T1"), 1, p.stderr)
        self.assertIn("give --change, --layer, --owners and --area", p.stderr)
        p = self.change("in", 1, "reader")  # declared: no note
        self.assertNotIn("decision: note:", p.stderr)
        p = self.ask(q="Run the audit now?")
        self.assertNotIn("decision: note:", p.stderr)


class Triggers(Ask):
    UP = ("--upstream", "the upstream writer drops the column order")
    REC = ("--rechecked", "opened rows 1-2 of the source table")
    BACK = ("--stepped-back", "census of every input kind on every target; design covers all")
    IN = ("--layer", "in", "--owners", "1", "--area", "reader")

    def round_(self, n, *extra, ok=True):
        return self.ask("--fix-round", str(n), *self.IN, *extra, q=f"Go on the T1 fix round {n}?", ok=ok)

    # test 7
    def test_a_first_fix_round_prints_and_records_the_upstream_trigger(self):
        p = self.round_(1, ok=False)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("give --upstream", p.stderr)
        self.assertEqual(len(self.lines(p, "upstream")), 1, p.stderr)
        p = self.round_(1, *self.UP)
        self.assertEqual([x.split(" trigger:")[0] for x in self.lines(p)], ["decision: upstream"])
        self.assertIn("fix round 1 of T1", self.lines(p)[0])
        self.assertEqual(self.rows()[-1]["triggers"], ["upstream"])

    # test 8
    def test_rounds_two_and_three_print_and_record_re_check_and_step_back(self):
        self.round_(1, *self.UP)
        p = self.round_(2, *self.UP, ok=False)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(len(self.lines(p, "re-check")), 1, p.stderr)
        p = self.round_(2, *self.UP, *self.REC)
        self.assertEqual(len(self.lines(p, "re-check")), 1, p.stderr)
        self.assertEqual(self.rows()[-1]["triggers"], ["upstream", "re-check"])
        p = self.round_(3, *self.UP, *self.REC, ok=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("--stepped-back", p.stderr.splitlines()[-1])
        self.assertEqual(len(self.lines(p, "step-back")), 1, p.stderr)
        p = self.round_(3, *self.UP, *self.REC, *self.BACK)
        self.assertEqual([x.split(" trigger:")[0] for x in self.lines(p)],
                         ["decision: upstream", "decision: re-check", "decision: step-back"])
        self.assertEqual(self.rows()[-1]["triggers"], ["upstream", "re-check", "step-back"])

    def test_a_fix_round_and_the_altitude_trigger_both_record(self):
        p = self.ask("--fix-round", "1", *self.UP, "--layer", "out", "--owners", "1", "--area", "reader",
                     options=("go on", ARCH), q="Go on the T1 fix round 1?")
        self.assertEqual(len(self.lines(p)), 2, p.stderr)
        self.assertEqual(self.rows()[-1]["triggers"], ["upstream", "altitude"])

    # test 9
    def test_an_ask_that_fires_nothing_records_an_empty_list_and_older_asks_still_read(self):
        self.decisions.parent.mkdir(parents=True, exist_ok=True)
        old = {"kind": "ask", "id": "d20260101-aaaaaa", "ts": "2026-01-01T09:00:00+00:00", "stream": "work",
               "project": None, "category": "process", "question": "Old?", "options": ["a", "b"],
               "recommendation": "a", "confidence": 0.5, "evidence": {"kind": "measured", "pointer": "x"},
               "blind": False, "autonomous": False, "questionsRef": None, "read": None, "task": "T1",
               "fixRound": None, "rechecked": None, "upstream": None, "steppedBack": None, "recheck": False}
        self.decisions.write_text(json.dumps(old) + "\n")
        self.ask(q="Run the audit now?")
        self.assertEqual(self.rows()[-1]["triggers"], [])
        self.change("out", 2, "reader", options=("go on", ARCH))
        self.assertEqual(self.rows()[-1]["triggers"], ["altitude"])
        p = self.run_([str(self.rt / "decision.sh"), "review"])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Old?", p.stdout)


class Cluster(Ask):
    OWN = "decision: altitude trigger: cluster: two or more places own the rule on {area}, {n} times ({tasks})"
    OUT = "decision: altitude trigger: cluster: layer out on {area}, {n} times ({tasks})"

    def area_check(self, area):
        return ("go on", f"architect check of the {area} area (redesign mode)")

    # test 12
    def test_the_third_same_kind_trigger_on_one_area_is_a_cluster(self):
        self.change("in", 2, "row shape", options=("go on", ARCH), task="T1", ts="2026-01-02T10:00:00+00:00")
        self.change("in", 3, "row shape", options=("go on", ARCH), task="T2", ts="2026-01-02T10:05:00+00:00")
        p = self.change("in", 2, "row shape", options=("go on", ARCH), task="T3", ok=False)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(self.lines(p), [self.OWN.format(area="row shape", n=3, tasks="T1, T2, T3")])
        self.assertIn("architect check of row shape", p.stderr.splitlines()[-1])
        self.assertEqual(self.refusals()[-1]["gate"], "decision:altitude-cluster")
        p = self.change("in", 2, "row shape", options=self.area_check("row shape"), task="T3")
        self.assertEqual(self.lines(p), [self.OWN.format(area="row shape", n=3, tasks="T1, T2, T3")])
        self.assertEqual(self.rows()[-1]["triggers"], ["altitude", "altitude-cluster"])
        # the fourth is a cluster too, and names the count
        p = self.change("in", 2, "Row  Shape", options=self.area_check("row shape"), task="T4")
        self.assertEqual(self.lines(p), [self.OWN.format(area="Row  Shape", n=4, tasks="T1, T2, T3, T4")])

    # test 13
    def test_a_different_kind_or_area_does_not_count_and_both_count_for_each(self):
        arch = ("go on", ARCH)
        self.change("out", 1, "X", options=arch, task="T1")
        self.change("in", 2, "X", options=arch, task="T2")
        self.change("in", 2, "Y", options=arch, task="T3")
        self.change("out", 1, "Y", options=arch, task="T4")
        p = self.change("in", 2, "X", options=arch, task="T5")  # owners on X: T2 only before
        self.assertEqual(self.lines(p), ["decision: altitude trigger: 2 places own the rule"])
        self.assertEqual(self.rows()[-1]["triggers"], ["altitude"])
        # both kinds on X: layer out has one before (T1), two or more owners two (T2, T5)
        p = self.change("out", 2, "X", options=self.area_check("X"), task="T6")
        self.assertEqual(self.lines(p), [self.OWN.format(area="X", n=3, tasks="T2, T5, T6"),
                                         "decision: altitude trigger: layer out"])
        # the ask that fired both counts for layer out too
        p = self.change("out", 1, "X", options=arch, task="T7", ok=False)
        self.assertEqual(self.lines(p), [self.OUT.format(area="X", n=3, tasks="T1, T6, T7")])

    # test 14
    def test_a_recorded_architect_run_does_not_let_a_cluster_through(self):
        self.change("in", 2, "row shape", options=("go on", ARCH), task="T1")
        self.change("in", 2, "row shape", options=("go on", ARCH), task="T2")
        self.seal("T3", "2000-01-01T00:00:00+00:00")
        self.architect("ar1", "T3")
        p = self.change("in", 2, "row shape", task="T3", ok=False)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("a recorded architect run for one task does not count", p.stderr.splitlines()[-1])
        p = self.change("in", 2, "row shape", options=("go on", ARCH), task="T3", ok=False)
        self.assertEqual(p.returncode, 1, "an architect check that does not name the area is not one of the area")
        p = self.change("in", 2, "a new area", task="T3")  # a single trigger still goes through on the run
        self.assertEqual(self.rows()[-1]["triggers"], ["altitude"])

    # test 16
    def test_a_new_area_lists_the_areas_already_recorded(self):
        p = self.change("out", 1, "reader", options=("go on", ARCH), ts="2026-01-02T10:00:00+00:00")
        self.assertEqual(self.lines(p), ["decision: altitude trigger: layer out"])  # nothing recorded yet
        for n in range(1, 12):
            self.change("in", 1, f"area {n:02d}", ts=f"2026-01-02T11:{n:02d}:00+00:00")
        p = self.change("out", 1, "parser", options=("go on", ARCH), ts="2026-01-02T12:00:00+00:00")
        listed = ", ".join(f"area {n:02d}" for n in range(11, 1, -1))
        self.assertEqual(self.lines(p), [f"decision: altitude trigger: layer out; area 'parser' is new"
                                         f" (areas already recorded, most recent first: {listed})"])
        p = self.change("out", 1, "Reader", options=("go on", ARCH))  # an area already recorded: no list
        self.assertEqual(self.lines(p), ["decision: altitude trigger: layer out"])


class OtherWriters(twf.Base):
    """Test 15: in records format 2 the count reads every writer's file."""

    def change(self, task, *extra):
        return self.tool("decision.sh", "ask", "--repo", self.repo, "--category", "process", "--question",
                         "Change the reader to keep the column order?", "--option", "go on", "--option", ARCH,
                         "--recommendation", "go on", "--confidence", "0.6", "--evidence-kind", "measured",
                         "--evidence", "WIP.md", "--task", task, "--change", "--layer", "in", "--owners", "2",
                         "--area", "row shape", *extra)

    def test_asks_in_another_writers_file_count(self):
        self.migrate("--writer", "beta")
        for task in ("W-1", "W-2"):
            p = self.change(task)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(self.rows(self.wfile("decisions_ledger", "beta"))), 2)
        self.rgit("config", "bakehouse.writer", "alpha")
        p = self.change("W-3")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("decision: altitude trigger: cluster: two or more places own the rule on row shape, 3 times"
                      " (W-1, W-2, W-3)", p.stderr)
        self.assertEqual(self.rows(self.wfile("decisions_ledger", "alpha")), [])


if __name__ == "__main__":
    unittest.main()
