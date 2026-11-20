"""Tests for item 6: run folders on the records branch (`bakehouse-records`, the records worktree) under `runs/`.
locator.runs_location says where a project keeps its run folders; every reader of a record folder asks it. A
binding with `reviews` and no `runs` keeps them in the code tree (tree mode), as before; the existing suite
guards that. These are the records-mode twins, and one end-to-end run. Standard library only; every test builds
scratch repositories and nothing outside a temporary folder is read or written.
python3 -m unittest runtime/tests/test_run_folders.py
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(TESTS))
import locator  # noqa: E402
import test_team_roles as roles_tests  # noqa: E402
import test_team_preflight as preflight_tests  # noqa: E402

BAKEHOUSE = RUNTIME.parent / "bin" / "bakehouse"
REC = "2026-11-10-W-1-step1-review"
GOOD_P = {"moves": [{"what": "out:v", "from": 1, "to": 2}], "mustNotMove": ["out:w"]}
BASE_M = {"out:v": 1, "out:w": 5}
PASS_M = {"out:v": 2, "out:w": 5}
FAIL_M = {"out:v": 3, "out:w": 5}
BINDING = {"owned": True, "reviewName": "{date}-{id}-step{n}-review",
           "tracker": {"file": "T.md", "id": "^W-[0-9]+$"}, "signal": {"codeRoots": ["src/"]}}


# ---------- W1: the location rule ----------

class Location(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.repo = self.tmp / "proj"
        self.records = self.tmp / "proj.bakehouse-records"
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_CONFIG_NOSYSTEM": "1",
                    "HOME": str(self.tmp), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        self.repo.mkdir()
        self.git(self.repo, "init", "-q", "-b", "main")
        self.git(self.repo, "commit", "-q", "--allow-empty", "-m", "first")
        (self.repo / ".team").mkdir()
        (self.repo / ".team" / "binding.json").write_text("{}")
        self.git(self.repo, "worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(self.records))
        self.git(self.repo, "config", "bakehouse.records", str(self.records))
        self.project = locator.find(self.repo, env={})

    def git(self, where, *args):
        subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True, text=True, env=self.env)

    def test_a_binding_with_no_runs_and_no_reviews_keeps_run_folders_in_the_records_worktree(self):
        self.assertEqual(locator.runs_location(self.project, {"owned": True}),
                         (self.records, "runs/", "records"))
        self.assertEqual(locator.runs_location(self.project, {"runs": "records"}),
                         (self.records, "runs/", "records"))

    def test_a_binding_with_reviews_and_no_runs_keeps_todays_tree(self):
        self.assertEqual(locator.runs_location(self.project, {"reviews": "team/runs/"}),
                         (self.repo, "team/runs/", "tree"))
        # the value as the binding writes it: each reader checks it as before
        self.assertEqual(locator.runs_location(self.project, {"reviews": ""}), (self.repo, "", "tree"))
        self.assertEqual(locator.runs_location(None, {"reviews": "r/"}, repo=self.tmp), (self.tmp, "r/", "tree"))

    def test_runs_tree_with_no_reviews_takes_the_default_folder(self):
        self.assertEqual(locator.runs_location(self.project, {"runs": "tree"}), (self.repo, "axis/runs/", "tree"))
        self.assertEqual(locator.runs_location(self.project, {"runs": "tree", "reviews": "x/"}),
                         (self.repo, "x/", "tree"))

    def test_runs_records_beside_reviews_is_a_bad_binding(self):
        with self.assertRaises(locator.BadRuns) as e:
            locator.runs_location(self.project, {"runs": "records", "reviews": "x/"})
        self.assertIn("reviews", str(e.exception))

    def test_any_other_runs_value_is_a_bad_binding(self):
        for bad in ("both", "", None, 1, ["tree"]):
            with self.subTest(runs=bad), self.assertRaises(locator.BadRuns) as e:
                locator.runs_location(self.project, {"runs": bad})
            self.assertIn("runs", str(e.exception))

    def test_with_no_project_a_records_mode_binding_reads_the_tree_default(self):
        # fix 8: no team (a --binding in a repo with no team) means no records worktree: tree mode, as on main
        self.assertEqual(locator.runs_location(None, {"owned": True}, repo=self.tmp),
                         (self.tmp, "axis/runs/", "tree"))

    def test_tree_mode_reads_the_repo_it_is_given(self):
        # L06: a lane clone's close passes its own repo, which is not the project's root
        self.assertEqual(locator.runs_location(self.project, {"reviews": "r/"}, repo=self.tmp),
                         (self.tmp, "r/", "tree"))

    def test_a_reviews_key_that_is_present_counts_whatever_its_value(self):
        # L04: "runs": "records" beside "reviews": null or "" is still a bad binding; without runs, tree mode
        for v in (None, ""):
            with self.subTest(reviews=v), self.assertRaises(locator.BadRuns):
                locator.runs_location(self.project, {"runs": "records", "reviews": v})
            self.assertEqual(locator.runs_mode({"reviews": v}), "tree")

    def test_a_project_that_needs_setup_owns_no_records_worktree(self):
        # L15: records_owner answers None, never raises, when the main clone needs setup
        (self.records / "runs" / "x").mkdir(parents=True)
        self.git(self.repo, "config", "--unset", "bakehouse.records")
        self.assertIsNone(locator.records_owner(self.records / "runs" / "x"))

    def test_a_symlinked_records_pointer_still_maps_back(self):
        # L14: the pointer names a link to the records worktree; both sides are compared resolved
        link = self.tmp / "records-link"
        link.symlink_to(self.records)
        self.git(self.repo, "config", "bakehouse.records", str(link))
        (self.records / "runs" / "x").mkdir(parents=True)
        p = locator.records_owner(self.records / "runs" / "x")
        self.assertIsNotNone(p)
        self.assertEqual((p.root, p.records), (self.repo, link))

    def test_the_records_worktree_maps_back_to_its_project(self):
        (self.records / "runs" / "x").mkdir(parents=True)
        p = locator.records_owner(self.records / "runs" / "x")
        self.assertEqual((p.root, p.records), (self.repo, self.records))
        self.assertEqual(locator.records_owner(self.records / "runs" / "x" / "not-yet"), p)  # a folder not made yet
        self.assertIsNone(locator.records_owner(self.repo))
        self.assertIsNone(locator.records_owner(self.tmp))
        # a worktree of the same repo that the pointer does not name
        other = self.tmp / "other"
        self.git(self.repo, "worktree", "add", "-q", "-b", "side", str(other))
        self.assertIsNone(locator.records_owner(other))


# ---------- a scratch project in records mode ----------

class Project(unittest.TestCase):
    binding = BINDING

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name).resolve()
        self.t = t
        self.repo = t / "repo"
        self.records = t / "repo.bakehouse-records"
        self.ledger = self.records / "ops" / "events.jsonl"
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
        self.bind(self.binding)
        self.git(self.repo, "worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(self.records))
        self.git(self.repo, "config", "bakehouse.records", str(self.records))
        self.n = 0
        self.rcommit("records")
        self.commit("base", {"src/app.py": "v = 1\n", "T.md": "t\n"})

    def tearDown(self):
        self.tmp.cleanup()

    def bind(self, b):
        (self.repo / ".team" / "binding.json").write_text(json.dumps(b))

    def git(self, where, *args, check=True, env=None):
        p = subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, env=env or self.env)
        if check and p.returncode:
            raise AssertionError(f"git {' '.join(args)} in {where}: {p.stderr}")
        return p.stdout.strip()

    def when(self):
        self.n += 1
        return f"2026-11-10T09:{self.n:02d}:00+00:00"

    def write(self, root, files):
        for path, data in (files or {}).items():
            p = root / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(data if isinstance(data, str) else json.dumps(data))

    def _commit(self, root, msg, files, trailers=(), rm=()):
        self.write(root, files)
        for path in rm:
            self.git(root, "rm", "-q", "-r", path)
        self.git(root, "add", "-A")
        w = self.when()
        self.git(root, "commit", "-q", "--allow-empty", "-m", msg, *[x for t in trailers for x in ("-m", t)],
                 env=dict(self.env, GIT_AUTHOR_DATE=w, GIT_COMMITTER_DATE=w))

    def commit(self, msg, files=None, *trailers):
        """A commit in the code tree."""
        self._commit(self.repo, msg, files, trailers)

    def rcommit(self, msg, files=None, *trailers, rm=()):
        """A commit in the records worktree; files are paths under runs/<REC>/."""
        self._commit(self.records, msg, {f"runs/{REC}/{n}": d for n, d in (files or {}).items()}, trailers,
                     [f"runs/{REC}/{n}" for n in rm])

    def rec(self, name=""):
        return self.records / "runs" / REC / name

    def run_tool(self, argv, cwd=None, env=None):
        p = subprocess.run(argv, cwd=cwd or self.repo, capture_output=True, text=True, env=env or self.env)
        return p.returncode, p.stdout + p.stderr

    def gate(self, *args, cwd=None, env=None):
        return self.run_tool(["python3", str(RUNTIME / "predict-gate.py"), *args], cwd, env)

    def bakehouse(self, *args, cwd=None, env=None):
        return self.run_tool(["python3", str(BAKEHOUSE), *args], cwd, env)

    def events(self):
        return [json.loads(x) for x in self.ledger.read_text().splitlines()] if self.ledger.exists() else []

    def code_tree_has_no_runs(self):
        names = self.git(self.repo, "ls-tree", "-r", "--name-only", "HEAD").split("\n")
        self.assertFalse([n for n in names if n.startswith(("runs/", "axis/"))], names)

    def unsealed_flow(self, measured=PASS_M):
        self.rcommit("W-1: prediction", {"moves.prediction.json": GOOD_P, "moves.baseline.json": BASE_M})
        self.commit("W-1: the change", {"src/app.py": "v = 2\n"})
        self.rcommit("W-1: measurement", {"moves.measured.json": measured})

    def seal(self, rnd, name, *extra, cwd=None):
        return self.gate("seal", "W-1", rnd, str(self.rec(name)), *extra, cwd=cwd)

    def seal_code(self, msg, files, rnd="first"):
        self.write(self.repo, files)
        self.git(self.repo, "add", "-A")
        w = self.when()
        return self.gate("seal-code", "W-1", rnd, "--", "-q", "-m", msg,
                         env=dict(self.env, GIT_AUTHOR_DATE=w, GIT_COMMITTER_DATE=w))

    def sealed_flow(self, commit=None):
        commit = commit or self.commit_named
        self.write(self.rec(), {"moves.prediction.json": GOOD_P, "moves.baseline.json": BASE_M})
        commit("W-1: prediction", ("moves.prediction.json", "moves.baseline.json"))
        for n in ("moves.prediction.json", "moves.baseline.json"):
            rc, out = self.seal("first", n)
            self.assertEqual(rc, 0, out)
        base = self.git(self.repo, "rev-parse", "HEAD")
        rc, out = self.seal_code("W-1: the change", {"src/app.py": "v = 2\n"})
        self.assertEqual(rc, 0, out)
        head = self.git(self.repo, "rev-parse", "HEAD")
        self.write(self.rec(), {"moves.measured.json": PASS_M,
                                "measure-report.json": {"base": {"sha": base}, "head": {"sha": head}}})
        rc, out = self.seal("first", "moves.measured.json")
        self.assertEqual(rc, 0, out)
        commit("W-1: measurement", ("moves.measured.json", "measure-report.json"))
        return base

    def commit_named(self, msg, names):
        self.rcommit(msg, {n: self.rec(n).read_text() for n in names})


# ---------- W2: close and seal read the records worktree ----------

class Close(Project):
    def test_an_unsealed_record_closes_from_the_records_worktree(self):
        self.unsealed_flow()
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"record folder(s) runs/{REC}", out)
        closed = [e for e in self.events() if e["kind"] == "work-order-closed"]
        self.assertEqual(len(closed), 1)
        self.assertIn(f"runs/{REC}/moves.prediction.json sha256:", closed[0]["note"])
        self.code_tree_has_no_runs()

    def test_a_wrong_measurement_refuses(self):
        self.unsealed_flow(FAIL_M)
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 1, out)
        self.assertIn(f"runs/{REC}/moves.prediction.json: check exit 1", out)

    def test_an_uncommitted_file_in_the_record_folder_refuses(self):
        self.unsealed_flow()
        self.write(self.records, {f"runs/{REC}/notes.md": "x"})
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 1, out)
        self.assertIn(f"runs/{REC}: uncommitted or untracked file(s)", out)

    def test_history_after_the_first_measurement_needs_the_owners_decision(self):
        self.unsealed_flow()
        self.rcommit("W-1: park a try", {"history/moves.measured.try.json": PASS_M})
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 1, out)
        self.assertIn(f"runs/{REC}/history/ changed in or after the commit of the record's first measurement", out)

    def test_a_removed_round_refuses(self):
        self.unsealed_flow()
        p2 = {"moves": [{"what": "out:v", "from": 2, "to": 3}]}
        self.rcommit("W-1: prediction fix1", {"moves.prediction.fix1.json": p2, "moves.baseline.fix1.json": PASS_M})
        self.commit("W-1: fix", {"src/app.py": "v = 3\n"})
        self.rcommit("W-1: measurement fix1", {"moves.measured.fix1.json": {"out:v": 4, "out:w": 5}})
        self.rcommit("W-1: drop the round", rm=("moves.prediction.fix1.json", "moves.measured.fix1.json",
                                                "moves.baseline.fix1.json"))
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 1, out)
        self.assertIn(f"runs/{REC}/moves.measured.fix1.json was removed in or after the commit", out)

    def test_a_code_commit_after_the_measurement_refuses(self):
        self.unsealed_flow()
        self.commit("W-1: late code", {"src/app.py": "v = 9\n"})
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 1, out)
        self.assertIn("comes after the latest measured file's commit", out)

    def test_a_record_in_the_code_tree_is_not_read_in_records_mode(self):
        self.commit("W-1: prediction", {f"axis/runs/{REC}/moves.prediction.json": json.dumps(GOOD_P)})
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 1, out)
        self.assertIn(f"no record folder matches {self.records}/runs/", out)

    def test_a_sealed_record_closes(self):
        base = self.sealed_flow()
        seals = [e for e in self.events() if e["kind"] == "sealed" and e.get("fileKind") == "prediction"]
        self.assertEqual(seals[0]["path"], f"runs/{REC}/moves.prediction.json")
        self.assertEqual(seals[0]["commit"], base[:len(seals[0]["commit"])])  # the code HEAD, not the records one
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 0, out)
        self.assertIn("sealed: judged by", out)
        self.code_tree_has_no_runs()

    def test_a_sealed_file_changed_in_the_records_worktree_refuses(self):
        self.sealed_flow()
        self.rcommit("W-1: edit", {"moves.measured.json": {"out:v": 2, "out:w": 5, "out:x": 0}})
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 1, out)
        self.assertIn(f"runs/{REC}/moves.measured.json at HEAD hashes", out)

    def test_merged_reads_the_record_in_the_records_worktree(self):
        self.unsealed_flow()
        self.assertEqual(self.gate("close", "W-1")[0], 0)
        rc, out = self.gate("close", "W-1", "--merged")
        self.assertEqual(rc, 0, out)
        self.rcommit("W-1: edit", {"moves.measured.json": {"out:v": 2, "out:w": 5, "out:x": 0}})
        rc, out = self.gate("close", "W-1", "--merged")
        self.assertEqual(rc, 1, out)

    def test_records_check_reads_the_close_from_the_records_worktree(self):
        self.unsealed_flow()
        self.assertEqual(self.gate("close", "W-1")[0], 0)
        rc, out = self.run_tool(["python3", str(RUNTIME / "records-check.py"), "--repo", str(self.repo),
                                 "--no-write", "--as-of", "2026-11-10", "--since", "2026-11-01"])
        self.assertEqual(rc, 0, out)
        self.assertIn("| W-1 | 1 | closed |", out)
        rc, out = self.run_tool(["python3", str(RUNTIME / "records-check.py"), "--repo", str(self.repo),
                                 "--register", str(self.t / "records.json"), "--as-of", "2026-11-10",
                                 "--since", "2026-11-01"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads((self.t / "records.json").read_text())["computed"]["reviews"], "runs/")

    def test_a_binding_file_in_a_repo_with_no_team_closes_in_the_tree_as_on_main(self):
        # fix 8: no project, so no records worktree: the record is read in the code tree, axis/runs/
        bare = self.t / "bare"
        subprocess.run(["git", "init", "-q", "-b", "main", str(bare)], check=True, env=self.env)
        bfile = self.t / "binding.json"
        bfile.write_text(json.dumps(BINDING))
        self._commit(bare, "base", {"src/app.py": "v = 1\n"})
        self._commit(bare, "W-1: prediction", {f"axis/runs/{REC}/moves.prediction.json": GOOD_P,
                                               f"axis/runs/{REC}/moves.baseline.json": BASE_M})
        self._commit(bare, "W-1: the change", {"src/app.py": "v = 2\n"})
        self._commit(bare, "W-1: measurement", {f"axis/runs/{REC}/moves.measured.json": PASS_M})
        rc, out = self.gate("close", "W-1", "--repo", str(bare), "--binding", str(bfile), cwd=bare)
        self.assertIn(f"record folder(s) axis/runs/{REC}", out)
        self.assertNotIn("records worktree", out)

    def test_a_bad_runs_field_is_bad_input(self):
        self.unsealed_flow()
        self.bind(dict(BINDING, runs="records", reviews="axis/runs/"))
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 2, out)
        self.assertIn("runs", out)


class SealCheckout(Project):
    """Fix round 1: in records mode the seal names a code checkout's HEAD, never the main clone's by default,
    and seals only a file under runs/ in the records worktree."""

    def lane(self):
        lane = self.t / "lane"
        self.git(self.t, "clone", "-q", str(self.repo), str(lane))
        self.git(lane, "config", "bakehouse.records", str(self.records))
        self._commit(lane, "W-1: lane work", {"src/lane.py": "x = 1\n"})
        return lane

    def sealed(self):
        return [e for e in self.events() if e["kind"] == "sealed"]

    def test_a_seal_from_the_records_worktree_with_no_repo_is_refused(self):
        self.write(self.rec(), {"moves.prediction.json": GOOD_P})
        rc, out = self.seal("first", "moves.prediction.json", cwd=self.records)
        self.assertEqual(rc, 2, out)
        self.assertIn("run seal from the code checkout, or pass --repo <checkout>", out)
        self.assertEqual(self.sealed(), [])

    def test_a_seal_names_the_lane_checkouts_head(self):
        lane = self.lane()
        head = self.git(lane, "rev-parse", "--short", "HEAD")
        self.assertNotEqual(head, self.git(self.repo, "rev-parse", "--short", "HEAD"))
        self.write(self.rec(), {"moves.prediction.json": GOOD_P, "moves.baseline.json": BASE_M})
        rc, out = self.seal("first", "moves.prediction.json", "--repo", str(lane), cwd=self.records)
        self.assertEqual(rc, 0, out)
        rc, out = self.seal("first", "moves.baseline.json", cwd=lane)
        self.assertEqual(rc, 0, out)
        self.assertEqual([e["commit"] for e in self.sealed()], [head, head])

    def test_a_records_mode_seal_refuses_a_file_outside_runs(self):
        self.write(self.repo, {f"axis/runs/{REC}/moves.prediction.json": GOOD_P})
        rc, out = self.gate("seal", "W-1", "first", str(self.repo / "axis" / "runs" / REC / "moves.prediction.json"))
        self.assertEqual(rc, 2, out)
        self.assertIn(f"{self.records}/runs/", out)
        self.assertEqual(self.sealed(), [])

    def test_a_tree_mode_seal_of_a_file_in_the_records_worktree_is_refused_as_on_main(self):
        self.bind(dict(BINDING, reviews="axis/runs/"))
        self.write(self.rec(), {"moves.prediction.json": GOOD_P})
        rc, out = self.seal("first", "moves.prediction.json")
        self.assertEqual(rc, 2, out)
        self.assertIn("has no team", out)
        self.assertEqual(self.sealed(), [])

    def test_a_project_that_needs_setup_is_a_clean_refusal(self):
        # L15: no traceback when the records worktree's project needs setup
        self.write(self.rec(), {"moves.prediction.json": GOOD_P})
        self.git(self.repo, "config", "--unset", "bakehouse.records")
        rc, out = self.seal("first", "moves.prediction.json")
        self.assertEqual(rc, 2, out)
        self.assertNotIn("Traceback", out)


# ---------- `bakehouse status`: the ledger, in either mode ----------

class Status(Project):
    binding = dict(BINDING, reviews="axis/runs/")

    def test_a_closed_work_order_reads_closed_after_the_mode_switches(self):
        self.commit("W-1: prediction", {f"axis/runs/{REC}/moves.prediction.json": GOOD_P,
                                        f"axis/runs/{REC}/moves.baseline.json": BASE_M})
        self.commit("W-1: the change", {"src/app.py": "v = 2\n"})
        self.commit("W-1: measurement", {f"axis/runs/{REC}/moves.measured.json": PASS_M})
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 0, out)
        rc, before = self.bakehouse("status", "--json")
        self.assertEqual(rc, 0, before)
        self.bind(BINDING)  # the binding drops `reviews`: records mode
        rc, after = self.bakehouse("status", "--json")
        self.assertEqual(rc, 0, after)
        self.assertEqual(after, before)
        self.assertEqual([c["workOrder"] for c in json.loads(after)["closedSince"]], ["W-1"])


# ---------- the signals read the records worktree's runs/ ----------

class Signals(Project):
    binding = {"owned": True, "register": "health.json",
               "signal": {"script": "health-signal", "ref": "HEAD", "codeRoots": ["src/"], "part": "^src/([^/]+)/"}}

    def signal(self, script, *args):
        return self.run_tool(["python3", str(RUNTIME / script), "--repo", str(self.repo), "--no-write", *args])

    def test_health_signal_reads_review_findings_in_the_records_worktree(self):
        self.commit("init", {"src/a/x.py": "1\n"})
        self.write(self.records, {"runs/2026-06-12-thing/findings.md": "P1: src/a/x.py breaks.\n"})
        self.write(self.repo, {f"axis/runs/2026-06-1{i}-old/findings.md": "P1: src/a/x.py, in the code tree.\n"
                               for i in (3, 4)})
        rc, out = self.run_tool(["python3", str(RUNTIME / "health-signal.py"), "--repo", str(self.repo),
                                 "--as-of", "2026-06-15", "--register", str(self.t / "health.json")])
        self.assertEqual(rc, 0, out)
        reg = json.loads((self.t / "health.json").read_text())
        self.assertEqual(reg["parts"]["a"]["reviewFindings"], 1)
        self.assertEqual(reg["computed"]["reviewFolders"], 1)

    def test_driver_signal_reads_the_records_worktree_and_needs_no_reviews(self):
        rc, out = self.signal("driver-signal.py", "--as-of", "2026-06-30")
        self.assertEqual(rc, 0, out)  # no runs/ folder yet: no run folders, not a failure
        body = "".join(f"value_{i} = compute(item_{i}, limit={i})\n" for i in range(12))
        for i, d in enumerate(("2026-06-10", "2026-06-20", "2026-06-29")):
            self.write(self.records, {f"runs/{d}-job{i}/drivers/probe.py": body})
        self.write(self.repo, {"axis/runs/2026-06-28-x/drivers/probe.py": body})
        rc, out = self.run_tool(["python3", str(RUNTIME / "driver-signal.py"), "--repo", str(self.repo),
                                 "--as-of", "2026-06-30", "--register", str(self.t / "drivers.json")])
        self.assertEqual(rc, 0, out)
        c = json.loads((self.t / "drivers.json").read_text())["computed"]
        self.assertEqual((c["runFolders"], c["driverFiles"]), (3, 3))
        self.assertEqual(c["reviews"], "runs/")  # fix 4: no machine path in the register

    def test_driver_signal_in_tree_mode_with_no_reviews_reads_the_default_folder(self):
        # fix 6: as the locator, close and health-signal do
        self.bind(dict(self.binding, runs="tree"))
        body = "".join(f"value_{i} = compute(item_{i}, limit={i})\n" for i in range(12))
        for i, d in enumerate(("2026-06-10", "2026-06-20")):
            self.write(self.repo, {f"axis/runs/{d}-job{i}/drivers/probe.py": body})
        rc, out = self.run_tool(["python3", str(RUNTIME / "driver-signal.py"), "--repo", str(self.repo),
                                 "--as-of", "2026-06-30", "--register", str(self.t / "drivers.json")])
        self.assertEqual(rc, 0, out)
        c = json.loads((self.t / "drivers.json").read_text())["computed"]
        self.assertEqual((c["runFolders"], c["reviews"]), (2, "axis/runs/"))


# ---------- W3: bakehouse record commit ----------

class RecordCommit(Project):
    def files(self, rev="HEAD"):
        return sorted(self.git(self.records, "show", "--name-only", "--format=", rev).split("\n"))

    def test_it_commits_only_its_folder_while_another_file_is_dirty(self):
        self.write(self.records, {"ops/note.txt": "one\n"})
        self.git(self.records, "add", "ops/note.txt")
        self.git(self.records, "commit", "-q", "-m", "note")
        self.write(self.records, {"ops/note.txt": "two\n", "loose.txt": "x\n", f"runs/{REC}-other/x.md": "x"})
        self.git(self.records, "add", "ops/note.txt")  # staged by someone else: never committed by record commit
        self.write(self.rec(), {"moves.prediction.json": GOOD_P, "moves.baseline.json": BASE_M})
        rc, out = self.bakehouse("record", "commit", str(self.rec()), "-m", "W-1: prediction",
                                 "-m", "Prediction-approved: d20261110-aaaaaa")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.files(), [f"runs/{REC}/moves.baseline.json", f"runs/{REC}/moves.prediction.json"])
        msg = self.git(self.records, "log", "-1", "--format=%B")
        self.assertEqual(msg, "W-1: prediction\n\nPrediction-approved: d20261110-aaaaaa")
        self.assertEqual(self.git(self.records, "diff", "--cached", "--name-only"), "ops/note.txt")
        self.assertIn("?? loose.txt", self.git(self.records, "status", "--porcelain"))

    def test_it_refuses_a_bad_measured_file_and_commits_nothing(self):
        self.write(self.rec(), {"moves.prediction.json": GOOD_P, "moves.baseline.json": BASE_M})
        self.assertEqual(self.bakehouse("record", "commit", str(self.rec()), "-m", "W-1: prediction")[0], 0)
        head = self.git(self.records, "rev-parse", "HEAD")
        self.write(self.rec(), {"moves.measured.json": FAIL_M})
        rc, out = self.bakehouse("record", "commit", str(self.rec()), "-m", "W-1: measurement")
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSED", out)
        self.assertEqual(self.git(self.records, "rev-parse", "HEAD"), head)
        self.assertEqual(self.git(self.records, "diff", "--cached", "--name-only"), "")
        # a measured file with no prediction of its round is refused as the hook refuses it
        self.write(self.rec(), {"moves.measured.json": PASS_M, "moves.measured.fix9.json": PASS_M})
        rc, out = self.bakehouse("record", "commit", str(self.rec()), "-m", "W-1: measurement")
        self.assertEqual(rc, 1, out)
        self.assertIn("no moves.prediction.fix9.json is in the index beside it", out)

    def test_the_owners_override_works_as_for_the_hook(self):
        self.write(self.rec(), {"moves.prediction.json": GOOD_P, "moves.baseline.json": BASE_M,
                                "moves.measured.json": FAIL_M})
        rc, out = self.bakehouse("record", "commit", str(self.rec()), "-m", "W-1: measurement",
                                 env=dict(self.env, PREDICT_GATE_OVERRIDE="owner go", PREDICT_GATE_TASK="W-1"))
        self.assertEqual(rc, 0, out)
        self.assertIn(f"runs/{REC}/moves.measured.json", self.files())
        over = [e for e in self.events() if e["kind"] == "gate-refused"]
        self.assertEqual(len(over), 1, self.events())
        self.assertIn("owner go", over[0]["note"])

    def test_a_failed_git_commit_leaves_nothing_staged(self):
        hook = Path(self.git(self.repo, "rev-parse", "--path-format=absolute", "--git-path", "hooks")) / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho the project's own hook refuses >&2\nexit 1\n")
        hook.chmod(0o755)
        self.write(self.rec(), {"moves.prediction.json": GOOD_P, "moves.baseline.json": BASE_M})
        rc, out = self.bakehouse("record", "commit", str(self.rec()), "-m", "W-1: prediction")
        self.assertEqual(rc, 1, out)
        self.assertIn("nothing committed", out)
        self.assertEqual(self.git(self.records, "log", "--format=%s"), "records")
        self.assertEqual(self.git(self.records, "diff", "--cached", "--name-only"), "")

    def test_tree_mode_exits_2(self):
        self.bind(dict(BINDING, reviews="axis/runs/"))
        self.write(self.repo, {f"axis/runs/{REC}/moves.prediction.json": GOOD_P})
        rc, out = self.bakehouse("record", "commit", str(self.repo / "axis" / "runs" / REC), "-m", "W-1: p")
        self.assertEqual(rc, 2, out)
        self.assertIn("this project keeps run folders in its code tree; commit them with git", out)

    def test_a_folder_outside_runs_exits_2(self):
        self.write(self.records, {"notes/x.md": "x", "runs/y.md": "y"})
        for folder in (self.records / "notes", self.records / "runs", self.repo / "src", self.t):
            with self.subTest(folder=folder):
                rc, out = self.bakehouse("record", "commit", str(folder), "-m", "W-1: x")
                self.assertEqual(rc, 2, out)
        self.assertEqual(self.git(self.records, "log", "--format=%s"), "records")

    def test_bad_usage_exits_2(self):
        for argv in ((), ("commit",), ("commit", str(self.rec())), ("commit", str(self.rec()), "-m"),
                     ("push", str(self.rec()), "-m", "x"), ("commit", str(self.rec()), "-a", "-m", "x")):
            with self.subTest(argv=argv):
                self.assertEqual(self.bakehouse("record", *argv)[0], 2)

    def test_it_retries_while_the_index_lock_is_held(self):
        self.write(self.rec(), {"moves.prediction.json": GOOD_P, "moves.baseline.json": BASE_M})
        lock = Path(self.git(self.records, "rev-parse", "--path-format=absolute", "--git-path", "index.lock"))
        lock.write_text("")
        threading.Timer(0.5, lock.unlink).start()
        started = time.monotonic()
        rc, out = self.bakehouse("record", "commit", str(self.rec()), "-m", "W-1: prediction")
        self.assertEqual(rc, 0, out)
        self.assertGreater(time.monotonic() - started, 0.4)
        self.assertEqual(len(self.files()), 2)

    def test_a_lock_that_stays_is_reported(self):
        self.write(self.rec(), {"moves.prediction.json": GOOD_P, "moves.baseline.json": BASE_M})
        lock = Path(self.git(self.records, "rev-parse", "--path-format=absolute", "--git-path", "index.lock"))
        lock.write_text("")
        rc, out = self.bakehouse("record", "commit", str(self.rec()), "-m", "W-1: prediction")
        lock.unlink()
        self.assertEqual(rc, 1, out)
        self.assertIn("index.lock", out)
        self.assertEqual(self.git(self.records, "log", "--format=%s"), "records")


class RecordCommitSeparateClone(Project):
    """Backlog 293: a folder in a separate clone on the records branch is named as that, not as a folder in no
    project; a lane clone pointing at the main clone's records worktree still commits there."""

    def test_record_commit_in_a_separate_clone_names_it(self):
        sep = self.t / "repo.sep"
        self.git(self.t, "clone", "-q", "-b", "bakehouse-records", str(self.repo), str(sep))
        folder = sep / "runs" / REC
        self.write(folder, {"moves.prediction.json": GOOD_P, "moves.baseline.json": BASE_M})
        rc, out = self.bakehouse("record", "commit", str(folder), "-m", "W-1: prediction", cwd=sep)
        self.assertEqual(rc, 2, out)
        self.assertIn("separate clone", out)
        self.assertIn("not a records worktree", out)
        self.assertNotIn("no project with a team", out)
        self.assertEqual(self.git(sep, "log", "--format=%s"), "records")

    def test_record_commit_from_a_lane_clone_commits_in_the_main_clones_records_worktree(self):
        lane = self.t / "lane"
        self.git(self.t, "clone", "-q", str(self.repo), str(lane))
        self.git(lane, "config", "bakehouse.records", str(self.records))
        self.write(self.rec(), {"moves.prediction.json": GOOD_P, "moves.baseline.json": BASE_M})
        rc, out = self.bakehouse("record", "commit", str(self.rec()), "-m", "W-1: prediction", cwd=lane)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.git(self.records, "log", "-1", "--format=%s"), "W-1: prediction")


