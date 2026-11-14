"""Tests for bin/bakehouse setup, the git-hook launcher and runtime/session-start.py. Standard library only.
Each test builds scratch git repos and a scratch machine folder; nothing outside a temporary folder is read
or written."""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
ROOT = RUNTIME.parent
BAKEHOUSE = ROOT / "bin" / "bakehouse"
SESSION_START = RUNTIME / "session-start.py"
sys.path.insert(0, str(RUNTIME))
import bakehouse_setup  # noqa: E402
import locator  # noqa: E402

IDENT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
BASE = {"gaps:unfaithful-logic": 9, "rules:fired": 120}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.machine = self.tmp / "machine"
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp),
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(self.tmp / ".gitconfig"),
                    "BAKEHOUSE_MACHINE_DIR": str(self.machine), **IDENT}
        self.repo = self.make_repo("proj", {"owned": True})
        self.launcher = self.machine / "git-hook"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def git(self, where, *args, check=True):
        return subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, env=self.env,
                              check=check)

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

    def setup_in(self, cwd, *args, pre_commit=True):
        """setup, with the opt-in pre-commit hook unless pre_commit=False."""
        args = (*args, "--pre-commit") if pre_commit else args
        p = subprocess.run([str(BAKEHOUSE), "setup", *args], cwd=cwd, capture_output=True, text=True, env=self.env)
        return p.returncode, p.stdout + p.stderr

    def config(self, where, key):
        return self.git(where, "config", "--get", key, check=False).stdout.strip()

    def hook(self, repo, name):
        return Path(self.git(repo, "rev-parse", "--path-format=absolute", "--git-path", "hooks").stdout.strip()) / name

    def hook_text(self, name):
        return f"#!/bin/sh\n# installed-by: bakehouse\nexec {shlex.quote(str(self.launcher))} {name}\n"

    def records(self, repo):
        return repo.parent / f"{repo.name}.bakehouse-records"

    def commit(self, where, *args, env=None):
        p = subprocess.run(["git", "-C", str(where), "commit", *args], capture_output=True, text=True,
                           env=env or self.env)
        return p.returncode, p.stdout + p.stderr


