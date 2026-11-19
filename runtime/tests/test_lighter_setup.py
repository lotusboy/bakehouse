"""Tests for item 8: `register` and `signal` are optional for `/bakehouse:change`. The code roots come from one
place, locator.code_roots: the binding's signal.codeRoots, else records.codeRoots. Close, both seal paths and
records-check read them there; preflight `change` refuses a binding that names none. A binding that names
signal.codeRoots behaves as before; the existing suite guards that. Standard library only; every test builds
scratch repositories and nothing outside a temporary folder is read or written.
python3 -m unittest runtime/tests/test_lighter_setup.py
"""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(TESTS))
import locator  # noqa: E402
import test_run_folders as folders  # noqa: E402
import test_team_preflight as preflight_tests  # noqa: E402

NO_ROOTS = "name the code folders in records.codeRoots (or signal.codeRoots)"
# a change-only binding: no register, no signal; the code roots in records.codeRoots
LIGHT = {"owned": True, "reviewName": "{date}-{id}-step{n}-review",
         "tracker": {"file": "T.md", "id": "^W-[0-9]+$"}, "records": {"codeRoots": ["src/"]}}


# ---------- W1: one answer for the code roots ----------

class CodeRoots(unittest.TestCase):
    def test_signal_code_roots_win(self):
        b = {"signal": {"codeRoots": ["src/"]}, "records": {"codeRoots": ["lib/"]}}
        self.assertEqual(locator.code_roots(b), ["src/"])

    def test_records_code_roots_when_the_signal_names_none(self):
        for sig in (None, "python3 tools/health.py", "recipe.yaml#steps.signal", {"script": "health-signal"},
                    {"codeRoots": []}):
            with self.subTest(signal=sig):
                b = {"records": {"codeRoots": ["lib/"]}}
                if sig is not None:
                    b["signal"] = sig
                self.assertEqual(locator.code_roots(b), ["lib/"])

    def test_none_when_the_binding_names_no_code_roots(self):
        for b in ({}, {"signal": "true"}, {"signal": {"codeRoots": []}}, {"records": {"since": "2026-01-01"}},
                  {"records": {"codeRoots": []}}, {"records": "src/"}):
            with self.subTest(binding=b):
                self.assertIsNone(locator.code_roots(b))


# ---------- W2: preflight ----------

