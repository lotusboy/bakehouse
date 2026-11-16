"""Tests for the close of a SEALED work order (V12c step 2a; owner d20261103-2a691d, d20261104-3cba63):
close judges the committed sealed events in the ops ledger, not the order of git history. Each V12a
review repro (F2-F6, F12, F13) and report A repro (A1-A5) is done to a sealed record and must refuse;
the honest flows close. Standard library only. python3 -m unittest discover -s runtime/tests
"""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNTIME))
import ops_ledger

_spec = importlib.util.spec_from_file_location(
    "predict_gate", RUNTIME / "predict-gate.py"
)
pg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pg)

REC = "runs/2026-11-10-W-1-step1-review"
GOOD_P = {"moves": [{"what": "out:v", "from": 1, "to": 2}], "mustNotMove": ["out:w"]}
FIX_P = {
    "moves": [
        {"what": "out:v", "from": 1, "to": 2},
        {"what": "out:w", "from": 5, "to": 6},
    ]
}
WIDE_P = {"moves": [{"what": "out:v", "range": [2, 3]}], "mustNotMove": ["out:w"]}
BASE_M = {"out:v": 1, "out:w": 5}
PASS_M = {"out:v": 2, "out:w": 5}
FAIL_M = {"out:v": 2, "out:w": 6}


