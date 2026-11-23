"""Tests for 0.15.1: `bakehouse door --task <id>` with no paths reads the work order's own `ground:` line from the tracker
file (the working tree's copy first, else the newest branch tip that has the item with a line), and stops, exit 2,
naming the line, when there is none."""
import json
import subprocess
import unittest

import test_door

TRACKED = {**test_door.TREE, "tracker": {"id": "^A[0-9]+$", "file": "docs/BACKLOG.md"}}


class DoorGroundLine(test_door.Door):
    def setUp(self):
        super().setUp()
        (self.repo / ".team" / "binding.json").write_text(json.dumps(TRACKED))
        self.git(self.repo, "commit", "-q", "-am", "binding with a tracker file")

    def tracker(self, text, commit=False, branch=None):
        f = self.repo / "docs" / "BACKLOG.md"
        f.parent.mkdir(exist_ok=True)
        if branch:
            self.git(self.repo, "checkout", "-q", "-b", branch)
        f.write_text(text)
        if commit or branch:
            self.git(self.repo, "add", "docs/BACKLOG.md")
            self.git(self.repo, "commit", "-q", "-m", "tracker")
        if branch:
            self.git(self.repo, "checkout", "-q", "main")

    def run_door(self, *args):
        return subprocess.run([str(test_door.BAKEHOUSE), "door", *args], cwd=self.repo, capture_output=True,
                              text=True, env=self.env)

    def paths(self, *args):
        return [p["path"] for p in self.door(*args)["ground"]["paths"]]

    def test_the_line_gives_the_paths(self):
        self.tracker("### A7 thing\n\n- id: A7\n- need: x\n- ground: src/a.py src/many\n\n### A8 other\n\n- id: A8\n"
                     "- ground: src/b.py\n")
        self.assertEqual(self.paths("--task", "A7"), ["src/a.py", "src/many"])
        self.assertEqual(self.paths("--task", "A8"), ["src/b.py"])

    def test_quoted_paths(self):
        self.tracker('- id: A7\n- ground: "src/a.py" `src/b.py`\n')
        self.assertEqual(self.paths("--task", "A7"), ["src/a.py", "src/b.py"])

    def test_no_line_stops(self):
        self.tracker("- id: A7\n- need: x\n\n- id: A8\n- ground: src/b.py\n")
        p = self.run_door("--task", "A7")
        self.assertEqual(p.returncode, 2, p.stdout)
        self.assertIn("ground:", p.stderr)
        self.assertIn("A7", p.stderr)
        self.assertEqual(p.stdout, "")

    def test_no_item_stops(self):
        self.tracker("- id: A8\n- ground: src/b.py\n")
        self.assertEqual(self.run_door("--task", "A7").returncode, 2)

    def test_an_item_only_on_a_branch(self):
        self.tracker("- id: A8\n", commit=True)
        self.tracker("- id: A8\n\n- id: A7\n- ground: src/b.py\n", branch="lane/a7")
        self.assertEqual(self.paths("--task", "A7"), ["src/b.py"])

    def test_the_working_tree_wins(self):
        self.tracker("- id: A7\n- ground: src/b.py\n", branch="lane/a7")
        self.tracker("- id: A7\n- ground: src/a.py\n")
        self.assertEqual(self.paths("--task", "A7"), ["src/a.py"])

    def test_given_paths_still_win(self):
        self.tracker("- id: A7\n- ground: src/b.py\n")
        self.assertEqual(self.paths("--task", "A7", "src/a.py"), ["src/a.py"])

    def test_no_paths_and_no_task_is_bad_usage(self):
        self.assertEqual(self.run_door().returncode, 2)

    def test_a_binding_without_a_tracker_file(self):
        (self.repo / ".team" / "binding.json").write_text(json.dumps(test_door.TREE))
        p = self.run_door("--task", "A7")
        self.assertEqual(p.returncode, 2)
        self.assertIn("tracker.file", p.stderr)


def load_tests(loader, tests, pattern):
    """Only this file's own tests, not the ones inherited from test_door.Door."""
    own = sorted(n for n in DoorGroundLine.__dict__ if n.startswith("test_"))
    return unittest.TestSuite(DoorGroundLine(n) for n in own)


if __name__ == "__main__":
    unittest.main()