class Preflight(preflight_tests.Fixture):
    def light(self, **over):
        self.owned(**{"runs": "tree", "records": {"codeRoots": ["docs/"]}, **over})
        b = json.loads(self.binding.read_text())
        for k in ("register", "signal"):
            if k not in over:
                b.pop(k, None)
        self.binding.write_text(json.dumps(b))

    def test_change_passes_with_no_register_and_no_signal(self):
        self.light()
        code, out = self.pre("change", "T1")
        self.assertEqual(code, 0, out)
        self.assertNotIn("FAIL", out)

    def test_refactor_and_health_still_need_register_and_signal(self):
        self.light()
        for job in (("refactor", "engine"), ("health",)):
            with self.subTest(job=job):
                code, out = self.pre(*job)
                self.assertEqual(code, 1, out)
                self.assertIn("FAIL check 2: required field 'register' is missing", out)
                self.assertIn("FAIL check 2: required field 'signal' is missing", out)

    def test_startup_is_quiet(self):
        self.light()
        code, out = self.pre("startup")
        self.assertEqual(code, 0, out)
        self.assertNotIn("FAIL", out)
        self.assertNotIn("register", out)
        self.assertNotIn("signal", out)

    def test_change_refuses_a_binding_with_no_code_roots(self):
        self.light(records={"since": "2026-01-01"})
        code, out = self.pre("change", "T1")
        self.assertEqual(code, 1, out)
        self.assertIn(f"FAIL check 2: the binding names no code roots: {NO_ROOTS}", out)
        # a command or pointer signal holds no code roots either
        self.owned(runs="tree")
        code, out = self.pre("change", "T1")
        self.assertEqual(code, 1, out)
        self.assertIn(NO_ROOTS, out)

    def test_only_change_asks_for_code_roots(self):
        self.owned(runs="tree")  # a pointer signal and no records.codeRoots, as before item 8
        for job in (("refactor", "engine"), ("health",), ("startup",)):
            with self.subTest(job=job):
                code, out = self.pre(*job)
                self.assertEqual(code, 0, out)
                self.assertNotIn(NO_ROOTS, out)

    def test_a_present_register_and_signal_are_checked_as_before(self):
        self.light(signal="recipe.yaml#steps.renamed-step")
        code, out = self.pre("change", "T1")
        self.assertEqual(code, 1, out)
        self.assertIn("pointer 'signal' = 'recipe.yaml#steps.renamed-step': no item", out)

    def test_a_repo_another_party_owns_runs_change_with_no_register_and_no_signal(self):
        self.binding.write_text(json.dumps({"owned": False, "approver": "the repository's PM",
                                            "records": {"codeRoots": ["docs/"]}}))
        (self.binding.parent / "approvals.json").write_text(json.dumps([{"scope": {"workOrder": "T-1"},
                                                                         "consumed": ""}]))
        code, out = self.pre("change", "T-1")
        self.assertEqual(code, 0, out)
        code, out = self.pre("health")
        self.assertEqual(code, 1, out)
        self.assertIn("required field 'signal' is missing", out)

    # ---- fix 1: preflight checks each records.codeRoots entry, for every job ----

    def test_a_code_root_that_names_no_real_folder_is_refused_for_every_job(self):
        self.owned(runs="tree", records={"codeRoots": ["docs/", "docz/"]})  # with register and signal
        for job in (("change", "T1"), ("refactor", "engine"), ("health",), ("startup",)):
            with self.subTest(job=job):
                code, out = self.pre(*job)
                self.assertEqual(code, 1, out)
                self.assertIn("FAIL check 3: path field 'records.codeRoots' = 'docz/' does not exist", out)
                self.assertNotIn("'records.codeRoots' = 'docs/'", out)

    def test_a_pathspec_or_glob_code_root_is_refused(self):
        for root in (":!docs", "*.nothing", "doc?/", "[d]ocs/"):
            with self.subTest(root=root):
                self.light(records={"codeRoots": [root]})
                code, out = self.pre("change", "T1")
                self.assertEqual(code, 1, out)
                self.assertIn(f"FAIL check 3: path field 'records.codeRoots' = '{root}' is a git pathspec or a "
                              "glob: name a folder", out)

    def test_the_guides_sample_binding_needs_its_src_folder(self):
        doc = (RUNTIME.parent / "docs" / "GETTING-STARTED.md").read_text()
        sample = json.loads(re.search(r"## 5\. Write the binding.*?```json\n(.*?)```", doc, re.S).group(1))
        (self.binding.parent / "gates.json").write_text(json.dumps({"gates": [{"id": "unit", "command": "true"}]}))
        self.binding.write_text(json.dumps(sample))
        code, out = self.pre("change", "T1")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL check 3: path field 'records.codeRoots' = 'src/' does not exist", out)
        (self.repo / "src").mkdir()
        (self.repo / "test").mkdir()
        (self.repo / "test" / "summarise.test.js").write_text("")  # the sample's mutation block names it
        code, out = self.pre("change", "T1")
        self.assertEqual(code, 0, out)

    # ---- fix 3 ----

    def test_an_unowned_binding_with_no_code_roots_is_refused(self):
        self.binding.write_text(json.dumps({"owned": False, "approver": "the repository's PM"}))
        (self.binding.parent / "approvals.json").write_text(json.dumps([{"scope": {"workOrder": "T-1"},
                                                                         "consumed": ""}]))
        code, out = self.pre("change", "T-1")
        self.assertEqual(code, 1, out)
        self.assertIn(f"FAIL check 2: the binding names no code roots: {NO_ROOTS}", out)


# ---------- close, seal, records-check, status and the signal on a change-only binding ----------

