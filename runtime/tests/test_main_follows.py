"""Tests for the binding's `mainFollows` (0.7.8): with it on, a work order's first baseline is sealed only when the
main clone's branch is merged into the code checkout's HEAD; session-start says when that branch has engine commits
this checkout lacks; close notes engine commits merged there after the first baseline; preflight checks the field.
Standard library only; every test builds scratch repositories in a temporary folder.
python3 -m unittest runtime/tests/test_main_follows.py
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
REC = "2026-11-10-W-1-step1-review"
GOOD_P = {"moves": [{"what": "out:v", "from": 1, "to": 2}], "mustNotMove": ["out:w"]}
BASE_M = {"out:v": 1, "out:w": 5}
PASS_M = {"out:v": 2, "out:w": 5}
BINDING = {"owned": True, "reviewName": "{date}-{id}-step{n}-review",
           "tracker": {"file": "T.md", "id": "^W-[0-9]+$"}, "signal": {"codeRoots": ["src/"]},
           "mainFollows": {"branch": "main"}}
NOTICE = "engine commit"
REFUSAL = "merge main into this branch"


class MainFollows(unittest.TestCase):
    binding = BINDING

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name).resolve()
        self.t = t
        self.repo = t / "repo"
        self.records = t / "repo.bakehouse-records"
        for d in ("home", "machine", "tmp"):
            (t / d).mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("OPS_", "PREDICT_GATE", "DECISION_", "BAKEHOUSE_", "CLAUDE_PROJECT_DIR",
                                         "GIT_"))}
        self.env.update(HOME=str(t / "home"), BAKEHOUSE_MACHINE_DIR=str(t / "machine"), TMPDIR=str(t / "tmp"),
                        PYTHONDONTWRITEBYTECODE="1", GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=str(t / "home" / ".gitconfig"), GIT_AUTHOR_NAME="t",
                        GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True, env=self.env)
        (self.repo / ".team").mkdir()
        (self.repo / ".team" / "binding.json").write_text(json.dumps(self.binding))
        self.git(self.repo, "worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(self.records))
        self.git(self.repo, "config", "bakehouse.records", str(self.records))
        self.n = 0
        self.commit(self.records, "records", {})
        self.commit(self.repo, "base", {"src/app.py": "v = 1\n", "T.md": "t\n"})
        self.git(self.repo, "checkout", "-q", "-b", "prod")  # a product branch, cut from main

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, where, *args, check=True):
        p = subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, env=self.env)
        if check and p.returncode:
            raise AssertionError(f"git {' '.join(args)} in {where}: {p.stderr}")
        return p.stdout.strip()

    def commit(self, root, msg, files):
        for path, data in files.items():
            p = root / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(data if isinstance(data, str) else json.dumps(data))
        self.git(root, "add", "-A")
        self.n += 1
        w = f"2026-11-10T09:{self.n:02d}:00+00:00"
        subprocess.run(["git", "-C", str(root), "commit", "-q", "--allow-empty", "-m", msg], check=True,
                       capture_output=True, env=dict(self.env, GIT_AUTHOR_DATE=w, GIT_COMMITTER_DATE=w))

    def on_main(self, msg, files):
        """A commit on the main clone's main (another product's engine change, merged as its work order closed),
        made in a worktree so the main clone's own checkout stays on its branch."""
        wt = self.t / "main-wt"
        if not wt.exists():
            self.git(self.repo, "worktree", "add", "-q", str(wt), "main")
        self.commit(wt, msg, files)

    def rec(self, name=""):
        return self.records / "runs" / REC / name

    def put(self, files):
        for name, data in files.items():
            self.rec(name).parent.mkdir(parents=True, exist_ok=True)
            self.rec(name).write_text(json.dumps(data))

    def run_tool(self, argv, cwd=None, env=None):
        p = subprocess.run(argv, cwd=cwd or self.repo, capture_output=True, text=True, env=env or self.env, input="")
        return p.returncode, p.stdout + p.stderr

    def gate(self, *args, cwd=None):
        return self.run_tool(["python3", str(RUNTIME / "predict-gate.py"), *args], cwd)

    def seal(self, rnd, name, cwd=None):
        return self.gate("seal", "W-1", rnd, str(self.rec(name)), cwd=cwd)

    def sealed(self):
        led = self.records / "ops" / "events.jsonl"
        return [json.loads(x) for x in led.read_text().splitlines()] if led.exists() else []

    def baseline(self, cwd=None):
        self.put({"moves.prediction.json": GOOD_P, "moves.baseline.json": BASE_M})
        rc, out = self.seal("first", "moves.prediction.json", cwd=cwd)
        self.assertEqual(rc, 0, out)  # a prediction is never checked
        return self.seal("first", "moves.baseline.json", cwd=cwd)

    # ---- A, B: the check at the first baseline ----

    def test_1_an_unmerged_main_refuses_the_first_baseline_naming_the_merge(self):
        self.on_main("engine change", {"src/engine.py": "e = 1\n"})
        rc, out = self.baseline()
        self.assertEqual(rc, 2, out)
        self.assertIn(REFUSAL, out)
        self.assertIn("then measure and seal the baseline again", out)
        self.assertNotIn("Traceback", out)
        self.assertEqual([e["fileKind"] for e in self.sealed() if e["kind"] == "sealed"], ["prediction"])

    def test_2_a_merged_main_passes(self):
        self.on_main("engine change", {"src/engine.py": "e = 1\n"})
        self.git(self.repo, "merge", "-q", "--no-edit", "main")
        rc, out = self.baseline()
        self.assertEqual(rc, 0, out)

    def test_3_a_lane_clone_reads_the_main_clones_tip_not_its_own_stale_copies(self):
        lane = self.t / "lane"
        self.git(self.t, "clone", "-q", "-b", "prod", str(self.repo), str(lane))
        self.git(lane, "config", "bakehouse.records", str(self.records))
        self.git(lane, "branch", "-q", "main", "origin/main")
        self.on_main("engine change", {"src/engine.py": "e = 1\n"})  # after the lane was cloned
        for stale in ("main", "origin/main"):  # both are in the lane's HEAD: reading either would pass
            self.git(lane, "merge-base", "--is-ancestor", stale, "HEAD")
        rc, out = self.baseline(cwd=lane)
        self.assertEqual(rc, 2, out)
        self.assertIn(REFUSAL, out)
        self.assertIn(f"git pull {self.repo} main", out)
        self.git(lane, "pull", "-q", "--no-rebase", "--no-edit", str(self.repo), "main")
        rc, out = self.seal("first", "moves.baseline.json", cwd=lane)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.sealed()[-1]["commit"], self.git(lane, "rev-parse", "--short", "HEAD"))

    def test_3b_a_main_clone_with_no_such_branch_refuses_naming_it(self):
        b = dict(BINDING, mainFollows={"branch": "trunk"})
        self.commit(self.repo, "bind trunk", {".team/binding.json": b})
        rc, out = self.baseline()
        self.assertEqual(rc, 2, out)
        self.assertIn("has no branch trunk", out)

    def test_4_a_fix_rounds_baseline_is_not_checked(self):
        self.on_main("engine change", {"src/engine.py": "e = 1\n"})
        self.put({"moves.prediction.fix1.json": GOOD_P, "moves.baseline.fix1.json": BASE_M})
        rc, out = self.seal("fix1", "moves.prediction.fix1.json")
        self.assertEqual(rc, 0, out)
        rc, out = self.seal("fix1", "moves.baseline.fix1.json")
        self.assertEqual(rc, 0, out)

    def test_5_with_the_switch_off_an_unmerged_main_seals(self):
        b = {k: v for k, v in BINDING.items() if k != "mainFollows"}
        self.commit(self.repo, "switch off", {".team/binding.json": b})
        self.on_main("engine change", {"src/engine.py": "e = 1\n"})
        rc, out = self.baseline()
        self.assertEqual(rc, 0, out)

    # ---- C: the session notice ----

    def session(self):
        rc, out = self.run_tool(["python3", str(RUNTIME / "session-start.py")],
                                env=dict(self.env, CLAUDE_PROJECT_DIR=str(self.repo)))
        self.assertEqual(rc, 0, out)
        return [x for x in out.splitlines() if NOTICE in x]

    def test_6_the_session_notice_counts_engine_commits_only(self):
        self.on_main("docs only", {"docs/x.md": "x\n"})
        self.assertEqual(self.session(), [])  # a commit outside the code roots is not counted
        self.on_main("engine change", {"src/engine.py": "e = 1\n"})
        self.on_main("engine change 2", {"src/engine.py": "e = 2\n"})
        self.assertEqual(self.session(), ["bakehouse: main has 2 engine commits this branch does not have: merge it "
                                          "in before the next work order's baseline"])
        self.git(self.repo, "merge", "-q", "--no-edit", "main")
        self.assertEqual(self.session(), [])

    def test_6c_a_lane_clone_counts_the_main_clones_commits_with_no_fetch(self):
        lane = self.t / "lane"
        self.git(self.t, "clone", "-q", "-b", "prod", str(self.repo), str(lane))
        self.git(lane, "config", "bakehouse.records", str(self.records))
        self.on_main("engine change", {"src/engine.py": "e = 1\n"})  # an object the lane does not hold
        rc, out = self.run_tool(["python3", str(RUNTIME / "session-start.py")],
                                env=dict(self.env, CLAUDE_PROJECT_DIR=str(lane)), cwd=lane)
        self.assertEqual(rc, 0, out)
        self.assertIn("bakehouse: main has 1 engine commit this branch does not have", out)

    def test_6b_no_notice_with_the_switch_off(self):
        b = {k: v for k, v in BINDING.items() if k != "mainFollows"}
        self.commit(self.repo, "switch off", {".team/binding.json": b})
        self.on_main("engine change", {"src/engine.py": "e = 1\n"})
        self.assertEqual(self.session(), [])

    # ---- D: the note at close ----

    def flow(self, moved):
        self.assertEqual(self.baseline()[0], 0)
        if moved:
            self.on_main("another product's engine change", {"src/engine.py": "e = 1\n"})
        base = self.git(self.repo, "rev-parse", "HEAD")
        (self.repo / "src" / "app.py").write_text("v = 2\n")
        self.git(self.repo, "add", "-A")
        rc, out = self.gate("seal-code", "W-1", "first", "--", "-q", "-m", "W-1: the change")
        self.assertEqual(rc, 0, out)
        head = self.git(self.repo, "rev-parse", "HEAD")
        self.put({"moves.measured.json": PASS_M,
                  "measure-report.json": {"base": {"sha": base}, "head": {"sha": head}}})
        rc, out = self.seal("first", "moves.measured.json")
        self.assertEqual(rc, 0, out)
        self.commit(self.records, "W-1: record", {})
        return self.gate("close", "W-1")

    def test_7_close_notes_engine_commits_merged_to_main_after_the_first_baseline(self):
        rc, out = self.flow(moved=True)
        self.assertEqual(rc, 0, out)
        self.assertIn("note: the main clone's main has 1 engine commit(s) that were not in", out)
        self.assertIn("first measured together at the next work order's baseline", out)

    def test_7b_no_note_when_main_did_not_move(self):
        rc, out = self.flow(moved=False)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("measured together", out)

    # ---- E: preflight ----

    def preflight(self, b):
        (self.repo / ".team" / "binding.json").write_text(json.dumps(b))
        rc, out = self.run_tool(["python3", str(RUNTIME / "team-preflight.py"), "startup", "--repo", str(self.repo)])
        return [x for x in out.splitlines() if x.startswith("FAIL") and "mainFollows" in x]

    def test_8_preflight_checks_the_shape_and_the_branch(self):
        for bad in ("main", None, {}, {"branch": ""}, {"branch": 1}, ["main"]):
            with self.subTest(mainFollows=bad):
                self.assertEqual(len(self.preflight(dict(BINDING, mainFollows=bad))), 1)
        fails = self.preflight(dict(BINDING, mainFollows={"branch": "trunk"}))
        self.assertEqual(len(fails), 1, fails)
        self.assertIn("has no branch trunk", fails[0])
        self.assertEqual(self.preflight(BINDING), [])


if __name__ == "__main__":
    unittest.main()
