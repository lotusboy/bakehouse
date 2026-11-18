"""Item 13b: a standing release check. One made-up work order, W-1, runs from start to finish as `/bakehouse:change`
documents it (commands/change.md steps 5 to 10; docs/PREDICT-GATE.md, "Sealing" and "Closing a work order"), with one
fix round, once in tree mode (a binding that names `reviews`) and once in records mode (no `reviews`, no `runs`).

The steps are the work order's "What to build", 1 to 5. Every assertion message starts with its step, so a failure
names the step. The PM's dispatches and the engineer's stops go through the real role hook (runtime/agent-roles.py)
as the harness sends them: a PreToolUse input for each dispatch, a SubagentStop input for each stop. Every seal,
check and close is the real predict-gate.py. Nothing is faked in the ops ledger.

Test-only, standard library only. Every test builds scratch repositories, a scratch HOME and a scratch machine
folder under one temporary folder; nothing outside it is read or written.
python3 -m unittest runtime/tests/test_release_check.py
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(TESTS))
import test_run_folders as run_folders  # noqa: E402
import test_team_roles as roles_tests  # noqa: E402

REC = run_folders.REC  # 2026-11-10-W-1-step1-review: reviewName with {date} fixed at step 5
BRANCH = "change/W-1-example"
TRAILER = "Co-Authored-By: Example Agent <noreply@example.invalid>"
MARKER = "[bakehouse:prediction-record]"
CODE_ROOTS = {"signal": {"codeRoots": ["src/"]}}
TRACKER = {"tracker": {"file": "T.md", "id": "^W-[0-9]+$"}}

# The first round: the change sets v from 1 to 2; w must not move.
P1 = {"moves": [{"what": "out:v", "from": 1, "to": 2}], "mustNotMove": ["out:w"]}
B1 = {"out:v": 1, "out:w": 5}
M1 = {"out:v": 2, "out:w": 5}
# The fix round, fix1: the review found x missing. Its baseline is measured on the first round's code and also
# reads x, so its bytes are not the first round's measured file (a seal of one never stands in for the other).
P2 = {"moves": [{"what": "out:x", "from": 0, "to": 1}], "mustNotMove": ["out:v", "out:w"]}
B2 = {"out:v": 2, "out:w": 5, "out:x": 0}
M2 = {"out:v": 2, "out:w": 5, "out:x": 1}

WORK_ORDER = """# Tracker