class Sealed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name).resolve()
        self.repo = t / "repo"
        self.rt = t / "runtime"
        self.rt.mkdir()
        for f in RUNTIME.iterdir():
            if f.is_file():
                (self.rt / f.name).symlink_to(f)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.binding = self.repo / ".team" / "binding.json"
        self.binding.parent.mkdir()
        self.binding.write_text(
            json.dumps(
                {
                    "owned": True,
                    "reviews": "runs/",
                    "reviewName": "{date}-{id}-step{n}-review",
                    "tracker": {"file": "T.md", "id": "^W-[0-9]+$"},
                    "signal": {"codeRoots": ["src/"]},
                }
            )
        )
        self.records = t / "repo.team-records"
        self.ledger = self.records / "ops/events.jsonl"
        for d in ("home", "machine", "tmp"):
            (t / d).mkdir()
        self.env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("OPS_", "PREDICT_GATE", "DECISION_", "BAKEHOUSE_", "CLAUDE_PROJECT_DIR"))
        }
        self.env.update(
            HOME=str(t / "home"),
            BAKEHOUSE_MACHINE_DIR=str(t / "machine"),
            TMPDIR=str(t / "tmp"),
            PYTHONDONTWRITEBYTECODE="1",
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@t",
        )
        for a in (  # the records worktree, where seals anchor; its branch has a first commit, as a branch does
            ["-C", str(self.repo), "worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(self.records)],
            ["-C", str(self.repo), "config", "bakehouse.records", str(self.records)],
            ["-C", str(self.records), "commit", "-q", "--allow-empty", "-m", "scratch records"],
        ):
            subprocess.run(
                ["git", *a],
                check=True,
                env=self.env,
                capture_output=True,
            )
        self.n = 0
        self.commit("base", {"src/app.py": "v = 1\n", "T.md": "t\n"})
        self.default = self.git("symbolic-ref", "--short", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *a):
        return subprocess.run(
            ["git", "-C", str(self.repo), *a],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        ).stdout.strip()

    def commit(self, msg, files=None, *trailers, rm=(), mv=(), when=None):
        self.n += 1
        when = when or f"2026-11-10T09:{self.n:02d}:00+00:00"
        for path, data in (files or {}).items():
            p = self.repo / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(data if isinstance(data, str) else json.dumps(data))
        for path in rm:
            self.git("rm", "-q", "-r", path)
        for a, b in mv:
            (self.repo / b).parent.mkdir(parents=True, exist_ok=True)
            self.git("mv", a, b)
        self.git("add", "-A")
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                msg,
                *[x for t in trailers for x in ("-m", t)],
            ],
            check=True,
            capture_output=True,
            env=dict(self.env, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when),
        )

    def head(self):
        return self.git("rev-parse", "HEAD")

    def r(self, n):
        return f"{REC}/{n}"

    def put(self, name, data):
        p = self.repo / self.r(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data if isinstance(data, str) else json.dumps(data))

    def gate(self, *args):
        return subprocess.run(
            ["python3", str(self.rt / "predict-gate.py"), *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )

    def seal(self, rnd, name):
        return self.gate("seal", "W-1", rnd, str(self.repo / self.r(name))).returncode

    def code(self, msg, files, rnd="first"):
        """The round's code, committed and sealed through predict-gate.sh seal-code (Step 2b)."""
        for path, data in files.items():
            f = self.repo / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(data)
        self.git("add", "-A")
        self.n += 1
        when = f"2026-11-10T09:{self.n:02d}:00+00:00"
        p = subprocess.run(
            [
                "python3",
                str(self.rt / "predict-gate.py"),
                "seal-code",
                "W-1",
                rnd,
                "--",
                "-q",
                "-m",
                msg,
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=False,
            env=dict(self.env, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when),
        )
        return p.returncode

    def measure(self, rnd, data, base, head):
        suffix = "" if rnd == "first" else f".{rnd}"
        self.put(f"moves.measured{suffix}.json", data)
        self.put(
            "measure-report.json" if rnd == "first" else f"measure-{rnd}-report.json",
            {"base": {"sha": base}, "head": {"sha": head}},
        )
        return self.seal(rnd, f"moves.measured{suffix}.json")

    def decide(
        self,
        did,
        task="W-1",
        approves="revise",
        choice="revise",
        options=("keep", "revise"),
    ):
        led = self.records / "decisions/ledger.jsonl"
        led.parent.mkdir(parents=True, exist_ok=True)
        with led.open("a") as fh:
            fh.write(
                json.dumps(
                    {
                        "kind": "ask",
                        "id": did,
                        "ts": "2026-11-01T00:00:00+00:00",
                        "stream": "repo",
                        "category": "process",
                        "question": "q",
                        "options": list(options),
                        "recommendation": options[0],
                        "confidence": 0.9,
                        "blind": False,
                        "autonomous": False,
                        "task": task,
                        **({"approves": approves} if approves is not None else {}),
                    }
                )
                + "\n"
            )
            fh.write(
                json.dumps(
                    {
                        "kind": "answer",
                        "id": did,
                        "ts": "2026-11-01T01:00:00+00:00",
                        "choice": choice,
                        "agreed": True,
                    }
                )
                + "\n"
            )

    def flow(self, pred=None, measured=None, branch=None):
        self.commit(
            "W-1: prediction",
            {
                self.r("moves.prediction.json"): pred or GOOD_P,
                self.r("moves.baseline.json"): BASE_M,
            },
        )
        self.assertEqual(self.seal("first", "moves.prediction.json"), 0)
        self.assertEqual(self.seal("first", "moves.baseline.json"), 0)
        if branch:
            self.git("checkout", "-q", "-b", branch)
        b = self.head()
        self.code("W-1: the change", {"src/app.py": "v = 2\n"})
        self.measure("first", measured or PASS_M, b, self.head())
        self.commit("W-1: measurement")
        return b

    def close(self, *want):
        p = self.gate("close", "W-1")
        out = p.stdout + p.stderr
        for w in want:
            self.assertIn(w, out)
        return p.returncode, out

    def assertCloses(self):
        rc, out = self.close("sealed: judged by")
        self.assertEqual(rc, 0, out)

    def assertRefuses(self, *why):
        rc, out = self.close(*why)
        self.assertEqual(rc, 1, out)

    # ---- the honest flows close ----

    def test_a_sealed_round_closes(self):
        self.flow()
        self.assertCloses()

    def test_a_merged_lane_is_compared_with_the_merges_lane_parent(self):
        self.flow(branch="lane")
        self.git("checkout", "-q", self.default)
        self.commit("W-2: another work order's code", {"src/other.py": "o = 1\n"})
        self.git("merge", "-q", "--no-ff", "lane", "-m", "Merge lane")
        self.assertCloses()

    def test_an_amended_away_head_is_compared_by_its_code_tree(self):
        self.commit(
            "W-1: prediction",
            {
                self.r("moves.prediction.json"): GOOD_P,
                self.r("moves.baseline.json"): BASE_M,
            },
        )
        self.seal("first", "moves.prediction.json")
        self.seal("first", "moves.baseline.json")
        b = self.head()
        self.code("W-1: the change", {"src/app.py": "v = 2\n"})
        self.measure("first", PASS_M, b, self.head())
        self.git("commit", "-q", "--amend", "-m", "W-1: the change, reworded")
        self.commit("W-1: measurement")
        self.assertCloses()

    def test_a_revised_prediction_closes(self):
        self.commit(
            "W-1: prediction",
            {
                self.r("moves.prediction.json"): GOOD_P,
                self.r("moves.baseline.json"): BASE_M,
            },
        )
        self.seal("first", "moves.prediction.json")
        self.seal("first", "moves.baseline.json")
        self.put("moves.prediction.json", WIDE_P)
        self.decide("d20261101-bbbbb1")
        self.assertEqual(
            self.gate(
                "revise",
                "W-1",
                "first",
                str(self.repo / self.r("moves.prediction.json")),
                "--decision",
                "d20261101-bbbbb1",
            ).returncode,
            0,
        )
        self.commit("W-1: prediction widened")
        b = self.head()
        self.code("W-1: the change", {"src/app.py": "v = 2\n"})
        self.measure("first", PASS_M, b, self.head())
        self.commit("W-1: measurement")
        self.assertCloses()

    def round_one_refused_then_fix1(self, retire):
        self.flow(measured=FAIL_M)
        h0 = self.head()
        self.commit(
            "W-1: round 1 to history/",
            mv=[
                (
                    self.r("moves.prediction.json"),
                    self.r("history/moves.prediction.json"),
                ),
                (self.r("moves.measured.json"), self.r("history/moves.measured.json")),
            ],
        )
        if retire:
            self.decide(
                "d20261101-bbbbb2",
                approves="retire",
                choice="retire",
                options=("keep", "retire"),
            )
            for n in ("history/moves.prediction.json", "history/moves.measured.json"):
                self.assertEqual(
                    self.gate(
                        "retire",
                        "W-1",
                        "first",
                        str(self.repo / self.r(n)),
                        "--decision",
                        "d20261101-bbbbb2",
                    ).returncode,
                    0,
                )
        self.commit(
            "W-1: fix1 prediction", {self.r("moves.prediction.fix1.json"): FIX_P}
        )
        self.seal("fix1", "moves.prediction.fix1.json")
        self.code("W-1: fix1 change", {"src/app.py": "v = 3\n"}, "fix1")
        self.measure("fix1", FAIL_M, h0, self.head())
        self.commit("W-1: fix1 measurement")

    def test_a_round_retired_by_an_approved_retire_needs_no_git_trailer(self):
        self.round_one_refused_then_fix1(retire=True)
        self.assertCloses()

    # ---- R0.1 S1: one place decides "is this seal retired?" ----

    def retire_measured(self, measured=None):
        """flow(), then the measured file moved to history/ and retired with the owner's approval.
        Returns (the measured base, the measured head)."""
        b = self.flow(measured=measured)
        h = self.git("rev-parse", "HEAD~1")
        self.commit(
            "W-1: measurement to history/",
            mv=[(self.r("moves.measured.json"), self.r("history/moves.measured.json"))],
        )
        self.decide(
            "d20261101-bbbbb2",
            approves="retire",
            choice="retire",
            options=("keep", "retire"),
        )
        p = self.gate(
            "retire",
            "W-1",
            "first",
            str(self.repo / self.r("history/moves.measured.json")),
            "--decision",
            "d20261101-bbbbb2",
        )
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return b, h

    def test_s1_a1_a_re_measure_with_the_retired_bytes_is_a_live_seal(self):
        # ANDON A1: the retire applies to the seal it retired; the same bytes sealed after it are live,
        # so the latest measurement still binds the code at HEAD
        b, h = self.retire_measured()
        self.assertEqual(self.measure("first", PASS_M, b, h), 0)
        self.commit("W-1: re-measured, the same bytes")
        self.commit("W-1: code after the measurement", {"src/app.py": "v = 3\n"})
        self.assertRefuses("code came after the latest measurement")

    def test_s1_a2_a_re_measure_after_a_retire_closes(self):
        # A2: a seal of the path after the retire of its latest bytes starts a new chain
        b, h = self.retire_measured(measured=FAIL_M)
        self.assertEqual(self.measure("first", PASS_M, b, h), 0)
        self.commit("W-1: re-measured")
        self.assertCloses()

    def test_s1_fix1_rule_5b_judges_the_live_chains_prediction_seal(self):
        # the first prediction is sealed before the lane code and then retired; the live prediction is sealed
        # after the code seal, at a commit that holds no code (so rule 5's tree test passes): 5b must refuse
        self.commit(
            "W-1: prediction",
            {
                self.r("moves.prediction.json"): GOOD_P,
                self.r("moves.baseline.json"): BASE_M,
            },
        )
        self.assertEqual(self.seal("first", "moves.prediction.json"), 0)
        self.assertEqual(self.seal("first", "moves.baseline.json"), 0)
        b = self.head()
        self.git("checkout", "-q", "-b", "lane")
        self.assertEqual(self.code("W-1: the change", {"src/app.py": "v = 2\n"}), 0)
        h = self.head()
        self.assertEqual(self.measure("first", PASS_M, b, h), 0)
        self.commit("W-1: measurement")
        self.decide(
            "d20261101-bbbbb2",
            approves="retire",
            choice="retire",
            options=("keep", "retire"),
        )

        def retire(n):
            p = self.gate("retire", "W-1", "first", str(self.repo / self.r(n)), "--decision", "d20261101-bbbbb2")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

        self.commit(
            "W-1: measurement to history/",
            mv=[(self.r("moves.measured.json"), self.r("history/moves.measured.json"))],
        )
        retire("history/moves.measured.json")
        self.git("checkout", "-q", self.default)
        self.commit(
            "W-1: prediction to history/",
            mv=[(self.r("moves.prediction.json"), self.r("history/moves.prediction.json"))],
        )
        retire("history/moves.prediction.json")
        self.commit("W-1: the live prediction", {self.r("moves.prediction.json"): WIDE_P})
        self.assertEqual(self.seal("first", "moves.prediction.json"), 0)
        self.git("merge", "-q", "--no-ff", "lane", "-m", "Merge lane")
        self.assertEqual(self.measure("first", PASS_M, b, h), 0)
        self.commit("W-1: re-measured")
        self.assertRefuses("its prediction was sealed after its first code commit")

    # ---- every repro done to a sealed record refuses ----

    def test_a1_a_round_moved_to_history_without_a_retire(self):
        self.round_one_refused_then_fix1(retire=False)
        self.assertRefuses(
            "moves.measured.json is sealed and is not at HEAD, and no retire of it is sealed",
            "its sealed check REFUSED",
        )

    def test_a1_a_sealed_file_deleted(self):
        self.flow()
        self.commit("W-1: drop it", rm=[self.r("moves.measured.json")])
        self.assertRefuses("moves.measured.json is sealed and is not at HEAD")

    def test_f2_the_record_re_recorded_under_another_date(self):
        self.flow()
        other = "runs/2026-11-11-W-1-step1-review"
        self.commit(
            "W-1: re-record",
            {
                f"{other}/moves.prediction.json": GOOD_P,
                f"{other}/moves.baseline.json": BASE_M,
                f"{other}/moves.measured.json": PASS_M,
            },
            rm=[REC],
        )
        self.assertRefuses(
            f"{other}/moves.prediction.json is at HEAD and has no committed seal"
        )

    def test_f3_a_stub_prediction_rewritten_after_the_code(self):
        self.commit(
            "W-1: stub",
            {
                self.r("moves.prediction.json"): {"moves": [{"what": "out:x"}]},
                self.r("moves.baseline.json"): BASE_M,
            },
        )
        self.seal("first", "moves.prediction.json")
        self.seal("first", "moves.baseline.json")
        b = self.head()
        self.commit("W-1: the change", {"src/app.py": "v = 2\n"})
        self.commit(
            "W-1: the prediction, written now",
            {self.r("moves.prediction.json"): GOOD_P},
        )
        self.measure("first", PASS_M, b, self.head())
        self.commit("W-1: measurement")
        self.assertRefuses(
            "moves.prediction.json at HEAD hashes",
            "a sealed file changes only through predict-gate.sh revise",
        )

    def test_f4_an_amend_after_a_refusal(self):
        self.flow(measured=FAIL_M)
        self.put("moves.measured.json", PASS_M)
        self.git("commit", "-q", "-a", "--amend", "--no-edit")
        self.assertRefuses(
            "moves.measured.json at HEAD hashes", "its sealed check REFUSED"
        )

    def test_f5_a_hand_edited_measurement(self):
        self.flow()
        self.commit(
            "W-1: tidy", {self.r("moves.measured.json"): json.dumps(PASS_M, indent=2)}
        )
        self.assertRefuses("moves.measured.json at HEAD hashes")

    def test_f6_f12_code_after_the_measurement_by_any_subject_or_date(self):
        self.flow()
        self.commit(
            "tidy", {"src/extra.py": "x = 1\n"}, when="2026-11-09T09:00:00+00:00"
        )
        self.assertRefuses("the code at HEAD differs from the code it measured")

    def test_f13_no_constant_date_exempts_a_sealed_work_order(self):
        self.flow()
        self.commit(
            "W-1: code after the measurement",
            {"src/app.py": "v = 3\n"},
            when="2026-11-01T09:00:00+00:00",
        )
        self.assertRefuses("the code at HEAD differs from the code it measured")

    def forged_revise(self, did, task, choice):
        self.commit(
            "W-1: prediction",
            {
                self.r("moves.prediction.json"): GOOD_P,
                self.r("moves.baseline.json"): BASE_M,
            },
        )
        self.seal("first", "moves.prediction.json")
        self.seal("first", "moves.baseline.json")
        self.put("moves.prediction.json", WIDE_P)
        self.decide(did, task, choice=choice)
        digest = hashlib.sha256(
            (self.repo / self.r("moves.prediction.json")).read_bytes()
        ).hexdigest()
        ops_ledger.append(
            self.ledger,
            lambda evs: {
                "ts": "2026-11-10T09:30:00+00:00",
                "kind": "revised",
                "task": "W-1",
                "round": "first",
                "fileKind": "prediction",
                "path": self.r("moves.prediction.json"),
                "sha256": digest,
                "decision": did,
            },
            start=True,
            then=lambda line: ops_ledger.anchor(self.ledger, line, "forged"),
        )
        self.commit("W-1: prediction widened")
        b = self.head()
        self.commit("W-1: the change", {"src/app.py": "v = 2\n"})
        self.measure("first", PASS_M, b, self.head())
        self.commit("W-1: measurement")

    def test_a2_a_revise_whose_decision_was_answered_against(self):
        self.forged_revise("d20261101-ccccc1", "W-1", "keep")
        self.assertRefuses(
            "without the owner's approval", "chose 'keep', not the approving option"
        )

    def test_a5_a_revise_citing_another_work_orders_decision(self):
        self.forged_revise("d20261101-ccccc2", "W-9", "revise")
        self.assertRefuses("was asked for W-9, not W-1")

    def test_a3_a_squash_merge_with_a_trailer_approves_nothing(self):
        self.flow(branch="work")
        self.decide("d20261101-bbbbb3")
        self.commit(
            "W-1: prediction widened",
            {self.r("moves.prediction.json"): WIDE_P},
            "Prediction-approved: d20261101-bbbbb3",
        )
        self.git("checkout", "-q", self.default)
        self.git("merge", "-q", "--squash", "work")
        self.commit("W-1: squash", None, "Prediction-approved: d20261101-bbbbb3")
        self.assertRefuses("moves.prediction.json at HEAD hashes")

    def test_a4_a_prediction_committed_with_its_code(self):
        b = self.head()
        self.commit(
            "W-1: prediction and change",
            {
                self.r("moves.prediction.json"): GOOD_P,
                self.r("moves.baseline.json"): BASE_M,
                "src/app.py": "v = 2\n",
            },
        )
        self.seal("first", "moves.prediction.json")
        self.seal("first", "moves.baseline.json")
        self.measure("first", PASS_M, b, self.head())
        self.commit("W-1: measurement")
        self.assertRefuses("which already holds code this round measured (src/app.py)")

    # ---- the ledger close is fed ----

    def test_a_seal_not_in_a_commit_does_not_count(self):
        self.flow()
        subprocess.run(
            ["git", "-C", str(self.records), "reset", "-q", "--soft", "HEAD~1"],
            env=self.env,
            check=True,
        )
        self.assertRefuses("1 sealed event(s) of W-1 are not in a commit of the ledger")

    def test_g3_a_broken_line_close_did_not_cause_refuses_and_names_the_repair(self):
        self.flow()
        with self.ledger.open("ab") as fh:
            fh.write(b'{"kind": "no\n')
        self.assertRefuses(
            "is BROKEN, and close did not write it: BROKEN at line",
            "The owner decides its repair, "
            "through a decision (decision.sh ask --task W-1",
        )

    def test_l7_a_rewrite_after_the_commit_refuses(self):
        self.flow()
        lines = [x for x in self.ledger.read_text().split("\n") if x.strip()]
        rows, out = [json.loads(x) for x in lines], []
        for e in rows:
            if e.get("fileKind") == "prediction":
                e["ts"] = "2026-11-01T00:00:00+00:00"
            if "prev" in e and out:
                e["prev"] = hashlib.sha256(out[-1].encode()).hexdigest()
            out.append(json.dumps(e))
        self.ledger.write_text("".join(x + "\n" for x in out))
        self.assertEqual(
            ops_ledger.verify(self.ledger)[0],
            True,
            "verify-chain alone still passes it (L7)",
        )
        self.assertRefuses("differs from its last commit in lines that commit holds")

    def test_a_head_this_repository_does_not_hold_and_no_trees_refuses_with_the_fetch(
        self,
    ):
        self.flow()
        why = pg.head_refusal(self.repo, "HEAD", "e" * 40, None, ["src/"])
        self.assertIn("fetch the lane branch that holds it", why)

    def test_an_unsealed_work_order_keeps_todays_rules(self):
        self.commit(
            "W-1: prediction",
            {
                self.r("moves.prediction.json"): GOOD_P,
                self.r("moves.baseline.json"): BASE_M,
            },
        )
        self.commit("W-1: the change", {"src/app.py": "v = 2\n"})
        self.commit("W-1: measurement", {self.r("moves.measured.json"): PASS_M})
        self.commit("W-1: more code", {"src/app.py": "v = 3\n"})
        rc, out = self.close()
        self.assertEqual(rc, 1)
        self.assertNotIn("sealed: judged by", out)
        self.assertIn("comes after the latest measured file's commit", out)

    def test_records_checks_stale_rule_for_a_sealed_work_order_compares_code(self):
        self.flow()
        evs = [json.loads(x) for x in self.ledger.read_text().split("\n") if x.strip()]
        self.assertEqual(
            pg.sealed_stale(self.repo, "HEAD", evs, ["src/"]), (True, None)
        )
        self.commit("W-2: later code", {"src/app.py": "v = 9\n"})
        sealed, why = pg.sealed_stale(self.repo, "HEAD", evs, ["src/"])
        self.assertTrue(sealed)
        self.assertIn("the code at HEAD differs from the code it measured", why)
        self.assertEqual(
            pg.sealed_stale(
                self.repo, "HEAD", [{"kind": "note", "task": "W-1"}], ["src/"]
            ),
            (False, None),
        )

    # ---- forged or edited ledger lines that verify-chain passes, judged again at close ----

    def forge(self, ev):
        ops_ledger.append(
            self.ledger,
            lambda evs: ev,
            start=True,
            then=lambda line: ops_ledger.anchor(self.ledger, line, "forged"),
        )

    def sha_of(self, name):
        return hashlib.sha256((self.repo / self.r(name)).read_bytes()).hexdigest()

    def forged_flow(self, *events):
        """prediction and baseline committed, the baseline sealed, the forged events, code, measurement."""
        self.commit(
            "W-1: prediction",
            {
                self.r("moves.prediction.json"): GOOD_P,
                self.r("moves.baseline.json"): BASE_M,
            },
        )
        self.seal("first", "moves.baseline.json")
        for ev in events:
            self.forge(
                dict(
                    {
                        "ts": "2026-11-10T09:30:00+00:00",
                        "task": "W-1",
                        "round": "first",
                        "fileKind": "prediction",
                        "path": self.r("moves.prediction.json"),
                    },
                    **ev,
                )
            )
        b = self.head()
        self.commit("W-1: the change", {"src/app.py": "v = 2\n"})
        self.measure("first", PASS_M, b, self.head())
        self.commit("W-1: measurement")

    def test_a_path_whose_first_event_is_a_revise(self):
        self.decide("d20261101-ddddd1")
        self.forged_flow(
            {
                "kind": "revised",
                "sha256": hashlib.sha256(json.dumps(GOOD_P).encode()).hexdigest(),
                "decision": "d20261101-ddddd1",
            }
        )
        self.assertRefuses(
            "moves.prediction.json: its first sealed event is a revised event, not a seal"
        )

    def test_a_second_seal_with_other_bytes_and_no_revise(self):
        stub = hashlib.sha256(b"{}").hexdigest()
        self.forged_flow(
            {"kind": "sealed", "sha256": stub},
            {
                "kind": "sealed",
                "sha256": hashlib.sha256(json.dumps(GOOD_P).encode()).hexdigest(),
            },
        )
        self.assertRefuses(
            "sealed again with other bytes", "and no revise: the first seal stands"
        )

    def test_a_revise_citing_a_decision_asked_without_approves(self):
        self.decide("d20261101-ddddd2", approves=None)
        stub = hashlib.sha256(b"{}").hexdigest()
        self.forged_flow(
            {"kind": "sealed", "sha256": stub},
            {
                "kind": "revised",
                "sha256": hashlib.sha256(json.dumps(GOOD_P).encode()).hexdigest(),
                "decision": "d20261101-ddddd2",
            },
        )
        self.assertRefuses("was asked without --approves")

    def test_a_retire_whose_decision_was_answered_against(self):
        self.flow(measured=FAIL_M)
        h0 = self.head()
        self.commit(
            "W-1: round 1 to history/",
            mv=[
                (
                    self.r("moves.prediction.json"),
                    self.r("history/moves.prediction.json"),
                ),
                (self.r("moves.measured.json"), self.r("history/moves.measured.json")),
            ],
        )
        self.decide(
            "d20261101-ddddd3",
            approves="retire",
            choice="keep",
            options=("keep", "retire"),
        )
        for n, kind in (
            ("moves.prediction.json", "prediction"),
            ("moves.measured.json", "measured"),
        ):
            self.forge(
                {
                    "ts": "2026-11-10T09:40:00+00:00",
                    "kind": "retired",
                    "task": "W-1",
                    "round": "first",
                    "fileKind": kind,
                    "path": self.r(f"history/{n}"),
                    "sha256": self.sha_of(f"history/{n}"),
                    "decision": "d20261101-ddddd3",
                }
            )
        self.commit(
            "W-1: fix1 prediction", {self.r("moves.prediction.fix1.json"): FIX_P}
        )
        self.seal("fix1", "moves.prediction.fix1.json")
        self.commit("W-1: fix1 change", {"src/app.py": "v = 3\n"})
        self.measure("fix1", FAIL_M, h0, self.head())
        self.commit("W-1: fix1 measurement")
        self.assertRefuses(
            "history/moves.measured.json: retired without the owner's approval",
            "chose 'keep', not the approving option",
        )

    def test_a_retired_copy_that_is_gone_from_head(self):
        self.round_one_refused_then_fix1(retire=True)
        self.commit("W-1: drop the history", rm=[self.r("history/moves.measured.json")])
        self.assertRefuses(
            "history/moves.measured.json, the retired copy, is not at HEAD with its sealed bytes"
        )

    def test_an_unmerged_head_with_other_code_refuses(self):
        self.flow()
        self.git("checkout", "-q", "-b", "side")
        self.commit("W-1: code on a branch nobody merged", {"src/app.py": "v = 7\n"})
        side = self.head()
        self.git("checkout", "-q", self.default)
        why = pg.head_refusal(self.repo, "HEAD", side, None, ["src/"])
        self.assertIn(
            f"the sealed head {side[:8]} is not merged into HEAD, or was amended", why
        )

    def test_records_check_close_state_judges_a_sealed_work_order_by_its_head_rule(
        self,
    ):
        spec = importlib.util.spec_from_file_location(
            "records_check", RUNTIME / "records-check.py"
        )
        rc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rc)
        events = [
            {
                "ts": "2026-11-10T10:00:00+00:00",
                "kind": rc.REFUSED_KIND,
                "purpose": rc.CLOSE_REFUSED,
                "task": "W-1",
            },
            {
                "ts": "2026-11-10T10:00:01+00:00",
                "kind": rc.CLOSED_KIND,
                "task": "W-1",
                "note": "OVERRIDDEN, see the gate-refused line; no pair passed",
            },
        ]
        self.assertEqual(
            rc.close_state(
                events, None, [], set(), None, None, (True, "the code differs")
            ),
            ("stale", "the code differs"),
        )
        self.assertEqual(
            rc.close_state(events, None, [], set(), None, None, (True, None)),
            ("overridden", None),
        )

    # ---- Step 2a fix 1: the review's H1, H3, H4 and K3 (findings-fix3.md) ----

    def strip_w1(self, commit):
        keep = [
            x
            for x in self.ledger.read_text().split("\n")
            if x.strip() and '"task": "W-1"' not in x
        ]
        self.ledger.write_text("".join(x + "\n" for x in keep))
        if commit:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.records),
                    "commit",
                    "-q",
                    "-m",
                    "drop W-1",
                    "--",
                    str(self.ledger.relative_to(self.records)),
                ],
                env=self.env,
                check=True,
                capture_output=True,
            )

    def test_h1_seals_stripped_from_the_working_ledger_still_seal_it(self):
        self.flow()
        self.commit("tidy", {"src/extra.py": "x = 1\n"})
        self.strip_w1(commit=False)
        self.assertRefuses(
            "sealed: judged by",
            "differs from its last commit in lines that commit holds",
            "the code at HEAD differs from the code it measured",
        )

    def test_h1_seals_stripped_and_committed_refuse_as_removed(self):
        self.flow()
        self.commit("tidy", {"src/extra.py": "x = 1\n"})
        self.strip_w1(commit=True)
        self.assertRefuses(
            "sealed: judged by", "a sealed event of W-1 was removed after its commit"
        )

    def test_h1_the_ledger_is_checked_on_every_close_sealed_or_not(self):
        self.commit(
            "W-1: prediction",
            {
                self.r("moves.prediction.json"): GOOD_P,
                self.r("moves.baseline.json"): BASE_M,
            },
        )
        self.commit("W-1: the change", {"src/app.py": "v = 2\n"})
        self.commit("W-1: measurement", {self.r("moves.measured.json"): PASS_M})
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.write_bytes(b'{"kind": "no\n')
        rc, out = self.close("is BROKEN, and close did not write it")
        self.assertEqual(rc, 1, out)
        self.assertNotIn("sealed: judged by", out)

    def test_h4_records_check_and_close_read_one_sealed_state(self):
        self.flow()
        self.strip_w1(commit=False)
        sealed, kept, refused = pg.sealed_state(self.ledger, "W-1")
        self.assertTrue(sealed, "a deletion from the ledger file never unseals")
        self.assertEqual(
            {e["fileKind"] for e in kept},
            {"prediction", "baseline", "code", "measured"},
        )
        self.assertEqual(refused, [])
        self.strip_w1(commit=True)
        sealed, kept, refused = pg.sealed_state(self.ledger, "W-1")
        self.assertEqual((sealed, kept), (True, []))
        self.assertEqual(len(refused), 4)
        self.assertEqual(pg.sealed_state(self.ledger, "W-2"), (False, [], []))

    def test_h3_the_same_work_orders_code_on_the_run_branch_after_its_lane_merged(self):
        self.flow(branch="lane")
        self.git("checkout", "-q", self.default)
        self.git("merge", "-q", "--no-ff", "lane", "-m", "Merge lane")
        self.commit("W-1: follow-up fix, not measured", {"src/app.py": "v = 5\n"})
        self.assertRefuses(
            "names W-1, touches the code roots and came on HEAD's own line after the merge"
        )

    def test_h3_another_work_orders_code_after_the_merge_does_not_refuse(self):
        self.flow(branch="lane")
        self.git("checkout", "-q", self.default)
        self.git("merge", "-q", "--no-ff", "lane", "-m", "Merge lane")
        self.commit("W-2: its own code", {"src/other.py": "o = 2\n"})
        self.assertCloses()

    def test_k3_a_lane_changed_after_its_measurement_then_merged(self):
        self.flow(branch="lane")
        self.commit("W-1: one more lane change", {"src/app.py": "v = 4\n"})
        self.git("checkout", "-q", self.default)
        self.git("merge", "-q", "--no-ff", "lane", "-m", "Merge lane")
        self.assertRefuses(
            "that merge's lane parent holds other code than the latest measurement read"
        )

    def test_h4_records_check_judges_a_sealed_work_order_by_the_sealed_state(self):
        self.flow()
        rc, out = self.close()
        self.assertEqual(rc, 0, out)
        self.commit(
            "W-1: more code after the close",
            {"src/app.py": "v = 9\n"},
            when="2026-11-01T09:00:00+00:00",
        )
        # back-dated: the old time rule calls this work order closed; only the head rule sees the new code
        reg = Path(self.tmp.name) / "register.json"
        p = subprocess.run(
            [
                "python3",
                str(self.rt / "records-check.py"),
                "--binding",
                str(self.binding),
                "--repo",
                str(self.repo),
                "--as-of",
                "2026-11-11",
                "--register",
                str(reg),
                "--ledger",
                str(self.ledger),
            ],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )
        state = (
            json.loads(reg.read_text())["workOrders"]["W-1"]["close"]
            if reg.is_file()
            else p.stdout + p.stderr
        )
        self.assertEqual(
            state,
            "stale",
            "records-check reads the sealed state close reads, and judges W-1 by its head",
        )

    # ---- Step 2a fix 2: the review's J1, J2 and J3 (findings-fix4.md) ----

    def pgit2(self, *a):
        return subprocess.run(
            ["git", "-C", str(self.records), *a],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_j1_a_ledger_committed_by_hand_after_failed_anchors_still_seals(self):
        hook = self.repo / ".git/hooks/pre-commit"  # the records worktree shares the clone's hooks
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(f'#!/bin/sh\n[ "$(git rev-parse --show-toplevel)" = "{self.records}" ] && exit 1\nexit 0\n')
        hook.chmod(0o755)
        self.commit(
            "W-1: prediction",
            {
                self.r("moves.prediction.json"): GOOD_P,
                self.r("moves.baseline.json"): BASE_M,
            },
        )
        self.assertEqual(
            [
                self.seal("first", "moves.prediction.json"),
                self.seal("first", "moves.baseline.json"),
            ],
            [1, 1],
        )
        b = self.head()
        self.commit("W-1: the change", {"src/app.py": "v = 2\n"})
        self.assertEqual(self.measure("first", PASS_M, b, self.head()), 1)
        self.commit("W-1: measurement")
        self.assertRefuses(
            "are not in a commit of the ledger",
            "seal again, so the anchor commits a new line",
        )
        hook.unlink()
        self.pgit2(
            "commit",
            "-q",
            "-m",
            "by hand",
            "--",
            str(self.ledger.relative_to(self.records)),
        )
        self.commit("tidy", {"src/extra.py": "x = 1\n"})
        self.assertEqual(
            ops_ledger.sealed_trailers(self.ledger), [], "no anchor ever committed"
        )
        self.assertRefuses(
            "sealed: judged by", "the code at HEAD differs from the code it measured"
        )

    def test_j2_seals_merged_with_ours_refuse_as_removed(self):
        # the seals end up on a side branch the records branch has not merged; the records worktree itself stays
        # on its branch, which the locator requires (S16 M5)
        base = self.pgit2("rev-parse", "HEAD").stdout.strip()
        self.flow()
        self.pgit2("branch", "side")
        self.pgit2("reset", "-q", "--hard", base)
        _, out = self.close()
        self.assertNotIn(
            "sealed: judged by",
            out,
            "limit L8: seals on an unmerged branch are not seen",
        )
        self.pgit2("merge", "-q", "-s", "ours", "side", "-m", "merge side, ours")
        self.assertRefuses(
            "sealed: judged by", "a sealed event of W-1 was removed after its commit"
        )

    def test_j2_the_trailer_scan_follows_a_moved_ledger(self):
        self.flow()
        before = ops_ledger.sealed_trailers(self.ledger)
        self.assertEqual(len(before), 4)
        self.pgit2("mv", "ops", "ops2")
        self.pgit2("commit", "-q", "-m", "move the ledger")
        moved = self.records / "ops2/events.jsonl"
        after = ops_ledger.sealed_trailers(moved)
        self.assertEqual([d for _, d, _ in after], [d for _, d, _ in before])
        self.assertEqual({p for _, _, p in after}, {"ops/events.jsonl"})
        keep = [
            x
            for x in moved.read_text().split("\n")
            if x.strip() and '"fileKind": "measured"' not in x
        ]
        moved.write_text("".join(x + "\n" for x in keep))
        self.pgit2(
            "commit",
            "-q",
            "-m",
            "drop the measured seal",
            "--",
            "ops2/events.jsonl",
        )
        sealed, _, refused = pg.sealed_state(moved, "W-1")
        self.assertTrue(sealed)
        self.assertEqual(
            len(refused),
            1,
            "the removed line is found in its commit's own path, before the move",
        )
        self.assertIn("removed after its commit", refused[0])

    def test_j3_a_record_only_commit_after_the_merge_still_closes(self):
        self.flow(branch="lane")
        self.git("checkout", "-q", self.default)
        self.git("merge", "-q", "--no-ff", "lane", "-m", "Merge lane")
        self.commit("W-1: review record", {self.r("findings.md"): "a review\n"})
        self.assertCloses()

    def test_j1_a_deleted_ledger_file_never_unseals(self):
        self.flow()
        self.commit("tidy", {"src/extra.py": "x = 1\n"})
        self.ledger.unlink()
        self.assertRefuses(
            "sealed: judged by",
            "differs from its last commit in lines that commit holds",
        )

    def test_j1_a_committed_deletion_of_the_ledger_file_refuses_as_removed(self):
        self.flow()
        rel = str(self.ledger.relative_to(self.records))
        self.pgit2("rm", "-q", "--", rel)
        self.pgit2("commit", "-q", "-m", "drop the ledger")
        self.assertFalse(self.ledger.exists())
        sealed, kept, refused = pg.sealed_state(self.ledger, "W-1")
        self.assertEqual((sealed, kept, len(refused)), (True, [], 4))
        self.assertTrue(all("removed after its commit" in r for r in refused))

    def test_j1_a_deleted_ledger_folder_never_unseals(self):
        self.flow()
        self.commit("tidy", {"src/extra.py": "x = 1\n"})
        shutil.rmtree(
            self.ledger.parent
        )  # the ledger, its lock file and the ops folder, all gone
        sealed, kept, _ = pg.sealed_state(self.ledger, "W-1")
        self.assertTrue(
            sealed, "git is found from the nearest folder that still exists"
        )
        self.assertEqual(len(kept), 4)
        self.assertRefuses(
            "sealed: judged by",
            "differs from its last commit in lines that commit holds",
        )

    # ---- Step 2b: the code seal (review H2) ----

    def test_h2_a_prediction_sealed_after_its_lane_code_refuses(self):
        self.commit(
            "W-1: prediction",
            {
                self.r("moves.prediction.json"): GOOD_P,
                self.r("moves.baseline.json"): BASE_M,
            },
        )
        b = self.head()
        self.git("checkout", "-q", "-b", "lane")
        self.assertEqual(self.code("W-1: the change", {"src/app.py": "v = 2\n"}), 0)
        self.git("checkout", "-q", self.default)
        self.seal("first", "moves.prediction.json")
        self.seal("first", "moves.baseline.json")
        self.git("checkout", "-q", "lane")
        self.measure("first", PASS_M, b, self.head())
        self.commit("W-1: measurement")
        self.git("checkout", "-q", self.default)
        self.git("merge", "-q", "--no-ff", "lane", "-m", "Merge lane")
        self.assertRefuses("its prediction was sealed after its first code commit")

    def test_h2_code_committed_outside_the_tool_refuses(self):
        self.commit(
            "W-1: prediction",
            {
                self.r("moves.prediction.json"): GOOD_P,
                self.r("moves.baseline.json"): BASE_M,
            },
        )
        self.seal("first", "moves.prediction.json")
        self.seal("first", "moves.baseline.json")
        b = self.head()
        self.commit("W-1: the change", {"src/app.py": "v = 2\n"})
        self.measure("first", PASS_M, b, self.head())
        self.commit("W-1: measurement")
        self.assertRefuses(
            "no code commit of the round is sealed",
            "predict-gate.sh seal-code W-1 first",
        )

    def test_h2_a_code_seal_that_is_not_the_first_code_change_refuses(self):
        self.commit(
            "W-1: prediction",
            {
                self.r("moves.prediction.json"): GOOD_P,
                self.r("moves.baseline.json"): BASE_M,
            },
        )
        self.seal("first", "moves.prediction.json")
        self.seal("first", "moves.baseline.json")
        b = self.head()
        self.commit("W-1: the change", {"src/app.py": "v = 2\n"})
        self.assertEqual(self.code("W-1: a tidy", {"src/extra.py": "x = 1\n"}), 0)
        self.measure("first", PASS_M, b, self.head())
        self.commit("W-1: measurement")
        self.assertRefuses("is not the round's first code change")

    def test_h2_a_round_that_changed_no_code_needs_no_code_seal(self):
        self.commit(
            "W-1: prediction",
            {
                self.r("moves.prediction.json"): GOOD_P,
                self.r("moves.baseline.json"): BASE_M,
            },
        )
        self.seal("first", "moves.prediction.json")
        self.seal("first", "moves.baseline.json")
        b = self.head()
        self.measure("first", PASS_M, b, b)
        self.commit("W-1: measurement")
        self.assertCloses()

    def test_step2b_a_scan_read_once_gives_every_work_order_the_same_answer(self):
        self.flow()
        scan = pg.sealed_scan(self.ledger)
        for wo in ("W-1", "W-2"):
            self.assertEqual(
                pg.sealed_state(self.ledger, wo, scan), pg.sealed_state(self.ledger, wo)
            )

    def test_f7_an_amended_away_head_with_no_code_seal_refuses_through_its_sealed_trees(
        self,
    ):
        self.commit(
            "W-1: prediction",
            {
                self.r("moves.prediction.json"): GOOD_P,
                self.r("moves.baseline.json"): BASE_M,
            },
        )
        self.seal("first", "moves.prediction.json")
        self.seal("first", "moves.baseline.json")
        b = self.head()
        self.commit("W-1: the change, outside the tool", {"src/app.py": "v = 2\n"})
        h = self.head()
        self.measure("first", PASS_M, b, h)
        self.git("commit", "-q", "--amend", "-m", "W-1: the change, reworded")
        for a in (
            ["reflog", "expire", "--expire=now", "--all"],
            ["gc", "-q", "--prune=now"],
        ):
            self.git(*a)
        self.assertNotEqual(
            subprocess.run(
                ["git", "-C", str(self.repo), "cat-file", "-e", f"{h}^{{commit}}"],
                capture_output=True,
                check=False,
            ).returncode,
            0,
            "the head is gone",
        )
        self.commit("W-1: measurement")
        self.assertRefuses("no code commit of the round is sealed")

    # ---- seal fixes: seal refuses what close would refuse later ----

    def test_seal_f1_a_chained_round_whose_baseline_equals_the_earlier_measured_file_closes(self):
        # the fix round's own baseline holds the first round's sealed measured bytes and has no seal of its own
        self.flow()
        self.commit(
            "W-1: fix1 prediction",
            {
                self.r("moves.prediction.fix1.json"): {
                    "moves": [{"what": "out:v", "from": 2, "to": 3}],
                    "mustNotMove": ["out:w"],
                },
                self.r("moves.baseline.fix1.json"): PASS_M,
            },
        )
        self.assertEqual(self.seal("fix1", "moves.prediction.fix1.json"), 0)
        b = self.head()
        self.assertEqual(self.code("W-1: fix1 change", {"src/app.py": "v = 3\n"}, "fix1"), 0)
        self.assertEqual(self.measure("fix1", {"out:v": 3, "out:w": 5}, b, self.head()), 0)
        self.commit("W-1: fix1 measurement")
        self.assertCloses()

    def fix_round(self, rnd, v, seal=True):
        """A fix round chained on the first round's baseline: out:v 1 -> v, its code sealed, its measured file
        written with its report and committed; sealed when seal is true."""
        self.commit(
            f"W-1: {rnd} prediction",
            {self.r(f"moves.prediction.{rnd}.json"): {"moves": [{"what": "out:v", "from": 1, "to": v}],
                                                      "mustNotMove": ["out:w"]}},
        )
        self.assertEqual(self.seal(rnd, f"moves.prediction.{rnd}.json"), 0)
        b = self.head()
        self.assertEqual(self.code(f"W-1: {rnd} change", {"src/app.py": f"v = {v}\n"}, rnd), 0)
        self.put(f"moves.measured.{rnd}.json", {"out:v": v, "out:w": 5})
        self.put(f"measure-{rnd}-report.json", {"base": {"sha": b}, "head": {"sha": self.head()}})
        if seal:
            self.assertEqual(self.seal(rnd, f"moves.measured.{rnd}.json"), 0)
        self.commit(f"W-1: {rnd} measurement")

    def test_seal_f3_a_late_seal_of_an_earlier_round_is_not_the_latest_measurement(self):
        # fix1's measured file is sealed after fix2's: the latest measurement is fix2's, by round order
        self.flow()
        self.fix_round("fix1", 3, seal=False)
        self.fix_round("fix2", 4)
        self.assertEqual(self.seal("fix1", "moves.measured.fix1.json"), 0)
        self.commit("W-1: fix1 sealed late")
        self.assertCloses()
        _, evs, _ = pg.sealed_state(self.ledger, "W-1")
        self.assertEqual(pg.sealed_stale(self.repo, "HEAD", evs, ["src/"], "W-1"), (True, None))

    def assert_seal_order_note(self, *labels):
        rc, out = self.close("sealed: judged by", "cannot be ordered by round number",
                             "the latest measurement is the last one sealed", *labels)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("Refused:", out)
        _, evs, _ = pg.sealed_state(self.ledger, "W-1")
        self.assertEqual(pg.sealed_stale(self.repo, "HEAD", evs, ["src/"], "W-1"), (True, None))

    def test_seal_f3_fallback_two_labels_with_one_number_use_seal_order_with_a_note(self):
        self.flow()
        self.fix_round("fix5", 3)
        self.fix_round("r5", 4)
        self.assert_seal_order_note("fix5", "r5")

    def test_seal_f3_fallback_a_free_label_uses_seal_order_with_a_note(self):
        self.flow()
        self.fix_round("blocks2", 3)
        self.assert_seal_order_note("blocks2")

    def test_seal_f3_fallback_a_free_label_keeps_the_old_rule_exactly(self):
        # free labels: the last measured seal in the ledger is the latest, as in 0.2.0, so a late seal of an
        # earlier round still reads as the latest and the later code refuses
        self.flow()
        self.fix_round("v2", 3, seal=False)
        self.fix_round("v3", 4)
        self.assertEqual(self.seal("v2", "moves.measured.v2.json"), 0)
        self.commit("W-1: v2 sealed late")
        self.assertRefuses("cannot be ordered by round number", "code came after the latest measurement")

    def test_seal_f3_the_latest_measurement_by_round_number(self):
        def m(rnd, n=0):
            return {"kind": "sealed", "fileKind": "measured", "round": rnd, "path": f"p{n}", "sha256": rnd}

        evs = [m("first"), m("fix3"), m("fix1"), m("fix3", 1)]
        self.assertEqual(pg.latest_measured(evs), (evs[3], None), "within a round the last live seal wins")
        self.assertEqual(pg.latest_measured([m("r4"), m("fix5"), m("first")])[0]["round"], "fix5")
        self.assertEqual(pg.latest_measured([m("fix10"), m("fix9")])[0]["round"], "fix10")
        self.assertEqual(pg.latest_measured([m("proto")]), (m("proto"), None), "one round needs no order")
        self.assertEqual(pg.latest_measured([]), (None, None))
        for evs in ([m("fix5"), m("r5")], [m("first"), m("fix0")], [m("fix3"), m("blocks2")],
                    [m("built-v5"), m("first")]):
            got, note = pg.latest_measured(evs)
            self.assertIs(got, evs[-1], "seal order, as before")
            self.assertIn("cannot be ordered by round number", note)

if __name__ == "__main__":
    unittest.main()
