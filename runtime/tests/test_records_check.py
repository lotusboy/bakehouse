"""Tests for runtime/records-check.py. Standard library only. Each test builds a scratch project: a git
repository with .team/binding.json and a records worktree (the ops ledger, the register) named by
`git config bakehouse.records`."""
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "records-check.py"
CONTRACTS = Path(__file__).resolve().parent.parent / "record-contracts.json"
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@t")
for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
    ENV.pop(k, None)

PRED = json.dumps({"moves": [{"what": "count:x", "from": 1, "to": 2}], "mustNotMove": []})
BASE = json.dumps({"count:x": 1})
MEAS = json.dumps({"count:x": 2})
CLOSE_REFUSED = "work order close overridden"
TRIO = {"moves.prediction.json": PRED, "moves.baseline.json": BASE, "moves.measured.json": MEAS,
        "result.md": "Result: 1 move, as predicted.\n"}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.repo, self.records = t / "repo", t / "repo.team-records"
        (self.repo / ".team").mkdir(parents=True)
        self.git("init", "-q")
        self.git("worktree", "add", "-q", "--orphan", "-b", "team-records", str(self.records))
        self.git("config", "bakehouse.records", str(self.records))
        self.binding = self.repo / ".team" / "binding.json"
        self.ledger = self.records / "ops" / "events.jsonl"
        self.ledger.parent.mkdir()
        self.ledger.write_text("")
        self.register = self.records / "registers" / "records.json"
        self.bind()

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, date="2026-06-10T12:00:00"):
        env = dict(ENV, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True,
                              text=True, env=env).stdout

    def commit(self, subject, files, date="2026-06-10T12:00:00"):
        """files: {path: text}."""
        for path, text in files.items():
            p = self.repo / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        self.git("add", "-A", date=date)
        self.git("commit", "-qm", subject, date=date)

    def record(self, folder, files, subject="record"):
        self.commit(subject, {f"reviews/{folder}/{n}": t for n, t in files.items()})

    def bind(self, **over):
        b = {"owned": True, "register": "health.json", "tracker": {"file": "T.md", "id": "^T[0-9]+$"},
             "signal": {"script": "health-signal", "ref": "HEAD", "codeRoots": ["src/"], "part": "^src/(.+)$"},
             "reviews": "reviews/"}
        b.update(over)
        self.binding.write_text(json.dumps(b))

    def close_event(self, task, note, ts="2026-06-11T00:00:00+00:00"):
        ev = {"ts": ts, "kind": "work-order-closed", "task": task, "note": note}
        with self.ledger.open("a") as f:
            f.write(json.dumps({k: v for k, v in ev.items() if v is not None}) + "\n")

    def refused_event(self, task, ts="2026-06-11T00:00:00+00:00", purpose=None):
        ev = {"ts": ts, "kind": "gate-refused", "task": task, "purpose": purpose,
              "note": "work order close overridden: owner go | refused: x"}
        with self.ledger.open("a") as f:
            f.write(json.dumps({k: v for k, v in ev.items() if v is not None}) + "\n")

    def sha(self, path):
        return hashlib.sha256((self.repo / path).read_bytes()).hexdigest()

    def run_check(self, *args, as_of="2026-06-15"):
        p = subprocess.run(["python3", str(SCRIPT), "--repo", str(self.repo),
                            "--as-of", as_of, *args], capture_output=True, text=True, env=ENV)
        return p.returncode, p.stdout + p.stderr

    def reg(self):
        return json.loads(self.register.read_text())

    def flags(self, wo=None):
        return {(f["workOrder"], f["rule"]): f for f in self.reg()["flags"] if wo in (None, f["workOrder"])}

    def closed_record(self, wo="T1", folder="2026-06-10-T1-step1-review"):
        self.commit(f"{wo}: the change", {"src/a.py": "x\n"})
        self.record(folder, TRIO)
        d = f"reviews/{folder}"
        self.close_event(wo, f"{d}/moves.prediction.json sha256:{self.sha(d + '/moves.prediction.json')} commits "
                             f"abc, {d}/moves.measured.json sha256:{self.sha(d + '/moves.measured.json')}, "
                             f"baseline {d}/moves.baseline.json (sha256 {self.sha(d + '/moves.baseline.json')}); "
                             f"ledger: x")
        return d


class TestCloseRule(Fixture):
    def test_a_complete_closed_change_has_no_flag(self):
        self.closed_record()
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.reg()["flags"], [])
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "closed")
        self.assertIn("0 open flag(s)", out)

    def test_code_commits_and_no_close_event_flag_close(self):
        self.commit("T1: the change", {"src/a.py": "x\n"})
        self.record("2026-06-10-T1-step1-review", TRIO)
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        f = self.flags()
        self.assertEqual(set(f), {("T1", "close")})
        self.assertEqual(f[("T1", "close")]["owner"], "pm")
        self.assertEqual(f[("T1", "close")]["state"], "open")
        self.assertIn("predict-gate.sh close T1", f[("T1", "close")]["next"])
        self.assertIn('PREDICT_GATE_CLOSE_OVERRIDE="<reason>"', f[("T1", "close")]["next"])

    def test_close_event_whose_sha256_does_not_match_flags(self):
        d = self.closed_record()
        self.commit("edit the record after close", {f"{d}/moves.measured.json": json.dumps({"count:x": 3})})
        self.run_check()
        f = self.flags()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "hash-mismatch")
        self.assertIn(f"sha256 does not match {d}/moves.measured.json", f[("T1", "close")]["what"][0])
        self.assertIn("changed after its close", f[("T1", "close")]["next"])

    def test_the_latest_close_event_counts(self):
        d = self.closed_record()
        self.close_event("T1", f"{d}/moves.prediction.json sha256:{'0' * 64}")
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "hash-mismatch")
        self.assertIn(f"sha256 does not match {d}/moves.prediction.json", self.flags()[("T1", "close")]["what"][0])

    def test_close_event_without_a_sha256_flags(self):
        self.commit("T1: the change", {"src/a.py": "x\n"})
        self.record("2026-06-10-T1-step1-review", TRIO)
        self.close_event("T1", "closed by hand")
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "hash-mismatch")
        self.assertIn("carries no sha256", self.flags()[("T1", "close")]["what"][0])

    def test_an_overridden_close_marks_record_flags_overridden(self):
        self.commit("T1: the change", {"src/a.py": "x\n"})
        self.record("2026-06-10-T1-step1-review", {"prediction.md": "prose\n", "result.md": "r\n"})
        self.refused_event("T1", purpose=CLOSE_REFUSED)
        self.close_event("T1", "OVERRIDDEN, see the gate-refused line; no pair passed; ledger: x")
        code, out = self.run_check()
        f = self.flags()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "overridden")
        self.assertNotIn(("T1", "close"), f)
        self.assertEqual({f[k]["state"] for k in f}, {"overridden"})
        self.assertIn(("T1", "change.prediction"), f)
        self.assertEqual(self.reg()["computed"]["openFlags"], 0)

    def test_no_record_folder_flags_close_only(self):
        self.commit("T1: a quick fix", {"src/a.py": "x\n"})
        self.run_check()
        f = self.flags()
        self.assertEqual(set(f), {("T1", "close")})
        self.assertIn("no record folder names it", f[("T1", "close")]["what"][0])

    def test_a_refactor_record_owes_no_close_but_its_byte_identical_check(self):
        self.commit("T1 step 1: move a function", {"src/a.py": "x\n"})
        self.record("2026-06-10-T1-step1-review", {"byte-identical.txt": ""})
        self.run_check()
        f = self.flags()
        self.assertEqual(set(f), {("T1", "refactor.byte-identical")})
        self.assertIn("byte-identical.txt is empty", f[("T1", "refactor.byte-identical")]["what"][0])
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "refactor-unproven")

    def test_a_repo_another_party_owns_has_no_close_rule(self):
        self.bind(owned=False, approver="the repository's maintainers")
        self.commit("T1: the change", {"src/a.py": "x\n"})
        self.record("2026-06-10-T1-step1-review", {"prediction.md": "prose\n"})
        self.run_check()
        f = self.flags()
        self.assertNotIn(("T1", "close"), f)
        self.assertIn(("T1", "change.prediction"), f)
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "not-applicable")


