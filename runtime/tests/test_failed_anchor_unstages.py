"""Tests for 0.12.1: a failed anchor commit unstages its file (runtime/ops_ledger.py `anchor`). A refused anchor
leaves the line on disk, unstaged and uncommitted, so no other commit (the PM's records commit takes everything
staged) carries it without its trailer, and close still reports the seal as not in a commit. Standard library
only; the scratch project is test_writer_files' (test_sealed_close's, with a scratch HOME).
python3 -m unittest runtime/tests/test_failed_anchor_unstages.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import locator  # noqa: E402
import test_sealed_close as tsc  # noqa: E402 - modules, so their tests are not collected here again
import test_writer_files as twf  # noqa: E402

WHY = "1 sealed event(s) of W-1 are not in a commit of the ledger"


class FailedAnchor(twf.Base):
    def failing_signer(self, on=True):
        if on:
            self.rgit("config", "commit.gpgsign", "true")
            self.rgit("config", "gpg.program", "false")  # a signer that fails: the anchor's commit fails
        else:
            self.rgit("config", "--unset", "commit.gpgsign")

    def staged(self):
        return self.rgit("diff", "--cached", "--name-only").split()

    def notes(self):
        return " ".join(tsc.pg.sealed_state(locator.ledger_files(self.records, "ops_ledger"), "W-1")[2])

    def refused_seal_then_pm_commit(self, ops_file):
        self.commit("W-1: prediction", {self.r("moves.prediction.json"): tsc.GOOD_P})
        before = self.rgit("rev-parse", "HEAD")
        self.failing_signer()
        p = self.gate("seal", "W-1", "first", str(self.repo / self.r("moves.prediction.json")))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("ANCHOR REFUSED", p.stderr)
        self.assertEqual(self.rgit("rev-parse", "HEAD"), before)
        self.assertIn('"sealed"', (self.records / ops_file).read_text())  # the line stays on disk
        self.assertNotIn(ops_file, self.staged())
        self.failing_signer(False)
        p = self.tool("spend.sh", "wave", "open", "w1", "--ceiling", "10", "--repo", self.repo)  # another record
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(WHY, self.notes())
        twf.pm_commit(self, self.records)
        self.assertNotIn(ops_file, self.rgit("log", "--format=", "--name-only").split())
        self.assertIn(ops_file, self.rgit("status", "--porcelain", "--", ops_file))  # still uncommitted
        self.assertIn(WHY, self.notes())

    def test_a_refused_seal_in_format_2_leaves_the_writer_file_out_of_the_pms_commit(self):
        self.migrate("--writer", "alpha")
        self.refused_seal_then_pm_commit("ops/events/alpha.jsonl")

    def test_a_refused_seal_in_format_1_leaves_the_legacy_file_out_of_the_pms_commit(self):
        self.refused_seal_then_pm_commit("ops/events.jsonl")

    def test_a_close_whose_own_line_fails_to_commit_leaves_its_file_unstaged(self):
        self.migrate("--writer", "alpha")
        self.flow()
        before = self.rgit("rev-parse", "HEAD")
        self.failing_signer()
        rc, out = self.close("sealed: judged by", "but not in a commit")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.rgit("rev-parse", "HEAD"), before)
        self.assertIn('"work-order-closed"', (self.records / "ops/events/alpha.jsonl").read_text())
        self.assertNotIn("ops/events/alpha.jsonl", self.staged())

    def test_only_the_anchors_own_path_is_unstaged(self):
        self.commit("W-1: prediction", {self.r("moves.prediction.json"): tsc.GOOD_P})
        (self.records / "other.md").write_text("staged before the seal\n")
        self.rgit("add", "--", "other.md")
        self.failing_signer()
        p = self.gate("seal", "W-1", "first", str(self.repo / self.r("moves.prediction.json")))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(self.staged(), ["other.md"])

    def test_a_seal_whose_commit_succeeds_is_unchanged(self):
        self.migrate("--writer", "alpha")
        self.commit("W-1: prediction", {self.r("moves.prediction.json"): tsc.GOOD_P})
        before = self.rgit("rev-parse", "HEAD")
        self.assertEqual(self.seal("first", "moves.prediction.json"), 0)
        self.assertEqual(self.rgit("rev-list", "--count", f"{before}..HEAD"), "1")
        self.assertEqual(self.rgit("log", "-1", "--format=", "--name-only").split(), ["ops/events/alpha.jsonl"])
        self.assertRegex(self.rgit("log", "-1", "--format=%B"), r"Sealed-line: [0-9a-f]{64}")
        self.assertEqual(self.staged(), [])
        self.assertEqual(self.notes(), "")


if __name__ == "__main__":
    unittest.main()
