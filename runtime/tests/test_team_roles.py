"""Tests for runtime/team_roles.py and the agent-roles hook. Standard library only.

The hook under test is runtime/agent-roles.py. Each test runs it with a scratch project (a git repository with
.team/binding.json and a records worktree), a scratch machine folder and a scratch HOME, so the installed agents
are exactly the ones the test writes.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNTIME))
import team_roles  # noqa: E402

HOOK = RUNTIME / "agent-roles.py"
# The agents the plugin ships (agents/); the shipped table names them bakehouse:<name>.
SHIPPED = {f.stem for f in (RUNTIME.parent / "agents").glob("*.md")}
GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def project(repo, binding):
    """A scratch project at repo: a git repository with .team/binding.json, and a records worktree beside it
    that `git config bakehouse.records` names. Returns the records folder."""
    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
                       env=dict(os.environ, **GIT))
    repo.mkdir(parents=True, exist_ok=True)
    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "first")
    (repo / ".team").mkdir()
    (repo / ".team" / "binding.json").write_text(json.dumps(binding))
    records = repo.parent / f"{repo.name}.team-records"
    git("worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(records))
    git("config", "bakehouse.records", str(records))
    return records


def seal_files(ledger, paths):
    """V12c Step 2b: the stop check sends back a moves file no seal names. A fixture that writes a moves file
    for a stop that should pass seals it here: one sealed line per file, in the project's ops ledger."""
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as fh:
        for p in paths:
            if Path(p).name.startswith("moves.") and Path(p).is_file():
                fh.write(json.dumps({"kind": "sealed", "task": "T7", "prev": None,
                                     "sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest()}) + "\n")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name).resolve()
        self.home, self.machine = t / "home", t / "machine"
        self.repo, self.other = t / "repo", t / "other"
        for d in (self.home / ".claude" / "agents", self.other):
            d.mkdir(parents=True, exist_ok=True)
        self.hook = HOOK
        self.bin = None  # a folder put first on PATH, holding a test's own git
        self.project_dir = None  # $CLAUDE_PROJECT_DIR, which the harness sets for a plugin hook
        self.install("refactor-engineer", "refactor-reviewer", "architect", "design-author", "absence-check",
                     "blast-radius")
        # self.repo has opted in; self.other has no binding
        self.records = project(self.repo, {"owned": True, "roles": {}})
        self.bpath = self.repo / ".team" / "binding.json"
        self.ledger = self.records / "ops" / "events.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def env(self):
        e = {k: v for k, v in os.environ.items()
             if not k.startswith(("BAKEHOUSE_", "CLAUDE_PROJECT_DIR", "OPS_"))}
        e.update(GIT, HOME=str(self.home), BAKEHOUSE_MACHINE_DIR=str(self.machine))
        if self.bin:
            e["PATH"] = f"{self.bin}:{e['PATH']}"
        if self.project_dir:
            e["CLAUDE_PROJECT_DIR"] = str(self.project_dir)
        return e

    def install(self, *names):
        """Each name as a user agent; a shipped agent also as the bakehouse plugin's, `bakehouse:<name>`, which
        is how the shipped table names it."""
        plugin = self.home / "plugin"
        for n in names:
            (self.home / ".claude" / "agents" / f"{n}.md").write_text(f"---\nname: {n}\n---\n")
            if n in SHIPPED:
                (plugin / "agents").mkdir(parents=True, exist_ok=True)
                (plugin / "agents" / f"{n}.md").write_text(f"---\nname: {n}\n---\n")
        (self.home / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {"bakehouse@t": True}}))
        (self.home / ".claude" / "plugins").mkdir(exist_ok=True)
        (self.home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"plugins": {"bakehouse@t": [{"scope": "user", "installPath": str(plugin)}]}}))

    def uninstall(self, name):
        (self.home / ".claude" / "agents" / f"{name}.md").unlink()
        (self.home / "plugin" / "agents" / f"{name}.md").unlink(missing_ok=True)

    def binding(self, roles):
        self.bpath.write_text(json.dumps({"owned": True, "roles": roles}))

    def personal(self, roles):
        self.machine.mkdir(exist_ok=True)
        (self.machine / "agent-roles.json").write_text(json.dumps({"roles": roles}))

    def dispatch(self, st, cwd=None, **extra):
        d = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(cwd or self.repo),
             "tool_input": {"subagent_type": st, "description": "x", "prompt": "p"}}
        d.update(extra)
        p = subprocess.run(["python3", str(self.hook)], input=json.dumps(d), capture_output=True, text=True,
                           env=self.env())
        self.assertEqual(p.stderr, "")
        if not p.stdout.strip():
            return None
        out = json.loads(p.stdout)["hookSpecificOutput"]
        return "DENY" if out["permissionDecision"] == "deny" else out["updatedInput"]

    def stop(self, **extra):
        t = self.repo / "t.jsonl"
        t.write_text("")
        d = {"hook_event_name": "SubagentStop", "agent_type": "refactor-reviewer",
             "agent_transcript_path": str(t), "cwd": str(self.repo)}
        d.update(extra)
        return subprocess.run(["python3", str(self.hook)], input=json.dumps(d), capture_output=True, text=True,
                              env=self.env()).stdout

    def slow_git(self, body):
        """A git that runs body before it answers --show-toplevel (a sleep: slow, or hung), then is the real git."""
        self.bin = Path(self.tmp.name).resolve() / "bin"
        self.bin.mkdir(exist_ok=True)
        (self.bin / "git").write_text(f'#!/bin/bash\nfor a in "$@"; do [ "$a" = --show-toplevel ] && {body}; done\n'
                                      f'exec {shutil.which("git")} "$@"\n')
        (self.bin / "git").chmod(0o755)

    def log(self):
        return (self.machine / "logs" / "agent-roles.log").read_text()


class TestNoBinding(Fixture):
    """Review 2026-10-11 (H1, H2): with no binding, the shipped table changes nothing by name."""

    def test_shipped_name_is_untouched_without_a_binding(self):
        self.assertIsNone(self.dispatch("architect", cwd=self.other))
        self.assertIsNone(self.dispatch("general-purpose", cwd=self.other, agent_type="refactor-reviewer"))

    def test_shipped_name_is_ruled_with_a_binding(self):
        self.assertEqual(self.dispatch("refactor-engineer")["model"], "opus")

    def test_role_prefix_works_without_a_binding(self):
        self.assertEqual(self.dispatch("role:engineer", cwd=self.other)["subagent_type"], "bakehouse:refactor-engineer")

    def test_stop_asks_nothing_without_a_binding(self):
        self.assertEqual(self.stop(cwd=str(self.other)), "")

    def test_stop_asks_nothing_in_a_repo_the_owner_does_not_own(self):
        self.bpath.write_text(json.dumps({"owned": False}))
        self.assertEqual(self.stop(), "")

    def test_owners_own_table_applies_everywhere(self):
        self.install("plugin-review")
        self.personal({"code-review": {"providers": ["plugin-review"], "model": "sonnet"}})
        self.assertEqual(self.dispatch("plugin-review", cwd=self.other)["model"], "sonnet")

    def test_non_string_model_is_ignored(self):
        self.install("plugin-review")
        self.personal({"code-review": {"providers": ["plugin-review"], "model": 7}})
        self.assertIsNone(self.dispatch("plugin-review"))


class TestGate(Fixture):
    """Move 2's gate (design §7), against the shipped table."""

    def test_engineer_resolves_to_shipped_default(self):
        self.assertEqual(self.dispatch("role:engineer")["subagent_type"], "bakehouse:refactor-engineer")
        self.assertEqual(self.dispatch("role:refactor:engineer")["subagent_type"], "bakehouse:refactor-engineer")

    def test_binding_override_wins_in_its_repo_only(self):
        self.binding({"engineer": "design-author"})
        self.assertEqual(self.dispatch("role:engineer")["subagent_type"], "design-author")
        self.assertEqual(self.dispatch("role:engineer", cwd=self.other)["subagent_type"], "bakehouse:refactor-engineer")

    def test_job_qualified_key_wins_over_plain(self):
        self.binding({"engineer": "architect", "change:engineer": "design-author"})
        self.assertEqual(self.dispatch("role:change:engineer")["subagent_type"], "design-author")
        self.assertEqual(self.dispatch("role:refactor:engineer")["subagent_type"], "architect")

    def test_uninstalled_override_is_skipped_and_logged(self):
        self.binding({"engineer": "no-such-agent"})
        self.assertEqual(self.dispatch("role:engineer")["subagent_type"], "bakehouse:refactor-engineer")
        self.assertIn("SKIP role:engineer: binding roles key 'engineer' names ['no-such-agent']", self.log())

    def test_reviewer_falls_back_to_general_purpose(self):
        self.uninstall("refactor-reviewer")
        self.assertEqual(self.dispatch("role:reviewer")["subagent_type"], "general-purpose")

    def test_code_review_without_personal_is_general_purpose(self):
        out = self.dispatch("role:code-review")
        self.assertEqual((out["subagent_type"], out["model"]), ("general-purpose", "sonnet"))

    def test_code_review_with_personal_uses_its_provider_and_rules(self):
        self.install("plugin-review")
        self.personal({"code-review": {"providers": ["plugin-review"], "model": "sonnet",
                                       "run_record": True, "prompt_append": "Run record (required): x"}})
        out = self.dispatch("role:code-review")
        self.assertEqual((out["subagent_type"], out["model"]), ("plugin-review", "sonnet"))
        self.assertIn("Run record (required): x", out["prompt"])
        self.assertEqual(self.dispatch("plugin-review")["model"], "sonnet")

    def test_unfilled_role_is_denied_and_logged(self):
        self.uninstall("architect")
        self.assertEqual(self.dispatch("role:architect"), "DENY")
        self.assertEqual(self.dispatch("role:tester"), "DENY")
        self.assertEqual(self.dispatch("role:no-such-role"), "DENY")
        self.assertIn("DENY bakehouse agent-roles: role 'architect' is unresolved", self.log())


class TestRules(Fixture):
    def test_direct_dispatch_keeps_role_rules(self):
        self.assertEqual(self.dispatch("refactor-engineer")["model"], "opus")
        self.assertIsNone(self.dispatch("general-purpose"))

    def test_nested_ceiling(self):
        out = self.dispatch("general-purpose", agent_type="refactor-reviewer",
                            tool_input={"subagent_type": "general-purpose", "model": "opus", "prompt": "p"})
        self.assertEqual(out["model"], "sonnet")

    def test_run_record_reads_reviews_from_binding(self):
        self.bpath.write_text(json.dumps({"owned": True, "reviews": "records/"}))
        t = self.repo / "t.jsonl"
        t.write_text("")
        stop = {"hook_event_name": "SubagentStop", "agent_type": "refactor-reviewer",
                "agent_transcript_path": str(t), "cwd": str(self.repo)}
        run = lambda: subprocess.run(["python3", str(self.hook)], input=json.dumps(stop), capture_output=True,
                                     text=True, env=self.env()).stdout
        self.assertIn("write records/<YYYY-MM-DD>", run())
        (self.repo / "records" / "2026-10-05-x-review").mkdir(parents=True)
        (self.repo / "records" / "2026-10-05-x-review" / "02-pass-output.md").write_text("x")
        self.assertEqual(run(), "")