class TestRecordRule(Fixture):
    def setUp(self):
        super().setUp()
        self.commit("T1: the change", {"src/a.py": "x\n"})

    def rec(self, **files):
        self.record("2026-06-10-T1-step1-review", {**TRIO, **files})
        self.run_check()
        return self.flags()

    def test_a_prose_only_record_flags_the_trio(self):
        self.record("2026-06-10-T1-step1-review", {"prediction.md": "prose\n", "result.md": "r\n"})
        self.run_check()
        f = self.flags()
        self.assertEqual(set(f), {("T1", "close"), ("T1", "change.prediction"), ("T1", "change.baseline"),
                                  ("T1", "change.measured")})
        self.assertIn("a prose prediction is not checked by a machine", f[("T1", "change.prediction")]["what"][0])
        self.assertIn("cannot be produced now", f[("T1", "change.prediction")]["next"])
        self.assertIn("Measure it", f[("T1", "change.measured")]["next"])

    def test_an_empty_file_fails(self):
        f = self.rec(**{"result.md": "\n", "moves.measured.json": ""})
        self.assertIn("result.md is empty", f[("T1", "change.result")]["what"][0])
        self.assertIn("moves.measured.json is empty", f[("T1", "change.measured")]["what"][0])

    def test_an_empty_prediction_fails(self):
        f = self.rec(**{"moves.prediction.json": "  \n"})
        self.assertIn("moves.prediction.json is empty", f[("T1", "change.prediction")]["what"][0])

    def test_a_prediction_that_does_not_parse_fails(self):
        f = self.rec(**{"moves.prediction.json": '{"moves": [{"what": "x", "to": 1, "delta": 2}]}'})
        self.assertIn("does not parse under predict-gate's checker", f[("T1", "change.prediction")]["what"][0])
        f = self.rec(**{"moves.prediction.json": "{not json"})
        self.assertIn("is not valid JSON", f[("T1", "change.prediction")]["what"][0])

    def test_a_prediction_that_names_no_move_fails(self):
        f = self.rec(**{"moves.prediction.json": json.dumps({"moves": [], "mustNotMove": ["x"]})})
        self.assertIn("names no move", f[("T1", "change.prediction")]["what"][0])

    def test_a_measured_file_that_is_not_flat_fails(self):
        f = self.rec(**{"moves.measured.json": json.dumps({"count:x": [2]})})
        self.assertIn("is not flat JSON", f[("T1", "change.measured")]["what"][0])

    def test_each_round_needs_its_own_measured_file_and_finds_its_baseline(self):
        f = self.rec(**{"moves.prediction.fix1.json": PRED, "moves.baseline.fix1.json": BASE})
        self.assertIn("moves.measured.fix1.json is missing", f[("T1", "change.measured")]["what"][0])
        self.assertNotIn(("T1", "change.baseline"), f)
        named = json.dumps({"moves": [{"what": "count:x", "to": 3}], "baseline": "moves.measured.json"})
        f = self.rec(**{"moves.prediction.fix1.json": named, "moves.measured.fix1.json": MEAS})
        self.assertNotIn(("T1", "change.baseline"), f)
        self.assertNotIn(("T1", "change.measured"), f)

    def test_a_complete_record_has_only_the_close_flag(self):
        self.assertEqual(set(self.rec()), {("T1", "close")})

    def test_one_flag_per_work_order_and_rule_across_folders(self):
        self.record("2026-06-09-T1-design", {"prediction.md": "prose\n"})
        self.record("2026-06-10-T1-step1-review", {"prediction.md": "prose\n", "result.md": "r\n"})
        self.run_check()
        f = self.flags()
        self.assertEqual(f[("T1", "change.prediction")]["folders"], ["2026-06-09-T1-design", "2026-06-10-T1-step1-review"])
        self.assertEqual(sum(1 for k in self.reg()["flags"] if k["rule"] == "change.prediction"), 1)
        self.assertIn("2026-06-09-T1-design: no file matches result*.md", f[("T1", "change.result")]["what"][0])

    def test_a_record_committed_nowhere_is_not_read(self):
        (self.repo / "reviews" / "2026-06-10-T1-step1-review").mkdir(parents=True)
        for n, t in TRIO.items():
            (self.repo / "reviews" / "2026-06-10-T1-step1-review" / n).write_text(t)
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["folders"], [])


class TestWorkOrders(Fixture):
    def test_a_commit_belongs_to_the_first_id_and_touches_a_code_root(self):
        self.commit("T2: uses what T1 built", {"src/a.py": "x\n"})
        self.commit("T3: docs only", {"docs/a.md": "x\n"})
        self.commit("tidy: no id", {"src/b.py": "x\n"})
        self.commit("T21 is not T2", {"src/c.py": "x\n"})
        code, out = self.run_check()
        r = self.reg()
        self.assertEqual({w: v["codeCommits"] for w, v in r["workOrders"].items()}, {"T2": 1, "T21": 1})
        self.assertEqual(r["computed"]["uncredited"], 1)
        self.assertIn("1 code commit(s) name no tracker id", out)

    def test_the_code_roots_and_the_window(self):
        self.commit("T1: old", {"src/a.py": "1\n"}, date="2026-01-01T12:00:00")
        self.commit("T2: in a tool", {"tools/a.py": "1\n"})
        self.run_check()
        self.assertEqual(list(self.reg()["workOrders"]), [])
        self.run_check("--code-roots", "src/", "tools/", "--since", "2025-12-31")
        self.assertEqual(list(self.reg()["workOrders"]), ["T1", "T2"])
        self.bind(records={"since": "2025-12-31"})
        self.run_check()
        self.assertEqual((list(self.reg()["workOrders"]), self.reg()["computed"]["since"]), (["T1"], "2025-12-31"))

    def test_a_record_folder_holds_the_id_as_a_whole_token_case_ignored(self):
        self.commit("T1: the change", {"src/a.py": "x\n"})
        self.record("2026-06-10-t1-lower", {"prediction.md": "p\n"})
        self.record("2026-06-10-T10-other", {"prediction.md": "p\n"})
        self.record("2026-06-10-review-T5-T1", {"findings.md": "f\n"})
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["folders"], ["2026-06-10-review-T5-T1", "2026-06-10-t1-lower"])

    def test_the_register_is_written_in_the_records_folder_unless_no_write(self):
        self.commit("T1: x", {"src/a.py": "x\n"})
        self.run_check("--no-write")
        self.assertFalse(self.register.exists())
        self.run_check()
        self.assertTrue(self.register.exists())

    def test_the_repository_is_not_written(self):
        self.commit("T1: x", {"src/a.py": "x\n"})
        before = self.git("status", "--porcelain", "--ignored")
        self.run_check()
        self.assertEqual(self.git("status", "--porcelain", "--ignored"), before)

    def test_a_repository_with_no_team_or_no_records_folder_is_bad_input(self):
        self.git("config", "--unset", "bakehouse.records")
        code, out = self.run_check()
        self.assertEqual((code, "bakehouse setup" in out), (2, True), out)
        self.binding.unlink()
        code, out = self.run_check()
        self.assertEqual((code, "has no team" in out), (2, True), out)

    def test_no_tracker_id_is_bad_input(self):
        self.bind(tracker={"file": "T.md"})
        code, out = self.run_check()
        self.assertEqual(code, 2)
        self.assertIn("no tracker.id", out)


class TestPromote(Fixture):
    def test_a_rule_flagged_on_two_work_orders_is_a_promote_to_code_candidate(self):
        self.commit("T1: x", {"src/a.py": "x\n"})
        self.run_check()
        r = self.reg()["rules"]["close"]
        self.assertEqual((r["promoteToCode"], r["for"]), (False, None))
        self.commit("T2: y", {"src/b.py": "y\n"})
        code, out = self.run_check()
        r = self.reg()["rules"]["close"]
        self.assertEqual((r["workOrders"], r["codeCommits"], r["promoteToCode"], r["for"]),
                         (["T1", "T2"], 2, True, "owner"))
        self.assertIn("Promote-to-code candidates for the owner", out)

    def test_overridden_flags_still_count_toward_promotion(self):
        for wo in ("T1", "T2"):
            self.commit(f"{wo}: x", {f"src/{wo}.py": "x\n"})
            self.record(f"2026-06-10-{wo}-step1-review", {"prediction.md": "p\n", "result.md": "r\n"})
            self.close_event(wo, "OVERRIDDEN, see the gate-refused line; no pair passed")
        self.run_check()
        self.assertTrue(self.reg()["rules"]["change.prediction"]["promoteToCode"])


class TestBindingAddsOnly(Fixture):
    def setUp(self):
        super().setUp()
        self.commit("T1: x", {"src/a.py": "x\n"})
        self.record("2026-06-10-T1-step1-review", TRIO)

    def test_a_binding_may_add_an_artefact(self):
        self.bind(records={"jobs": {"change": {"require": [{"id": "gates", "glob": "gates*.md"}]}}})
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        f = self.flags()
        self.assertIn("no file matches gates*.md", f[("T1", "change.gates")]["what"][0])
        self.assertIn("gates", self.reg()["contracts"]["change"]["require"])

    def test_an_empty_require_list_removes_nothing(self):
        self.bind(records={"jobs": {"change": {"require": []}}})
        self.record("2026-06-10-T1-step1-review", {"result.md": ""})
        self.run_check()
        self.assertIn(("T1", "change.result"), self.flags())
        self.assertEqual(self.reg()["contracts"]["change"]["require"], ["prediction", "baseline", "measured", "result"])

    def test_a_binding_that_tries_to_remove_or_replace_a_default_is_refused(self):
        cases = [
            ({"jobs": {"change": {"remove": ["result"]}}}, "never change or remove its defaults"),
            ({"jobs": {"change": {"require": [{"id": "result", "glob": "notes.md"}]}}}, "replaces the default artefact 'result'"),
            ({"jobs": {"reviewer": {"require": [{"id": "mutations", "glob": "m.json"}]}}}, "replaces the default artefact 'mutations'"),
            ({"jobs": {"change": {"require": [{"id": "x", "glob": "x.md", "active": False}]}}}, "must be exactly"),
            ({"jobs": {"change": {"require": [{"id": "x", "glob": "x.md", "check": "prediction"}]}}}, "must be exactly"),
            ({"jobs": {"change": {"require": [{"id": "x", "glob": "sub/x.md"}]}}}, "top level"),
            ({"jobs": {"deploy": {"require": [{"id": "x", "glob": "x.md"}]}}}, "no such job"),
            ({"jobs": {"change": {"marks": []}}}, "never change or remove its defaults"),
            ({"promoteAt": 99}, "is not a records field"),
            ({"since": "last week"}, "is not a date"),
            ([], "must be an object"),
        ]
        for rec, why in cases:
            self.bind(records=rec)
            code, out = self.run_check()
            self.assertEqual(code, 2, (rec, out))
            self.assertIn(why, out, rec)
            self.assertFalse(self.register.exists(), rec)


class TestStepRecords(Fixture):
    def setUp(self):
        super().setUp()
        self.commit("T1: the change", {"src/a.py": "x\n"})

    def test_a_step_folder_with_only_a_result_is_a_change_record(self):
        self.record("2026-06-10-T1-step1-review", {"result.md": "r\n"})
        self.run_check()
        f = self.flags()
        self.assertEqual(set(f), {("T1", "close"), ("T1", "change.prediction"), ("T1", "change.baseline"),
                                  ("T1", "change.measured")})
        self.assertEqual(self.reg()["workOrders"]["T1"]["jobs"], ["change"])

    def test_a_step_folder_with_neither_prediction_nor_result_is_a_change_record(self):
        self.record("2026-06-10-T1-step1-review", {"contract.md": "c\n", "gates.md": "g\n"})
        self.run_check()
        self.assertEqual({r for _, r in self.flags()}, {"close", "change.prediction", "change.baseline",
                                                         "change.measured", "change.result"})

    def test_a_folder_holding_a_result_is_a_change_record_whatever_its_name(self):
        self.record("2026-06-10-T1-build", {"result.md": "r\n"})
        self.run_check()
        self.assertIn(("T1", "change.prediction"), self.flags())

    def test_a_folder_that_is_no_step_and_holds_no_prediction_or_result_is_no_change_record(self):
        self.record("2026-06-10-T1-census", {"census.md": "c\n"})
        self.run_check()
        f = self.flags()
        self.assertEqual(set(f), {("T1", "close")})
        self.assertIn("mark none of", f[("T1", "close")]["what"][0])

    def test_the_step_number_the_case_and_the_bindings_review_name(self):
        self.record("2026-06-10-t1-step3-review", {"gates.md": "g\n"})
        self.run_check()
        self.assertIn(("T1", "change.prediction"), self.flags())
        self.bind(reviewName="{date}-{id}-run{n}")
        self.record("2026-06-11-T1-run2", {"gates.md": "g\n"})
        self.run_check()
        self.assertEqual(self.flags()[("T1", "change.prediction")]["folders"], ["2026-06-11-T1-run2"])


