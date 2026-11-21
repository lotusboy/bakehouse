"""Tests for 0.12.0: in records format 2, decision.sh and spend.sh commit the writer file they appended to, by path,
under this writer's ops ledger lock, then run the records sync (docs/PROJECT-TEAM.md, "Records sync"). Format 1
commits nothing. Every remote is a local bare repository. Standard library only.
python3 -m unittest runtime/tests/test_records_self_commit.py
"""

import concurrent.futures
import fcntl
import json
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
import test_records_sync as trs  # noqa: E402 - a module, so its tests are not collected here again
import ops_ledger  # noqa: E402 - runtime/, on the path through test_records_sync

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
        (self.records / "staged.txt").write_text("s\n")
        self.g(self.records, "add", "--", "staged.txt")  # staged by someone else
        before = self.rgit("rev-parse", "HEAD")
        self.ask(self.repo)
        self.assertEqual(self.count(self.records, before), 1)
        self.assertEqual(self.names(self.records), ["decisions/ledger/alpha.jsonl"])
        dirty = [x.strip() for x in self.rgit("status", "--porcelain").splitlines()]
        self.assertIn(f"M {ops}", dirty)
        self.assertIn("?? notes/", dirty)
        self.assertIn("A  staged.txt", self.rgit("status", "--porcelain").splitlines())  # still staged
        self.assertEqual(self.g(self.records, "diff", "--cached", "--name-only"), "staged.txt")
        self.assertNotIn("staged.txt", self.g(self.records, "ls-tree", "-r", "--name-only", "HEAD").split())


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

    def test_a_second_ask_mid_merge_on_a_file_head_holds_says_not_committed_and_unstages(self):
        """The file is in HEAD already, but not this line: a failed commit is not taken for one already made."""
        self.origin_setup()
        self.sync(self.repo)
        self.ask(self.repo, "First?")
        self.g(self.records, "checkout", "-q", "-b", "side")
        self.local_commit(self.records, "notes/side.txt", "s\n")
        self.g(self.records, "checkout", "-q", "bakehouse-records")
        self.g(self.records, "merge", "-q", "--no-commit", "--no-ff", "side")
        before = self.rgit("rev-parse", "HEAD")
        p = self.ask(self.repo, "Second?")
        self.assertEqual(p.returncode, 0, p.stderr)
        lines = [x for x in p.stderr.splitlines() if x.startswith("bakehouse")]
        self.assertEqual(len(lines), 1, p.stderr)
        self.assertIn("not committed", lines[0])
        self.assertEqual(self.rgit("rev-parse", "HEAD"), before)
        self.assertEqual(len(self.rows(self.wfile("decisions_ledger"))), 2)
        staged = self.g(self.records, "diff", "--cached", "--name-only").split()
        self.assertNotIn("decisions/ledger/alpha.jsonl", staged)


class MoreLines(Base):
    """An outcome line and a spend.sh wave open line each commit themselves."""

    def test_an_outcome_line_commits_itself(self):
        self.origin_setup()
        self.sync(self.repo)
        did = self.ask(self.repo).stdout.strip()
        before = self.rgit("rev-parse", "HEAD")
        p = self.tool("decision.sh", "outcome", "--repo", self.repo, did, "--verdict", "held", "--by", "the run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.count(self.records, before), 1)
        self.assertEqual(self.subject(self.records), f"decisions: outcome {did} (decision.sh)")
        self.assertEqual(self.tip(), self.rgit("rev-parse", "HEAD"))

    def test_a_wave_open_line_commits_itself(self):
        self.origin_setup()
        self.sync(self.repo)
        before = self.rgit("rev-parse", "HEAD")
        p = self.tool("spend.sh", "wave", "open", "w1", "--ceiling", "10", "--repo", self.repo)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.count(self.records, before), 1)
        self.assertEqual(self.names(self.records), ["spend/ledger/alpha.jsonl"])
        self.assertEqual(self.subject(self.records), "spend: wave-open w1 (spend.sh)")
        self.assertEqual(self.tip(), self.rgit("rev-parse", "HEAD"))