# ---------- the end-to-end run ----------
    # R0.3 D4
    def test_d4_a_refusal_names_the_command_that_ran(self):
        self.write(self.rec(), {"moves.prediction.json": GOOD_P, "moves.baseline.json": BASE_M,
                                "moves.measured.json": FAIL_M})
        rc, out = self.bakehouse("record", "commit", str(self.rec()), "-m", "W-1: measurement")
        self.assertEqual(rc, 1, out)
        self.assertIn('Owner-approved exception only: PREDICT_GATE_OVERRIDE="<reason>" [PREDICT_GATE_TASK=<id>] '
                      "bakehouse record commit <record folder> -m ...", out)
        self.assertNotIn("git commit", out)

class EndToEnd(Project):
    def test_a_work_order_runs_and_closes_with_its_record_on_the_records_branch(self):
        def by_record_commit(msg, names):
            rc, out = self.bakehouse("record", "commit", str(self.rec()), "-m", msg)
            self.assertEqual(rc, 0, out)
            self.assertEqual(sorted(self.git(self.records, "show", "--name-only", "--format=", "HEAD").split("\n")),
                             sorted(f"runs/{REC}/{n}" for n in names))

        self.sealed_flow(by_record_commit)
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 0, out)
        self.assertIn("Result: CLOSED", out)
        self.code_tree_has_no_runs()
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), "")