class TestRefactorSteps(Fixture):
    def steps(self, *records):
        for n, files in enumerate(records, 1):
            self.commit(f"T1: step {n} move", {f"src/{n}.py": "x\n"})
            self.record(f"2026-06-1{n}-T1-step{n}-review", files)
        self.run_check()
        return self.flags()

    def test_a_refactor_step_without_its_proof_is_flagged_as_that(self):
        f = self.steps({"byte-identical.txt": "0 diffs\n", "findings.md": "f\n", "mutations.json": "[]"},
                       {"findings.md": "f\n", "mutations.json": "[]", "disposition.md": "d\n"})
        self.assertEqual(set(f), {("T1", "refactor.byte-identical")})
        flag = f[("T1", "refactor.byte-identical")]
        self.assertEqual(flag["folders"], ["2026-06-12-T1-step2-review"])
        self.assertIn("byte-identical gates", flag["next"])
        self.assertNotIn("predict-gate.sh close", flag["next"])
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "refactor-unproven")

    def test_every_step_proven_owes_no_close(self):
        f = self.steps({"byte-identical.txt": "0 diffs\n"}, {"byte-identical.txt": "0 diffs\n"})
        self.assertEqual(f, {})
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "not-owed")

    def test_a_refactor_step_with_a_prediction_is_checked_as_a_change(self):
        f = self.steps({"byte-identical.txt": "0 diffs\n"}, TRIO)
        self.assertEqual(f, {})
        self.assertEqual(self.reg()["workOrders"]["T1"]["jobs"], ["change", "refactor"])
        self.record("2026-06-12-T1-step2-review", {"moves.measured.json": ""})
        self.run_check()
        self.assertEqual(set(self.flags()), {("T1", "change.measured")})

    def test_a_result_in_a_refactor_step_does_not_make_it_a_change(self):
        f = self.steps({"byte-identical.txt": "0 diffs\n", "result.md": "r\n"})
        self.assertEqual(f, {})


class TestCloseIsItsOwn(Fixture):
    def test_a_close_hashing_a_file_outside_the_record_does_not_count(self):
        self.commit("T1: the change", {"src/a.py": "x\n"})
        self.record("2026-06-10-T1-step1-review", TRIO)
        self.close_event("T1", f"src/a.py sha256:{self.sha('src/a.py')}")
        self.run_check()
        f = self.flags()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "not-its-record")
        self.assertIn("outside this work order's record folders: src/a.py", f[("T1", "close")]["what"][0])
        self.assertIn("does not count", f[("T1", "close")]["next"])

    def test_a_close_carrying_another_work_orders_record_does_not_count(self):
        self.closed_record("T1", "2026-06-10-T1-step1-review")
        self.commit("T2: other change", {"src/z.py": "z\n"})
        self.close_event("T2", json.loads(self.ledger.read_text().splitlines()[0])["note"])
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T2"]["close"], "not-its-record")
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "closed")

    def test_the_baseline_hash_is_checked(self):
        d = self.closed_record()
        self.commit("edit the baseline after close", {f"{d}/moves.baseline.json": json.dumps({"count:x": 0})})
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "hash-mismatch")
        self.assertIn(f"does not match {d}/moves.baseline.json", self.flags()[("T1", "close")]["what"][0])

    def test_a_close_older_than_a_later_code_commit_is_stale(self):
        self.closed_record()
        self.commit("T1 run 2: a later change", {"src/c.py": "c\n"}, date="2026-06-12T12:00:00")
        self.run_check()
        f = self.flags()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "stale")
        self.assertIn("is older than the work order's last code commit", f[("T1", "close")]["what"][0])
        self.assertIn("new prediction round", f[("T1", "close")]["next"])

    def measured_after_code(self, day):
        """Prediction, code, measurement, one more code commit, then a close an hour later (review A4 repro 2)."""
        d = f"reviews/{day}-T1-step1-review"
        self.commit("T1: prediction", {f"{d}/moves.prediction.json": PRED, f"{d}/moves.baseline.json": BASE},
                    date=f"{day}T09:00:00")
        self.commit("T1: the change", {"src/a.py": "x\n"}, date=f"{day}T09:01:00")
        self.commit("T1: measurement", {f"{d}/moves.measured.json": MEAS, f"{d}/result.md": TRIO["result.md"]},
                    date=f"{day}T09:02:00")
        self.commit("T1: one more fix", {"src/a.py": "y\n"}, date=f"{day}T09:03:00")
        self.close_event("T1", " ".join(f"{d}/{n} sha256:{self.sha(f'{d}/{n}')}," for n in
                                        ("moves.prediction.json", "moves.measured.json"))
                         + f" baseline {d}/moves.baseline.json (sha256 {self.sha(d + '/moves.baseline.json')})",
                         ts=f"{day}T23:00:00+00:00")
        self.run_check(as_of=day)

    def test_code_after_the_measurement_is_stale_though_the_close_is_later(self):
        self.measured_after_code("2026-11-10")
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "stale")
        self.assertIn("is later than its latest measured file's commit", self.flags()[("T1", "close")]["what"][0])

    def test_a_record_before_the_cut_off_keeps_the_close_time_rule(self):
        # named limit (owner d20261103-fb2f2c): a record with no round from ORDER_FROM keeps the close-time rule
        self.measured_after_code("2026-10-20")
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "closed")

    def test_a_close_with_no_time_is_stale(self):
        d = self.closed_record()
        note = json.loads(self.ledger.read_text().splitlines()[0])["note"]
        self.close_event("T1", note, ts=None)
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "stale")
        self.assertIn("has no time", self.flags()[("T1", "close")]["what"][0])


class TestDesignFolders(Fixture):
    def test_a_clean_close_supersedes_a_design_folders_flags(self):
        self.commit("T1: the design's spike", {"src/b.py": "x\n"})
        self.record("2026-06-09-T1-design", {"prediction.md": "We expect x to move.\n", "design.md": "d\n"})
        self.run_check()
        self.assertEqual({f["state"] for f in self.reg()["flags"] if f["rule"] != "close"}, {"open"})
        self.closed_record()
        code, out = self.run_check()
        f = self.flags()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "closed")
        self.assertEqual(f[("T1", "change.prediction")]["state"], "superseded")
        self.assertEqual(self.reg()["computed"]["openFlags"], 0)
        self.assertIn("(superseded: a design or planning folder", out)

    def test_a_step_folders_flag_is_never_superseded(self):
        self.closed_record()
        self.record("2026-06-10-T1-step2-review", {"gates.md": "g\n"})
        self.run_check()
        f = self.flags()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "closed")
        self.assertEqual(f[("T1", "change.prediction")]["state"], "open")


class TestWindowAndRegister(Fixture):
    def setUp(self):
        super().setUp()
        self.commit("T1: the change", {"src/a.py": "x\n"})

    def test_a_future_since_is_refused(self):
        self.bind(records={"since": "2999-01-01"})
        code, out = self.run_check()
        self.assertEqual(code, 2, out)
        self.assertIn("is in the future", out)

    def test_a_since_after_as_of_warns(self):
        code, out = self.run_check("--since", "2026-06-20")
        self.assertEqual(code, 0, out)
        self.assertIn("WARNING: the window starts (2026-06-20) after --as-of (2026-06-15)", out)
        self.assertEqual(len(self.reg()["computed"]["warnings"]), 1)
        self.run_check()
        self.assertEqual(self.reg()["computed"]["warnings"], [])

    def test_code_commits_that_name_no_tracker_id_are_listed(self):
        self.commit("F2: a fix round step", {"src/b.py": "x\n"})
        code, out = self.run_check()
        listed = self.reg()["uncreditedCommits"]
        self.assertEqual([x.split(" ", 1)[1] for x in listed], ["F2: a fix round step"])
        self.assertEqual(self.reg()["computed"]["uncredited"], 1)
        self.assertIn(f"- {listed[0]}", out)
        self.assertIn("starts with its work-order id", out)

    def test_the_register_holds_no_as_of_or_head(self):
        self.bind(records={"since": "2026-06-01"})
        code, out = self.run_check(as_of="2026-06-15")
        first = self.register.read_bytes()
        self.assertNotIn("asOf", self.reg()["computed"])
        self.assertNotIn("head", self.reg()["computed"])
        self.assertIn("2026-06-01 to 2026-06-15 at `", out)
        self.commit("docs: not code", {"docs/a.md": "a\n"})
        self.run_check(as_of="2026-06-16")
        self.assertEqual(self.register.read_bytes(), first)



