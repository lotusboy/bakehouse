"""Tests for the gate-refused event: ops-event.sh's gate field, runtime/gate_event.py, and the refusal
sites that write it (docs/PROJECT-TEAM.md, "Gate refusals"). Standard library only:
python3 -m unittest discover -s runtime/tests

Every test writes to a scratch project with one owned binding, never to a real ledger."""

import ast
import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
GIT = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


class Scratch(unittest.TestCase):
    """A scratch project: runtime/ holds a copy of every runtime file (so a test can replace one, and each
    tool finds its neighbours in its own folder); the repository `work` has an owned .team/binding.json
    and a records worktree named by `git config bakehouse.records`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name).resolve()
        self.root, self.rt, self.work = t, t / "runtime", t / "work"
        self.machine = t / "machine"
        self.rt.mkdir()
        for f in RUNTIME.iterdir():
            if f.is_file():
                shutil.copy2(f, self.rt / f.name)
        self.binding = {
            "owned": True,
            "reviews": "runs/",
            "reviewName": "{date}-{id}-step{n}-review",
            "tracker": {"file": "docs/TRACKER.md", "id": "^T[0-9]+$"},
        }
        subprocess.run(["git", "init", "-q", str(self.work)], check=True)
        self.git("commit", "-q", "--allow-empty", "-m", "first")
        (self.work / ".team").mkdir()
        (self.work / ".team" / "binding.json").write_text(json.dumps(self.binding))
        self.records = t / "work.team-records"
        self.git("worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(self.records))
        self.git("config", "bakehouse.records", str(self.records))
        self.ledger = self.records / "ops" / "events.jsonl"
        self.env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("PREDICT_GATE", "OPS_", "SPEND_", "DECISION_", "BAKEHOUSE_", "CLAUDE_PROJECT_DIR"))
        }
        self.env.update(
            GIT, BAKEHOUSE_MACHINE_DIR=str(self.machine),
            DECISION_NOW="2026-01-02T12:00:00+00:00",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, repo=None):
        return subprocess.run(
            ["git", "-C", str(repo or self.work), *args],
            capture_output=True,
            text=True,
            env=dict(os.environ, **GIT),
            check=True,
        )

    def run_(self, argv, cwd=None, env=None, stdin=None):
        return subprocess.run(
            argv,
            cwd=cwd or self.work,
            capture_output=True,
            text=True,
            input=stdin,
            env=dict(self.env, **(env or {})),
            check=False,
        )

    def events(self):
        return (
            [json.loads(x) for x in self.ledger.read_text().splitlines()]
            if self.ledger.exists()
            else []
        )

    def refusals(self):
        return [e for e in self.events() if e["kind"] == "gate-refused"]


class TestOpsEvent(Scratch):
    def ops(self, *args, env=None, cwd=None):
        return self.run_([str(self.rt / "ops-event.sh"), *args], env=env, cwd=cwd)

    def test_an_event_without_a_gate_has_no_gate_key(self):
        p = self.ops("note", "T1", "a note", "-", "-", "3", "-", "-", "done", "x")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("gate", json.loads(p.stdout))
        self.assertEqual(
            list(self.events()[0]),
            [
                "ts",
                "role",
                "kind",
                "task",
                "purpose",
                "model",
                "effort",
                "count",
                "tokens",
                "durationS",
                "outcome",
                "note",
                "commit",
            ],
        )
        p = self.ops(
            "gate-refused",
            "T1",
            "a refusal",
            "-",
            "-",
            "-",
            "-",
            "-",
            "refused",
            "x",
            "decision:by",
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(list(self.events()[1])[-1], "gate")
        self.assertEqual(self.events()[1]["gate"], "decision:by")

    def test_a_turn_cap_keeps_turns_used_briefed_and_cap(self):
        p = self.ops(
            "agent-run", "T1", "reviewer: T1", "opus", "-", "101", "197952", "1229",
            "turn-cap", "briefed 150 of cap 100; findings.md a draft",
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        e = self.events()[0]
        self.assertEqual((e["count"], e["turnsBriefed"], e["turnCap"]), (101, 150, 100))
        p = self.ops("agent-run", "T1", "r", "-", "-", "60", "-", "-", "turn-cap", "briefed - of cap 60")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIsNone(self.events()[1]["turnsBriefed"])
        self.assertNotIn("turnCap", self.ops("note", "T1", "n").stdout)

    def test_a_malformed_turn_cap_is_refused(self):
        for args, why in (
            (("note", "T1", "n", "-", "-", "5", "-", "-", "turn-cap", "briefed 5 of cap 9"), "agent-run outcome"),
            (("agent-run", "T1", "r", "-", "-", "-", "-", "-", "turn-cap", "briefed 5 of cap 9"), "count"),
            (("agent-run", "T1", "r", "-", "-", "5", "-", "-", "turn-cap", "hit the cap"), "briefed"),
        ):
            with self.subTest(why=why):
                p = self.ops(*args)
                self.assertEqual(p.returncode, 1)
                self.assertIn(why, p.stderr)
        self.assertEqual(self.events(), [])

    def test_a_gate_on_another_kind_is_refused(self):
        p = self.ops(
            "note", "T1", "a note", "-", "-", "-", "-", "-", "done", "x", "decision:by"
        )
        self.assertEqual(p.returncode, 1)
        self.assertIn("only on a gate-refused event", p.stderr)
        self.assertEqual(self.events(), [])

    def test_a_malformed_gate_is_refused(self):
        for bad in (
            "Decision:by",
            "decision",
            "decision:",
            ":by",
            "decision:by:x",
            "decision: by",
        ):
            with self.subTest(gate=bad):
                p = self.ops(
                    "gate-refused",
                    "T1",
                    "r",
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                    "refused",
                    "x",
                    bad,
                )
                self.assertEqual(p.returncode, 1)
                self.assertIn("is not <tool>:<check>", p.stderr)
        self.assertEqual(self.events(), [])

    def test_ops_repo_names_the_project(self):
        # from outside any repository: the named project's ledger, commit its HEAD
        p = self.ops(
            "gate-refused",
            "-",
            "r",
            "-",
            "-",
            "-",
            "-",
            "-",
            "refused",
            "x",
            "spend:by",
            env={"OPS_REPO": str(self.work)},
            cwd=self.root,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        head = self.git("rev-parse", "--short", "HEAD").stdout.strip()
        self.assertEqual(self.events()[0]["commit"], head)
        elsewhere = self.root / "elsewhere"
        subprocess.run(["git", "init", "-q", str(elsewhere)], check=True)
        for bad in (self.root, elsewhere):
            with self.subTest(repo=bad):
                self.assertEqual(
                    self.ops("note", "-", "r", env={"OPS_REPO": str(bad)}).returncode, 1
                )
        (self.work / ".team" / "binding.json").write_text(json.dumps({"owned": False}))
        self.assertEqual(
            self.ops("note", "-", "r", env={"OPS_REPO": str(self.work)}).returncode, 1
        )
        self.assertEqual(len(self.events()), 1)


class TestGateEvent(Scratch):
    def refused(self, path, cwd):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import gate_event; "
                "print(gate_event.refused('decision:by', 'p', 'n', task='T1', path=sys.argv[2] or None))")
        return self.run_(["python3", "-c", code, str(self.rt), str(path or "")], cwd=cwd)

    def test_the_event_goes_to_the_project_of_the_path_else_of_the_working_folder(self):
        elsewhere = self.root / "elsewhere"
        subprocess.run(["git", "init", "-q", str(elsewhere)], check=True)
        (self.work / "design").mkdir()
        rec = self.work / "design" / "t1.json"
        p = self.refused(rec, cwd=elsewhere)
        self.assertEqual(p.stdout, "None\n", p.stderr)
        (ev,) = self.refusals()
        self.assertEqual((ev["gate"], ev["task"], ev["purpose"], ev["note"]), ("decision:by", "T1", "p", "n"))
        p = self.refused(None, cwd=self.work / "design")
        self.assertEqual(p.stdout, "None\n", p.stderr)
        self.assertEqual(len(self.refusals()), 2)
        # a folder with no team: the reason comes back, and nothing is written
        p = self.refused(None, cwd=elsewhere)
        self.assertIn("has no team", p.stdout)
        self.assertEqual(len(self.events()), 2)


PG_BASE = {"rules:fired": 120, "gaps:open": 9}
PG_GOOD = {"moves": [{"what": "rules:fired", "from": 120, "to": 121}]}
RESULT = [
    "--why",
    "a build agent",
    "--predicted",
    "the gate passes",
    "--predicted-tokens",
    "100",
    "--result",
    "the gate passed",
    "--outcome",
    "as-predicted",
]


class SpendScratch(Scratch):
    def spend(self, *args, env=None, script=None):
        return self.run_(
            [str(script or self.rt / "spend.sh"), *args], env=env
        )

    def rec(self, agent, *extra, env=None, script=None):
        return self.spend(
            "record",
            "--agent",
            agent,
            "--label",
            f"job {agent}",
            "--tokens",
            "100",
            "--no-ops",
            *extra,
            env=env,
            script=script,
        )

    def rows(self):
        p = self.records / "spend" / "ledger.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def kind_of(self, agent):
        return [r for r in self.rows() if r.get("agent") == agent][-1]["kind"]


class TestRefusalsWriteOneEvent(SpendScratch):
    def decision(self, *args, env=None):
        return self.run_([str(self.rt / "decision.sh"), *args], env=env)

    def test_decision_refusal_writes_one_event_with_its_gate(self):
        p = self.decision("ask", "--category", " ")
        self.assertEqual(
            (p.returncode, p.stdout, p.stderr),
            (1, "", "decision: --category is empty\n"),
        )
        (ev,) = self.refusals()
        self.assertEqual(
            (ev["gate"], ev["purpose"], ev["outcome"], ev["task"], ev["note"]),
            (
                "decision:category",
                "decision.sh refused",
                "refused",
                None,
                "decision: --category is empty",
            ),
        )
        p = self.decision(
            "ask",
            "--category",
            "spend",
            "--question",
            "Go?",
            "--option",
            "a",
            "--option",
            "b",
            "--recommendation",
            "a",
            "--confidence",
            "0.5",
            "--evidence-kind",
            "measured",
            "--evidence",
            "x",
            "--task",
            "T1",
            "--fix-round",
            "0",
        )
        self.assertEqual(p.returncode, 1)
        self.assertEqual(
            [(e["gate"], e["task"]) for e in self.refusals()],
            [("decision:category", None), ("decision:fix-round-number", "T1")],
        )
        # no team: nothing to write to, and nothing written (the boundary)
        self.assertEqual(self.decision("score", "--repo", str(self.root)).returncode, 1)
        self.assertEqual(len(self.events()), 2)

    def test_decision_refusal_output_is_unchanged_without_a_ledger(self):
        args = ("answer", "d20990101-ffffff", "--choice", "yes")
        with_ledger = self.decision(*args)
        (self.work / ".team" / "binding.json").write_text(json.dumps({"owned": False}))
        without = self.decision(*args)
        self.assertEqual(len(self.refusals()), 1)
        self.assertEqual(
            (with_ledger.returncode, with_ledger.stdout, with_ledger.stderr),
            (without.returncode, without.stdout, without.stderr),
        )
        self.assertEqual(
            without.stderr, "decision: no decision d20990101-ffffff in work\n"
        )

    def test_spend_refusal_writes_one_event_with_its_gate(self):
        p = self.spend("wave", "open")
        self.assertEqual(
            (p.returncode, p.stderr), (1, "spend: wave open needs a name\n")
        )
        self.assertEqual(
            self.rec("ar1", "--kind", "architect", "--task", "T5", *RESULT).returncode,
            0,
        )
        p = self.rec("ar2", "--kind", "architect", "--task", "T5", *RESULT)
        self.assertEqual(p.returncode, 1)
        self.assertIn("give --stepped-back", p.stderr)
        self.assertEqual(
            [
                (e["gate"], e["task"], e["purpose"], e["outcome"])
                for e in self.refusals()
            ],
            [
                ("spend:wave-args", None, "spend.sh refused", "refused"),
                ("spend:stepped-back", "T5", "spend.sh refused", "refused"),
            ],
        )
        self.assertEqual(self.refusals()[1]["note"], p.stderr.strip())

    # ---------- predict-gate ----------

    def trio(self, folder, measured, prediction=None):
        folder.mkdir(parents=True, exist_ok=True)
        for n, data in (
            ("moves.prediction.json", prediction or PG_GOOD),
            ("moves.baseline.json", PG_BASE),
            ("moves.measured.json", measured),
        ):
            (folder / n).write_text(json.dumps(data))
        return folder / "moves.measured.json"

    def pg(self, *args, env=None):
        return self.run_(
            [str(self.rt / "predict-gate.sh"), *args], env=env
        )

    def test_predict_gate_check_refusal_writes_one_event(self):
        m = self.trio(
            self.work / "runs" / "x", {**PG_BASE, "rules:fired": 121, "gaps:open": 11}
        )
        p = self.pg("check", "--measured", str(m), "--task", "T7")
        self.assertEqual(p.returncode, 1)
        self.assertIn("4. Moved, and nobody predicted it: 1.", p.stdout)
        (ev,) = self.refusals()
        self.assertEqual(
            (ev["gate"], ev["task"], ev["count"], ev["outcome"], ev["purpose"]),
            ("predict-gate:check", "T7", 1, "refused", "predicted-moves gate refused"),
        )
        self.assertIn("unpredicted 1", ev["note"])
        # the owner's override: still one event, the override's own, now with its gate
        p = self.pg(
            "check",
            "--measured",
            str(m),
            "--task",
            "T7",
            "--override",
            "owner said yes",
        )
        self.assertEqual(p.returncode, 0)
        self.assertEqual(
            [(e["gate"], e["purpose"], e["outcome"]) for e in self.refusals()],
            [
                ("predict-gate:check", "predicted-moves gate refused", "refused"),
                ("predict-gate:check", "predicted-moves gate overridden", "done"),
            ],
        )
        # a check that passes writes nothing
        ok = self.trio(self.work / "runs" / "y", {**PG_BASE, "rules:fired": 121})
        self.assertEqual(self.pg("check", "--measured", str(ok)).returncode, 0)
        self.assertEqual(len(self.events()), 2)

    def test_predict_gate_check_refusal_goes_to_the_records_project_not_the_working_folders(self):
        m = self.trio(
            self.work / "runs" / "x",
            {**PG_BASE, "rules:fired": 121, "gaps:open": 11},
        )
        gate = [str(self.rt / "predict-gate.sh"), "check", "--measured", str(m)]
        self.assertEqual(
            self.run_(gate, cwd=self.root).returncode,
            1,
            "run from a folder outside any repository",
        )
        self.assertEqual(self.pg("check", "--measured", str(m)).returncode, 1)
        self.assertEqual(
            [e["gate"] for e in self.refusals()], ["predict-gate:check"] * 2
        )
        # the override is recorded in the record's project too
        p = self.run_(gate + ["--override", "owner said yes"], cwd=self.root)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(
            [(e["gate"], e["outcome"]) for e in self.refusals()],
            [("predict-gate:check", "refused")] * 2 + [("predict-gate:check", "done")],
        )
        self.assertEqual(len(self.events()), 3)

    def test_predict_gate_hook_refusal_writes_one_event_per_commit(self):
        hook = self.work / ".git" / "hooks" / "pre-commit"
        hook.write_text(f'#!/bin/sh\nexec "{self.rt / "predict-gate.sh"}" hook\n')
        hook.chmod(0o755)
        for name in ("2026-01-02-T8-step1-review", "2026-01-02-T9-step1-review"):
            self.trio(
                self.work / "runs" / name,
                {**PG_BASE, "rules:fired": 121, "gaps:open": 11},
            )
        self.git("add", "-A")
        p = self.run_(
            ["git", "-C", str(self.work), "commit", "-q", "-m", "records"],
            env={"PREDICT_GATE_TASK": "T8"},
        )
        self.assertNotEqual(p.returncode, 0)
        (ev,) = self.refusals()
        self.assertEqual(
            (ev["gate"], ev["task"], ev["count"], ev["purpose"]),
            ("predict-gate:hook", "T8", 2, "predicted-moves commit refused"),
        )
        p = self.run_(
            ["git", "-C", str(self.work), "commit", "-q", "-m", "records"],
            env={"PREDICT_GATE_OVERRIDE": "owner said yes", "PREDICT_GATE_TASK": "T8"},
        )
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(
            [(e["gate"], e["outcome"]) for e in self.refusals()],
            [
                ("predict-gate:hook", "refused"),
                ("predict-gate:hook", "done"),
                ("predict-gate:hook", "done"),
            ],
        )

    def test_predict_gate_close_refusal_then_override_keeps_the_override_last(self):
        self.trio(
            self.work / "runs" / "2026-01-02-T7-step1-review",
            {**PG_BASE, "rules:fired": 121, "gaps:open": 11},
        )
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "T7 record")
        p = self.pg("close", "T7", "--repo", str(self.work))
        self.assertEqual(p.returncode, 1)
        self.assertIn("Result: REFUSED.", p.stdout)
        self.assertEqual(
            [(e["gate"], e["purpose"], e["task"]) for e in self.refusals()],
            [("predict-gate:close", "work order close refused", "T7")],
        )
        p = self.pg(
            "close",
            "T7",
            "--repo",
            str(self.work),
            env={"PREDICT_GATE_CLOSE_OVERRIDE": "owner said yes"},
        )
        self.assertEqual(p.returncode, 0, p.stdout)
        refused, closed = self.events()[-2:]
        self.assertEqual(
            (refused["kind"], refused["purpose"], refused["gate"]),
            ("gate-refused", "work order close overridden", "predict-gate:close"),
        )
        self.assertEqual(closed["kind"], "work-order-closed")
        self.assertEqual(len(self.events()), 3)

    def test_predict_gate_bad_input_writes_one_event(self):
        m = self.trio(
            self.work / "runs" / "x",
            {**PG_BASE, "rules:fired": 121},
            prediction={"moves": [{"what": "rules:fired", "range": "120-130"}]},
        )
        p = self.pg("check", "--measured", str(m), "--task", "T7")
        self.assertEqual(p.returncode, 2)
        self.assertTrue(p.stderr.startswith("predict-gate: bad input:"))
        p = self.pg("close", "BAD-1", "--repo", str(self.work))
        self.assertEqual(p.returncode, 2)
        self.assertEqual(
            [(e["gate"], e["task"]) for e in self.refusals()],
            [("predict-gate:input", "T7"), ("predict-gate:input", "BAD-1")],
        )
        # argparse's own error is not a gate refusal
        self.assertEqual(self.pg("check").returncode, 2)
        self.assertEqual(len(self.events()), 2)

    # ---------- records-check ----------

    def rc(self, *args):
        return self.run_(
            ["python3", str(self.rt / "records-check.py"), *args]
        )

    def test_records_check_bad_input_writes_one_event(self):
        bad = self.root / "bad.json"
        bad.write_text("[]")
        rule = self.root / "rule.json"
        rule.write_text(
            json.dumps(
                {
                    **self.binding,
                    "records": {"jobs": {"change": {"remove": ["result"]}}},
                }
            )
        )
        for b in (bad, rule):
            p = self.rc("--binding", str(b), "--repo", str(self.work), "--no-write")
            self.assertEqual(p.returncode, 2)
            self.assertTrue(p.stderr.startswith("records-check: bad input:"))
        self.assertEqual(
            [(e["gate"], e["purpose"]) for e in self.refusals()],
            [
                ("records-check:input", "records-check refused its input"),
                ("records-check:binding-records", "records-check refused its input"),
            ],
        )

    def test_records_check_register_run_writes_no_event(self):
        (self.work / "src").mkdir()
        (self.work / "src" / "a.py").write_text("x\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "T7: the change")
        self.trio(
            self.work / "runs" / "2026-01-02-T7-step1-review",
            {**PG_BASE, "rules:fired": 121, "gaps:open": 11},
        )
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "T7 record")
        self.assertEqual(self.pg("close", "T7", "--repo", str(self.work)).returncode, 1)
        p = self.pg(
            "close",
            "T7",
            "--repo",
            str(self.work),
            env={"PREDICT_GATE_CLOSE_OVERRIDE": "owner said yes"},
        )
        self.assertEqual(p.returncode, 0, p.stdout)
        before = self.ledger.read_text()
        reg = self.root / "records.json"
        p = self.rc(
            "--binding",
            str(self.work / ".team" / "binding.json"),
            "--repo",
            str(self.work),
            "--code-roots",
            "src",
            "--register",
            str(reg),
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.ledger.read_text(), before)
        self.assertEqual(
            json.loads(reg.read_text())["workOrders"]["T7"]["close"], "overridden"
        )

    # ---------- the dispatch and stop hooks ----------

    def hook(self, d):
        return self.run_(
            ["python3", str(self.rt / "agent-roles.py")],
            stdin=json.dumps(d),
            env={"HOME": str(self.home())},
        )

    def home(self):
        """A scratch HOME where the two agents are user agents and the bakehouse plugin's (`bakehouse:<name>`,
        as the shipped table names them)."""
        h = self.root / "home"
        if not h.exists():
            (h / ".claude" / "agents").mkdir(parents=True)
            (h / "plugin" / "agents").mkdir(parents=True)
            for n in ("engineer", "reviewer"):
                for d in (h / ".claude" / "agents", h / "plugin" / "agents"):
                    (d / f"{n}.md").write_text(f"---\nname: {n}\n---\n")
            (h / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {"bakehouse@t": True}}))
            (h / ".claude" / "plugins").mkdir()
            (h / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps(
                {"plugins": {"bakehouse@t": [{"scope": "user", "installPath": str(h / "plugin")}]}}))
        return h

    def pre(self, st, prompt, cwd=None):
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "cwd": str(cwd or self.work),
            "tool_input": {"subagent_type": st, "description": "x", "prompt": prompt},
        }

    def sent(self, st, prompt, cwd=None):
        """The prompt a dispatch the hook allows hands the agent; the hook decides its record folder here."""
        out = json.loads(self.hook(self.pre(st, prompt, cwd)).stdout)
        return out["hookSpecificOutput"]["updatedInput"]["prompt"]

    def stop(self, agent, prompt, name, **extra):
        t = self.root / f"{name}.jsonl"
        t.write_text(json.dumps({"message": {"content": prompt}}) + "\n")
        return {
            "hook_event_name": "SubagentStop",
            "agent_type": agent,
            "agent_transcript_path": str(t),
            "cwd": str(self.work),
            **extra,
        }

    def verdict(self, p):
        out = json.loads(p.stdout) if p.stdout.strip() else {}
        return (
            (out.get("hookSpecificOutput") or {}).get("permissionDecision")
            or out.get("decision")
            or "none"
        )

    def test_hook_deny_and_block_write_one_event_each(self):
        rec, rv = self.work / "runs" / "t7", self.work / "runs" / "rv"
        rec.mkdir(parents=True)
        calls = [
            self.pre("role:no-such-role", "p"),
            self.pre(
                "role:change:engineer",
                "Work order T7: x\nRecord folder: relative/t7/\n",
            ),
            self.stop(
                "engineer",
                f"Work order T7: x\nRecord folder: {rec}/\n\n[bakehouse:prediction-record]",
                "e",
            ),
            self.stop("reviewer", "Review W1.\n", "r"),
        ]
        verdicts = [self.verdict(self.hook(d)) for d in calls]
        # the reviewer's record folder is decided at its dispatch, which is allowed and writes no event
        artefacts = self.stop(
            "reviewer", self.sent("role:reviewer", f"Review W1.\nRecord folder: {rv}/\n"), "a"
        )
        (rv / "review").mkdir(parents=True)
        (rv / "review" / "findings.md").write_text("F1\n")
        verdicts.append(self.verdict(self.hook(artefacts)))
        self.assertEqual(verdicts, ["deny", "deny", "block", "block", "block"])
        self.assertEqual(
            [(e["gate"], e["task"], e["purpose"]) for e in self.refusals()],
            [
                ("dispatch:role-unresolved", None, "agent dispatch refused"),
                ("dispatch:record-folder", "T7", "agent dispatch refused"),
                ("stop:prediction", "T7", "agent stop refused"),
                ("stop:run-record", None, "agent stop refused"),
                ("stop:record-artefacts", None, "agent stop refused"),
            ],
        )
        # the second stop lets the agent go: not a refusal, no event
        self.assertEqual(
            self.verdict(self.hook(dict(artefacts, stop_hook_active=True))), "none"
        )
        self.assertEqual(len(self.events()), 5)

    def test_stop_routes_by_the_record_folder_not_the_working_folder(self):
        """The record folder is in the project `work`, decided at dispatch; the agent's working folder is a
        repository with no team. The stop's event goes to `work`: routed by the record, not by the working
        folder (review F10)."""
        elsewhere = self.root / "elsewhere"
        subprocess.run(["git", "init", "-q", str(elsewhere)], check=True)
        rv = self.work / "runs" / "rv"
        sent = self.sent("role:reviewer", f"Review W1.\nRecord folder: {rv}/\n", cwd=elsewhere)
        d = self.stop("reviewer", sent, "a")
        d["cwd"] = str(elsewhere)
        (rv / "review").mkdir(parents=True)
        (rv / "review" / "findings.md").write_text("F1\n")
        self.assertEqual(self.verdict(self.hook(d)), "block")
        self.assertEqual(
            [e["gate"] for e in self.refusals()], ["stop:record-artefacts"]
        )

    def test_a_slow_ledger_write_is_cut_at_three_seconds(self):
        """A writer that hangs (with a child holding its pipes) is cut at gate_event.WAIT_S; the tool's
        exit code and output do not change, and it finishes well inside a hook's 10 s (review F7)."""
        import time

        fast = self.decision("ask", "--category", " ")
        ops = self.rt / "ops-event.sh"
        ops.unlink()
        ops.write_text("#!/bin/sh\nsleep 30\n")
        ops.chmod(0o755)
        t0 = time.monotonic()
        slow = self.decision("ask", "--category", " ")
        took = time.monotonic() - t0
        self.assertEqual(
            (slow.returncode, slow.stdout, slow.stderr),
            (fast.returncode, fast.stdout, fast.stderr),
        )
        self.assertLess(took, 8)
        self.assertGreaterEqual(took, 3)
        self.assertEqual(len(self.refusals()), 1)

    def test_a_cut_write_leaves_no_writer_process_alive(self):
        """A writer cut at gate_event.WAIT_S is killed with its whole process group: neither it nor a child
        it started is alive afterwards, so nothing can append after the tool has returned (re-check N2)."""
        import time

        pids = self.root / "writer.pids"
        ops = self.rt / "ops-event.sh"
        ops.unlink()
        ops.write_text(
            f"#!/bin/sh\necho $$ > {pids}\nsleep 37 &\necho $! >> {pids}\nwait\n"
        )
        ops.chmod(0o755)
        p = self.decision("ask", "--category", " ")
        self.assertEqual(
            (p.returncode, p.stderr), (1, "decision: --category is empty\n")
        )
        alive = [int(x) for x in pids.read_text().split()]
        self.assertEqual(len(alive), 2)
        deadline = time.monotonic() + 3
        try:
            while alive and time.monotonic() < deadline:
                for pid in list(alive):
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        alive.remove(pid)
                time.sleep(0.05)
            self.assertEqual(alive, [], "a cut writer, or its child, is still running")
        finally:
            for pid in alive:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, 9)
        self.assertEqual(self.events(), [])

    def test_hook_without_ops_event_stays_silent(self):
        d = self.pre("role:no-such-role", "p")
        with_ledger = self.hook(d)
        (self.rt / "ops-event.sh").unlink()
        without = self.hook(d)
        self.assertEqual(
            (without.returncode, without.stdout, without.stderr),
            (with_ledger.returncode, with_ledger.stdout, ""),
        )
        self.assertEqual(len(self.events()), 1)
        log = (self.machine / "logs" / "agent-roles.log").read_text()
        self.assertIn(
            "GATE-EVENT dispatch:role-unresolved not written: cannot run ops-event.sh",
            log,
        )


