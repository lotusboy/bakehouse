"""Tests for runtime/heavy-run.sh: the job always runs, and a lane event that cannot be written is said,
not hidden (S15: a clone with no records pointer lost all three events silently). Scratch repos only."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HEAVY = Path(__file__).resolve().parents[1] / "heavy-run.sh"


class HeavyRun(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.tmp), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": str(self.tmp / ".gitconfig"),
                    "BAKEHOUSE_MACHINE_DIR": str(self.tmp / "machine"),
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        self.repo = self.tmp / "proj"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q", "-b", "main")
        (self.repo / ".team").mkdir()
        (self.repo / ".team" / "binding.json").write_text(json.dumps({"owned": True}))
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-q", "-m", "first")

    def git(self, where, *args):
        subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True, env=self.env)

    def run_heavy(self, *cmd):
        return subprocess.run([str(HEAVY), "heavy", *cmd], cwd=self.repo, capture_output=True, text=True,
                              env=self.env)

    def test_no_records_pointer_still_runs_the_job_and_says_each_lost_event(self):
        p = self.run_heavy("sh", "-c", "echo ran; exit 7")
        self.assertEqual(p.returncode, 7)
        self.assertIn("ran", p.stdout)
        for kind in ("resource-waited", "resource-started", "resource-ended"):
            self.assertIn(f"the {kind} lane event was not written", p.stderr)
        self.assertIn("bakehouse setup --pointer", p.stderr)

    def test_with_records_no_warning_and_three_events(self):
        rec = self.tmp / "proj.bakehouse-records"
        self.git(self.repo, "worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(rec))
        self.git(self.repo, "config", "bakehouse.records", str(rec))
        p = self.run_heavy("true")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("was not written", p.stderr)
        lines = (rec / "ops" / "lanes.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(l)["kind"] for l in lines],
                         ["resource-waited", "resource-started", "resource-ended"])


if __name__ == "__main__":
    unittest.main()