class TestTemplates(unittest.TestCase):
    """T8 fix round 1, F4: every brief template carries the `Record folder:` line, and the stop hook's own
    reader gives back the folder the PM fills in, so the templates and the hook cannot drift apart."""

    def test_each_brief_template_has_the_line_the_hook_reads(self):
        import importlib.util
        import re
        root = SCRIPT.parent.parent
        spec = importlib.util.spec_from_file_location("agent_roles", root / "runtime" / "agent-roles.py")
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        for path in ("change.md", "refactor.md"):
            text = (root / "commands" / path).read_text()
            for brief in ("Engineer", "Reviewer"):
                with self.subTest(path=path, brief=brief):
                    m = re.search(rf"^### {brief} brief\n.*?^```text\n(.*?)^```", text, re.MULTILINE | re.DOTALL)
                    self.assertIsNotNone(m, f"{path} has no {brief} brief block")
                    block = m.group(1)
                    lines = [x for x in block.splitlines() if "Record folder:" in x]
                    self.assertEqual(len(lines), 1, f"{path} {brief} brief: {lines}")
                    self.assertTrue(lines[0].startswith("Record folder: ["), lines[0])
                    holder = lines[0][len("Record folder: "):].rstrip(".")
                    for folder in ("~/perma/team/widget/design/w1", "records/2026-01-03-T7-step1-review"):
                        self.assertEqual(hook.line_value(block.replace(holder, folder + "/")), folder)

    def test_each_brief_template_fills_a_full_path_the_dispatch_accepts(self):
        """T8 fix round 2: the hook decides the record folder at dispatch and refuses a relative line, so
        each template's placeholder must fill to a full path, and the filled brief must be accepted."""
        import importlib.util
        import re
        root = SCRIPT.parent.parent
        spec = importlib.util.spec_from_file_location("agent_roles_fill", root / "runtime" / "agent-roles.py")
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            repo = t / "repo"
            (repo / ".team").mkdir(parents=True)
            (repo / ".team" / "binding.json").write_text(
                json.dumps({"owned": True, "reviews": "records/", "reviewName": "{date}-{id}-step{n}-review"}))
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            for args in (("worktree", "add", "-q", "--orphan", "-b", "team-records", str(t / "repo.team-records")),
                         ("config", "bakehouse.records", str(t / "repo.team-records"))):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
            hook.LOG = t / "log.txt"
            terms = {"{root}": f"{repo}/", "binding.reviews": "records/", "binding.reviewName": "2026-01-03-T7-step1-review"}
            for path in ("change.md", "refactor.md"):
                text = (root / "commands" / path).read_text()
                for brief in ("Engineer", "Reviewer"):
                    with self.subTest(path=path, brief=brief):
                        block = re.search(rf"^### {brief} brief\n.*?^```text\n(.*?)^```", text, re.MULTILINE | re.DOTALL).group(1)
                        holder = re.search(r"^Record folder: (\[[^\]\n]*\])", block, re.MULTILINE).group(1)
                        # the placeholder's terms, in the order it names them: a fill without {root} is relative
                        fill = "".join(v for _, v in sorted((holder.find(k), v) for k, v in terms.items() if k in holder))
                        self.assertTrue(fill.startswith(("/", "~")), f"{path} {brief}: the line fills to {fill!r}")
                        got = hook.dispatch_decision(block.replace(holder, fill), str(repo), True)
                        self.assertEqual((got[0], got[1][0], got[1][2]),
                                         ("record", "repo", "records/2026-01-03-T7-step1-review"), got)
                        relative = hook.dispatch_decision(block.replace(holder, fill[len(f"{repo}/"):]), str(repo), True)
                        self.assertEqual(relative[0], "refuse")

if __name__ == "__main__":
    unittest.main()


class TestCloseHashesItsRecord(Fixture):
    def test_a_close_hashing_only_a_result_in_its_own_step_folder_does_not_count(self):
        self.commit("T1: change", {"src/a.py": "x\n"})
        self.record("2026-06-10-T1-step1-review", {"result.md": "done\n"})
        self.record("2026-06-09-T1-design", {"prediction.md": "x moves\n"})
        p = "reviews/2026-06-10-T1-step1-review/result.md"
        self.close_event("T1", f"{p} sha256:{self.sha(p)}")
        self.run_check()
        f = self.flags()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "not-its-record")
        self.assertIn("holds no prediction", f[("T1", "close")]["what"][0])
        self.assertEqual({v["state"] for v in f.values()}, {"open"})

    def test_a_close_carrying_none_of_its_records_files_does_not_count(self):
        self.commit("T1: change", {"src/a.py": "x\n"})
        self.record("2026-06-10-T1-step1-review", TRIO)
        p = "reviews/2026-06-10-T1-step1-review/result.md"
        self.close_event("T1", f"{p} sha256:{self.sha(p)}")
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "not-its-record")
        self.assertIn("carries none of the files", self.flags()[("T1", "close")]["what"][0])

    def test_a_round_added_after_the_close_is_a_mismatch(self):
        d = self.closed_record()
        self.record("2026-06-10-T1-step1-review", {"moves.prediction.v2.json": PRED, "moves.measured.v2.json": MEAS})
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "hash-mismatch")
        self.assertIn(f"does not carry the sha256 of {d}/moves.measured.v2.json, {d}/moves.prediction.v2.json",
                      self.flags()[("T1", "close")]["what"][0])

    def test_a_hand_written_override_with_no_gate_refused_event_does_not_count(self):
        self.commit("T1: change", {"src/a.py": "x\n"})
        self.close_event("T1", "OVERRIDDEN by me")
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "not-its-record")
        self.assertIn("no gate-refused event", self.flags()[("T1", "close")]["what"][0])

    def test_a_gate_refused_event_after_the_close_does_not_back_it(self):
        self.commit("T1: change", {"src/a.py": "x\n"})
        self.close_event("T1", "OVERRIDDEN, see the gate-refused line; no pair passed")
        self.refused_event("T1", purpose=CLOSE_REFUSED)
        self.refused_event("T2", purpose=CLOSE_REFUSED)
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "not-its-record")

    def test_another_work_orders_gate_refused_event_does_not_back_it(self):
        self.commit("T1: change", {"src/a.py": "x\n"})
        self.refused_event("T2", purpose=CLOSE_REFUSED)
        self.close_event("T1", "OVERRIDDEN, see the gate-refused line; no pair passed")
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "not-its-record")

    def test_an_override_still_hashes_only_its_own_files(self):
        self.commit("T1: change", {"src/a.py": "x\n"})
        self.record("2026-06-10-T1-step1-review", {"prediction.md": "p\n"})
        self.refused_event("T1", purpose=CLOSE_REFUSED)
        self.close_event("T1", f"OVERRIDDEN, see the gate-refused line; src/a.py sha256:{self.sha('src/a.py')}")
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "not-its-record")

    def test_a_nested_path_inside_its_own_step_folder_is_not_its_record(self):
        d = self.closed_record()
        self.record("2026-06-10-T1-step1-review", {"history/old.json": "{}"})
        note = json.loads(self.ledger.read_text().splitlines()[0])["note"]
        n = f"{d}/history/old.json"
        self.close_event("T1", note.replace("; ledger", f", {n} sha256:{self.sha(n)}; ledger"))
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "not-its-record")
        self.assertIn(n, self.flags()[("T1", "close")]["what"][0])

    def test_it_needs_only_the_files_predict_gate_close_reads(self):
        self.closed_record()
        self.record("2026-06-11-T1-step2-review", TRIO)
        self.record("2026-06-12-t1-step1-review", TRIO)
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "closed")

    def test_a_close_pointing_at_a_design_folder_is_not_its_record(self):
        self.closed_record()
        self.record("2026-06-09-T1-design", {"design.md": "d\n"})
        note = json.loads(self.ledger.read_text().splitlines()[0])["note"]
        n = "reviews/2026-06-09-T1-design/design.md"
        self.close_event("T1", note.replace("; ledger", f", {n} sha256:{self.sha(n)}; ledger"))
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "not-its-record")


class TestRealClose(Fixture):
    """A close and an override written by the runtime's own `predict-gate.sh close`, then read back."""

    def setUp(self):
        super().setUp()
        self.env = dict(ENV)
        for k in ("PREDICT_GATE_OVERRIDE", "PREDICT_GATE_CLOSE_OVERRIDE"):
            self.env.pop(k, None)

    def commit(self, subject, files, date=None):
        for path, text in files.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text)
        env = dict(ENV, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date) if date else ENV
        for args in (("add", "-A"), ("commit", "-qm", subject)):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, env=env)

    def close(self, **env):
        return subprocess.run([str(SCRIPT.parent / "predict-gate.sh"), "close", "T1", "--repo", str(self.repo)],
                              capture_output=True, text=True, env=dict(self.env, **env))

    def check(self):
        code, out = self.run_check(as_of=subprocess.run(["date", "+%Y-%m-%d"], capture_output=True,
                                                        text=True).stdout.strip())
        self.assertEqual(code, 0, out)
        return self.reg()

    def test_a_close_written_by_predict_gate_counts(self):
        # prediction, code, measurement, a minute apart: from predict-gate's ORDER_FROM the close checks that order
        d, now = "reviews/2026-06-10-T1-step1-review", datetime.now().astimezone()
        at = [(now - timedelta(minutes=m)).isoformat(timespec="seconds") for m in (3, 2, 1)]
        self.commit("T1: prediction", {f"{d}/moves.prediction.json": PRED, f"{d}/moves.baseline.json": BASE}, at[0])
        self.commit("T1: the change", {"src/a.py": "x\n"}, at[1])
        self.commit("T1: measurement", {f"{d}/moves.measured.json": MEAS, f"{d}/result.md": TRIO["result.md"]}, at[2])
        p = self.close()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        r = self.check()
        self.assertEqual(r["workOrders"]["T1"]["close"], "closed")
        self.assertEqual(r["flags"], [])

    def test_an_override_written_by_predict_gate_counts(self):
        self.commit("T1: the change", {"src/a.py": "x\n"})
        self.record("2026-06-10-T1-step1-review", {"prediction.md": "prose\n", "result.md": "r\n"})
        self.assertEqual(self.close().returncode, 1)
        p = self.close(PREDICT_GATE_CLOSE_OVERRIDE="owner go")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        r = self.check()
        self.assertEqual(r["workOrders"]["T1"]["close"], "overridden")
        self.assertEqual(r["computed"]["openFlags"], 0)
        self.assertIn(("T1", "change.prediction"), self.flags())