class TestSpendKinds(SpendScratch):
    def table(self):
        return json.loads((RUNTIME / "agent-roles.json").read_text())

    def test_agent_is_refused_on_a_new_first_record(self):
        p = self.rec("n1", "--kind", "agent", *RESULT)
        self.assertEqual(p.returncode, 1)
        self.assertTrue(
            p.stderr.startswith(
                "spend: --kind agent names no role, so a new record may not take it"
            )
        )
        self.assertNotIn("agent", p.stderr.split("one of ")[1].strip().split(", "))
        self.assertEqual(self.rows(), [])
        # a backfilled row may carry it; a correction that keeps it may; one that changes to it may not
        bf = {"SPEND_TS": "2026-01-02T10:00:00+00:00"}
        self.assertEqual(
            self.rec(
                "o1", "--kind", "agent", "--note", "from a log", env=bf
            ).returncode,
            0,
        )
        self.assertEqual(self.rec("o1", "--correct").returncode, 0)
        self.assertEqual(self.rec("o1", "--correct", "--kind", "agent").returncode, 0)
        self.assertEqual(self.rec("r1", "--kind", "reviewer", *RESULT).returncode, 0)
        self.assertEqual(self.rec("r1", "--correct", "--kind", "agent").returncode, 1)
        self.assertEqual(
            [r["kind"] for r in self.rows()], ["agent", "agent", "agent", "reviewer"]
        )
        self.assertEqual(
            [e["gate"] for e in self.refusals()], ["spend:kind-not-a-role"] * 2
        )

    def test_kinds_are_the_roles_and_spend_kinds(self):
        p = self.spend("record", "--help")
        t = self.table()
        kinds = sorted(set(t["roles"]) | set(t["spend_kinds"]))
        self.assertIn(
            "{" + ",".join(kinds) + "}", p.stdout.replace("\n", "").replace(" ", "")
        )
        # the kinds in use before the list was derived are all still kinds
        self.assertLessEqual(
            {"agent", "answering-agent", "reviewer", "architect"}, set(kinds)
        )
        self.assertEqual(
            t["spend_kinds"],
            {"agent": {"newRecords": False}, "answering-agent": {"newRecords": True},
             "no-role": {"newRecords": True}},
        )

    def test_b5_an_agent_that_ran_in_no_role_is_recorded_as_no_role(self):
        # an Explore search, an ad-hoc general-purpose agent or a fork: the PM records every agent
        p = self.rec("x1", "--kind", "no-role", *RESULT)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.kind_of("x1"), "no-role")
        self.assertEqual(self.refusals(), [])
        p = self.rec("x2", *RESULT)
        self.assertIn("no-role", p.stderr.split("one of ")[1].split(" (runtime")[0].split(", "))

    def test_a_role_added_to_the_table_is_a_kind(self):
        rt = self.root / "other-runtime"
        rt.mkdir()
        shutil.copy(RUNTIME / "spend.py", rt / "spend.py")
        shutil.copy(RUNTIME / "locator.py", rt / "locator.py")
        t = self.table()
        t["roles"]["scribe"] = {"providers": []}
        (rt / "agent-roles.json").write_text(json.dumps(t))
        p = self.run_(
            [
                "python3",
                str(rt / "spend.py"),
                "record",
                "--agent",
                "s1",
                "--label",
                "j",
                "--tokens",
                "5",
                "--kind",
                "scribe",
                "--no-ops",
                *RESULT,
            ]
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.kind_of("s1"), "scribe")
        self.assertEqual(self.rec("s2", "--kind", "scribe", *RESULT).returncode, 2)

    def test_an_unknown_kind_is_argparse_error(self):
        p = self.rec("x1", "--kind", "intern", *RESULT)
        self.assertEqual(p.returncode, 2)
        self.assertIn("invalid choice: 'intern'", p.stderr)
        self.assertEqual((self.rows(), self.events()), ([], []))

    def test_a_first_record_without_a_kind_is_refused(self):
        p = self.rec("x1", *RESULT)
        self.assertEqual(p.returncode, 1)
        self.assertTrue(
            p.stderr.startswith(
                "spend: a first record names the role the agent ran as: give --kind"
            )
        )
        self.assertEqual(self.rows(), [])
        # missing both: the first-record refusal comes first, its message unchanged
        p = self.rec("x2")
        self.assertIn(
            "spend: a first record states the tool's result, not only its cost: give --why",
            p.stderr,
        )
        self.assertEqual(
            [e["gate"] for e in self.refusals()],
            ["spend:kind-required", "spend:first-record"],
        )
        self.assertEqual(self.rec("x1", "--kind", "engineer", *RESULT).returncode, 0)

    def test_a_correction_keeps_the_kind_of_the_row_it_corrects(self):
        self.assertEqual(self.rec("c1", "--kind", "reviewer", *RESULT).returncode, 0)
        self.assertEqual(self.rec("c1", "--correct").returncode, 0)
        self.assertEqual(self.kind_of("c1"), "reviewer")
        self.assertEqual(
            self.rec("c1", "--correct", "--kind", "engineer").returncode, 0
        )
        self.assertEqual(self.rec("c1", "--correct").returncode, 0)
        self.assertEqual(
            [r["kind"] for r in self.rows()],
            ["reviewer", "reviewer", "engineer", "engineer"],
        )

    def test_a_backfilled_row_without_a_kind_is_agent(self):
        p = self.rec(
            "b1", "--note", "from a log", env={"SPEND_TS": "2026-01-02T10:00:00+00:00"}
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.kind_of("b1"), "agent")
        self.assertEqual(self.spend("status", "--all").returncode, 0)