class TestReview(Fixture):
    """Findings of the adversarial review of 31fe3fe + a907219."""

    def assert_resolves(self):
        self.assertEqual(self.dispatch("role:code-review")["subagent_type"], "general-purpose")
        self.assertEqual(self.dispatch("role:absence-check")["subagent_type"], "bakehouse:absence-check")

    def test_p1_slow_resolver_times_out(self):
        # decided (review B2): a locator that does not answer refuses a role: dispatch, naming the locator;
        # it no longer skips the binding level and resolves past it
        self.slow_git("sleep 7")
        t0 = time.time()
        self.assertEqual(self.dispatch("role:code-review"), "DENY")
        self.assertLess(time.time() - t0, 9)
        self.assertIn("TimeoutExpired", self.log())
        self.assertIn("did not answer within", self.log())

    def test_m14_personal_level_before_shipped(self):
        self.personal({"absence-check": {"providers": ["design-author"]}})
        self.assertEqual(self.dispatch("role:absence-check")["subagent_type"], "design-author")

    def test_m15_personal_rules_before_shipped_rules(self):
        self.personal({"code-review": {"providers": ["refactor-engineer"], "model": "haiku"}})
        self.assertEqual(self.dispatch("refactor-engineer")["model"], "haiku")

    def test_m17_deny_reason_names_the_cause(self):
        self.uninstall("architect")
        self.dispatch("role:architect")
        self.dispatch("role:no-such-role")
        log = self.log()
        self.assertIn("role 'architect' is unresolved (no installed agent fills it)", log)
        self.assertIn("role 'no-such-role' is unresolved (no level names it)", log)

    def test_m22_prompt_append_added_once(self):
        self.personal({"code-review": {"providers": ["design-author"], "prompt_append": "EXTRA"}})
        out = self.dispatch("design-author", tool_input={"subagent_type": "design-author",
                                                          "prompt": "p\n\n[bakehouse:agent-roles]\nEXTRA"})
        self.assertIsNone(out)
        self.assertEqual(self.dispatch("design-author")["prompt"].count("EXTRA"), 1)

    def test_m24_second_stop_is_let_through(self):
        self.assertIn('"decision": "block"', self.stop())
        out = self.stop(stop_hook_active=True)
        self.assertNotIn("block", out)
        self.assertIn("systemMessage", out)

    def test_m25_model_approved_keeps_the_model(self):
        out = self.dispatch("blast-radius", tool_input={"subagent_type": "blast-radius", "model": "opus",
                                                         "description": "x [model-approved]", "prompt": "p"})
        self.assertIsNone(out)
        self.assertEqual(self.dispatch("blast-radius")["model"], "sonnet")

    def test_p2_4_prompt_names_the_bindings_review_folder(self):
        self.personal({"code-review": {"providers": ["design-author"],
                                       "prompt_append": "create {reviews}<date>-x/"}})
        self.assertIn("create axis/runs/<date>-x/", self.dispatch("design-author")["prompt"])
        self.bpath.write_text(json.dumps({"owned": True, "reviews": "records"}))
        self.assertIn("create records/<date>-x/", self.dispatch("design-author")["prompt"])

    def test_p3_2_binding_provider_in_no_table_takes_the_roles_model(self):
        self.install("other-agent")
        self.binding({"engineer": "other-agent"})
        out = self.dispatch("role:refactor:engineer")
        self.assertEqual((out["subagent_type"], out["model"]), ("other-agent", "opus"))
        self.assertNotIn("[bakehouse:agent-roles]", out["prompt"])

    def test_binding_table_form_entry_sets_the_model(self):
        self.install("other-agent")
        self.binding({"change:engineer": {"providers": ["other-agent"], "model": "sonnet"}})
        out = self.dispatch("role:change:engineer")
        self.assertEqual((out["subagent_type"], out["model"]), ("other-agent", "sonnet"))
        self.assertEqual(self.dispatch("role:refactor:engineer")["model"], "opus")
        self.assertEqual(self.dispatch("role:change:engineer", cwd=self.other)["subagent_type"],
                         "bakehouse:refactor-engineer")

    def test_p3_3_string_providers_name_no_agent(self):
        self.install("a")
        self.personal({"absence-check": {"providers": "abc"}})
        self.assertEqual(self.dispatch("role:absence-check")["subagent_type"], "bakehouse:absence-check")
        self.assertEqual(team_roles.providers({"providers": "abc"}), [])

    def test_p3_3_bad_reviews_skip_the_run_record_check(self):
        for bad in (["x"], "/tmp", "../up"):
            self.bpath.write_text(json.dumps({"owned": True, "reviews": bad}))
            self.assertEqual(self.stop(), "", bad)
        log = self.log()
        self.assertIn("is not a path", log)
        self.assertEqual(log.count("is outside the repository"), 2)


class TestPredictionRecord(Fixture):
    """A role:change:engineer may not stop until its record folder holds a prediction that parses."""

    def setUp(self):
        super().setUp()
        self.bpath.write_text(json.dumps({"owned": True, "reviews": "records/"}))
        self.folder = self.repo / "records" / "2026-01-02-T7-step1-review"

    def engineer_stop(self, prompt, **extra):
        t = self.repo / "agent.jsonl"
        t.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
        self.folder.mkdir(parents=True, exist_ok=True)
        seal = extra.pop("seal", True)
        written = []
        for name, text in extra.pop("files", {}).items():
            (self.folder / name).write_text(text)
            written.append(self.folder / name)
        if seal:
            seal_files(self.ledger, written)
        d = {"hook_event_name": "SubagentStop", "agent_type": "refactor-engineer",
             "agent_transcript_path": str(t), "cwd": str(self.repo), **extra}
        return subprocess.run(["python3", str(self.hook)], input=json.dumps(d), capture_output=True, text=True,
                              env=self.env()).stdout

    @property
    def BRIEF(self):
        # a full path: the dispatch refuses a relative line (fix round 2)
        return f"Work order T7: x.\nRecord folder: {self.repo}/records/2026-01-02-T7-step1-review/.\nBudget: 1k."

    def dispatch_brief(self, st, prompt=None):
        d = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(self.repo),
             "tool_input": {"subagent_type": st, "description": "x", "prompt": prompt or self.BRIEF}}
        p = subprocess.run(["python3", str(self.hook)], input=json.dumps(d), capture_output=True, text=True,
                           env=self.env())
        return json.loads(p.stdout) if p.stdout.strip() else {}

    def marked(self):
        return self.dispatch_brief("role:change:engineer")["hookSpecificOutput"]["updatedInput"]["prompt"]

    def test_prediction_record_marks_only_the_change_engineer(self):
        self.assertIn("[bakehouse:prediction-record]", self.marked())
        self.assertIn("records/2026-01-02-T7-step1-review/ must hold the prediction as moves.prediction.json",
                      self.marked())
        self.assertIn("under records/ must hold the prediction", self.dispatch("role:change:engineer")["prompt"])
        self.assertNotIn("[bakehouse:prediction-record]", self.dispatch("role:refactor:engineer")["prompt"])
        self.assertNotIn("[bakehouse:prediction-record]", self.dispatch("role:engineer")["prompt"])

    def test_prediction_record_survives_a_provider_override(self):
        self.install("other-agent")
        self.binding({"change:engineer": "other-agent"})
        self.personal({"change:engineer": {"providers": ["other-agent"]}})
        out = self.dispatch("role:change:engineer")
        self.assertEqual(out["subagent_type"], "other-agent")
        self.assertIn("[bakehouse:prediction-record]", out["prompt"])

    def test_prediction_record_stop_blocks_a_prose_only_folder(self):
        out = self.engineer_stop(self.marked(), files={"prediction.md": "moves, in prose"})
        self.assertIn('"decision": "block"', out)
        self.assertIn("records/2026-01-02-T7-step1-review holds no moves.prediction.json", out)

    def test_prediction_record_stop_blocks_a_prediction_that_does_not_parse(self):
        out = self.engineer_stop(self.marked(), files={"moves.prediction.json": json.dumps({"moves": [{"to": 1}]})})
        self.assertIn('"decision": "block"', out)
        self.assertIn("move 1 needs a 'what' key", out)
        out = self.engineer_stop(self.marked(), files={"moves.prediction.json": "{not json"})
        self.assertIn("is not valid JSON", out)

    def test_prediction_record_stop_passes_a_parsing_prediction_and_a_fix_round(self):
        ok = json.dumps({"moves": [{"what": "tests:passed", "range": [10, 12]}], "mustNotMove": ["out:*"]})
        self.assertEqual(self.engineer_stop(self.marked(), files={"moves.prediction.json": ok}), "")
        (self.folder / "moves.prediction.json").unlink()
        self.assertEqual(self.engineer_stop(self.marked(), files={"moves.prediction.fix1.json": ok}), "")
        self.assertIn("PREDICTION ok for refactor-engineer", self.log())

    # ---- Step 2b fix 1 (review findings-fix6.md F1, F7) ----

    OKP = json.dumps({"moves": [{"what": "tests:passed", "range": [10, 12]}], "mustNotMove": ["out:*"]})

    def test_f1_a_repo_reached_through_a_symlink_still_gets_the_seal_check(self):
        link = Path(self.tmp.name) / "repo-link"
        link.symlink_to(self.repo)
        out = self.engineer_stop("Work order T7: x.", files={"moves.prediction.json": self.OKP}, seal=False,
                                 cwd=str(link))
        self.assertIn('"decision": "block"', out, "the cwd is the symlinked path")
        self.assertIn("moves file(s) with no seal in the ops ledger", out)

    def test_f7_an_unsealed_measured_file_beside_a_sealed_prediction_sends_the_engineer_back(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "moves.measured.json").write_text('{"tests:passed": 11}')
        out = self.engineer_stop(self.marked(), files={"moves.prediction.json": self.OKP})
        self.assertIn('"decision": "block"', out)
        self.assertIn("moves.measured.json", out)
        self.assertNotIn("/moves.prediction.json", out.split("no seal in the ops ledger:")[1].split(".")[0])

    def test_f7_a_prediction_sealed_by_its_revise_lets_the_engineer_stop(self):
        led = self.ledger
        led.parent.mkdir(parents=True, exist_ok=True)
        led.write_text(json.dumps({"kind": "revised", "task": "T7", "prev": None,
                                   "sha256": hashlib.sha256(self.OKP.encode()).hexdigest()}) + "\n")
        self.assertEqual(self.engineer_stop(self.marked(), files={"moves.prediction.json": self.OKP}, seal=False), "")

    def test_step2b_a_moves_file_with_no_seal_sends_the_engineer_back_once(self):
        ok = json.dumps({"moves": [{"what": "tests:passed", "range": [10, 12]}], "mustNotMove": ["out:*"]})
        out = self.engineer_stop(self.marked(), files={"moves.prediction.json": ok}, seal=False)
        self.assertIn('"decision": "block"', out)
        self.assertIn("moves file(s) with no seal in the ops ledger", out)
        self.assertIn("moves.prediction.json", out)
        self.assertIn("predict-gate.sh seal", out)
        self.assertIn("SEAL missing for refactor-engineer", self.log())
        out = self.engineer_stop(self.marked(), seal=False, stop_hook_active=True)
        self.assertNotIn('"decision": "block"', out)
        self.assertIn("cannot close until they are sealed", out)

    def test_step2b_a_sealed_prediction_lets_the_engineer_stop(self):
        ok = json.dumps({"moves": [{"what": "tests:passed", "range": [10, 12]}], "mustNotMove": ["out:*"]})
        self.assertEqual(self.engineer_stop(self.marked(), files={"moves.prediction.json": ok}), "")
        self.assertIn("PREDICTION ok for refactor-engineer", self.log())

    def test_prediction_record_second_stop_is_let_through_and_told(self):
        out = self.engineer_stop(self.marked(), stop_hook_active=True)
        self.assertNotIn("block", out)
        self.assertIn("stopped without a machine prediction", out)

    def test_prediction_record_unmarked_engineer_is_not_checked(self):
        self.assertEqual(self.engineer_stop(self.dispatch("role:refactor:engineer")["prompt"]), "")
        self.assertEqual(self.engineer_stop("no marker here"), "")


