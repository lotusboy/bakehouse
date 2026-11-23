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


    def test_a_repeated_line_whose_commit_really_fails_says_not_committed(self):
        """The same row twice (two records in one second): HEAD holds it once, so a refused commit is not taken for one."""
        self.origin_setup()
        self.sync(self.repo)
        self.ask(self.repo, "First?")
        path = self.wfile("decisions_ledger")
        rel = path.relative_to(self.records).as_posix()
        a = '{"ts": "2026-11-23T10:00:00+00:00", "event": "agent", "agent": "x", "tokens": 5}\n'
        with path.open("a") as f:
            f.write(a)
        self.assertNotIn("not committed", self.commit_own(a))
        with path.open("a") as f:
            f.write(a)
        hooks = Path(self.g(self.records, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
        (hooks / "pre-commit").chmod(0o755)
        self.assertIn("not committed", self.commit_own(a))
        self.assertEqual(self.rgit("status", "--porcelain", "--", rel), "M " + rel)

    def test_a_line_glued_to_a_torn_last_line_and_committed_by_another_call_says_nothing(self):
        self.origin_setup()
        self.sync(self.repo)
        self.ask(self.repo, "First?")
        path = self.wfile("decisions_ledger")
        b, c = ('{"kind": "ask", "id": "%s", "question": "caf\u00e9 \u2014 ok?"}\n' % x for x in "bc")
        with path.open("a", encoding="utf-8") as f:
            f.write("torn")
        self.commit_own("torn")
        with path.open("a", encoding="utf-8") as f:
            f.write(b + c)
        self.assertNotIn("not committed", self.commit_own(c))  # takes b's line too
        self.assertNotIn("not committed", self.commit_own(b))


class Callers(trsc.Base):
    """decision.py and spend.py pass commit_own the very line they wrote, non-ASCII text included."""

    def last_line_and_passed(self, module, name, event):
        import locator
        passed = []
        with mock.patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(records_sync, "commit_own", lambda *a: passed.append(a[3])):
            project = locator.find(self.repo, dict(os.environ))
            module.append(project, event)
            written = project.write_file(name).read_text(encoding="utf-8").splitlines(keepends=True)[-1]
        return written, passed

    def test_decision_and_spend_pass_the_line_they_wrote(self):
        import decision
        import spend
        import json
        self.origin_setup()
        self.sync(self.repo)
        self.ask(self.repo, "First?")  # a real ask, so the second has every field the ledger's readers expect
        ask = json.loads(self.wfile("decisions_ledger").read_text(encoding="utf-8").splitlines()[-1])
        ask.update(id="d20261123-000001", question="caf\u00e9 \u2014 go?")
        for module, name, event in (
                (decision, "decisions_ledger", ask),
                (spend, "spend_ledger", {"ts": "2026-11-23T10:00:00+00:00", "event": "agent", "note": "na\u00efve"})):
            written, passed = self.last_line_and_passed(module, name, event)
            self.assertEqual(passed, [written])
            self.assertIn("\u00e9" if module is decision else "\u00ef", written)


if __name__ == "__main__":
    unittest.main()
