"""Tests for 0.12.0: in records format 2, decision.sh and spend.sh commit the writer file they appended to, by path,
under this writer's ops ledger lock, then run the records sync (docs/PROJECT-TEAM.md, "Records sync"). Format 1
commits nothing. Every remote is a local bare repository. Standard library only.
python3 -m unittest runtime/tests/test_records_self_commit.py
"""

import json
import re
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
import test_records_sync as trs  # noqa: E402 - a module, so its tests are not collected here again

ASK = ("--category", "spend", "--option", "yes", "--option", "no", "--recommendation", "yes",
       "--confidence", "0.9", "--evidence-kind", "measured", "--evidence", "WIP.md")
DID = re.compile(r"d\d{8}-[0-9a-f]{6}")


class Base(trs.Base):
    def ask(self, repo, question="Run it?", ok=True):
        p = self.tool("decision.sh", "ask", "--repo", repo, "--question", question, *ASK)
        if ok:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def count(self, rec, since):
        return int(self.g(rec, "rev-list", "--count", f"{since}..HEAD"))

    def names(self, rec, ref="HEAD"):
        return self.g(rec, "log", "-1", "--format=", "--name-only", ref).split()

    def subject(self, rec):
        return self.g(rec, "log", "-1", "--format=%s")

    def settled(self):
        """Both machines synced once, so the first sync's one-off commit (the register untracked) is behind us."""
        self.sync(self.b)
        self.sync(self.repo)
        self.sync(self.b)


for _n in [n for n in dir(trs.Base) if n.startswith("test")]:
    setattr(Base, _n, None)


class Ask(Base):
    """Test 1: an ask on B is one commit of B's decisions file, pushed; A's next sync lists it as waiting."""

    def test_an_ask_on_b_is_committed_by_path_pushed_and_waiting_on_a_after_its_sync(self):
        self.two()
        self.settled()
        before = self.g(self.brec, "rev-parse", "HEAD")
        p = self.ask(self.b)
        did = p.stdout.strip()
        self.assertRegex(did, DID)
        self.assertNotIn("bakehouse sync", p.stderr)  # level: says nothing
        self.assertEqual(self.count(self.brec, before), 1)
        self.assertEqual(self.names(self.brec), ["decisions/ledger/beta.jsonl"])
        self.assertEqual(self.subject(self.brec), f"decisions: ask {did} (decision.sh)")
        self.assertEqual(self.tip(), self.g(self.brec, "rev-parse", "HEAD"))
        self.assertEqual(self.status_of(self.repo)["waitingDecisions"], [])
        self.sync(self.repo)
        self.assertEqual([d["id"] for d in self.status_of(self.repo)["waitingDecisions"]], [did])


class AnswerAndSpend(Base):
    """Test 2: an answer on A reaches B at its sync; a spend record on B reaches A at its sync."""

    def test_a_answers_bs_ask_and_b_sees_the_answer_after_its_sync(self):
        self.two()
        self.settled()
        did = self.ask(self.b).stdout.strip()
        self.sync(self.repo)
        before = self.rgit("rev-parse", "HEAD")
        p = self.tool("decision.sh", "answer", "--repo", self.repo, did, "--choice", "yes", "--agreed", "yes")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.count(self.records, before), 1)
        self.assertEqual(self.names(self.records), ["decisions/ledger/alpha.jsonl"])
        self.assertEqual(self.subject(self.records), f"decisions: answer {did} (decision.sh)")
        self.assertEqual(self.tip(), self.rgit("rev-parse", "HEAD"))
        self.assertEqual(len(self.status_of(self.b)["waitingDecisions"]), 1)
        self.sync(self.b)
        self.assertEqual(self.status_of(self.b)["waitingDecisions"], [])

    def test_a_spend_record_on_b_reaches_a_after_its_sync(self):
        self.two()
        self.settled()
        before = self.g(self.brec, "rev-parse", "HEAD")
        p = self.tool("spend.sh", "record", "--repo", self.b, "--agent", "a-1", "--label", "one run",
                      "--tokens", "1000", "--kind", "no-role", "--why", "it fits", "--predicted", "it works",
                      "--predicted-tokens", "1000", "--result", "it worked", "--outcome", "as-predicted")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("bakehouse sync", p.stderr)
        self.assertEqual(self.count(self.brec, before), 1)
        self.assertEqual(self.names(self.brec), ["spend/ledger/beta.jsonl"])
        self.assertEqual(self.subject(self.brec), "spend: agent a-1 (spend.sh)")
        self.assertEqual(self.tip(), self.g(self.brec, "rev-parse", "HEAD"))
        self.sync(self.repo)
        p = self.tool("spend.sh", "report", "--json", "--repo", self.repo)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout)["agents"], 1)