class TestPredictionStop(TestPredictionRecord):
    """Fix round 1: plain-name dispatch, the engineer's own folder, the marker's place, the agent type."""

    OK = json.dumps({"moves": [{"what": "a", "to": 1}], "mustNotMove": []})

    def setUp(self):
        super().setUp()
        self.bpath.write_text(json.dumps({"owned": True, "reviews": "records/",
                                                    "tracker": {"file": "T.md", "id": "^T[0-9]+$"}}))

    def stop_as(self, agent, prompt, **extra):
        t = self.repo / "agent.jsonl"
        t.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
        d = {"hook_event_name": "SubagentStop", "agent_type": agent, "agent_transcript_path": str(t),
             "cwd": str(self.repo), **extra}
        return subprocess.run(["python3", str(self.hook)], input=json.dumps(d), capture_output=True, text=True,
                              env=self.env()).stdout

    def test_a_plain_name_dispatch_with_a_work_order_is_marked_and_checked(self):
        out = self.dispatch_brief("refactor-engineer")["hookSpecificOutput"]["updatedInput"]["prompt"]
        self.assertEqual(out.splitlines()[-1], "[bakehouse:prediction-record]")
        self.assertIn('"decision": "block"', self.engineer_stop(out, files={"prediction.md": "prose"}))

    def test_the_plugin_agent_is_the_change_engineer_with_or_without_its_prefix(self):
        """The shipped table names the plugin agent bakehouse:refactor-engineer, the subagent_type the harness
        gives a plugin agent. A stop may report it with or without the prefix; another plugin's agent of the
        same name is not it."""
        out = self.dispatch_brief("bakehouse:refactor-engineer")["hookSpecificOutput"]["updatedInput"]["prompt"]
        self.assertEqual(out.splitlines()[-1], "[bakehouse:prediction-record]")
        for agent in ("bakehouse:refactor-engineer", "refactor-engineer"):
            with self.subTest(agent=agent):
                self.assertIn('"decision": "block"',
                              self.engineer_stop(out, files={"prediction.md": "prose"}, agent_type=agent))
        self.assertEqual(self.engineer_stop(out, files={"prediction.md": "prose"}, agent_type="other:refactor-engineer"),
                         "")

    def test_a_refactor_job_with_a_work_order_line_is_not_checked(self):
        brief = "Refactor part X, the follow-up to:\nWork order T6: the earlier change.\n" \
                "Record folder: records/2026-01-02-T7-step1-review/."
        out = self.dispatch_brief("role:refactor:engineer", brief)
        sent = out["hookSpecificOutput"]["updatedInput"]["prompt"] if out else brief
        self.assertNotIn("[bakehouse:prediction-record]", sent)
        for prompt in (sent, brief):  # the transcript holds the prompt after the rewrite, or before it
            with self.subTest(prompt=prompt[-30:]):
                self.assertEqual(self.engineer_stop(prompt, files={"result.md": "refactor done"}), "")
        self.assertIn("dispatched as a job that is not a change job", self.log())
        marked = self.marked()
        self.assertIn('"decision": "block"', self.engineer_stop(marked, files={"prediction.md": "prose"}))

    def test_a_built_in_agent_is_checked_only_when_marked(self):
        self.bpath.write_text(json.dumps({"owned": True, "reviews": "records/",
            "tracker": {"file": "T.md", "id": "^T[0-9]+$"}, "roles": {"change:engineer": {"providers": ["general-purpose"]}}}))
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "prediction.md").write_text("prose")
        self.assertEqual(self.stop_as("general-purpose", "Diff: a..b. Do not edit.\nWork order T7: need.\n"), "")
        sent = self.dispatch_brief("role:change:engineer")["hookSpecificOutput"]["updatedInput"]["prompt"]
        self.assertIn('"decision": "block"', self.stop_as("general-purpose", sent))
        self.assertIn('"decision": "block"', self.stop_as("general-purpose", self.BRIEF))  # logged as a change job

    def test_a_plain_name_dispatch_without_a_work_order_warns(self):
        out = self.dispatch_brief("refactor-engineer", "Refactor part X.")
        self.assertIn("predict-gate.sh close` is the hard gate", out["systemMessage"])
        self.assertNotIn("[bakehouse:prediction-record]", json.dumps(out))
        self.assertIn("WARN", self.log())

    def test_no_binding_no_mark_and_no_warning(self):
        d = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(self.other),
             "tool_input": {"subagent_type": "refactor-engineer", "description": "x", "prompt": self.BRIEF}}
        p = subprocess.run(["python3", str(self.hook)], input=json.dumps(d), capture_output=True, text=True,
                           env=self.env())
        self.assertNotIn("prediction-record", p.stdout)
        self.assertNotIn("systemMessage", p.stdout)

    def test_the_brief_is_enough_when_line_1_holds_the_prompt_before_the_rewrite(self):
        self.assertIn('"decision": "block"', self.engineer_stop(self.BRIEF, files={"prediction.md": "prose"}))
        self.assertIn("PROMPT-SOURCE refactor-engineer: transcript line 1 does not end with", self.log())
        self.assertEqual(self.engineer_stop(self.BRIEF, files={"moves.prediction.json": self.OK}), "")

    def test_the_marked_prompt_logs_that_line_1_was_rewritten(self):
        self.engineer_stop(self.marked(), files={"moves.prediction.json": self.OK})
        self.assertIn("PROMPT-SOURCE refactor-engineer: transcript line 1 ends with [bakehouse:prediction-record]",
                      self.log())

    def test_only_the_engineers_own_folder_counts(self):
        old = self.repo / "records" / "2025-12-01-T6-step1-review"
        old.mkdir(parents=True)
        (old / "moves.prediction.json").write_text(self.OK)
        (old / "notes.txt").write_text("touched just now")
        out = self.engineer_stop(self.marked(), files={"prediction.md": "prose"})
        self.assertIn('"decision": "block"', out)
        self.assertIn("records/2026-01-02-T7-step1-review holds no moves.prediction.json", out)

    def test_without_a_record_folder_line_the_binding_folder_of_the_work_order_counts(self):
        brief = "Work order T7: x."
        self.assertIn('"decision": "block"', self.engineer_stop(brief, files={"prediction.md": "prose"}))
        self.assertEqual(self.engineer_stop(brief, files={"moves.prediction.json": self.OK}), "")

    def test_an_empty_prediction_is_refused(self):
        out = self.engineer_stop(self.marked(), files={"moves.prediction.json": json.dumps({"moves": [], "mustNotMove": ["x"]})})
        self.assertIn('"decision": "block"', out)
        self.assertIn("moves.prediction.json names no move", out)

    def test_a_marker_quoted_mid_prompt_does_not_mark(self):
        out = self.engineer_stop("Look at [bakehouse:prediction-record] in the hook.", files={"prediction.md": "prose"})
        self.assertEqual(out, "")

    def test_a_non_engineer_is_never_blocked_and_its_run_record_check_runs(self):
        quote = "Review T7.\n[bakehouse:prediction-record]"
        self.assertNotIn("machine prediction", self.stop_as("refactor-reviewer", quote))
        self.assertIn("requires a run record", self.stop_as("refactor-reviewer", quote))
        rec = self.repo / "records" / "2026-01-03-T7-review"
        rec.mkdir(parents=True)
        (rec / "findings.md").write_text("x")
        (rec / "mutations.json").write_text("[]")
        (rec / "contract.md").write_text("c")
        self.assertEqual(self.stop_as("refactor-reviewer", quote), "")
        self.assertNotIn("PROMPT-SOURCE refactor-reviewer", self.log())


REVIEW_ROW = {"id": "M1", "mutation": "guard removed", "site": "src/a.py:3", "gate": "tests/test_a.py",
              "verdict": "killed"}