class Light(folders.Project):
    binding = LIGHT

    def records_check(self, *args):
        return self.run_tool(["python3", str(RUNTIME / "records-check.py"), "--repo", str(self.repo),
                              "--as-of", "2026-11-10", "--since", "2026-11-01", *args])

    def test_a_clean_record_closes_and_runs_the_code_order_checks(self):
        self.unsealed_flow()
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("code-order checks were not run", out)

    def test_code_committed_after_the_measurement_refuses(self):
        self.unsealed_flow()
        self.commit("W-1: late code", {"src/app.py": "v = 9\n"})
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 1, out)
        self.assertIn("comes after the latest measured file's commit", out)

    def test_a_prediction_committed_after_the_code_refuses(self):
        self.commit("W-1: the change, no prediction yet", {"src/app.py": "v = 2\n"})
        self.rcommit("W-1: record", {"moves.prediction.json": folders.GOOD_P, "moves.baseline.json": folders.BASE_M,
                                     "moves.measured.json": folders.PASS_M})
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 1, out)
        self.assertIn("has no round whose prediction was committed before it", out)

    def test_a_dirty_code_root_refuses(self):
        self.unsealed_flow()
        self.write(self.repo, {"src/app.py": "v = 4\n", "src/new.py": "n = 1\n", "notes.txt": "outside\n"})
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 1, out)
        line = next(x for x in out.split("\n") if "under the code roots" in x)
        self.assertIn("M src/app.py", line)
        self.assertIn("?? src/new.py", line)
        self.assertNotIn("notes.txt", line)

    def test_seal_code_and_the_measured_seal_find_the_code_roots(self):
        self.sealed_flow()
        code = [e for e in self.events() if e["kind"] == "sealed" and e.get("fileKind") == "code"]
        self.assertEqual(len(code), 1)
        self.assertEqual(list(code[0]["codeTrees"]), ["src/"])
        measured = [e for e in self.events() if e["kind"] == "sealed" and e.get("fileKind") == "measured"]
        self.assertEqual(measured[-1]["headTrees"], {"src/": self.git(self.repo, "rev-parse", "HEAD:src")})
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 0, out)
        self.assertIn("sealed: judged by", out)

    def test_records_check_and_status_work(self):
        self.unsealed_flow()
        self.assertEqual(self.gate("close", "W-1")[0], 0)
        rc, out = self.records_check("--no-write")
        self.assertEqual(rc, 0, out)
        self.assertIn("| W-1 | 1 | closed |", out)
        rc, out = self.records_check("--register", str(self.t / "records.json"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads((self.t / "records.json").read_text())["computed"]["codeRoots"], ["src/"])
        rc, out = self.bakehouse("status", "--json")
        self.assertEqual(rc, 0, out)
        self.assertEqual([c["workOrder"] for c in json.loads(out)["closedSince"]], ["W-1"])

    def test_health_signal_refuses_cleanly(self):
        rc, out = self.run_tool(["python3", str(RUNTIME / "health-signal.py"), "--repo", str(self.repo),
                                 "--no-write"])
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn("Traceback", out)
        self.assertIn('the binding\'s "signal" is not rules for this script', out)

    def test_session_start_prints_only_the_pm_line(self):
        p = subprocess.run(["python3", str(RUNTIME / "session-start.py")], input="", capture_output=True, text=True,
                           env=dict(self.env, CLAUDE_PROJECT_DIR=str(self.repo)), cwd=self.repo)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stderr, "")
        self.assertEqual(p.stdout.strip().split("\n"), ["bakehouse: this project has a team. Be its PM: load the "
                                                        "`bakehouse:pm` skill now and follow it for the whole session."])

    def test_a_sealed_close_refuses_code_after_the_measurement(self):
        # fix 2: the sealed rules read the code roots from records.codeRoots too
        self.sealed_flow()
        self.commit("W-1: late code", {"src/app.py": "v = 9\n"})
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 1, out)
        self.assertIn("differs from the code it measured", out)

    def test_code_roots_flag_replaces_the_bindings(self):
        # fix 4
        self.unsealed_flow()
        rc, out = self.records_check("--code-roots", "lib/", "--register", str(self.t / "records.json"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads((self.t / "records.json").read_text())["computed"]["codeRoots"], ["lib/"])


class BadCodeRoots(folders.Project):
    """Fix 5: a malformed records.codeRoots is bad input for close, seal-code and the measured seal: one line, exit 2."""
    binding = LIGHT
    BAD = ("src/", True, [5])

    def bad(self, roots):
        self.bind(dict(LIGHT, records={"codeRoots": roots}))

    def refused(self, rc, out):
        self.assertEqual(rc, 2, out)
        self.assertNotIn("Traceback", out)
        self.assertIn("records.codeRoots", out)

    def test_close_refuses_and_closes_nothing(self):
        self.unsealed_flow()
        for roots in self.BAD:
            with self.subTest(roots=roots):
                self.bad(roots)
                self.refused(*self.gate("close", "W-1"))
                self.assertFalse([e for e in self.events() if e["kind"] == "work-order-closed"])

    def test_seal_code_refuses_and_commits_nothing(self):
        before = self.git(self.repo, "rev-parse", "HEAD")
        for roots in self.BAD:
            with self.subTest(roots=roots):
                self.bad(roots)
                self.refused(*self.seal_code("W-1: the change", {"src/app.py": "v = 2\n"}))
                self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), before)

    def test_the_measured_seal_refuses(self):
        self.write(self.rec(), {"moves.prediction.json": folders.GOOD_P, "moves.baseline.json": folders.BASE_M})
        self.commit_named("W-1: prediction", ("moves.prediction.json", "moves.baseline.json"))
        for n in ("moves.prediction.json", "moves.baseline.json"):
            self.assertEqual(self.seal("first", n)[0], 0)
        base = self.git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(self.seal_code("W-1: the change", {"src/app.py": "v = 2\n"})[0], 0)
        head = self.git(self.repo, "rev-parse", "HEAD")
        self.write(self.rec(), {"moves.measured.json": folders.PASS_M,
                                "measure-report.json": {"base": {"sha": base}, "head": {"sha": head}}})
        seals = len(self.events())
        for roots in self.BAD:
            with self.subTest(roots=roots):
                self.bad(roots)
                self.refused(*self.seal("first", "moves.measured.json"))
                self.assertEqual(len(self.events()), seals)


