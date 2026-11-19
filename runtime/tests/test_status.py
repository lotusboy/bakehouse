"""Tests for `bakehouse status` (runtime/bakehouse_status.py), the public read contract. Standard library only.
Each test builds a scratch project with a records worktree and hand-written ledger lines; nothing outside a
temporary folder is read or written."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BAKEHOUSE = ROOT / "bin" / "bakehouse"
IDENT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
H = "a" * 64

# Line shapes as ops-event.sh, predict-gate.py's sealer and run_close, and decision.py write them.
OPS = [
    {"ts": "2026-11-01T10:00:00+00:00", "role": "engine", "kind": "sealed", "task": "T1", "purpose": "sealed prediction file",
     "round": "first", "fileKind": "prediction", "path": "runs/2026-11-01-T1-step1-review/moves.prediction.json",
     "sha256": H, "commit": "1111111", "outcome": "done"},
    {"ts": "2026-11-01T11:00:00+00:00", "role": "engine", "kind": "agent-run", "task": "T2", "purpose": "engineer",
     "model": None, "effort": None, "count": None, "tokens": 1000, "durationS": None, "outcome": "done", "note": None,
     "commit": "2222222"},
    {"ts": "2026-11-02T10:00:00+00:00", "role": "engine", "kind": "work-order-closed", "task": "T2",
     "purpose": "work order closed behind the predicted-moves gate", "model": None, "effort": None, "count": 1,
     "tokens": None, "durationS": None, "outcome": "done",
     "note": f"runs/2026-11-01-T2-step1-review/moves.prediction.json sha256:{H}; ledger: x", "commit": "3333333"},
    {"ts": "2026-11-02T12:00:00+00:00", "role": "engine", "kind": "session-start", "task": None, "purpose": "start",
     "model": None, "effort": None, "count": None, "tokens": None, "durationS": None, "outcome": None, "note": None,
     "commit": "3333333"},
    {"ts": "2026-11-03T09:00:00+00:00", "role": "engine", "kind": "sealed", "task": "T1", "purpose": "sealed measured file",
     "round": "fix1", "fileKind": "measured", "path": "runs/2026-11-01-T1-step1-review/moves.measured.fix1.json",
     "sha256": H, "commit": "4444444", "outcome": "done"},
    {"ts": "2026-11-03T10:00:00+00:00", "role": "engine", "kind": "agent-run", "task": "T1", "purpose": "reviewer",
     "model": None, "effort": None, "count": None, "tokens": 2000, "durationS": None, "outcome": "done", "note": None,
     "commit": "4444444"},
    {"ts": "2026-11-04T10:00:00+00:00", "role": "engine", "kind": "gate-refused", "task": "T3",
     "purpose": "work order close overridden", "model": None, "effort": None, "count": 1, "tokens": None,
     "durationS": None, "outcome": "done", "note": "work order close overridden: owner said so | refused: x",
     "commit": "5555555", "gate": "predict-gate:close"},
    {"ts": "2026-11-04T10:00:01+00:00", "role": "engine", "kind": "work-order-closed", "task": "T3",
     "purpose": "work order closed behind the predicted-moves gate", "model": None, "effort": None, "count": 0,
     "tokens": None, "durationS": None, "outcome": "done",
     "note": "OVERRIDDEN, see the gate-refused line; no pair passed; ledger: x", "commit": "5555555"},
]
DECISIONS = [
    {"kind": "ask", "id": "d20261101-aaaaaa", "ts": "2026-11-01T09:00:00+00:00", "stream": "proj", "project": None,
     "category": "spend", "question": "Run the second wave?", "options": ["yes", "no"], "recommendation": "yes",
     "confidence": 0.8, "evidence": {"kind": "measured", "pointer": "spend"}, "blind": False, "autonomous": False,
     "questionsRef": None, "read": None, "task": "T1", "fixRound": None, "rechecked": None, "upstream": None,
     "steppedBack": None, "recheck": False},
    {"kind": "ask", "id": "d20261101-bbbbbb", "ts": "2026-11-01T09:30:00+00:00", "stream": "proj", "project": None,
     "category": "scope", "question": "Split the step?", "options": ["yes", "no"], "recommendation": "no",
     "confidence": 0.7, "evidence": {"kind": "source-read", "pointer": "a.py"}, "blind": False, "autonomous": False,
     "questionsRef": None, "read": None, "task": "T2", "fixRound": None, "rechecked": None, "upstream": None,
     "steppedBack": None, "recheck": False},
    {"kind": "answer", "id": "d20261101-bbbbbb", "ts": "2026-11-01T10:00:00+00:00", "choice": "no", "agreed": True,
     "notes": None},
    {"kind": "ask", "id": "d20261101-cccccc", "ts": "2026-11-01T09:45:00+00:00", "stream": "proj", "project": None,
     "category": "naming", "question": "Name it x?", "options": ["yes", "no"], "recommendation": "yes",
     "confidence": 0.9, "evidence": {"kind": "inference", "pointer": "-"}, "blind": False, "autonomous": True,
     "questionsRef": None, "read": None, "task": None, "fixRound": None, "rechecked": None, "upstream": None,
     "steppedBack": None, "recheck": False},
]


def chained(events) -> bytes:
    """The ledger's bytes: from the first sealed event on, each line carries prev, as ops_ledger.append writes."""
    out, prev = [], None
    for ev in events:
        if prev is not None or ev["kind"] == "sealed":
            ev = dict(ev, prev=prev)
        line = json.dumps(ev).encode()
        out.append(line)
        prev = hashlib.sha256(line).hexdigest()
    return b"\n".join(out) + b"\n"