class TestReviewerStop(Fixture):
    """T3: a reviewer may not stop until each review folder it wrote holds mutations.json in the format."""

    STEP = "records/2026-01-03-T7-step1-review"

    def setUp(self):
        super().setUp()
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_a.py").write_text("t")
        (self.repo / "recipe.yaml").write_text("gates:\n  - id: lint\n    command: ruff\n")
        self.bind_reviews()

    def bind_reviews(self, **over):
        self.bpath.write_text(json.dumps({"owned": True, "reviews": "records/", **over}))

    def stop_rev(self, files, agent="refactor-reviewer", **extra):
        t = self.repo / "agent.jsonl"
        t.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "Review T7."}}) + "\n")
        for path, text in files.items():
            f = self.repo / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text if isinstance(text, str) else json.dumps(text))
        d = {"hook_event_name": "SubagentStop", "agent_type": agent, "agent_transcript_path": str(t),
             "cwd": str(self.repo), **extra}
        return subprocess.run(["python3", str(self.hook)], input=json.dumps(d), capture_output=True, text=True, check=False,
                              env=self.env()).stdout

    def review(self, rows=None, **extra):
        files = {f"{self.STEP}/contract.md": "c", f"{self.STEP}/review/findings.md": "F1"}
        if rows is not None:
            files[f"{self.STEP}/review/mutations.json"] = rows
        return self.stop_rev(files, **extra)

    def test_findings_without_mutations_json_is_sent_back_once_then_told(self):
        out = self.review()
        self.assertIn('"decision": "block"', out)
        self.assertIn("review: no file matches mutations.json", out)
        self.assertIn("mutations.json beside findings.md", out)
        out = self.review(stop_hook_active=True)
        self.assertNotIn("block", out)
        self.assertIn("record incomplete", out)
        self.assertIn("RECORD-ARTEFACTS missing or bad for refactor-reviewer", self.log())

    def test_a_complete_mutation_record_lets_it_stop(self):
        self.assertEqual(self.review([REVIEW_ROW]), "")
        self.assertEqual(self.review([]), "")

    def test_a_survivor_with_no_disposition_sends_it_back(self):
        out = self.review([dict(REVIEW_ROW, id="M2", verdict="survived")])
        self.assertIn("M2 survived and has no disposition", out)
        ok = dict(REVIEW_ROW, id="M2", verdict="survived", disposition="accepted: reach 0, no input has it")
        self.assertEqual(self.review([ok]), "")

    def test_the_gate_is_a_binding_gate_id_or_a_path(self):
        self.assertIn("neither a gate id", self.review([dict(REVIEW_ROW, gate="lint")]))
        self.bind_reviews(gates={"predictedMoves": {"gates": "recipe.yaml#gates"}})
        self.assertEqual(self.review([dict(REVIEW_ROW, gate="lint")]), "")
        self.assertIn("neither a gate id", self.review([dict(REVIEW_ROW, gate="the pre-process")]))

    def test_a_nested_review_with_no_top_level_file_gets_both_reasons(self):
        out = self.stop_rev({f"{self.STEP}/review/findings.md": "F1"})
        self.assertIn("requires a run record", out)
        self.assertIn("no file matches mutations.json", out)

    def test_a_review_folder_written_before_the_run_is_not_checked(self):
        old = self.repo / self.STEP / "review-old" / "findings.md"
        old.parent.mkdir(parents=True)
        old.write_text("F1")
        os.utime(old, (time.time() - 7200, time.time() - 7200))
        self.assertEqual(self.review([REVIEW_ROW]), "")

    def old(self, files):
        for path, text in files.items():
            f = self.repo / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text if isinstance(text, str) else json.dumps(text))
            os.utime(f, (time.time() - 3600, time.time() - 3600))

    def test_a_recheck_in_the_same_folder_must_write_its_own_rows(self):
        for d in (f"{self.STEP}/review", "records/2026-01-04-T7-review"):
            with self.subTest(d=d):
                self.old({f"{d}/findings.md": "F1", f"{d}/mutations.json": [REVIEW_ROW], f"{d}/contract.md": "c"})
                recheck = {f"{d}/findings-fix1.md": "F1 again", f"{self.STEP}/contract-fix1.md": "c"}
                out = self.stop_rev(recheck)
                self.assertIn('"decision": "block"', out)
                self.assertIn("no file matches mutations-fix1.json", out)
                recheck[f"{d}/mutations-fix1.json"] = [dict(REVIEW_ROW, id="R1")]
                self.assertEqual(self.stop_rev(recheck), "")

    def test_a_rewritten_findings_file_needs_its_rows_rewritten(self):
        self.old({f"{self.STEP}/review/mutations.json": [REVIEW_ROW]})
        out = self.review()
        self.assertIn('"decision": "block"', out)
        self.assertIn("mutations.json was not written during this run (owed by findings.md)", out)
        self.assertEqual(self.review([REVIEW_ROW]), "")

    def test_a_findings_file_under_another_name_is_sent_back(self):
        st, d = self.STEP, f"{self.STEP}/review"
        cases = [({f"{d}/fix1-recheck.md": "G1"}, "fix1-recheck.md", "findings-fix1.md", "mutations-fix1.json"),
                 ({f"{d}/recheck.md": "G1"}, "recheck.md", "findings-fix1.md", "mutations-fix1.json"),
                 ({f"{d}/Findings-Fix2.md": "G1"}, "Findings-Fix2.md", "findings-fix2.md", "mutations-fix2.json")]
        for files, name, want, rows in cases:
            with self.subTest(name=name):
                self.tearDown()
                self.setUp()
                self.old({f"{d}/findings.md": "F1", f"{d}/mutations.json": [REVIEW_ROW]})
                out = self.stop_rev({**files, f"{st}/contract-fix1.md": "c"})
                self.assertIn('"decision": "block"', out)
                self.assertIn(f"review: {name} is not a declared findings name; write it as {want} and its rows as "
                              f"{rows} (record-contracts.json, rounds)", out)
        self.tearDown()
        self.setUp()
        out = self.stop_rev({f"{st}/contract.md": "c", f"{d}/FINDINGS.md": "F1"})
        self.assertIn("FINDINGS.md is not a declared findings name; write it as findings.md and its rows as "
                      "mutations.json", out)
        self.tearDown()
        self.setUp()
        self.old({f"{d}/recheck.md": "an earlier run's record"})
        self.assertEqual(self.stop_rev({f"{st}/contract.md": "c", f"{st}/prediction-review-fix.md": "p",
                                        f"{d}/findings.md": "F1", f"{d}/pass1.md": "p",
                                        f"{d}/mutations.json": [REVIEW_ROW]}), "")

    def test_each_findings_file_written_needs_its_own_fresh_rows(self):
        d = f"{self.STEP}/review"
        self.old({f"{d}/findings.md": "F1", f"{d}/mutations.json": [REVIEW_ROW]})
        out = self.stop_rev({f"{self.STEP}/contract.md": "c", f"{d}/findings.md": "F1 F2",
                             f"{d}/findings-fix1.md": "G1", f"{d}/mutations-fix1.json": [dict(REVIEW_ROW, id="R1")]})
        self.assertIn("mutations.json was not written during this run (owed by findings.md)", out)
        self.assertNotIn("mutations-fix1.json was not written", out)

    def test_an_open_survivor_lets_it_stop(self):
        self.assertEqual(self.review([dict(REVIEW_ROW, verdict="survived", disposition="open: F1")]), "")
        out = self.review([dict(REVIEW_ROW, verdict="survived", disposition="open: F9")])
        self.assertIn('"decision": "block"', out)
        self.assertIn("which the findings file beside it does not name", out)

    def test_a_test_added_disposition_names_a_test_file(self):
        (self.repo / "README.md").write_text("r")
        row = dict(REVIEW_ROW, verdict="survived")
        self.assertEqual(self.review([dict(row, disposition="test-added tests/test_a.py")]), "")
        self.assertIn("which is not a test file", self.review([dict(row, disposition="test-added README.md")]))
        self.assertIn("does not hold", self.review([dict(row, disposition="test-added tests/test_a.py::test_x")]))
        self.assertIn("which is a folder, not a test file", self.review([dict(row, disposition="test-added tests")]))

    def test_a_crash_in_the_record_check_fails_open(self):
        self.bind_reviews(records={"reviewer": 1})
        out = self.stop_rev({f"{self.STEP}/review/findings.md": "F1"})
        self.assertIn('"decision": "block"', out)
        self.assertIn("requires a run record", out)
        self.assertNotIn("mutations.json", out)
        self.assertIn("ERROR record check for reviewer: BadInput", self.log())
        self.assertEqual(self.stop_rev({"records/2026-01-05-T7-review/findings.md": "F1"}), "")
        self.assertIn("RUN-RECORD ok for refactor-reviewer", self.log())

    def test_a_role_that_is_not_a_record_job_is_not_checked(self):
        self.personal({"architect": {"providers": ["architect"], "run_record": True}})
        self.assertEqual(self.review(agent="architect"), "")

    def test_b4_a_contract_beside_the_findings_or_above_it_in_the_record_counts(self):
        rows = {f"{self.STEP}/pass1.md": "p", f"{self.STEP}/review/findings.md": "F1",
                f"{self.STEP}/review/mutations.json": [REVIEW_ROW]}
        out = self.stop_rev(rows)
        self.assertIn('"decision": "block"', out)
        self.assertIn(f"{self.STEP}/review: no contract.md here or in a folder above it, inside records/", out)
        for at in (f"{self.STEP}/review/contract.md", f"{self.STEP}/contract.md", f"{self.STEP}/contract-fix1.md"):
            with self.subTest(at=at):
                self.assertEqual(self.stop_rev({**rows, at: "c"}), "")
                (self.repo / at).unlink()
        # a contract in another record folder is not this review's
        self.old({"records/2026-01-04-T8-step1-review/contract.md": "c"})
        self.assertIn("no contract.md here", self.stop_rev(rows))


class RecordFixture(Fixture):
    """A git repository `ws` with no team beside the bound repo, and `~/repo`, a link to the bound repo."""

    PRED = json.dumps({"moves": [{"what": "a", "to": 1}], "mustNotMove": []})

    def setUp(self):
        super().setUp()
        self.ws = Path(self.tmp.name).resolve() / "ws"
        subprocess.run(["git", "init", "-q", str(self.ws)], check=True)
        (self.home / "repo").symlink_to(self.repo)
        self.rf = self.repo / "records" / "2026-01-03-T7-x"

    def stop_in(self, prompt, files, cwd=None, agent="refactor-reviewer", seal=True, **extra):
        t = Path(self.tmp.name) / "agent.jsonl"
        t.unlink(missing_ok=True)
        t.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
        for path, text in files.items():
            f = Path(path)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text if isinstance(text, str) else json.dumps(text))
        if seal:
            seal_files(self.ledger, files)
        d = {"hook_event_name": "SubagentStop", "agent_type": agent, "agent_transcript_path": str(t),
             "cwd": str(cwd or self.ws), **extra}
        out = subprocess.run(["python3", str(self.hook)], input=json.dumps(d), capture_output=True, text=True, check=False,
                             env=self.env()).stdout
        for path in files:
            Path(path).unlink()
        return out