class TestCloseRunsTheGate(Fixture):
    F = "2026-06-10-T1-step1-review"

    def hand_close(self):
        d = f"reviews/{self.F}"
        self.close_event("T1", f"{d}/moves.prediction.json sha256:{self.sha(d + '/moves.prediction.json')} "
                               f"commits abc, {d}/moves.measured.json sha256:{self.sha(d + '/moves.measured.json')}, "
                               f"baseline {d}/moves.baseline.json (sha256 {self.sha(d + '/moves.baseline.json')})")

    def test_a_hand_written_close_on_a_trio_the_gate_passes_counts(self):
        self.commit("T1: change", {"src/a.py": "x\n"})
        self.record(self.F, TRIO)
        self.hand_close()
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "closed")
        self.assertEqual(self.reg()["computed"]["openFlags"], 0)

    def test_a_close_on_a_trio_the_gate_refuses_does_not_count(self):
        self.commit("T1: change", {"src/a.py": "x\n"})
        self.record(self.F, dict(TRIO, **{"moves.measured.json": json.dumps({"count:x": 99, "count:y": 7})}))
        self.hand_close()
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "not-its-record")
        self.assertIn("predict-gate's own check refuses", self.flags()[("T1", "close")]["what"][0])

    def test_another_gates_refusal_does_not_back_an_override(self):
        self.commit("T1: change", {"src/a.py": "x\n"})
        self.refused_event("T1", purpose="predicted-moves gate overridden")
        self.close_event("T1", "OVERRIDDEN, see the gate-refused line; no pair passed")
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "not-its-record")

    def test_an_event_between_the_refusal_and_the_override_breaks_it(self):
        # V12c step 2a, item 4: the event that breaks it is an earlier close, which used that refusal up
        self.commit("T1: change", {"src/a.py": "x\n"})
        self.refused_event("T1", purpose=CLOSE_REFUSED)
        self.close_event("T1", "OVERRIDDEN by me")
        self.close_event("T1", "OVERRIDDEN by me, again")
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "not-its-record")

    def test_another_event_of_the_work_order_between_the_refusal_and_the_override_does_not_break_it(self):
        # V12c step 2a, item 4: a seal from another lane can land between the two lines
        self.commit("T1: change", {"src/a.py": "x\n"})
        self.refused_event("T1", purpose=CLOSE_REFUSED)
        with self.ledger.open("a") as f:
            f.write(json.dumps({"ts": "2026-06-11T00:00:00+00:00", "kind": "agent-run", "task": "T1"}) + "\n")
        self.close_event("T1", "OVERRIDDEN, see the gate-refused line; no pair passed")
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "overridden")

    def test_an_override_right_after_the_close_refusal_counts(self):
        self.commit("T1: change", {"src/a.py": "x\n"})
        self.refused_event("T2", purpose=CLOSE_REFUSED)
        self.refused_event("T1", purpose=CLOSE_REFUSED)
        self.refused_event("T2")
        self.close_event("T1", "OVERRIDDEN, see the gate-refused line; no pair passed")
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "overridden")


class TestRefactorProofTiming(Fixture):
    def test_a_code_commit_after_the_latest_proof_is_flagged(self):
        self.commit("T1: refactor step", {"src/a.py": "x\n"})
        self.record("2026-06-10-T1-step1-review", {"byte-identical.txt": "0 diffs\n"})
        self.commit("T1: later behaviour change", {"src/a.py": "changed\n"})
        self.run_check()
        f = self.flags()
        self.assertEqual(set(f), {("T1", "refactor.byte-identical")})
        self.assertIn("1 code commit(s) come after the latest step proof", f[("T1", "refactor.byte-identical")]["what"][0])
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "refactor-unproven")

    def test_a_later_run_in_an_off_pattern_folder_is_flagged(self):
        self.commit("T1: refactor step", {"src/a.py": "x\n"})
        self.record("2026-06-10-T1-step1-review", {"byte-identical.txt": "BASE vs step: 0 diffs\n"})
        self.commit("T1 run 2: a behaviour change", {"src/b.py": "y\n"})
        self.record("2026-06-11-T1-run2", {"gates.md": "gates passed\n"})
        self.run_check()
        self.assertEqual(set(self.flags()), {("T1", "refactor.byte-identical")})

    def test_a_proof_only_outside_the_step_folders_is_flagged(self):
        self.commit("T1: refactor", {"src/a.py": "x\n"})
        self.record("2026-06-10-T1-census", {"byte-identical.txt": "0 diffs\n"})
        self.run_check()
        self.assertIn("no step folder holds one", self.flags()[("T1", "refactor.byte-identical")]["what"][0])

    def test_a_meaning_change_steps_measurement_proves_its_code(self):
        self.commit("T1: step 1 move", {"src/a.py": "x\n"})
        self.record("2026-06-10-T1-step1-review", {"byte-identical.txt": "0 diffs\n"})
        self.commit("T1: step 2 meaning change", {"src/b.py": "y\n"})
        self.record("2026-06-11-T1-step2-review", {"moves.prediction.json": PRED, "moves.baseline.json": BASE})
        self.run_check()
        self.assertIn(("T1", "refactor.byte-identical"), self.flags())
        self.record("2026-06-11-T1-step2-review", {"moves.measured.json": MEAS, "result.md": "r\n"})
        self.run_check()
        self.assertEqual(self.flags(), {})
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "not-owed")

    def test_a_byte_identical_file_beside_a_prediction_makes_no_refactor(self):
        self.commit("T1: a behaviour change", {"src/a.py": "x\n"})
        self.record("2026-06-10-T1-step1-review", dict(TRIO, **{"byte-identical.txt": "mustNotMove: 0 diffs\n"}))
        self.run_check()
        self.assertEqual(set(self.flags()), {("T1", "close")})
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "not-closed")


class TestRecordEdges(Fixture):
    def test_a_step_name_with_a_suffix_is_not_a_step_folder(self):
        self.closed_record()
        self.record("2026-06-10-T1-step1-review-notes", {"result.md": "r\n"})
        self.run_check()
        f = self.flags()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "closed")
        self.assertEqual(f[("T1", "change.prediction")]["state"], "superseded")

    def test_a_close_time_with_no_timezone_is_read_as_local(self):
        self.closed_record()
        ev = json.loads(self.ledger.read_text().splitlines()[0])
        ev["ts"] = "2026-06-11T00:00:00"
        self.ledger.write_text(json.dumps(ev) + "\n")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "closed")

    def test_a_dot_slash_reviews_is_normalised(self):
        self.bind(reviews="./reviews/")
        self.closed_record()
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "closed")
        self.assertEqual(self.reg()["computed"]["reviews"], "reviews/")


class TestStreamCurrency(Fixture):
    """The ops ledger keeps up with the repo."""

    def currency(self):
        return [f for f in self.reg()["flags"] if f["rule"] == "stream.currency"]

    def test_an_ops_ledger_behind_the_repo_by_the_hooks_threshold_flags(self):
        self.close_event("T0", "x", ts="2026-06-01T00:00:00+00:00")
        for i in range(5):
            self.commit(f"c{i}", {f"f{i}": "x\n"})
        code, out = self.run_check("--code-roots", "src/")
        self.assertEqual(code, 0, out)
        [f] = self.currency()
        self.assertEqual((f["workOrder"], f["owner"], f["state"], f["next"]),
                         ("(stream)", "pm", "open", "bring the ops ledger up to date with the repository's commits"))
        self.assertEqual(f["what"], ["ops ledger is 5 commit(s) behind — last event 2026-06-01T00:00:00+00:00 "
                                     "(the post-commit hook's warning)"])
        self.assertIn("Rule `stream.currency` (owner: pm):", out)


KILLED = {"id": "M1", "mutation": "guard removed", "site": "src/a.py:3", "gate": "tests/test_a.py", "verdict": "killed"}
SURVIVED = {"id": "M2", "mutation": "edge moved", "site": "src/a.py:9", "gate": "tests/test_a.py",
            "verdict": "survived", "disposition": "test-added tests/test_b.py"}
STEP = "2026-06-10-T1-step1-review"
# a findings file added before the rounds date is history and owes no mutation file
FROM = json.loads(CONTRACTS.read_text())["jobs"]["reviewer"]["rounds"]["from"] + "T12:00:00"