class Setup(Base):
    def test_a_fresh_bound_repo_gets_the_records_worktree_the_pointer_and_both_hooks(self):
        rc, out = self.setup_in(self.repo)
        self.assertEqual(rc, 0, out)
        rec = self.records(self.repo)
        self.assertEqual(self.git(rec, "symbolic-ref", "--short", "HEAD").stdout.strip(), "bakehouse-records")
        self.assertEqual(self.git(rec, "log", "--format=%s").stdout.splitlines(),
                         ["bakehouse records: the branch's first commit"])
        self.assertEqual(self.git(rec, "ls-tree", "--name-only", "HEAD").stdout.split(), [".gitignore"])
        # the branch shares no history with main
        self.assertNotEqual(self.git(self.repo, "merge-base", "main", "bakehouse-records", check=False).returncode, 0)
        self.assertEqual(self.config(self.repo, "bakehouse.records"), str(rec))
        self.assertEqual(locator.find(self.repo, env={}).records, rec)
        for name in ("pre-commit", "post-commit"):
            self.assertEqual(self.hook(self.repo, name).read_text(), self.hook_text(name))
            self.assertTrue(os.access(self.hook(self.repo, name), os.X_OK))
        self.assertEqual(bakehouse_setup.launcher_target(self.launcher), str(ROOT))
        self.assertTrue(os.access(self.launcher, os.X_OK))
        # the lock files and the decision view are never committed (R2-17)
        (rec / "DECISIONS.md").write_text("# view\n")
        (rec / "ops").mkdir()
        (rec / "ops" / ".events.jsonl.lock").write_text("")
        (rec / "decisions").mkdir()
        (rec / "decisions" / ".ledger.jsonl.lock").write_text("")
        self.assertEqual(self.git(rec, "status", "--porcelain").stdout, "")

    def test_a_second_run_changes_nothing_and_says_so(self):
        self.setup_in(self.repo)
        rec = self.records(self.repo)

        def state():
            return (self.hook(self.repo, "pre-commit").read_text(), self.hook(self.repo, "post-commit").read_text(),
                    self.launcher.read_text(), self.git(rec, "rev-parse", "HEAD").stdout,
                    self.git(self.repo, "worktree", "list").stdout, self.config(self.repo, "bakehouse.records"),
                    sorted(p.name for p in self.tmp.iterdir()))

        before = state()
        rc, out = self.setup_in(self.repo)
        self.assertEqual(rc, 0, out)
        self.assertIn(f"already set up: records at {rec}", out)
        self.assertEqual(state(), before)

    def test_setup_from_a_sub_folder_sets_up_the_repo(self):
        (self.repo / "deep").mkdir()
        rc, out = self.setup_in(self.repo / "deep")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.config(self.repo, "bakehouse.records"), str(self.records(self.repo)))

    def test_no_team_refuses(self):
        plain = self.make_repo("plain", None)
        rc, out = self.setup_in(plain)
        self.assertNotEqual(rc, 0)
        self.assertIn(f"{plain} has no team", out)
        self.assertFalse(self.records(plain).exists())
        self.assertEqual(self.config(plain, "bakehouse.records"), "")
        self.assertFalse(self.hook(plain, "pre-commit").exists())

    def test_a_sibling_folder_that_is_not_the_checkout_refuses_naming_it(self):
        folder = self.records(self.repo)
        folder.mkdir()
        (folder / "mine.txt").write_text("x")
        rc, out = self.setup_in(self.repo)
        self.assertNotEqual(rc, 0)
        self.assertIn(f"{folder} exists and is not this repo's bakehouse-records checkout. Refused", out)
        self.assertEqual(self.config(self.repo, "bakehouse.records"), "")
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["mine.txt"])

    def test_a_fresh_clone_checks_out_the_pushed_records_branch(self):
        self.setup_in(self.repo)
        rec = self.records(self.repo)
        (rec / "ops").mkdir()
        (rec / "ops" / "events.jsonl").write_text("{}\n")
        self.git(rec, "add", "-A")
        self.git(rec, "commit", "-q", "-m", "anchor")
        clone = self.tmp / "clone"
        self.git(self.tmp, "clone", "-q", str(self.repo), str(clone))
        rc, out = self.setup_in(clone)
        self.assertEqual(rc, 0, out)
        self.assertIn("checked out origin/bakehouse-records", out)
        crec = self.records(clone)
        self.assertEqual(self.git(crec, "rev-parse", "HEAD").stdout, self.git(rec, "rev-parse", "HEAD").stdout)
        self.assertEqual(self.git(crec, "symbolic-ref", "--short", "HEAD").stdout.strip(), "bakehouse-records")
        self.assertEqual(self.git(crec, "rev-parse", "--abbrev-ref", "@{upstream}").stdout.strip(),
                         "origin/bakehouse-records")
        self.assertEqual((crec / "ops" / "events.jsonl").read_text(), "{}\n")
        self.assertEqual(self.config(clone, "bakehouse.records"), str(crec))

    def test_a_local_records_branch_is_checked_out_not_made_again(self):
        self.setup_in(self.repo)
        rec = self.records(self.repo)
        head = self.git(rec, "rev-parse", "HEAD").stdout
        self.git(self.repo, "worktree", "remove", str(rec))
        self.git(self.repo, "config", "--unset", "bakehouse.records")
        rc, out = self.setup_in(self.repo)
        self.assertEqual(rc, 0, out)
        self.assertIn("checked out the branch bakehouse-records", out)
        self.assertEqual(self.git(rec, "rev-parse", "HEAD").stdout, head)

    def test_without_the_flag_the_pre_commit_hook_is_not_installed(self):
        rc, out = self.setup_in(self.repo, pre_commit=False)
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.lexists(self.hook(self.repo, "pre-commit")))
        self.assertEqual(self.hook(self.repo, "post-commit").read_text(), self.hook_text("post-commit"))
        self.assertIn("bakehouse setup --pre-commit", out)

    def test_a_lane_clone_without_the_flag_gets_no_pre_commit_hook(self):
        self.setup_in(self.repo)
        lane = self.tmp / "lane-np"
        self.git(self.tmp, "clone", "-q", str(self.repo), str(lane))
        rc, out = self.setup_in(lane, "--pointer", str(self.repo), pre_commit=False)
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.lexists(self.hook(lane, "pre-commit")))
        self.assertTrue(self.hook(lane, "post-commit").is_file())

    def test_an_unowned_binding_gets_the_pre_commit_hook_only(self):
        """Another party's commits are not the owner's to log (repo-hooks.sh's rule, kept)."""
        other = self.make_repo("other", {"owned": False})
        rc, out = self.setup_in(other)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.hook(other, "pre-commit").read_text(), self.hook_text("pre-commit"))
        self.assertFalse(self.hook(other, "post-commit").exists())
        self.assertIn('post-commit: not installed, because the binding does not say "owned": true', out)

    def test_the_post_commit_hook_reports_the_ledger_drift_after_a_commit(self):
        self.setup_in(self.repo)
        p = subprocess.run([str(RUNTIME / "ops-event.sh"), "note", "-", "x"], cwd=self.repo, capture_output=True,
                           text=True, env=dict(self.env, OPS_TS="2000-01-01T00:00:00+00:00"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rc, out = self.commit(self.repo, "--allow-empty", "-m", "second")
        self.assertEqual(rc, 0, out)
        self.assertIn("ops ledger: 2 commit(s) behind", out)

    def test_a_sidecar_team_excludes_the_local_settings_once(self):
        theirs = self.make_repo("theirs", None)
        side = self.tmp / "side"
        side.mkdir()
        (side / "binding.json").write_text(json.dumps({"owned": False}))
        self.git(theirs, "config", "bakehouse.sidecar", str(side))
        exclude = theirs / ".git" / "info" / "exclude"
        exclude.write_text("# git ls-files --others --exclude-from=.git/info/exclude\n*.tmp")
        for _ in range(2):
            rc, out = self.setup_in(theirs)
            self.assertEqual(rc, 0, out)
        self.assertEqual(exclude.read_text().splitlines()[1:], ["*.tmp", ".claude/settings.local.json"])
        self.assertEqual(locator.find(theirs, env={}).team, side)

    def test_the_usage_names_the_commands_the_locator_names(self):
        rc, out = self.setup_in(self.repo, "--pointer")
        self.assertEqual(rc, 2)
        self.assertIn("usage: bakehouse setup [--pointer <main clone>]", out)
        with self.assertRaises(locator.SetupNeeded) as e:
            locator.find(self.repo, env={})
        self.assertIn("`bakehouse setup` in the main clone, or `bakehouse setup --pointer <main clone>`",
                      str(e.exception))


class Pointer(Base):
    def lane(self):
        lane = self.tmp / "lane"
        self.git(self.tmp, "clone", "-q", str(self.repo), str(lane))
        return lane

    def test_pointer_writes_only_the_pointer_and_the_hooks(self):
        self.setup_in(self.repo)
        lane = self.lane()
        before = sorted(p.name for p in self.tmp.iterdir())
        rc, out = self.setup_in(lane, "--pointer", str(self.repo))
        self.assertEqual(rc, 0, out)
        rec = self.records(self.repo)
        self.assertEqual(self.config(lane, "bakehouse.records"), str(rec))
        self.assertEqual(locator.find(lane, env={}).records, rec)
        for name in ("pre-commit", "post-commit"):
            self.assertEqual(self.hook(lane, name).read_text(), self.hook_text(name))
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), before)
        self.assertEqual(len(self.git(lane, "worktree", "list").stdout.splitlines()), 1)
        self.assertNotEqual(self.git(lane, "show-ref", "--verify", "--quiet", "refs/heads/bakehouse-records",
                                     check=False).returncode, 0)

    def test_pointer_refuses_a_main_clone_with_no_records(self):
        lane = self.lane()
        rc, out = self.setup_in(lane, "--pointer", str(self.repo))
        self.assertNotEqual(rc, 0)
        self.assertIn(f"{self.repo} has no usable records", out)
        self.assertEqual(self.config(lane, "bakehouse.records"), "")
        self.assertFalse(self.hook(lane, "pre-commit").exists())

    def test_pointer_refuses_a_folder_with_no_team(self):
        lane = self.lane()
        plain = self.make_repo("plain", None)
        rc, out = self.setup_in(lane, "--pointer", str(plain))
        self.assertNotEqual(rc, 0)
        self.assertIn(f"{plain} is not a clone with a team", out)
        self.assertEqual(self.config(lane, "bakehouse.records"), "")


