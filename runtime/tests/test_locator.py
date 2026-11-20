"""Tests for runtime/locator.py: finding the project, its team facts and its records. Standard library only.
Each test builds scratch git repos; nothing outside a temporary folder is read or written."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import locator  # noqa: E402

LOCATOR = Path(__file__).resolve().parents[1] / "locator.py"
ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_CONFIG_NOSYSTEM": "1"}


def git(where, *args):
    subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True, text=True,
                   env={**ENV, "HOME": str(where), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.repo = self.tmp / "proj"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "first")

    def bind(self):
        (self.repo / ".team").mkdir()
        (self.repo / ".team" / "binding.json").write_text("{}")

    def records(self):
        rec = self.tmp / "proj.bakehouse-records"
        git(self.repo, "worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(rec))
        git(self.repo, "config", "bakehouse.records", str(rec))
        return rec


class TestFind(Base):
    def test_no_repo_is_no_team(self):
        self.assertIsNone(locator.find(self.tmp, env={}))

    def test_repo_without_binding_is_no_team(self):
        self.assertIsNone(locator.find(self.repo, env={}))

    def test_binding_without_records_needs_setup_and_names_it(self):
        self.bind()
        with self.assertRaises(locator.SetupNeeded) as e:
            locator.find(self.repo, env={})
        self.assertIn("bakehouse setup", str(e.exception))

    def test_stale_records_pointer_needs_setup(self):
        self.bind()
        git(self.repo, "config", "bakehouse.records", str(self.tmp / "gone"))
        with self.assertRaises(locator.SetupNeeded):
            locator.find(self.repo, env={})

    def test_a_plain_folder_is_not_a_records_checkout(self):
        self.bind()
        (self.tmp / "plain").mkdir()
        git(self.repo, "config", "bakehouse.records", str(self.tmp / "plain" ))
        with self.assertRaises(locator.SetupNeeded):
            locator.find(self.repo, env={})

    def test_a_dangling_binding_link_is_still_a_team(self):
        (self.repo / ".team").mkdir()
        (self.repo / ".team" / "binding.json").symlink_to(self.tmp / "nowhere.json")
        rec = self.records()
        p = locator.find(self.repo, env={})
        self.assertEqual(p.binding, self.repo / ".team" / "binding.json")
        self.assertFalse(p.binding.is_file())

    def test_bound_repo_with_records(self):
        self.bind()
        rec = self.records()
        p = locator.find(self.repo, env={})
        self.assertEqual(p.root, self.repo)
        self.assertEqual(p.binding, self.repo / ".team" / "binding.json")
        self.assertEqual(p.ops_ledger, rec / "ops" / "events.jsonl")
        self.assertEqual(p.decisions_ledger, rec / "decisions" / "ledger.jsonl")
        self.assertEqual(p.health_register, rec / "registers" / "health.json")

    def test_a_subfolder_finds_the_same_project(self):
        self.bind()
        self.records()
        (self.repo / "a" / "b").mkdir(parents=True)
        self.assertEqual(locator.find(self.repo / "a" / "b", env={}).root, self.repo)

    def test_inside_the_records_worktree_is_no_team(self):
        self.bind()
        rec = self.records()
        self.assertIsNone(locator.find(rec, env={}))

    def test_a_records_folder_on_another_branch_needs_setup(self):
        self.bind()
        rec = self.tmp / "proj.other"
        git(self.repo, "worktree", "add", "-q", "--orphan", "-b", "other", str(rec))
        git(self.repo, "config", "bakehouse.records", str(rec))
        with self.assertRaises(locator.SetupNeeded) as e:
            locator.find(self.repo, env={})
        self.assertIn("bakehouse-records", str(e.exception))

    def test_a_records_worktree_off_its_branch_is_told_to_switch_back(self):
        # S16 fix2 P2-1: detached (a rebase, a checkout of an old commit) or on another branch
        self.bind()
        rec = self.records()
        git(rec, "commit", "-q", "--allow-empty", "-m", "first")
        git(rec, "commit", "-q", "--allow-empty", "-m", "second")
        for args in (("checkout", "-q", "--detach", "HEAD~1"), ("switch", "-q", "-c", "other")):
            git(rec, *args)
            with self.assertRaises(locator.SetupNeeded) as e:
                locator.find(self.repo, env={})
            self.assertIn(f"run `git -C {rec} switch bakehouse-records`", str(e.exception))
        # a pointer naming a main clone (no linked worktree) is not told to switch it to the records branch
        other = self.tmp / "other"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        git(other, "commit", "-q", "--allow-empty", "-m", "first")
        git(self.repo, "config", "bakehouse.records", str(other))
        with self.assertRaises(locator.SetupNeeded) as e:
            locator.find(self.repo, env={})
        self.assertNotIn("switch", str(e.exception))
        self.assertIn("is missing or is not a records checkout", str(e.exception))

    def test_a_records_folder_in_the_project_tree_needs_setup(self):
        self.bind()
        git(self.repo, "config", "bakehouse.records", str(self.repo))
        with self.assertRaises(locator.SetupNeeded) as e:
            locator.find(self.repo, env={})
        self.assertIn("is the project's own folder or inside it", str(e.exception))
        inner = self.repo / "rec"
        git(self.repo, "worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(inner))
        git(self.repo, "config", "bakehouse.records", str(inner))
        with self.assertRaises(locator.SetupNeeded):
            locator.find(self.repo, env={})

    def test_the_records_worktree_is_known_by_its_branch_not_by_the_pointer(self):
        # a sidecar team: the records worktree shares the repo's config, so it sees the sidecar binding too
        side = self.tmp / "sidecar"
        side.mkdir()
        (side / "binding.json").write_text("{}")
        git(self.repo, "config", "bakehouse.sidecar", str(side))
        rec = self.records()
        lane = self.tmp / "lane"
        git(self.tmp, "clone", "-q", str(self.repo), str(lane))
        lane_rec = self.tmp / "lane.bakehouse-records"
        git(lane, "worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(lane_rec))
        git(self.repo, "config", "bakehouse.records", str(lane_rec))
        self.assertEqual(locator.find(self.repo, env={}).records, lane_rec)
        self.assertIsNone(locator.find(rec, env={}))

    def test_a_clone_with_a_binding_on_a_branch_named_like_the_records_is_still_a_team(self):
        # S16 fix2 P2-6: "inside the records worktree" needs the branch and no .team/binding.json at the top
        self.bind()
        rec = self.records()
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "bind")
        lane = self.tmp / "lane"
        git(self.tmp, "clone", "-q", str(self.repo), str(lane))
        git(lane, "config", "bakehouse.records", str(rec))
        git(lane, "checkout", "-q", "-b", "bakehouse-records")
        self.assertEqual(locator.find(lane, env={}).records, rec)
        self.assertIsNone(locator.find(rec, env={}))

    def test_a_relative_pointer_is_read_from_the_repo_root(self):
        self.bind()
        rec = self.records()
        git(self.repo, "config", "bakehouse.records", "../proj.bakehouse-records")
        (self.repo / "sub").mkdir()
        for where in (self.repo, self.repo / "sub"):
            with self.subTest(where=where):
                self.assertEqual(locator.find(where, env={}).records, rec)

    def test_claude_project_dir_is_used_when_the_folder_is_in_no_repo(self):
        self.bind()
        self.records()
        other = self.tmp / "other"
        other.mkdir()
        p = locator.find(other, env={"CLAUDE_PROJECT_DIR": str(self.repo)})
        self.assertEqual(p.root, self.repo)

    def test_the_repo_git_runs_in_wins_over_claude_project_dir(self):
        # R0.1 R2-8: the folder's repo (else the current folder's) comes first; CLAUDE_PROJECT_DIR only when it
        # is in no git repo
        self.bind()
        self.records()
        other = self.tmp / "other"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        env = {"CLAUDE_PROJECT_DIR": str(self.repo)}
        self.assertIsNone(locator.find(other, env=env))
        here = os.getcwd()
        os.chdir(other)
        try:
            self.assertIsNone(locator.find(None, env=env))
        finally:
            os.chdir(here)
        r = subprocess.run([sys.executable, str(LOCATOR), "records", str(other)], capture_output=True, text=True,
                           env={**ENV, **env})
        self.assertEqual(r.returncode, locator.NO_TEAM)

    def test_a_lane_clone_points_at_the_main_clones_records(self):
        self.bind()
        git(self.repo, "add", ".team")
        git(self.repo, "commit", "-q", "-m", "bind")
        rec = self.records()
        lane = self.tmp / "lane1"
        git(self.tmp, "clone", "-q", str(self.repo), str(lane))
        self.assertRaises(locator.SetupNeeded, locator.find, lane, {})
        git(lane, "config", "bakehouse.records", str(rec))
        p = locator.find(lane, env={})
        self.assertEqual((p.root, p.records), (lane, rec))

    def test_a_sidecar_binding_for_a_repo_another_party_owns(self):
        side = self.tmp / "sidecar"
        side.mkdir()
        (side / "binding.json").write_text("{}")
        git(self.repo, "config", "bakehouse.sidecar", str(side))
        rec = self.records()
        p = locator.find(self.repo, env={})
        self.assertEqual((p.team, p.records), (side, rec))

    def test_a_symlinked_path_finds_the_same_project(self):
        self.bind()
        rec = self.records()
        link = self.tmp / "link"
        link.symlink_to(self.repo)
        self.assertEqual(locator.find(link, env={}).records, rec)


class TestRecordsAreAWorktreeOfTheRightRepository(Base):
    """Backlog 293: the records folder is a worktree of the project's repository, or, for a lane clone, of its main
    clone. A separate clone on the records branch is refused: records committed there never reach the project."""

    def setUp(self):
        super().setUp()
        self.bind()
        git(self.repo, "add", ".team")
        git(self.repo, "commit", "-q", "-m", "bind")
        self.rec = self.records()
        git(self.rec, "commit", "-q", "--allow-empty", "-m", "records")

    def separate_clone(self, name):
        sep = self.tmp / name
        git(self.tmp, "clone", "-q", "-b", "bakehouse-records", str(self.repo), str(sep))
        return sep

    def lane(self):
        # the real lane shape: a full clone made by `git clone <main clone path>`, its origin that local path, its
        # own .team/binding.json in its tree, and its pointer naming the main clone's records worktree
        lane = self.tmp / "lane1"
        git(self.tmp, "clone", "-q", str(self.repo), str(lane))
        git(lane, "config", "bakehouse.records", str(self.rec))
        return lane

    def test_a_separate_clone_on_the_records_branch_is_refused_naming_the_cause(self):
        sep = self.separate_clone("proj.sep")
        git(self.repo, "config", "bakehouse.records", str(sep))
        with self.assertRaises(locator.SetupNeeded) as e:
            locator.find(self.repo, env={})
        self.assertIn(f"the records folder {sep} is a separate clone", str(e.exception))
        self.assertIn("not a worktree", str(e.exception))
        self.assertIn("`bakehouse setup` in the main clone", str(e.exception))
        self.assertIn("`bakehouse setup --pointer <main clone>` in a lane clone", str(e.exception))

    def test_the_real_lane_clone_shape_finds_the_main_clones_records(self):
        lane = self.lane()
        self.assertTrue((lane / ".git").is_dir())
        self.assertEqual(subprocess.run(["git", "-C", str(lane), "config", "remote.origin.url"], capture_output=True,
                                        text=True).stdout.strip(), str(self.repo))
        self.assertTrue((lane / ".team" / "binding.json").is_file())
        p = locator.find(lane, env={})
        self.assertEqual((p.root, p.team, p.records), (lane, lane / ".team", self.rec))

    def test_a_linked_worktree_of_the_main_clone_used_as_a_lane_finds_the_records(self):
        wt = self.tmp / "proj.lane"
        git(self.repo, "worktree", "add", "-q", "-b", "lane", str(wt))
        self.assertEqual(locator.find(wt, env={}).records, self.rec)

    def test_a_lane_pointing_at_a_separate_clone_of_the_main_clone_is_refused(self):
        lane = self.lane()
        sep = self.separate_clone("proj.sep")
        git(lane, "config", "bakehouse.records", str(sep))
        with self.assertRaises(locator.SetupNeeded) as e:
            locator.find(lane, env={})
        self.assertIn(f"the records folder {sep} is a separate clone", str(e.exception))

    def test_a_lane_pointing_at_the_records_worktree_of_an_unrelated_repository_is_refused(self):
        other = self.tmp / "other"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        git(other, "commit", "-q", "--allow-empty", "-m", "unrelated")
        other_rec = self.tmp / "other.bakehouse-records"
        git(other, "worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(other_rec))
        lane = self.lane()
        git(lane, "config", "bakehouse.records", str(other_rec))
        with self.assertRaises(locator.SetupNeeded) as e:
            locator.find(lane, env={})
        self.assertIn(f"shares no root commit with {lane}", str(e.exception))


class TestMachineDir(unittest.TestCase):
    def test_override_then_xdg_then_home(self):
        self.assertEqual(locator.machine_dir({"BAKEHOUSE_MACHINE_DIR": "/m"}), Path("/m"))
        self.assertEqual(locator.machine_dir({"XDG_STATE_HOME": "/s", "HOME": "/h"}), Path("/s/bakehouse"))
        self.assertEqual(locator.machine_dir({"HOME": "/h"}), Path("/h/.local/state/bakehouse"))


class TestCommandLine(Base):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(LOCATOR), *args], capture_output=True, text=True, env=ENV)

    def test_exit_codes_and_one_path(self):
        self.assertEqual(self.run_cli("ops_ledger", str(self.repo)).returncode, locator.NO_TEAM)
        self.bind()
        r = self.run_cli("ops_ledger", str(self.repo))
        self.assertEqual(r.returncode, locator.SETUP_NEEDED)
        self.assertIn("bakehouse setup", r.stderr)
        rec = self.records()
        r = self.run_cli("ops_ledger", str(self.repo))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, str(rec / "ops" / "events.jsonl")))
        self.assertEqual(self.run_cli("nonsense").returncode, 2)


if __name__ == "__main__":
    unittest.main()
