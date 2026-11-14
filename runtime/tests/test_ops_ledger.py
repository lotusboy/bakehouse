"""Tests for runtime/ops-event.sh and ops-gap.sh. Standard library only:
python3 -m unittest discover -s runtime/tests"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=dict(os.environ, **GIT))


class Fixture(unittest.TestCase):
    """Two scratch projects: each a git repo with .team/binding.json and a records worktree named by
    `git config bakehouse.records`. `owned` says "owned": true, `other` does not."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name).resolve()
        self.rt = t / "runtime"
        self.rt.mkdir()
        for s in ("locator.py", "ops-event.sh", "ops_ledger.py", "ops-gap.sh"):
            (self.rt / s).symlink_to(RUNTIME / s)
        self.owned, self.other = t / "owned", t / "other"
        self.records = {}
        for repo, owned in ((self.owned, True), (self.other, False)):
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            self.commit(repo, "first")
            (repo / ".team").mkdir()
            (repo / ".team" / "binding.json").write_text(json.dumps({"owned": owned}))
            rec = t / f"{repo.name}.team-records"
            git(repo, "worktree", "add", "-q", "--orphan", "-b", "team-records", str(rec))
            git(repo, "config", "bakehouse.records", str(rec))
            self.records[repo] = rec
        self.env = dict(os.environ, **GIT)
        self.env.pop("OPS_REPO", None)
        self.ledger = self.records[self.owned] / "ops" / "events.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self, repo, msg):
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", msg],
                       check=True, env=dict(os.environ, **GIT), capture_output=True)

    def run_in(self, cwd, script, *args, env=None):
        p = subprocess.run([str(self.rt / script), *args], cwd=cwd, capture_output=True,
                           text=True, env=env or self.env)
        return p.returncode, p.stdout + p.stderr


class OpsEvent(Fixture):
    def test_writes_to_the_projects_ledger_and_stamps_the_repos_head(self):
        sub = self.owned / "deep"
        sub.mkdir()
        rc, out = self.run_in(sub, "ops-event.sh", "decision", "T1", "pick a path", "-", "-", "-", "-", "-", "done", "why")
        self.assertEqual(rc, 0, out)
        (row,) = [json.loads(l) for l in self.ledger.read_text().splitlines()]
        head = subprocess.run(["git", "-C", str(self.owned), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual((row["kind"], row["task"], row["outcome"], row["note"], row["commit"]),
                         ("decision", "T1", "done", "why", head))
        self.assertFalse((self.owned / "docs").exists())

    def test_appends_and_never_rewrites(self):
        self.run_in(self.owned, "ops-event.sh", "note", "-", "one")
        self.run_in(self.owned, "ops-event.sh", "note", "-", "two")
        self.assertEqual([json.loads(l)["purpose"] for l in self.ledger.read_text().splitlines()], ["one", "two"])

    def test_refuses_a_repository_with_no_team(self):
        stray = Path(self.tmp.name) / "stray"
        subprocess.run(["git", "init", "-q", str(stray)], check=True)
        rc, out = self.run_in(stray, "ops-event.sh", "note", "-", "x")
        self.assertEqual(rc, 1)
        self.assertIn("has no team", out)


    def test_ops_repo_names_the_repository_when_run_from_outside_it(self):
        """M9: OPS_REPO, not the working directory, picks the repository."""
        rc, out = self.run_in(Path(self.tmp.name), "ops-event.sh", "note", "-", "x",
                              env=dict(self.env, OPS_REPO=str(self.owned)))
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.ledger.read_text().splitlines()), 1)

    def test_refuses_a_repository_whose_team_folder_is_gone(self):
        """M10: a repository with no .team/ folder keeps no ledger, though its records pointer stands."""
        import shutil
        shutil.rmtree(self.owned / ".team")
        rc, out = self.run_in(self.owned, "ops-event.sh", "note", "-", "x")
        self.assertEqual(rc, 1)
        self.assertIn("has no team", out)
        self.assertFalse(self.ledger.exists())

    def test_refuses_a_repository_whose_records_need_setup(self):
        """A binding with no records pointer: the locator's setup message, and no ledger anywhere."""
        git(self.owned, "config", "--unset", "bakehouse.records")
        rc, out = self.run_in(self.owned, "ops-event.sh", "note", "-", "x")
        self.assertEqual(rc, 1)
        self.assertIn("bakehouse setup", out)
        self.assertFalse(self.ledger.exists())

    def test_refuses_a_repository_the_owner_does_not_own(self):
        """Another party's repository (owned: false) never gets a ledger, even with a records folder."""
        rc, out = self.run_in(self.other, "ops-event.sh", "note", "-", "x")
        self.assertEqual(rc, 1)
        self.assertIn('does not say "owned": true', out)
        self.assertFalse((self.records[self.other] / "ops").exists())


class OpsGap(Fixture):
    def test_reads_the_projects_ledger(self):
        self.run_in(self.owned, "ops-event.sh", "note", "-", "x", env=dict(self.env, OPS_TS="2000-01-01T00:00:00+00:00"))
        rc, out = self.run_in(self.owned, "ops-gap.sh", "1")
        self.assertEqual(rc, 0, out)
        self.assertIn("ops ledger is 1 commit(s) behind — last event 2000-01-01", out)

    def test_quiet_line_under_the_threshold(self):
        self.run_in(self.owned, "ops-event.sh", "note", "-", "x")
        rc, out = self.run_in(self.owned, "ops-gap.sh")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"^ops ledger: [01] commit\(s\) behind \(last event ")


if __name__ == "__main__":
    unittest.main()
