"""Tests for runtime/decision.sh. Standard library only:
python3 -m unittest discover -s runtime/tests"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_gate_events import Scratch  # noqa: E402


class Ledger(Scratch):
    """A scratch project (test_gate_events.Scratch); the decision ledger is in its records worktree."""

    def setUp(self):
        super().setUp()
        self.env["DECISION_NOW"] = "2026-10-10T12:00:00+01:00"
        self.decisions = self.records / "decisions" / "ledger.jsonl"
        self.src = self.work / "rates.csv"
        self.src.write_text("Band,Rate\n1 to 25,0.5\n")
        self.grounded = ["--read", str(self.src), "--read-what", "rows 1-2"]

    def run_(self, *args, env=None, ok=True):
        p = subprocess.run([str(self.rt / "decision.sh"), *args], cwd=self.work,
                           capture_output=True, text=True, env=dict(self.env, **(env or {})))
        if ok:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def ask(self, cat="spend", conf=0.9, ts="2026-10-10T10:00:00+01:00", **kw):
        args = ["ask", "--category", cat, "--question", kw.get("q", "Run it?"), "--option", "yes",
                "--option", "no", "--recommendation", "yes", "--confidence", str(conf),
                "--evidence-kind", "measured", "--evidence", "WIP.md"]
        args += kw.get("grounding", self.grounded) + kw.get("extra", [])
        return self.run_(*args, env={"DECISION_TS": ts}).stdout.strip()

    def rows(self):
        return [json.loads(l) for l in self.decisions.read_text().splitlines()]

    def score(self, *extra):
        return json.loads(self.run_("score", "--json", *extra).stdout)

    # --- the lock (V12c step 2a, item 11) ---
    def test_the_lock_holds_while_a_checkout_swaps_the_ledger_file(self):
        import fcntl
        import time

        self.ask()
        lock = self.decisions.with_name(".ledger.jsonl.lock")
        self.assertTrue(lock.exists(), "the lock is a file of its own beside the ledger")
        with lock.open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            swapped = self.decisions.with_name("swap.tmp")
            swapped.write_bytes(self.decisions.read_bytes())
            os.replace(swapped, self.decisions)  # a new inode at the same path, as git checkout writes
            args = ["ask", "--category", "spend", "--question", "Run it again?", "--option", "yes", "--option",
                    "no", "--recommendation", "yes", "--confidence", "0.9", "--evidence-kind", "measured",
                    "--evidence", "WIP.md", *self.grounded]
            p = subprocess.Popen([str(self.rt / "decision.sh"), *args], cwd=self.work,
                                 env=dict(self.env, DECISION_TS="2026-10-10T10:00:00+01:00"),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.5)
            self.assertIsNone(p.poll(), "a writer took the lock on the new ledger file while it was held")
        self.assertEqual(p.wait(timeout=30), 0)
        self.assertEqual(len(self.rows()), 2)

    # --- record ---
    def test_ask_answer_outcome_append_to_the_projects_ledger(self):
        did = self.ask()
        self.run_("answer", did, "--choice", "yes")
        self.run_("outcome", did, "--verdict", "held", "--by", "the run finished")
        kinds = [(r["kind"], r["id"]) for r in self.rows()]
        self.assertEqual(kinds, [("ask", did), ("answer", did), ("outcome", did)])
        self.assertTrue(self.rows()[1]["agreed"])
        self.assertEqual(self.rows()[0]["stream"], "work")

    def test_ask_records_the_approving_option_and_refuses_one_not_offered(self):
        self.ask(extra=["--approves", "yes", "--task", "W-1"])
        self.assertEqual((self.rows()[-1]["approves"], self.rows()[-1]["task"]), ("yes", "W-1"))
        before = self.decisions.read_text()
        p = self.run_("ask", "--category", "spend", "--question", "Run it?", "--option", "yes", "--option", "no",
                      "--recommendation", "yes", "--confidence", "0.9", "--evidence-kind", "measured",
                      "--evidence", "WIP.md", "--approves", "approve", *self.grounded, ok=False)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("--approves must be one of the --option values", p.stdout + p.stderr)
        self.assertEqual(self.decisions.read_text(), before)
        self.ask(q="Other?")
        self.assertNotIn("approves", self.rows()[-1])

    def test_explicit_repo_works_outside_the_project(self):
        p = subprocess.run([str(self.rt / "decision.sh"), "ask", "--repo", str(self.work), "--category", "scope",
                            "--question", "q", "--option", "x", "--option", "y", "--recommendation", "x",
                            "--confidence", "0.5", "--evidence-kind", "inference", "--evidence", "e", *self.grounded],
                           cwd=self.root, capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(self.decisions.exists())

    def test_validation_refuses_bad_input_and_writes_nothing(self):
        base = ["ask", "--category", "spend", "--question", "q", "--option", "a", "--option", "b",
                "--recommendation", "a", "--evidence-kind", "measured", "--evidence", "e", *self.grounded]
        for extra in (["--confidence", "1.5"], [], ["--confidence", "0.5", "--blind", "--autonomous"]):
            self.assertNotEqual(self.run_(*base, *extra, ok=False).returncode, 0, extra)
        bad_kind = [x if x != "measured" else "vibes" for x in base] + ["--confidence", "0.5"]
        self.assertNotEqual(self.run_(*bad_kind, ok=False).returncode, 0)
        self.assertFalse(self.decisions.exists())

    def test_a_question_must_rest_on_a_source_the_asker_opened(self):
        report = self.work / "findings.md"
        report.write_text("x")
        for grounding in ([], ["--read", str(self.work / "missing.csv"), "--read-what", "r"],
                          ["--read", str(report), "--read-what", "r"], ["--read", str(self.src)]):
            p = self.run_("ask", "--category", "correction", "--question", "q", "--option", "a", "--option", "b",
                          "--recommendation", "a", "--confidence", "0.5", "--evidence-kind", "measured",
                          "--evidence", "e", *grounding, ok=False)
            self.assertNotEqual(p.returncode, 0, grounding)
        self.assertFalse(self.decisions.exists())
        did = self.ask()
        self.assertEqual(self.rows()[0]["read"], [{"path": str(self.src), "what": "rows 1-2"}])
        self.assertTrue(did)

    def test_b5_a_blind_readers_report_or_agreement_is_not_a_read_source(self):
        run = self.work / "blind"
        run.mkdir()
        for name in ("reader-a.md", "reader-b.md", "agreement.md"):
            (run / name).write_text("x")
        for name in ("reader-a.md", "reader-b.md", "agreement.md"):
            with self.subTest(name):
                p = self.run_("ask", "--category", "correction", "--question", "q", "--option", "a", "--option",
                              "b", "--recommendation", "a", "--confidence", "0.5", "--evidence-kind", "measured",
                              "--evidence", "e", "--read", str(run / name), "--read-what", "row 1", ok=False)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("that is an agent's record, not the source", p.stderr)
        self.assertFalse(self.decisions.exists())
        # a source file whose name only contains the words is still a source
        for name in ("card-reader.csv", "agreements.csv", "reader.md"):
            (run / name).write_text("x")
            with self.subTest(name):
                self.ask("correction", q=f"Use {name}?", grounding=["--read", str(run / name), "--read-what", "row 1"])

    def test_b5_an_agreement_json_beside_its_reports_is_not_a_read_source(self):
        # ratify.sh compare writes agreement.json beside agreement.md in a blind-readers run folder
        run = self.work / "blind-json"
        run.mkdir()
        for name in ("reader-a.md", "reader-b.md", "agreement.md", "agreement.json"):
            (run / name).write_text("x")
        p = self.run_("ask", "--category", "correction", "--question", "q", "--option", "a", "--option", "b",
                      "--recommendation", "a", "--confidence", "0.5", "--evidence-kind", "measured", "--evidence", "e",
                      "--read", str(run / "agreement.json"), "--read-what", "row 1", ok=False)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("that is an agent's record, not the source", p.stderr)
        self.assertFalse(self.decisions.exists())
        # an agreement.json with no blind-readers run beside it is still a source
        alone = self.work / "alone-json"
        alone.mkdir()
        (alone / "agreement.json").write_text("x")
        self.ask("correction", q="Use agreement.json?", grounding=["--read", str(alone / "agreement.json"),
                                                                  "--read-what", "row 1"])

    def test_b5_fix1_reader_and_agreement_names_outside_a_blind_readers_run_are_sources(self):
        # a folder with no reader-*.md and agreement.md pair is not a blind-readers run: as on 435f6cb. Each name is
        # in a folder of its own (agreement.md beside reader-guide.md would be such a pair)
        for name in ("agreement.md", "agreement.txt", "reader-guide.md", "Reader-Notes.txt"):
            with self.subTest(name):
                d = self.work / f"alone-{name}"
                d.mkdir()
                (d / name).write_text("x")
                self.ask("correction", q=f"Use {name}?", grounding=["--read", str(d / name), "--read-what", "row 1"])
        # a reader-*.md with no agreement.md beside it, and an agreement.md with no reader-*.md beside it
        for folder, names in (("half-a", ("reader-a.md", "reader-b.md")), ("half-b", ("agreement.md", "reader.md"))):
            d = self.work / folder
            d.mkdir()
            for name in names:
                (d / name).write_text("x")
                with self.subTest(folder=folder, name=name):
                    self.ask("correction", q=f"Use {folder} {name}?", grounding=["--read", str(d / name), "--read-what", "row 1"])

    def test_a_guess_word_must_be_labelled(self):
        for q in ("It probably holds, approve?", "The rule works it out from the pattern, go?",
                  "Rates should stay put, ok?", "It appears to be a label, go?"):
            p = self.run_("ask", "--category", "spend", "--question", q, "--option", "a", "--option", "b",
                          "--recommendation", "a", "--confidence", "0.5", "--evidence-kind", "measured",
                          "--evidence", "e", *self.grounded, ok=False)
            self.assertNotEqual(p.returncode, 0, q)
            self.assertIn("guess:", p.stderr)
        self.assertFalse(self.decisions.exists())
        self.ask(q="guess: the label column is probably the first; check it?")
        self.ask(q="Shoulder rates and appearance fees: go?")
        self.assertEqual(len(self.rows()), 2)

    def test_a_second_fix_round_needs_a_source_recheck(self):
        def go(q, *extra):
            return self.run_("ask", "--category", "process", "--question", q, "--option", "a", "--option", "b",
                             "--recommendation", "a", "--confidence", "0.5", "--evidence-kind", "measured",
                             "--evidence", "e", *self.grounded, *extra, ok=False)
        up = ["--upstream", "the upstream writer drops the column order"]
        self.assertNotEqual(go("Go on the A1 fix round?", "--task", "A1", "--fix-round", "1", "--layer", "in", "--owners", "1", "--area", "reader").returncode, 0)
        # 0.17.2: was refused (the words "fix round" with no --task or --fix-round), now recorded with exit 0
        self.assertEqual(go("Go on the A1 fix round?", *up).returncode, 0)
        self.assertNotEqual(go("Go on the A1 fix round?", "--fix-round", "1", "--layer", "in", "--owners", "1", "--area", "reader", *up).returncode, 0)
        self.assertEqual(go("Go on the A1 fix round?", "--task", "A1", "--fix-round", "1", "--layer", "in", "--owners", "1", "--area", "reader", *up).returncode, 0)
        p = go("Go on the second A1 fix round?", "--task", "A1", "--fix-round", "2", "--layer", "in", "--owners", "1", "--area", "reader", *up)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("--rechecked", p.stderr)
        ok = go("Go on the second A1 fix round?", "--task", "A1", "--fix-round", "2", "--layer", "in", "--owners", "1", "--area", "reader", *up,
                "--rechecked", "opened rows 1-2 of the source table")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(self.rows()[-1]["rechecked"], "opened rows 1-2 of the source table")

    def test_a_fix_names_what_feeds_the_failing_step(self):
        base = ["ask", "--category", "process", "--option", "a", "--option", "b", "--recommendation", "a",
                "--confidence", "0.5", "--evidence-kind", "measured", "--evidence", "e", *self.grounded]
        # item 13: a fix is what the asker declares with --fix-round; the bare word only prints a note
        for q in ("Fix the count?", "Is fixing the scoreboard worth it?", "Ship the workaround?"):
            p = self.run_(*base, "--question", q)
            self.assertIn("the question mentions a fix", p.stderr, q)
            self.assertEqual(self.rows()[-1]["question"], q)
        p = self.run_(*base, "--question", "Fix the count?", "--task", "A1", "--fix-round", "1", "--layer", "in", "--owners", "1", "--area", "reader", ok=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("--upstream", p.stderr)
        self.run_(*base, "--question", "Fix the count?", "--task", "A1", "--fix-round", "1", "--layer", "in", "--owners", "1", "--area", "reader",
                  "--upstream", "the upstream writer drops the column order")
        self.assertEqual(self.rows()[-1]["upstream"], "the upstream writer drops the column order")

    # 0.17.2: was test_the_words_fix_round_still_ask_for_the_flag (refused, decision:fix-round), now the two tests below
    FIX_ROUND_NOTE = ("decision: note: the question says 'fix round': it is recorded inside the work order's current"
                      " round; --fix-round N is given only on the ask that opens round N\n")
    NO_ROUND_NOTE = ("decision: note: the question says 'fix round' and no fix round is open on its work order: if"
                     " it opens one, give --task, --fix-round 1 and --upstream\n")  # review F1: the note reads the ledger

    def fix_round_question(self, *extra):
        return self.run_("ask", "--category", "process", "--option", "a", "--option", "b", "--recommendation", "a",
                         "--confidence", "0.5", "--evidence-kind", "measured", "--evidence", "e", *self.grounded,
                         "--question", "Go on the A1 fix round?", *extra, ok=False)

    def test_a_question_about_a_fix_round_on_a_task_is_recorded_with_a_note(self):
        p = self.fix_round_question("--task", "A1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stderr.count(self.NO_ROUND_NOTE), 1, p.stderr)
        self.assertNotIn(self.FIX_ROUND_NOTE, p.stderr)
        self.assertNotIn("mentions a fix", p.stderr)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[-1]["task"], "A1")
        self.assertIsNone(self.rows()[-1].get("fixRound"))

    def test_a_question_about_a_fix_round_with_no_task_is_recorded_with_a_note(self):
        p = self.fix_round_question()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stderr.count(self.NO_ROUND_NOTE), 1, p.stderr)
        self.assertNotIn(self.FIX_ROUND_NOTE, p.stderr)
        self.assertEqual(len(self.rows()), 1)
        self.assertIsNone(self.rows()[-1].get("fixRound"))

    def test_a_question_inside_round_1_opens_no_round_for_the_door(self):
        import importlib
        sys.path.insert(0, str(self.rt))
        door = importlib.import_module("bakehouse_door")
        opened = self.fix_round_question("--task", "A5", "--fix-round", "1", "--layer", "in", "--owners", "1",
                                         "--area", "reader", "--upstream", "the upstream writer drops the column order")
        self.assertEqual(opened.returncode, 0, opened.stderr)
        p = self.fix_round_question("--task", "A5")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(self.FIX_ROUND_NOTE, p.stderr)
        self.assertEqual([r.get("fixRound") for r in self.rows()], [1, None])
        self.assertEqual(door.fix_rounds([], self.rows()), {"a5": 1})

    def test_opening_round_1_twice_is_still_refused_and_says_a_question_needs_no_flag(self):
        def open1():
            return self.fix_round_question("--task", "A6", "--fix-round", "1", "--layer", "in", "--owners", "1",
                                           "--area", "reader", "--upstream", "the upstream writer drops the column order")
        self.assertEqual(open1().returncode, 0)
        p = open1()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("A6 already has fix round(s) [1]; this one is round 2", p.stderr)
        self.assertIn("A question inside a round needs no --fix-round.", p.stderr)
        self.assertEqual(self.refusals()[-1]["gate"], "decision:fix-round-number")
        self.assertEqual(len(self.rows()), 1)

    def test_a_bare_fix_word_prints_a_note_and_writes_the_row(self):
        base = ["ask", "--category", "process", "--option", "a", "--option", "b", "--recommendation", "a",
                "--confidence", "0.5", "--evidence-kind", "measured", "--evidence", "e", *self.grounded]
        note = ("decision: note: the question mentions a fix; if it proposes one, give --task, --fix-round"
                " and --upstream\n")
        for i, (q, extra) in enumerate((("Fix the count?", []), ("Ship the workaround?", ["--task", "A1"]),
                                        ("Is fixing the scoreboard worth it?", ["--task", "A1", "--upstream",
                                         "the upstream writer drops the column order"]))):
            p = self.run_(*base, "--question", q, *extra)
            self.assertEqual(p.returncode, 0, q)
            self.assertEqual(p.stderr.count("the question mentions a fix"), 1, q)
            self.assertIn(note, p.stderr, q)
            self.assertEqual(len(self.rows()), i + 1)
            self.assertEqual(self.rows()[-1]["question"], q)
            self.assertIsNone(self.rows()[-1]["fixRound"])
        # no note where no fix word is used, or where the asker declared the fix round or the recheck
        p = self.run_(*base, "--question", "Run the audit now?")
        self.assertNotIn("mentions a fix", p.stderr)
        p = self.run_(*base, "--question", "Fix the count?", "--task", "A2", "--fix-round", "1", "--layer", "in", "--owners", "1", "--area", "reader",
                      "--upstream", "the upstream writer drops the column order")
        self.assertNotIn("mentions a fix", p.stderr)
        p = self.run_(*base, "--question", "Re-check the A2 fix?", "--task", "A2", "--recheck")
        self.assertNotIn("mentions a fix", p.stderr)
        self.assertTrue(self.rows()[-1]["recheck"])

    def test_a_fix_round_without_the_word_still_needs_upstream(self):
        base = ["ask", "--category", "process", "--option", "a", "--option", "b", "--recommendation", "a",
                "--confidence", "0.5", "--evidence-kind", "measured", "--evidence", "e", *self.grounded,
                "--task", "A1", "--fix-round", "1", "--layer", "in", "--owners", "1", "--area", "reader"]
        for up in ([], ["--upstream", "."]):
            p = self.run_(*base, "--question", "Change the reader to keep the column order?", *up, ok=False)
            self.assertNotEqual(p.returncode, 0, up)
            self.assertIn("the question proposes a fix: give --upstream", p.stderr)
            self.assertNotIn("mentions a fix", p.stderr)
        self.assertFalse(self.decisions.exists())
        self.run_(*base, "--question", "Change the reader to keep the column order?",
                  "--upstream", "the upstream writer drops the column order")
        self.assertEqual(self.rows()[-1]["fixRound"], 1)
        self.assertEqual(self.rows()[-1]["upstream"], "the upstream writer drops the column order")

    def test_upstream_needs_three_words(self):
        base = ["ask", "--category", "process", "--option", "a", "--option", "b", "--recommendation", "a",
                "--confidence", "0.5", "--evidence-kind", "measured", "--evidence", "e", *self.grounded,
                "--task", "A1", "--fix-round", "1", "--layer", "in", "--owners", "1", "--area", "reader", "--question", "Change the reader to keep the column order?"]
        p = self.run_(*base, "--upstream", "writer drops", ok=False)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("the question proposes a fix: give --upstream", p.stderr)
        self.assertFalse(self.decisions.exists())
        self.run_(*base, "--upstream", "writer drops columns")
        self.assertEqual(self.rows()[-1]["upstream"], "writer drops columns")

    def test_a_third_fix_round_needs_a_recorded_step_back(self):
        def go(n, *extra):
            return self.run_("ask", "--category", "process", "--question", f"Go on the A9 fix round {n}?",
                             "--option", "a", "--option", "b", "--recommendation", "a", "--confidence", "0.5",
                             "--evidence-kind", "measured", "--evidence", "e", *self.grounded,
                             "--upstream", "the extract step hands over merged cells", "--task", "A9",
                             "--fix-round", str(n), "--layer", "in", "--owners", "1", "--area", "reader", "--rechecked", "re-ran the reader on every sheet",
                             *extra, ok=False)
        self.assertEqual(go(1).returncode, 0)
        self.assertEqual(go(2).returncode, 0)
        p = go(3)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("--stepped-back", p.stderr)
        p = go(2)  # re-asking an earlier round number does not dodge the count
        self.assertNotEqual(p.returncode, 0)
        ok = go(3, "--stepped-back", "census of every input kind on every target; design covers all")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertTrue(self.rows()[-1]["steppedBack"])

    def test_review_2026_10_16_the_gates_need_real_evidence_and_refuse_less_by_mistake(self):
        base = ["ask", "--option", "a", "--option", "b", "--recommendation", "a", "--confidence", "0.5",
                "--evidence-kind", "measured", "--evidence", "e"]
        def ask(cat, q, *extra):
            return self.run_(*base, "--category", cat, "--question", q, *extra, ok=False)
        # a spend, process or scope question needs no source file; the tool's own header example passes
        self.assertEqual(ask("spend", "Run the audit now?").returncode, 0)
        # junk evidence is refused
        for extra in (["--read", str(self.work), "--read-what", "rows"],          # a folder
                      ["--read", str(self.src), "--read-what", "."]):
            self.assertNotEqual(ask("correction", "Keep the band?", *extra).returncode, 0, extra)
        for flag in ("--upstream", "--rechecked", "--stepped-back"):
            p = ask("process", "Fix the count?", *self.grounded, "--upstream", ".", "--task", "B1",
                    "--fix-round", "3", "--rechecked", ".", "--stepped-back", ".")
            self.assertNotEqual(p.returncode, 0, flag)
        self.assertNotEqual(ask("process", "Fix the count?", *self.grounded, "--upstream",
                                "the extract step hands over merged cells", "--task", "B2",
                                "--fix-round", "-4").returncode, 0)
        # item 13: the bare word on a task only prints the note; a fix round on a task must say which round it opens
        p = ask("process", "Apply the fix again?", *self.grounded, "--upstream",
                "the extract step hands over merged cells", "--task", "B3")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("the question mentions a fix", p.stderr)
        p = ask("process", "Go on the B3 fix round?", *self.grounded, "--upstream",
                "the extract step hands over merged cells", "--task", "B3")
        # 0.17.2: was refused (decision:fix-round, "give --fix-round N"), now recorded inside B3's round with a note
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("it is recorded inside the work order's current round", p.stderr)
        # ordinary questions are not refused
        for q in ("Should we ship the pricing change?", "Which naming pattern do we keep?",
                  "Is the fixed fee applied per policy?", "Bump the patch version?"):
            self.assertEqual(ask("correction", q, *self.grounded).returncode, 0, q)
        pdf = self.work / "contract.pdf"
        pdf.write_text("%PDF")
        self.assertEqual(ask("correction", "Keep clause 4?", "--read", str(pdf), "--read-what", "page 3").returncode, 0)
        # a guess label clears its own sentence only
        p = ask("correction", "guess: none. It is probably fine; ship?", *self.grounded)
        self.assertIn("'probably'", p.stderr)
        self.assertEqual(ask("correction", "guess: it is probably fine. Ship?", *self.grounded).returncode, 0)

    def test_a_quoted_inference_word_is_named_not_used(self):
        for q in ("The check refuses the word 'should'; relax it?", 'It flags "probably" when quoted; go?',
                  "It flags ‘assume’ and “guess” when named; go?"):
            self.ask(q=q)
        p = self.run_("ask", "--category", "spend", "--question", "It's 'fine' but should hold; go?",
                      "--option", "a", "--option", "b", "--recommendation", "a", "--confidence", "0.5",
                      "--evidence-kind", "measured", "--evidence", "e", *self.grounded, ok=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("'should'", p.stderr)

    def test_a_recheck_reviews_a_round_and_opens_none(self):
        def go(q, *extra):
            return self.run_("ask", "--category", "process", "--question", q, "--option", "a", "--option", "b",
                             "--recommendation", "a", "--confidence", "0.5", "--evidence-kind", "measured",
                             "--evidence", "e", *self.grounded, *extra, ok=False)
        for n in (1, 2):
            self.assertEqual(go(f"Go on the A7 fix round {n}?", "--upstream", "the extract step hands over merged cells",
                                "--task", "A7", "--fix-round", str(n), "--layer", "in", "--owners", "1", "--area", "reader", "--rechecked", "re-ran the reader on every sheet").returncode, 0)
        ok = go("Re-check fix round 2 of A7?", "--task", "A7", "--recheck")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertTrue(self.rows()[-1]["recheck"])
        self.assertIsNone(self.rows()[-1]["fixRound"])
        self.assertNotEqual(go("Re-check A7?", "--recheck").returncode, 0)
        self.assertNotEqual(go("Re-check A7?", "--task", "A7", "--recheck", "--fix-round", "3", "--layer", "in", "--owners", "1", "--area", "reader",
                               "--rechecked", "re-ran the reader on every sheet", "--stepped-back", "a whole-problem census came first").returncode, 0)
        self.assertNotEqual(go("Re-check A99?", "--task", "A99", "--recheck").returncode, 0)  # no round to review
        p = go("Go on the A7 fix round 3?", "--upstream", "the extract step hands over merged cells", "--task", "A7",
               "--fix-round", "3", "--layer", "in", "--owners", "1", "--area", "reader", "--rechecked", "re-ran the reader on every sheet")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("--stepped-back", p.stderr)

    def test_disagreement_must_be_stated_and_past_lines_are_never_rewritten(self):
        did = self.ask()
        self.assertNotEqual(self.run_("answer", did, "--choice", "not now", ok=False).returncode, 0)
        before = self.decisions.read_text()
        self.run_("answer", did, "--choice", "not now", "--agreed", "no")
        self.assertTrue(self.decisions.read_text().startswith(before))
        self.assertFalse(self.rows()[-1]["agreed"])

    def test_blind_answer_may_leave_agreed_open(self):
        did = self.ask(extra=["--blind"])
        self.run_("answer", did, "--choice", "something else")
        self.assertIsNone(self.rows()[-1]["agreed"])
        self.assertEqual(self.score()["overall"]["scored"], 0)

    def test_autonomous_decisions_take_outcomes_not_answers(self):
        did = self.ask(extra=["--autonomous"])
        self.assertNotEqual(self.run_("answer", did, "--choice", "yes", ok=False).returncode, 0)
        self.run_("outcome", did, "--verdict", "reversed", "--by", "owner review")
        s = self.score()["overall"]
        self.assertEqual((s["autonomous"], s["asked"], s["reversed"]), (1, 0, 1))

    def test_backfill_relaxes_unknowable_fields_and_marks_the_line(self):
        env = {"DECISION_BACKFILL": "1", "DECISION_TS": "2026-10-09T08:00:00+01:00"}
        did = self.run_("ask", "--category", "spend", "--question", "q", env=env).stdout.strip()
        self.run_("answer", did, "--choice", "go", "--agreed", "yes", env=env)
        a, b = self.rows()
        self.assertTrue(a["backfilled"] and b["backfilled"])
        self.assertIsNone(a["confidence"])
        self.assertEqual(a["ts"], "2026-10-09T08:00:00+01:00")

    # --- score ---
    def test_agreement_brier_and_reversal_counting_against_agreement(self):
        a = self.ask(conf=0.9)
        b = self.ask(conf=0.6)
        c = self.ask(conf=0.8)
        self.run_("answer", a, "--choice", "yes")
        self.run_("answer", b, "--choice", "no", "--agreed", "no")
        self.run_("answer", c, "--choice", "yes")
        self.run_("outcome", c, "--verdict", "reversed", "--by", "re-measure")
        s = self.score()["categories"]["spend"]
        self.assertAlmostEqual(s["agreement"], 1 / 3, places=3)
        # (0.9-1)^2 + (0.6-0)^2 + (0.8-0)^2 = 0.01 + 0.36 + 0.64 = 1.01; / 3
        self.assertAlmostEqual(s["brier"], 0.337, places=3)
        self.assertEqual(s["reversed"], 1)
        band = {b["band"]: b for b in s["reliability"]}
        self.assertEqual(band["0.9-1.0"]["n"], 1)
        self.assertEqual(band["0.9-1.0"]["agreement"], 1.0)

    def test_recency_weighting_favours_recent_answers(self):
        old = self.ask(ts="2026-09-12T12:00:00+01:00")  # 28 days = two half-lives: weight 0.25
        new = self.ask(ts="2026-10-10T12:00:00+01:00")  # weight 1
        self.run_("answer", old, "--choice", "no", "--agreed", "no")
        self.run_("answer", new, "--choice", "yes")
        s = self.score()["overall"]
        self.assertEqual(s["agreement"], 0.5)
        self.assertAlmostEqual(s["agreementRecency"], 0.8, places=3)

    def make_autonomous(self, cat="placement", n=20):
        for _ in range(n):
            self.run_("answer", self.ask(cat=cat, conf=0.95), "--choice", "yes")

    def test_autonomy_needs_every_threshold(self):
        self.make_autonomous(n=19)
        s = self.score()["categories"]["placement"]
        self.assertEqual(s["autonomy"], "ask")
        self.assertIn("answered 19 < 20", s["autonomyReasons"][0])
        self.make_autonomous(n=1)
        self.assertEqual(self.score()["categories"]["placement"]["autonomy"], "autonomous")
        last = self.rows()[-1]["id"]
        self.run_("outcome", last, "--verdict", "reversed", "--by", "x")
        self.assertEqual(self.score()["categories"]["placement"]["autonomy"], "ask")

    def test_thresholds_are_configurable_per_project(self):
        (self.work / ".team" / "decision-config.json").write_text(json.dumps({"autonomy": {"minAnswered": 2}}))
        self.make_autonomous(n=2)
        s = self.score()["categories"]["placement"]
        self.assertEqual(s["autonomy"], "autonomous", s["autonomyReasons"])

    # --- sample ---
    def test_should_ask_always_asks_until_autonomous_then_samples_by_seed(self):
        self.assertEqual(self.run_("should-ask", "placement").stdout.strip(), "ask")
        self.make_autonomous()
        draws = {self.run_("should-ask", "placement", "--seed", str(i)).stdout.strip() for i in range(12)}
        self.assertEqual(draws, {"ask-blind", "decide-and-log"})
        again = [self.run_("should-ask", "placement", "--seed", "3").stdout.strip() for _ in range(3)]
        self.assertEqual(len(set(again)), 1)
        # random.Random(0).random() = 0.844 >= 0.2; random.Random(4).random() = 0.236; Random(2) = 0.956
        self.assertEqual(self.run_("should-ask", "placement", "--seed", "0").stdout.strip(), "decide-and-log")

    # --- review ---
    def test_review_lists_every_decision_newest_first_with_scores_on_top(self):
        a = self.ask(q="First | question", ts="2026-10-09T09:00:00+01:00")
        b = self.ask(q="Second question", ts="2026-10-10T09:00:00+01:00")
        self.run_("answer", a, "--choice", "yes", "--notes", "fine")
        self.run_("outcome", a, "--verdict", "refined", "--by", "later read")
        out = self.run_("review", "--md").stdout
        self.assertLess(out.index("Decision scores"), out.index("Every decision"))
        self.assertLess(out.index("Second question"), out.index("First \\| question"))
        self.assertIn("refined: later read", out)
        self.assertIn("(no answer yet)", out)
        self.assertIn(b, out)
        since = self.run_("review", "--since", "2026-10-10").stdout
        self.assertNotIn("First", since)

    # --- DECISIONS.md ---
    def test_decisions_md_is_regenerated_from_the_ledger_after_every_write(self):
        did = self.ask(q="Ship the page?")
        md = (self.records / "DECISIONS.md").read_text()
        self.assertIn(f"### `{did}` — Ship the page?", md)
        self.assertIn("no answer yet", md)
        self.run_("answer", did, "--choice", "yes")
        md = (self.records / "DECISIONS.md").read_text()
        self.assertIn("**Agreed with the recommendation:** yes", md)
        self.assertLess(md.index("## Scores"), md.index("## Every decision"))

    def test_an_older_ask_keeps_its_questions_md_entry_in_the_views(self):
        self.ask()
        row = self.rows()[0]
        row.update(id="d20261010-aaaaaa", questionsRef="## Q — Keep the old cache? (raised 2026-10-01) [OPEN]")
        with self.decisions.open("a") as f:
            f.write(json.dumps(row) + "\n")
        out = self.run_("review", "--md").stdout
        self.assertIn("Keep the old cache?", out.split("Every decision")[1])
        self.run_("render")
        self.assertIn("**QUESTIONS.md entry:** Keep the old cache?", (self.records / "DECISIONS.md").read_text())

    # --- unattended mode (R1) ---
    def settings(self, **unattended):
        (self.work / ".team" / "decision-config.json").write_text(json.dumps({"unattended": unattended}))

    def away(self, **unattended):
        """Unattended mode turned on by the project and started."""
        self.settings(enabled=True, **unattended)
        self.run_("unattended", "start", "--why", "owner said unattended")

    def refused(self, gate, *args):
        before = self.decisions.read_text() if self.decisions.exists() else None
        p = self.run_(*args, ok=False)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(self.refusals()[-1]["gate"], gate, p.stderr)
        self.assertEqual(self.decisions.read_text() if self.decisions.exists() else None, before)
        return p

    def assume(self, did, est, *extra, ok=True):
        return self.run_("answer", did, "--assumed", "--est-tokens", str(est), *extra, ok=ok)

    def test_r1_start_and_stop_are_refused_until_the_project_turns_the_mode_on(self):
        self.refused("decision:unattended-disabled", "unattended", "start", "--why", "owner said unattended")
        self.settings(enabled=False)
        self.refused("decision:unattended-disabled", "unattended", "start", "--why", "owner said unattended")
        self.settings(enabled=True)
        self.refused("decision:unattended-why", "unattended", "start", "--why", "  ")
        self.run_("unattended", "start", "--why", "no answer to d20260101-aaaaaa for 30 minutes", "--wave", "w1",
                  env={"DECISION_TS": "2026-10-10T20:00:00+01:00"})
        self.run_("unattended", "stop", env={"DECISION_TS": "2026-10-11T08:00:00+01:00"})
        self.assertEqual(self.rows(), [
            {"kind": "unattended", "action": "start", "ts": "2026-10-10T20:00:00+01:00", "stream": "work",
             "why": "no answer to d20260101-aaaaaa for 30 minutes", "wave": "w1"},
            {"kind": "unattended", "action": "stop", "ts": "2026-10-11T08:00:00+01:00", "stream": "work"}])
        # the switch's events are not decisions: the scores and the views read as before
        self.assertEqual(self.score()["overall"]["decisions"], 0)
        self.run_("review")

    def test_r1_an_assumed_answer_is_the_recommendation_marked_assumed(self):
        self.away()
        did = self.ask(q="Ship the cache?", conf=0.9)
        self.assume(did, 50000, "--notes", "owner away")
        row = self.rows()[-1]
        self.assertEqual({k: row[k] for k in ("kind", "id", "choice", "agreed", "assumed", "estTokens", "notes")},
                         {"kind": "answer", "id": did, "choice": "yes", "agreed": True, "assumed": True,
                          "estTokens": 50000, "notes": "owner away"})
        self.assertIn("Assumed by the PM, to confirm", (self.records / "DECISIONS.md").read_text())
        # --choice may be given, and must then be the recommendation itself
        other = self.ask(q="Keep the cache?", conf=0.9)
        self.assume(other, 0, "--choice", "yes")
        self.assertEqual(self.rows()[-1]["choice"], "yes")
        # the owner confirms or reverses with the normal answer, and the latest answer wins
        self.run_("answer", did, "--choice", "no", "--agreed", "no")
        self.assertNotIn("assumed", self.rows()[-1])

    def test_r1_each_refusal_of_an_assumed_answer_has_its_own_gate(self):
        did = self.ask(q="Ship the cache?", conf=0.9)
        self.refused("decision:assumed-off", "answer", did, "--assumed", "--est-tokens", "1000")
        self.settings(enabled=True)
        self.refused("decision:assumed-off", "answer", did, "--assumed", "--est-tokens", "1000")
        self.run_("unattended", "start", "--why", "owner said unattended")
        self.refused("decision:assumed-estimate", "answer", did, "--assumed")
        self.refused("decision:assumed-estimate", "answer", did, "--choice", "yes", "--est-tokens", "1000")
        self.refused("decision:assumed-choice", "answer", did, "--assumed", "--est-tokens", "1000", "--choice", "no")
        loose = {"DECISION_BACKFILL": "1", "DECISION_TS": "2026-10-09T08:00:00+01:00"}
        bare = self.run_("ask", "--category", "spend", "--question", "Clear the cache?", env=loose).stdout.strip()
        self.refused("decision:assumed-recommendation", "answer", bare, "--assumed", "--est-tokens", "1000")
        unsure = self.run_("ask", "--category", "spend", "--question", "Warm the cache?", "--option", "yes",
                           "--option", "no", "--recommendation", "yes", env=loose).stdout.strip()
        n = len(self.refusals())
        self.refused("decision:assumed-recommendation", "answer", unsure, "--assumed", "--est-tokens", "1000")
        self.assertEqual(len(self.refusals()), n + 1, "the no-confidence refusal writes its own event")
        gate = self.ask(q="Approve the cache?", extra=["--approves", "yes", "--task", "W-1"])
        self.refused("decision:assumed-approves", "answer", gate, "--assumed", "--est-tokens", "1000")
        self.refused("decision:assumed-confidence", "answer", self.ask(q="Size the cache?", conf=0.7),
                     "--assumed", "--est-tokens", "1000")
        self.refused("decision:assumed-cost", "answer", did, "--assumed", "--est-tokens", "500001")
        self.settings(enabled=True, neverCategories=["spend"])
        self.refused("decision:assumed-never", "answer", did, "--assumed", "--est-tokens", "1000")
        self.settings(enabled=True)
        self.assume(did, 1000)

    def test_an_assumed_answer_needs_a_category_from_the_projects_list(self):
        self.away()
        made_up = self.ask(cat="spnd", q="Ship the cache?")
        p = self.refused("decision:assumed-category", "answer", made_up, "--assumed", "--est-tokens", "1000")
        self.assertIn("spnd", p.stderr)
        self.assertIn(".team/decision-config.json", p.stderr)
        # the same name as a known one, but for case and a trailing space: known
        self.assume(self.ask(cat="Spend ", q="Warm the cache?"), 1000)
        self.assertTrue(self.rows()[-1]["assumed"])
        # an answer from the owner is unchanged: any category
        self.run_("answer", made_up, "--choice", "yes")
        self.assertNotIn("assumed", self.rows()[-1])

    def ask_args(self, cat, q):
        return ["ask", "--category", cat, "--question", q, "--option", "yes", "--option", "no",
                "--recommendation", "yes", "--confidence", "0.9", "--evidence-kind", "measured",
                "--evidence", "WIP.md", *self.grounded]

    def test_the_ask_note_names_the_projects_categories_and_the_consequence(self):
        p = self.run_(*self.ask_args("spnd", "Ship the cache?"))
        self.assertIn("category 'spnd' is outside the project's categories (", p.stderr)
        self.assertIn("an assumed answer is refused for it", p.stderr)
        p = self.run_(*self.ask_args("Spend ", "Warm the cache?"))
        self.assertNotIn("outside the project's categories", p.stderr)

    def test_categories_of_another_shape_are_refused(self):
        self.away()
        did = self.ask(q="Ship the cache?")
        for cats in (None, "spend", ["spend", 3], []):
            with self.subTest(categories=cats):
                (self.work / ".team" / "decision-config.json").write_text(json.dumps(
                    {"categories": cats, "unattended": {"enabled": True}}))
                for args in (("answer", did, "--assumed", "--est-tokens", "1000"),
                             self.ask_args("spend", "Warm the cache?")):
                    p = self.refused("decision:categories-config", *args)
                    self.assertIn(".team/decision-config.json", p.stderr)
                    self.assertNotIn("Traceback", p.stderr)

    def test_a_projects_own_category_can_be_assumed(self):
        (self.work / ".team" / "decision-config.json").write_text(json.dumps(
            {"categories": ["caching"], "unattended": {"enabled": True}}))
        self.run_("unattended", "start", "--why", "owner said unattended")
        self.assume(self.ask(cat="caching", q="Ship the cache?"), 1000)
        self.assertTrue(self.rows()[-1]["assumed"])
        # the project's list replaces the defaults: a default name is no longer known
        self.refused("decision:assumed-category", "answer", self.ask(cat="spend", q="Warm the cache?"),
                     "--assumed", "--est-tokens", "1000")

    def test_r1_fix_an_assumed_answer_is_refused_once_the_owner_has_answered(self):
        self.away()
        did = self.ask(q="Ship the cache?", conf=0.9)
        self.assume(did, 1000)
        self.assume(did, 2000)  # an earlier assumed answer is not the owner's: a second one is taken
        self.run_("answer", did, "--choice", "no", "--agreed", "no")
        self.refused("decision:assumed-answered", "answer", did, "--assumed", "--est-tokens", "1000")
        self.assertNotIn("assumed", self.rows()[-1])

    def test_r1_the_confidence_line_rises_with_the_steps_cost(self):
        self.away()
        for conf, est, gate in ((0.75, 100000, "decision:assumed-confidence"), (0.76, 100000, None),
                                (0.76, 0, None), (0.85, 100001, "decision:assumed-confidence"),
                                (0.86, 100001, None), (0.86, 500000, None),
                                (0.99, 500001, "decision:assumed-cost"), (1.0, 900000, "decision:assumed-cost")):
            with self.subTest(conf=conf, est=est):
                did = self.ask(q=f"Ship the cache at {est}?", conf=conf)
                if gate:
                    self.refused(gate, "answer", did, "--assumed", "--est-tokens", str(est))
                else:
                    self.assume(did, est)
                    self.assertTrue(self.rows()[-1]["assumed"])

    def test_r1_a_project_file_merges_over_the_default_settings(self):
        self.away()  # only enabled is set: the default bands hold
        self.assume(self.ask(q="Ship the cache?", conf=0.8), 100000)
        self.refused("decision:assumed-confidence", "answer", self.ask(q="Keep the cache?", conf=0.8),
                     "--assumed", "--est-tokens", "100001")
        self.settings(enabled=True, bands=[{"maxTokens": 1000, "above": 0.5}])
        self.assume(self.ask(q="Size the cache?", conf=0.6), 1000)
        self.refused("decision:assumed-cost", "answer", self.ask(q="Warm the cache?", conf=0.99),
                     "--assumed", "--est-tokens", "1001")

    def test_r1_an_assumed_answer_is_left_out_of_the_scores_until_the_owner_answers(self):
        (self.work / ".team" / "decision-config.json").write_text(json.dumps(
            {"unattended": {"enabled": True}, "autonomy": {"minAnswered": 2}}))
        self.run_("unattended", "start", "--why", "owner said unattended")
        owner = self.ask(cat="placement", conf=0.95)
        self.run_("answer", owner, "--choice", "yes")
        guessed = self.ask(cat="placement", conf=0.95, q="Place the cache?")
        self.assume(guessed, 1000)
        s = self.score()["categories"]["placement"]
        self.assertEqual((s["answered"], s["scored"], s["withConfidence"], s["agreement"]), (1, 1, 1, 1.0))
        self.assertEqual(s["autonomy"], "ask")
        self.assertIn("answered 1 < 2", s["autonomyReasons"][0])
        self.run_("answer", guessed, "--choice", "yes")
        s = self.score()["categories"]["placement"]
        self.assertEqual((s["answered"], s["scored"], s["withConfidence"]), (2, 2, 2))
        self.assertEqual(s["autonomy"], "autonomous", s["autonomyReasons"])

    # --- unattended mode, fix round 1 ---
    def test_r1_fix1_the_never_list_holds_whatever_the_case_or_spacing(self):
        self.away(neverCategories=["Spend", "correction "])
        for cat in ("spend", "SPEND", "spend ", "Correction", " correction"):
            with self.subTest(category=cat):
                did = self.ask(cat=cat, q=f"Ship the cache as {cat!r}?")
                self.refused("decision:assumed-never", "answer", did, "--assumed", "--est-tokens", "1000")

    def test_r1_fix1_only_the_json_value_true_turns_the_mode_on(self):
        for value in ("false", "true", 1, "yes"):
            with self.subTest(enabled=value):
                self.settings(enabled=value)
                self.refused("decision:unattended-disabled", "unattended", "start", "--why", "owner said unattended")
        self.away()
        did = self.ask(q="Ship the cache?", conf=0.9)
        self.settings(enabled="false")
        self.refused("decision:assumed-off", "answer", did, "--assumed", "--est-tokens", "1000")

    def test_r1_fix1_stop_is_always_accepted_and_start_keeps_its_refusal(self):
        self.away()
        self.settings(enabled=False)
        self.run_("unattended", "stop", env={"DECISION_TS": "2026-10-11T08:00:00+01:00"})
        self.assertEqual(self.rows()[-1], {"kind": "unattended", "action": "stop", "ts": "2026-10-11T08:00:00+01:00",
                                           "stream": "work"})
        self.settings(enabled=True)  # the old start does not come back: the stop is the latest switch event
        self.refused("decision:assumed-off", "answer", self.ask(q="Ship the cache?"), "--assumed", "--est-tokens", "1")
        (self.work / ".team" / "decision-config.json").unlink()
        self.run_("unattended", "stop")
        self.refused("decision:unattended-disabled", "unattended", "start", "--why", "owner said unattended")

    def test_r1_fix1_a_start_is_refused_while_the_mode_is_on(self):
        self.away()
        self.refused("decision:unattended-on", "unattended", "start", "--why", "second start", "--wave", "w9")
        self.run_("unattended", "stop")
        self.run_("unattended", "start", "--why", "owner said unattended", "--wave", "nowave")
        self.assertEqual(self.rows()[-1]["wave"], "nowave", "the wave is not checked against the spend ledger")

    def test_r1_fix1_agreed_is_refused_with_assumed(self):
        self.away()
        did = self.ask(q="Ship the cache?", conf=0.9)
        for agreed in ("no", "yes"):
            with self.subTest(agreed=agreed):
                self.refused("decision:assumed-agreed", "answer", did, "--assumed", "--est-tokens", "1000",
                             "--agreed", agreed)

    def test_r1_fix1_an_assumed_answer_on_a_blind_ask_is_refused(self):
        self.away()
        did = self.ask(q="Ship the cache?", conf=0.9, extra=["--blind"])
        self.refused("decision:assumed-blind", "answer", did, "--assumed", "--est-tokens", "1000")

    def test_r1_fix1_an_unsorted_band_list_is_read_in_order_of_size(self):
        self.away(bands=[{"maxTokens": 500000, "above": 0.85}, {"maxTokens": 100000, "above": 0.75}])
        self.assume(self.ask(q="Ship the cache?", conf=0.8), 100000)
        self.refused("decision:assumed-confidence", "answer", self.ask(q="Keep the cache?", conf=0.8),
                     "--assumed", "--est-tokens", "100001")

    def test_r1_fix1_a_bad_unattended_block_is_refused_with_one_gate_naming_the_file(self):
        did = None
        for block in (None, [], "on", {"enabled": True, "bands": None}, {"enabled": True, "bands": "100000"},
                      {"enabled": True, "bands": [{"above": 0.5}]}, {"enabled": True, "bands": [{"maxTokens": 1000}]},
                      {"enabled": True, "bands": [{"maxTokens": "100000", "above": "0.75"}]},
                      {"enabled": True, "bands": [{"maxTokens": 1.5, "above": 0.5}]},
                      {"enabled": True, "bands": [{"maxTokens": True, "above": 0.5}]},
                      {"enabled": True, "bands": [5]},
                      {"enabled": True, "neverCategories": None}, {"enabled": True, "neverCategories": "spend"},
                      {"enabled": True, "neverCategories": ["spend", 3]}):
            with self.subTest(block=block):
                if did is None:
                    self.away()
                    did = self.ask(q="Ship the cache?", conf=0.9)
                (self.work / ".team" / "decision-config.json").write_text(json.dumps({"unattended": block}))
                for args in (("answer", did, "--assumed", "--est-tokens", "100001"),
                             ("unattended", "start", "--why", "owner said unattended")):
                    p = self.refused("decision:unattended-config", *args)
                    self.assertIn(".team/decision-config.json", p.stderr)
                    self.assertNotIn("Traceback", p.stderr)
        self.settings(enabled=True, bands=[{"maxTokens": 1000, "above": 0}], neverCategories=[])
        self.assume(did, 1000)  # a whole-number line is a number


if __name__ == "__main__":
    unittest.main()
