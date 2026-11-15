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
        """The git hook setup writes, as literal expected text (not built by hook_stub itself)."""
        q = shlex.quote(str(self.launcher))
        return ("#!/bin/sh\n# installed-by: bakehouse\n"
                '[ "$(git symbolic-ref -q HEAD)" = refs/heads/bakehouse-records ] && exit 0\n'
                f'[ -x {q} ] || {{ echo "bakehouse: the git-hook launcher "{q}" is missing or not executable, so the '
                f'{name} hook cannot run. Run \\`bakehouse setup\\` to write it again." >&2; '
                f'exit {1 if name == "pre-commit" else 0}; }}\n'
                f"exec {q} {name}\n")

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

    def pushed(self):
        """The main clone set up, one records commit made, and both branches pushed to a bare origin."""
        self.setup_in(self.repo)
        rec = self.records(self.repo)
        (rec / "ops").mkdir()
        (rec / "ops" / "events.jsonl").write_text("{}\n")
        self.git(rec, "add", "-A")
        self.git(rec, "commit", "-q", "-m", "anchor")
        origin = self.tmp / "origin.git"
        self.git(self.tmp, "init", "-q", "--bare", str(origin))
        self.git(self.repo, "push", "-q", str(origin), "main", "bakehouse-records")
        return rec, origin

    def test_a_fresh_clone_checks_out_the_pushed_records_branch(self):
        # S16 M4: a clone of a remote, not of the main clone (that one is told to use --pointer)
        rec, origin = self.pushed()
        clone = self.tmp / "clone"
        self.git(self.tmp, "clone", "-q", "-b", "main", str(origin), str(clone))
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

    def test_a_single_branch_clone_fetches_the_records_branch_from_origin(self):
        rec, origin = self.pushed()
        clone = self.tmp / "clone"
        self.git(self.tmp, "clone", "-q", "--single-branch", "-b", "main", str(origin), str(clone))
        rc, out = self.setup_in(clone)
        self.assertEqual(rc, 0, out)
        self.assertIn("checked out origin/bakehouse-records", out)
        self.assertEqual(self.git(self.records(clone), "rev-parse", "HEAD").stdout, self.git(rec, "rev-parse", "HEAD").stdout)

    def test_a_remote_that_cannot_be_asked_refuses_unless_the_owner_asserts_it_holds_no_records_branch(self):
        # a remote that needs a login setup cannot give: its ssh command records that it was asked, then fails
        asked = self.tmp / "asked"
        self.git(self.repo, "remote", "add", "origin", "ssh://git@host.invalid/proj.git")
        self.env["GIT_SSH_COMMAND"] = f"touch {shlex.quote(str(asked))}; false"
        rc, out = self.setup_in(self.repo)
        self.assertEqual(rc, 1, out)
        self.assertIn("could not look for bakehouse-records on origin", out)
        self.assertTrue(asked.exists(), "without the option, setup asks the remote")
        self.assertFalse(os.path.lexists(self.records(self.repo)))
        self.assertEqual(self.config(self.repo, "bakehouse.records"), "")
        asked.unlink()
        rc, out = self.setup_in(self.repo, "--no-remote-check")
        self.assertEqual(rc, 0, out)
        self.assertFalse(asked.exists(), "with the option, setup never asks the remote")
        self.assertIn("origin was not asked", out)
        rec = self.records(self.repo)
        self.assertEqual(self.git(rec, "symbolic-ref", "--short", "HEAD").stdout.strip(), "bakehouse-records")
        self.assertEqual(self.git(rec, "log", "--format=%s").stdout.splitlines(),
                         ["bakehouse records: the branch's first commit"])
        self.assertNotEqual(self.git(self.repo, "merge-base", "main", "bakehouse-records", check=False).returncode, 0)
        self.assertEqual(self.config(self.repo, "bakehouse.records"), str(rec))

    def test_the_no_remote_check_option_is_explained_in_the_usage(self):
        rc, out = self.setup_in(self.repo, "--no-such-option")
        self.assertEqual(rc, 2)
        self.assertIn("--no-remote-check", out)
        self.assertIn("the owner asserts that origin has never held the\n", out)
        self.assertIn("bakehouse-records branch. setup then makes a new orphan bakehouse-records branch here without\n"
                      "                     asking origin", out)
        rc, out = self.setup_in(self.tmp, "--pointer", str(self.repo), "--no-remote-check")
        self.assertEqual(rc, 2, "a lane clone never asks the remote, so the option is bad usage there")

    def test_help_prints_every_option_and_exits_0_changing_nothing(self):
        rc, out = self.setup_in(self.repo, "--help", pre_commit=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("usage: bakehouse setup [--pointer <main clone>] [--pre-commit] [--no-remote-check]", out)
        for option in ("--pointer <main clone>", "--pre-commit", "--no-remote-check", "--help"):
            self.assertIn(f"\n  {option} ", out, f"{option} has no line of its own in the usage")
        self.assertEqual(self.config(self.repo, "bakehouse.records"), "")
        self.assertFalse(self.records(self.repo).exists())
        self.assertFalse(self.hook(self.repo, "post-commit").exists())

    def test_a_clone_of_the_main_clone_is_told_to_use_the_pointer(self):
        self.setup_in(self.repo)
        clone = self.tmp / "clone"
        self.git(self.tmp, "clone", "-q", str(self.repo), str(clone))
        rc, out = self.setup_in(clone)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"bakehouse setup --pointer {self.repo}", out)
        self.assertFalse(os.path.lexists(self.records(clone)))
        self.assertEqual(self.config(clone, "bakehouse.records"), "")
        self.assertNotEqual(self.git(clone, "show-ref", "--verify", "--quiet", "refs/heads/bakehouse-records",
                                     check=False).returncode, 0)

    def uncommitted_record(self, rec):
        (rec / "decisions").mkdir()
        (rec / "decisions" / "ledger.jsonl").write_text('{"kind": "ask", "id": "d1"}\n')

    def test_a_main_clone_moved_alone_is_reconnected_to_its_records(self):
        self.setup_in(self.repo)
        rec = self.records(self.repo)
        self.uncommitted_record(rec)
        moved = self.tmp / "moved"
        self.repo.rename(moved)
        self.assertRaises(locator.SetupNeeded, locator.find, moved, {})
        rc, out = self.setup_in(moved)
        self.assertEqual(rc, 0, out)
        self.assertIn("reconnected", out)
        self.assertEqual(locator.find(moved, env={}).records, rec)
        self.assertEqual(self.config(moved, "bakehouse.records"), str(rec))
        self.assertTrue((rec / "decisions" / "ledger.jsonl").is_file())

    def test_a_main_clone_moved_with_its_records_is_reconnected_never_moved_away(self):
        self.setup_in(self.repo)
        rec = self.records(self.repo)
        self.uncommitted_record(rec)
        there = self.tmp / "there"
        there.mkdir()
        self.repo.rename(there / "proj")
        rec.rename(there / rec.name)
        moved = there / "proj"
        rc, out = self.setup_in(moved)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("move it away", out)
        self.assertEqual(locator.find(moved, env={}).records, there / rec.name)
        self.assertTrue((there / rec.name / "decisions" / "ledger.jsonl").is_file())

    def assert_both_ways_out(self, rc, out, gone):
        """S16 fix2 P1-1: the branch is registered at a path that no longer exists: refused, naming both ways
        out, with nothing pruned and no replacement checkout."""
        self.assertEqual(rc, 1, out)
        self.assertIn(f"checked out at {gone}, which no longer exists", out)
        self.assertIn("`git worktree repair <new path>`", out)
        self.assertIn("`git worktree prune`, then `bakehouse setup`", out)
        self.assertNotIn("pruned", out)
        self.assertFalse(os.path.lexists(self.records(self.repo)))
        self.assertIn(f"worktree {gone}", self.git(self.repo, "worktree", "list", "--porcelain").stdout)

    def test_a_records_folder_that_is_gone_is_refused_then_checked_out_again_after_a_prune(self):
        self.setup_in(self.repo)
        rec = self.records(self.repo)
        head = self.git(rec, "rev-parse", "HEAD").stdout
        shutil.rmtree(rec)
        self.assert_both_ways_out(*self.setup_in(self.repo), rec)
        self.git(self.repo, "worktree", "prune")
        rc, out = self.setup_in(self.repo)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.git(rec, "rev-parse", "HEAD").stdout, head)
        self.assertEqual(locator.find(self.repo, env={}).records, rec)

    def test_a_records_folder_moved_alone_is_refused_then_reconnected_after_a_repair(self):
        self.setup_in(self.repo)
        rec = self.records(self.repo)
        self.uncommitted_record(rec)
        moved = self.tmp / "elsewhere" / rec.name
        moved.parent.mkdir()
        rec.rename(moved)
        self.assert_both_ways_out(*self.setup_in(self.repo), rec)
        self.git(self.repo, "worktree", "repair", str(moved))
        rc, out = self.setup_in(self.repo)
        self.assertEqual(rc, 0, out)
        self.assertEqual(locator.find(self.repo, env={}).records, moved)
        self.assertTrue((moved / "decisions" / "ledger.jsonl").is_file())

    def test_a_records_worktree_off_its_branch_is_told_to_switch_back_never_moved_away(self):
        # S16 fix2 P2-1
        self.setup_in(self.repo)
        rec = self.records(self.repo)
        self.git(rec, "commit", "-q", "--allow-empty", "-m", "second")
        self.uncommitted_record(rec)
        self.git(rec, "checkout", "-q", "--detach", "HEAD~1")
        rc, out = self.setup_in(self.repo)
        self.assertEqual(rc, 1, out)
        self.assertIn(f"`git -C {rec} switch bakehouse-records`", out)
        self.assertNotIn("move it away", out)
        self.assertTrue((rec / "decisions" / "ledger.jsonl").is_file())
        self.git(rec, "switch", "-q", "bakehouse-records")
        rc, out = self.setup_in(self.repo)
        self.assertEqual(rc, 0, out)
        self.assertEqual(locator.find(self.repo, env={}).records, rec)

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

    def test_a_second_run_without_the_flag_says_the_earlier_pre_commit_hook_is_left(self):
        self.setup_in(self.repo)
        rc, out = self.setup_in(self.repo, pre_commit=False)
        self.assertEqual(rc, 0, out)
        hook = self.hook(self.repo, "pre-commit")
        self.assertEqual(hook.read_text(), self.hook_text("pre-commit"))
        self.assertIn(f"pre-commit: {hook}, installed earlier, is left in place", out)
        self.assertNotIn("pre-commit: not installed", out)

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

    def test_a_sidecar_team_is_refused_and_nothing_changes(self):
        # S16 M8: R0 does not set up records for a repo another party owns (a known limit)
        theirs = self.make_repo("theirs", None)
        side = self.tmp / "side"
        side.mkdir()
        (side / "binding.json").write_text(json.dumps({"owned": False}))
        self.git(theirs, "config", "bakehouse.sidecar", str(side))
        exclude = theirs / ".git" / "info" / "exclude"
        exclude.write_text("# git ls-files --others --exclude-from=.git/info/exclude\n*.tmp")
        for args in ((), ("--pointer", str(self.repo))):
            with self.subTest(args=args):
                rc, out = self.setup_in(theirs, *args)
                self.assertEqual(rc, 1, out)
                self.assertIn("bakehouse R0 does not set up records for a repo another party owns (a known limit); "
                              "nothing was changed", out)
        self.assertEqual(self.git(theirs, "branch", "--list").stdout.split(), ["*", "main"])
        self.assertFalse(os.path.lexists(self.records(theirs)))
        self.assertEqual(self.config(theirs, "bakehouse.records"), "")
        self.assertEqual(exclude.read_text().splitlines()[1:], ["*.tmp"])
        self.assertFalse(self.hook(theirs, "pre-commit").exists())

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

    def moved_main_clone(self):
        """The main clone and its records moved together (a renamed workspace), and repaired by setup there."""
        there = self.tmp / "there"
        there.mkdir()
        rec = self.records(self.repo)
        self.repo.rename(there / self.repo.name)
        rec.rename(there / rec.name)
        rc, out = self.setup_in(there / self.repo.name)
        self.assertEqual(rc, 0, out)
        return there / self.repo.name

    def assert_told_to_use_the_pointer(self, lane, before):
        rc, out = self.setup_in(lane)
        self.assertEqual(rc, 1, out)
        self.assertIn("bakehouse setup --pointer <main clone>", out)
        self.assertFalse(os.path.lexists(self.records(lane)))
        self.assertEqual(self.config(lane, "bakehouse.records"), before)
        self.assertNotEqual(self.git(lane, "show-ref", "--verify", "--quiet", "refs/heads/bakehouse-records",
                                     check=False).returncode, 0)

    def test_plain_setup_in_a_lane_whose_main_clone_moved_is_told_to_use_the_pointer(self):
        # S16 fix2 P1-2: a pointer that is not this clone's own records means a lane: no second records home
        self.setup_in(self.repo)
        lane = self.lane()
        self.setup_in(lane, "--pointer", str(self.repo))
        before = self.config(lane, "bakehouse.records")
        self.moved_main_clone()
        self.assertRaises(locator.SetupNeeded, locator.find, lane, {})
        self.assert_told_to_use_the_pointer(lane, before)

    def test_plain_setup_in_a_clone_whose_local_origin_is_gone_is_told_to_use_the_pointer(self):
        self.setup_in(self.repo)
        lane = self.lane()
        self.moved_main_clone()
        self.assert_told_to_use_the_pointer(lane, "")

    def test_pointer_refuses_a_main_clone_with_no_records(self):
        lane = self.lane()
        rc, out = self.setup_in(lane, "--pointer", str(self.repo))
        self.assertNotEqual(rc, 0)
        self.assertIn(f"{self.repo} has no usable records", out)
        self.assertEqual(self.config(lane, "bakehouse.records"), "")
        self.assertFalse(self.hook(lane, "pre-commit").exists())

    def test_pointer_refuses_a_main_clone_of_another_project(self):
        other = self.make_repo("other", {"owned": True, "name": "other"})
        self.setup_in(other)
        lane = self.lane()
        rc, out = self.setup_in(lane, "--pointer", str(other))
        self.assertNotEqual(rc, 0)
        self.assertIn(f"{other} shares no root commit with {lane}", out)
        self.assertEqual(self.config(lane, "bakehouse.records"), "")
        self.assertFalse(self.hook(lane, "pre-commit").exists())

    def test_pointer_accepts_a_shallow_lane_whose_head_the_main_clone_holds(self):
        # S16 fix2 P2-3: a shallow clone's root is its cut-off commit, so its HEAD is looked up instead
        for i in range(3):
            self.git(self.repo, "commit", "-q", "--allow-empty", "-m", f"c{i}")
        self.setup_in(self.repo)
        lane = self.tmp / "shallow"
        self.git(self.tmp, "clone", "-q", "--depth", "1", f"file://{self.repo}", str(lane))
        rc, out = self.setup_in(lane, "--pointer", str(self.repo))
        self.assertEqual(rc, 0, out)
        self.assertEqual(locator.find(lane, env={}).records, self.records(self.repo))
        # a shallow clone of another project is still refused
        other = self.make_repo("other", {"owned": True, "name": "other"})
        self.setup_in(other)
        rc, out = self.setup_in(lane, "--pointer", str(other))
        self.assertEqual(rc, 1, out)
        self.assertIn(f"{other} does not hold {lane}'s HEAD commit", out)

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

    def test_a_missing_launcher_is_named_by_the_hook(self):
        self.setup_in(self.repo)
        self.launcher.unlink()
        rc, out = self.commit(self.repo, "--allow-empty", "-m", "x")
        self.assertNotEqual(rc, 0)
        self.assertIn(f"bakehouse: the git-hook launcher {self.launcher} is missing or not executable", out)
        self.assertIn("Run `bakehouse setup`", out)
        p = subprocess.run([str(self.hook(self.repo, "post-commit"))], cwd=self.repo, capture_output=True, text=True,
                           env=self.env)
        self.assertEqual(p.returncode, 0)
        self.assertIn("Run `bakehouse setup`", p.stderr)

    def records_commit(self):
        rec = self.records(self.repo)
        (rec / "ops").mkdir(exist_ok=True)
        (rec / "ops" / "events.jsonl").write_text("{}\n")
        self.git(rec, "add", "-A")
        return self.commit(rec, "-m", "anchor")

    def test_a_records_commit_runs_without_the_launcher(self):
        # S16 fix2 P2-5: the stub knows the records worktree by its branch, before it checks the launcher
        self.setup_in(self.repo)
        self.launcher.unlink()
        rc, out = self.records_commit()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("launcher", out)

    def test_the_launcher_knows_the_records_worktree_by_its_branch(self):
        # S16 fix2 P2-5: not by comparing its path with the pointer, which may be stale or unset
        self.setup_in(self.repo)
        bakehouse_setup.write_launcher(self.fake_root(), env=self.env)
        self.git(self.repo, "config", "--unset", "bakehouse.records")
        rc, out = self.records_commit()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.marked(), [])

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
        # run in the project, as the harness runs the hook in the session's folder: the locator asks the
        # repo git runs in first (R0.1 R2-8)
        p = subprocess.run(["python3", str(SESSION_START)], capture_output=True, text=True, cwd=str(project),
                           env={**self.env, "CLAUDE_PROJECT_DIR": str(project), **extra})
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def role(self, text):
        self.machine.mkdir(exist_ok=True)
        (self.machine / "role").write_text(text)

    def test_retargets_the_launcher_to_the_plugin_root(self):
        bakehouse_setup.write_launcher("/old/root", env=self.env)
        new = self.tmp / "new root"
        (new / "runtime").mkdir(parents=True)
        (new / "runtime" / "predict-gate.sh").write_text("#!/bin/sh\n")
        self.run_hook(self.tmp, CLAUDE_PLUGIN_ROOT=str(new))
        self.assertEqual(bakehouse_setup.launcher_target(self.launcher), str(new))
        text = self.launcher.read_text()
        bakehouse_setup.write_launcher(new, env=self.env)
        self.assertEqual(self.launcher.read_text(), text)

    def test_never_retargets_to_a_root_without_the_scripts(self):
        bakehouse_setup.write_launcher(ROOT, env=self.env)
        self.run_hook(self.tmp, CLAUDE_PLUGIN_ROOT=str(self.tmp / "elsewhere" / "bakehouse-v0"))
        self.assertEqual(bakehouse_setup.launcher_target(self.launcher), str(ROOT))

    def test_never_creates_the_launcher_without_a_project_with_bakehouse_hooks(self):
        self.run_hook(self.tmp, CLAUDE_PLUGIN_ROOT=str(ROOT))
        self.assertFalse(self.launcher.exists())
        # nor in a project whose git hooks are not bakehouse's
        self.run_hook(self.make_repo("plain", None), CLAUDE_PLUGIN_ROOT=str(ROOT))
        self.assertFalse(self.launcher.exists())

    def test_recreates_a_missing_launcher_for_a_project_with_bakehouse_hooks(self):
        self.setup_in(self.repo)
        self.launcher.unlink()
        self.run_hook(self.repo / ".team", CLAUDE_PLUGIN_ROOT=str(ROOT))
        self.assertEqual(bakehouse_setup.launcher_target(self.launcher), str(ROOT))
        self.assertTrue(os.access(self.launcher, os.X_OK))

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

    def compact(self, process_cwd, data, **extra):
        """The hook after a compaction: the process cwd is wherever the shell last was; data is the hook input."""
        p = subprocess.run(["python3", str(SESSION_START)], input=json.dumps(data), capture_output=True, text=True,
                           cwd=str(process_cwd), env={**self.env, **extra})
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_after_a_compaction_the_project_is_judged_not_the_process_cwd(self):
        # S2 fix 1: the folder is $CLAUDE_PROJECT_DIR, else the hook input's cwd, else the process cwd
        self.setup_in(self.repo)
        other = self.make_repo("plain", None)
        data = {"hook_event_name": "SessionStart", "source": "compact", "cwd": str(other)}
        self.assertIn("load the `bakehouse:pm` skill", self.compact(other, data, CLAUDE_PROJECT_DIR=str(self.repo)))
        self.assertIn("load the `bakehouse:pm` skill", self.compact(other, dict(data, cwd=str(self.repo))))
        self.assertEqual(self.compact(self.repo, data), "")

    def test_after_a_compaction_a_missing_launcher_is_made_for_the_project(self):
        self.setup_in(self.repo)
        self.launcher.unlink()
        other = self.make_repo("plain", None)
        data = {"hook_event_name": "SessionStart", "source": "compact", "cwd": str(other)}
        self.compact(other, data, CLAUDE_PROJECT_DIR=str(self.repo), CLAUDE_PLUGIN_ROOT=str(ROOT))
        self.assertEqual(bakehouse_setup.launcher_target(self.launcher), str(ROOT))

    def test_a_broken_machine_folder_still_exits_zero(self):
        self.setup_in(self.repo)
        shutil.rmtree(self.machine)
        self.machine.write_text("not a folder")
        out = self.run_hook(self.repo, CLAUDE_PLUGIN_ROOT=str(ROOT))
        self.assertIn("load the `bakehouse:pm` skill", out)