class TestRecordLineFix1(RecordFixture):
    """T8 fix round 1: the dispatch log with no binding (F1), the line's forms and refusal (F2)."""

    def sent(self, st, prompt, cwd):
        out = self.dispatch(st, cwd=cwd, tool_input={"subagent_type": st, "description": "x", "prompt": prompt})
        return out["prompt"] if out else prompt

    def bind_repo(self, **extra):
        self.bpath.write_text(json.dumps({"owned": True, "reviews": "records/", **extra}))

    def test_a_refactor_job_with_a_record_line_is_not_checked_and_a_change_job_is(self):
        self.bind_repo()
        log = self.machine / "logs" / "prediction-dispatch.jsonl"
        for cwd in (self.ws, self.other, self.repo):
            with self.subTest(cwd=cwd):
                brief = f"Case {cwd.name}.\nWork order W1: x\nRecord folder: {self.rf}/\n"
                n = len(log.read_text().splitlines()) if log.is_file() else 0
                refactor = self.sent("role:refactor:engineer", brief, cwd)
                self.assertEqual(len(log.read_text().splitlines()), n + 1)
                self.assertEqual(self.stop_in(refactor, {self.rf / "result.md": "r"}, cwd=cwd, agent="refactor-engineer"), "")
                change = self.sent("role:change:engineer", "Change. " + brief, cwd)
                self.assertEqual(len(log.read_text().splitlines()), n + 2)
                self.assertIn("records/2026-01-03-T7-x holds no moves.prediction.json",
                              self.stop_in(change, {self.rf / "prediction.md": "p"}, cwd=cwd, agent="refactor-engineer"))
        line = "Record folder: records/2026-01-02-T7-step1-review/"
        refactor = self.sent("role:refactor:engineer", f"Work order T7: x\n{line}\n", self.repo)
        self.assertEqual(self.stop_in(refactor, {self.rf / "result.md": "r"}, cwd=self.repo, agent="refactor-engineer"), "")

    def test_the_line_is_read_in_every_form(self):
        self.bind_repo()
        t = "~/repo/records/2026-01-03-T7-x/"
        for line in (f"Record folder: `{t}`", f"**Record folder:** {t}", f"**Record folder**: {t}",
                     f"  Record folder: {t}", f"- Record folder: {t}", f"* Record folder: `{t}`",
                     "Record folder: ~/repo/records/../records/2026-01-03-T7-x/", f"Record folder: '{t}'"):
            with self.subTest(line=line):
                out = self.stop_in(f"Review W1.\n{line}\n", {self.rf / "review" / "findings.md": "F1"}, cwd=self.repo)
                self.assertIn("records/2026-01-03-T7-x/review: no file matches mutations.json", out)
                self.assertNotIn("line names", out)

    def test_a_relative_line_is_read_from_the_repository_root(self):
        self.bind_repo()
        (self.repo / "src").mkdir()
        out = self.stop_in("Record folder: records/2026-01-03-T7-x/\n", {self.rf / "review" / "findings.md": "F1"},
                           cwd=self.repo / "src")
        self.assertIn("records/2026-01-03-T7-x/review: no file matches mutations.json", out)
        self.assertNotIn("line names", out)
        out = self.stop_in("Record folder: 2026-01-03-T7-x/\n", {self.rf / "review" / "findings.md": "F1"},
                           cwd=self.repo / "records")
        self.assertIn("line names 2026-01-03-T7-x, which is not under this repository's records/", out)

    def test_a_line_no_check_can_read_is_reported_at_the_stop_never_dropped(self):
        # with no dispatch decision (a plain-name dispatch, or one from before it) the stop reads the line
        # from its cwd, as in round 1, and reports a line it cannot place; it never refuses it (fix round 2)
        self.bind_repo()
        clone = Path(self.tmp.name).resolve() / "clone"
        cases = [(f"{clone}/records/2026-01-03-T7-x/", self.repo, "not under this repository's records/"),
                 ("design/w1/", self.repo, "not under this repository's records/")]
        for line, cwd, why in cases:
            for again in (False, True):
                with self.subTest(line=line, again=again):
                    out = json.loads(self.stop_in(f"Review T7.\nRecord folder: {line}\n", {}, cwd=cwd,
                                                  **({"stop_hook_active": True} if again else {})))
                    self.assertIn(f"`Record folder:` line names {line.rstrip('/')}, which is {why}", out["systemMessage"])
                    self.assertIn("so the record it names was not checked", out["systemMessage"])
                    self.assertNotIn("line names", out.get("reason", ""))
                    # the bound repo's own check still runs, as for a brief with no line
                    self.assertEqual("decision" in out, cwd == self.repo and not again)
        # a line inside the bound repo's reviews folder, relative or absolute, is the repo record, as before
        for line in ("records/2026-01-03-T7-x/", f"{self.repo}/records/2026-01-03-T7-x/"):
            with self.subTest(line=line):
                out = self.stop_in(f"Record folder: {line}\n", {self.rf / "review" / "findings.md": "F1"}, cwd=self.repo)
                self.assertIn("records/2026-01-03-T7-x/review: no file matches mutations.json", out)
                self.assertNotIn("line names", out)
        # the change engineer: reported, and the work order's folders are checked as for a brief with no line
        brief = "Work order T7: x\nRecord folder: design/w1/\n"
        out = json.loads(self.stop_in(brief, {self.repo / "records" / "2026-01-02-T7-step1-review" /
                                              "moves.prediction.json": self.PRED}, cwd=self.repo, agent="refactor-engineer"))
        self.assertNotIn("decision", out)
        self.assertIn("line names design/w1, which is not under this repository's records/", out["systemMessage"])
        self.assertIn('"decision": "block"', self.stop_in(brief, {}, cwd=self.repo, agent="refactor-engineer"))
        # the engineer reads the bold form too
        out = self.stop_in(self.sent("role:change:engineer", "Work order W1: x\n**Record folder:** ~/repo/records/2026-01-03-T7-x/\n",
                                     self.ws), {self.rf / "prediction.md": "p"}, cwd=self.ws, agent="refactor-engineer")
        self.assertIn("records/2026-01-03-T7-x holds no moves.prediction.json", out)


class DecisionFixture(RecordFixture):
    """A bound repo with a tracker id form and a `records/` reviews folder, beside the repository `ws`."""

    def setUp(self):
        super().setUp()
        self.bpath.write_text(json.dumps({"owned": True, "reviews": "records/", "tracker": {"id": "^T[0-9]+$"}}))
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_a.py").write_text("t")
        self.log_file = self.machine / "logs" / "prediction-dispatch.jsonl"

    def decide(self, st, prompt, cwd):
        """("DENY", reason) or ("ok", the prompt the agent receives)."""
        d = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(cwd),
             "tool_input": {"subagent_type": st, "description": "x", "prompt": prompt}}
        p = subprocess.run(["python3", str(self.hook)], input=json.dumps(d), capture_output=True, text=True,
                           env=self.env(), check=False)
        self.assertEqual(p.stderr, "")
        h = (json.loads(p.stdout) if p.stdout.strip() else {}).get("hookSpecificOutput") or {}
        if h.get("permissionDecision") == "deny":
            return "DENY", h["permissionDecisionReason"]
        return "ok", h.get("updatedInput", {}).get("prompt", prompt)

    def logged(self):
        return [json.loads(x) for x in self.log_file.read_text().splitlines()] if self.log_file.is_file() else []