class TestMutations(Fixture):
    """The reviewer's mutation record (T3): a reviewer record, at any depth, holds mutations.json."""

    def fresh(self):
        self.tearDown()
        self.setUp()

    def review(self, rows, folder=STEP + "/review", raw=None, extra=None, close=False):
        if close:
            self.closed_record()
        else:
            self.commit("T1: the change", {"src/a.py": "x\n"})
            self.record(STEP, TRIO)
        # the real order: the review's findings, then the fix round's tests, then the settled mutation record
        self.commit("record", {f"reviews/{folder}/findings.md": "F1 nothing\n"}, date=FROM)
        self.commit("tests", {"tests/test_a.py": "t\n", "tests/test_b.py": "def test_edge():\n    pass\n", **(extra or {})})
        if rows is not None or raw is not None:
            self.record(folder, {"mutations.json": raw if raw is not None else json.dumps(rows)})
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        return self.flags().get(("T1", "reviewer.mutations")), out

    def what(self, rows, **kw):
        flag, _ = self.review(rows, **kw)
        return " | ".join(flag["what"]) if flag else ""

    def test_the_shipped_reviewer_contract_checks_mutations_at_any_depth(self):
        rev = json.loads(CONTRACTS.read_text())["jobs"]["reviewer"]
        self.assertEqual(rev["require"], [{"id": "mutations", "check": "mutations", "glob": "mutations*.json",
                                           "per": "findings*.md"},
                                          {"id": "findings", "check": "nonempty", "glob": "findings*.md",
                                           "per": "findings*.md"}])
        self.assertIs(rev["nested"], True)
        self.review([KILLED])
        c = self.reg()["contracts"]["reviewer"]
        self.assertEqual((c["require"], c["inactive"]), (["mutations", "findings"], []))

    def test_b4_an_empty_findings_file_is_flagged_for_the_reviewer(self):
        self.commit("T1: the change", {"src/a.py": "x\n"})
        self.record(STEP, TRIO)
        self.commit("record", {f"reviews/{STEP}/review/findings.md": "F1\n",
                               f"reviews/{STEP}/review/findings-fix1.md": "",
                               f"reviews/{STEP}/review/mutations.json": json.dumps([KILLED]),
                               f"reviews/{STEP}/review/mutations-fix1.json": json.dumps([KILLED]),
                               "tests/test_a.py": "t\n"}, date=FROM)
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.flags().get(("T1", "reviewer.mutations"), {}).get("what"), None)
        flag = self.flags()[("T1", "reviewer.findings")]
        self.assertEqual(flag["what"], [STEP + "/review: findings-fix1.md is empty"])
        self.assertEqual((flag["owner"], flag["state"]), ("reviewer", "open"))
        self.assertNotIn(("T1", "reviewer.mutations"), self.flags())

    def test_a_nested_review_without_mutations_json_is_flagged_for_the_reviewer(self):
        flag, out = self.review(None)
        self.assertEqual(flag["folders"], [STEP + "/review"])
        self.assertEqual(flag["what"], [STEP + "/review: no file matches mutations.json"])
        self.assertEqual((flag["owner"], flag["state"]), ("reviewer", "open"))
        self.assertIn("mutations.json beside findings.md", flag["next"])
        self.assertIn("reviewer.mutations", out)
        self.assertIn("reviewer", self.reg()["workOrders"]["T1"]["jobs"])

    def test_a_top_level_reviewer_record_is_checked_too(self):
        flag, _ = self.review(None, folder="2026-06-11-T1-final-review")
        self.assertEqual(flag["folders"], ["2026-06-11-T1-final-review"])

    def test_a_complete_record_has_no_flag_and_is_counted_per_gate(self):
        flag, out = self.review([KILLED, SURVIVED, dict(KILLED, id="M3", gate=["tests/test_a.py", "tests"])])
        self.assertIsNone(flag)
        m = self.reg()["mutations"]
        self.assertEqual((m["records"], m["rows"]), (1, 3))
        self.assertEqual(m["gates"], {"tests/test_a.py": {"killed": 1, "survived": 1, "records": 1, "workOrders": ["T1"]},
                                      "tests/test_a.py + tests": {"killed": 1, "survived": 0, "records": 1,
                                                                  "workOrders": ["T1"]}})
        self.assertIn("Kill rate per gate (reported, never a target): 3 mutation row(s) in 1", out)
        self.assertIn("| tests/test_a.py | 1 | 1 | T1 |", out)

    def test_an_empty_list_is_a_record_of_no_mutation(self):
        self.assertEqual(self.what([]), "")
        self.assertEqual(self.reg()["mutations"]["rows"], 0)

    def test_a_survivor_with_no_disposition_is_flagged(self):
        for d in ({"disposition": None}, {"disposition": ""}, {}):
            with self.subTest(d=d):
                self.fresh()
                row = {k: v for k, v in SURVIVED.items() if k != "disposition"}
                self.assertIn("M2 survived and has no disposition", self.what([KILLED, dict(row, **d)]))
                self.assertEqual(self.reg()["mutations"]["gates"]["tests/test_a.py"]["survived"], 1)

    def test_a_disposition_is_one_of_the_two_forms(self):
        cases = [("test-added tests/missing.py", "not a path in the repository"),
                 ("accepted: no input today", "does not state its reach"),
                 ("fixed later", "neither `test-added <path>` nor `accepted: <reason>`"),
                 ("accepted: reach 0 of 35 files", None),
                 ("test-added tests/test_b.py::test_edge", None)]
        for d, want in cases:
            with self.subTest(d=d):
                self.fresh()
                got = self.what([dict(SURVIVED, disposition=d)])
                if want:
                    self.assertIn(want, got)
                else:
                    self.assertEqual(got, "")

    def test_test_added_names_a_test_file(self):
        extra = {"README.md": "r\n", "docs/notes.md": "## Heading\n"}
        cases = [("test-added README.md", "which is not a test file: it is not under the test roots (tests/, test/)"),
                 ("test-added docs/PLAN.md", "not a path in the repository"),
                 ("test-added tests", "which is a folder, not a test file"),
                 ("test-added tests/test_a.py::test_x", 'names "test_x", which tests/test_a.py does not hold'),
                 ("test-added tests/test_b.py::test_ed", 'names "test_ed", which tests/test_b.py does not hold'),
                 ("test-added tests/test_b.py", None),
                 ("test-added tests/test_b.py::test_edge[one]", None),
                 ("test-added docs/notes.md::Heading", 'names "Heading", which docs/notes.md does not hold')]
        for d, want in cases:
            with self.subTest(d=d):
                self.fresh()
                got = self.what([dict(SURVIVED, disposition=d)], extra=extra)
                if want:
                    self.assertIn(want, got)
                else:
                    self.assertEqual(got, "")

    def test_the_binding_may_name_its_test_roots(self):
        for d, want in (("test-added spec/a_spec.rb", None), ("test-added tests/test_b.py", "(spec/)")):
            with self.subTest(d=d):
                self.fresh()
                self.bind(records={"testRoots": ["spec"]})
                got = self.what([dict(SURVIVED, disposition=d)], extra={"spec/a_spec.rb": "s\n"})
                if want:
                    self.assertIn(want, got)
                else:
                    self.assertEqual(got, "")
        for roots in ("tests/", [], ["/abs"], ["../up"], [""]):
            with self.subTest(roots=roots):
                self.bind(records={"testRoots": roots})
                code, out = self.run_check()
                self.assertEqual(code, 2, out)
                self.assertIn("'records.testRoots'", out)

    def test_accepted_states_its_reach_as_a_count(self):
        cases = [("accepted: reach", False), ("accepted: no reach stated", False),
                 ("accepted: overreaching test", False), ("accepted: reach unknown", False),
                 ("accepted: reach 0 today", True), ("accepted: reach: 3 of 35 files", True),
                 ("accepted: no input has it; reach is 0", True)]
        for d, ok in cases:
            with self.subTest(d=d):
                self.fresh()
                got = self.what([dict(SURVIVED, disposition=d)])
                if ok:
                    self.assertEqual(got, "")
                else:
                    self.assertIn("M2's accepted disposition does not state its reach as a count", got)

    def test_an_open_survivor_names_a_finding_and_stays_flagged(self):
        got = self.what([KILLED, dict(SURVIVED, disposition="open: F1")])
        self.assertEqual(got, STEP + "/review: mutations.json: M2 survived and is open (F1): the round that "
                              "settles it writes `test-added` or `accepted:` in its row")
        self.assertEqual(self.reg()["mutations"]["gates"]["tests/test_a.py"]["survived"], 1)
        for d in ("open: F9", "open: F", "open:"):
            with self.subTest(d=d):
                self.fresh()
                self.assertIn("which the findings file beside it does not name",
                              self.what([dict(SURVIVED, disposition=d)]))

    def test_a_killed_row_carries_no_disposition(self):
        self.assertIn("M1 is killed and carries a disposition", self.what([dict(KILLED, disposition="accepted: reach 0")]))

    def test_the_gate_is_a_gate_id_from_the_binding_or_a_path(self):
        self.bind(gates={"run": "gates.json#gates", "one": "recipe.yaml#gates.lint"})
        extra = {"gates.json": json.dumps({"gates": [{"id": "corpus"}, {"id": "pins"}]}),
                 "recipe.yaml": "gates:\n  - id: lint\n    command: ruff\n  - id: other\n"}
        cases = [("corpus", None), ("lint", None), ("tests/test_a.py::test_x", None), ("tests", None),
                 ("other", "neither a gate id"), ("one product's pre-process", "neither a gate id"),
                 ("tests/missing.py", "neither a gate id"), ("../outside.py", "neither a gate id"),
                 (["corpus", "tests/missing.py"], "neither a gate id"), ([], "has no gate")]
        for g, want in cases:
            with self.subTest(g=g):
                self.fresh()
                self.bind(gates={"run": "gates.json#gates", "one": "recipe.yaml#gates.lint"})
                got = self.what([dict(KILLED, gate=g)], extra=extra)
                if want:
                    self.assertIn(want, got)
                else:
                    self.assertEqual(got, "")

    def test_a_gate_is_checked_at_the_records_own_commit(self):
        flag, _ = self.review([dict(KILLED, gate="tests/test_c.py"), dict(SURVIVED, gate="tests/test_c.py")],
                              extra={"tests/test_c.py": "t\n"})
        self.assertIsNone(flag)
        self.git("rm", "-q", "tests/test_c.py", "tests/test_b.py")
        self.git("commit", "-qm", "tests renamed")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertNotIn(("T1", "reviewer.mutations"), self.flags())
        m = self.reg()["mutations"]
        self.assertEqual((m["rows"], m["gates"]["tests/test_c.py"]["killed"]), (2, 1))

    def test_the_verdict_is_killed_or_survived_in_any_case(self):
        self.assertEqual(self.what([dict(KILLED, verdict="KILLED")]), "")
        self.fresh()
        self.assertIn("is not killed or survived", self.what([dict(KILLED, verdict="caught")]))

    def test_a_record_that_is_not_the_format_is_flagged(self):
        cases = [("", "mutations.json is empty"), ("{", "is not JSON"), ('{"M1": "killed"}', "is not a JSON list"),
                 ("[1]", "row 1 is not an object"),
                 (json.dumps([{k: v for k, v in KILLED.items() if k != "site"}]), "M1 has no site"),
                 (json.dumps([KILLED, KILLED]), "M1 repeats an id")]
        for raw, want in cases:
            with self.subTest(raw=raw):
                self.fresh()
                self.assertIn(want, self.what(None, raw=raw))

    def test_a_review_two_levels_below_a_record_folder_is_read(self):
        flag, _ = self.review(None, folder=STEP + "/fix1/review")
        self.assertEqual(flag["folders"], [STEP + "/fix1/review"])
        self.assertEqual(flag["what"], [STEP + "/fix1/review: no file matches mutations.json"])

    def test_an_inactive_artefact_is_never_checked(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("records_check_inactive", SCRIPT)
        rc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rc)
        job = {"marks": ["n.md"], "require": [{"id": "x", "check": "nonempty", "glob": "x.md", "active": False},
                                              {"id": "y", "check": "nonempty", "glob": "y.md"}]}
        self.assertEqual(rc.check_folder({}, job, None), [("y", "nonempty", "no file matches y.md")])

    def test_only_a_nested_job_is_read_below_a_record_folder(self):
        self.review([KILLED])
        self.record(STEP + "/fix1", {"prediction.md": "prose\n"})
        self.run_check()
        self.assertEqual(set(self.flags()), {("T1", "close")})
        self.fresh()
        self.commit("T1: the change", {"src/a.py": "x\n"})
        self.record(STEP, TRIO)
        self.record(STEP + "/fix1", {"prediction.md": "prose\n"})
        self.run_check()
        self.assertEqual(self.reg()["workOrders"]["T1"]["jobs"], ["change"])

    def test_a_recheck_in_the_same_folder_owes_its_own_mutations_file(self):
        self.assertIsNone(self.review([KILLED])[0])
        rev = json.loads(CONTRACTS.read_text())["jobs"]["reviewer"]["require"][0]
        self.assertEqual((rev["glob"], rev["per"]), ("mutations*.json", "findings*.md"))
        self.commit("record", {f"reviews/{STEP}/review/{n}": t for n, t in
                               {"findings-fix1.md": "F1 again\n", "mutations-raw.json": "not read"}.items()}, date=FROM)
        self.run_check()
        flag = self.flags()[("T1", "reviewer.mutations")]
        self.assertEqual(flag["what"], [STEP + "/review: no file matches mutations-fix1.json"])
        self.record(STEP + "/review", {"mutations-fix1.json": json.dumps([dict(KILLED, id="R1")])})
        self.run_check()
        self.assertNotIn(("T1", "reviewer.mutations"), self.flags())
        m = self.reg()["mutations"]
        self.assertEqual((m["records"], m["rows"]), (2, 2))
        self.assertEqual(m["gates"]["tests/test_a.py"], {"killed": 2, "survived": 0, "records": 2, "workOrders": ["T1"]})

    def test_a_folder_named_by_several_work_orders_is_counted_once(self):
        self.commit("T1: the change", {"src/a.py": "x\n"})
        self.commit("T2: the change", {"src/b.py": "x\n"})
        self.commit("T3: the change", {"src/c.py": "x\n"})
        self.commit("tests", {"tests/test_a.py": "t\n", "tests/test_b.py": "t\n"})
        self.record("2026-06-11-T1-T2-T3-review", {"findings.md": "F1 nothing\n",
                                                   "mutations.json": json.dumps([KILLED, SURVIVED])})
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        m = self.reg()["mutations"]
        self.assertEqual((m["records"], m["rows"]), (1, 2))
        self.assertEqual(m["gates"], {"tests/test_a.py": {"killed": 1, "survived": 1, "records": 1,
                                                          "workOrders": ["T1", "T2", "T3"]}})

    def test_a_nested_flag_in_a_step_folder_is_not_superseded_by_a_clean_close(self):
        flag, _ = self.review(None, close=True)
        self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "closed")
        self.assertEqual(flag["state"], "open")

    def rc(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("records_check_rounds", SCRIPT)
        rc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rc)
        return rc

    def test_the_round_names_are_declared_once(self):
        import fnmatch
        rc, rev = self.rc(), json.loads(CONTRACTS.read_text())["jobs"]["reviewer"]
        self.assertEqual(rev["rounds"]["first"],
                         {"findings": "findings.md", "mutations": "mutations.json", "log": "mutations.log.jsonl"})
        self.assertEqual(rev["rounds"]["fix"], {"findings": "findings-fix{n}.md", "mutations": "mutations-fix{n}.json",
                                                "log": "mutations-fix{n}.log.jsonl"})
        req = rev["require"][0]
        for n in range(4):
            name = rc.round_name(rev, n, "findings")
            self.assertTrue(fnmatch.fnmatchcase(name, rev["marks"][0]) and fnmatch.fnmatchcase(name, req["per"]))
            self.assertEqual(rc.owed_name(req, name), rc.round_name(rev, n, "mutations"))
            self.assertEqual(rc.declared(name, rev), n)
        for name in ("recheck.md", "fix1-recheck.md", "findings-fix0.md", "findings-fix.md", "FINDINGS.md",
                     "findings-recheck.md", "findings-fix1.md.bak"):
            self.assertIsNone(rc.declared(name, rev), name)
        claimed = rc.claimed(rc.merged_jobs({}, rc.load_contracts()), "reviewer")
        names = ["findings.md", "mutations.json", "contract.md", "pass1.md", "fix1-pass1.md", "fix2-recheck.md",
                 "recheck.md", "Review-notes.md", "prediction-review-fix.md", "result-review-fix.md", "findings.json"]
        self.assertEqual(rc.misnamed(names, rev, claimed),
                         [("Review-notes.md", "findings-fix1.md", "mutations-fix1.json"),
                          ("fix2-recheck.md", "findings-fix2.md", "mutations-fix2.json"),
                          ("recheck.md", "findings-fix3.md", "mutations-fix3.json")])

    def test_the_briefs_and_prompts_name_the_declared_rounds(self):
        import re
        root = SCRIPT.parent.parent
        rc, rev = self.rc(), json.loads(CONTRACTS.read_text())["jobs"]["reviewer"]
        globs = set(rev["marks"]) | {rev["require"][0]["glob"], rev["require"][0]["per"]}
        pairs = [(rc.round_name(rev, 0, "findings"), rc.round_name(rev, 0, "mutations")),
                 tuple(rev["rounds"]["fix"][k].replace("{n}", "<N>") for k in ("findings", "mutations"))]
        for path in ("commands/change.md", "commands/refactor.md", "agents/refactor-reviewer.md",
                     "runtime/agent-roles.py", "runtime/records-check.py", "runtime/agent-roles.json"):
            text = (root / path).read_text()
            for tok in re.findall(r"\b(?:findings|mutations)[\w.<>{}*-]*?\.(?:md|json)\b", text):
                as_findings = tok if tok.endswith(".md") else f"findings{tok[len('mutations'):-len('.json')]}.md"
                ok = tok in globs or tok in pairs[1] or rc.declared(as_findings, rev) is not None and (
                    tok == as_findings or tok == rc.owed_name(rev["require"][0], as_findings))
                self.assertTrue(ok, f"{path} names {tok}, which is not a round name record-contracts.json declares")
            if path.endswith(".md"):
                for name in (n for p in pairs for n in p):
                    self.assertIn(name, text, f"{path} does not state the declared name {name}")

    def test_a_findings_file_under_another_name_is_flagged_from_the_rounds_date(self):
        day = json.loads(CONTRACTS.read_text())["jobs"]["reviewer"]["rounds"]["from"]
        late = f"{day}T12:00:00"
        self.commit("history", {f"reviews/{STEP}/review/recheck.md": "G1\n", f"reviews/{STEP}/old-review-notes.md": "n\n"})
        self.commit("T1: the change", {"src/a.py": "x\n"}, date=late)
        self.commit("record", {f"reviews/{STEP}/{n}": t for n, t in TRIO.items()}, date=late)
        self.commit("new", {f"reviews/{STEP}/review/fix2-recheck.md": "H1\n", f"reviews/{STEP}/review/FINDINGS.md": "x\n",
                            f"reviews/{STEP}/prediction-review-fix.md": "p\n"}, date=late)
        code, out = self.run_check(as_of=day)
        self.assertEqual(code, 0, out)
        flag = self.flags()[("T1", "reviewer.rounds")]
        tail = "(record-contracts.json, rounds)"
        self.assertEqual(flag["what"], [
            (f"{STEP}/review: FINDINGS.md is not a declared findings name; write it as findings.md and its rows as "
             f"mutations.json {tail}"),
            (f"{STEP}/review: fix2-recheck.md is not a declared findings name; write it as findings-fix2.md and its "
             f"rows as mutations-fix2.json {tail}")])
        self.assertEqual((flag["owner"], flag["state"], flag["folders"]), ("reviewer", "open", [STEP + "/review"]))
        self.assertIn("git mv", flag["next"])
        self.assertNotIn(("T1", "reviewer.mutations"), self.flags())

    def history(self, day, extra=None):
        """T1's step record, then a review folder whose findings.md (no mutations file) is added on day."""
        self.commit("T1: the change", {"src/a.py": "x\n"})
        self.record(STEP, TRIO)
        self.commit("review", {f"reviews/{STEP}/review/findings.md": "F1 prose mutations\n", **(extra or {})},
                    date=f"{day}T12:00:00")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("reviewer", self.reg()["workOrders"]["T1"]["jobs"])
        return self.flags()

    def test_a_findings_file_added_before_the_rounds_date_owes_no_mutations_file(self):
        from datetime import date, timedelta
        day = (date.fromisoformat(FROM[:10]) - timedelta(days=1)).isoformat()
        self.assertNotIn(("T1", "reviewer.mutations"), self.history(day))

    def test_a_findings_file_added_on_the_rounds_date_owes_its_mutations_file(self):
        flag = self.history(FROM[:10])[("T1", "reviewer.mutations")]
        self.assertEqual(flag["what"], [STEP + "/review: no file matches mutations.json"])

    def test_the_mutations_rule_and_the_round_name_rule_share_one_date(self):
        from datetime import date, timedelta
        before = (date.fromisoformat(FROM[:10]) - timedelta(days=1)).isoformat()
        for day, want in ((before, set()), (FROM[:10], {"reviewer.mutations", "reviewer.rounds"})):
            with self.subTest(day=day):
                self.fresh()
                flags = self.history(day, {f"reviews/{STEP}/review/recheck.md": "G1\n"})
                self.assertEqual({r for w, r in flags if w == "T1" and r.startswith("reviewer.")}, want)

    def test_an_open_survivor_stays_open_at_a_clean_close_in_any_folder(self):
        for folder in ("2026-06-12-T1-review", "2026-06-12-T1-T2-review/review", STEP + "/review"):
            with self.subTest(folder=folder):
                self.fresh()
                flag, _ = self.review([KILLED, dict(SURVIVED, disposition="open: F1")], folder=folder, close=True)
                self.assertEqual(self.reg()["workOrders"]["T1"]["close"], "closed")
                self.assertEqual(flag["state"], "open")
        self.fresh()
        flag, _ = self.review([dict(SURVIVED, disposition=None)], folder="2026-06-12-T1-review", close=True)
        self.assertEqual(flag["state"], "superseded")

    def test_test_added_names_a_test_changed_after_the_review(self):
        rows = json.dumps([KILLED, dict(SURVIVED, disposition="test-added tests/test_c.py")])
        refused = 'names "tests/test_c.py", which no commit changed after its findings file was first committed'
        d = f"reviews/{STEP}/review"
        cases = {"the test before the review": ([{"tests/test_c.py": "def test_c():\n    pass\n"},
                                                 {f"{d}/findings.md": "F1\n", f"{d}/mutations.json": rows}], True),
                 "the test in the review's own commit": ([{"tests/test_c.py": "def test_c():\n    pass\n",
                                                           f"{d}/findings.md": "F1\n", f"{d}/mutations.json": rows}], False),
                 "the test after the review, the row settled later": (
                     [{f"{d}/findings.md": "F1\n", f"{d}/mutations.json": "[]"},
                      {"tests/test_c.py": "def test_c():\n    pass\n"}, {f"{d}/mutations.json": rows}], False),
                 "the test changed only after the record": (
                     [{"tests/test_c.py": "def test_c():\n    pass\n"}, {f"{d}/findings.md": "F1\n", f"{d}/mutations.json": rows},
                      {"tests/test_c.py": "def test_c():\n    assert True\n"}], True),
                 "only the row rewritten after the review": (
                     [{"tests/test_c.py": "def test_c():\n    pass\n"}, {f"{d}/findings.md": "F1\n", f"{d}/mutations.json": "[]"},
                      {f"{d}/mutations.json": rows}], True)}
        for name, (commits, bad) in cases.items():
            with self.subTest(case=name):
                self.fresh()
                self.commit("T1: the change", {"src/a.py": "x\n"})
                self.record(STEP, TRIO)
                self.commit("tests", {"tests/test_a.py": "t\n"})
                for i, files in enumerate(commits):
                    self.commit(f"step {i}", files)
                self.run_check()
                flag = self.flags().get(("T1", "reviewer.mutations"))
                if bad:
                    self.assertIn(refused, " | ".join(flag["what"]))
                else:
                    self.assertIsNone(flag)

    def test_path_name_names_a_def_or_class(self):
        extra = {"tests/test_c.py": "class TestC:\n    def test_one(self):\n        pass\n\n\ndef helper():\n    pass\n\n\n"
                                    "async def test_two():\n    pass\n"}
        held = "which tests/test_c.py does not hold"
        cases = [("tests/test_c.py::TestC", None), ("tests/test_c.py::TestC::test_one", None),
                 ("tests/test_c.py::test_two", None), ("tests/test_c.py::TestC::test_one[a-1]", None),
                 ("tests/test_c.py::TestC::test_missing", held), ("tests/test_c.py::Missing::test_one", held),
                 ("tests/test_c.py::helper", held), ("tests/test_c.py::pass", held), ("tests/test_c.py::def", held),
                 ("tests/test_c.py::test_one::TestC", held), ("tests/test_c.py::TestC::", held),
                 ("tests/test_c.py::Test", held), ("tests/test_c.py::test_tw", held)]
        for target, want in cases:
            with self.subTest(target=target):
                self.fresh()
                got = self.what([dict(SURVIVED, disposition=f"test-added {target}")], extra=extra)
                if want:
                    self.assertIn(want, got)
                else:
                    self.assertEqual(got, "")

    def test_two_review_folders_of_one_work_order_are_two_records(self):
        self.review([KILLED])
        self.record(STEP + "/fix1/review", {"findings.md": "F1\n", "mutations.json": json.dumps([KILLED])})
        self.run_check()
        m = self.reg()["mutations"]
        self.assertEqual((m["records"], m["rows"]), (2, 2))
        self.assertEqual(m["gates"]["tests/test_a.py"], {"killed": 2, "survived": 0, "records": 2, "workOrders": ["T1"]})

    def test_a_later_findings_file_does_not_redate_an_earlier_record(self):
        flag, _ = self.review([dict(KILLED, gate="tests/test_c.py")], extra={"tests/test_c.py": "t\n"})
        self.assertIsNone(flag)
        self.git("rm", "-q", "tests/test_c.py")
        self.git("commit", "-qm", "test_c removed")
        self.record(STEP + "/review", {"findings-fix1.md": "F1 again\n", "mutations-fix1.json": json.dumps([dict(KILLED, id="R1")])})
        self.run_check()
        self.assertNotIn(("T1", "reviewer.mutations"), self.flags())
        self.assertEqual(self.reg()["mutations"]["gates"]["tests/test_c.py"]["killed"], 1)