## W-1
id: W-1
need: the example tool sets v to 2.
evidence: src/app.py sets v to 1.
layer: src/
gates: predicted-moves
expected: out:v 1 -> 2
mustNotMove: out:w
decided: owner, 2026-11-10
serves: the example output, 1 of 1 in-scope items.
"""


class ChangeJob(run_folders.Project):
    """The job's steps, in the documented order. A subclass gives the binding, the record folder and how a record
    file is committed (change.md, "Record commits")."""

    folder = None  # the record folder, a full path
    record_root = None  # the checkout that holds it
    record_rel = None  # its path in that checkout

    def setUp(self):
        super().setUp()
        # the plugin's engineer, as the shipped table names it (bakehouse:engineer); installed by the role tests'
        # own helper, which reads self.home
        self.home, self.machine = self.t / "home", self.t / "machine"
        (self.home / ".claude" / "agents").mkdir(parents=True)
        roles_tests.Fixture.install(self, "engineer", own=False)
        self.transcripts = self.t / "transcripts"
        self.transcripts.mkdir()
        self.code_commit("W-1: work order", {"T.md": WORK_ORDER})  # the item on the default branch
        self.stops = 0

    # ---------- helpers ----------

    def rec(self, name=""):
        return self.folder / name

    def dated(self):
        w = self.when()
        return dict(self.env, GIT_AUTHOR_DATE=w, GIT_COMMITTER_DATE=w)

    def code_commit(self, msg, files):
        """A commit in the code tree, by path, with the trailer (change.md, "Commit by path")."""
        self.write(self.repo, files)
        self.git(self.repo, "add", "--", *files)
        self.git(self.repo, "commit", "-q", "-m", msg, "-m", TRAILER, "--", *files, env=self.dated())

    def item(self, line):
        """A line written back into the tracker item on the job's branch."""
        text = (self.repo / "T.md").read_text() + line + "\n"
        self.code_commit(f"W-1: {line.split(':')[0]}", {"T.md": text})

    def seal_code(self, msg, files, rnd="first"):
        """The round's first code, staged by path and committed through seal-code (change.md step 6.1)."""
        self.write(self.repo, files)
        self.git(self.repo, "add", "--", *files)
        return self.gate("seal-code", "W-1", rnd, "--", "-q", "-m", msg, "-m", TRAILER, env=self.dated())

    def head(self):
        return self.git(self.repo, "rev-parse", "HEAD")

    def hook(self, d):
        p = subprocess.run(["python3", str(roles_tests.HOOK)], input=json.dumps(d), capture_output=True, text=True,
                           env=self.env, cwd=self.repo)
        return p.stdout

    def log(self):
        f = self.machine / "logs" / "agent-roles.log"
        return f.read_text() if f.exists() else ""

    def brief(self, extra=""):
        """The engineer brief (change.md, "Engineer brief"), filled for W-1. The record folder line holds the path
        only."""
        return (f"Repo: the working directory's repository; every path below is relative to its root.\n"
                f"Branch: {BRANCH} from BASE {self.base}. Work on it. Do not push or open a pull request.\n"
                f"Work order W-1: the example tool sets v to 2.\n"
                f"Decided: owner, 2026-11-10.\n"
                f"Evidence: src/app.py sets v to 1. Reproduce it on BASE first.\n"
                f"Owning layer: src/. Fix it there; a fix in another layer is an andon.\n"
                f"Expected moves: out:v 1 -> 2.\n"
                f"Must not move: out:w.\n"
                f"Record folder: {self.folder}/\n"
                f"Record commits: {self.record_commits}\n"
                f"Gate (predicted moves): predicted-moves. Write moves.prediction.json before your first edit.\n"
                f"Commits: one job each; end each with this trailer: {TRAILER}\n"
                f"{extra}"
                f"Report: result line, moves against the prediction, gates, commits. Budget: 10k.")

    # ---------- step 1: the dispatch ----------

    def dispatch(self, prompt, step):
        """The PM's dispatch of role:change:engineer through the role hook. Returns the rewritten tool input."""
        out = self.hook({"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(self.repo),
                         "tool_input": {"subagent_type": "role:change:engineer", "description": "engineer: W-1",
                                        "prompt": prompt}})
        self.assertTrue(out.strip(), f"{step}: the role hook answered nothing")
        hso = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "allow", f"{step}: the dispatch was refused: {hso}")
        ti = hso["updatedInput"]
        self.assertEqual(ti["subagent_type"], "bakehouse:engineer", f"{step}: role:change:engineer resolves")
        self.assertIn(f"Before you stop, your record folder {self.folder}/ must hold the prediction as "
                      "moves.prediction.json", ti["prompt"], f"{step}: the brief gets its record-folder line")
        self.assertEqual(ti["prompt"].splitlines()[-1], MARKER, f"{step}: the marker is the prompt's final line")
        lines = (self.machine / "logs" / "prediction-dispatch.jsonl").read_text().splitlines()
        logged = json.loads(lines[-1])
        self.assertTrue(logged["change"], f"{step}: the dispatch is logged as a change job")
        self.assertEqual((logged["record"]["kind"], logged["record"]["root"], logged["record"]["folder"]),
                         ("repo", str(self.record_root), self.record_rel),
                         f"{step}: the record folder is decided at dispatch")
        return ti

    # ---------- the engineer's stop ----------

    def stop(self, ti, step):
        """The engineer's SubagentStop, its transcript's first line the rewritten prompt. It must pass: no block,
        no message, and the hook asks for no seal."""
        self.stops += 1
        t = self.transcripts / f"agent-{self.stops}.jsonl"
        t.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": ti["prompt"]}}) + "\n")
        before = len(self.log())
        out = self.hook({"hook_event_name": "SubagentStop", "agent_type": ti["subagent_type"],
                         "agent_transcript_path": str(t), "cwd": str(self.repo)})
        log = self.log()[before:]
        self.assertEqual(out, "", f"{step}: the engineer's stop is held: {out}")
        self.assertIn("PREDICTION ok for bakehouse:engineer", log, f"{step}: the stop check ran: {log}")
        for no in ("SEAL missing", "cannot be sealed", "PREDICTION missing"):
            self.assertNotIn(no, log, f"{step}: the stop asks for a seal or a prediction: {log}")

    # ---------- steps 2 and 3: one round ----------

    def seal_ok(self, step, rnd, name, *extra):
        rc, out = self.seal(rnd, name, *extra)
        self.assertEqual(rc, 0, f"{step}: predict-gate.sh seal W-1 {rnd} {name}: {out}")

    def round(self, step, ti, rnd, pred, base_m, code, measured):
        """One round, as change.md step 6 (and step 8 for a fix round) orders it."""
        sfx = "" if rnd == "first" else f".{rnd}"
        pname, bname, mname = (f"moves.{k}{sfx}.json" for k in ("prediction", "baseline", "measured"))
        report = "measure-report.json" if rnd == "first" else f"measure-{rnd}-report.json"
        # 6.1: the prediction and its baseline (measured on the round's base), committed by path before the first
        # code edit, each sealed at that commit
        self.write(self.rec(), {pname: pred, bname: base_m})
        self.record_commit(f"W-1: {rnd} prediction and baseline", [pname, bname])
        self.seal_ok(step, rnd, pname)
        self.seal_ok(step, rnd, bname)
        self.stop(ti, f"{step} (the stop after the prediction)")
        # 6.2: the owner's go on the prediction, one line in the item
        self.item(f"prediction {pname}: approved by the owner, 2026-11-10")
        # 6.3: the round's first code through seal-code; measured, its file sealed with its report as it is
        # written (the baseline is sealed already); the gate run; the record committed
        base = self.head()
        rc, out = self.seal_code(f"W-1: {rnd} change", code, rnd)
        self.assertEqual(rc, 0, f"{step}: predict-gate.sh seal-code W-1 {rnd}: {out}")
        self.write(self.rec(), {mname: measured, report: {"base": {"sha": base}, "head": {"sha": self.head()}}})
        self.seal_ok(step, rnd, mname, "--report", str(self.rec(report)))
        rc, out = self.gate("check", "--measured", str(self.rec(mname)))
        self.assertEqual(rc, 0, f"{step}: predict-gate.sh check --measured {mname}: {out}")
        self.write(self.rec(), {"result.md": f"W-1 {rnd}: 1 move predicted, 1 moved, as predicted.\n"})
        self.record_commit(f"W-1: {rnd} measurement", [mname, report, "result.md"])
        self.item(f"result {rnd}: predicted-moves pass; commits {self.head()[:8]}")
        self.stop(ti, f"{step} (the stop after the measurement)")
        kinds = {e["fileKind"] for e in self.events() if e["kind"] == "sealed" and e.get("round") == rnd}
        self.assertEqual(kinds, {"prediction", "baseline", "code", "measured"},
                         f"{step}: round {rnd} has its four seals")

    # ---------- the whole job ----------

    def run_job(self):
        # change.md step 5 (PM): the branch from BASE, and the job lines in the item
        self.base = self.head()
        self.git(self.repo, "checkout", "-q", "-b", BRANCH)
        self.item(f"branch: {BRANCH}")
        self.item(f"BASE: {self.base}; record folder: {self.record_rel}")

        # step 1: the engineer, dispatched through the role hook
        ti = self.dispatch(self.brief(), "step 1 (dispatch)")

        # step 2: the first round
        self.round("step 2 (first round)", ti, "first", P1, B1, {"src/app.py": "v = 2\n"}, M1)

        # change.md step 7: the reviewer writes its record; the PM commits it in step 8
        self.write(self.rec(), {"findings.md": "F1 (P1): x is missing from src/app.py.\n"})

        # step 3: the fix round, fix1, by a fresh engineer given the findings and the same brief (change.md step 8)
        ti = self.dispatch(self.brief("Findings: F1 (P1): x is missing from src/app.py. Round: fix1.\n"),
                           "step 3 (fix-round dispatch)")
        self.round("step 3 (fix round fix1)", ti, "fix1", P2, B2, {"src/app.py": "v = 2\nx = 1\n"}, M2)
        self.write(self.rec(), {"disposition.md": "F1: fixed in round fix1.\n"})
        self.record_commit("W-1: review record and disposition", ["findings.md", "disposition.md"])

        # step 4: close on the job's branch (change.md step 9), then close --merged after the merge (step 10)
        rc, out = self.gate("close", "W-1")
        self.assertEqual(rc, 0, f"step 4 (close): {out}")
        self.assertIn("Result: CLOSED", out, "step 4 (close)")
        self.assertIn("sealed: judged by", out, "step 4 (close)")
        self.assertNotIn("OVERRIDDEN", out, "step 4 (close): no override")
        self.assertNotIn("Refused:", out, "step 4 (close)")
        closed = [e for e in self.events() if e["kind"] == "work-order-closed"]
        self.assertEqual(len(closed), 1, "step 4 (close): one work-order-closed line")
        self.assertNotIn("OVERRIDDEN", closed[0]["note"], "step 4 (close): no override")
        for n in ("moves.prediction.json", "moves.measured.json", "moves.prediction.fix1.json",
                  "moves.measured.fix1.json"):
            self.assertIn(f"{self.record_rel}/{n} sha256:", closed[0]["note"], f"step 4 (close): {n} is hashed")
        refused = [e for e in self.events() if e["kind"] == "gate-refused"]
        self.assertEqual(refused, [], "step 4: no gate refused anything on the documented path")

        self.git(self.repo, "checkout", "-q", "main")
        self.git(self.repo, "merge", "-q", "--no-ff", "-m", "Merge the W-1 pull request", BRANCH, env=self.dated())
        rc, out = self.gate("close", "W-1", "--merged")
        self.assertEqual(rc, 0, f"step 4 (close --merged): {out}")
        self.assertIn("Result: CLOSED", out, "step 4 (close --merged)")
        notes = [e for e in self.events() if e["kind"] == "note" and "--merged" in (e.get("purpose") or "")]
        self.assertEqual(len(notes), 1, "step 4 (close --merged): one note line, not a second close")
        self.assertEqual(len([e for e in self.events() if e["kind"] == "work-order-closed"]), 1,
                         "step 4 (close --merged): no second work-order-closed line")
        # change.md step 10: the ops ledger is committed by its path
        self.git(self.records, "add", "--", "ops/events.jsonl")
        self.git(self.records, "commit", "-q", "-m", "W-1: ops ledger", "-m", TRAILER, "--", "ops/events.jsonl")
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), "", "step 4: the code tree is clean")

    def tree_names(self, where, ref="HEAD"):
        return [n for n in self.git(where, "ls-tree", "-r", "--name-only", ref).split("\n") if n]

    def record_files(self):
        return sorted(f"{self.record_rel}/{p.name}" for p in self.folder.iterdir())


