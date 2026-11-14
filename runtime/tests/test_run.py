"""Tests for `bakehouse run <script> [args]` (bin/bakehouse): the stable way to name a plugin script. Standard
library only. Each test builds a scratch git repo; nothing outside a temporary folder is read or written."""
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


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp),
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(self.tmp / ".gitconfig"),
                    "BAKEHOUSE_MACHINE_DIR": str(self.tmp / "machine"), **IDENT}
        self.repo = self.tmp / "proj"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.repo / ".team").mkdir()
        (self.repo / ".team" / "binding.json").write_text(json.dumps({"owned": True}))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "first")
        self.records = self.tmp / "proj.bakehouse-records"
        self.git("worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(self.records))
        self.git("config", "bakehouse.records", str(self.records))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, env=self.env,
                       check=True)

    def run_(self, *args, cwd=None):
        p = subprocess.run([str(BAKEHOUSE), "run", *args], cwd=cwd or self.repo, capture_output=True, text=True,
                           env=self.env)
        return p.returncode, p.stdout, p.stderr

    def test_runs_the_script_with_its_args(self):
        rc, out, err = self.run_("locator.py", "ops_ledger")
        self.assertEqual((rc, out.strip()), (0, str(self.records / "ops" / "events.jsonl")), err)

    def test_the_scripts_exit_code_is_the_commands(self):
        no_team = self.tmp / "plain"
        no_team.mkdir()
        subprocess.run(["git", "-C", str(no_team), "init", "-q"], env=self.env, check=True)
        self.assertEqual(self.run_("locator.py", "records", cwd=no_team)[0], 3)

    def test_a_shell_script_runs_too(self):
        rc, out, err = self.run_("ops-gap.sh", "5")
        self.assertEqual(rc, 1)
        self.assertIn("ops-gap: no ledger at", err)

    def test_a_python_script_without_the_execute_bit_runs(self):
        self.assertFalse(os.access(ROOT / "runtime" / "locator.py", os.X_OK))
        self.assertEqual(self.run_("locator.py", "root")[0], 0)

    def test_a_name_with_a_slash_is_refused(self):
        for name in ("tests/test_run.py", "../bin/bakehouse", "./locator.py", "/bin/sh"):
            rc, out, err = self.run_(name)
            self.assertEqual(rc, 2, name)
            self.assertIn("is not a script in", err)

    def test_a_name_that_is_not_a_file_there_is_refused(self):
        for name in ("no-such-script.sh", "tests", "__pycache__", "..", ""):
            rc, out, err = self.run_(name)
            self.assertEqual(rc, 2, name)
            self.assertIn("is not a script in", err)

    def test_no_script_is_bad_usage(self):
        rc, out, err = self.run_()
        self.assertEqual(rc, 2)
        self.assertIn("usage: bakehouse run <script> [args]", err)

    def test_the_top_usage_names_run(self):
        p = subprocess.run([str(BAKEHOUSE)], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 2)
        self.assertIn("setup|status|run", p.stderr)


if __name__ == "__main__":
    unittest.main()