TEST_C = {"tests/test_c.py": "def test_c():\n    pass\n"}


class TestTestAddedOrder(Fixture):
    """T8 (T3 re-check F2-C): a test-added test changed on a line that descends from the findings commit."""

    D = f"reviews/{STEP}/review"
    REFUSED = 'names "tests/test_c.py", which no commit changed after its findings file was first committed'

    def rows(self, d):
        return json.dumps([KILLED, dict(SURVIVED, disposition=d)])

    def flag(self, steps):
        """steps: ("main"|"side", files, date), ("fork",) or ("merge", date); then the row is settled on main."""
        self.commit("T1: the change", {"src/a.py": "x\n", "tests/test_a.py": "t\n"})
        self.record(STEP, TRIO)
        main = self.git("rev-parse", "--abbrev-ref", "HEAD").strip()
        for s in steps:
            if s[0] == "fork":
                self.git("branch", "side")
            elif s[0] == "merge":
                self.git("checkout", "-q", main)
                self.git("merge", "-q", "--no-ff", "-m", "merge side", "side", date=s[1])
            else:
                self.git("checkout", "-q", main if s[0] == "main" else "side")
                self.commit("step", s[1], date=s[2])
        self.git("checkout", "-q", main)
        self.commit("settled", {f"{self.D}/mutations.json": self.rows("test-added tests/test_c.py")}, date="2026-06-13T12:00:00")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        f = self.flags().get(("T1", "reviewer.mutations"))
        return " | ".join(f["what"]) if f else ""

    def review(self):
        return {f"{self.D}/findings.md": "F1\n", f"{self.D}/mutations.json": self.rows("open: F1")}

    def test_a_test_from_a_side_branch_made_before_the_review_is_refused(self):
        got = self.flag([("fork",), ("side", TEST_C, "2026-06-10T13:00:00"), ("main", self.review(), "2026-06-11T12:00:00"),
                         ("merge", "2026-06-12T12:00:00")])
        self.assertIn(self.REFUSED, got)

    def test_a_branch_that_does_not_hold_the_findings_commit_is_refused_whatever_its_date(self):
        got = self.flag([("fork",), ("main", self.review(), "2026-06-11T12:00:00"), ("side", TEST_C, "2026-06-12T09:00:00"),
                         ("merge", "2026-06-12T12:00:00")])
        self.assertIn(self.REFUSED, got)

    def test_a_branch_forked_after_the_review_is_accepted(self):
        self.assertEqual(self.flag([("main", self.review(), "2026-06-11T12:00:00"), ("fork",),
                                    ("side", TEST_C, "2026-06-12T09:00:00"), ("merge", "2026-06-12T12:00:00")]), "")

    def test_the_line_decides_not_the_date(self):
        self.assertEqual(self.flag([("main", self.review(), "2026-06-11T12:00:00"),
                                    ("main", TEST_C, "2026-06-01T09:00:00")]), "")
        self.fresh()
        self.assertIn(self.REFUSED, self.flag([("main", TEST_C, "2026-06-20T09:00:00"),
                                               ("main", self.review(), "2026-06-11T12:00:00")]))

    def test_a_merge_that_only_brings_the_test_in_is_not_a_change_to_it(self):
        got = self.flag([("fork",), ("side", TEST_C, "2026-06-10T13:00:00"), ("main", self.review(), "2026-06-11T12:00:00"),
                         ("main", {"src/b.py": "y\n"}, "2026-06-11T13:00:00"), ("merge", "2026-06-12T12:00:00")])
        self.assertIn(self.REFUSED, got)

    def fresh(self):
        self.tearDown()
        self.setUp()


