"""Tests for runtime/team-preflight.py. Standard library only: python3 -m unittest discover runtime/tests"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "team-preflight.py"
RECIPE = """steps:
  - id: build
    command: true
  - id: signal
    command: >-
      true --flag
gates:
  - id: tests
    command: true
  - id: review
    command: a careful read of the diff by a second agent
other: 1
"""


def run(*args, env=None, script=SCRIPT):
    p = subprocess.run(["python3", str(script), *args], capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.repo = t / "repo"
        (self.repo / "docs").mkdir(parents=True)
        (self.repo / "reviews").mkdir()
        (self.repo / "docs" / "TRACKER.md").write_text("x\n")
        (self.repo / "recipe.yaml").write_text(RECIPE)
        (self.repo / "recipe.json").write_text(json.dumps({"gates": [{"id": "tests", "command": "true"}]}))
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        # a scratch project: .team/binding.json and a records worktree named by git config bakehouse.records
        (self.repo / ".team").mkdir()
        self.binding = self.repo / ".team" / "binding.json"
        for args in (("worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(t / "repo.team-records")),
                     ("config", "bakehouse.records", str(t / "repo.team-records"))):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        # scratch install: results never depend on this machine's agents, plugins or tables
        (t / "home").mkdir()
        self.env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        self.env.update(HOME=str(t / "home"), BAKEHOUSE_MACHINE_DIR=str(t / "machine"))

    def tearDown(self):
        self.tmp.cleanup()

    def owned(self, **over):
        b = {"owned": True, "register": "shape.yaml", "signal": "recipe.yaml#steps.signal",
             "tracker": {"file": "docs/TRACKER.md", "id": "^T[0-9]+$"},
             "measure": {"commands": ["echo {register} {part}"]},
             "gates": {"byteIdentical": "recipe.yaml#gates",
                       "predictedMoves": {"gates": "recipe.json#gates.tests", "prediction": "moves.prediction.json"}},
             "reviews": "reviews/", "reviewName": "{date}-{id}-step{n}-review",
             "branches": {"refactor": "r/{id}-{n}-{name}", "change": "f/{id}-{name}"}}
        b.update(over)
        self.binding.write_text(json.dumps(b))

    def other(self):
        self.binding.write_text(json.dumps({"owned": False, "approver": "the repository's PM",
                                            "register": "h.json", "signal": "recipe.yaml#steps.build"}))

    def pre(self, *job, script=SCRIPT):
        return run(*job, "--repo", str(self.repo), env=self.env, script=script)

    def runtime(self, without=()):
        """A copy of runtime/, so a test can add or remove a file beside team-preflight.py."""
        rt = Path(self.tmp.name) / "rt"
        shutil.copytree(SCRIPT.parent, rt, ignore=shutil.ignore_patterns("tests", "__pycache__", "logs", *without))
        return rt


class TestOwned(Fixture):
    def test_valid_binding_passes_and_lists_prose_gate(self):
        self.owned()
        code, out = self.pre("health")
        self.assertEqual(code, 0, out)
        self.assertIn("prose gates, run by the reviewer: review", out)
        self.assertNotIn("tests", out.split("prose gates")[1].split("\n")[0])

    def test_job_reports_roles(self):
        self.owned()
        code, out = self.pre("refactor", "engine")
        self.assertEqual(code, 0, out)
        self.assertIn("role refactor:reviewer -> ", out)

    def test_binding_role_override_wins(self):
        self.owned(roles={"refactor:engineer": "general-purpose"})
        code, out = self.pre("refactor", "engine")
        self.assertIn("role refactor:engineer -> general-purpose (from binding roles", out)

    def test_uninstalled_override_is_skipped_and_logged(self):
        self.owned(roles={"engineer": "no-such-agent"})
        code, out = self.pre("change", "T1")
        self.assertEqual(code, 0, out)
        self.assertIn("names ['no-such-agent'], not installed; skipped", out)

    def test_renamed_path_fails_and_names_it(self):
        self.owned(tracker={"file": "docs/TRACKER-renamed.md", "id": "^T[0-9]+$"})
        code, out = self.pre("health")
        self.assertEqual(code, 1)
        self.assertIn("path field 'tracker.file' = 'docs/TRACKER-renamed.md' does not exist", out)

    def test_placeholder_path_checks_prefix(self):
        self.owned(leaveAlone=["docs/{id}.md"])
        self.assertEqual(self.pre("health")[0], 0)
        self.owned(leaveAlone=["gone/{id}.md"])
        self.assertIn("'leaveAlone' = 'gone/{id}.md' does not exist", self.pre("health")[1])

    def test_broken_yaml_pointer_fails_and_names_it(self):
        self.owned(signal="recipe.yaml#steps.renamed-step")
        code, out = self.pre("health")
        self.assertEqual(code, 1)
        self.assertIn("pointer 'signal' = 'recipe.yaml#steps.renamed-step': no item 'id: renamed-step' under 'steps'", out)

    def test_yaml_item_under_another_key_does_not_count(self):
        self.owned(signal="recipe.yaml#gates.build")
        self.assertIn("no item 'id: build' under 'gates'", self.pre("health")[1])

    def test_missing_top_level_key_and_json_pointer(self):
        self.owned(gates={"byteIdentical": "recipe.yaml#nokey",
                          "predictedMoves": {"gates": "recipe.json#gates.nope", "prediction": "moves.prediction.json"}})
        code, out = self.pre("health")
        self.assertEqual(code, 1)
        self.assertIn("no top-level 'nokey:' line in recipe.yaml", out)
        self.assertIn("no item 'id: nope' under 'gates' in recipe.json", out)

    def test_measure_command_pointer_is_checked(self):
        self.owned(measure={"commands": ["missing.yaml#steps"]})
        self.assertIn("pointer 'measure.commands.0' = 'missing.yaml#steps': file 'missing.yaml' does not exist",
                      self.pre("health")[1])

    def test_required_field_missing(self):
        self.owned(branches={"refactor": "r/{id}"})
        code, out = self.pre("health")
        self.assertEqual(code, 1)
        self.assertIn("required field 'branches.change' is missing", out)

    def test_prediction_must_be_a_machine_file(self):
        gates = {"byteIdentical": "recipe.yaml#gates", "predictedMoves": {"gates": "recipe.json#gates.tests"}}
        for name in ("prediction.md", "p.md", "moves.prediction.txt", "records/moves.prediction.json"):
            self.owned(gates={**gates, "predictedMoves": {**gates["predictedMoves"], "prediction": name}})
            code, out = self.pre("change", "T1")
            self.assertEqual(code, 1, (name, out))
            self.assertIn(f"'gates.predictedMoves.prediction' = \"{name}\" is not a file the predicted-moves "
                          "gate reads; name moves.prediction.json", out)
        for name in ("moves.prediction.json", "moves.prediction.yaml"):
            self.owned(gates={**gates, "predictedMoves": {**gates["predictedMoves"], "prediction": name}})
            code, out = self.pre("health")
            self.assertEqual(code, 0, (name, out))

    def test_records_may_add_to_the_contracts_never_remove(self):
        self.owned(records={"since": "2026-01-01", "jobs": {"change": {"require": [{"id": "gates", "glob": "gates*.md"}]}}})
        code, out = self.pre("health")
        self.assertEqual(code, 0, out)
        for rec, why in (({"jobs": {"change": {"remove": ["result"]}}}, "never change or remove its defaults"),
                         ({"jobs": {"change": {"require": [{"id": "result", "glob": "r.md"}]}}},
                          "replaces the default artefact 'result'")):
            self.owned(records=rec)
            code, out = self.pre("health")
            self.assertEqual(code, 1, (rec, out))
            self.assertIn("FAIL check 2: 'records.jobs.change", out)
            self.assertIn(why, out)

    def test_c2_check_2_reads_records_code_roots(self):
        self.owned(records={"codeRoots": ["src/"]})
        code, out = self.pre("health")
        self.assertNotIn("records", out)
        for roots in ("src/", [], ["../up"]):
            with self.subTest(roots=roots):
                self.owned(records={"codeRoots": roots})
                code, out = self.pre("health")
                self.assertEqual(code, 1, out)
                self.assertIn("FAIL check 2: 'records.codeRoots' = ", out)

    def test_records_since_in_the_future_is_refused(self):
        self.owned(records={"since": "2999-01-01"})
        code, out = self.pre("health")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL check 2: 'records.since' = \"2999-01-01\" is in the future", out)

    def test_unknown_placeholder_fails_regex_fields_exempt(self):
        self.owned(reviewName="{date}-{ticket}", tracker={"file": "docs/TRACKER.md", "id": "^T[0-9]{2}$"})
        code, out = self.pre("health")
        self.assertEqual(code, 1)
        self.assertIn("'reviewName' uses placeholder '{ticket}'", out)
        self.assertNotIn("tracker.id", out)

    def test_unparsable_binding(self):
        self.binding.write_text("{not json")
        code, out = self.pre("health")
        self.assertEqual(code, 1)
        self.assertIn("check 2", out)

    def test_missing_binding(self):
        none = str(self.binding.parent / "none.json")
        code, out = run("health", "--repo", str(self.repo), "--binding", none, env=self.env)
        self.assertEqual(code, 1)
        self.assertIn("FAIL check 1", out)
        self.assertEqual(run("startup", "--repo", str(self.repo), "--binding", none, env=self.env)[0], 0)
        code, out = self.pre("health")
        self.assertEqual(code, 1)
        self.assertIn("FAIL check 1: ", out)
        self.assertIn("has no team", out)
        code, out = self.pre("startup")
        self.assertEqual(code, 0)
        self.assertIn("has no team", out)

    def test_empty_outside_and_non_string_paths_fail(self):
        for bad, msg in (("", "= \"\" is not a path"), ("../x", "'reviews' = '../x' is outside"),
                         ("/etc", "'reviews' = '/etc' is outside")):
            self.owned(reviews=bad)
            code, out = self.pre("health")
            self.assertEqual(code, 1, bad)
            self.assertIn(msg, out)
        self.owned(leaveAlone=[{"x": 1}])
        code, out = self.pre("health")
        self.assertEqual(code, 1)
        self.assertIn("FAIL check 3: path field 'leaveAlone' = {\"x\": 1} is not a path", out)
        self.assertNotIn("Traceback", out)

    def test_angle_placeholder_in_binding_fails(self):
        self.owned(measure={"commands": ["echo <touched files>"]})
        code, out = self.pre("health")
        self.assertEqual(code, 1)
        self.assertIn("'measure.commands.0' uses a '<...>' placeholder", out)

    def test_role_check_reads_the_next_table_before_the_swap(self):
        rt = self.runtime()
        (rt / "agent-roles.json").write_text(json.dumps({"roles": {"old-role": {"providers": ["Plan"]}}}))
        (rt / "agent-roles.next.json").write_text(json.dumps({"roles": {"engineer": {"providers": ["Explore"]}}}))
        script = rt / "team-preflight.py"
        self.owned()
        self.assertIn("role refactor:engineer -> Explore (from runtime/agent-roles.next.json",
                      self.pre("refactor", "engine", script=script)[1])
        (rt / "agent-roles.next.json").unlink()
        self.assertIn("role refactor:engineer -> the session does it", self.pre("refactor", "engine", script=script)[1])

    def test_job_needs_its_argument(self):
        self.owned()
        self.assertEqual(self.pre("refactor")[0], 2)


class TestMutationBlock(Fixture):
    """T9: the binding's optional `mutation` block (runtime/mutate.py) is checked at check 2 (its shape)
    and check 3 (each test path it names exists in the checkout)."""

    def test_a_bad_mutation_block_fails_check_2(self):
        (self.repo / "tests").mkdir()
        self.owned(mutation={"command": ["python3", "-m", "pytest"], "tests": [{"files": "src/*", "tests": ["tests"]}]})
        code, out = self.pre("startup")
        self.assertEqual(code, 0, out)
        self.owned(mutation={"command": "pytest", "timeout": -1, "tests": [{"files": "src/*"}]})
        code, out = self.pre("startup")
        self.assertEqual(code, 1, out)
        fails = [x for x in out.split("\n") if x.startswith("FAIL ")]
        self.assertEqual(len(fails), 3, out)
        self.assertTrue(all(x.startswith("FAIL check 2: 'mutation") for x in fails), out)

    def test_a_mutation_test_path_that_does_not_exist_fails_check_3(self):
        self.owned(mutation={"command": ["{root}/.venv/bin/python", "-m", "pytest"],
                             "tests": [{"files": "src/*", "tests": ["tests/no-such-folder"]}]})
        code, out = self.pre("startup")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL check 3: path field 'mutation.tests[0].tests' = 'tests/no-such-folder' does not exist", out)

    def test_a_runner_that_fails_to_load_stops_only_the_mutation_check(self):
        rt = self.runtime()
        (rt / "mutate.py").write_text("raise RuntimeError('broken runner')\n")
        (self.repo / "tests").mkdir()
        self.owned()
        p = subprocess.run(["python3", str(rt / "team-preflight.py"), "startup", "--repo", str(self.repo),
                            "--binding", str(self.binding)], capture_output=True, text=True, env=self.env, check=False)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.owned(mutation={"command": ["python3"], "tests": [{"files": "src/*", "tests": ["tests"]}]})
        p = subprocess.run(["python3", str(rt / "team-preflight.py"), "startup", "--repo", str(self.repo),
                            "--binding", str(self.binding)], capture_output=True, text=True, env=self.env, check=False)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("runtime/mutate.py fails to load (RuntimeError: broken runner)", p.stdout)

    def test_a_mutation_link_target_that_does_not_exist_fails_check_3(self):
        (self.repo / "tests").mkdir()
        self.owned(mutation={"command": ["python3"], "links": {"data": "../outside"},
                             "tests": [{"files": "src/*", "tests": ["tests"]}]})
        code, out = self.pre("startup")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL check 2: 'mutation.links' must be an object", out)
        self.owned(mutation={"command": ["python3"], "links": {"data": "no-such-data"},
                             "tests": [{"files": "src/*", "tests": ["tests"]}]})
        code, out = self.pre("startup")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL check 3: path field 'mutation.links.data' = 'no-such-data' does not exist", out)
        self.owned(mutation={"command": ["python3"], "links": {"data": "tests"},
                             "tests": [{"files": "src/*", "tests": ["tests"]}]})
        code, out = self.pre("startup")
        self.assertEqual(code, 1, out)
        self.assertIn("'mutation.links.data' = 'tests' is not the top of a git repository", out)


class TestApproval(Fixture):
    """Move 1 gate: owned false - no record fails, unconsumed same scope passes, consumed fails."""

    def approvals(self, records):
        (self.binding.parent / "approvals.json").write_text(json.dumps(records))

    def rec(self, scope, consumed=""):
        return {"proposal": "P1", "approver": "the repository's PM", "date": "2026-10-05",
                "scope": scope, "consumed": consumed}

    def test_no_approvals_file_fails(self):
        self.other()
        code, out = self.pre("refactor", "engine")
        self.assertEqual(code, 1)
        self.assertIn("check 6", out)

    def test_no_matching_record_fails(self):
        self.other()
        self.approvals([self.rec({"part": "other"}), self.rec({"workOrder": "engine"})])
        code, out = self.pre("refactor", "engine")
        self.assertEqual(code, 1)
        self.assertIn('holds no approval with scope {"part": "engine"}', out)

    def test_unconsumed_matching_record_passes(self):
        self.other()
        self.approvals([self.rec({"part": "engine"})])
        self.assertEqual(self.pre("refactor", "engine")[0], 0)
        self.approvals([self.rec({"workOrder": "T-1"})])
        self.assertEqual(self.pre("change", "T-1")[0], 0)

    def test_superset_scope_does_not_match(self):
        self.other()
        self.approvals([self.rec({"part": "engine", "workOrder": "T-1"})])
        self.assertEqual(self.pre("refactor", "engine")[0], 1)
        self.assertEqual(self.pre("change", "T-1")[0], 1)

    def test_consumed_record_fails(self):
        self.other()
        self.approvals([self.rec({"part": "engine"}, consumed="https://example.invalid/pr/1")])
        code, out = self.pre("refactor", "engine")
        self.assertEqual(code, 1)
        self.assertIn("is consumed", out)

    def test_health_skips_approval(self):
        self.other()
        self.assertEqual(self.pre("health")[0], 0)

    def test_owned_false_needs_approver(self):
        self.binding.write_text(json.dumps({"owned": False, "register": "h.json", "signal": "true"}))
        self.assertIn("required field 'approver' is missing", self.pre("health")[1])


class TestSignalRules(Fixture):
    def test_generic_script_rules(self):
        (self.repo / "src").mkdir()
        (self.repo / "src" / "a.py").write_text("x\n")
        g = ["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(g + ["add", "-A"], check=True)
        subprocess.run(g + ["commit", "-qm", "init"], check=True)
        rt = self.runtime(without=("health-signal.py",))
        env, script = self.env, rt / "team-preflight.py"
        sig = {"script": "health-signal", "ref": "HEAD", "fix": "^fix", "codeRoots": ["src/"],
               "part": "^(src)/"}
        self.binding.write_text(json.dumps({"owned": False, "approver": "PM", "register": "h.json", "signal": sig}))
        args = ("health", "--repo", str(self.repo))
        for job in ("health", "startup"):
            code, out = run(job, *args[1:], env=env, script=script)
            self.assertEqual(code, 0, out)
            self.assertIn("note check 4: the generic signal script is not installed yet", out)
        (self.binding.parent / "approvals.json").write_text(json.dumps(
            [{"scope": {"part": "src"}, "consumed": ""}, {"scope": {"workOrder": "T-1"}, "consumed": ""}]))
        code, out = run("change", "T-1", *args[1:], env=env, script=script)
        self.assertEqual(code, 0, out)
        self.assertIn("note check 4: the generic signal script is not installed yet", out)
        code, out = run("refactor", "src", *args[1:], env=env, script=script)
        self.assertEqual(code, 1)
        self.assertIn("FAIL check 4: the generic signal script is not installed yet", out)
        (rt / "health-signal.py").write_text("")
        code, out = run(*args, env=env, script=script)
        self.assertEqual(code, 0)
        self.assertNotIn("not installed", out)
        self.assertEqual(run("refactor", "src", *args[1:], env=env, script=script)[0], 0)
        sig.update(ref="nope", fix="(")
        self.binding.write_text(json.dumps({"owned": False, "approver": "PM", "register": "h.json", "signal": sig}))
        out = run(*args, env=env, script=script)[1]
        self.assertIn("signal.fix does not compile", out)
        self.assertIn("signal.ref 'nope' does not resolve", out)
        sig.update(ref="HEAD", fix="^fix", codeRoots=["src/", "lib/"])
        self.binding.write_text(json.dumps({"owned": False, "approver": "PM", "register": "h.json", "signal": sig}))
        self.assertIn("signal.codeRoots 'lib/' does not exist at HEAD", run(*args, env=env, script=script)[1])
        del sig["part"]
        sig.update(codeRoots=["src/"])
        self.binding.write_text(json.dumps({"owned": False, "approver": "PM", "register": "h.json", "signal": sig}))
        self.assertIn("signal has no part rule", run(*args, env=env, script=script)[1])
        sig.update(parts=[{"kind": "x", "match": "^src/"}, {"match": "("}])
        self.binding.write_text(json.dumps({"owned": False, "approver": "PM", "register": "h.json", "signal": sig}))
        out = run(*args, env=env, script=script)[1]
        self.assertIn("signal.parts[0].match needs one group", out)
        self.assertIn("signal.parts[1].match is missing or does not compile", out)
        # the documented id template, a quantifier and a named group in a match, a finding pattern: all fine
        sig.update(parts=[{"kind": "config", "match": "^src/(?P<n>[a-z]{1,}[^/]*)/", "id": "config/{1}"}],
                   reviewFinding="\\bP[01]{1}\\b")
        self.binding.write_text(json.dumps({"owned": False, "approver": "PM", "register": "h.json", "signal": sig}))
        code, out = run(*args, env=env, script=script)
        self.assertEqual(code, 0, out)
        sig.update(parts=[{"match": "^(src)/", "id": "config/{part}"}])
        self.binding.write_text(json.dumps({"owned": False, "approver": "PM", "register": "h.json", "signal": sig}))
        self.assertIn("may only use group numbers such as '{1}', got '{part}'", run(*args, env=env, script=script)[1])


if __name__ == "__main__":
    unittest.main()