# ---------- W2: the role hook ----------

class RoleHook(roles_tests.Fixture):
    def setUp(self):
        super().setUp()
        self.bpath.write_text(json.dumps({"owned": True, "tracker": {"id": "^T[0-9]+$"}}))  # records mode
        self.folder = self.records / "runs" / "2026-01-02-T7-step1-review"

    def brief(self, path):
        return f"Work order T7: x.\nRecord folder: {path}/\nBudget: 1k."

    def send(self, st, prompt):
        d = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(self.repo),
             "tool_input": {"subagent_type": st, "description": "x", "prompt": prompt}}
        p = subprocess.run(["python3", str(self.hook)], input=json.dumps(d), capture_output=True, text=True,
                           env=self.env())
        out = json.loads(p.stdout)["hookSpecificOutput"] if p.stdout.strip() else {}
        return "DENY" if out.get("permissionDecision") == "deny" else out.get("updatedInput", {}).get("prompt")

    def engineer_stop(self, prompt, files, seal=True):
        t = self.repo / "agent.jsonl"
        t.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
        self.folder.mkdir(parents=True, exist_ok=True)
        written = []
        for name, text in files.items():
            (self.folder / name).write_text(text)
            written.append(self.folder / name)
        if seal:
            roles_tests.seal_files(self.ledger, written)
        d = {"hook_event_name": "SubagentStop", "agent_type": "engineer", "agent_transcript_path": str(t),
             "cwd": str(self.repo)}
        return subprocess.run(["python3", str(self.hook)], input=json.dumps(d), capture_output=True, text=True,
                              env=self.env()).stdout

    def dispatched(self):
        lines = (self.machine / "logs" / "prediction-dispatch.jsonl").read_text().splitlines()
        return json.loads(lines[-1]).get("record")

    def test_a_folder_under_runs_is_decided_at_dispatch_and_checked_at_the_stop(self):
        prompt = self.send("role:change:engineer", self.brief(self.folder))
        self.assertNotEqual(prompt, "DENY")
        self.assertIn(f"your record folder {self.folder}/ must hold the prediction", prompt)
        rec = self.dispatched()
        self.assertEqual((rec["kind"], rec["root"], rec["folder"]),
                         ("repo", str(self.records), "runs/2026-01-02-T7-step1-review"))
        out = self.engineer_stop(prompt, {"prediction.md": "moves, in prose"})
        self.assertIn('"decision": "block"', out)
        self.assertIn("runs/2026-01-02-T7-step1-review holds no moves.prediction.json", out)
        ok = json.dumps({"moves": [{"what": "tests:passed", "range": [10, 12]}]})
        self.assertEqual(self.engineer_stop(prompt, {"moves.prediction.json": ok}), "")
        # an unsealed moves file in the records worktree is found through its project's ledger
        out = self.engineer_stop(prompt, {"moves.prediction.json": ok + " "}, seal=False)
        self.assertIn("moves file(s) with no seal in the ops ledger", out)

    def test_a_folder_in_the_records_worktree_outside_runs_is_refused(self):
        self.assertEqual(self.send("role:change:engineer", self.brief(self.records / "notes" / "x")), "DENY")
        self.assertEqual(self.send("role:change:engineer", self.brief(self.records / "runs")), "DENY")

    def test_a_folder_in_a_tree_mode_projects_records_or_in_an_unnamed_worktree_is_refused(self):
        theirs = roles_tests.project(Path(self.tmp.name).resolve() / "theirs", {"owned": True, "reviews": "records/"})
        self.assertEqual(self.send("role:change:engineer", self.brief(theirs / "runs" / "x")), "DENY")
        # a worktree on another branch, which no records pointer names
        side = Path(self.tmp.name).resolve() / "side"
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "-q", "-b", "side", str(side)],
                       check=True, capture_output=True, env=self.env())
        self.assertEqual(self.send("role:change:engineer", self.brief(side / "runs" / "x")), "DENY")

    def test_a_folder_under_another_records_mode_projects_runs_is_judged_by_that_project(self):
        # the path's own project judges it, as in tree mode (PM ruling, fix round 1): accepted under its rules
        theirs = roles_tests.project(Path(self.tmp.name).resolve() / "theirs", {"owned": True})
        folder = theirs / "runs" / "2026-01-02-T7-step1-review"
        self.assertNotEqual(self.send("role:change:engineer", self.brief(folder)), "DENY")
        rec = self.dispatched()
        self.assertEqual((rec["kind"], rec["root"], rec["folder"]),
                         ("repo", str(theirs), "runs/2026-01-02-T7-step1-review"))
        self.assertEqual(self.send("role:change:engineer", self.brief(theirs / "notes" / "x")), "DENY")

    def test_a_folder_in_the_code_tree_is_refused_in_records_mode(self):
        self.assertEqual(self.send("role:change:engineer", self.brief(self.repo / "axis" / "runs" / "x")), "DENY")

    def test_the_reviews_placeholder_is_the_full_records_path(self):
        self.personal({"code-review": {"providers": ["design-author"], "prompt_append": "create {reviews}<date>-x/"}})
        self.assertIn(f"create {self.records}/runs/<date>-x/", self.dispatch("design-author")["prompt"])

    # R0.3 D5
    def reason(self, prompt):
        d = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(self.repo),
             "tool_input": {"subagent_type": "role:change:engineer", "description": "x", "prompt": prompt}}
        p = subprocess.run(["python3", str(self.hook)], input=json.dumps(d), capture_output=True, text=True,
                           env=self.env())
        out = json.loads(p.stdout)["hookSpecificOutput"]
        self.assertEqual(out.get("permissionDecision"), "deny", out)
        return out["permissionDecisionReason"]

    def test_d5_a_refused_record_folder_names_the_records_runs_folder(self):
        for path in (self.repo / "axis" / "runs" / "x", self.records / "notes" / "x"):
            with self.subTest(path=path):
                why = self.reason(self.brief(path))
                self.assertIn("which is not under a project's records runs/ folder", why)
                self.assertIn(f"a folder under the records runs/ folder, {self.records}/runs/", why)
                self.assertNotIn("reviews folder", why)