class TestDispatchDecision(DecisionFixture):
    """T8 fix round 2: the record folder is decided once, at dispatch, from a full path; the stop checks the
    folder decided there, whatever its own cwd."""

    def test_a_relative_line_is_refused_at_dispatch_naming_where_it_resolves(self):
        line = "Record folder: records/2026-01-03-T7-x/"
        for st, cwd, brief in (("role:reviewer", self.ws, f"Review T7.\n{line}"),
                               ("role:reviewer", self.repo, f"Review T7.\n{line}"),
                               ("refactor-reviewer", self.repo, f"Review T7.\n{line}"),
                               ("role:change:engineer", self.repo, f"Work order T7: x\n{line}"),
                               ("role:change:engineer", self.ws, f"Work order T7: x\n{line}")):
            with self.subTest(st=st, cwd=cwd):
                got, why = self.decide(st, brief, cwd)
                self.assertEqual(got, "DENY")
                top = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], capture_output=True,
                                     text=True, check=True).stdout.strip()
                self.assertIn(f"`{line}` is not a full path: read from this working folder it would be "
                              f"{Path(top).resolve() / 'records/2026-01-03-T7-x'}", why)
                self.assertIn("`Record folder: ~/<path>/`", why)
        self.assertEqual(self.logged(), [])

    def test_a_full_path_is_decided_at_dispatch_and_the_stop_uses_it(self):
        brief = f"Review T7.\nRecord folder: {self.rf}/\n"
        got, sent = self.decide("role:reviewer", brief, self.ws)
        self.assertEqual(got, "ok")
        rec = self.logged()[-1]["record"]
        self.assertEqual((rec["kind"], rec["folder"]), ("repo", "records/2026-01-03-T7-x"))
        self.assertNotIn("change", self.logged()[-1])
        part = {self.rf / "review" / "findings.md": "F1", self.rf / "contract.md": "c"}
        for cwd in (self.other, self.repo, self.ws):
            with self.subTest(cwd=cwd):
                out = self.stop_in(sent, part, cwd=cwd)
                self.assertIn('"decision": "block"', out)
                self.assertIn("records/2026-01-03-T7-x/review: no file matches mutations.json", out)
                self.assertEqual(self.stop_in(sent, {**part, self.rf / "review" / "mutations.json": [REVIEW_ROW]},
                                              cwd=cwd), "")
        self.assertIn("requires a run record", self.stop_in(sent, {}, cwd=self.ws))
        # the change engineer
        brief = f"Work order T7: x\nRecord folder: {self.rf}/\n"
        got, sent = self.decide("role:change:engineer", brief, self.ws)
        self.assertEqual((got, self.logged()[-1]["change"], self.logged()[-1]["record"]["kind"]), ("ok", True, "repo"))
        self.assertIn(f"your record folder {self.rf}/ must hold the prediction", sent)
        self.assertIn("records/2026-01-03-T7-x holds no moves.prediction.json",
                      self.stop_in(sent, {self.rf / "prediction.md": "p"}, cwd=self.ws, agent="refactor-engineer"))
        self.assertEqual(self.stop_in(sent, {self.rf / "moves.prediction.json": self.PRED}, cwd=self.ws,
                                      agent="refactor-engineer"), "")

    def test_b4_an_empty_findings_file_and_no_contract_are_sent_back_once(self):
        # review B4's repro: a decided record folder, a 0-byte findings.md, `[]` rows and no contract.md
        got, sent = self.decide("role:reviewer", f"Review T7.\nRecord folder: {self.rf}/\n", self.ws)
        self.assertEqual(got, "ok")
        files = {self.rf / "findings.md": "", self.rf / "mutations.json": []}
        out = self.stop_in(sent, files, cwd=self.repo)
        self.assertIn('"decision": "block"', out)
        self.assertIn("records/2026-01-03-T7-x: findings.md is empty", out)
        self.assertIn("records/2026-01-03-T7-x: no contract.md here or in a folder above it", out)
        out = self.stop_in(sent, files, cwd=self.repo, stop_hook_active=True)
        self.assertNotIn("block", out)
        self.assertIn("stopped with its record incomplete", out)
        self.assertIn("findings.md is empty", out)
        self.assertEqual(self.stop_in(sent, {**files, self.rf / "findings.md": "F1", self.rf / "contract.md": "c"},
                                      cwd=self.repo), "")

    def test_h5_a_repo_record_is_checked_in_the_decided_folder_only(self):
        # T10: two work orders' records under one repo `records` folder; the stop for T7 neither passes on
        # nor is blocked by T8's files
        got, sent = self.decide("role:reviewer", f"Review T7.\nRecord folder: {self.rf}/\n", self.ws)
        self.assertEqual(got, "ok")
        mine, theirs = self.rf, self.repo / "records" / "2026-01-04-T8-y"
        done = lambda f: {f / "contract.md": "c", f / "review" / "findings.md": "F1",
                          f / "review" / "mutations.json": [REVIEW_ROW]}
        for cwd in (self.ws, self.repo):
            with self.subTest(cwd=cwd):
                out = self.stop_in(sent, done(theirs), cwd=cwd)
                self.assertIn("requires a run record", out)
                self.assertIn(f"the record folder your brief names, {self.rf}/", out)
                self.assertEqual(self.stop_in(sent, {**done(mine), theirs / "review" / "findings.md": "F1"}, cwd=cwd), "")
                self.assertEqual(self.stop_in(sent, {**done(mine), theirs / "review" / "recheck.md": "G1"}, cwd=cwd), "")
                out = self.stop_in(sent, {}, cwd=cwd)
                self.assertIn(f"the record folder your brief names, {self.rf}/", out)
                self.assertIn("records/2026-01-03-T7-x/review: no file matches mutations.json",
                              self.stop_in(sent, {mine / "contract.md": "c", mine / "review" / "findings.md": "F1",
                                                  **done(theirs)}, cwd=cwd))

    def test_f1_a_decided_repo_folder_counts_a_file_written_at_any_depth(self):
        # a product review writes its whole record in <work order>/review/: a complete one stops cleanly
        got, sent = self.decide("role:reviewer", f"Review T7.\nRecord folder: {self.rf}/\n", self.ws)
        self.assertEqual(got, "ok")
        rec = {self.rf / "review" / "contract.md": "c", self.rf / "review" / "findings.md": "F1",
               self.rf / "review" / "mutations.json": [REVIEW_ROW]}
        for cwd in (self.ws, self.repo):
            with self.subTest(cwd=cwd):
                self.assertEqual(self.stop_in(sent, rec, cwd=cwd), "")
                out = self.stop_in(sent, {}, cwd=cwd)
                self.assertIn(f"the record folder your brief names, {self.rf}/, or in a subfolder of it", out)

    def test_f2_a_folder_decided_two_levels_under_reviews_counts_as_written(self):
        deep = self.rf / "review"
        got, sent = self.decide("role:reviewer", f"Review T7.\nRecord folder: {deep}/\n", self.ws)
        self.assertEqual((got, self.logged()[-1]["record"]["folder"]), ("ok", "records/2026-01-03-T7-x/review"))
        for cwd in (self.ws, self.repo):
            with self.subTest(cwd=cwd):
                self.assertEqual(self.stop_in(sent, {deep / "contract.md": "c", deep / "findings.md": "F1",
                                                     deep / "mutations.json": [REVIEW_ROW]}, cwd=cwd), "")

    def test_f3_the_fallback_with_no_dispatch_decision_reads_the_whole_reviews_folder(self):
        # not decided at dispatch: the line is read from the cwd and the whole reviews folder is checked
        theirs = self.repo / "records" / "2026-01-04-T8-y"
        brief = f"Not dispatched.\nRecord folder: {self.rf}/\n"
        self.assertEqual(self.stop_in(brief, {theirs / "contract.md": "c", theirs / "review" / "findings.md": "F1",
                                              theirs / "review" / "mutations.json": [REVIEW_ROW]}, cwd=self.repo), "")
        self.assertIn("records/2026-01-04-T8-y/review: no file matches mutations.json",
                      self.stop_in(brief, {theirs / "contract.md": "c", theirs / "review" / "findings.md": "F1"},
                                   cwd=self.repo))

    def test_f5_problems_at_the_decided_folders_top_are_read(self):
        got, sent = self.decide("role:reviewer", f"Review T7.\nRecord folder: {self.rf}/\n", self.ws)
        self.assertEqual(got, "ok")
        for cwd in (self.ws, self.repo):
            with self.subTest(cwd=cwd):
                out = self.stop_in(sent, {self.rf / "contract.md": "c", self.rf / "findings.md": "F1"}, cwd=cwd)
                self.assertIn("records/2026-01-03-T7-x: no file matches mutations.json", out)
                self.assertEqual(self.stop_in(sent, {self.rf / "contract.md": "c", self.rf / "findings.md": "F1",
                                                     self.rf / "mutations.json": [REVIEW_ROW]}, cwd=cwd), "")

    def test_f6_problems_are_judged_with_the_record_repos_binding(self):
        # the record repo's binding names gate `corpus`; the stop's cwd has its own binding with no gates
        d = self.bpath
        d.write_text(json.dumps(dict(json.loads(d.read_text()), gates={"g": "gates.json#gates"})))
        (self.repo / "gates.json").write_text(json.dumps({"gates": [{"id": "corpus"}]}))
        project(self.ws, {"owned": True, "reviews": "records/"})
        got, sent = self.decide("role:reviewer", f"Review T7.\nRecord folder: {self.rf}/\n", self.ws)
        self.assertEqual(got, "ok")
        rec = {self.rf / "review" / "findings.md": "F1", self.rf / "review" / "mutations.json": [dict(REVIEW_ROW, gate="corpus")],
               self.rf / "contract.md": "c"}
        self.assertEqual(self.stop_in(sent, rec, cwd=self.ws), "")
        self.assertIn("neither a gate id", self.stop_in(sent, {**rec, self.rf / "review" / "mutations.json": [
            dict(REVIEW_ROW, gate="lint")]}, cwd=self.ws))

    def test_f7_a_repo_folder_counts_as_written_only_through_a_new_marked_file(self):
        # the reviewer's mark is findings*.md (record-contracts.json): a stray file, a contract alone, a
        # re-check's contract alone and a report alone are sent back, naming the mark
        got, sent = self.decide("role:reviewer", f"Review T7.\nRecord folder: {self.rf}/\n", self.ws)
        self.assertEqual(got, "ok")
        r = self.rf / "review"
        old = {r / "findings.md": "F1", r / "mutations.json": json.dumps([REVIEW_ROW])}
        for cwd in (self.ws, self.repo):
            for files in ({self.rf / "drivers" / "fix1" / "out.txt": "o"}, {r / "contract.md": "c"},
                          {self.rf / "out" / "report.md": "r"}):
                with self.subTest(cwd=cwd, files=list(files)):
                    out = self.stop_in(sent, files, cwd=cwd)
                    self.assertIn("requires a run record", out)
                    self.assertIn(f"{self.rf}/, or in a subfolder of it, as your dispatch prompt describes: no file "
                                  "written in this run there matches findings*.md.", out)
            with self.subTest(cwd=cwd, files="a re-check's contract beside an earlier round"):
                for f, v in old.items():
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_text(v)
                    os.utime(f, (time.time() - 3600, time.time() - 3600))
                self.assertIn("matches findings*.md", self.stop_in(sent, {r / "fix1-contract.md": "c"}, cwd=cwd))
                self.assertEqual(self.stop_in(sent, {r / "fix1-contract.md": "c", r / "findings-fix1.md": "G1",
                                                     r / "mutations-fix1.json": [REVIEW_ROW]}, cwd=cwd), "")
                for f in old:
                    f.unlink()
            self.assertEqual(self.stop_in(sent, {r / "contract.md": "c", r / "findings.md": "F1",
                                                 r / "mutations.json": [REVIEW_ROW]}, cwd=cwd), "")

    def test_two_lines_naming_different_folders_are_refused(self):
        a, b = "~/repo/records/2026-01-03-T7-x/", "~/repo/records/2026-01-04-T7-y/"
        for brief in (f"Record folder: {a}\nRecord folder: {b}\n",
                      f"Example:\n```text\nRecord folder: {b}\n```\nRecord folder: {a}\n",
                      f"The old brief said:\n    Record folder: {b}\nRecord folder: {a}\n"):
            with self.subTest(brief=brief):
                got, why = self.decide("role:reviewer", brief, self.ws)
                self.assertEqual(got, "DENY")
                self.assertIn("2 `Record folder:` lines name different folders", why)
                self.assertIn(b, why)
        self.assertEqual(self.decide("role:reviewer", f"Record folder: {a}\nRecord folder: {self.rf}/\n", self.ws)[0],
                         "ok")

    def test_a_line_the_reader_cannot_read_is_refused_at_dispatch(self):
        t = "~/repo/records/2026-01-03-T7-x/"
        for line, why in ((f"*Record folder:* {t}", "is not in the form the record check reads"),
                          (f"1. Record folder: {t}", "is not in the form"),
                          (f"> Record folder: {t}", "is not in the form"),
                          (f"Record Folder: {t}", "is not in the form"),
                          (f"Your record folder: {t}", "is not in the form"),
                          (f"Record folder: **{t}**", "holds '*' (U+002A) in its path"),
                          ("Record folder:", "is not in the form")):
            with self.subTest(line=line):
                got, reason = self.decide("role:reviewer", f"Review W1.\n{line}\n", self.ws)
                self.assertEqual(got, "DENY")
                self.assertIn(f"`{line}` {why}", reason)
        self.assertEqual(self.decide("role:reviewer", f"Review W1.\nRecord folder: {t}\n", self.ws)[0], "ok")
        # a line in prose that does not start with the label is not a record folder line
        self.assertEqual(self.decide("role:reviewer", f"Write it in the record folder: {t}\nRecord folder: {t}\n",
                                     self.ws)[0], "ok")

    def test_a_line_no_check_can_read_is_refused_at_dispatch(self):
        for line, cwd in (("~/repo/other/design/w1/", self.ws), (f"{self.repo}/elsewhere/x/", self.repo),
                          (f"{self.repo}/records/", self.repo), (f"{self.ws}/w1/", self.ws),
                          ("C:\\Users\\x\\w1\\", self.ws),
                          ("[binding.reviews + binding.reviewName, filled].", self.repo)):
            with self.subTest(line=line):
                got, why = self.decide("role:reviewer", f"Review.\nRecord folder: {line}\n", cwd)
                self.assertEqual(got, "DENY")
                # fix round 3: a line with text after its path is refused for that first
                self.assertTrue("which is not under a bound repository's reviews folder" in why
                                or "is not a full path" in why or "holds text after its path" in why, why)
        self.assertEqual(self.logged(), [])

    def test_the_project_dir_gives_the_cwds_binding_and_a_named_path_its_own_repos(self):
        # the harness sets CLAUDE_PROJECT_DIR for a plugin hook; the hook judges its input's cwd, never that
        # variable (S16 M2), and a path a brief names from that path's own repository
        self.bpath.write_text(json.dumps({"owned": True, "reviews": "records/", "roles": {"engineer": "design-author"}}))
        self.project_dir = self.repo
        self.assertEqual(self.decide("role:engineer", "p", self.other)[0], "ok")
        self.assertIn("role:engineer -> bakehouse:refactor-engineer (runtime/agent-roles.json key 'engineer')",
                      self.log())
        self.project_dir = self.ws
        got, sent = self.decide("role:reviewer", f"Review T7.\nRecord folder: {self.rf}/\n", self.ws)
        self.assertEqual((got, self.logged()[-1]["record"]["folder"]), ("ok", "records/2026-01-03-T7-x"))

    def test_the_opt_in_rule_holds_at_dispatch(self):
        line = "Record folder: w1/\n"
        # a plain-name dispatch outside any binding is not this hook's: no decision, no output
        self.assertEqual(self.decide("refactor-reviewer", line, self.other), ("ok", line))
        # a job the stop does not check keeps its line: a refactor job, a role with no run record
        self.assertEqual(self.decide("role:refactor:engineer", f"Work order T7: x\n{line}", self.repo)[0], "ok")
        self.assertEqual(self.decide("role:design-author", line, self.repo)[0], "ok")
        self.assertNotIn("record", json.dumps(self.logged()))
        # no line: nothing to decide
        self.assertEqual(self.decide("role:reviewer", "Review T7.\n", self.repo)[0], "ok")