class Hooks(Base):
    def fake_root(self):
        """A plugin root whose two hook scripts only leave a mark."""
        root = self.tmp / "fake root"
        (root / "runtime").mkdir(parents=True)
        self.marks = self.tmp / "marks"
        for script, word in (("predict-gate.sh", "pre"), ("ops-gap.sh", "post")):
            (root / "runtime" / script).write_text(f"#!/bin/sh\necho {word} >> {shlex.quote(str(self.marks))}\n")
            (root / "runtime" / script).chmod(0o755)
        return root

    def marked(self):
        return self.marks.read_text().split() if self.marks.exists() else []

    def test_a_commit_in_the_records_worktree_runs_neither_hook_script(self):
        self.setup_in(self.repo)
        bakehouse_setup.write_launcher(self.fake_root(), env=self.env)
        rc, out = self.commit(self.repo, "--allow-empty", "-m", "code")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.marked(), ["pre", "post"])  # the hooks do run in the main clone
        self.marks.unlink()
        rec = self.records(self.repo)
        (rec / "ops").mkdir()
        (rec / "ops" / "events.jsonl").write_text("{}\n")
        self.git(rec, "add", "-A")
        rc, out = self.commit(rec, "-m", "anchor")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.marked(), [])

    def test_the_pre_commit_hook_refuses_a_commit_the_gate_refuses(self):
        self.setup_in(self.repo)
        run = self.repo / "runs" / "x"
        run.mkdir(parents=True)
        for name, data in (("moves.prediction.json", {"moves": []}), ("moves.baseline.json", BASE),
                           ("moves.measured.json", {**BASE, "gaps:unfaithful-logic": 11})):
            (run / name).write_text(json.dumps(data))
        self.git(self.repo, "add", "-A")
        rc, out = self.commit(self.repo, "-m", "x")
        self.assertNotEqual(rc, 0)
        self.assertIn("4. Moved, and nobody predicted it: 1.", out)

    def test_a_foreign_hook_is_left_alone_and_the_line_to_add_is_printed(self):
        hook = self.hook(self.repo, "pre-commit")
        hook.write_text("#!/bin/sh\necho mine\n")
        rc, out = self.setup_in(self.repo)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"{hook} exists and was not written by bakehouse. Left alone", out)
        self.assertIn(f"{shlex.quote(str(self.launcher))} pre-commit || exit 1", out)
        self.assertEqual(hook.read_text(), "#!/bin/sh\necho mine\n")
        self.assertEqual(self.hook(self.repo, "post-commit").read_text(), self.hook_text("post-commit"))

    def test_core_hooks_path_is_reported_and_nothing_is_installed(self):
        self.git(self.repo, "config", "core.hooksPath", ".githooks")
        rc, out = self.setup_in(self.repo)
        self.assertEqual(rc, 1, out)
        self.assertIn("sets core.hooksPath (.githooks), so git ignores its hooks folder", out)
        self.assertIn(f"to .githooks/pre-commit:  {shlex.quote(str(self.launcher))} pre-commit || exit 1", out)
        self.assertIn(f"to .githooks/post-commit:  {shlex.quote(str(self.launcher))} post-commit", out)
        self.assertFalse((self.repo / ".githooks").exists())
        git_hooks = self.repo / ".git" / "hooks"
        self.assertFalse((git_hooks / "pre-commit").exists())
        self.assertFalse((git_hooks / "post-commit").exists())
        self.assertEqual(self.config(self.repo, "bakehouse.records"), str(self.records(self.repo)))

    def test_a_symlinked_hook_is_replaced_not_written_through(self):
        target = self.tmp / "elsewhere-hook"
        target.write_text("#!/bin/sh\n# installed-by: bakehouse\nexit 0\n")
        before = target.read_text()
        hook = self.hook(self.repo, "pre-commit")
        hook.symlink_to(target)
        rc, out = self.setup_in(self.repo)
        self.assertEqual(rc, 0, out)
        self.assertFalse(hook.is_symlink())
        self.assertEqual(hook.read_text(), self.hook_text("pre-commit"))
        self.assertEqual(target.read_text(), before)

    def test_the_launcher_with_its_target_gone(self):
        self.setup_in(self.repo)
        bakehouse_setup.write_launcher(self.tmp / "gone", env=self.env)
        for name, code in (("pre-commit", 1), ("post-commit", 0)):
            p = subprocess.run([str(self.launcher), name], cwd=self.repo, capture_output=True, text=True,
                               env=self.env)
            self.assertEqual(p.returncode, code, name)
            self.assertIn("Run `bakehouse setup`", p.stderr)
        # through git: the missing gate stops the commit, as a missing script stops the hook today
        rc, out = self.commit(self.repo, "--allow-empty", "-m", "x")
        self.assertNotEqual(rc, 0)
        self.assertIn("the pre-commit gate cannot run. Run `bakehouse setup`", out)