# ---------- F3, F4: every refusal site names its own gate, and no refusal form goes without one ----------

GATE_ARG = {
    "fail": 1,
    "refusal_event": 0,
    "ops_event": 6,
    "BadInput": 1,
}  # where the gate id sits
EVENT_CALLS = ("refusal_event", "refused")  # calls that write a refusal event
TOOL_FILES = (
    "decision.py",
    "spend.py",
    "predict-gate.py",
    "records-check.py",
    "agent-roles.py",
)


def _lead(node):
    """The first literal text of a message argument, up to 40 characters."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value[:40]
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) else "{}" for v in node.values
        )[:40]
    if isinstance(node, ast.BinOp):
        return _lead(node.left)
    if isinstance(node, ast.IfExp):
        return _lead(node.body)
    return ""


def _gate_const(node):
    return (
        node.value
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and ":" in node.value
        else None
    )


def gate_sites(runtime=RUNTIME):
    """(file, function, the message's first text or the callee, gate id) for every place a gate id is named:
    a positional gate argument, a gate= keyword, or a gate parameter's default."""
    out = []
    for t in TOOL_FILES:
        tree = ast.parse((runtime / t).read_text())
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            a = fn.args
            for arg, d in zip(a.args[len(a.args) - len(a.defaults) :], a.defaults):
                if arg.arg == "gate" and _gate_const(d):
                    out.append((t, fn.name, "default", _gate_const(d)))
            for c in ast.walk(fn):
                if not isinstance(c, ast.Call):
                    continue
                name = (
                    c.func.id
                    if isinstance(c.func, ast.Name)
                    else getattr(c.func, "attr", "")
                )
                for k in c.keywords:
                    if k.arg == "gate" and _gate_const(k.value):
                        out.append((t, fn.name, name, _gate_const(k.value)))
                i = GATE_ARG.get(name)
                g = (
                    _gate_const(c.args[i])
                    if i is not None and len(c.args) > i
                    else None
                )
                if g:
                    msg = {"refusal_event": 1, "ops_event": 3}.get(name, 0)
                    out.append(
                        (t, fn.name, _lead(c.args[msg]) if len(c.args) > msg else "", g)
                    )
    return sorted(out)


def _calls(node, names):
    return any(
        isinstance(c, ast.Call)
        and (
            getattr(c.func, "id", None) in names
            or getattr(c.func, "attr", None) in names
        )
        for c in ast.walk(node)
    )


def _gated_event(node):
    """A call that writes a refusal event: refusal_event / gate_event.refused, or ops_event with a gate."""
    for c in ast.walk(node):
        if not isinstance(c, ast.Call):
            continue
        name = getattr(c.func, "id", None) or getattr(c.func, "attr", None)
        if name in EVENT_CALLS or (
            name == "ops_event"
            and (len(c.args) > 6 or any(k.arg == "gate" for k in c.keywords))
        ):
            return True
    return False


def _direct_event(stmt):
    """A statement that itself writes a refusal event: a simple statement with the call, or an `if` with no
    else whose body is simple statements, one of them the call."""
    compound = (
        ast.If,
        ast.For,
        ast.While,
        ast.Try,
        ast.With,
        ast.FunctionDef,
        ast.ClassDef,
    )
    if not isinstance(stmt, compound):
        return _gated_event(stmt)
    if (
        isinstance(stmt, ast.If)
        and not stmt.orelse
        and not any(isinstance(b, compound) for b in stmt.body)
    ):
        return any(_gated_event(b) for b in stmt.body)
    return False


_COMPOUND = (ast.If, ast.For, ast.While, ast.Try, ast.With)


def _forms(node):
    """The refusal statements inside node (except fail(), checked by _unguarded_fail)."""
    for n in ast.walk(node):
        if (
            isinstance(n, ast.Call)
            and getattr(n.func, "attr", None) == "exit"
            and getattr(n.func.value, "id", None) == "sys"
        ):
            if not (
                n.args
                and isinstance(n.args[0], ast.Call)
                and getattr(n.args[0].func, "id", "") == "main"
            ):
                yield "sys.exit"
        elif (
            isinstance(n, ast.Raise)
            and n.exc is not None
            and "SystemExit" in ast.dump(n.exc)
        ):
            yield "raise SystemExit"
        elif (
            isinstance(n, ast.Return)
            and isinstance(n.value, ast.Constant)
            and type(n.value.value) is int
            and n.value.value in (1, 2)
        ):
            yield "return"
        elif isinstance(n, ast.Dict):
            pairs = {
                getattr(k, "value", None): getattr(v, "value", None)
                for k, v in zip(n.keys, n.values)
            }
            if (
                pairs.get("decision") == "block"
                or pairs.get("permissionDecision") == "deny"
            ):
                yield "hook"


def _unguarded_fail(node):
    for n in ast.walk(node):
        if (
            isinstance(n, ast.Call)
            and getattr(n.func, "id", None) == "fail"
            and not (len(n.args) > 1 and _gate_const(n.args[1]))
        ):
            yield "fail without a gate"


def _walk(where, stmts, inherited, found):
    """Add (file, function, form) to found for each refusal in stmts that no event write covers."""
    here = any(_direct_event(x) for x in stmts)
    for i, st in enumerate(stmts):
        if isinstance(st, (ast.FunctionDef, ast.ClassDef)):
            continue
        before = inherited or any(_direct_event(x) for x in stmts[:i])
        parts = [st]
        if isinstance(st, _COMPOUND):
            parts = [
                getattr(st, f)
                for f in ("test", "iter")
                if getattr(st, f, None) is not None
            ]
            parts += [w.context_expr for w in getattr(st, "items", [])]
            nested = [
                getattr(st, f)
                for f in ("body", "orelse", "finalbody")
                if getattr(st, f, None)
            ]
            nested += [h.body for h in getattr(st, "handlers", [])]
            for b in nested:
                _walk(where, b, before, found)
        for part in parts:
            if not (before or here):
                found.extend((*where, f) for f in _forms(part))
            found.extend((*where, f) for f in _unguarded_fail(part))


def refusal_forms(runtime=RUNTIME):
    """(file, function, form) for every refusal statement of five spellings that is NOT next to an event
    write: a call spelt `sys.exit(...)` with anything but main(), a `raise SystemExit`, a `return` of the
    literal 1 or 2, a hook block or deny dict, and every fail() without a gate, wherever it is. "Next to" =
    its own statement list writes a refusal event (before or after it), or an enclosing list writes one,
    as a direct statement, before the statement holding it; that is a rule about position, not about the
    path a run takes. Not seen (docs/PROJECT-TEAM.md, "Gate refusals", Limits): os._exit, a bare exit(),
    an aliased sys, a return placed after a covered branch, and an exit code passed through a variable."""
    found = []
    for t in TOOL_FILES:
        tree = ast.parse((runtime / t).read_text())
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            _walk((t, fn.name), fn.body, False, found)
    return sorted(found)


# Each place a gate id is named: (file, function, the message's first 40 characters or the callee, gate).
# Generated once from the code and checked against the census (result.md 1.2); edit it by hand, on purpose.
SITE_GATES = [
    ("agent-roles.py", "on_pre_tool_use", "", "dispatch:binding-unreadable"),
    ("agent-roles.py", "pre_tool_use", "", "dispatch:record-folder"),
    ("agent-roles.py", "pre_tool_use", "", "dispatch:role-unresolved"),
    ("agent-roles.py", "prediction_stop", "", "stop:prediction"),
    ("agent-roles.py", "prediction_stop", "", "stop:seal"),
    (
        "agent-roles.py",
        "subagent_stop",
        "bakehouse agent-roles:",
        "stop:record-artefacts",
    ),
    (
        "agent-roles.py",
        "subagent_stop",
        "bakehouse agent-roles: role '{}' require",
        "stop:run-record",
    ),
    (
        "decision.py",
        "check_grounding",
        "--fix-round counts from 1",
        "decision:fix-round-number",
    ),
    (
        "decision.py",
        "check_grounding",
        "--fix-round needs --task (the work order",
        "decision:fix-round",
    ),
    (
        "decision.py",
        "check_grounding",
        "--read <file> is required: a source file",
        "decision:read-required",
    ),
    ("decision.py", "check_grounding", "--read {}: no such file", "decision:read-file"),
    (
        "decision.py",
        "check_grounding",
        "--read {}: that is an agent's record, no",
        "decision:read-report",
    ),
    (
        "decision.py",
        "check_grounding",
        "--read-what is required: the rows, cells",
        "decision:read-what",
    ),
    (
        "decision.py",
        "check_grounding",
        "--recheck needs --task and no --fix-roun",
        "decision:recheck",
    ),
    (
        "decision.py",
        "check_grounding",
        "--recheck: {} has no fix round in the le",
        "decision:recheck",
    ),
    (
        "decision.py",
        "check_grounding",
        "fix round {} of {}: give --rechecked, wh",
        "decision:rechecked",
    ),
    (
        "decision.py",
        "check_grounding",
        "the question is about a fix round: give ",
        "decision:fix-round",
    ),
    (
        "decision.py",
        "check_grounding",
        "the question proposes a fix on {}: give ",
        "decision:fix-round",
    ),
    (
        "decision.py",
        "check_grounding",
        "the question proposes a fix: give --upst",
        "decision:upstream",
    ),
    (
        "decision.py",
        "check_grounding",
        "the question uses {}: an inference. Chec",
        "decision:inference",
    ),
    (
        "decision.py",
        "check_grounding",
        "{} already has fix round(s) {}; this one",
        "decision:fix-round-number",
    ),
    (
        "decision.py",
        "check_grounding",
        "{} is at fix round {} (rounds asked so f",
        "decision:stepped-back",
    ),
    ("decision.py", "cmd_answer", "--choice is empty", "decision:choice"),
    ("decision.py", "cmd_answer", "no decision {} in {}", "decision:unknown-id"),
    (
        "decision.py",
        "cmd_answer",
        "the choice does not match the recommenda",
        "decision:agreed",
    ),
    (
        "decision.py",
        "cmd_answer",
        "{} was decided autonomously; record what",
        "decision:autonomous-answer",
    ),
    ("decision.py", "cmd_ask", "--approves must be one of the --option v", "decision:approves"),
    ("decision.py", "cmd_ask", "--category is empty", "decision:category"),
    ("decision.py", "cmd_ask", "--confidence (0-1) is required", "decision:confidence"),
    (
        "decision.py",
        "cmd_ask",
        "--confidence must be between 0 and 1",
        "decision:confidence",
    ),
    (
        "decision.py",
        "cmd_ask",
        "--evidence (a one-line pointer) is requi",
        "decision:evidence",
    ),
    (
        "decision.py",
        "cmd_ask",
        "--evidence-kind is required: {}",
        "decision:evidence-kind",
    ),
    (
        "decision.py",
        "cmd_ask",
        "--evidence-kind must be one of {}",
        "decision:evidence-kind",
    ),
    (
        "decision.py",
        "cmd_ask",
        "--question is empty",
        "decision:question",
    ),
    (
        "decision.py",
        "cmd_ask",
        "--recommendation is required",
        "decision:recommendation",
    ),
    (
        "decision.py",
        "cmd_ask",
        "--recommendation must be one of the --op",
        "decision:recommendation",
    ),
    (
        "decision.py",
        "cmd_ask",
        "a decision is either asked blind or deci",
        "decision:blind-autonomous",
    ),
    (
        "decision.py",
        "cmd_ask",
        "give at least two --option values (yes/n",
        "decision:options",
    ),
    ("decision.py", "cmd_ask", "id {} already exists in {}", "decision:id-exists"),
    ("decision.py", "cmd_outcome", "--by (what showed it) is empty", "decision:by"),
    ("decision.py", "cmd_outcome", "no decision {} in {}", "decision:unknown-id"),
    (
        "predict-gate.py",
        "main",
        "predict-gate {} refused its input",
        "predict-gate:input",
    ),
    ("predict-gate.py", "main", "predicted-moves gate refused", "predict-gate:check"),
    ("predict-gate.py", "record_override", "default", "predict-gate:check"),
    ("predict-gate.py", "run_check", "default", "predict-gate:check"),
    (
        "predict-gate.py",
        "run_close",
        "work order close overridden",
        "predict-gate:close",
    ),
    ("predict-gate.py", "run_close", "work order close refused", "predict-gate:close"),
    (
        "predict-gate.py",
        "run_hook",
        "predicted-moves commit refused",
        "predict-gate:hook",
    ),
    ("predict-gate.py", "run_hook", "run_check", "predict-gate:hook"),
    ("predict-gate.py", "run_seal", "predict-gate {}: line not anchored", "predict-gate:anchor"),
    ("predict-gate.py", "run_seal_code", "predict-gate seal-code: line not anchore", "predict-gate:anchor"),
    ("records-check.py", "__init__", "default", "records-check:input"),
    (
        "records-check.py",
        "merged_jobs",
        "the binding's records field: ",
        "records-check:binding-records",
    ),
    (
        "spend.py",
        "cmd_record",
        "--correct given but agent {} has no row ",
        "spend:correct-no-row",
    ),
    (
        "spend.py",
        "cmd_record",
        "--kind {} names no role, so a new record",
        "spend:kind-not-a-role",
    ),
    (
        "spend.py",
        "cmd_record",
        "a backfilled row (SPEND_TS set) needs --",
        "spend:backfill-note",
    ),
    (
        "spend.py",
        "cmd_record",
        "a first record names the role the agent ",
        "spend:kind-required",
    ),
    (
        "spend.py",
        "cmd_record",
        "a first record states the tool's result,",
        "spend:first-record",
    ),
    (
        "spend.py",
        "cmd_record",
        "a second architect run for {} ({} before",
        "spend:stepped-back",
    ),
    (
        "spend.py",
        "cmd_record",
        "agent {} is already recorded in {}; pass",
        "spend:already-recorded",
    ),
    (
        "spend.py",
        "cmd_record",
        "an architect run names its task: give --",
        "spend:architect-task",
    ),
    (
        "spend.py",
        "cmd_record",
        "no wave {} in {}; open it first: spend.s",
        "spend:wave-unknown",
    ),
    (
        "spend.py",
        "cmd_record",
        "{} waves are open in {} ({}); pass --wav",
        "spend:wave-ambiguous",
    ),
    ("spend.py", "cmd_status", "no wave {} in {}", "spend:wave-unknown"),
    ("spend.py", "cmd_wave", "no wave {} in {}", "spend:wave-unknown"),
    ("spend.py", "cmd_wave", "wave open needs --ceiling <tokens>", "spend:wave-args"),
    ("spend.py", "cmd_wave", "wave open needs a name", "spend:wave-args"),
    (
        "spend.py",
        "cmd_wave",
        "wave {} already exists in {} (opened {})",
        "spend:wave-exists",
    ),
    ("spend.py", "cmd_wave", "wave {} was already closed at {}", "spend:wave-closed"),
    (
        "spend.py",
        "cmd_wave",
        "{} waves are open in {}; name the one to",
        "spend:wave-ambiguous",
    ),
]


# The census's exclusions (result.md 1.1), pinned by count, so a new one has to be added here on purpose.
EXCLUDED = {
    (
        "decision.py",
        "fail",
        "sys.exit",
    ): "fail() itself: it writes the event first when it has a gate",
    (
        "spend.py",
        "fail",
        "sys.exit",
    ): "fail() itself: it writes the event first when it has a gate",
    (
        "decision.py",
        "resolve_project",
        "fail without a gate",
    ): "boundary 2: no team, so no ledger",
    (
        "spend.py",
        "resolve_project",
        "fail without a gate",
    ): "boundary 2: no team, so no ledger",
    (
        "predict-gate.py",
        "run_check",
        "return",
    ): "its callers write the event (main's check, the hook, close)",
    (
        "predict-gate.py",
        "run_close",
        "return",
    ): "boundary 3: the close itself could not be recorded",
    (
        "predict-gate.py",
        "main",
        "return",
    ): "boundary 5: the usage text for an unknown subcommand",
}
EXCLUDED_COUNT = {
    ("decision.py", "fail", "sys.exit"): 1,
    ("spend.py", "fail", "sys.exit"): 1,
    ("decision.py", "resolve_project", "fail without a gate"): 2,
    ("spend.py", "resolve_project", "fail without a gate"): 2,
    ("predict-gate.py", "run_check", "return"): 2,
    ("predict-gate.py", "run_close", "return"): 1,
    ("predict-gate.py", "main", "return"): 1,
}


class TestGateIds(unittest.TestCase):
    TOOLS = (
        "decision.py",
        "spend.py",
        "predict-gate.py",
        "records-check.py",
        "agent-roles.py",
    )
    ID = r"(?:decision|spend|predict-gate|records-check|dispatch|stop):[a-z0-9-]+"

    def test_every_gate_id_in_code_is_in_the_docs_table_and_back(self):
        code = {
            g
            for t in self.TOOLS
            for g in re.findall(rf"[\"']({self.ID})[\"']", (RUNTIME / t).read_text())
        }
        doc = (RUNTIME.parent / "docs" / "PROJECT-TEAM.md").read_text()
        section = doc[
            doc.index("### Gate refusals") : doc.index("## Known limits in this release")
        ]
        table = set(re.findall(rf"^\| `({self.ID})` \|", section, re.MULTILINE))
        self.assertEqual(sorted(code - table), [], "in code, not in the docs table")
        self.assertEqual(sorted(table - code), [], "in the docs table, not in code")
        self.assertEqual(len(code), 53)  # 51, plus V12b's new id, V12c's predict-gate:anchor and stop:seal, less decision:questions-ref (R0)

    def test_no_refusal_in_decision_or_spend_is_without_a_gate(self):
        """Every fail() outside resolve_project passes a gate id, so a refusal added without one fails here."""
        for name, prefix in (("decision.py", "decision:"), ("spend.py", "spend:")):
            tree = ast.parse((RUNTIME / name).read_text())
            found, bare = 0, []
            for fn in (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name != "resolve_project"
            ):
                for call in (c for c in ast.walk(fn) if isinstance(c, ast.Call)):
                    if not (isinstance(call.func, ast.Name) and call.func.id == "fail"):
                        continue
                    found += 1
                    gate = call.args[1] if len(call.args) == 2 else None
                    if not (
                        isinstance(gate, ast.Constant)
                        and str(gate.value).startswith(prefix)
                    ):
                        bare.append(f"{name}:{call.lineno}")
            with self.subTest(file=name):
                self.assertEqual(bare, [])
                self.assertEqual(found, {"decision.py": 34, "spend.py": 17}[name])

    def test_each_refusal_site_carries_its_own_gate_id(self):
        """Each site, found by its function and message, names the gate it is documented with. A site that
        took another site's gate (a copied line keeping the old id) fails here (review F3)."""
        self.assertEqual(gate_sites(), SITE_GATES)

    def test_every_refusal_form_in_the_five_tools_is_gated_or_excluded(self):
        """A refusal written as `sys.exit(...)`, `raise SystemExit`, a literal `return 1`/`return 2`, a hook
        block or deny, or a gate-less fail(), with no event write next to it, is either one of the census's
        pinned exclusions or a failure here (review F4). It checks those spellings and that position only;
        refusal_forms names the five forms it does not see (re-check N1)."""
        counts = {}
        for k in refusal_forms():
            counts[k] = counts.get(k, 0) + 1
        self.assertEqual(
            {k: v for k, v in counts.items() if k not in EXCLUDED},
            {},
            "a refusal with no event",
        )
        self.assertEqual(counts, EXCLUDED_COUNT)
        # bad input is raised anywhere and written once, by the handler in main
        for t, fns in (
            ("predict-gate.py", ("main",)),
            ("records-check.py", ("main", "main_stream")),
        ):
            tree = ast.parse((RUNTIME / t).read_text())
            for fn in (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name in fns
            ):
                handlers = [
                    h
                    for n in ast.walk(fn)
                    if isinstance(n, ast.Try)
                    for h in n.handlers
                    if "BadInput" in ast.dump(h.type)
                ]
                with self.subTest(file=t, function=fn.name):
                    self.assertTrue(handlers)
                    self.assertTrue(
                        all(_calls(h, ("refusal_event",)) for h in handlers)
                    )


if __name__ == "__main__":
    unittest.main()


class TestRefusalEventTime(unittest.TestCase):
    """V12c: an exported OPS_TS or OPS_COMMIT never reaches the refusal event's writer, so it is never back-dated."""

    def test_a_refusal_event_is_never_back_dated(self):
        import sys
        from unittest import mock

        sys.path.insert(0, str(RUNTIME))
        import gate_event

        with tempfile.TemporaryDirectory() as tmp:
            here = Path(tmp)
            seen = here / "env.txt"
            w = here / "ops-event.sh"
            w.write_text(f'#!/bin/bash\necho "ts=${{OPS_TS-unset}} commit=${{OPS_COMMIT-unset}}" > \'{seen}\'\n')
            w.chmod(0o755)
            with mock.patch.dict(os.environ, {"OPS_TS": "2020-01-01T00:00:00Z", "OPS_COMMIT": "abc123"}), \
                    mock.patch.object(gate_event, "HERE", here):
                self.assertIsNone(gate_event.refused("spend:wave-unknown", "p", "n", path=tmp))
            self.assertEqual(seen.read_text(), "ts=unset commit=unset\n")