class TestB1SpacedPath(DecisionFixture):
    """Review B1: a `Record folder:` path that holds a space is read whole, at dispatch and at the stop."""

    def setUp(self):
        self.outer = tempfile.TemporaryDirectory(prefix="sp ace ")
        old, tempfile.tempdir = tempfile.tempdir, self.outer.name
        try:
            super().setUp()
        finally:
            tempfile.tempdir = old

    def tearDown(self):
        super().tearDown()
        self.outer.cleanup()

    def folder(self, st, prompt, cwd):
        n = len(self.logged())
        got, sent = self.decide(st, prompt, cwd)
        self.assertEqual(got, "ok", sent)
        recs = [e["record"] for e in self.logged()[n:] if "record" in e]
        self.assertEqual(len(recs), 1)
        return recs[0], sent

    def test_b1_a_record_folder_path_with_a_space_is_read_whole(self):
        # decided (owner, fix round 3): a path with a space is read whole in backticks or quotes, and refused bare
        self.assertIn(" ", str(self.rf))
        for line, cwd in ((f"Record folder: `{self.rf}/`", self.ws), (f"Record folder: '{self.rf}/'  ", self.repo),
                          (f"- **Record folder:** `{self.rf}/`", self.repo)):
            with self.subTest(line=line):
                rec, _ = self.folder("role:reviewer", f"Review T7.\n{line}\n", cwd)
                self.assertEqual((rec["kind"], rec["folder"], rec["line"]), ("repo", "records/2026-01-03-T7-x", str(self.rf)))
        rec, sent = self.folder("role:change:engineer", f"Work order T7: x\nRecord folder: `{self.rf}/`\n", self.repo)
        self.assertIn(f"your record folder {self.rf}/ must hold the prediction", sent)
        got, why = self.decide("role:reviewer", f"Review T7.\nRecord folder: {self.rf}/\n", self.ws)
        self.assertEqual(got, "DENY")
        self.assertIn("a path with a space goes in backticks; notes go on the next line", why)
        # the stop checks the decided folder: T7's complete record passes
        _, sent = self.decide("role:reviewer", f"Review T7.\nRecord folder: `{self.rf}/`\n", self.ws)
        done = {self.rf / "contract.md": "c", self.rf / "review" / "findings.md": "F1",
                self.rf / "review" / "mutations.json": [REVIEW_ROW]}
        self.assertEqual(self.stop_in(sent, done, cwd=self.repo), "")
        # with no dispatch decision the stop reads the quoted spaced line whole, from its cwd
        out = self.stop_in(f"Not dispatched.\nRecord folder: `{self.rf}/`\n", {self.rf / "review" / "findings.md": "F1"},
                           cwd=self.repo)
        self.assertIn("records/2026-01-03-T7-x/review: no file matches mutations.json", out)
        self.assertNotIn("line names", out)


class TestStrictRecordLine(DecisionFixture):
    """Owner, fix round 3: a `Record folder:` line holds one path and nothing else; nothing after it is guessed."""

    def test_f7_text_after_the_path_is_refused_showing_the_line(self):
        for tail in (" (layout as in records/2026-01-02-T6-x/)", " — see docs/README.md", " and ~/elsewhere/x/",
                     " (see a/b)", " notes in docs/", ", as in records/T6", "/ (layout as in records/T6/)",
                     " (new folder)", "/` — prose", ". Write contract.md first.", "/. Gate commands: see the recipe",
                     "/\tnotes", "/\u00a0notes"):
            with self.subTest(tail=tail):
                line = f"Record folder: {self.rf}{tail}"
                got, why = self.decide("role:reviewer", f"Review T7.\n{line}\n", self.ws)
                self.assertEqual(got, "DENY")
                self.assertIn(f"`{line}` holds text after its path (a path with a space goes in backticks; notes go "
                              "on the next line)", why)
        for line in (f"Record folder: `{self.rf}/`.", f"Record folder: `{self.rf}/` (see a/b)", f"Record folder: `{self.rf}/"):
            with self.subTest(line=line):
                self.assertEqual(self.decide("role:reviewer", f"Review T7.\n{line}\n", self.ws)[0], "DENY")
        # the path alone, with or without quotes and trailing whitespace, is decided; a note on the next line is fine
        for line in (f"Record folder: {self.rf}/", f"Record folder: `{self.rf}/`", f"Record folder: {self.rf}/  \t"):
            with self.subTest(line=line):
                self.assertEqual(self.decide("role:reviewer", f"Review T7.\n{line}\nLayout as in T6.\n", self.ws)[0], "ok")

    def test_f7_a_line_with_text_after_its_path_is_reported_at_the_stop(self):
        done = {self.rf / "contract.md": "c", self.rf / "review" / "findings.md": "F1",
                self.rf / "review" / "mutations.json": [REVIEW_ROW]}
        out = self.stop_in(f"Not dispatched.\nRecord folder: {self.rf} (see a/b)\n", done, cwd=self.repo)
        self.assertIn("holds text after its path", out)
        self.assertNotIn('"decision": "block"', out)

    def test_f9_a_refused_line_from_an_unbound_folder_is_in_care(self):
        # a plain-name dispatch from a folder with no binding: in care through the line's path
        got, why = self.decide("refactor-reviewer", f"Review W1.\nRecord folder: {self.rf} (note)\n", self.other)
        self.assertEqual(got, "DENY")
        self.assertIn("holds text after its path", why)
        # review F16: in care through a bound repo's reviews folder, and through a ~/ path
        for line in (f"Record folder: {self.rf}/ (note)", "Record folder: ~/repo/records/2026-01-03-T7-x/ (note)"):
            with self.subTest(line=line):
                self.assertEqual(self.decide("refactor-reviewer", f"Review W1.\n{line}\n", self.other)[0], "DENY")
        # review F12: a line in a form the reader cannot read is in care through its path too, so it is refused
        for line in (f"1. Record folder: {self.rf}/", f"> Record folder: {self.rf}/", f"`Record folder:` {self.rf}/",
                     f"Your record folder: {self.rf}/"):
            with self.subTest(line=line):
                got, why = self.decide("refactor-reviewer", f"Review W1.\n{line}\n", self.other)
                self.assertEqual(got, "DENY")
                self.assertIn("is not in the form the record check reads", why)
        for line in (f"Record folder: {self.other}/records/x/ (note)", f"Record folder: {self.repo}/src/ (note)"):
            with self.subTest(line=line):
                self.assertEqual(self.decide("refactor-reviewer", f"Review W1.\n{line}\n", self.other)[0], "ok")
        # the opt-in rule holds: a line whose path is under no bound repository's reviews is not this hook's
        self.assertEqual(self.decide("refactor-reviewer", "Record folder: w1/ (note)\n", self.other),
                         ("ok", "Record folder: w1/ (note)\n"))

    # Review findings-fix3.md: the 29 line shapes of its probe lr.py, in its order, P under the bound repo's reviews.
    # Each: the shape after `Record folder: `, the reader's path (None: refused), in care through the line.
    P = "~/repo/records/2026-01-03-T7-x"
    SHAPES = ((P + "/", P, True), ("`" + P + "/`", P, True), ("`" + P + "/`.", None, True), (P + "/.", P, True),
              (P + "/`", None, True), (P + '/"', None, True), (P + "/'", None, True), ("**" + P + "/**", None, True),
              (P + "/**", None, True), ("`" + P + "/'", None, True), ("'" + P + "/`", None, True),
              (P + "/\u200bnotes", None, True), (P + "/ notes", None, True), (P + "/\tnotes", None, True),
              (P + "/\r", P, True), ("``", "", False), ('""', "", False), ("`", None, False),
              (P + "/ " + P + "/", None, True), ("~", "~", False), ("~/", "~", False), ("`" + P + " a/`", P + " a", True),
              (P + "/`x`", None, True), ("`" + P + "/`\u200b", None, True), ('"' + P + '/" ', P, True),
              ("`" + P + "/``", None, True), (P + "/\u3000", P, True), ("'" + P + "/' ", P, True),
              ("~user/x/", "~user/x", False))

    def test_the_29_line_shapes_read_and_care_as_the_table_says(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("agent_roles_table", HOOK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(len(self.SHAPES), 29)
        for shape, path, care in self.SHAPES:
            with self.subTest(shape=shape):
                line = f"Record folder: {shape}"
                got, problem = mod.line_reading(line + "\nnext\n")
                self.assertEqual(got if problem is None else None, path)
                n = len(self.logged())
                d, why = self.decide("refactor-reviewer", f"Review W1.\n{line}\nnext\n", self.other)
                decided = [e["record"]["folder"] for e in self.logged()[n:] if "record" in e]
                if not care:
                    self.assertEqual((d, decided), ("ok", []))
                elif path is None:
                    self.assertEqual(d, "DENY", why)
                else:
                    self.assertEqual(decided, ["records/2026-01-03-T7-x" + path[len(self.P):]])


class TestB2UnreadableInput(DecisionFixture):
    """Review B2, decided: a dispatch whose binding cannot be read, or whose project locator does not answer, is
    refused, the reason naming the real cause, under one deadline for the whole run; a stop still fails open."""

    DEADLINE = 6

    def setUp(self):
        super().setUp()
        # a copy of the runtime, so a test can replace the event writer beside the hook
        self.rt = Path(self.tmp.name).resolve() / "runtime"
        self.rt.mkdir()
        for f in RUNTIME.iterdir():
            if f.is_file():
                shutil.copy2(f, self.rt / f.name)
        self.hook = self.rt / "agent-roles.py"
        self.events = Path(self.tmp.name) / "events.txt"
        self.writer()
        self.line = f"Record folder: {self.rf}/"

    def writer(self, sleep=0):
        w = self.rt / "ops-event.sh"
        w.write_text(f"#!/bin/bash\nsleep {sleep}\necho \"$1 ${{11}}\" >> '{self.events}'\n")
        w.chmod(0o755)

    def gates(self):
        return self.events.read_text().split("\n")[:-1] if self.events.is_file() else []

    def briefs(self):
        return (("role:change:engineer", f"Work order T7: x\n{self.line}\n"), ("role:reviewer", f"Review T7.\n{self.line}\n"),
                ("refactor-reviewer", f"Review T7.\n{self.line}\n"), ("role:code-review", "Review it.\n"))

    def test_b2_a_binding_that_does_not_parse_refuses_naming_the_binding(self):
        for text, why in (('{"owned": true,', "cannot be read"), ("[]", "is not a JSON object")):
            self.bpath.write_text(text)
            for st, brief in self.briefs():
                with self.subTest(text=text, st=st):
                    got, reason = self.decide(st, brief, self.repo)
                    self.assertEqual(got, "DENY")
                    self.assertIn(f"the binding {self.bpath} {why}", reason)
                    self.assertIn("fix that file", reason)
                    self.assertNotIn("Record folder", reason)
        # decided (review F2): each refusal is kept in the log, not sent through the cwd
        self.assertEqual(self.gates(), [])
        self.assertEqual(self.log().count("GATE-EVENT dispatch:binding-unreadable kept in this log"), 8)
        # a built-in agent's dispatch reads no binding, so it is not refused
        self.assertEqual(self.decide("general-purpose", "Look it up.\n", self.repo), ("ok", "Look it up.\n"))

    def test_b2_a_missing_records_folder_refuses_naming_setup(self):
        shutil.rmtree(self.records)
        got, reason = self.decide("role:reviewer", f"Review T7.\n{self.line}\n", self.repo)
        self.assertEqual(got, "DENY")
        self.assertIn(f"the records folder {self.records} is missing", reason)
        self.assertIn("bakehouse setup", reason)
        self.assertNotIn("Record folder", reason)

    def test_b2_a_hung_resolver_with_3_record_lines_refuses_under_the_deadline(self):
        self.slow_git("exec sleep 30")
        t0 = time.monotonic()
        got, reason = self.decide("role:change:engineer", f"Work order T7: x\n{self.line}\n{self.line}\n{self.line}\n",
                                  self.repo)
        secs = time.monotonic() - t0
        self.assertEqual(got, "DENY")
        self.assertIn(f"the project locator `{self.rt / 'locator.py'} binding {self.repo}` did not answer within", reason)
        self.assertNotIn("Record folder", reason)
        self.assertLess(secs, self.DEADLINE)

    def test_b2_the_refusal_event_keeps_the_run_under_the_deadline(self):
        # the whole run, with a hung git and an event writer that hangs too
        self.slow_git("exec sleep 30")
        self.writer(sleep=30)
        t0 = time.monotonic()
        self.assertEqual(self.decide("role:reviewer", f"Review T7.\n{self.line}\n", self.repo)[0], "DENY")
        self.assertLess(time.monotonic() - t0, self.DEADLINE)
        # the event is given what is left of the deadline, not its own 3 s
        import importlib.util
        spec = importlib.util.spec_from_file_location("agent_roles_b2", self.hook)
        mod = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {"BAKEHOUSE_MACHINE_DIR": str(self.machine), "HOME": str(self.home)}):
            spec.loader.exec_module(mod)
            mod.START = time.monotonic() - mod.RUN_BUDGET_S + 1  # 1 s left
            t0 = time.monotonic()
            mod.refusal_event("dispatch:binding-unreadable", "r", "p", self.repo)
            self.assertLess(time.monotonic() - t0, 1.6)
            mod.START = time.monotonic() - mod.RUN_BUDGET_S - 1  # past it: not run at all
            t0 = time.monotonic()
            mod.refusal_event("dispatch:binding-unreadable", "r", "p", self.repo)
            self.assertLess(time.monotonic() - t0, 0.3)

    def test_b2_several_slow_paths_stop_at_the_run_deadline(self):
        # fix round 1 (build survivor V6): a locator that answers each path in about 1.5 s, inside its own 2 s
        # limit; the cwd and four distinct record folders would take about 8 s; the run's deadline refuses before 6 s
        self.slow_git("sleep 0.5")
        lines = "".join(f"Record folder: {self.repo}/records/2026-01-0{i}-T7-x/\n" for i in range(1, 5))
        t0 = time.monotonic()
        got, reason = self.decide("role:reviewer", f"Review T7.\n{lines}", self.repo)
        secs = time.monotonic() - t0
        self.assertEqual(got, "DENY")
        self.assertIn("the project locator", reason)
        self.assertNotIn("Record folder", reason)
        self.assertLess(secs, self.DEADLINE)

    def test_f2_the_refusal_is_logged_locally_with_its_cause(self):
        shutil.copy2(RUNTIME / "ops-event.sh", self.rt / "ops-event.sh")  # the real writer
        self.bpath.write_text('{"owned": true,')
        self.assertEqual(self.decide("role:reviewer", f"Review T7.\n{self.line}\n", self.repo)[0], "DENY")
        kept = [x for x in self.log().splitlines() if "GATE-EVENT dispatch:binding-unreadable kept in this log" in x]
        self.assertEqual(len(kept), 1)
        self.assertIn(f"the binding {self.bpath} cannot be read", kept[0])
        self.assertNotIn("not written: ops-event", self.log())

    def test_f4_a_dangling_binding_refuses_naming_it(self):
        self.bpath.unlink()
        self.bpath.symlink_to(self.bpath.parent / "gone.json")
        got, why = self.decide("role:reviewer", f"Review T7.\n{self.line}\n", self.repo)
        self.assertEqual(got, "DENY")
        self.assertIn(f"the binding {self.bpath} is a symlink to {self.bpath.parent / 'gone.json'}, which does not exist", why)

    def test_f5_a_deadline_used_up_by_earlier_calls_is_named(self):
        self.slow_git("sleep 0.5")
        lines = "".join(f"Record folder: {self.repo}/records/2026-01-0{i}-T7-x/\n" for i in range(1, 5))
        got, why = self.decide("role:reviewer", f"Review T7.\n{lines}", self.repo)
        self.assertEqual(got, "DENY")
        self.assertIn("run deadline ran out: earlier calls in this run", why)

    def test_f6_a_hung_git_is_cut_by_the_run_deadline(self):
        self.slow_git("exec sleep 30")
        lines = "".join(f"Record folder: {self.repo}/records/w{i}/\n" for i in range(1, 6))
        d = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(self.ws),
             "tool_input": {"subagent_type": "role:reviewer", "description": "x", "prompt": f"Review W1.\n{lines}"}}
        t0 = time.monotonic()
        subprocess.run(["python3", str(self.hook)], input=json.dumps(d), capture_output=True, text=True, check=False,
                       env=self.env())
        self.assertLess(time.monotonic() - t0, self.DEADLINE)

    def test_b2_no_binding_is_not_unreadable(self):
        # no team at the cwd: as before, nothing to refuse
        self.assertEqual(self.decide("refactor-reviewer", f"Review.\n{self.line}\n", self.other)[0], "ok")
        self.assertEqual(self.decide("role:code-review", "Review it.\n", self.other)[0], "ok")
        # a repository with no .team/binding.json: no binding, not unreadable
        self.bpath.unlink()
        self.assertEqual(self.decide("role:code-review", "Review it.\n", self.repo)[0], "ok")
        self.assertEqual(self.gates(), [])

    def test_b2_a_stop_still_fails_open_on_an_unreadable_binding(self):
        self.bpath.write_text('{"owned": true,')
        engineer = f"Work order T7: x\n{self.line}\n\n[bakehouse:prediction-record]"
        self.assertEqual(self.stop_in(engineer, {}, cwd=self.repo, agent="refactor-engineer"), "")
        self.assertEqual(self.stop_in(f"Review T7.\n{self.line}\n", {}, cwd=self.repo), "")
        self.assertIn("UNREADABLE binding for", self.log())


