"""Regression: the decision tool's read commands give the same bytes as the last release did.

fixtures/regression/decisions.jsonl is a made-up decision ledger of 266 rows: every event kind (ask,
answer, assumed answer, outcome, unattended start and stop), eight categories (one the project adds, one
outside its list), blind, autonomous, backfilled and unanswered asks, fix rounds, re-answers, and one
category that has earned autonomy. fixtures/regression/decision-config.json is its project's config.

The test puts both in a scratch project with a records worktree, runs each command in COMMANDS through
runtime/decision.sh at a fixed time, and compares the exit code, stdout and stderr with
fixtures/regression/expected/<name>.txt, which the last release wrote. render's DECISIONS.md is compared
too. The read commands must leave the ledger as it was.

When a change is meant to move an output, write the expected files again and commit them with the change,
so the diff shows each output that moved:
    BAKEHOUSE_REGRESSION_WRITE=1 python3 -m unittest runtime/tests/test_regression_decisions.py
python3 -m unittest runtime/tests/test_regression_decisions.py
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "regression"
EXPECTED = FIXTURE / "expected"
TOOL = ROOT / "runtime" / "decision.sh"
NOW = "2026-03-02T12:00:00+00:00"
CATEGORIES = ("correction", "engine-design", "placement", "spend", "process", "scope", "release", "naming",
              "unlisted")
COMMANDS = ([["score"], ["score", "--json"], ["score", "--since", "2026-02-01"],
             ["score", "--json", "--since", "2026-02-15"], ["review"], ["review", "--md"],
             ["review", "--since", "2026-02-20"], ["review", "--md", "--since", "2026-02-20"]]
            + [["score", "--category", c] for c in CATEGORIES]
            + [["should-ask", c, "--seed", s] for c in CATEGORIES for s in ("1", "2")]
            + [["render"]])


def name(cmd):
    return re.sub(r"[^a-z0-9]+", "-", " ".join(cmd).lower()).strip("-")


class DecisionReplay(unittest.TestCase):
    def test_each_read_command_gives_the_last_releases_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp).resolve()  # macOS's temp folder is behind a symlink; the tools print the real path
            work, rec = t / "fixture-app", t / "fixture-app.bakehouse-records"
            (t / "home").mkdir()
            (t / "gitconfig").write_text("[user]\n\tname = Fixture\n\temail = fixture@example.invalid\n")
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("BAKEHOUSE_", "DECISION_", "CLAUDE_", "GIT_", "SPEND_", "OPS_",
                                        "PREDICT_GATE"))}
            env.update(HOME=str(t / "home"), GIT_CONFIG_GLOBAL=str(t / "gitconfig"), GIT_CONFIG_NOSYSTEM="1",
                       BAKEHOUSE_MACHINE_DIR=str(t / "machine"), DECISION_NOW=NOW)

            def git(*args):
                subprocess.run(["git", "-C", str(work), *args], check=True, capture_output=True, env=env)

            work.mkdir()
            git("init", "-q", "-b", "main")
            (work / ".team").mkdir()
            (work / ".team" / "binding.json").write_text(json.dumps({"owned": True}) + "\n")
            shutil.copy2(FIXTURE / "decision-config.json", work / ".team" / "decision-config.json")
            git("add", "-A")
            git("commit", "-q", "-m", "fixture")
            git("worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(rec))
            git("config", "bakehouse.records", str(rec))
            (rec / "decisions").mkdir()
            ledger = rec / "decisions" / "ledger.jsonl"
            shutil.copy2(FIXTURE / "decisions.jsonl", ledger)
            before = ledger.read_bytes()

            got = {}
            for cmd in COMMANDS:
                p = subprocess.run([str(TOOL), *cmd, "--repo", str(work)], cwd=work, env=env,
                                   capture_output=True, text=True, timeout=120)
                got[name(cmd)] = (f"$ decision.sh {' '.join(cmd)}\nexit {p.returncode}\n--- stdout\n{p.stdout}"
                                  f"--- stderr\n{p.stderr}").replace(str(t), "<TMP>")
            md = rec / "DECISIONS.md"
            got["decisions-md"] = md.read_text().replace(str(t), "<TMP>") if md.exists() else "(no DECISIONS.md)\n"
            self.assertEqual(ledger.read_bytes(), before, "a read command changed the ledger")

        if os.environ.get("BAKEHOUSE_REGRESSION_WRITE") == "1":
            shutil.rmtree(EXPECTED, ignore_errors=True)
            EXPECTED.mkdir(parents=True)
            for k, v in got.items():
                (EXPECTED / f"{k}.txt").write_text(v)
        self.assertEqual(sorted(p.stem for p in EXPECTED.glob("*.txt")), sorted(got),
                         "the expected files and the commands differ; write the expected files again")
        for k, v in got.items():
            with self.subTest(output=k):
                self.assertEqual(v, (EXPECTED / f"{k}.txt").read_text())


if __name__ == "__main__":
    unittest.main()