class NoCommit(Base):
    """Tests 3 and 4: format 1 commits nothing; a refused ask commits nothing."""

    def test_an_ask_in_format_1_commits_nothing(self):
        before = self.rgit("rev-parse", "HEAD")
        p = self.ask(self.repo)
        self.assertRegex(p.stdout.strip(), DID)
        self.assertEqual(self.rgit("rev-parse", "HEAD"), before)
        self.assertIn("?? decisions/", self.rgit("status", "--porcelain"))
        self.assertNotIn("bakehouse", p.stderr)

    def test_a_refused_ask_commits_nothing(self):
        self.origin_setup()
        self.sync(self.repo)
        before = self.rgit("rev-parse", "HEAD")
        p = self.tool("decision.sh", "ask", "--repo", self.repo, "--question", "Run it?", "--category", "spend",
                      "--option", "yes", "--option", "no", "--recommendation", "yes", "--confidence", "2",
                      "--evidence-kind", "measured", "--evidence", "WIP.md")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(self.rgit("rev-parse", "HEAD"), before)
        self.assertFalse(self.wfile("decisions_ledger").exists())
        kinds = [e["kind"] for e in self.rows(self.wfile("ops_ledger"))]
        self.assertEqual(kinds, ["gate-refused"])  # in ops, left to the anchors
        self.assertIn("ops/", self.rgit("status", "--porcelain"))


class OnlyItsFile(Base):
    """Test 5: other dirty files in the records worktree are not committed."""

    def test_an_untracked_file_and_a_modified_ops_file_stay_out_of_the_commit(self):
        self.origin_setup()
        self.sync(self.repo)
        self.assertEqual(self.note("first").returncode, 0)
        ops = self.wfile("ops_ledger").relative_to(self.records).as_posix()
        self.g(self.records, "add", "--", ops)
        self.g(self.records, "commit", "-q", "-m", "ops: a note", "--", ops)
        self.assertEqual(self.note("second").returncode, 0)  # the ops file, modified
        (self.records / "notes").mkdir()
        (self.records / "notes" / "loose.txt").write_text("x\n")  # untracked
        before = self.rgit("rev-parse", "HEAD")
        self.ask(self.repo)
        self.assertEqual(self.count(self.records, before), 1)
        self.assertEqual(self.names(self.records), ["decisions/ledger/alpha.jsonl"])
        dirty = [x.strip() for x in self.rgit("status", "--porcelain").splitlines()]
        self.assertIn(f"M {ops}", dirty)
        self.assertIn("?? notes/", dirty)
        self.assertEqual(self.g(self.records, "diff", "--cached", "--name-only"), "")


class Offline(Base):
    """Test 6: the hub unreachable: exit 0, the same stdout, one `bakehouse sync: offline` line, the commit local;
    the next sync pushes it."""

    def test_an_ask_offline_commits_locally_and_the_next_sync_pushes_it(self):
        self.origin_setup()
        self.sync(self.repo)
        online = self.ask(self.repo, "First?")
        away = self.origin.with_name("away.git")
        self.origin.rename(away)
        before = self.rgit("rev-parse", "HEAD")
        p = self.ask(self.repo, "Second?")
        did = p.stdout.strip()
        self.assertEqual(p.stdout.splitlines(), [did])
        self.assertEqual(len(online.stdout.splitlines()), 1)
        self.assertRegex(did, DID)
        lines = [x for x in p.stderr.splitlines() if x.startswith("bakehouse")]
        self.assertEqual(len(lines), 1, p.stderr)
        self.assertTrue(lines[0].startswith("bakehouse sync: offline"), lines[0])
        self.assertEqual(self.count(self.records, before), 1)
        self.assertEqual(self.subject(self.records), f"decisions: ask {did} (decision.sh)")
        self.assertEqual(self.status_of(self.repo)["sync"]["waiting"], 1)
        away.rename(self.origin)
        self.assertIn("level with origin/bakehouse-records", self.sync(self.repo))
        self.assertEqual(self.tip(), self.rgit("rev-parse", "HEAD"))


class CannotCommit(Base):
    """Test 7: the records worktree mid-merge: the ask's exit code is unchanged and one line says why."""

    def test_an_ask_mid_merge_exits_0_with_one_line_and_the_line_stays_in_the_file(self):
        self.origin_setup()
        self.sync(self.repo)
        self.g(self.records, "checkout", "-q", "-b", "side")
        self.local_commit(self.records, "notes/side.txt", "s\n")
        self.g(self.records, "checkout", "-q", "bakehouse-records")
        self.g(self.records, "merge", "-q", "--no-commit", "--no-ff", "side")
        before = self.rgit("rev-parse", "HEAD")
        p = self.ask(self.repo)
        did = p.stdout.strip()
        self.assertRegex(did, DID)
        lines = [x for x in p.stderr.splitlines() if x.startswith("bakehouse")]
        self.assertEqual(len(lines), 1, p.stderr)
        self.assertIn("not committed", lines[0])
        self.assertIn("decisions/ledger/alpha.jsonl", lines[0])
        self.assertEqual(self.rgit("rev-parse", "HEAD"), before)
        self.assertEqual([e["id"] for e in self.rows(self.wfile("decisions_ledger"))], [did])
        staged = self.g(self.records, "diff", "--cached", "--name-only").split()
        self.assertNotIn("decisions/ledger/alpha.jsonl", staged)  # unstaged again: it stays out of the merge commit


if __name__ == "__main__":
    unittest.main()