class TestReadBinding(Fixture):
    def test_read_binding_tells_none_from_unreadable(self):
        read = lambda p, **k: team_roles.read_binding(p, env=self.env(), **k)
        self.assertEqual(read(self.other), ({}, None))
        self.assertEqual(read(self.repo), ({"owned": True, "roles": {}}, None))
        self.assertEqual(read(self.repo, timeout=0)[1][:24], "the project locator `" + str(RUNTIME)[:3])
        for text, why in (("{", "cannot be read"), ("[1]", "is not a JSON object")):
            self.bpath.write_text(text)
            self.assertIn(why, read(self.repo)[1])
            self.assertEqual(team_roles.load_binding(self.repo), {})
        self.bpath.unlink()
        self.assertEqual(read(self.repo), ({}, None))
        self.bpath.write_text("{}")
        shutil.rmtree(self.records)
        self.assertIn("bakehouse setup", read(self.repo)[1])
        self.assertEqual(team_roles.load_binding(self.repo), {})


class TestTheHooksFolder(Fixture):
    """S16 M2: the dispatch hook judges the hook input's cwd, resolved to its git toplevel; never
    $CLAUDE_PROJECT_DIR. The plugin's switch-on and a project-scope install are read from that toplevel."""

    def project_scope(self, project_path):
        """The plugin switched on in the repo's .claude/settings.json, installed for project_path only."""
        (self.home / ".claude" / "settings.json").write_text("{}")
        (self.repo / ".claude").mkdir(exist_ok=True)
        (self.repo / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {"bakehouse@t": True}}))
        (self.home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"plugins": {"bakehouse@t": [{"scope": "project", "projectPath": str(project_path),
                                          "installPath": str(self.home / "plugin")}]}}))

    def test_a_dispatch_from_a_subfolder_finds_the_project_scope_plugin(self):
        self.project_scope(self.repo)
        (self.repo / "sub" / "deeper").mkdir(parents=True)
        self.project_dir = self.repo
        for cwd in (self.repo, self.repo / "sub", self.repo / "sub" / "deeper"):
            with self.subTest(cwd=cwd):
                self.assertEqual(self.dispatch("role:reviewer", cwd=cwd)["subagent_type"], "bakehouse:refactor-reviewer")
                self.assertEqual(self.dispatch("role:architect", cwd=cwd)["subagent_type"], "bakehouse:architect")

    def test_a_project_path_is_compared_resolved_and_whole(self):
        link = Path(self.tmp.name).resolve() / "link"
        link.symlink_to(self.repo)
        self.project_scope(link)
        self.assertEqual(self.dispatch("role:architect")["subagent_type"], "bakehouse:architect")
        # /a/proj2 is not /a/proj
        sibling = Path(f"{self.repo}2")
        project(sibling, {"owned": True, "roles": {}})
        (sibling / ".claude").mkdir()
        (sibling / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {"bakehouse@t": True}}))
        self.project_scope(self.repo)
        self.assertEqual(self.dispatch("role:architect", cwd=sibling), "DENY")

    def test_a_folder_outside_the_repo_is_judged_as_itself(self):
        self.binding({"engineer": "design-author"})
        self.project_dir = self.repo
        self.assertEqual(self.dispatch("role:engineer", cwd=self.other)["subagent_type"], "bakehouse:refactor-engineer")

    def test_the_repo_is_judged_when_the_project_dir_is_a_parent_that_is_no_repo(self):
        self.binding({"engineer": "design-author"})
        self.project_dir = Path(self.tmp.name).resolve()
        self.assertEqual(self.dispatch("role:engineer")["subagent_type"], "design-author")
        # a record-folder refusal holds too (pass 2's ANDON-2 shape)
        ti = {"subagent_type": "bakehouse:refactor-reviewer", "description": "d",
              "prompt": "Review it.\nRecord folder: axis/runs/x/\n"}
        self.assertEqual(self.dispatch("x", tool_input=ti), "DENY")


class TestResolve(unittest.TestCase):
    def test_levels_in_order(self):
        lv = [("binding roles", {"reviewer": "b"}), ("personal", {"reviewer": {"providers": ["p"]}}),
              ("shipped", {"reviewer": {"providers": ["s"]}})]
        self.assertEqual(team_roles.resolve("reviewer", lv, {"b", "p", "s"})["agent"], "b")
        self.assertEqual(team_roles.resolve("reviewer", lv, {"p", "s"})["where"], "personal")
        r = team_roles.resolve("refactor:reviewer", lv, {"s"})
        self.assertEqual((r["agent"], r["key"], len(r["skipped"])), ("s", "reviewer", 2))

    def test_fallbacks(self):
        self.assertEqual(team_roles.resolve("x:reviewer", [], set())["agent"], "general-purpose")
        self.assertEqual(team_roles.resolve("code-review", [], set())["fallback"], "general-purpose")
        r = team_roles.resolve("refactor:engineer", [], set())
        self.assertEqual((r["agent"], r["fallback"]), (None, "session"))


if __name__ == "__main__":
    unittest.main()
