"""Tests for 0.10.0 R2, work-order ids reserved before use (docs/PROJECT-TEAM.md, "Work-order ids reserved before
use"): with the binding's `tracker.reserve` and records format 2, `bakehouse reserve --start` sets the floor (the
highest id on every branch), `bakehouse reserve` hands out the next free id through the remote, and preflight
`change <id>` refuses an id with no reservation above the floor. Every remote is a local bare repository. Standard
library only.
python3 -m unittest runtime/tests/test_reservations.py
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
import test_records_sync as trs  # noqa: E402 - modules, so their tests are not collected here again
import test_team_preflight as ttp  # noqa: E402

LEGACY = ("ops/events.jsonl", "ops/lanes.jsonl", "decisions/ledger.jsonl", "spend/ledger.jsonl",
          "ratifications/ledger.jsonl")


class Base(trs.Base):
    """test_records_sync's machines: A (writer alpha) and, after two(), a bare origin and B (writer beta). The
    tracker is T.md, ids W-<n>."""

    def reserve_on(self, repo, prefix="W-"):
        """tracker.reserve in repo's binding, on disk (the locator reads it from there)."""
        path = repo / ".team" / "binding.json"
        b = json.loads(path.read_text())
        b["tracker"]["reserve"] = {"prefix": prefix}
        path.write_text(json.dumps(b))

    def reserve(self, repo, *args, rc=0):
        p = self.bakehouse("reserve", *args, "--repo", repo)
        self.assertEqual(p.returncode, rc, p.stdout + p.stderr)
        return p

    def branch(self, repo, name, tracker):
        """A branch off the default branch whose T.md is tracker, committed; the checkout goes back after."""
        self.g(repo, "checkout", "-q", "-b", name, self.default)
        (repo / "T.md").write_text(tracker)
        self.g(repo, "commit", "-q", "-m", f"{name}: tracker rows", "--", "T.md")
        self.g(repo, "checkout", "-q", self.default)

    def held(self, rec, ref="HEAD"):
        out = self.g(rec, "ls-tree", "--name-only", ref, "--", "reservations/", ok=False)
        return sorted(Path(x).name for x in out.splitlines() if x.strip())

    def started(self, floor_rows="| W-1 | one |\n| W-3 | three |\n"):
        """two(), both bindings with tracker.reserve, the default branch's tracker holding floor_rows, and the
        floor set by A and synced to B."""
        self.two()
        for repo in (self.repo, self.b):
            self.reserve_on(repo)
        (self.repo / "T.md").write_text(floor_rows)
        self.g(self.repo, "commit", "-q", "-m", "tracker rows", "--", "T.md")
        self.reserve(self.repo, "--start")
        self.sync(self.b)


for _n in [n for n in dir(trs.Base) if n.startswith("test")]:
    setattr(Base, _n, None)  # not callable: the loader skips the base classes' tests