class BrokenImport(Base):
    """A hook whose own module cannot be imported fails open, as its docstring says (S16 M1)."""

    def broken_copy(self, script):
        """script beside a locator.py that raises on import."""
        d = self.tmp / "broken"
        d.mkdir()
        for name in (script, "bakehouse_setup.py", "team_roles.py"):
            shutil.copy(RUNTIME / name, d / name)
        (d / "locator.py").write_text("raise TypeError('unsupported operand')\n")
        return d / script

    def test_the_dispatch_hook_logs_the_failure_and_lets_the_dispatch_through(self):
        d = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(self.tmp),
             "tool_input": {"subagent_type": "role:nosuchrole", "description": "d", "prompt": "x"}}
        p = subprocess.run(["python3", str(self.broken_copy("agent-roles.py"))], input=json.dumps(d),
                           capture_output=True, text=True, env=self.env)
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))
        self.assertIn("ERROR import locator: TypeError('unsupported operand')",
                      (self.machine / "logs" / "agent-roles.log").read_text())

    def test_session_start_says_so_and_exits_zero(self):
        p = subprocess.run(["python3", str(self.broken_copy("session-start.py"))], capture_output=True, text=True,
                           env=self.env)
        self.assertEqual((p.returncode, p.stdout), (0, ""))
        self.assertIn("bakehouse: session-start failed: TypeError('unsupported operand')", p.stderr)


