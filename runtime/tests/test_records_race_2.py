"""Tests for 0.15.2: in records format 2, a line appended while commit_own's `git commit -- <file>` runs leaves no
staged leftover (the second AtOnce failure, made deterministic). `git commit --only` reads the file twice: once for
the real index, once for the commit; a line appended between the two reads went into the commit but not the index,
so the index kept an older copy of the file (git status: MM). A clean filter appends the line between the reads.
Every remote is a local bare repository. Standard library only.
python3 -m unittest runtime/tests/test_records_race_2.py
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
import records_sync  # noqa: E402 - runtime/, on the path through test_records_sync

SUBJECT = "decisions: ask {} (decision.sh)"
FILTER = """#!/bin/sh
# pass the file through; in `git add`, move its mtime, so the commit's first read hashes it again; in `git commit`,
# on the first read only, append a line to the file: after the real index's read, before the commit's
cat
case " $(ps -o args= -p $PPID) " in
  *" commit "*) if [ ! -e '{mark}' ]; then : > '{mark}'; printf '{line}' >> '{file}'; fi ;;
  *" add "*) touch -t 203001010000 '{file}' ;;
esac
"""


class AppendInCommit(trsc.Base):
    """The order: B appends; B's commit reads the file for the index; C appends; B's commit reads it for HEAD."""

    def commit_own(self, line):
        err = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stderr(err):
            records_sync.commit_own(self.records, self.wfile("decisions_ledger"), SUBJECT.format(line), line)
        return err.getvalue()

    def test_a_line_appended_between_the_commits_two_reads_is_committed_and_nothing_is_left_staged(self):
        self.origin_setup()
        self.sync(self.repo)
        self.ask(self.repo, "First?")
        path = self.wfile("decisions_ledger")
        rel = path.relative_to(self.records).as_posix()
        b, c = ('{"kind": "ask", "id": "%s"}\n' % x for x in "bc")
        gitdir = Path(self.g(self.records, "rev-parse", "--absolute-git-dir"))
        script = gitdir / "bakehouse-test-race.sh"
        script.write_text(FILTER.replace("{mark}", str(gitdir / "bakehouse-test-race.mark"))
                          .replace("{file}", str(path)).replace("{line}", c.replace("\n", "\\n")))
        script.chmod(0o755)
        self.g(self.records, "config", "filter.race.clean", str(script))
        attributes = Path(self.g(self.records, "rev-parse", "--path-format=absolute", "--git-path", "info/attributes"))
        attributes.parent.mkdir(parents=True, exist_ok=True)
        attributes.write_text(f"{rel} filter=race\n")
        with path.open("a") as f:
            f.write(b)
        self.assertNotIn("not committed", self.commit_own(b))  # B's commit: its line, and C's
        self.assertEqual(len(self.g(self.records, "show", f"HEAD:{rel}").splitlines()), 3)
        self.assertEqual(self.rgit("status", "--porcelain", "--", rel), "")  # the index is HEAD's, not B's first read
        self.assertNotIn("not committed", self.commit_own(c))  # C's: already committed
        self.assertEqual(self.rgit("status", "--porcelain", "--", rel), "")
        self.assertEqual(self.tip(), self.rgit("rev-parse", "HEAD"))


if __name__ == "__main__":
    unittest.main()