class TreeMode(ChangeJob):
    """A binding that names `reviews` and no `runs`: the extractor's mode. Record files are committed with git, by
    path, in the code tree."""

    binding = {"owned": True, "reviews": "axis/runs/", "reviewName": "{date}-{id}-step{n}-review",
               **TRACKER, **CODE_ROOTS}
    record_commits = "commit record files with git, by path"

    def setUp(self):
        super().setUp()
        self.folder = self.repo / "axis" / "runs" / REC
        self.record_root, self.record_rel = self.repo, f"axis/runs/{REC}"

    def record_commit(self, msg, names):
        self.code_commit(msg, {f"{self.record_rel}/{n}": (self.rec(n)).read_text() for n in names})

    def test_a_work_order_with_a_fix_round_runs_from_dispatch_to_the_merged_close(self):
        self.run_job()
        # step 5 (tree mode's twin): every record is in the code tree on the default branch, none on the records
        # branch
        on_main = [n for n in self.tree_names(self.repo, "main") if n.startswith(self.record_rel + "/")]
        self.assertEqual(sorted(on_main), self.record_files(), "step 5: every record is in the code tree on main")
        self.assertFalse([n for n in self.tree_names(self.records) if n.startswith("runs/")],
                         "step 5: no record on the records branch")


class RecordsMode(ChangeJob):
    """A binding with no `reviews` and no `runs`: run folders under runs/ on the records branch, each record file
    committed with `bakehouse record commit`."""

    binding = {"owned": True, "reviewName": "{date}-{id}-step{n}-review", **TRACKER, **CODE_ROOTS}
    record_commits = ("commit record files with `bakehouse record commit <the record folder> -m <message>`, "
                      "a trailer in its own -m")

    def setUp(self):
        super().setUp()
        self.folder = self.records / "runs" / REC
        self.record_root, self.record_rel = self.records, f"runs/{REC}"

    def record_commit(self, msg, names):
        rc, out = self.bakehouse("record", "commit", str(self.folder), "-m", msg, "-m", TRAILER)
        self.assertEqual(rc, 0, f"bakehouse record commit ({msg}): {out}")

    def test_a_work_order_with_a_fix_round_runs_from_dispatch_to_the_merged_close(self):
        self.run_job()
        # step 5: every record is on the records branch, and none in the code tree
        on_records = [n for n in self.tree_names(self.records) if n.startswith(self.record_rel + "/")]
        self.assertEqual(sorted(on_records), self.record_files(), "step 5: every record is on the records branch")
        for ref in (BRANCH, "main"):
            names = self.tree_names(self.repo, ref)
            self.assertFalse([n for n in names if n.startswith(("runs/", "axis/")) or Path(n).name.startswith(
                ("moves.", "measure")) or Path(n).name in ("result.md", "findings.md", "disposition.md")],
                f"step 5: no record in the code tree on {ref}: {names}")
        self.assertEqual(self.git(self.records, "status", "--porcelain", "--", "runs/"), "",
                         "step 5: nothing in runs/ is left uncommitted")


if __name__ == "__main__":
    unittest.main()
