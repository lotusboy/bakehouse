"""Role and agent prompts that must agree with the rules beside them. Standard library only.

Review B3: the PM's reply shape asked every update for a decision id and a spend line, against the
one-line run-start notice, the short session-start hand-back, and §8 ("none is required"). Since R6 the
shape is a three-point core; the exceptions and the ledger-only decision id still hold. Review B4:
the refactor reviewer was told both "at least 5" mutations and "`[]` when you performed none"."""

import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent


def bullet(text, head):
    """The list item that starts with **head**, up to the next top-level item or heading."""
    m = re.search(
        r"^- \*\*" + re.escape(head) + r"\*\*.*?(?=^- \*\*|^#)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    return " ".join(m.group(0).split()) if m else ""


class TestPmReplyShape(unittest.TestCase):
    def setUp(self):
        self.pm = (RUNTIME.parent / "skills" / "pm" / "SKILL.md").read_text()
        self.shape = bullet(self.pm, "A short core in every update.")
        self.assertTrue(self.shape)

    def test_b3_the_shape_covers_results_and_questions_and_names_its_two_exceptions(
        self,
    ):
        self.assertIn(
            "Every update that reports a result, and every question to the owner, carries three things",
            self.shape,
        )
        self.assertIn(
            'the one-line notice when a run starts (§4, "Say when a run starts")',
            self.shape,
        )
        self.assertIn(
            "the session-start hand-back (§3, step 2)", self.shape
        )
        self.assertIn("**Say when a run starts.**", self.pm)
        self.assertNotIn("Every update and every question", self.pm)

    def test_b3_the_decision_id_and_spend_lines_apply_only_where_their_tool_is_used(
        self,
    ):
        self.assertIn(
            "Where the owner keeps a decision ledger (§8), log the question before sending it",
            self.shape,
        )
        self.assertNotIn("**Spend:**", self.pm)
        self.assertIn("none is required", self.pm)


class TestRecordsAndLanes(unittest.TestCase):
    """S16 M6, M7: the PM commits the records worktree; a lane's commands name the variables the tools read."""

    def setUp(self):
        self.pm = (RUNTIME.parent / "skills" / "pm" / "SKILL.md").read_text()
        self.change = (RUNTIME.parent / "commands" / "change.md").read_text()
        self.refactor = (RUNTIME.parent / "commands" / "refactor.md").read_text()

    def test_m6_the_pm_commits_the_records_after_each_close_and_before_the_session_ends(self):
        b = bullet(self.pm, "The records folder is always committed: after each close, and before the session ends.")
        self.assertIn("`git -C <records> add -A -- . ':!ops/events.jsonl' ':!ops/events' && { git -C <records> diff "
                      "--cached --quiet || git -C <records> commit -m \"<work order or 'records'>: records\"; }`", b)
        self.assertEqual(self.pm.count("git -C <records> add -A"), 1)

    def test_p2_2_the_records_commit_stages_everything_but_the_ops_ledger(self):
        # S16 fix2 P2-2: the seal anchors commit the ops ledger themselves, by path; the PM's add leaves it alone
        b = bullet(self.pm, "The records folder is always committed: after each close, and before the session ends.")
        add = b.split("`git -C <records> add", 1)[1].split(" && ", 1)[0]
        with tempfile.TemporaryDirectory() as t:
            rec = Path(t)
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": t, "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_CONFIG_GLOBAL": str(rec / ".gitconfig")}
            subprocess.run(["git", "init", "-q", str(rec)], check=True, env=env)
            for f in ("ops/events.jsonl", "ops/events/alpha.jsonl", "decisions/ledger.jsonl", "spend/ledger.jsonl",
                      "spend/ledger/alpha.jsonl"):
                (rec / f).parent.mkdir(parents=True, exist_ok=True)
                (rec / f).write_text("{}\n")
            subprocess.run(f"git -C {shlex.quote(t)} add{add}", shell=True, check=True, env=env)
            staged = subprocess.run(["git", "-C", t, "diff", "--cached", "--name-only"], capture_output=True,
                                    text=True, check=True, env=env).stdout.split()
        self.assertEqual(staged, ["decisions/ledger.jsonl", "spend/ledger.jsonl", "spend/ledger/alpha.jsonl"])

    def test_m7_the_lane_variables_are_named_where_lanes_are_set(self):
        for text in (self.pm, self.change, self.refactor):
            self.assertIn("BAKEHOUSE_LANE=", text)
            self.assertIn("BAKEHOUSE_WO=", text)
            self.assertIn("The older lane variable names are not read", " ".join(text.split()))


class TestSkillReferences(unittest.TestCase):
    """S16 M11: a skill names the PM role by its skill name, which resolves from any project; docs/TOOLS.md lists
    the pm skill and session-start.py."""

    def test_the_method_skills_name_the_pm_skill_by_name(self):
        for name in ("dev-team", "step-back", "live-prism"):
            with self.subTest(skill=name):
                text = (RUNTIME.parent / "skills" / name / "SKILL.md").read_text()
                self.assertNotIn("skills/pm/SKILL.md", text)
        self.assertIn("`bakehouse:pm`", (RUNTIME.parent / "skills" / "dev-team" / "SKILL.md").read_text())

    def test_tools_lists_the_pm_skill_and_session_start(self):
        tools = (RUNTIME.parent / "docs" / "TOOLS.md").read_text()
        self.assertIn("\n| `pm` |", tools)
        self.assertIn("\n| `session-start.py` |", tools)


class TestReviewerPrompt(unittest.TestCase):
    def test_b4_the_reviewer_owes_at_least_5_rows(self):
        text = " ".join(
            (RUNTIME.parent / "agents" / "reviewer.md").read_text().split()
        )
        self.assertIn("Pick at least 5 of the engineer's mutation tests", text)
        self.assertIn(
            "One row per mutation performed, and at least 5 rows (section 3).", text
        )
        self.assertNotIn("when you performed none", text)


class TestEngineerSeals(unittest.TestCase):
    """0.17.5: an engineer committed its prediction and baseline and handed back without sealing either; the
    seal rule was only in change.md step 6.1, which the PM reads. The engineer's own brief and prompt say it."""

    def test_the_engineer_brief_template_names_the_seal_commands(self):
        change = (RUNTIME.parent / "commands" / "change.md").read_text()
        brief = " ".join(change[change.index("### Engineer brief"):change.index("### Reviewer brief")].split())
        self.assertIn("predict-gate.sh seal {id} <round> <file>", brief)
        self.assertIn("predict-gate.sh seal-code {id} <round> -- <git commit args>", brief)
        self.assertIn("predict-gate.sh seal {id} <round> <measured file> --report <measure report>", brief)
        # fix1 F1: step 6.1 orders no prediction seal against a baseline seal; each is sealed at its own commit
        self.assertNotIn("baseline, then the prediction", brief)
        self.assertIn("Seal the prediction at the commit that adds it, and the baseline at the commit that adds it",
                      brief)
        # fix1 F5, F2, F3
        self.assertIn("after its baseline is sealed", brief)
        self.assertIn("With the binding's `mainFollows` set, the first round's baseline seals only when the main "
                      "clone's `<branch>` is merged into this branch; if the seal refuses for that reason, stop and "
                      "report it to the PM.", brief)
        self.assertIn("In a clone, run `bakehouse setup --pointer` first, as this brief's clone line says; without "
                      "it, give each seal `--repo <the main clone>`.", brief)
        # 0.17.5 fix2 (review F7): the brief's clone line tells the engineer to run setup, so nothing may forbid it
        self.assertNotIn("never run `bakehouse setup`", " ".join(brief.split()))
        self.assertIn("If it still refuses with `--repo`, stop and report it to the PM.", brief)
        self.assertIn("Never set `OPS_TS` or `OPS_COMMIT` for a seal.", brief)

    def test_the_engineer_prompt_states_the_seal_rule(self):
        text = " ".join((RUNTIME.parent / "agents" / "engineer.md").read_text().split())
        self.assertIn("In a change job you must seal what you commit", text)
        for word in ("`predict-gate.sh seal`", "`--report <measure report>`", "`predict-gate.sh seal-code`"):
            self.assertIn(word, text)
        # fix1 F1, F2
        self.assertNotIn("the baseline and then the prediction", text)
        self.assertIn("the prediction at the commit that adds it and the baseline at the commit that adds it", text)
        self.assertIn("the baseline before its measured file", text)
        self.assertIn("With the binding's `mainFollows` set, the first round's baseline seals only when the main "
                      "clone's `<branch>` is merged into this branch; if the seal refuses for that reason, stop and "
                      "report it to the PM.", text)


class TestFixRoundChain(unittest.TestCase):
    """0.17.6: a fix round whose prediction names no `"baseline"` takes the measure report's `base` as its code
    base, so close read the earlier rounds' code as its own. Step 8 and the engineer brief template name the chain
    (docs/PREDICT-GATE.md, "A chained round")."""

    CHAIN = ("a fix round's prediction names the round before's measured file as its `\"baseline\"` "
             "(`\"baseline\": \"moves.measured.json\"` in fix1, `\"baseline\": \"moves.measured.fix1.json\"` in "
             "fix2), and that measured file is sealed before this round's prediction is sealed")

    def test_step_8_and_the_engineer_brief_name_the_chain(self):
        change = (RUNTIME.parent / "commands" / "change.md").read_text()
        step8 = " ".join(change[change.index("### 8. Fix round"):change.index("### 9.")].split())
        brief = " ".join(change[change.index("### Engineer brief"):change.index("### Reviewer brief")].split())
        for name, text in (("step 8", step8), ("engineer brief", brief)):
            with self.subTest(name):
                self.assertIn(self.CHAIN, text.replace("A fix round's", "a fix round's"))
                # a fix round with its own baseline file keeps that option (PREDICT-GATE.md, "Rounds")
                self.assertIn("its own `moves.baseline<R>.json`", text)


if __name__ == "__main__":
    unittest.main()
