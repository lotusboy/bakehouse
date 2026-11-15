"""Role and agent prompts that must agree with the rules beside them. Standard library only.

Review B3: the PM's reply shape asked every update for a decision id and a spend line, against the
one-line run-start notice, the short session-start hand-back, and §8 ("none is required"). Review B4:
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
        self.shape = bullet(self.pm, "One reply shape.")
        self.assertTrue(self.shape)

    def test_b3_the_shape_covers_results_and_questions_and_names_its_two_exceptions(
        self,
    ):
        self.assertIn(
            "Every update that reports a result, and every question to the owner, takes this shape",
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
            "**Question (<decision id>, where the owner keeps a decision ledger).**",
            self.shape,
        )
        self.assertIn(
            "**Spend:** <the open wave: spent of ceiling>, where a spend wave is open.",
            self.shape,
        )
        self.assertIn(
            "Where the owner keeps a decision ledger (§8), log the question before sending it",
            self.shape,
        )
        self.assertIn(
            "Where no spend wave is open, the Spend line is left out.", self.shape
        )
        self.assertIn("none is required", self.pm)


class TestRecordsAndLanes(unittest.TestCase):
    """S16 M6, M7: the PM commits the records worktree; a lane's commands name the variables the tools read."""

    def setUp(self):
        self.pm = (RUNTIME.parent / "skills" / "pm" / "SKILL.md").read_text()
        self.change = (RUNTIME.parent / "commands" / "change.md").read_text()
        self.refactor = (RUNTIME.parent / "commands" / "refactor.md").read_text()

    def test_m6_the_pm_commits_the_records_after_each_close_and_before_the_session_ends(self):
        b = bullet(self.pm, "The records folder is always committed: after each close, and before the session ends.")
        self.assertIn("`git -C <records> add -A -- . ':!ops/events.jsonl' && git -C <records> commit -m "
                      "\"<work order or 'records'>: records\"`", b)
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
            for f in ("ops/events.jsonl", "decisions/ledger.jsonl", "spend/ledger.jsonl"):
                (rec / f).parent.mkdir(exist_ok=True)
                (rec / f).write_text("{}\n")
            subprocess.run(f"git -C {shlex.quote(t)} add{add}", shell=True, check=True, env=env)
            staged = subprocess.run(["git", "-C", t, "diff", "--cached", "--name-only"], capture_output=True,
                                    text=True, check=True, env=env).stdout.split()
        self.assertEqual(staged, ["decisions/ledger.jsonl", "spend/ledger.jsonl"])

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
    def test_b4_the_refactor_reviewer_owes_at_least_5_rows(self):
        text = " ".join(
            (RUNTIME.parent / "agents" / "reviewer.md").read_text().split()
        )
        self.assertIn("Pick at least 5 of the engineer's mutation tests", text)
        self.assertIn(
            "One row per mutation performed, and at least 5 rows (section 3).", text
        )
        self.assertNotIn("when you performed none", text)


if __name__ == "__main__":
    unittest.main()
