"""Tests for the run folders `bakehouse ground` reads from git in tree mode (runtime/bakehouse_ground.py): a run
folder on a branch, a remote branch or HEAD that the working tree lacks. Standard library only. Each test builds
scratch projects; nothing outside a temporary folder is read or written."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from test_status import BAKEHOUSE, IDENT

TREE = {"tracker": {"id": "^A[0-9]+[a-z]?$"}, "records": {"codeRoots": ["src"]}, "runs": "tree", "reviews": "notes/done"}
RECORDS = {"tracker": {"id": "^BD[0-9]+$"}, "signal": {"codeRoots": ["packages"]}}
LINE = "run folders read: "


def finding(fid, severity, site=None):
    return "\n".join([f"## {fid} ({severity}): what", "What it does.", *([f"Site: {site}"] if site else []), ""])


class GroundGitRuns(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        (self.tmp / ".gitconfig").write_text("[maintenance]\n\tauto = false\n[gc]\n\tauto = 0\n")
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp),
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(self.tmp / ".gitconfig"),
                    "BAKEHOUSE_MACHINE_DIR": str(self.tmp / "machine"), **IDENT}
        self.repo = self.project("proj", TREE)
        self.commit("A1: x", "src/a.py", "src/b.py")
        self.git(self.repo, "branch", "old")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def git(self, where, *args):
        return subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, check=True,
                              env=self.env).stdout

    def project(self, name, binding):
        repo = self.tmp / name
        (repo / ".team").mkdir(parents=True)
        (repo / ".team" / "binding.json").write_text(json.dumps(binding))
        self.git(repo, "init", "-q", "-b", "main")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "first")
        records = self.tmp / f"{name}.bakehouse-records"
        self.git(repo, "worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(records))
        self.git(repo, "config", "bakehouse.records", str(records))
        (records / "ops").mkdir()
        (records / "ops" / "events.jsonl").touch()
        return repo

    def commit(self, subject, *files, repo=None):
        repo = repo or self.repo
        for f in files:
            (repo / f).parent.mkdir(parents=True, exist_ok=True)
            with (repo / f).open("a") as out:
                out.write(subject + "\n")
        self.git(repo, "add", "--", *files)
        self.git(repo, "commit", "-q", "-m", subject)

    def review(self, work_order, *found, base=None, name="{date}-{id}-review", commit=None):
        """Write one findings file, and commit it when asked; the folder it is in."""
        day = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        run = (base or self.repo / "notes" / "done") / name.format(date=day, id=work_order)
        run.mkdir(parents=True, exist_ok=True)
        (run / "findings.md").write_text("# Findings\n\n" + "\n".join(found))
        if commit:
            where = base.parent if base else self.repo
            self.git(where, "add", "-A")
            self.git(where, "commit", "-q", "-m", commit)
        return run

    def on_branch_only(self, branch, work_order, *found):
        """A run folder committed on `branch` (made from main), and not in the working tree, which is on main."""
        self.git(self.repo, "switch", "-q", "-c", branch, "main")
        self.review(work_order, *found, commit=f"{work_order} review")
        self.git(self.repo, "switch", "-q", "main")

    def run_ground(self, *args, cwd=None):
        p = subprocess.run([str(BAKEHOUSE), "ground", *args], cwd=cwd or self.repo, capture_output=True, text=True,
                           env=self.env)
        return p.returncode, p.stdout, p.stderr

    def as_json(self, *args, cwd=None):
        rc, out, err = self.run_ground("--json", *args, cwd=cwd)
        self.assertEqual((rc, err), (0, ""), out)
        return json.loads(out)

    def found(self, s, n=0):
        f = s["paths"][n].get("findings")
        return f and (f["count"], f["foundBy"])

    def test_a_run_folder_only_on_main_is_read(self):
        self.review("A3", finding("F1", "P1", "src/a.py:4"), commit="A3 review")
        self.git(self.repo, "switch", "-q", "old")
        self.assertFalse((self.repo / "notes").exists())
        s = self.as_json("src/a.py")
        self.assertEqual(self.found(s), (1, ["A3"]), "the finding is on main, not in this working tree")
        self.assertEqual(s["runFolders"], {"read": 1, "onlyInGit": 1})
        self.assertEqual(self.run_ground("src/a.py")[1].splitlines()[-1],
                         LINE + "1 (1 only in git, not in this working tree)")

    def test_the_working_tree_copy_wins_and_is_read_once(self):
        run = self.review("A3", finding("F1", "P1", "src/a.py:4"), commit="A3 review")
        self.git(self.repo, "switch", "-q", "old")
        self.review("A3", finding("F1", "P1", "src/b.py:4"))
        self.assertTrue(run.is_dir())
        s = self.as_json("src/a.py", "src/b.py")
        self.assertEqual((self.found(s, 0), self.found(s, 1)), (None, (1, ["A3"])))
        self.assertEqual(s["runFolders"], {"read": 1, "onlyInGit": 0})
        self.assertNotIn(LINE, self.run_ground("src/b.py")[1], "no line when nothing came only from git")

    def test_a_remote_branch_is_read_and_the_records_branch_is_not(self):
        self.on_branch_only("lane", "A4", finding("F1", "P1", "src/a.py:4"))
        self.git(self.repo, "update-ref", "refs/remotes/origin/lane", "lane")
        self.git(self.repo, "branch", "-q", "-D", "lane")
        records = self.tmp / "proj.bakehouse-records"
        self.review("A5", finding("F1", "P1", "src/a.py:4"), base=records / "notes" / "done", commit="A5 review")
        self.git(self.repo, "update-ref", "refs/remotes/origin/bakehouse-records", "bakehouse-records")
        s = self.as_json("src/a.py")
        self.assertEqual(self.found(s), (1, ["A4"]), "A5 is on the records branch only")
        self.assertEqual(s["runFolders"], {"read": 1, "onlyInGit": 1})

    def test_a_folder_on_three_refs_counts_once(self):
        self.on_branch_only("one", "A6", finding("F1", "P1", "src/a.py:4"), finding("F2", "P0"))
        self.git(self.repo, "branch", "two", "one")
        self.git(self.repo, "update-ref", "refs/remotes/origin/three", "one")
        self.commit("on main", "docs/x.md")
        self.on_branch_only("four", "A6", finding("F1", "P1", "src/a.py:4"), finding("F2", "P0"))
        self.assertNotEqual(self.git(self.repo, "rev-parse", "one"), self.git(self.repo, "rev-parse", "four"))
        s = self.as_json("src/a.py")
        self.assertEqual((self.found(s), s.get("findingsUnread")), ((1, ["A6"]), 1))
        self.assertEqual(s["runFolders"], {"read": 1, "onlyInGit": 1})

    def test_a_ref_that_cannot_be_read(self):
        self.on_branch_only("side", "A8", finding("F1", "P1", "src/b.py:4"))
        tree = self.git(self.repo, "rev-parse", "side:notes/done").strip()
        (self.repo / ".git" / "objects" / tree[:2] / tree[2:]).unlink()
        rc, out, err = self.run_ground("src/b.py")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(err.splitlines()), 1, err)
        self.assertIn("refs/heads/side", err)
        self.assertTrue(out.startswith("only the code roots count: src\nsrc/b.py\n  work orders, last 14 days: 1 (A1)"),
                        out)
        self.assertNotIn("serious review findings", out)

    def test_records_mode_output_is_unchanged(self):
        other = self.project("web", RECORDS)
        self.commit("BD7 card", "packages/card.ts", repo=other)
        self.review("BD31", finding("F1", "P1", "packages/card.ts:9"),
                    base=self.tmp / "web.bakehouse-records" / "runs", name="{date}-x-{id}")
        self.review("BD32", finding("F1", "P1", "packages/card.ts:9"), base=other / "notes" / "done", commit="BD32")
        s = self.as_json("packages/card.ts", cwd=other)
        self.assertEqual(sorted(s), ["codeRoots", "days", "format", "noId", "not", "paths", "project", "team"])
        self.assertEqual(self.found(s), (1, ["BD31"]), "records mode reads the records worktree only")
        rc, out, err = self.run_ground("packages/card.ts", cwd=other)
        self.assertEqual((rc, err, out.splitlines()[-1]),
                         (0, "", "  serious review findings, ever: 1 (found by BD31)"))


if __name__ == "__main__":
    unittest.main()