class AtOnce(Base):
    """M1: asks at once in one records worktree. A later commit that finds its line already committed by an earlier
    one says nothing and syncs."""

    def test_asks_at_once_say_no_not_committed_and_every_line_is_in_head_and_on_the_hub(self):
        self.origin_setup()
        self.sync(self.repo)
        for rnd in range(2):
            with concurrent.futures.ThreadPoolExecutor(5) as ex:
                ps = list(ex.map(lambda i: self.ask(self.repo, f"Q{rnd}-{i}?", ok=False), range(5)))
            for p in ps:
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertNotIn("not committed", p.stderr)
        rel = self.wfile("decisions_ledger").relative_to(self.records).as_posix()
        self.assertEqual(len(self.g(self.records, "show", f"HEAD:{rel}").splitlines()), 10)
        self.assertEqual(self.rgit("status", "--porcelain", "--", rel), "")
        # a sync skipped while another held the sync lock leaves its commit for the next fixed point: this ask
        self.ask(self.repo, "Last?")
        self.assertEqual(self.tip(), self.rgit("rev-parse", "HEAD"))
        self.assertEqual(len(self.g(self.origin, "show", f"bakehouse-records:{rel}").splitlines()), 11)


class Signing(Base):
    """L1: commit_own's commit runs without signing, as the sync's own local steps do."""

    def test_an_ask_is_committed_with_gpgsign_on_and_a_failing_signer(self):
        self.origin_setup()
        self.sync(self.repo)
        self.g(self.records, "config", "commit.gpgsign", "true")
        self.g(self.records, "config", "gpg.program", "false")
        before = self.rgit("rev-parse", "HEAD")
        p = self.ask(self.repo)
        self.assertNotIn("not committed", p.stderr)
        self.assertEqual(self.count(self.records, before), 1)
        self.assertEqual(self.names(self.records), ["decisions/ledger/alpha.jsonl"])


class Locks(Base):
    """The writer lock is held around the commit; the decisions lock is free while the sync runs."""

    def test_the_commit_waits_for_the_writer_lock(self):
        self.origin_setup()
        self.sync(self.repo)
        before = self.rgit("rev-parse", "HEAD")
        lock = ops_ledger.lock_path(self.wfile("ops_ledger"))
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            p = subprocess.Popen([str(self.rt / "decision.sh"), "ask", "--repo", str(self.repo), "--question",
                                  "Run it?", *ASK], cwd=self.repo, env=self.env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + 20
            while not self.rows(self.wfile("decisions_ledger")) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertEqual(len(self.rows(self.wfile("decisions_ledger"))), 1)  # the line is written
            time.sleep(1.5)
            self.assertEqual(self.rgit("rev-parse", "HEAD"), before)  # and not committed while the lock is held
            self.assertIsNone(p.poll())
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 0, err)
        self.assertEqual(self.count(self.records, before), 1)
        self.assertEqual(self.names(self.records), ["decisions/ledger/alpha.jsonl"])

    def test_the_decisions_lock_is_free_while_the_sync_runs(self):
        self.origin_setup()
        self.sync(self.repo)
        seen = self.repo.parent / "decisions-lock.txt"
        lock = self.records / "decisions" / ".ledger.jsonl.lock"
        hooks = Path(self.g(self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "pre-push").write_text(  # the sync's push: try the decisions lock without waiting
            "#!/bin/sh\n"
            f"{sys.executable} -c 'import fcntl, sys\n"
            "f = open(sys.argv[1], \"a\")\n"
            "try:\n    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); r = \"free\"\n"
            "except OSError:\n    r = \"held\"\n"
            f"open(sys.argv[2], \"w\").write(r)' '{lock}' '{seen}'\n")
        (hooks / "pre-push").chmod(0o755)
        self.ask(self.repo)
        self.assertEqual(seen.read_text(), "free")
        self.assertEqual(self.tip(), self.rgit("rev-parse", "HEAD"))


if __name__ == "__main__":
    unittest.main()