class SessionStart(Base):
    def run_hook(self, project, **extra):
        p = subprocess.run(["python3", str(SESSION_START)], capture_output=True, text=True,
                           env={**self.env, "CLAUDE_PROJECT_DIR": str(project), **extra})
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def role(self, text):
        self.machine.mkdir(exist_ok=True)
        (self.machine / "role").write_text(text)

    def test_retargets_the_launcher_to_the_plugin_root(self):
        bakehouse_setup.write_launcher("/old/root", env=self.env)
        new = self.tmp / "new root"
        self.run_hook(self.tmp, CLAUDE_PLUGIN_ROOT=str(new))
        self.assertEqual(bakehouse_setup.launcher_target(self.launcher), str(new))
        text = self.launcher.read_text()
        bakehouse_setup.write_launcher(new, env=self.env)
        self.assertEqual(self.launcher.read_text(), text)

    def test_never_creates_the_launcher(self):
        self.run_hook(self.tmp, CLAUDE_PLUGIN_ROOT=str(ROOT))
        self.assertFalse(self.launcher.exists())

    def test_no_team_prints_nothing(self):
        self.assertEqual(self.run_hook(self.make_repo("plain", None)), "")
        self.assertEqual(self.run_hook(self.tmp), "")

    def test_setup_needed_prints_the_locator_message(self):
        out = self.run_hook(self.repo)
        self.assertEqual(len(out.splitlines()), 1, out)
        self.assertIn("`bakehouse setup` in the main clone, or `bakehouse setup --pointer <main clone>`", out)

    def test_pm_and_a_missing_role_file_print_the_instruction(self):
        self.setup_in(self.repo)
        want = ("bakehouse: this project has a team. Be its PM: load the `bakehouse:pm` skill now and follow it "
                "for the whole session.\n")
        self.assertEqual(self.run_hook(self.repo), want)
        self.role("pm\n")
        self.assertEqual(self.run_hook(self.repo), want)

    def test_none_prints_nothing(self):
        self.setup_in(self.repo)
        self.role("none\n")
        self.assertEqual(self.run_hook(self.repo), "")

    def test_other_role_content_is_read_as_pm_and_said(self):
        self.setup_in(self.repo)
        self.role("boss\n")
        lines = self.run_hook(self.repo).splitlines()
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("holds 'boss', not `pm` or `none`; treating it as `pm`", lines[0])
        self.assertIn("load the `bakehouse:pm` skill", lines[1])

    def test_a_broken_machine_folder_still_exits_zero(self):
        self.setup_in(self.repo)
        shutil.rmtree(self.machine)
        self.machine.write_text("not a folder")
        out = self.run_hook(self.repo, CLAUDE_PLUGIN_ROOT=str(ROOT))
        self.assertIn("load the `bakehouse:pm` skill", out)


if __name__ == "__main__":
    unittest.main()