OLD_PYTHON = Path("/usr/bin/python3")


def old_python() -> bool:
    """True when /usr/bin/python3 exists and is older than 3.10 (macOS ships 3.9)."""
    if not OLD_PYTHON.is_file():
        return False
    p = subprocess.run([str(OLD_PYTHON), "-c", "import sys; print(sys.version_info < (3, 10))"],
                       capture_output=True, text=True)
    return p.stdout.strip() == "True"


@unittest.skipUnless(old_python(), "needs /usr/bin/python3 older than 3.10")
class OldPython(Base):
    """The hooks run bare `python3`, which may be 3.9: every module they import must load there (S16 M1)."""
    HOOK_MODULES = ("agent-roles.py", "team_roles.py", "locator.py", "session-start.py", "bakehouse_setup.py",
                    "gate_event.py", "predict-gate.py", "records-check.py", "team-preflight.py", "decision.py",
                    "ops_ledger.py")

    def old(self, *args, input=None, cwd=None, **extra):
        return subprocess.run([str(OLD_PYTHON), *args], capture_output=True, text=True, input=input, cwd=cwd,
                              env={**self.env, "PYTHONDONTWRITEBYTECODE": "1", **extra})

    def test_every_module_the_hooks_import_loads(self):
        code = ("import importlib.util, sys; sys.path.insert(0, sys.argv[1]); "
                "s = importlib.util.spec_from_file_location('m', sys.argv[2]); "
                "m = sys.modules['m'] = importlib.util.module_from_spec(s); s.loader.exec_module(m)")
        for name in self.HOOK_MODULES:
            with self.subTest(name=name):
                p = self.old("-c", code, str(RUNTIME), str(RUNTIME / name))
                self.assertEqual(p.returncode, 0, p.stderr)

    def test_the_dispatch_hook_still_denies_an_unknown_role(self):
        d = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(self.tmp),
             "tool_input": {"subagent_type": "role:nosuchrole", "description": "d", "prompt": "x"}}
        p = self.old(str(RUNTIME / "agent-roles.py"), input=json.dumps(d))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_session_start_still_prints_the_pm_instruction(self):
        self.setup_in(self.repo)
        p = self.old(str(SESSION_START), cwd=str(self.repo), CLAUDE_PROJECT_DIR=str(self.repo))
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertIn("load the `bakehouse:pm` skill", p.stdout)


if __name__ == "__main__":
    unittest.main()