# ---------- W2: preflight checks `runs` ----------

class Preflight(preflight_tests.Fixture):
    NOTE = 'add "runs": "tree" to say so'

    def test_reviews_with_no_runs_is_tree_mode_and_says_so(self):
        self.owned()
        code, out = self.pre("health")
        self.assertEqual(code, 0, out)
        self.assertIn(self.NOTE, out)

    def test_runs_tree_beside_reviews_needs_no_note(self):
        self.owned(runs="tree")
        code, out = self.pre("health")
        self.assertEqual(code, 0, out)
        self.assertNotIn(self.NOTE, out)

    def test_no_runs_and_no_reviews_is_records_mode(self):
        self.owned()
        b = json.loads(self.binding.read_text())
        del b["reviews"]
        self.binding.write_text(json.dumps(b))
        code, out = self.pre("health")
        self.assertEqual(code, 0, out)
        self.assertNotIn(self.NOTE, out)

    def test_runs_records_beside_reviews_fails(self):
        self.owned(runs="records")
        code, out = self.pre("health")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL check 2:", out)
        self.assertIn("runs", out)

    def test_another_runs_value_fails(self):
        for bad in ("both", None, 3):
            with self.subTest(runs=bad):
                self.owned(runs=bad)
                code, out = self.pre("health")
                self.assertEqual(code, 1, out)
                self.assertIn("FAIL check 2:", out)


