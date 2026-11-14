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

    def test_a_relative_pointer_is_read_from_the_repo_root(self):
        self.bind()
        rec = self.records()
        git(self.repo, "config", "bakehouse.records", "../proj.bakehouse-records")
        (self.repo / "sub").mkdir()
        for where in (self.repo, self.repo / "sub"):
            with self.subTest(where=where):
                self.assertEqual(locator.find(where, env={}).records, rec)

    def test_claude_project_dir_wins_over_the_folder(self):
        self.bind()
        self.records()
        other = self.tmp / "other"
        other.mkdir()
        p = locator.find(other, env={"CLAUDE_PROJECT_DIR": str(self.repo)})
        self.assertEqual(p.root, self.repo)

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