class BadSignalCodeRoots(BadCodeRoots):
    """0.7.3: a signal.codeRoots that is not a list of folders is bad input for close, seal-code, the measured seal
    and records-check, as a malformed records.codeRoots is: one line naming the field, never a traceback."""
    binding = folders.BINDING
    BAD = (True, "src/", [5])

    def bad(self, roots):
        self.bind(dict(folders.BINDING, signal={"codeRoots": roots}))

    def refused(self, rc, out):
        self.assertEqual(rc, 2, out)
        self.assertNotIn("Traceback", out)
        self.assertIn("signal.codeRoots", out)

    def test_records_check_refuses(self):
        for roots in self.BAD:
            with self.subTest(roots=roots):
                self.bad(roots)
                self.refused(*self.run_tool(["python3", str(RUNTIME / "records-check.py"), "--repo", str(self.repo),
                                             "--no-write"]))


class NoCodeRoots(folders.Project):
    binding = {k: v for k, v in LIGHT.items() if k != "records"}

    def test_seal_code_names_both_fields(self):
        rc, out = self.seal_code("W-1: the change", {"src/app.py": "v = 2\n"})
        self.assertEqual(rc, 2, out)
        self.assertIn("names no signal.codeRoots or records.codeRoots", out)


class BothRoots(folders.Project):
    """signal.codeRoots and a different records.codeRoots: signal.codeRoots wins, as it does today."""
    binding = dict(folders.BINDING, records={"codeRoots": ["lib/"]})

    def test_signal_code_roots_win_in_close_seal_and_records_check(self):
        self.sealed_flow()
        code = [e for e in self.events() if e["kind"] == "sealed" and e.get("fileKind") == "code"]
        self.assertEqual(list(code[0]["codeTrees"]), ["src/"])
        measured = [e for e in self.events() if e["kind"] == "sealed" and e.get("fileKind") == "measured"]
        self.assertEqual(list(measured[-1]["headTrees"]), ["src/"])
        self.write(self.repo, {"lib/x.py": "x\n"})
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 0, out)  # lib/ is not a code root here
        self.write(self.repo, {"src/new.py": "n = 1\n"})
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 1, out)
        self.assertIn("?? src/new.py", out)
        rc, out = self.run_tool(["python3", str(RUNTIME / "records-check.py"), "--repo", str(self.repo),
                                 "--as-of", "2026-11-10", "--since", "2026-11-01",
                                 "--register", str(self.t / "records.json")])
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads((self.t / "records.json").read_text())["computed"]["codeRoots"], ["src/"])


if __name__ == "__main__":
    unittest.main()
