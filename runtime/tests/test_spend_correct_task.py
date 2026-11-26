"""0.17.1: a spend.sh --correct row with no --task takes the task of the row it corrects, as it takes its kind.
Standard library only: python3 -m unittest test_spend_correct_task (from runtime/tests)"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_gate_events import Scratch  # noqa: E402

RESULT = ["--why", "a build agent", "--predicted", "the gate passes", "--predicted-tokens", "10000",
          "--result", "the gate passed", "--outcome", "as-predicted"]


class CorrectKeepsTask(Scratch):
    def setUp(self):
        super().setUp()
        self.spent = self.records / "spend" / "ledger.jsonl"

    def rec(self, agent, tokens, *extra):
        first = [] if "--correct" in extra else RESULT + ["--kind", "engineer"]
        p = subprocess.run([str(self.rt / "spend.sh"), "record", "--agent", agent, "--label", f"job {agent}",
                            "--tokens", str(tokens), *first, *extra], cwd=self.work, capture_output=True,
                           text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads(self.spent.read_text().splitlines()[-1])

    def test_a_correction_with_no_task_takes_the_task_of_the_row_it_corrects(self):
        self.rec("e1", 100000, "--task", "A9")
        row = self.rec("e1", 160000, "--correct")
        self.assertEqual((row["task"], row["kind"], row["corrects"]), ("A9", "engineer", True))

    def test_a_chain_of_corrections_keeps_the_task(self):
        self.rec("e1", 100000, "--task", "A9")
        self.rec("e1", 160000, "--correct")
        self.assertEqual(self.rec("e1", 200000, "--correct")["task"], "A9")

    def test_a_task_given_on_the_correction_is_kept(self):
        self.rec("e1", 100000, "--task", "A9")
        self.assertEqual(self.rec("e1", 160000, "--correct", "--task", "A10")["task"], "A10")

    def test_a_correction_of_a_row_with_no_task_has_none(self):
        self.rec("e1", 100000)
        self.assertIsNone(self.rec("e1", 160000, "--correct")["task"])

    def test_the_corrected_row_is_the_agents_own(self):
        self.rec("e1", 100000, "--task", "A9")
        self.rec("e2", 100000, "--task", "A10")
        self.assertEqual(self.rec("e1", 160000, "--correct")["task"], "A9")


if __name__ == "__main__":
    unittest.main()
