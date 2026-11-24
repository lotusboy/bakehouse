"""Tests for 0.16.1's binding compared with main's (runtime/bakehouse_door.py): the door compares the binding of the
folder it runs in with .team/binding.json as committed on main's branch in the project's main clone; a careLimits that
differs fails fast-binding, and any differing key gives one warning line. Standard library only; each test builds a
scratch project in a temporary folder."""
import json
import subprocess
import time
import unittest
from datetime import datetime

import test_door
from test_door import BAKEHOUSE, TREE

LIMITS = {"paths": ["src/keys/**"]}


class Binding(unittest.TestCase):
    # The scratch-project helpers of test_door.Door, without its tests.
    setUp, tearDown, git, project, records = (test_door.Door.setUp, test_door.Door.tearDown, test_door.Door.git,
                                              test_door.Door.project, test_door.Door.records)
    commit, day, review, ask, door, route = (test_door.Door.commit, test_door.Door.day, test_door.Door.review,
                                             test_door.Door.ask, test_door.Door.door, test_door.Door.route)

    def write(self, binding, repo=None):
        ((repo or self.repo) / ".team" / "binding.json").write_text(json.dumps(binding))

    def bind(self, binding, subject="binding", repo=None):
        """Writes the binding and commits it on the branch checked out."""
        self.write(binding, repo)
        self.git(repo or self.repo, "commit", "-q", "-m", subject, "--", ".team/binding.json")

    def text(self, *args, cwd=None):
        p = subprocess.run([str(BAKEHOUSE), "door", *args], cwd=cwd or self.repo, capture_output=True, text=True,
                           env=self.env)
        self.assertEqual((p.returncode, p.stderr), (0, ""), p.stdout)
        return p.stdout.splitlines()

    def warning(self, keys, repo=None, branch="main"):
        return f"door: warning: the binding here differs from main's ({repo or self.repo} {branch}) in: {keys}"

    def old_branch_without_limits(self):
        """main's binding has careLimits; the branch old, checked out, has the binding from before them."""
        self.git(self.repo, "branch", "old")
        self.bind({**TREE, "careLimits": LIMITS}, "limits")
        self.git(self.repo, "checkout", "-q", "old")

    def test_the_same_binding_as_main_gives_no_warning(self):
        out = self.door("src/a.py")
        self.assertEqual(out["bindingDrift"], {"against": {"clone": str(self.repo), "branch": "main"}, "keys": [],
                                               "notMade": None})
        self.assertEqual(out["route"], "fast")
        lines = self.text("src/a.py")
        self.assertEqual(len(lines), 1, "one line: no warning")
        self.assertTrue(lines[0].startswith("door: fast: "), lines)

    def test_a_branch_without_main_s_hard_limits_is_never_fast(self):
        self.old_branch_without_limits()
        self.commit("A1: x", "src/b.py")
        self.ask("A1", fixRound=3)
        out = self.door("src/a.py")
        self.assertEqual((out["route"], [r["rule"] for r in out["reasons"]]), ("normal", ["fast-binding"]))
        self.assertEqual(out["bindingDrift"]["keys"], ["careLimits"])
        self.assertEqual(next(r for r in out["reasons"] if r["rule"] == "fast-binding")["against"],
                         {"clone": str(self.repo), "branch": "main"})
        self.assertEqual(self.text("src/a.py"), [
            f"door: normal: the hard limits here differ from main's ({self.repo} main): fast needs main's careLimits",
            self.warning("careLimits")])
        self.assertEqual(self.route("src/b.py"), ("look-around", ["look-fix-round"]), "a look-around stays one")
        self.assertEqual(self.text("src/b.py")[1], self.warning("careLimits"))

    def test_only_mutation_differs_and_fast_is_unaffected(self):
        self.git(self.repo, "branch", "old")
        self.bind({**TREE, "mutation": {"tool": "none"}}, "mutation")
        self.git(self.repo, "checkout", "-q", "old")
        out = self.door("src/a.py")
        self.assertEqual((out["route"], out["bindingDrift"]["keys"]), ("fast", ["mutation"]))
        self.assertNotIn("fast-binding", [r["rule"] for r in out["reasons"]])
        self.assertEqual(self.text("src/a.py")[1:], [self.warning("mutation")])

    def test_main_follows_names_the_branch_compared_with(self):
        follows = {**TREE, "mainFollows": {"branch": "engine"}}
        self.bind(follows, "follows")
        self.git(self.repo, "checkout", "-q", "-b", "engine")
        self.bind({**follows, "mutation": {"tool": "none"}}, "mutation on engine")
        self.git(self.repo, "checkout", "-q", "main")
        out = self.door("src/a.py")
        self.assertEqual(out["bindingDrift"], {"against": {"clone": str(self.repo), "branch": "engine"},
                                               "keys": ["mutation"], "notMade": None}, "engine, not main")
        self.assertEqual(self.text("src/a.py")[1:], [self.warning("mutation", branch="engine")])

    def test_no_main_no_master_no_main_follows_makes_no_comparison(self):
        self.git(self.repo, "branch", "-m", "main", "trunk")
        out = self.door("src/a.py")
        self.assertEqual((out["bindingDrift"]["against"], out["bindingDrift"]["keys"]), (None, []))
        self.assertEqual(out["bindingDrift"]["notMade"],
                         f"the binding has no mainFollows and {self.repo} has no local branch main or master")
        self.assertEqual(out["route"], "fast")
        self.assertEqual(len(self.text("src/a.py")), 1, "no text line for a comparison not made")

    def test_a_lane_clone_compares_with_the_main_clone_s_main(self):
        lane = self.tmp / "lane"
        self.git(self.tmp, "clone", "-q", str(self.repo), str(lane))
        self.git(lane, "config", "bakehouse.records", str(self.records()))
        self.bind({**TREE, "careLimits": LIMITS}, "limits")  # main clone's main moves on; lane's origin/main does not
        self.bind({**TREE, "careLimits": LIMITS}, "limits", repo=lane)
        self.assertNotIn("careLimits", self.git(lane, "show", "origin/main:.team/binding.json"))
        out = self.door("src/a.py", cwd=lane)
        self.assertEqual(out["bindingDrift"], {"against": {"clone": str(self.repo), "branch": "main"}, "keys": [],
                                               "notMade": None})
        self.assertEqual(out["route"], "fast")
        self.assertEqual(len(self.text("src/a.py", cwd=lane)), 1)

    def test_an_uncommitted_edit_on_main_differs(self):
        self.write({**TREE, "careLimits": LIMITS})
        out = self.door("src/a.py")
        self.assertEqual((out["route"], [r["rule"] for r in out["reasons"]]), ("normal", ["fast-binding"]))
        self.assertEqual(self.text("src/a.py")[1:], [self.warning("careLimits")])

    def test_as_of_does_not_change_the_comparison(self):
        self.old_branch_without_limits()
        now = self.door("src/a.py")
        before = datetime.fromtimestamp(time.time() - 4 * 86400).astimezone().isoformat(timespec="seconds")
        then = self.door("--as-of", before, "src/a.py")
        self.assertEqual(then["bindingDrift"], now["bindingDrift"])
        self.assertEqual((then["route"], [r["rule"] for r in then["reasons"]]), ("normal", ["fast-binding"]))


if __name__ == "__main__":
    unittest.main()
