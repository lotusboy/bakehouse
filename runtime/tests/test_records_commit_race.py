"""Tests for 0.14.3: in records format 2, a commit_own whose line an earlier call's commit took, while a third call
appends its line before this one checks, says nothing and syncs (the AtOnce failure on Linux, made deterministic).
Every remote is a local bare repository. Standard library only.
python3 -m unittest runtime/tests/test_records_commit_race.py
"""

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_records_self_commit as trsc  # noqa: E402 - a module, so its tests are not collected here again
import ops_ledger  # noqa: E402 - runtime/, on the path through test_records_sync
import records_sync  # noqa: E402

SUBJECT = "decisions: ask {} (decision.sh)"


class Race(trsc.Base):
    """The order: A and B append; A's commit takes both lines; B's commit finds nothing to commit; C appends; B checks."""

    def commit_own(self, line, after_anchor=None):
        real = ops_ledger.anchor

        def anchor(*args, **kwargs):
            why = real(*args, **kwargs)
            if after_anchor:
                after_anchor()
            return why

        err = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(ops_ledger, "anchor", anchor), \
                contextlib.redirect_stderr(err):
            records_sync.commit_own(self.records, self.wfile("decisions_ledger"), SUBJECT.format(line), line)
        return err.getvalue()

    def test_a_line_already_committed_says_nothing_when_a_later_line_comes_before_the_check(self):
        self.origin_setup()
        self.sync(self.repo)
        self.ask(self.repo, "First?")
        path = self.wfile("decisions_ledger")
        rel = path.relative_to(self.records).as_posix()
        a, b, c = ('{"kind": "ask", "id": "%s"}\n' % x for x in "abc")
        with path.open("a") as f:
            f.write(a + b)

        def append_c():
            with path.open("a") as f:
                f.write(c)

        self.assertNotIn("not committed", self.commit_own(a))  # A's commit: both lines
        err = self.commit_own(b, append_c)  # B's: nothing to commit, then C's line before the check
        self.assertNotIn("not committed", err)
        self.assertEqual(len(self.g(self.records, "show", f"HEAD:{rel}").splitlines()), 3)
        self.assertEqual(self.g(self.records, "diff", "--cached", "--name-only"), "")  # C's line is not staged
        self.assertEqual(self.tip(), self.rgit("rev-parse", "HEAD"))  # and B synced
        self.assertNotIn("not committed", self.commit_own(c))
        self.assertEqual(len(self.g(self.records, "show", f"HEAD:{rel}").splitlines()), 4)
        self.assertEqual(self.rgit("status", "--porcelain", "--", rel), "")


if __name__ == "__main__":
    unittest.main()
