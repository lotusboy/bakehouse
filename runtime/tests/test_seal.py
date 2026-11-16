"""Tests for the sealer: predict-gate.sh seal, revise, retire and verify-chain, and the ops ledger's one
append path (runtime/ops_ledger.py: the lock and the chain). Owner decision d20261103-2a691d.
python3 -m unittest discover -s runtime/tests
"""

import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNTIME))
import ops_ledger

REC = "runs/2026-11-10-W-1-step1-review"
PRED = {"moves": [{"what": "out:v", "from": 1, "to": 2}], "mustNotMove": ["out:w"]}
BASE = {"out:v": 1, "out:w": 5}
LEGACY = [
    '{"ts": "2026-11-01T00:00:00+00:00", "kind": "note", "purpose": "legacy one"}',
    '{"ts": "2026-11-01T00:00:01+00:00", "kind": "note", "purpose": "legacy two"}',
]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def team(repo: Path, binding: dict, env: dict) -> Path:
    """Give the git repo at repo a team: .team/binding.json and a records worktree beside it, named by
    `git config bakehouse.records`. Returns the records folder."""
    (repo / ".team").mkdir(exist_ok=True)
    (repo / ".team" / "binding.json").write_text(json.dumps(binding))
    rec = repo.with_name(f"{repo.name}.team-records")
    for a in (["worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(rec)],
              ["config", "bakehouse.records", str(rec)]):
        subprocess.run(["git", "-C", str(repo), *a], check=True, env=env, capture_output=True)
    return rec


class SealTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name).resolve()
        self.repo = t / "repo"
        self.rt = t / "runtime"
        self.rt.mkdir()
        for f in RUNTIME.iterdir():
            if f.is_file():
                (self.rt / f.name).symlink_to(f)
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
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.records = team(self.repo, {"owned": True}, self.env)
        self.binding = self.repo / ".team" / "binding.json"
        self.ledger = self.records / "ops/events.jsonl"
        self.ledger.parent.mkdir(parents=True)
        self.ledger.write_text("".join(x + "\n" for x in LEGACY))
        self.write(
            {
                f"{REC}/moves.prediction.json": PRED,
                f"{REC}/moves.baseline.json": BASE,
                f"{REC}/moves.measured.json": {"out:v": 2, "out:w": 5},
                f"{REC}/moves.prediction.fix1.json": PRED,
                f"{REC}/moves.measured.fix1.json": {"out:v": 2, "out:w": 6},
                # seal F2: a measured seal needs its measure report
                f"{REC}/measure-report.json": {"base": {"sha": "e" * 40}, "head": {"sha": "f" * 40}},
                f"{REC}/measure-fix1-report.json": {"base": {"sha": "e" * 40}, "head": {"sha": "f" * 40}},
            }
        )
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "W-1: record")
        for a in (
            ["add", "-A"],
            ["commit", "-q", "-m", "scratch records"],
        ):  # the records worktree is a git checkout: seals anchor there
            subprocess.run(
                ["git", "-C", str(self.records), *a],
                check=True,
                env=self.env,
                capture_output=True,
            )

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *a):
        return subprocess.run(
            ["git", "-C", str(self.repo), *a],
            capture_output=True,
            text=True,
            env=self.env,
            check=True,
        ).stdout.strip()

    def write(self, files):
        for rel, data in files.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(data if isinstance(data, str) else json.dumps(data))

    def f(self, name):
        return str(self.repo / REC / name)

    def gate(self, *args, **env):
        return subprocess.run(
            ["python3", str(self.rt / "predict-gate.py"), *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
            env=dict(self.env, **env),
            check=False,
        )

    def seal(self, rnd, name, *extra, **env):
        return self.gate("seal", "W-1", rnd, self.f(name), *extra, **env)

    def ops(self, *args):
        return subprocess.run(
            [str(self.rt / "ops-event.sh"), *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )

    def lines(self):
        return [x for x in self.ledger.read_text().split("\n") if x.strip()]

    def events(self):
        return [json.loads(x) for x in self.lines()]

    def decide(
        self, did, task="W-1", approves="yes", choice="yes", options=("yes", "no")
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

    @contextlib.contextmanager
    def hold_lock(self):
        """Hold the ledger's lock as another writer would: its own lock file, beside the ledger."""
        lp = ops_ledger.lock_path(self.ledger)
        lp.parent.mkdir(parents=True, exist_ok=True)
        with lp.open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            yield

    def assertWrote(self, n):
        self.assertEqual(len(self.lines()) - len(LEGACY), n, self.ledger.read_text())

    # ---- seal ----

    def test_seal_writes_one_chained_event_with_the_files_sha256(self):
        p = self.seal("first", "moves.prediction.json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        lines = self.lines()
        self.assertEqual(
            lines[:2], LEGACY, "the events before the chain stay as they are"
        )
        ev = json.loads(lines[2])
        self.assertEqual(
            (ev["kind"], ev["task"], ev["round"], ev["fileKind"], ev["path"]),
            ("sealed", "W-1", "first", "prediction", f"{REC}/moves.prediction.json"),
        )
        self.assertEqual(
            ev["sha256"],
            hashlib.sha256(
                Path(self.f("moves.prediction.json")).read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(ev["prev"], sha(lines[1]))
        self.assertEqual(ev["commit"], self.git("rev-parse", "--short", "HEAD"))
        self.assertNotEqual(ev["ts"][:4], "2020")

    def test_seal_baseline_and_a_dash_round(self):
        self.write({f"{REC}/moves.prediction-fix2.json": PRED})
        self.assertEqual(self.seal("first", "moves.baseline.json").returncode, 0)
        self.assertEqual(self.seal("fix2", "moves.prediction-fix2.json").returncode, 0)
        self.assertEqual(
            [(e["fileKind"], e["round"]) for e in self.events()[2:]],
            [("baseline", "first"), ("prediction", "fix2")],
        )

    def test_round_must_match_the_file_name(self):
        p = self.seal("fix1", "moves.prediction.json")
        self.assertEqual(p.returncode, 2)
        self.assertIn("is a round 'first' file, not round 'fix1'", p.stderr)
        self.assertFalse(any(e["kind"] == "sealed" for e in self.events()))

    def test_refuses_a_file_that_is_not_a_moves_file(self):
        self.write({f"{REC}/notes.json": "{}"})
        p = self.seal("first", "notes.json")
        self.assertEqual(p.returncode, 2)
        self.assertIn(
            "is not a moves.prediction, moves.baseline or moves.measured file", p.stderr
        )

    def test_measured_seal_carries_the_check_and_the_reports_commits(self):
        self.write(
            {
                f"{REC}/measure-report.json": {
                    "base": {"sha": "a" * 40},
                    "head": {"sha": "b" * 40},
                }
            }
        )
        self.seal("first", "moves.baseline.json")  # seal F1: the baseline first
        p = self.seal("first", "moves.measured.json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        ev = self.events()[-1]
        self.assertEqual(
            (ev["base"], ev["head"], ev["report"]),
            ("a" * 40, "b" * 40, f"{REC}/measure-report.json"),
        )
        self.assertEqual(ev["check"]["result"], "pass")
        self.assertEqual(ev["check"]["failures"], 0)
        self.assertEqual(ev["check"]["prediction"], f"{REC}/moves.prediction.json")
        self.assertEqual(
            ev["check"]["predictionSha256"],
            hashlib.sha256(
                Path(self.f("moves.prediction.json")).read_bytes()
            ).hexdigest(),
        )

    def test_measured_seal_reads_a_named_report_and_refuses_when_there_is_none(self):
        self.write(
            {
                f"{REC}/measure-stepA-report.json": {
                    "base": {"sha": "c" * 40},
                    "head": {"sha": "d" * 40},
                }
            }
        )
        (self.repo / REC / "measure-report.json").unlink()
        self.seal("first", "moves.baseline.json")  # seal F1: the baseline first
        # seal F2: no report, no seal (it was sealed with "report": null before)
        self.assertEqual(self.seal("first", "moves.measured.json").returncode, 2)
        self.assertEqual(
            self.seal(
                "first",
                "moves.measured.json",
                "--report",
                self.f("measure-stepA-report.json"),
            ).returncode,
            0,
        )
        self.assertEqual(
            (self.events()[-1]["base"], self.events()[-1]["head"]), ("c" * 40, "d" * 40)
        )

    def test_a_report_without_commits_is_refused(self):
        self.write(
            {f"{REC}/measure-report.json": {"base": {}, "head": {"sha": "b" * 40}}}
        )
        p = self.seal("first", "moves.measured.json")
        self.assertEqual(p.returncode, 2)
        self.assertIn("has no base.sha and head.sha", p.stderr)

    def test_a_refused_check_is_still_sealed_and_exits_1(self):
        self.seal("first", "moves.baseline.json")  # seal F1: the baseline first
        p = self.seal("fix1", "moves.measured.fix1.json")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        ev = self.events()[-1]
        self.assertEqual(
            (ev["kind"], ev["check"]["result"], ev["check"]["failures"]),
            ("sealed", "refused", 1),
        )
        self.assertIn("The sealed check REFUSED", p.stdout)

    def test_refuses_ops_ts_and_ops_commit_and_its_refusal_is_not_backdated(self):
        for k, v in (
            ("OPS_TS", "2020-01-01T00:00:00+00:00"),
            ("OPS_COMMIT", "feedbee"),
        ):
            n = len(self.lines())
            p = self.seal("first", "moves.prediction.json", **{k: v})
            self.assertEqual(p.returncode, 2)
            self.assertIn(f"{k} is set", p.stderr)
            new = self.events()[n:]
            self.assertEqual(
                [e["kind"] for e in new], ["gate-refused"], "only the refusal event"
            )
            self.assertNotEqual(new[0]["ts"][:4], "2020")
            self.assertNotEqual(new[0]["commit"], "feedbee")

    def test_refuses_when_no_project_is_found_and_takes_repo(self):
        lane = Path(self.tmp.name) / "lane"
        subprocess.run(["git", "clone", "-q", str(self.repo), str(lane)], check=True, capture_output=True)
        rec = str(lane / REC / "moves.prediction.json")
        p = self.gate("seal", "W-1", "first", rec)
        self.assertEqual(p.returncode, 2)
        self.assertIn("bakehouse setup --pointer", p.stderr)  # a lane clone with no records pointer
        self.assertWrote(0)
        self.assertEqual(self.gate("seal", "W-1", "first", rec, "--repo", str(self.repo)).returncode, 0)
        self.assertEqual(
            self.gate("seal", "W-1", "first", str(lane / REC / "moves.baseline.json"), "--repo",
                      str(self.repo)).returncode, 0
        )
        self.assertWrote(2)

    def test_refuses_a_project_that_keeps_no_ledger(self):
        t = Path(self.tmp.name)
        for name, why in (("u", '.team/binding.json does not say "owned": true'), ("nope", "has no team")):
            subprocess.run(["git", "init", "-q", str(t / name)], check=True)
            if name == "u":
                team(t / name, {"owned": False}, self.env)
            p = self.seal("first", "moves.prediction.json", "--repo", str(t / name))
            self.assertEqual(p.returncode, 2, name)
            self.assertIn(why, p.stderr)
        self.assertFalse(any(e["kind"] == "sealed" for e in self.events()))

    # ---- revise ----

    def revise(self, did):
        return self.gate(
            "revise", "W-1", "first", self.f("moves.prediction.json"), "--decision", did
        )

    def test_revise_needs_a_decision(self):
        p = self.gate("revise", "W-1", "first", self.f("moves.prediction.json"))
        self.assertEqual(p.returncode, 2)
        self.assertIn("--decision", p.stderr)

    def test_revise_writes_a_revised_event_when_the_decision_approves(self):
        self.seal("first", "moves.prediction.json")
        self.write(
            {
                f"{REC}/moves.prediction.json": {
                    "moves": [{"what": "out:v", "range": [2, 3]}]
                }
            }
        )
        self.decide(
            "d20261101-bbbbb1",
            approves="revise",
            choice="revise",
            options=("keep", "revise"),
        )
        p = self.revise("d20261101-bbbbb1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        lines = self.lines()
        ev = json.loads(lines[-1])
        self.assertEqual(
            (ev["kind"], ev["decision"], ev["fileKind"]),
            ("revised", "d20261101-bbbbb1", "prediction"),
        )
        self.assertEqual(ev["prev"], sha(lines[-2]))

    def test_revise_refuses_an_unchanged_or_never_sealed_prediction(self):
        self.decide(
            "d20261101-bbbbb1",
            approves="revise",
            choice="revise",
            options=("keep", "revise"),
        )
        p = self.revise("d20261101-bbbbb1")
        self.assertEqual(p.returncode, 2)
        self.assertIn("seal it before revising it", p.stderr)
        self.seal("first", "moves.prediction.json")
        p = self.revise("d20261101-bbbbb1")
        self.assertEqual(p.returncode, 2)
        self.assertIn("unchanged since its last seal or revise", p.stderr)

    def test_revise_applies_the_decision_rule(self):
        self.seal("first", "moves.prediction.json")
        self.write(
            {
                f"{REC}/moves.prediction.json": {
                    "moves": [{"what": "out:v", "range": [2, 3]}]
                }
            }
        )
        self.decide(
            "d20261101-bbbbb2",
            approves="revise",
            choice="keep",
            options=("keep", "revise"),
        )
        self.decide(
            "d20261101-bbbbb3",
            task="W-9",
            approves="revise",
            choice="revise",
            options=("keep", "revise"),
        )
        self.decide(
            "d20261101-bbbbb4", task="W-9", approves=None
        )  # agreed true, no --approves
        self.decide("d20261101-bbbbb5", task="W-1", approves=None)
        for did, why in (
            ("d20261101-ccccc1", "is not in the decision ledger"),
            ("d20261101-bbbbb2", "not the approving option"),
            ("d20261101-bbbbb3", "was asked for W-9, not W-1"),
            (
                "d20261101-bbbbb4",
                "was asked without --approves, so it cannot approve a revise",
            ),
            (
                "d20261101-bbbbb5",
                "was asked without --approves, so it cannot approve a revise",
            ),
            ("not-an-id", "is not a decision id"),
        ):
            p = self.revise(did)
            self.assertEqual(p.returncode, 2, did)
            self.assertIn(why, p.stderr)
        self.assertFalse(any(e["kind"] == "revised" for e in self.events()))

    def test_revise_is_only_for_a_prediction(self):
        self.decide("d20261101-bbbbb1")
        p = self.gate(
            "revise",
            "W-1",
            "first",
            self.f("moves.baseline.json"),
            "--decision",
            "d20261101-bbbbb1",
        )
        self.assertEqual(p.returncode, 2)
        self.assertIn("revise follows a prediction", p.stderr)

    # ---- retire ----

    def to_history(self):
        (self.repo / REC / "history").mkdir(exist_ok=True)
        self.git(
            "mv",
            f"{REC}/moves.measured.fix1.json",
            f"{REC}/history/moves.measured.fix1.json",
        )
        return self.f("history/moves.measured.fix1.json")

    def test_retire_writes_a_retired_event_for_a_sealed_file_moved_to_history(self):
        self.seal("first", "moves.baseline.json")  # seal F1: the baseline first
        self.seal("fix1", "moves.measured.fix1.json")
        self.decide(
            "d20261101-bbbbb1",
            approves="retire",
            choice="retire",
            options=("keep", "retire"),
        )
        p = self.gate(
            "retire", "W-1", "fix1", self.to_history(), "--decision", "d20261101-bbbbb1"
        )
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        ev = self.events()[-1]
        self.assertEqual(
            (ev["kind"], ev["fileKind"], ev["round"], ev["path"]),
            ("retired", "measured", "fix1", f"{REC}/history/moves.measured.fix1.json"),
        )

    def test_retire_refuses_outside_history_unsealed_or_another_round(self):
        self.decide(
            "d20261101-bbbbb1",
            approves="retire",
            choice="retire",
            options=("keep", "retire"),
        )
        p = self.gate(
            "retire",
            "W-1",
            "fix1",
            self.f("moves.measured.fix1.json"),
            "--decision",
            "d20261101-bbbbb1",
        )
        self.assertEqual(p.returncode, 2)
        self.assertIn("is not under a history/ folder", p.stderr)
        moved = self.to_history()
        p = self.gate("retire", "W-1", "fix1", moved, "--decision", "d20261101-bbbbb1")
        self.assertEqual(p.returncode, 2)
        self.assertIn("no sealed file of W-1 moved to", p.stderr)
        self.git(
            "mv",
            f"{REC}/history/moves.measured.fix1.json",
            f"{REC}/moves.measured.fix1.json",
        )
        self.seal("first", "moves.baseline.json")  # seal F1: the baseline first
        self.seal("fix1", "moves.measured.fix1.json")
        moved = self.to_history()
        p = self.gate("retire", "W-1", "first", moved, "--decision", "d20261101-bbbbb1")
        self.assertEqual(p.returncode, 2)
        self.assertIn("was sealed as round 'fix1', not round 'first'", p.stderr)
        self.assertFalse(any(e["kind"] == "retired" for e in self.events()))

    def test_retire_needs_a_decision(self):
        p = self.gate("retire", "W-1", "fix1", self.f("moves.measured.fix1.json"))
        self.assertEqual(p.returncode, 2)
        self.assertIn("--decision", p.stderr)

    # ---- the chain, through ops-event.sh ----

    def test_ops_event_chains_once_the_chain_has_started(self):
        self.assertEqual(self.ops("note", "-", "before").returncode, 0)
        self.assertNotIn("prev", self.events()[-1], "no chain before the first seal")
        self.seal("first", "moves.prediction.json")
        self.assertEqual(self.ops("note", "-", "after").returncode, 0)
        lines = self.lines()
        self.assertEqual(json.loads(lines[-1])["prev"], sha(lines[-2]))

    def test_ops_event_refuses_the_sealed_kinds(self):
        for kind in ("sealed", "revised", "retired"):
            p = self.ops(kind, "W-1", "by hand")
            self.assertEqual(p.returncode, 1, kind)
            self.assertIn(
                "is written only by predict-gate.sh seal, revise or retire", p.stderr
            )
        self.assertWrote(0)

    # ---- verify-chain ----

    def verify(self):
        return self.gate("verify-chain", "--ledger", str(self.ledger))

    def test_verify_chain_not_started_and_whole(self):
        p = self.verify()
        self.assertEqual(p.returncode, 0)
        self.assertIn("Chain: not started. 2 event(s), none chained.", p.stdout)
        self.seal("first", "moves.prediction.json")
        self.seal("first", "moves.baseline.json")
        p = self.gate("verify-chain", "--repo", str(self.repo))
        self.assertEqual(p.returncode, 0)
        self.assertIn(
            "Chain: whole. 2 chained event(s), lines 3 to 4; 2 earlier event(s) not chained.",
            p.stdout,
        )

    def test_verify_chain_reports_the_first_broken_link(self):
        self.seal("first", "moves.prediction.json")
        self.seal("first", "moves.baseline.json")
        self.seal("fix1", "moves.prediction.fix1.json")
        good = self.lines()
        for name, lines, at in (
            ("edited", good[:2] + [good[2].replace("first", "First")] + good[3:], 4),
            ("deleted", good[:2] + good[3:], 3),
            ("unchained line", good[:3] + ['{"kind": "note"}'] + good[3:], 4),
            ("the line the chain starts from", good[:1] + ["{}"] + good[2:], 3),
        ):
            self.ledger.write_text("".join(x + "\n" for x in lines))
            p = self.verify()
            self.assertEqual(p.returncode, 1, name)
            self.assertIn(f"BROKEN at line {at}", p.stdout, name)
        self.ledger.write_text("".join(x + "\n" for x in ["{}"] + good[1:]))
        self.assertEqual(
            self.verify().returncode,
            0,
            "a limit: lines before the chain's first link are not judged",
        )

    def test_verify_chain_needs_a_ledger(self):
        p = self.gate("verify-chain", "--ledger", str(self.ledger) + ".missing")
        self.assertEqual(p.returncode, 2)
        self.assertIn("no ledger at", p.stderr)

    # ---- the lock ----

    def test_the_seal_waits_for_the_ledger_lock(self):
        with self.hold_lock():
            p = subprocess.Popen(
                [
                    "python3",
                    str(self.rt / "predict-gate.py"),
                    "seal",
                    "W-1",
                    "first",
                    self.f("moves.prediction.json"),
                ],
                cwd=self.repo,
                env=self.env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            self.assertIsNone(
                p.poll(), "the seal wrote while another writer held the lock"
            )
            self.assertWrote(0)
        self.assertEqual(p.wait(timeout=30), 0)
        self.assertWrote(1)

    def test_ops_event_waits_for_the_ledger_lock(self):
        with self.hold_lock():
            p = subprocess.Popen(
                [str(self.rt / "ops-event.sh"), "note", "-", "x"],
                cwd=self.repo,
                env=self.env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            self.assertIsNone(
                p.poll(), "ops-event.sh wrote while another writer held the lock"
            )
        self.assertEqual(p.wait(timeout=30), 0)
        self.assertWrote(1)

    def test_two_sealers_at_once_keep_the_chain_whole(self):
        script = Path(self.tmp.name) / "lane.sh"
        script.write_text(
            "#!/bin/sh\nfor i in 1 2 3 4 5 6 7 8; do\n"
            f'  python3 {self.rt / "predict-gate.py"} seal W-1 first "$1" >/dev/null 2>&1\n'
            "  ./ops-event.sh note - lane >/dev/null 2>&1\ndone\n".replace(
                "./ops-event.sh", str(self.rt / "ops-event.sh")
            )
        )
        script.chmod(0o755)
        self.seal(
            "first", "moves.prediction.json"
        )  # the chain has started before either lane writes
        ps = [
            subprocess.Popen([str(script), self.f(n)], cwd=self.repo, env=self.env)
            for n in ("moves.prediction.json", "moves.baseline.json")
        ]
        for p in ps:
            self.assertEqual(p.wait(timeout=120), 0)
        self.assertWrote(33)
        p = self.verify()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("Chain: whole. 33 chained event(s)", p.stdout)

    # ---- fix round 2: one test per review finding (v12c/review/findings.md) ----

    def raw(self):
        return self.ledger.read_bytes()

    def test_f1_a_torn_last_line_gets_its_newline_and_the_seal_is_kept(self):
        with self.ledger.open("ab") as fh:
            fh.write(b'{"ts": "2026-11-01T00:00:02+00:00", "kind": "no')
        p = self.seal("first", "moves.prediction.json")
        self.assertEqual(p.returncode, 0, p.stderr)
        lines = self.raw().split(b"\n")
        self.assertEqual(lines[2], b'{"ts": "2026-11-01T00:00:02+00:00", "kind": "no')
        ev = json.loads(lines[3])
        self.assertEqual(
            (ev["kind"], ev["prev"]), ("sealed", hashlib.sha256(lines[2]).hexdigest())
        )
        v = self.verify()
        self.assertEqual(v.returncode, 1)
        self.assertEqual(
            v.stdout.strip(), "Chain: BROKEN at line 3: it is not a JSON object."
        )

    def test_f1_a_torn_line_after_the_chain_started(self):
        self.seal("first", "moves.prediction.json")
        with self.ledger.open("ab") as fh:
            fh.write(b'{"kind": "no')
        self.assertEqual(self.seal("first", "moves.baseline.json").returncode, 0)
        self.assertEqual(json.loads(self.raw().split(b"\n")[4])["fileKind"], "baseline")
        v = self.verify()
        self.assertEqual(
            v.stdout.strip(), "Chain: BROKEN at line 4: it is not a JSON object."
        )

    def test_f3_a_line_that_is_not_utf8_never_stops_a_write(self):
        with self.ledger.open("ab") as fh:
            fh.write(b'{"kind": "note", "note": "caf\xe9"}\n')
        self.assertEqual(self.ops("note", "-", "after").returncode, 0)
        self.assertEqual(self.seal("first", "moves.prediction.json").returncode, 0)
        lines = self.raw().split(b"\n")
        self.assertEqual(
            json.loads(lines[4])["prev"], hashlib.sha256(lines[3]).hexdigest()
        )
        v = self.verify()
        self.assertEqual(v.returncode, 1)
        self.assertEqual(v.stdout.strip(), "Chain: BROKEN at line 3: it is not UTF-8.")

    def test_f1_f3_verify_reports_every_line_it_cannot_read(self):
        with self.ledger.open("ab") as fh:
            fh.write(b"not json\n[1, 2]\n\xff\n")
        v = self.verify()
        self.assertEqual(v.returncode, 1)
        self.assertEqual(
            v.stdout.strip().split("\n"),
            [
                "Chain: BROKEN at line 3: it is not a JSON object.",
                "Chain: BROKEN at line 4: it is not a JSON object.",
                "Chain: BROKEN at line 5: it is not UTF-8.",
            ],
        )

    def test_f4_the_lock_holds_while_a_checkout_swaps_the_ledger_file(self):
        with self.hold_lock():
            swapped = self.ledger.with_name("swap.tmp")
            swapped.write_bytes(self.raw())
            os.replace(
                swapped, self.ledger
            )  # a new inode at the same path, as git checkout writes
            p = subprocess.Popen(
                [
                    "python3",
                    str(self.rt / "predict-gate.py"),
                    "seal",
                    "W-1",
                    "first",
                    self.f("moves.prediction.json"),
                ],
                cwd=self.repo,
                env=self.env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            self.assertIsNone(
                p.poll(), "the seal took the lock on the new file while it was held"
            )
        self.assertEqual(p.wait(timeout=30), 0)
        self.assertWrote(1)

    def test_f5_verify_refuses_a_seal_without_prev_before_the_chain(self):
        forged = json.dumps(
            {
                "kind": "sealed",
                "task": "W-1",
                "round": "first",
                "fileKind": "prediction",
                "sha256": "0" * 64,
            }
        )
        self.ledger.write_text(LEGACY[0] + "\n" + forged + "\n" + LEGACY[1] + "\n")
        self.seal("first", "moves.prediction.json")
        v = self.verify()
        self.assertEqual(v.returncode, 1)
        self.assertIn("Chain: BROKEN at line 2: a sealed event with no prev", v.stdout)

    def test_f5_verify_refuses_stripped_prevs(self):
        self.seal("first", "moves.prediction.json")
        self.seal("first", "moves.baseline.json")
        rows = [json.loads(x) for x in self.lines()]
        self.ledger.write_text(
            "".join(
                json.dumps({k: v for k, v in e.items() if k != "prev"}) + "\n"
                for e in rows
            )
        )
        v = self.verify()
        self.assertEqual(v.returncode, 1)
        self.assertEqual(
            len([x for x in v.stdout.split("\n") if "with no prev" in x]), 2
        )

    def test_f5_verify_refuses_a_chain_that_does_not_start_with_a_seal(self):
        self.ledger.write_text(
            LEGACY[0]
            + "\n"
            + json.dumps({"kind": "note", "prev": sha(LEGACY[0])})
            + "\n"
        )
        v = self.verify()
        self.assertEqual(v.returncode, 1)
        self.assertIn("the chain's first line is a note event, not a seal", v.stdout)

    def test_f5_limit_l7_a_rewrite_that_recomputes_every_prev_passes(self):
        self.seal("first", "moves.prediction.json")
        self.seal("first", "moves.baseline.json")
        self.seal("fix1", "moves.prediction.fix1.json")
        lines = self.lines()
        rows = [json.loads(x) for x in lines]
        rows[2]["sha256"] = "f" * 64
        out = lines[:2]
        for e in rows[2:]:
            e["prev"] = sha(out[-1])
            out.append(json.dumps(e))
        self.ledger.write_text("".join(x + "\n" for x in out))
        self.assertEqual(
            self.verify().returncode,
            0,
            "named limit L7: only an anchor outside the ledger sees this",
        )

    def test_f2_retire_refuses_a_decision_asked_without_approves(self):
        self.seal("first", "moves.baseline.json")  # seal F1: the baseline first
        self.seal("fix1", "moves.measured.fix1.json")
        self.decide("d20261101-bbbbb6", task="W-1", approves=None)
        p = self.gate(
            "retire", "W-1", "fix1", self.to_history(), "--decision", "d20261101-bbbbb6"
        )
        self.assertEqual(p.returncode, 2)
        self.assertIn(
            "was asked without --approves, so it cannot approve a retire", p.stderr
        )

    def test_f6_retire_refuses_a_copy_elsewhere_or_with_the_original_in_place(self):
        self.seal("first", "moves.baseline.json")  # seal F1: the baseline first
        self.seal("fix1", "moves.measured.fix1.json")
        self.decide(
            "d20261101-bbbbb1",
            approves="retire",
            choice="retire",
            options=("keep", "retire"),
        )
        elsewhere = self.repo / "elsewhere/history/notes.txt"
        elsewhere.parent.mkdir(parents=True)
        shutil.copy(self.f("moves.measured.fix1.json"), elsewhere)
        p = self.gate(
            "retire", "W-1", "fix1", str(elsewhere), "--decision", "d20261101-bbbbb1"
        )
        self.assertEqual(p.returncode, 2)
        self.assertIn(
            "no sealed file of W-1 moved to elsewhere/history/notes.txt", p.stderr
        )
        copy = self.repo / REC / "history/moves.measured.fix1.json"
        copy.parent.mkdir()
        shutil.copy(self.f("moves.measured.fix1.json"), copy)
        p = self.gate(
            "retire", "W-1", "fix1", str(copy), "--decision", "d20261101-bbbbb1"
        )
        self.assertEqual(p.returncode, 2)
        self.assertIn(f"{REC}/moves.measured.fix1.json is still there", p.stderr)
        self.assertFalse(any(e["kind"] == "retired" for e in self.events()))

    def test_f6_f11_retire_takes_the_kind_of_the_seal_at_that_name(self):
        self.write(
            {f"{REC}/moves.measured.json": BASE}
        )  # a 0-move round: measured bytes == baseline bytes
        for n in (
            "moves.prediction.json",
            "moves.baseline.json",
            "moves.measured.json",
        ):
            self.assertEqual(self.seal("first", n).returncode, 0, n)
        self.decide(
            "d20261101-bbbbb1",
            approves="retire",
            choice="retire",
            options=("keep", "retire"),
        )
        (self.repo / REC / "history").mkdir()
        for n, kind in (
            ("moves.baseline.json", "baseline"),
            ("moves.measured.json", "measured"),
            ("moves.prediction.json", "prediction"),
        ):
            self.git("mv", f"{REC}/{n}", f"{REC}/history/{n}")
            p = self.gate(
                "retire",
                "W-1",
                "first",
                self.f(f"history/{n}"),
                "--decision",
                "d20261101-bbbbb1",
            )
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(self.events()[-1]["fileKind"], kind)

    def test_f7_seal_refuses_a_changed_prediction_or_baseline(self):
        self.seal("first", "moves.prediction.json")
        self.seal("first", "moves.baseline.json")
        self.assertEqual(
            self.seal("first", "moves.prediction.json").returncode,
            0,
            "unchanged: sealed again",
        )
        self.write(
            {
                f"{REC}/moves.prediction.json": {"moves": [{"what": "out:v"}]},
                f"{REC}/moves.baseline.json": {"out:v": 9},
            }
        )
        p = self.seal("first", "moves.prediction.json")
        self.assertEqual(p.returncode, 2)
        self.assertIn(
            "changed since its seal: a changed prediction is sealed by predict-gate.sh revise",
            p.stderr,
        )
        p = self.seal("first", "moves.baseline.json")
        self.assertEqual(p.returncode, 2)
        self.assertIn(
            "changed since its seal: a changed baseline is a new round", p.stderr
        )

    def test_f7_seal_refuses_a_first_prediction_after_the_measured_seal(self):
        self.seal("first", "moves.baseline.json")  # seal F1: the baseline first
        self.seal("first", "moves.measured.json")
        p = self.seal("first", "moves.prediction.json")
        self.assertEqual(p.returncode, 2)
        self.assertIn("has a sealed measured file and no sealed", p.stderr)
        self.assertEqual(
            self.seal("fix1", "moves.prediction.fix1.json").returncode,
            0,
            "another round is not judged",
        )
        # seal F1: a baseline after its measured seal is reached only through a baseline whose bytes another
        # seal covers
        self.write({f"{REC}/moves.baseline.fix1.json": (self.repo / REC / "moves.baseline.json").read_text()})
        self.seal("fix1", "moves.measured.fix1.json")
        p = self.seal("fix1", "moves.baseline.fix1.json")
        self.assertEqual(p.returncode, 2)
        self.assertIn("has a sealed measured file and no sealed baseline", p.stderr)

    def test_f10_seal_checks_the_work_order_against_the_binding(self):
        self.binding.write_text(
            json.dumps({"owned": True, "tracker": {"id": "^W-[0-9]+$"}})
        )
        p = self.gate("seal", "w-1", "first", self.f("moves.prediction.json"))
        self.assertEqual(p.returncode, 2)
        self.assertIn(
            "'w-1' does not match the binding's tracker id form '^W-[0-9]+$'", p.stderr
        )
        self.assertEqual(self.seal("first", "moves.prediction.json").returncode, 0)

    def test_f11_revise_needs_a_seal_of_its_own_round(self):
        self.seal("fix1", "moves.prediction.fix1.json")
        self.write({f"{REC}/moves.prediction.json": {"moves": [{"what": "out:v"}]}})
        self.decide(
            "d20261101-bbbbb1",
            approves="revise",
            choice="revise",
            options=("keep", "revise"),
        )
        p = self.revise("d20261101-bbbbb1")
        self.assertEqual(p.returncode, 2)
        self.assertIn(
            "no sealed prediction of W-1 round first: seal it before revising it",
            p.stderr,
        )

    def test_f11_revise_and_retire_ignore_another_work_orders_seals(self):
        self.gate("seal", "W-2", "first", self.f("moves.prediction.json"))
        self.gate("seal", "W-2", "fix1", self.f("moves.measured.fix1.json"))
        self.write({f"{REC}/moves.prediction.json": {"moves": [{"what": "out:v"}]}})
        self.decide(
            "d20261101-bbbbb1",
            approves="revise",
            choice="revise",
            options=("keep", "revise"),
        )
        p = self.revise("d20261101-bbbbb1")
        self.assertEqual(p.returncode, 2)
        self.assertIn("no sealed prediction of W-1 round first", p.stderr)
        self.decide(
            "d20261101-bbbbb7",
            approves="retire",
            choice="retire",
            options=("keep", "retire"),
        )
        p = self.gate(
            "retire", "W-1", "fix1", self.to_history(), "--decision", "d20261101-bbbbb7"
        )
        self.assertEqual(p.returncode, 2)
        self.assertIn("no sealed file of W-1 moved to", p.stderr)

    def test_g1_one_lock_through_a_symlink_and_any_environment(self):
        link = Path(self.tmp.name).resolve() / "records-link"
        link.symlink_to(self.records)
        self.git("config", "bakehouse.records", str(link))
        other = Path(self.tmp.name).resolve() / "elsewhere"
        other.mkdir()
        env = dict(
            self.env, BAKEHOUSE_MACHINE_DIR=str(other), HOME=str(other)
        )
        with self.hold_lock():
            p = subprocess.Popen(
                [
                    "python3",
                    str(self.rt / "predict-gate.py"),
                    "seal",
                    "W-1",
                    "first",
                    self.f("moves.prediction.json"),
                ],
                cwd=self.repo,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            self.assertIsNone(
                p.poll(),
                "a writer through a symlink, with another machine folder and HOME, took no lock",
            )
        self.assertEqual(p.wait(timeout=30), 0)
        self.assertWrote(1)

    # ---- Step 2a ----

    def pgit(self, *a):
        return subprocess.run(
            ["git", "-C", str(self.records), *a],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )

    def test_anchor_commits_the_sealed_line_and_only_the_ledger(self):
        self.assertEqual(self.seal("first", "moves.prediction.json").returncode, 0)
        line = self.raw().split(b"\n")[-2]
        rel = str(self.ledger.relative_to(self.records))
        self.assertIn(
            line.decode(), self.pgit("show", f"HEAD:{rel}").stdout.split("\n")
        )
        msg = self.pgit("log", "-1", "--format=%B").stdout
        self.assertIn(f"Sealed-line: {hashlib.sha256(line).hexdigest()}", msg)
        self.assertEqual(
            self.pgit("show", "--name-only", "--format=", "HEAD").stdout.split(), [rel]
        )

    def test_anchor_retries_git_index_lock_then_commits(self):
        lock = Path(self.pgit("rev-parse", "--path-format=absolute", "--git-path", "index.lock").stdout.strip())
        lock.write_text("")
        p = subprocess.Popen(
            [
                "python3",
                str(self.rt / "predict-gate.py"),
                "seal",
                "W-1",
                "first",
                self.f("moves.prediction.json"),
            ],
            cwd=self.repo,
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)
        lock.unlink()
        self.assertEqual(p.wait(timeout=30), 0)
        self.assertIn(
            '"sealed"',
            self.pgit("show", f"HEAD:{self.ledger.relative_to(self.records)}").stdout,
        )

    def test_anchor_refuses_loudly_when_it_cannot_commit(self):
        Path(self.pgit("rev-parse", "--path-format=absolute", "--git-path", "index.lock").stdout.strip()).write_text("")
        p = self.seal("first", "moves.prediction.json")
        self.assertEqual(p.returncode, 1)
        self.assertIn(
            "ANCHOR REFUSED: the sealed line is written to the ledger but is not in a commit",
            p.stderr,
        )
        self.assertIn("seal again once the cause is gone", p.stderr)
        self.assertNotIn("by path", p.stderr)
        rows = [json.loads(x) for x in self.lines()]
        self.assertEqual(rows[-2]["kind"], "sealed")
        self.assertEqual(
            (rows[-1]["kind"], rows[-1].get("gate")),
            ("gate-refused", "predict-gate:anchor"),
        )
        n = len(self.lines())
        (self.records / ".git").unlink()  # the records folder is no longer a git checkout
        p = self.seal("first", "moves.baseline.json")
        self.assertEqual(p.returncode, 2)
        self.assertIn("is missing or is not a records checkout", p.stderr)  # the locator's refusal
        self.assertEqual(len(self.lines()), n, "nothing is written to a ledger that cannot be anchored")

    def test_seal_writes_only_to_the_project_the_locator_finds_for_the_file(self):
        other = Path(self.tmp.name).resolve() / "other"
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        other_ledger = team(other, {"owned": True}, self.env) / "ops/events.jsonl"
        p = self.seal("first", "moves.prediction.json", CLAUDE_PROJECT_DIR=str(other))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.events()[-1]["kind"], "sealed")
        self.assertFalse(other_ledger.exists())

    def test_g5_a_retired_seal_does_not_count(self):
        self.assertEqual(self.seal("first", "moves.prediction.json").returncode, 0)
        self.decide(
            "d20261101-bbbbb1",
            approves="retire",
            choice="retire",
            options=("keep", "retire"),
        )
        (self.repo / REC / "history").mkdir()
        self.git(
            "mv", f"{REC}/moves.prediction.json", f"{REC}/history/moves.prediction.json"
        )
        p = self.gate(
            "retire",
            "W-1",
            "first",
            self.f("history/moves.prediction.json"),
            "--decision",
            "d20261101-bbbbb1",
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.write(
            {
                f"{REC}/moves.prediction.json": {
                    "moves": [{"what": "out:v", "range": [2, 3]}]
                }
            }
        )
        p = self.seal("first", "moves.prediction.json")
        self.assertEqual(
            p.returncode,
            0,
            "a new prediction under a retired round's label: " + p.stderr,
        )

    def test_a_measured_seal_carries_its_heads_code_trees(self):
        self.binding.write_text(
            json.dumps({"owned": True, "signal": {"codeRoots": ["runs/"]}})
        )
        head = self.git("rev-parse", "HEAD")
        self.write(
            {
                f"{REC}/measure-report.json": {
                    "base": {"sha": head},
                    "head": {"sha": head},
                }
            }
        )
        self.seal("first", "moves.baseline.json")  # seal F1: the baseline first
        self.assertEqual(self.seal("first", "moves.measured.json").returncode, 0)
        self.assertEqual(
            self.events()[-1]["headTrees"],
            {"runs/": self.git("rev-parse", "HEAD:runs")},
        )
        self.write(
            {
                f"{REC}/measure-report.json": {
                    "base": {"sha": "a" * 40},
                    "head": {"sha": "b" * 40},
                }
            }
        )
        self.assertEqual(self.seal("first", "moves.measured.json").returncode, 0)
        self.assertIsNone(
            self.events()[-1]["headTrees"],
            "a head this repository does not hold has no trees",
        )

    def test_k6_the_anchor_commits_the_ledger_alone_and_leaves_staged_files_staged(
        self,
    ):
        (self.records / "other.txt").write_text("staged by the PM\n")
        self.pgit("add", "other.txt")
        self.assertEqual(self.seal("first", "moves.prediction.json").returncode, 0)
        rel = str(self.ledger.relative_to(self.records))
        self.assertEqual(
            self.pgit("show", "--name-only", "--format=", "HEAD").stdout.split(), [rel]
        )
        self.assertIn("A  other.txt", self.pgit("status", "--porcelain").stdout)

    def test_h6_a_symlink_to_the_ledger_file_takes_the_same_lock(self):
        link = self.ledger.with_name("events-link.jsonl")
        link.symlink_to(self.ledger)
        self.assertEqual(ops_ledger.lock_path(link), ops_ledger.lock_path(self.ledger))

    # ---- Step 2b: seal-code, the round's first code commit ----

    def code_roots(self):
        self.binding.write_text(
            json.dumps({"owned": True, "signal": {"codeRoots": ["src/"]}})
        )

    def seal_code(self, *commit_args, rnd="first"):
        return self.gate("seal-code", "W-1", rnd, "--", *commit_args)

    def test_seal_code_commits_the_staged_code_and_seals_that_commit(self):
        self.code_roots()
        self.write({"src/a.py": "a = 1\n"})
        self.git("add", "src/a.py")
        before = self.git("rev-parse", "HEAD")
        p = self.seal_code("-q", "-m", "W-1: the change")
        self.assertEqual(p.returncode, 0, p.stderr)
        head = self.git("rev-parse", "HEAD")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "W-1: the change")
        ev = self.events()[-1]
        self.assertEqual(
            (ev["kind"], ev["fileKind"], ev["round"], ev["commit"], ev["parent"]),
            ("sealed", "code", "first", head, before),
        )
        self.assertEqual(ev["codeTrees"], {"src/": self.git("rev-parse", "HEAD:src")})
        self.assertIn(
            self.lines()[-1],
            self.pgit("show", f"HEAD:{self.ledger.relative_to(self.records)}").stdout,
        )
        self.write({"src/b.py": "b = 1\n"})
        self.git("add", "src/b.py")
        p = self.seal_code("-q", "-m", "W-1: more")
        self.assertEqual(p.returncode, 2)
        self.assertIn(
            "round first of W-1 already has a sealed first code commit", p.stderr
        )
        self.assertNotEqual(
            self.git("rev-parse", "HEAD"), head, "the commit stays, unsealed"
        )

    def test_seal_code_refuses_without_staged_code_or_with_dash_a(self):
        self.code_roots()
        before = self.git("rev-parse", "HEAD")
        self.write({f"{REC}/notes.md": "n\n"})
        self.git("add", "-A")
        p = self.seal_code("-q", "-m", "W-1: notes")
        self.assertEqual(p.returncode, 2)
        self.assertIn("nothing under the code roots (src/) is staged", p.stderr)
        self.write({"src/a.py": "a = 1\n"})
        p = self.seal_code("-qam", "W-1: all")
        self.assertEqual(p.returncode, 2)
        self.assertIn("seal-code does not read -a", p.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), before, "nothing is committed")

    def test_seal_code_needs_the_bindings_code_roots(self):
        self.write({"src/a.py": "a = 1\n"})
        self.git("add", "src/a.py")
        p = self.seal_code("-q", "-m", "W-1: the change")
        self.assertEqual(p.returncode, 2)
        self.assertIn("names no signal.codeRoots", p.stderr)

    # ---- seal fixes: seal refuses what close would refuse later ----

    def report(self, name="measure-report.json"):
        self.write({f"{REC}/{name}": {"base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}})
        return self.f(name)

    def test_seal_f1_a_measured_seal_refuses_while_its_baseline_is_unsealed(self):
        self.assertEqual(self.seal("first", "moves.prediction.json").returncode, 0)
        p = self.seal("first", "moves.measured.json", "--report", self.report())
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn(f"predict-gate.sh seal W-1 first {REC}/moves.baseline.json", p.stderr)
        self.assertEqual([e["fileKind"] for e in self.events() if e["kind"] == "sealed"], ["prediction"])
        self.assertEqual(self.seal("first", "moves.baseline.json").returncode, 0)
        p = self.seal("first", "moves.measured.json", "--report", self.report())
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.events()[-1]["fileKind"], "measured")

    def test_seal_f1_a_chained_baseline_equal_to_a_sealed_measured_file_needs_no_seal_of_its_own(self):
        # A1176's shape: the round's own baseline holds the bytes of the earlier round's sealed measured file
        self.write({f"{REC}/moves.baseline.fix1.json": (self.repo / REC / "moves.measured.json").read_text(),
                    f"{REC}/moves.prediction.fix1.json": {"moves": [{"what": "out:w", "from": 5, "to": 6}]}})
        for name in ("moves.prediction.json", "moves.baseline.json"):
            self.assertEqual(self.seal("first", name).returncode, 0)
        self.assertEqual(self.seal("first", "moves.measured.json", "--report", self.report()).returncode, 0)
        self.assertEqual(self.seal("fix1", "moves.prediction.fix1.json").returncode, 0)
        p = self.seal("fix1", "moves.measured.fix1.json", "--report", self.report("measure-fix1-report.json"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.events()[-1]["check"]["baseline"], f"{REC}/moves.baseline.fix1.json")

    def test_seal_f1_another_work_orders_seal_does_not_cover_the_baseline(self):
        self.assertEqual(self.gate("seal", "W-2", "first", self.f("moves.baseline.json")).returncode, 0)
        p = self.seal("first", "moves.measured.json", "--report", self.report())
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("no live seal of W-1 holds its bytes", p.stderr)

    def test_seal_f2_a1179_a_measured_seal_with_no_report_is_refused(self):
        (self.repo / REC / "measure-report.json").unlink()
        self.assertEqual(self.seal("first", "moves.baseline.json").returncode, 0)
        p = self.seal("first", "moves.measured.json")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("--report", p.stderr)
        self.assertIn("base.sha", p.stderr)
        self.assertIn("head.sha", p.stderr)
        self.assertEqual([e["fileKind"] for e in self.events() if e["kind"] == "sealed"], ["baseline"])

    def test_seal_f2_a_report_whose_head_sha_is_null_is_refused(self):
        self.assertEqual(self.seal("first", "moves.baseline.json").returncode, 0)
        self.write({f"{REC}/measure-report.json": {"base": {"sha": "a" * 40}, "head": {"sha": None}}})
        p = self.seal("first", "moves.measured.json")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("head.sha", p.stderr)
        self.assertEqual([e["fileKind"] for e in self.events() if e["kind"] == "sealed"], ["baseline"])


if __name__ == "__main__":
    unittest.main()