STREAM_ROW = dict(KILLED, gate="runtime/tests/test_x.py")


class TestC2CodeRoots(Fixture):
    """Review C2: a binding whose signal is a command or a pointer names its code roots in records.codeRoots."""

    def test_c2_a_command_signal_reads_records_code_roots(self):
        self.commit("T1: the change", {"src/a.py": "x\n", "tools/b.py": "y\n"})
        self.bind(signal="python3 tools/health.py --register {register}")
        code, out = self.run_check()
        self.assertEqual(code, 2, out)
        self.assertIn("no code roots: the binding's signal.codeRoots (a rules signal), records.codeRoots (a command "
                      "or pointer signal), or --code-roots", out)
        for sig in ("python3 tools/health.py --register {register}", "recipe.yaml#steps.signal"):
            with self.subTest(sig=sig):
                self.bind(signal=sig, records={"codeRoots": ["tools/"]})
                code, out = self.run_check()
                self.assertEqual(code, 0, out)
                self.assertEqual(self.reg()["computed"]["codeRoots"], ["tools/"])
                self.run_check("--code-roots", "src/")
                self.assertEqual(self.reg()["computed"]["codeRoots"], ["src/"])
        # a rules signal keeps its own code roots
        self.bind(records={"codeRoots": ["tools/"]})
        self.run_check()
        self.assertEqual(self.reg()["computed"]["codeRoots"], ["src/"])

    def test_c2_records_code_roots_is_a_list_of_repository_folders(self):
        for roots in ("src/", [], ["/abs"], ["../up"], [""], [1]):
            with self.subTest(roots=roots):
                self.bind(signal="python3 tools/health.py", records={"codeRoots": roots})
                code, out = self.run_check()
                self.assertEqual(code, 2, out)
                self.assertIn("'records.codeRoots'", out)

    def test_c2_the_docs_example_binding_names_its_code_roots(self):
        import re
        doc = (SCRIPT.parent.parent / "docs" / "PROJECT-TEAM.md").read_text()
        example = json.loads(re.search(r"### An example\n.*?```json\n(.*?)```", doc, re.DOTALL).group(1))
        self.assertIsInstance(example["signal"], str)
        self.assertEqual(example["records"]["codeRoots"], ["src/"])
        self.commit("T1: the change", {"src/a.py": "x\n"})
        self.binding.write_text(json.dumps(dict(example, reviews="reviews/")))
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertEqual(list(self.reg()["workOrders"]), ["T1"])