class Start(Base):
    """Test 1: --start reads the tracker on every local and remote-tracking branch; a second run changes nothing."""

    def test_start_reads_every_branch_and_commits_the_floor_alone(self):
        self.two()
        for repo in (self.repo, self.b):
            self.reserve_on(repo)
        (self.repo / "T.md").write_text("| W-1 | one |\n| W-3 | three |\n")
        self.g(self.repo, "commit", "-q", "-m", "tracker rows", "--", "T.md")
        self.branch(self.repo, "f/local", "| W-1 | one |\n| W-9 | nine, on an unmerged local branch |\n")
        # B's branch reaches origin only: A sees it after --start's fetch, as a remote-tracking branch
        self.branch(self.b, "f/remote", "| W-1 | one |\n| W-12b | twelve b, with a letter after the number |\n")
        self.g(self.b, "push", "-q", "origin", "f/remote")
        self.assertEqual(self.g(self.repo, "rev-parse", "-q", "--verify", "refs/remotes/origin/f/remote", ok=False), "")
        p = self.reserve(self.repo, "--start")
        self.assertIn("W-12", p.stdout)
        floor = json.loads((self.records / "reservations" / "FLOOR").read_text())
        self.assertEqual((floor["floor"], floor["writer"]), (12, "alpha"))
        self.assertEqual(self.rgit("log", "-1", "--format=", "--name-only"), "reservations/FLOOR")
        self.assertEqual(self.tip(), self.rgit("rev-parse", "HEAD"))  # and pushed
        self.assertEqual(self.rgit("status", "--porcelain", "--", "reservations"), "")
        head = self.rgit("rev-parse", "HEAD")
        p = self.reserve(self.repo, "--start")
        self.assertIn("nothing changed", p.stdout)
        self.assertEqual(self.rgit("rev-parse", "HEAD"), head)
        # B's --start syncs first, so it finds A's floor and changes nothing either
        p = self.reserve(self.b, "--start")
        self.assertIn("nothing changed", p.stdout)
        self.assertEqual(self.tip(), head)
        self.assertEqual(self.reserve(self.repo).stdout.strip(), "W-13")


    def test_start_refuses_when_no_branch_holds_the_tracker_file(self):
        self.two()
        self.reserve_on(self.repo)
        path = self.repo / ".team" / "binding.json"
        b = json.loads(path.read_text())
        b["tracker"]["file"] = "NOT-THE-TRACKER.md"
        path.write_text(json.dumps(b))
        head = self.rgit("rev-parse", "HEAD")
        p = self.reserve(self.repo, "--start", rc=1)
        self.assertIn("no branch", p.stderr)
        self.assertIn("NOT-THE-TRACKER.md", p.stderr)
        self.assertEqual(self.rgit("rev-parse", "HEAD"), head)
        self.assertFalse((self.records / "reservations").exists())