# ---------- R0.3 (item 9) ----------

class R03ColleagueCommit(Project):
    """T1: a colleague commits code under the code roots without bakehouse, between the measured seal and close.
    Close is not changed. Each test asserts what close does today, and its name says so."""

    def test_t1_today_close_refuses_a_colleagues_commit_on_the_same_branch_after_the_measured_seal(self):
        self.sealed_flow()
        self.commit("tidy the helper", {"src/helper.py": "h = 1\n"})
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 1, out)
        self.assertIn("is on HEAD's own line, and the code at HEAD differs from the code it measured: code came "
                      "after the latest measurement; measure it in a new round", out)
        self.assertIn("Result: REFUSED", out)
        self.assertEqual([e for e in self.events() if e["kind"] == "work-order-closed"], [])

    def test_t1_today_close_refuses_a_colleagues_branch_merged_in_after_the_measured_seal(self):
        base = self.sealed_flow()
        self.git(self.repo, "checkout", "-q", "-b", "colleague", base)
        self.commit("tidy the helper", {"src/helper.py": "h = 1\n"})
        self.git(self.repo, "checkout", "-q", "main")
        w = self.when()
        self.git(self.repo, "merge", "-q", "--no-ff", "-m", "Merge the colleague's branch", "colleague",
                 env=dict(self.env, GIT_AUTHOR_DATE=w, GIT_COMMITTER_DATE=w))
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 1, out)
        self.assertIn("is on HEAD's own line, and the code at HEAD differs from the code it measured: code came "
                      "after the latest measurement; measure it in a new round", out)
        self.assertIn("Result: REFUSED", out)
        self.assertEqual([e for e in self.events() if e["kind"] == "work-order-closed"], [])

    # fix round 1: the unsealed half. An unsealed close judges only the code commits whose subject names the work
    # order, so the colleague's commit does not stop it. Sealed and unsealed close disagree: the owner's call.
    def test_t1_today_an_unsealed_work_order_closes_with_a_colleagues_commit_on_the_same_branch(self):
        self.unsealed_flow()
        self.commit("tidy the helper", {"src/helper.py": "h = 1\n"})
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 0, out)
        self.assertIn("Result: CLOSED", out)
        self.assertEqual(len([e for e in self.events() if e["kind"] == "work-order-closed"]), 1)

    def test_t1_today_an_unsealed_work_order_closes_with_a_colleagues_branch_merged_in(self):
        self.unsealed_flow()
        self.git(self.repo, "checkout", "-q", "-b", "colleague", "HEAD~1")
        self.commit("tidy the helper", {"src/helper.py": "h = 1\n"})
        self.git(self.repo, "checkout", "-q", "main")
        w = self.when()
        self.git(self.repo, "merge", "-q", "--no-ff", "-m", "Merge the colleague's branch", "colleague",
                 env=dict(self.env, GIT_AUTHOR_DATE=w, GIT_COMMITTER_DATE=w))
        self.assertEqual(self.git(self.repo, "show", "-s", "--format=%P", "HEAD").count(" "), 1)
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 0, out)
        self.assertIn("Result: CLOSED", out)
        self.assertEqual(len([e for e in self.events() if e["kind"] == "work-order-closed"]), 1)

    def test_t1_with_no_colleague_commit_the_same_work_order_closes(self):
        self.sealed_flow()
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 0, out)
        self.assertIn("Result: CLOSED", out)


if __name__ == "__main__":
    unittest.main()
