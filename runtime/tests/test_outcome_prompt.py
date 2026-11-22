"""Tests for 0.14.2: the outcome line `decision.sh ask` prints at a second fix round, one --read-what per --read
file, and a correction's lesson stub with fields not given. Standard library only; every test writes to a scratch
project. python3 -m unittest runtime/tests/test_outcome_prompt.py
"""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_gate_events import RESULT, Scratch  # noqa: E402


class Ask(Scratch):
    """A scratch project; questions in "process", which needs no --read."""

    def setUp(self):
        super().setUp()
        self.decisions = self.records / "decisions" / "ledger.jsonl"

    def ask(self, *extra, task="T7", ok=True, env=None, category="process", q="Change the reader to keep the order?"):
        p = self.run_([str(self.rt / "decision.sh"), "ask", "--category", category, "--question", q,
                       "--option", "go on", "--option", "stop", "--recommendation", "go on", "--confidence", "0.6",
                       "--evidence-kind", "measured", "--evidence", "WIP.md",
                       *(["--task", task] if task else []), *extra], env=env)
        if ok:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def round_(self, n, *extra, **kw):
        why = "opened the source rows first"
        return self.ask("--fix-round", str(n), "--layer", "in", "--owners", "1", "--area", "reader",
                        "--upstream", why, *(["--rechecked", why] if n >= 2 else []),
                        *(["--stepped-back", why] if n >= 3 else []), *extra, **kw)

    def outcome(self, did, verdict="held"):
        p = self.run_([str(self.rt / "decision.sh"), "outcome", did, "--verdict", verdict, "--by", "the next run"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def rows(self):
        return [json.loads(x) for x in self.decisions.read_text().splitlines()] if self.decisions.exists() else []

    def lines(self, p):
        return [x for x in (p.stdout + p.stderr).splitlines() if "outcome:" in x]

    def line(self, n, did, earlier=None, task="T7"):
        was = f" (fix round {earlier})" if earlier else ""
        return (f"decision: outcome: fix round {n} of {task} follows {did}{was}, whose outcome is not recorded."
                f" When you know how it turned out: decision.sh outcome {did} --verdict held|reversed|refined"
                ' --by "<what showed it>"')


class OutcomeLine(Ask):
    def test_round_two_names_round_one_once_on_stderr_after_the_ask(self):
        one = self.round_(1)
        self.assertEqual(self.lines(one), [])
        did = one.stdout.strip()
        p = self.round_(2)
        self.assertEqual(self.lines(p), [self.line(2, did, 1)])
        self.assertEqual(p.stderr.count("decision: outcome:"), 1)
        self.assertRegex(p.stdout, r"\Ad\d{8}-[0-9a-f]{6}\n\Z")  # the id alone
        self.assertLess(p.stderr.index('{"kind": "ask"'), p.stderr.index("decision: outcome:"))

    def test_triggers_and_the_ledger_are_unchanged(self):
        self.round_(1)
        p = self.round_(2)
        self.assertEqual(len(self.lines(p)), 1)
        self.assertEqual([r["kind"] for r in self.rows()], ["ask", "ask"])
        one, two = self.rows()
        self.assertEqual(two["triggers"], ["upstream", "re-check"])
        self.assertEqual(set(one), set(two))
        self.assertNotIn("outcome", two)
        self.assertNotIn("outcome trigger", p.stderr)

    def test_round_three_names_round_two(self):
        self.round_(1)
        two = self.round_(2).stdout.strip()
        self.assertEqual(self.lines(self.round_(3)), [self.line(3, two, 2)])

    def test_the_latest_lower_round_by_time_and_not_by_place(self):
        two = self.round_(2, env={"DECISION_TS": "2026-01-02T10:00:00+00:00"}).stdout.strip()
        # backfilled later, for an earlier time
        self.round_(1, env={"DECISION_TS": "2026-01-01T10:00:00+00:00", "DECISION_BACKFILL": "1"})
        self.assertEqual(self.lines(self.round_(3)), [self.line(3, two, 2)])

    def test_a_round_is_named_before_a_later_ask_that_is_no_round(self):
        one = self.round_(1, env={"DECISION_TS": "2026-01-01T10:00:00+00:00"}).stdout.strip()
        self.ask(env={"DECISION_TS": "2026-01-02T10:00:00+00:00"})
        self.assertEqual(self.lines(self.round_(2)), [self.line(2, one, 1)])

    def test_a_round_not_below_this_one_is_not_named_as_a_round(self):
        self.round_(2, env={"DECISION_TS": "2026-01-02T10:00:00+00:00"})
        last = self.round_(3, env={"DECISION_TS": "2026-01-03T10:00:00+00:00"}).stdout.strip()
        p = self.round_(2, env={"DECISION_BACKFILL": "1", "DECISION_TS": "2026-01-04T10:00:00+00:00"})
        self.assertEqual(self.lines(p), [self.line(2, last)])

    def test_with_no_earlier_round_the_latest_earlier_ask_is_named_with_no_round(self):
        self.ask(env={"DECISION_TS": "2026-01-01T10:00:00+00:00"})
        last = self.ask(env={"DECISION_TS": "2026-01-02T10:00:00+00:00"}).stdout.strip()
        self.assertEqual(self.lines(self.round_(2)), [self.line(2, last)])

    def test_no_earlier_ask_and_another_tasks_asks_print_nothing(self):
        self.ask(task=None)
        self.round_(1, task="T8")
        self.ask(task="T70")
        self.assertEqual(self.lines(self.round_(2)), [])

    def test_a_backfilled_round_with_no_task_follows_nothing(self):
        self.ask(task=None)
        self.assertEqual(self.lines(self.round_(2, task=None, env={"DECISION_BACKFILL": "1"})), [])

    def test_the_task_matches_without_case(self):
        one = self.round_(1, task="t7").stdout.strip()
        self.assertEqual(self.lines(self.round_(2, task="T7")), [self.line(2, one, 1)])

    def test_an_outcome_of_any_verdict_silences_it(self):
        for n, verdict in enumerate(("held", "reversed", "refined")):
            task = f"T{n}"
            self.outcome(self.round_(1, task=task).stdout.strip(), verdict)
            self.assertEqual(self.lines(self.round_(2, task=task)), [], verdict)

    def test_an_outcome_on_another_decision_does_not(self):
        other = self.ask().stdout.strip()
        one = self.round_(1).stdout.strip()
        self.outcome(other)
        two = self.round_(2)
        self.assertEqual(self.lines(two), [self.line(2, one, 1)])
        # nor does it pass to an older decision once the latest has one
        self.outcome(two.stdout.strip())
        self.assertEqual(self.lines(self.round_(3)), [])

    def test_an_answer_is_no_outcome(self):
        one = self.round_(1).stdout.strip()
        self.run_([str(self.rt / "decision.sh"), "answer", one, "--choice", "go on", "--agreed", "yes"])
        self.assertEqual(self.lines(self.round_(2)), [self.line(2, one, 1)])

    def test_the_first_round_an_ask_with_no_round_and_a_recheck_print_nothing(self):
        self.ask()
        self.assertEqual(self.lines(self.round_(1)), [])
        self.assertEqual(self.lines(self.ask()), [])
        self.assertEqual(self.lines(self.ask("--recheck")), [])
        p = self.round_(2, "--recheck", env={"DECISION_BACKFILL": "1"})
        self.assertEqual(self.lines(p), [])

    def test_a_refused_ask_prints_nothing_and_the_next_one_prints(self):
        one = self.round_(1).stdout.strip()
        for extra in (("--upstream", "x"), ("--rechecked", "x"), ("--approves", "maybe"), ("--id", one)):
            p = self.round_(2, *extra, ok=False)
            self.assertNotEqual(p.returncode, 0)
            self.assertEqual(self.lines(p), [], extra)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.lines(self.round_(2)), [self.line(2, one, 1)])

    def test_the_same_line_for_an_autonomous_a_blind_and_a_backfilled_ask(self):
        for n, (extra, env) in enumerate(((("--autonomous",), None), (("--blind",), None),
                                          ((), {"DECISION_BACKFILL": "1"}))):
            task = f"T{n}"
            one = self.round_(1, *extra, task=task).stdout.strip()
            self.assertEqual(self.lines(self.round_(2, *extra, task=task, env=env)),
                             [self.line(2, one, 1, task)], extra)

    def test_the_lines_own_command_silences_it(self):
        one = self.round_(1).stdout.strip()
        (line,) = self.lines(self.round_(2))
        did = re.search(r"decision\.sh outcome (\S+) --verdict held", line)[1]
        self.assertEqual(did, one)
        two = self.rows()[-1]["id"]
        self.outcome(did)
        self.assertEqual(self.lines(self.round_(3)), [self.line(3, two, 2)])


class ReadWhat(Ask):
    def setUp(self):
        super().setUp()
        self.files = []
        for n in "abc":
            (self.root / f"{n}.csv").write_text("x\n")
            self.files.append(str(self.root / f"{n}.csv"))

    def read(self, files, *whats, ok=True, env=None):
        args = [x for f in self.files[:files] for x in ("--read", f)]
        return self.ask(*args, *(x for w in whats for x in ("--read-what", w)), category="data", ok=ok, env=env)

    def stored(self, files, *whats, **kw):
        self.read(files, *whats, **kw)
        read = self.rows()[-1]["read"]
        self.assertEqual([r["path"] for r in read], self.files[:files])
        self.assertTrue(all(set(r) == {"path", "what"} for r in read))
        return [r["what"] for r in read]

    def test_one_text_is_stored_against_every_file(self):
        self.assertEqual(self.stored(1, "rows 1-2"), ["rows 1-2"])
        self.assertEqual(self.stored(3, "rows 1-2"), ["rows 1-2"] * 3)

    def test_equal_counts_go_to_their_own_files_in_order(self):
        self.assertEqual(self.stored(2, "rows 1-2", "cell B3"), ["rows 1-2", "cell B3"])
        self.assertEqual(self.stored(3, "row c", "row a", "row b"), ["row c", "row a", "row b"])

    def test_any_other_count_stores_every_text_against_every_file(self):
        self.assertEqual(self.stored(1, "rows 1-2", "cell B3"), ["rows 1-2; cell B3"])
        self.assertEqual(self.stored(3, "rows 1-2", "cell B3"), ["rows 1-2; cell B3"] * 3)
        self.assertEqual(self.stored(2, "row a", "row b", "row c"), ["row a; row b; row c"] * 2)

    def test_none_or_a_meaningless_one_of_two_is_refused(self):
        for whats in ((), ("1-2",), ("rows 1-2", "7"), ("7", "rows 1-2"), ("", "rows 1-2")):
            p = self.read(2, *whats, ok=False)
            self.assertEqual(p.returncode, 1, whats)
            self.assertIn("--read-what is required", p.stderr)
        self.assertEqual(self.rows(), [])

    def test_where_none_is_needed_none_is_stored_as_null(self):
        self.ask("--read", self.files[0])  # "process" is exempt
        self.assertEqual(self.rows()[-1]["read"], [{"path": self.files[0], "what": None}])
        self.assertEqual(self.stored(2, env={"DECISION_BACKFILL": "1"}), [None, None])
        self.ask("--read-what", "rows 1-2", "--read-what", "cell B3")
        self.assertIsNone(self.rows()[-1]["read"])


class Correction(Scratch):
    def setUp(self):
        super().setUp()
        self.tools = self.records / "TOOLS.md"
        self.spend("wave", "open", "w1", "--ceiling", "100")
        self.record("--tokens", "60", "--kind", "engineer", *RESULT)
        self.tools.write_text("# TOOLS\n")

    def spend(self, *args, env=None):
        p = self.run_([str(self.rt / "spend.sh"), *args], env=env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def record(self, *args, env=None, agent="a"):
        return self.spend("record", "--agent", agent, "--label", "job", "--no-ops", *args, env=env)

    def stub(self):
        return re.findall(r"- \*\*(?:Why this tool|Predicted|Actual):\*\* (.*)", self.tools.read_text())

    def test_nothing_given(self):
        for outcome in ("worse", "failed"):
            self.tools.write_text("# TOOLS\n")
            before = (self.records / "spend" / "ledger.jsonl").read_text().count('"event": "agent"')
            p = self.record("--correct", "--tokens", "120", "--outcome", outcome)
            self.assertEqual(self.stub(), ["not given", "not given; tokens not given.",
                                           f"not given; 120 tokens (harness); outcome {outcome}."])
            self.assertNotIn("None", self.tools.read_text())
            self.assertIn("TOOLS.md: lesson stub added", p.stdout)
            self.assertIn("recorded a (job): 120 tokens", p.stdout)
            self.assertRegex(p.stdout, r"wave w1: .*120 / 100 \(120%\)")
            self.assertIn("STOP: wave w1", p.stderr)
            rows = [json.loads(x) for x in (self.records / "spend" / "ledger.jsonl").read_text().splitlines()]
            rows = [r for r in rows if r["event"] == "agent"]
            self.assertEqual(len(rows), before + 1)
            self.assertIsNone(rows[-1]["predictedTokens"])
            self.assertIsNone(rows[-1]["why"])

    def test_each_field_stands_when_given(self):
        given = (("--why", "w"), ("--predicted", "p"), ("--predicted-tokens", "1,200"), ("--result", "r"))
        want = (("w", "not given; tokens not given.", "not given"), ("not given", "p; tokens not given.", "not given"),
                ("not given", "not given; 1,200 tokens.", "not given"), ("not given", "not given; tokens not given.", "r"))
        for extra, (why, predicted, result) in zip(given, want):
            self.tools.write_text("# TOOLS\n")
            self.record("--correct", "--tokens", "70", "--outcome", "worse", *extra)
            self.assertEqual(self.stub(), [why, predicted, f"{result}; 70 tokens (harness); outcome worse."])
        self.tools.write_text("# TOOLS\n")
        self.record("--correct", "--tokens", "70", "--outcome", "worse", *(x for g in given for x in g))
        self.assertEqual(self.stub(), ["w", "p; 1,200 tokens.", "r; 70 tokens (harness); outcome worse."])

    def test_a_backfilled_first_row(self):
        p = self.record("--tokens", "10", "--outcome", "failed", "--note", "from the log", agent="b",
                        env={"SPEND_TS": "2026-01-02T10:00:00+00:00"})
        self.assertIn("wave w1:", p.stdout)
        self.assertEqual(self.stub()[:2], ["not given", "not given; tokens not given."])


if __name__ == "__main__":
    unittest.main()