class Race(Base):
    """Test 2: two machines reserve at once through one bare remote. B's pre-push hook runs A's reserve first, so A's
    push lands and B's is refused; B drops its losing commit and takes the next id."""

    def test_two_machines_reserving_at_once_get_different_ids_and_the_loser_stays_clean(self):
        self.started()
        hooks = Path(self.g(self.b, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        flag, out_a = self.repo.parent / "raced", self.repo.parent / "a-reserve.txt"
        (hooks / "pre-push").write_text(  # B's first push of a reservation: A reserves, and pushes, first
            "#!/bin/sh\nunset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE\n"
            f"[ -e '{flag}' ] && exit 0\n"
            "while read -r lref lsha rref rsha; do\n"
            f"  if [ \"${{lsha}}\" != \"${{rsha}}\" ] && git -C '{self.brec}' diff --name-only \"${{rsha}}\" "
            "\"${lsha}\" -- reservations/ | grep -q 'W-'; then\n"
            f"    : > '{flag}'\n"
            f"    '{sys.executable}' '{trs.twf.BAKEHOUSE}' reserve --repo '{self.repo}' > '{out_a}' 2>&1\n"
            "  fi\n"
            "done\nexit 0\n")
        (hooks / "pre-push").chmod(0o755)
        before = self.g(self.brec, "status", "--porcelain")
        got_b = self.reserve(self.b).stdout.strip()
        self.assertTrue(flag.exists())
        got_a = out_a.read_text().strip()
        self.assertEqual((got_a, got_b), ("W-4", "W-5"))
        self.assertEqual(self.held(self.origin, "refs/heads/bakehouse-records"), ["FLOOR", "W-4", "W-5"])
        self.assertEqual(json.loads(self.g(self.origin, "show", "refs/heads/bakehouse-records:reservations/W-4"))["writer"],
                         "alpha")
        self.assertEqual(json.loads(self.g(self.origin, "show", "refs/heads/bakehouse-records:reservations/W-5"))["writer"],
                         "beta")
        # the loser's dropped commit left nothing behind: a clean worktree, and no commit of W-4 by beta anywhere
        self.assertEqual(self.g(self.brec, "status", "--porcelain"), before)
        self.assertEqual(self.g(self.brec, "log", "--all", "--format=%s", "--", "reservations/W-4").splitlines(),
                         [self.g(self.origin, "log", "-1", "--format=%s", "refs/heads/bakehouse-records~1")])
        # and each next sync merges with no conflict: the three heads are one
        self.assertIn("level with origin/bakehouse-records", self.sync(self.b))
        self.assertIn("level with origin/bakehouse-records", self.sync(self.repo))
        heads = {self.tip(), self.rgit("rev-parse", "HEAD"), self.g(self.brec, "rev-parse", "HEAD")}
        self.assertEqual(len(heads), 1, heads)
        self.assertEqual(self.held(self.records), ["FLOOR", "W-4", "W-5"])


class Refused(Base):
    """B's pushes refused because the remote moved with no reservation of B's id: B merges and pushes the same id
    again; refused five times: it refuses and leaves nothing committed."""

    def moving_hook(self, every):
        """B's pre-push hook, on B's pushes of a reservation (not the sync's): A commits a note and pushes it first, on
        the first such push only, or on every one."""
        hooks = Path(self.g(self.b, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        count, flag = self.repo.parent / "pushes", self.repo.parent / "moved"
        (self.records / "notes").mkdir(exist_ok=True)
        (hooks / "pre-push").write_text(
            "#!/bin/sh\nunset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE\n"
            "while read -r lref lsha rref rsha; do\n"
            f"  git -C '{self.brec}' diff --name-only \"${{rsha}}\" \"${{lsha}}\" -- reservations/ | grep -q 'W-' "
            "|| continue\n"
            f"  echo x >> '{count}'\n"
            + ("" if every else f"  [ -e '{flag}' ] && exit 0\n  : > '{flag}'\n")
            + f"  date +%s%N >> '{self.records}/notes/moving.txt'\n"
            f"  git -C '{self.records}' add -- notes/moving.txt && "
            f"git -C '{self.records}' commit -q -m move -- notes/moving.txt && "
            f"git -C '{self.records}' push -q origin bakehouse-records\n"
            "done\nexit 0\n")
        (hooks / "pre-push").chmod(0o755)
        return count

    def test_a_push_refused_once_pushes_the_same_id_again(self):
        self.started()
        count = self.moving_hook(every=False)
        self.assertEqual(self.reserve(self.b).stdout.strip(), "W-4")
        self.assertEqual(len(count.read_text().split()), 2)
        self.assertEqual(self.held(self.origin, "refs/heads/bakehouse-records"), ["FLOOR", "W-4"])
        self.assertTrue((self.brec / "notes" / "moving.txt").exists())  # A's move was merged
        self.assertEqual(self.tip(), self.g(self.brec, "rev-parse", "HEAD"))

    def test_a_push_refused_five_times_refuses_and_leaves_nothing_committed(self):
        self.started()
        count = self.moving_hook(every=True)
        before = self.g(self.brec, "status", "--porcelain")
        p = self.reserve(self.b, rc=1)
        self.assertEqual(len(count.read_text().split()), 5)
        self.assertIn("refused 5 times", p.stderr)
        self.assertEqual(p.stdout, "")
        self.assertEqual(self.held(self.brec), ["FLOOR"])
        self.assertEqual(self.held(self.origin, "refs/heads/bakehouse-records"), ["FLOOR"])
        self.assertEqual(self.g(self.brec, "status", "--porcelain"), before)


class Drop(Base):
    """The losing commit is taken back only when it is the tip and holds only its reservation file."""

    def test_a_commit_that_is_not_the_tip_or_holds_more_is_not_taken_back(self):
        sys.path.insert(0, str(trs.RUNTIME))
        import reserve
        self.origin_setup()
        self.local_commit(self.records, "reservations/W-9", "{}\n")
        sha = self.rgit("rev-parse", "HEAD")
        self.local_commit(self.records, "notes/after.txt", "a seal's commit, after it\n")
        head = self.rgit("rev-parse", "HEAD")
        with self.assertRaises(reserve.Refused) as e:
            reserve.drop(self.records, "reservations/W-9", sha)
        self.assertIn("not the records branch's tip", str(e.exception))
        (self.records / "reservations" / "W-8").write_text("{}\n")
        (self.records / "notes" / "more.txt").write_text("more\n")
        self.g(self.records, "add", "--", "reservations/W-8", "notes/more.txt")
        self.g(self.records, "commit", "-q", "-m", "two files")
        two = self.rgit("rev-parse", "HEAD")
        with self.assertRaises(reserve.Refused):
            reserve.drop(self.records, "reservations/W-8", two)
        self.assertEqual(self.rgit("rev-parse", "HEAD~1"), head)
        self.assertEqual(self.rgit("rev-parse", "HEAD"), two)
        self.local_commit(self.records, "reservations/W-7", "{}\n")
        reserve.drop(self.records, "reservations/W-7", self.rgit("rev-parse", "HEAD"))
        self.assertEqual(self.rgit("rev-parse", "HEAD"), two)
        self.assertFalse((self.records / "reservations" / "W-7").exists())
        self.assertEqual(self.rgit("status", "--porcelain", "--", "reservations"), "")


class Lock(Base):
    """reserve holds the sync lock from its sync to its last push: it waits for a sync that holds it."""

    def test_reserve_waits_for_the_sync_lock(self):
        self.started()
        lock = Path(self.rgit("rev-parse", "--absolute-git-dir")) / trs.records_sync.LOCK
        with lock.open("a") as f:
            trs.fcntl.flock(f, trs.fcntl.LOCK_EX)
            p = subprocess.Popen([sys.executable, str(trs.twf.BAKEHOUSE), "reserve", "--repo", str(self.repo)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env)
            with self.assertRaises(subprocess.TimeoutExpired):
                p.wait(timeout=2)
            self.assertEqual(self.held(self.records), ["FLOOR"])
        out, err = p.communicate(timeout=30)
        self.assertEqual((p.returncode, out.strip()), (0, "W-4"), err)


class Offline(Base):
    """Test 3: with the remote out of reach, reserve refuses, naming the remote, and commits nothing."""

    def test_offline_reserve_refuses_naming_the_remote_and_commits_nothing(self):
        self.started()
        head = self.rgit("rev-parse", "HEAD")
        self.origin.rename(self.origin.with_name("away.git"))
        p = self.reserve(self.repo, rc=1)
        self.assertIn("an id needs the remote, and the sync could not reach it", p.stderr)
        self.assertIn("fetch to origin", p.stderr)  # the sync's own fetch: nothing was committed to push
        self.assertEqual(p.stdout, "")
        self.assertEqual(self.rgit("rev-parse", "HEAD"), head)
        self.assertEqual(sorted(x.name for x in (self.records / "reservations").iterdir()), ["FLOOR"])
        self.assertEqual(self.rgit("status", "--porcelain", "--", "reservations"), "")

    def test_offline_start_refuses_and_commits_nothing(self):
        self.two()
        self.reserve_on(self.repo)
        head = self.rgit("rev-parse", "HEAD")
        self.origin.rename(self.origin.with_name("away.git"))
        p = self.reserve(self.repo, "--start", rc=1)
        self.assertIn("needs the remote", p.stderr)
        self.assertEqual(self.rgit("rev-parse", "HEAD"), head)
        self.assertFalse((self.records / "reservations").exists())


class BeforeStart(Base):
    """Test 5: reserve before --start refuses, naming it."""

    def test_reserve_before_start_refuses_naming_it(self):
        self.two()
        self.reserve_on(self.repo)
        head = self.rgit("rev-parse", "HEAD")
        p = self.reserve(self.repo, rc=1)
        self.assertIn("bakehouse reserve --start", p.stderr)
        self.assertEqual(self.rgit("rev-parse", "HEAD"), head)
        self.assertFalse((self.records / "reservations").exists())


class Off(Base):
    """Test 6: reservations off. No tracker.reserve, or records format 1: reserve refuses and changes nothing."""

    def test_no_reserve_field_refuses_and_changes_nothing(self):
        self.two()
        before = (self.rgit("rev-parse", "HEAD"), self.tip())
        p = self.reserve(self.repo, rc=1)
        self.assertIn("tracker.reserve", p.stderr)
        self.assertEqual((self.rgit("rev-parse", "HEAD"), self.tip()), before)

    def test_format_1_refuses_and_changes_nothing(self):
        self.origin_setup(migrate=False)
        self.reserve_on(self.repo)
        before = (self.rgit("rev-parse", "HEAD"), self.tip())
        for args in ((), ("--start",)):
            p = self.reserve(self.repo, *args, rc=1)
            self.assertIn("records format 2", p.stderr)
        self.assertEqual((self.rgit("rev-parse", "HEAD"), self.tip()), before)


class Preflight(ttp.Fixture):
    """Test 4, and test 6 for preflight: `change <id>` with reservations on. The records are made format 2 by hand
    (FORMAT naming the five legacy files absent), with a floor of T7, T9 reserved by this writer (alpha) and T10 by
    beta."""

    ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def setUp(self):
        super().setUp()
        self.env.update(self.ENV)
        self.rec = Path(self.tmp.name) / "repo.team-records"

    def rg(self, *args):
        p = subprocess.run(["git", "-C", str(self.rec), *args], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout.strip()

    def records(self, fmt=2, floor=7, reserved=(("T9", "alpha"), ("T10", "beta"))):
        files = {}
        if fmt == 2:
            files["FORMAT"] = json.dumps({"format": 2, "bakehouse": "0.8.0", "frozen": {r: "absent" for r in LEGACY}})
        if floor is not None:
            files["reservations/FLOOR"] = json.dumps({"floor": floor, "writer": "alpha", "time": "2026-11-20T10:00:00+00:00"})
        for wo, w in reserved:
            files[f"reservations/{wo}"] = json.dumps({"id": wo, "writer": w, "time": "2026-11-20T10:01:00+00:00"})
        for name, text in files.items():
            (self.rec / name).parent.mkdir(parents=True, exist_ok=True)
            (self.rec / name).write_text(text + "\n")
        self.rg("add", "-A")
        self.rg("commit", "-q", "--allow-empty", "-m", "scratch records")
        self.rg("config", "bakehouse.writer", "alpha")

    def on(self, **tracker):
        self.owned(records={"codeRoots": ["docs/"]},
                   tracker={"file": "docs/TRACKER.md", "id": "^T[0-9]+[a-z]?$", "reserve": {"prefix": "T"}, **tracker})

    def test_an_unreserved_id_above_the_floor_fails_naming_reserve(self):
        self.records()
        self.on()
        code, out = self.pre("change", "T11")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL check 7: work order T11", out)
        self.assertIn("bakehouse reserve", out)

    def test_a_reserved_id_passes(self):
        self.records()
        self.on()
        for wo in ("T9", "T9b"):  # a letter after the number counts as its number
            with self.subTest(wo=wo):
                code, out = self.pre("change", wo)
                self.assertEqual(code, 0, out)
                self.assertNotIn("check 7", out)

    def test_an_id_at_or_under_the_floor_passes(self):
        self.records()
        self.on()
        for wo in ("T1", "T7", "T7b"):
            with self.subTest(wo=wo):
                code, out = self.pre("change", wo)
                self.assertEqual(code, 0, out)
                self.assertNotIn("check 7", out)

    def test_another_writers_reservation_is_a_note(self):
        self.records()
        self.on()
        code, out = self.pre("change", "T10")
        self.assertEqual(code, 0, out)
        self.assertIn("note check 7: work order T10 is reserved by writer beta", out)
        self.assertNotIn("FAIL", out)

    def test_no_floor_fails_naming_start(self):
        self.records(floor=None, reserved=())
        self.on()
        code, out = self.pre("change", "T1")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL check 7", out)
        self.assertIn("bakehouse reserve --start", out)

    def test_a_bad_reserve_field_fails_check_2(self):
        self.records()
        for bad in ({"prefix": 5}, {"prefix": "7A"}, {"prefix": "T", "other": 1}, "T", {"prefix": "T/"}):
            with self.subTest(bad=bad):
                self.owned(records={"codeRoots": ["docs/"]},
                           tracker={"file": "docs/TRACKER.md", "id": "^T[0-9]+$", "reserve": bad})
                code, out = self.pre("health")
                self.assertEqual(code, 1, out)
                self.assertIn("FAIL check 2: field 'tracker.reserve'", out)

    def test_off_no_reserve_field_or_format_1_changes_nothing(self):
        for fmt, field in ((2, False), (1, True)):
            with self.subTest(fmt=fmt, field=field):
                self.records(fmt=fmt)
                if field:
                    self.on()
                else:
                    self.owned(records={"codeRoots": ["docs/"]})
                code, out = self.pre("change", "T11")
                self.assertEqual(code, 0, out)
                self.assertNotIn("check 7", out)
                self.rg("rm", "-q", "-r", "--ignore-unmatch", "FORMAT", "reservations")
                self.rg("commit", "-q", "--allow-empty", "-m", "clear")


if __name__ == "__main__":
    unittest.main()