class Status(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp),
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(self.tmp / ".gitconfig"),
                    "BAKEHOUSE_MACHINE_DIR": str(self.tmp / "machine"), **IDENT}
        self.repo = self.make_repo("proj", {"owned": True})
        self.records = self.tmp / "proj.bakehouse-records"
        self.git(self.repo, "worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(self.records))
        self.git(self.repo, "config", "bakehouse.records", str(self.records))
        self.ops = self.records / "ops" / "events.jsonl"
        self.decisions = self.records / "decisions" / "ledger.jsonl"
        self.ops.parent.mkdir(parents=True)
        self.decisions.parent.mkdir(parents=True)
        self.ops.write_bytes(chained(OPS))
        self.decisions.write_text("".join(json.dumps(e) + "\n" for e in DECISIONS))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def git(self, where, *args):
        return subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, env=self.env,
                              check=True)

    def make_repo(self, name, binding):
        repo = self.tmp / name
        repo.mkdir()
        self.git(repo, "init", "-q", "-b", "main")
        if binding is not None:
            (repo / ".team").mkdir()
            (repo / ".team" / "binding.json").write_text(json.dumps(binding))
            self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "--allow-empty", "-m", "first")
        return repo

    def status(self, *args, cwd=None):
        p = subprocess.run([str(BAKEHOUSE), "status", *args], cwd=cwd or self.repo, capture_output=True, text=True,
                           env=self.env)
        return p.returncode, p.stdout, p.stderr

    def as_json(self, *args, cwd=None):
        rc, out, err = self.status("--json", *args, cwd=cwd)
        self.assertEqual(rc, 0, out + err)
        return json.loads(out)

    def test_the_json_names_the_project_the_open_work_orders_the_waiting_decisions_and_every_close(self):
        s = self.as_json()
        self.assertEqual((s["format"], s["team"], s["project"], s["records"]),
                         (1, True, str(self.repo), str(self.records)))
        self.assertEqual(s["workOrders"], [{"workOrder": "T1", "events": 3, "latestRound": "fix1",
                                            "latestKind": "agent-run", "latestAt": "2026-11-03T10:00:00+00:00"}],
                         "T2 and T3 have a close; the session-start event has no work order")
        self.assertEqual(s["waitingDecisions"], [{"id": "d20261101-aaaaaa", "category": "spend",
                                                  "question": "Run the second wave?",
                                                  "askedAt": "2026-11-01T09:00:00+00:00"}],
                         "the answered decision and the autonomous one are not waiting")
        self.assertEqual(s["closedSince"], [
            {"workOrder": "T2", "closedAt": "2026-11-02T10:00:00+00:00", "commit": "3333333", "overridden": False},
            {"workOrder": "T3", "closedAt": "2026-11-04T10:00:01+00:00", "commit": "5555555", "overridden": True}])

    def test_a_work_order_is_open_only_from_a_seal_with_no_close_after_it(self):
        def ev(ts, kind, task):
            return {"ts": ts, "role": "engine", "kind": kind, "task": task, "purpose": "p", "outcome": "done",
                    "commit": "6666666"}
        self.ops.write_bytes(chained(OPS + [
            ev("2026-11-05T10:00:00+00:00", "agent-run", "T4"),
            ev("2026-11-05T11:00:00+00:00", "gate-refused", "T4"),
            ev("2026-11-05T12:00:00+00:00", "sealed", "T5"),
            ev("2026-11-05T13:00:00+00:00", "work-order-closed", "T5"),
            ev("2026-11-05T14:00:00+00:00", "sealed", "T5"),
            ev("2026-11-05T15:00:00+00:00", "sealed", "T6"),
            ev("2026-11-05T16:00:00+00:00", "work-order-closed", "T6"),
            ev("2026-11-05T17:00:00+00:00", "agent-run", "T6")]))
        self.assertEqual([w["workOrder"] for w in self.as_json()["workOrders"]], ["T1", "T5"],
                         "T4 has events and no seal; T5 was sealed again after its close; T6's close came after "
                         "its seal")

    def test_since_keeps_the_closes_at_or_after_it(self):
        self.assertEqual([c["workOrder"] for c in self.as_json("--since", "2026-11-02T10:00:00+00:00")["closedSince"]],
                         ["T2", "T3"], "a close at the --since time counts")
        self.assertEqual([c["workOrder"] for c in self.as_json("--since", "2026-11-02T10:00:01+00:00")["closedSince"]],
                         ["T3"])
        self.assertEqual(self.as_json("--since", "2026-11-05T00:00:00+00:00")["closedSince"], [])
        self.assertEqual(len(self.as_json("--since", "2026-11-05")["workOrders"]), 1, "--since filters closes only")

    def test_a_bad_since_is_bad_usage(self):
        rc, out, err = self.status("--since", "last week")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("not an ISO time", err)

    def test_repo_reads_another_folder(self):
        self.assertEqual(self.as_json("--repo", str(self.repo), cwd=self.tmp)["project"], str(self.repo))

    def test_the_plain_lines_carry_the_same_content(self):
        rc, out, err = self.status("--since", "2026-11-03T00:00:00+00:00")
        self.assertEqual(rc, 0, out + err)
        self.assertIn(f"project: {self.repo}", out)
        self.assertIn("open work orders: 1\n  T1: 3 event(s), latest agent-run at 2026-11-03T10:00:00+00:00, round fix1",
                      out)
        self.assertIn("waiting decisions: 1\n  d20261101-aaaaaa [spend] asked 2026-11-01T09:00:00+00:00: "
                      "Run the second wave?", out)
        self.assertIn("closed since 2026-11-03T00:00:00+00:00: 1\n  T3: at 2026-11-04T10:00:01+00:00, commit 5555555 "
                      "(overridden)", out)

    def test_it_writes_nothing(self):
        before = {p: p.read_bytes() for p in self.records.rglob("*") if p.is_file()}
        self.as_json()
        self.status()
        self.assertEqual({p: p.read_bytes() for p in self.records.rglob("*") if p.is_file()}, before)
        self.assertEqual(self.git(self.repo, "status", "--porcelain").stdout, "")

    def test_no_ledgers_yet_is_an_empty_state(self):
        self.ops.unlink()
        self.decisions.unlink()
        s = self.as_json()
        self.assertEqual((s["workOrders"], s["waitingDecisions"], s["closedSince"]), ([], [], []))

    def test_no_team_prints_team_false_and_exits_3(self):
        bare = self.make_repo("bare", None)
        rc, out, err = self.status("--json", cwd=bare)
        self.assertEqual((rc, json.loads(out)), (3, {"format": 1, "team": False}), err)
        rc, out, err = self.status(cwd=self.tmp)  # not a git repository at all
        self.assertEqual(rc, 3, out + err)
        self.assertIn("no team here", out)

    def test_a_repo_that_is_not_a_folder_in_a_git_repo_is_bad_usage(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        for where in (self.tmp / "no" / "such", self.repo / ".team" / "binding.json", plain):
            with self.subTest(where=where):
                rc, out, err = self.status("--json", "--repo", str(where))
                self.assertEqual((rc, out), (2, ""), err)
                self.assertIn(f"--repo {where} is not a folder inside a git repository", err)

    def test_a_ledger_line_status_cannot_read_is_named_and_exits_1(self):
        self.ops.write_bytes(chained(OPS + [{"ts": "2026-11-03T10:00:00+00:00", "kind": "note", "task": ["a"]}]))
        rc, out, err = self.status("--json")
        self.assertEqual((rc, out), (1, ""), err)
        self.assertIn(f"the ledger {self.ops} cannot be read", err)
        self.assertNotIn("Traceback", err)
        self.ops.write_bytes(chained(OPS))
        with self.decisions.open("a") as f:
            f.write('{"kind": "ask", "question": "q"}\n')
        rc, out, err = self.status("--json")
        self.assertEqual((rc, out), (1, ""), err)
        self.assertIn(f"the ledger {self.decisions} cannot be read", err)
        self.assertNotIn("Traceback", err)

    def test_setup_needed_gives_the_locators_message_and_exits_4(self):
        self.git(self.repo, "config", "--unset", "bakehouse.records")
        rc, out, err = self.status("--json")
        self.assertEqual((rc, out), (4, ""), err)
        self.assertIn("run `bakehouse setup`", err)

    def test_a_broken_ops_ledger_is_named_and_exits_1(self):
        with self.ops.open("ab") as f:
            f.write(b"not json\n")
        rc, out, err = self.status("--json")
        self.assertEqual((rc, out), (1, ""), err)
        self.assertIn(f"the ledger {self.ops} is BROKEN", err)
        self.assertIn(f"line {len(OPS) + 1}", err)

    def test_an_edited_chained_line_breaks_the_ops_ledger(self):
        lines = self.ops.read_bytes().split(b"\n")
        lines[4] = lines[4].replace(b"4444444", b"4444445")
        self.ops.write_bytes(b"\n".join(lines))
        rc, out, err = self.status("--json")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("its prev does not match line 5", err)

    def test_a_broken_decision_ledger_is_named_and_exits_1(self):
        with self.decisions.open("a") as f:
            f.write("{torn\n")
        rc, out, err = self.status("--json")
        self.assertEqual((rc, out), (1, ""), err)
        self.assertIn(f"the ledger {self.decisions} is BROKEN: line {len(DECISIONS) + 1} is not a JSON object", err)

    # --- unattended mode (R1) ---
    START = {"kind": "unattended", "action": "start", "ts": "2026-11-05T20:00:00+00:00", "stream": "proj",
             "why": "owner said unattended", "wave": "w1"}
    ASK = {**DECISIONS[0], "id": "d20261105-dddddd", "ts": "2026-11-05T20:10:00+00:00", "question": "Ship the cache?",
           "confidence": 0.8}
    ASSUMED = {"kind": "answer", "id": "d20261105-dddddd", "ts": "2026-11-05T20:20:00+00:00", "choice": "yes",
               "agreed": True, "notes": None, "assumed": True, "estTokens": 50000}

    def away(self, *events, enabled=True):
        (self.repo / ".team" / "decision-config.json").write_text(json.dumps({"unattended": {"enabled": enabled}}))
        self.decisions.write_text("".join(json.dumps(e) + "\n" for e in DECISIONS + list(events)))

    def test_r1_status_shows_unattended_mode_and_the_assumed_answers_to_confirm(self):
        self.away(self.START, self.ASK, self.ASSUMED)
        s = self.as_json()
        self.assertEqual(s["unattended"], {"on": True, "since": "2026-11-05T20:00:00+00:00",
                                           "why": "owner said unattended", "wave": "w1"})
        self.assertEqual(s["assumedToConfirm"], [{"id": "d20261105-dddddd", "question": "Ship the cache?",
                                                  "confidence": 0.8, "estTokens": 50000}])
        self.assertEqual([d["id"] for d in s["waitingDecisions"]], ["d20261101-aaaaaa"])
        rc, out, err = self.status()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("unattended mode: on since 2026-11-05T20:00:00+00:00 (owner said unattended), wave w1", out)
        self.assertIn("assumed, to confirm: 1\n  d20261105-dddddd confidence 0.8, estimate 50,000 tokens: "
                      "Ship the cache?", out)

    def test_r1_unattended_mode_is_off_by_default_and_after_a_stop_and_an_owners_answer_confirms(self):
        s = self.as_json()
        self.assertEqual((s["unattended"], s["assumedToConfirm"]),
                         ({"on": False, "since": None, "why": None, "wave": None}, []))
        rc, out, err = self.status()
        self.assertIn("unattended mode: off\nassumed, to confirm: 0", out)
        self.away(self.START, enabled=False)
        self.assertFalse(self.as_json()["unattended"]["on"], "the project has not turned the mode on")
        stop = {"kind": "unattended", "action": "stop", "ts": "2026-11-06T08:00:00+00:00", "stream": "proj"}
        owner = {"kind": "answer", "id": "d20261105-dddddd", "ts": "2026-11-06T08:10:00+00:00", "choice": "yes",
                 "agreed": True, "notes": None}
        self.away(self.START, self.ASK, self.ASSUMED, stop, owner)
        s = self.as_json()
        self.assertEqual((s["unattended"]["on"], s["assumedToConfirm"]), (False, []))

    def test_r1_fix1_a_bad_unattended_block_is_named_not_blamed_on_the_ledger(self):
        self.away(self.START)
        for block in (None, {"enabled": True, "bands": None}, {"enabled": True, "neverCategories": "spend"}):
            with self.subTest(block=block):
                (self.repo / ".team" / "decision-config.json").write_text(json.dumps({"unattended": block}))
                for args in (("--json",), ()):
                    rc, out, err = self.status(*args)
                    self.assertEqual((rc, out), (1, ""), err)
                    self.assertIn(".team/decision-config.json", err)
                    self.assertNotIn("ledger", err)
                    self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
