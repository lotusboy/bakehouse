"""Tests for 0.8.0 R1a, one records file per writer (docs/PROJECT-TEAM.md, "One records file per writer"):
`bakehouse setup migrate` freezes the five legacy ledgers and writes FORMAT; each tool then appends to its writer's
own file (<ledger without .jsonl>/<writer>.jsonl), and every reader reads the legacy file, then every writer file.
A records folder with no FORMAT keeps today's behaviour (the rest of the suite). Standard library only; every
test builds scratch repositories under one temporary folder, as test_sealed_close does.
python3 -m unittest runtime/tests/test_writer_files.py
"""

import hashlib
import importlib.util
import json
import re
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(TESTS))
import locator  # noqa: E402
import test_sealed_close as tsc  # noqa: E402 - a module, so its tests are not collected here again

BAKEHOUSE = RUNTIME.parent / "bin" / "bakehouse"
REC = tsc.REC
LEGACY = sorted(locator.LEDGERS.values())


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, RUNTIME / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Base(tsc.Sealed):
    """test_sealed_close's scratch project (tree mode: record folders in the code tree, seals anchored in the
    records worktree), with helpers for the writer files. None of Sealed's own tests run here."""

    def run_(self, cmd, cwd=None, env=None):
        return subprocess.run([str(c) for c in cmd], cwd=cwd or self.repo, capture_output=True, text=True,
                              env=dict(self.env, **(env or {})), check=False)

    def bakehouse(self, *args, cwd=None):
        return self.run_([sys.executable, BAKEHOUSE, *args], cwd=cwd)

    def tool(self, name, *args, cwd=None, env=None):
        return self.run_([self.rt / name, *args], cwd=cwd, env=env)

    def rgit(self, *args):
        return subprocess.run(["git", "-C", str(self.records), *args], capture_output=True, text=True,
                              env=self.env, check=False).stdout.strip()

    def migrate(self, *extra, ok=True):
        p = self.bakehouse("setup", "migrate", *extra)
        if ok:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def commit_records(self, msg="records"):
        """The PM's records commit, the ops ledger too, so the records worktree is clean before a migration."""
        self.rgit("add", "-A")
        self.rgit("commit", "-q", "-m", msg)

    def legacy(self):
        return {rel: (self.records / rel).read_bytes() if (self.records / rel).exists() else None for rel in LEGACY}

    def wfile(self, field, w="alpha"):
        return locator.writer_folder(self.records / locator.LEDGERS[field]) / f"{w}.jsonl"

    def rows(self, path):
        return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []

    def note(self, purpose="a note", repo=None):
        return self.tool("ops-event.sh", "note", "-", purpose, env={"OPS_REPO": str(repo or self.repo)})

    def status(self):
        p = self.bakehouse("status", "--json", "--repo", self.repo)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads(p.stdout)


for _n in [n for n in dir(tsc.Sealed) if n.startswith("test")]:
    setattr(Base, _n, None)  # not callable: the loader skips Sealed's own tests


class Migration(Base):
    """Test 1: the migration writes FORMAT and the writer id; a second run is a no-op; uncommitted records refuse."""

    def test_migrate_writes_format_and_the_writer_id_and_commits_format_by_path(self):
        self.flow()  # seals in the legacy ops ledger, anchored
        self.decide("d20261101-aaaaa1")
        self.commit_records()  # every legacy file is committed: migration freezes them as git holds them
        before = self.legacy()
        p = self.migrate("--writer", "alpha")
        self.assertIn("records format 2", p.stdout)
        fmt = json.loads((self.records / "FORMAT").read_text())
        self.assertEqual((fmt["format"], fmt["bakehouse"]), (2, "0.8.0"))
        self.assertEqual(fmt["frozen"], {rel: hashlib.sha256(b).hexdigest() if b is not None else "absent"
                                         for rel, b in before.items()})
        self.assertEqual(fmt["frozen"]["spend/ledger.jsonl"], "absent")
        self.assertEqual(self.rgit("config", "--get", "bakehouse.writer"), "alpha")
        self.assertEqual(self.rgit("log", "-1", "--format=", "--name-only"), "FORMAT")
        self.assertEqual(self.rgit("status", "--porcelain", "--", "FORMAT"), "")
        self.assertEqual(self.legacy(), before)

    def test_a_second_run_changes_nothing_and_says_so(self):
        self.migrate("--writer", "alpha")
        head, text = self.rgit("rev-parse", "HEAD"), (self.records / "FORMAT").read_bytes()
        p = self.migrate()
        self.assertIn("already in records format 2", p.stdout)
        self.assertIn("nothing changed", p.stdout)
        self.assertEqual((self.rgit("rev-parse", "HEAD"), (self.records / "FORMAT").read_bytes()), (head, text))
        self.assertEqual(self.rgit("config", "--get", "bakehouse.writer"), "alpha")

    def test_uncommitted_changes_to_tracked_records_refuse(self):
        self.flow()
        self.assertEqual(self.note().returncode, 0)  # a line in the tracked ops ledger that no commit holds yet
        p = self.migrate(ok=False)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("uncommitted changes to tracked files (ops/events.jsonl)", p.stdout)
        self.assertFalse((self.records / "FORMAT").exists())
        self.assertEqual(self.rgit("config", "--get", "bakehouse.writer"), "")
        self.commit_records()
        self.migrate()

    def test_an_untracked_legacy_file_refuses(self):
        # F5: a legacy file git does not hold would be frozen on this machine only, and refuse on every clone
        self.decide("d20261101-aaaaa1")  # the decision ledger, written and not committed
        p = self.migrate("--writer", "alpha", ok=False)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("decisions/ledger.jsonl", p.stdout)
        self.assertIn("not committed", p.stdout)
        self.assertFalse((self.records / "FORMAT").exists())
        self.assertEqual(self.rgit("config", "--get", "bakehouse.writer"), "")

    def test_a_failed_migration_commit_leaves_no_writer_id_and_no_format(self):
        # F8: "Nothing migrated" holds: the writer id is set only after the FORMAT commit
        hooks = Path(self.git("rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
        (hooks / "pre-commit").chmod(0o755)
        p = self.migrate("--writer", "alpha", ok=False)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("Nothing migrated", p.stdout)
        self.assertFalse((self.records / "FORMAT").exists())
        self.assertEqual(self.rgit("config", "--get", "bakehouse.writer"), "")
        self.assertEqual(self.rgit("status", "--porcelain"), "")

    def test_migrate_without_a_name_sets_w_and_8_hex_and_setup_keeps_it(self):
        self.migrate()
        w = self.rgit("config", "--get", "bakehouse.writer")
        self.assertRegex(w, r"^w[0-9a-f]{8}$")
        p = self.bakehouse("setup", "--pre-commit")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(f"writer id: {w}", p.stdout)
        p = self.bakehouse("setup", "--writer", "other")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn(f"already has the writer id {w}", p.stdout)
        self.assertEqual(self.rgit("config", "--get", "bakehouse.writer"), w)

    def test_setup_sets_the_given_writer_id_and_refuses_a_bad_name(self):
        for bad in ("a b", "x" * 33, "é", ""):
            p = self.bakehouse("setup", "--writer", bad)
            self.assertEqual(p.returncode, 2, (bad, p.stdout))
        self.assertEqual(self.rgit("config", "--get", "bakehouse.writer"), "")
        p = self.bakehouse("setup", "--writer", "Mac-1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.rgit("config", "--get", "bakehouse.writer"), "Mac-1")


class Writes(Base):
    """Test 2: after migration each of the five writers appends to its writer file; no legacy file changes."""

    def test_each_tool_appends_to_its_writer_file_and_no_legacy_file_changes(self):
        self.decide("d20261101-aaaaa1")
        self.assertEqual(self.note("before migration").returncode, 0)
        self.commit_records()
        self.migrate("--writer", "alpha")
        before = self.legacy()
        # the ops ledger: ops-event.sh, every seal, and the close
        self.assertEqual(self.note("after migration").returncode, 0)
        self.flow()
        self.assertEqual(self.rgit("log", "-1", "--format=", "--name-only"), "ops/events/alpha.jsonl")
        self.assertCloses()
        kinds = [e["kind"] for e in self.rows(self.wfile("ops_ledger"))]
        self.assertEqual(kinds, ["note", "sealed", "sealed", "sealed", "sealed", "work-order-closed"])
        whole = self.gate("verify-chain")
        self.assertEqual(whole.returncode, 0, whole.stdout + whole.stderr)
        self.assertIn("ops/events/alpha.jsonl: Chain: whole. 5 chained event(s), lines 2 to 6", whole.stdout)
        # the lane ledger
        p = self.tool("lane-event.sh", "lane-opened", "lane=a", env={"OPS_REPO": str(self.repo)})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([e["kind"] for e in self.rows(self.wfile("lanes_ledger"))], ["lane-opened"])
        # the decision ledger
        p = self.tool("decision.sh", "ask", "--repo", self.repo, "--category", "spend", "--question", "Run it?",
                      "--option", "yes", "--option", "no", "--recommendation", "yes", "--confidence", "0.9",
                      "--evidence-kind", "measured", "--evidence", "WIP.md")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([e["kind"] for e in self.rows(self.wfile("decisions_ledger"))], ["ask"])
        # the spend ledger
        p = self.tool("spend.sh", "wave", "open", "wave-1", "--ceiling", "1000", "--repo", self.repo)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([e["event"] for e in self.rows(self.wfile("spend_ledger"))], ["wave-open"])
        # the ratification ledger
        agreement = self.repo.parent / "agreement.json"
        agreement.write_text(json.dumps({"reports": [], "entries": [
            {"id": "X1", "patternHash": "h", "patternKind": "regex", "pattern": "p", "class": "c",
             "verdict": "ratified", "readers": {}}]}))
        p = self.tool("ratify.sh", "record", "--repo", self.repo, "--agreement", agreement)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual([e["id"] for e in self.rows(self.wfile("ratify_ledger"))], ["X1"])
        p = self.tool("ratify.sh", "status", "--repo", self.repo)
        self.assertIn("1 entries", p.stdout)
        self.assertEqual(self.legacy(), before)


class SameAnswers(Base):
    """Test 3: status, close, records-check and the stop's unsealed check give the same answers on the same
    events, whether they sit in the legacy file (no FORMAT) or in a writer file (migrated first)."""

    def answers(self):
        out = {}
        self.commit("W-1: prediction", {self.r("moves.prediction.json"): tsc.GOOD_P,
                                        self.r("moves.baseline.json"): tsc.BASE_M})
        self.assertEqual(self.seal("first", "moves.prediction.json"), 0)
        self.assertEqual(self.seal("first", "moves.baseline.json"), 0)
        b = self.head()
        self.code("W-1: the change", {"src/app.py": "v = 2\n"})
        out["open"] = [(w["workOrder"], w["events"], w["latestKind"], w["latestRound"])
                       for w in self.status()["workOrders"]]
        rc, text = self.close()
        out["early close"] = (rc, sorted(re.findall(r"^  - (\S+):", text, re.M)))
        self.measure("first", tsc.PASS_M, b, self.head())
        self.put("moves.prediction.fix1.json", tsc.FIX_P)  # on disk, not sealed
        self.commit("W-1: measurement")
        roles = load("agent_roles_writer_test", "agent-roles.py")
        out["unsealed"] = [(p.name, why) for p, why in roles.unsealed_moves([self.repo / REC], str(self.repo), None,
                                                                            "W-1")]
        (self.repo / self.r("moves.prediction.fix1.json")).unlink()
        self.commit("W-1: fix1 not now")
        rc, text = self.close()
        out["close"] = (rc, text.splitlines()[-1].split(" Recorded in ")[0])  # the file it names differs
        out["closed"] = [(c["workOrder"], c["overridden"]) for c in self.status()["closedSince"]]
        reg = self.repo.parent / "records.json"
        p = self.run_([sys.executable, self.rt / "records-check.py", "--repo", self.repo, "--as-of", "2026-11-20",
                       "--since", "2026-11-01", "--register", reg])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        r = json.loads(reg.read_text())
        out["records-check"] = ({w: v["close"] for w, v in r["workOrders"].items()}, r["computed"]["ledger"],
                                r["computed"]["openFlags"])
        return out

    def test_the_answers_are_the_same_before_and_after_migration(self):
        one = self.answers()
        twin = type(self)(self._testMethodName)
        twin.setUp()
        self.addCleanup(twin.tearDown)
        twin.migrate("--writer", "alpha")
        two = twin.answers()
        self.assertFalse(twin.legacy()["ops/events.jsonl"])  # every event went to the writer file
        self.assertTrue(twin.rows(twin.wfile("ops_ledger")))
        self.assertEqual(one, two)
        self.assertEqual(one["close"][0], 0)
        self.assertEqual(one["unsealed"], [("moves.prediction.fix1.json", None)])
        self.assertEqual(one["records-check"][1], "ops/events.jsonl")


class AcrossTheMigration(Base):
    """Test 4: a work order sealed in the legacy file and closed after migration closes: its seal and close order
    holds across the two files."""

    def test_prediction_sealed_before_migration_code_and_measurement_after_close(self):
        self.commit("W-1: prediction", {self.r("moves.prediction.json"): tsc.GOOD_P,
                                        self.r("moves.baseline.json"): tsc.BASE_M})
        self.assertEqual(self.seal("first", "moves.prediction.json"), 0)
        self.assertEqual(self.seal("first", "moves.baseline.json"), 0)
        self.migrate("--writer", "alpha")
        before = self.legacy()
        b = self.head()
        self.assertEqual(self.code("W-1: the change", {"src/app.py": "v = 2\n"}), 0)
        self.assertEqual(self.measure("first", tsc.PASS_M, b, self.head()), 0)
        self.commit("W-1: measurement")
        rc, out = self.close("sealed: judged by 4 committed sealed event(s)")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.legacy(), before)
        self.assertEqual([e["kind"] for e in self.rows(self.wfile("ops_ledger"))],
                         ["sealed", "sealed", "work-order-closed"])

    def test_a_whole_round_sealed_before_migration_closes_after_it(self):
        self.flow()
        self.migrate("--writer", "alpha")
        self.assertCloses()
        self.assertEqual([e["kind"] for e in self.rows(self.wfile("ops_ledger"))], ["work-order-closed"])

    def test_a_seal_after_migration_is_judged_against_the_legacy_files_baseline_seal(self):
        # the seal's own rules read the legacy file first: its baseline seal makes a changed baseline refuse, and
        # lets the measured seal after migration find the sealed baseline
        self.commit("W-1: prediction", {self.r("moves.prediction.json"): tsc.GOOD_P,
                                        self.r("moves.baseline.json"): tsc.BASE_M})
        self.assertEqual(self.seal("first", "moves.prediction.json"), 0)
        self.assertEqual(self.seal("first", "moves.baseline.json"), 0)
        self.assertEqual([e["fileKind"] for e in self.rows(self.records / "ops/events.jsonl")],
                         ["prediction", "baseline"])
        self.migrate("--writer", "alpha")
        self.put("moves.baseline.json", {"out:v": 9, "out:w": 5})
        p = self.gate("seal", "W-1", "first", str(self.repo / self.r("moves.baseline.json")))
        self.assertNotEqual(p.returncode, 0, p.stdout)
        self.assertIn("changed since its seal", p.stdout + p.stderr)
        self.put("moves.baseline.json", tsc.BASE_M)
        b = self.head()
        self.assertEqual(self.code("W-1: the change", {"src/app.py": "v = 2\n"}), 0)
        self.assertEqual(self.measure("first", tsc.PASS_M, b, self.head()), 0)
        self.assertEqual([e["fileKind"] for e in self.rows(self.wfile("ops_ledger")) if e["kind"] == "sealed"],
                         ["code", "measured"])  # beside the refused seal's gate-refused event


def work_order(case, repo, wo, src, value, when, close_ok=True):
    """One sealed round of wo in repo (tree mode), sealed and closed through predict-gate, as Sealed.flow does for
    W-1. Returns the close's output; close_ok=False: the close may refuse."""
    folder = f"runs/2026-11-10-{wo}-step1-review"

    def git(*a, dated=False):
        env = dict(case.env, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when) if dated else case.env
        p = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=env, check=False)
        case.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def gate(*a, ok=True):
        p = subprocess.run([sys.executable, str(case.rt / "predict-gate.py"), *a], cwd=repo, capture_output=True,
                           text=True, env=dict(case.env, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when), check=False)
        if ok:
            case.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def put(name, data):
        (repo / folder).mkdir(parents=True, exist_ok=True)
        (repo / folder / name).write_text(json.dumps(data))

    put("moves.prediction.json", {"moves": [{"what": "out:v", "from": 1, "to": 2}]})
    put("moves.baseline.json", {"out:v": 1})
    git("add", "-A")
    git("commit", "-q", "-m", f"{wo}: prediction", dated=True)
    for n in ("moves.prediction.json", "moves.baseline.json"):
        gate("seal", wo, "first", str(repo / folder / n))
    base = git("rev-parse", "HEAD")
    (repo / src).parent.mkdir(parents=True, exist_ok=True)
    (repo / src).write_text(value)
    git("add", "-A")
    gate("seal-code", wo, "first", "--", "-q", "-m", f"{wo}: the change")
    put("moves.measured.json", {"out:v": 2})
    put("measure-report.json", {"base": {"sha": base}, "head": {"sha": git("rev-parse", "HEAD")}})
    gate("seal", wo, "first", str(repo / folder / "moves.measured.json"))
    git("add", "-A")
    git("commit", "-q", "-m", f"{wo}: measurement", dated=True)
    return gate("close", wo, ok=close_ok)


class TwoWriters(Base):
    """Test 5: two clones of one records repository, each with its own writer id, each seal and close a different
    work order; a plain git pull (a merge) has no conflict, and each side's readers see both."""

    def test_two_machines_seal_and_close_apart_and_a_pull_merges_both(self):
        origin = self.repo.parent / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, env=self.env)
        (self.records / ".gitignore").write_text(load("setup_writer_test", "bakehouse_setup.py").GITIGNORE)
        self.commit_records("the records branch's .gitignore, as setup writes it")
        self.migrate("--writer", "alpha")
        self.git("remote", "add", "origin", str(origin))
        self.git("push", "-q", "origin", self.default, "bakehouse-records")
        self.rgit("branch", "-q", "--set-upstream-to", "origin/bakehouse-records")
        # machine B: its own clone and its own records worktree of the same records branch
        b = self.repo.parent / "b"
        brec = self.repo.parent / "b.bakehouse-records"
        for a in (["clone", "-q", str(origin), str(b)],
                  ["-C", str(b), "worktree", "add", "-q", "--track", "-b", "bakehouse-records", str(brec),
                   "origin/bakehouse-records"],
                  ["-C", str(b), "config", "bakehouse.records", str(brec)]):
            subprocess.run(["git", *a], check=True, capture_output=True, env=self.env)
        p = self.bakehouse("setup", "--writer", "beta", cwd=b)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        # each machine seals and closes its own work order, then commits its records (the PM's commit)
        self.assertIn("Result: CLOSED", work_order(self, self.repo, "W-1", "src/a.py", "a = 1\n",
                                                   "2026-11-10T09:30:00+00:00"))
        self.assertIn("Result: CLOSED", work_order(self, b, "W-2", "src/b.py", "b = 1\n",
                                                   "2026-11-10T09:40:00+00:00"))
        for rec, w in ((self.records, "alpha"), (brec, "beta")):
            (rec / "spend" / "ledger").mkdir(parents=True, exist_ok=True)
            (rec / "spend" / "ledger" / f"{w}.jsonl").write_text("{}\n")  # another record, for the PM's commit
            pm_commit(self, rec)  # the documented commit: the ops files stay with the seal anchors
        self.rgit("push", "-q", "origin", "bakehouse-records")
        # B pulls A's records (a merge: both sides have commits), pushes; A pulls (a fast forward)
        for rec, push in ((brec, True), (self.records, False)):
            p = subprocess.run(["git", "-C", str(rec), "pull", "-q", "--no-rebase", "--no-edit"],
                               capture_output=True, text=True, env=self.env)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            if push:
                subprocess.run(["git", "-C", str(rec), "push", "-q"], check=True, capture_output=True, env=self.env)
        pg = tsc.pg
        for repo, rec in ((self.repo, self.records), (b, brec)):
            names = sorted(p.name for p in (rec / "ops" / "events").glob("*.jsonl"))
            self.assertEqual(names, ["alpha.jsonl", "beta.jsonl"], rec)
            p = self.bakehouse("status", "--json", "--repo", repo)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            # a close line is committed by the next seal's anchor only, so each side sees its own close so far
            own = "W-1" if rec == self.records else "W-2"
            self.assertEqual([c["workOrder"] for c in json.loads(p.stdout)["closedSince"]], [own])
            files = locator.ledger_files(rec, "ops_ledger")
            for wo in ("W-1", "W-2"):
                sealed, kept, refused = pg.sealed_state(files, wo)
                self.assertEqual((sealed, len(kept), refused), (True, 4, []), (rec, wo))
            p = subprocess.run([sys.executable, str(self.rt / "predict-gate.py"), "verify-chain"], cwd=repo,
                               capture_output=True, text=True, env=self.env)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("ops/events/beta.jsonl: Chain: whole.", p.stdout)


class Refusals(Base):
    """Test 6: a changed frozen file refuses, naming it; an unknown FORMAT refuses, naming the version; a write
    with no writer id after migration refuses, naming the setup command."""

    def test_a_changed_frozen_file_refuses_naming_it(self):
        self.flow()
        self.migrate("--writer", "alpha")
        with (self.records / "ops/events.jsonl").open("a") as f:  # an older bakehouse, still writing the old path
            f.write('{"kind": "note"}\n')
        p = self.bakehouse("status", "--repo", self.repo)
        self.assertEqual(p.returncode, locator.SETUP_NEEDED, p.stdout)
        self.assertIn("the frozen ledger ops/events.jsonl", p.stderr)
        p = self.note()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("the frozen ledger ops/events.jsonl", p.stderr)
        self.assertFalse(self.wfile("ops_ledger").exists())
        rc, out = self.close("the frozen ledger ops/events.jsonl")
        self.assertNotEqual(rc, 0, out)
        # a frozen file that was absent and now exists refuses too
        (self.records / "ops/events.jsonl").write_bytes(b"")
        self.rgit("checkout", "--", "ops/events.jsonl")
        self.assertEqual(self.bakehouse("status", "--repo", self.repo).returncode, 0)
        (self.records / "spend").mkdir()
        (self.records / "spend/ledger.jsonl").write_text("{}\n")
        p = self.bakehouse("status", "--repo", self.repo)
        self.assertIn("the frozen ledger spend/ledger.jsonl", p.stderr)
        self.assertIn("sha256 absent", p.stderr)

    def test_an_unknown_format_refuses_naming_the_version_it_needs(self):
        self.migrate("--writer", "alpha")
        (self.records / "FORMAT").write_text(json.dumps({"format": 3, "bakehouse": "0.9.0", "frozen": {}}))
        p = self.bakehouse("status", "--repo", self.repo)
        self.assertEqual(p.returncode, locator.SETUP_NEEDED, p.stdout)
        self.assertIn("records format 3", p.stderr)
        self.assertIn("it needs bakehouse 0.9.0", p.stderr)
        p = self.note()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("it needs bakehouse 0.9.0", p.stderr)
        p = self.bakehouse("setup", "migrate")
        self.assertEqual(p.returncode, 1)
        self.assertIn("it needs bakehouse 0.9.0", p.stdout)

    def test_a_write_with_no_writer_id_refuses_naming_setup_and_reads_still_work(self):
        self.flow()
        self.migrate("--writer", "alpha")
        self.rgit("config", "--unset", "bakehouse.writer")
        p = self.note()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("has no writer id (git config bakehouse.writer)", p.stderr)
        self.assertIn("run `bakehouse setup` in the main clone", p.stderr)
        rc, out = self.close("has no writer id")
        self.assertNotEqual(rc, 0, out)
        p = self.tool("decision.sh", "ask", "--repo", self.repo, "--category", "spend", "--question", "Run it?",
                      "--option", "yes", "--option", "no", "--recommendation", "yes", "--confidence", "0.9",
                      "--evidence-kind", "measured", "--evidence", "WIP.md")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("has no writer id", p.stderr)
        self.assertEqual(self.status()["workOrders"][0]["workOrder"], "W-1")  # reads need no writer id
        self.assertFalse(locator.writer_folder(self.records / "ops/events.jsonl").exists())


class ThreeFiles(Base):
    """F6: events in the legacy file and in two writer files (alpha, beta): every reader reads all three."""

    def setw(self, w):
        self.rgit("config", "bakehouse.writer", w)

    def ask(self, question):
        p = self.tool("decision.sh", "ask", "--repo", self.repo, "--category", "spend", "--question", question,
                      "--option", "yes", "--option", "no", "--recommendation", "yes", "--confidence", "0.9",
                      "--evidence-kind", "measured", "--evidence", "WIP.md")
        self.assertEqual(p.returncode, 0, p.stderr)

    def spend_open(self, wave):
        p = self.tool("spend.sh", "wave", "open", wave, "--ceiling", "1000", "--repo", self.repo)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_every_reader_reads_the_legacy_file_and_both_writer_files(self):
        old = {"OPS_REPO": str(self.repo), "OPS_TS": "2026-11-01T00:00:00+00:00"}
        self.assertEqual(self.tool("ops-event.sh", "note", "-", "backfilled: legacy", env=old).returncode, 0)
        self.spend_open("w0")
        self.ask("legacy")
        self.commit_records()
        self.migrate("--writer", "alpha")
        self.spend_open("w1")
        self.ask("alpha")
        self.assertIn("Result: CLOSED", work_order(self, self.repo, "W-1", "src/a.py", "a = 1\n",
                                                   "2026-11-10T09:30:00+00:00"))
        self.setw("beta")
        self.spend_open("w2")
        # closed_hashes: the same bytes as W-1's record, closed in alpha's file, refuse W-2's close in beta's
        out = work_order(self, self.repo, "W-2", "src/b.py", "b = 1\n", "2026-11-10T09:40:00+00:00", close_ok=False)
        self.assertIn("sha256 already recorded by the close of W-1", out)
        self.assertEqual([e["kind"] for e in self.rows(self.wfile("ops_ledger", "beta"))],
                         ["sealed"] * 4 + ["gate-refused"])  # W-2's seals and its refused close: beta's own file
        # decisions: the asks of the legacy file and of a writer file, both waiting
        self.ask("beta")
        self.assertEqual(sorted(d["question"] for d in self.status()["waitingDecisions"]), ["alpha", "beta", "legacy"])
        # spend: the waves of all three files
        p = self.tool("spend.sh", "status", "--all", "--json", "--repo", self.repo)
        self.assertEqual(sorted(w["wave"] for w in json.loads(p.stdout)["waves"]), ["w0", "w1", "w2"], p.stderr)
        # ops-gap: the latest event of every file
        rows = [e for f in locator.ledger_files(self.records, "ops_ledger") for e in self.rows(f)]
        p = self.tool("ops-gap.sh", env={"OPS_REPO": str(self.repo)})
        self.assertIn(f"last event {max(e['ts'] for e in rows)}", p.stdout, p.stderr)
        # ledger_refusals: a committed line of alpha's file edited refuses a close
        f = self.wfile("ops_ledger", "alpha")
        lines = f.read_text().splitlines()
        f.write_text("\n".join([lines[0].replace('"role": "engine"', '"role": "edited"')] + lines[1:]) + "\n")
        rc, out = self.close("the ledger ops/events/alpha.jsonl differs from its last commit")
        self.assertNotEqual(rc, 0, out)
        self.rgit("checkout", "--", "ops/events/alpha.jsonl")
        # status: a line of beta's file that is not a JSON object is BROKEN, naming beta's file
        with self.wfile("ops_ledger", "beta").open("a") as fh:
            fh.write("not json\n")
        p = self.bakehouse("status", "--repo", self.repo)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("ops/events/beta.jsonl is BROKEN", p.stderr)


def pm_commit(case, rec):
    """The PM's records commit, exactly as skills/pm/SKILL.md documents it, with <records> filled in."""
    pm = (RUNTIME.parent / "skills" / "pm" / "SKILL.md").read_text()
    cmd = re.search(r"`(git -C <records> add -A -- [^`]*)`", pm).group(1)
    p = subprocess.run(cmd.replace("<records>", shlex.quote(str(rec))).replace("<work order or 'records'>", "W"),
                       shell=True, capture_output=True, text=True, env=case.env)
    case.assertEqual(p.returncode, 0, p.stdout + p.stderr)


class PmCommit(Base):
    """F2: the PM's records commit leaves the ops writer files to the seal anchors, as it leaves the legacy file."""

    def test_a_seal_whose_anchor_was_undone_stays_uncommitted_after_the_pms_commit(self):
        self.migrate("--writer", "alpha")
        self.commit("W-1: prediction", {self.r("moves.prediction.json"): tsc.GOOD_P})
        self.assertEqual(self.seal("first", "moves.prediction.json"), 0)
        self.rgit("reset", "-q", "--mixed", "HEAD~1")  # what an ANCHOR REFUSED leaves: the line on disk, no commit
        p = self.tool("spend.sh", "wave", "open", "w1", "--ceiling", "10", "--repo", self.repo)  # another record
        self.assertEqual(p.returncode, 0, p.stderr)
        files = locator.ledger_files(self.records, "ops_ledger")
        why = "1 sealed event(s) of W-1 are not in a commit of the ledger"
        self.assertIn(why, " ".join(tsc.pg.sealed_state(files, "W-1")[2]))
        pm_commit(self, self.records)
        self.assertEqual(self.rgit("log", "-1", "--format=", "--name-only").split(), ["spend/ledger/alpha.jsonl"])
        self.assertIn(why, " ".join(tsc.pg.sealed_state(files, "W-1")[2]))


class CrossWriter(Base):
    """F1, F3, F4, F9, F10: what one writer's records do to another's, and a FORMAT gone from disk."""

    def setw(self, w):
        self.rgit("config", "bakehouse.writer", w)

    def test_a_writer_file_git_reads_as_a_copy_of_another_never_unseals(self):
        # F1: beta's file, first committed while alpha's is as short, is a copy to `git log --follow`; alpha's
        # Sealed-line trailers then show in beta's history, and must not read as removed from it
        self.migrate("--writer", "alpha")
        self.commit("W-1: prediction", {self.r("moves.prediction.json"): tsc.GOOD_P})
        self.assertEqual(self.seal("first", "moves.prediction.json"), 0)
        self.setw("beta")
        f2 = "runs/2026-11-10-W-2-step1-review/moves.prediction.json"
        self.commit("W-2: prediction", {f2: tsc.GOOD_P})
        p = self.gate("seal", "W-2", "first", str(self.repo / f2))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        files = locator.ledger_files(self.records, "ops_ledger")
        for wo in ("W-1", "W-2"):
            sealed, kept, refused = tsc.pg.sealed_state(files, wo)
            self.assertEqual((sealed, len(kept), refused), (True, 1, []), wo)

    def test_an_answer_in_one_writer_to_an_ask_in_another_counts(self):
        # F3: read order is by writer name, so alpha's answer comes before zed's ask
        self.migrate("--writer", "zed")
        p = self.tool("decision.sh", "ask", "--repo", self.repo, "--category", "spend", "--question", "Run it?",
                      "--option", "yes", "--option", "no", "--recommendation", "yes", "--confidence", "0.9",
                      "--evidence-kind", "measured", "--evidence", "WIP.md")
        self.assertEqual(p.returncode, 0, p.stderr)
        did = self.rows(self.wfile("decisions_ledger", "zed"))[0]["id"]
        self.setw("alpha")
        p = self.tool("decision.sh", "answer", "--repo", self.repo, did, "--choice", "yes", "--agreed", "yes")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([e["kind"] for e in self.rows(self.wfile("decisions_ledger", "alpha"))], ["answer"])
        self.assertEqual(self.status()["waitingDecisions"], [])
        decided, _ = load("decision_writer_test", "decision.py").decisions(locator.find(self.repo, env={}))
        self.assertEqual([a["choice"] for a in decided[did]["answers"]], ["yes"])

    def test_a_format_file_git_holds_and_the_disk_lacks_refuses(self):
        # F4: with FORMAT gone, the tools would read and write format 1 again: the frozen file, appended
        self.migrate("--writer", "alpha")
        self.flow()
        before = self.legacy()
        (self.records / "FORMAT").unlink()
        p = self.bakehouse("status", "--repo", self.repo)
        self.assertEqual(p.returncode, locator.SETUP_NEEDED, p.stdout)
        self.assertIn("checkout -- FORMAT", p.stderr)
        p = self.note()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("checkout -- FORMAT", p.stderr)
        self.assertEqual(self.legacy(), before)

    def test_a_writer_id_in_the_global_git_config_is_not_this_records_worktrees(self):
        # F9: the id names the records worktree, so only the repository's own config counts
        self.migrate("--writer", "alpha")
        self.rgit("config", "--unset", "bakehouse.writer")
        glob = self.repo.parent / "global.gitconfig"
        glob.write_text("[user]\n\tname = t\n\temail = t@t\n[bakehouse]\n\twriter = glob\n")
        p = self.tool("ops-event.sh", "note", "-", "x", env={"OPS_REPO": str(self.repo),
                                                              "GIT_CONFIG_GLOBAL": str(glob)})
        self.assertNotEqual(p.returncode, 0, p.stdout)
        self.assertIn("has no writer id", p.stderr)
        self.assertFalse(self.wfile("ops_ledger", "glob").exists())

    def test_status_names_the_writer_file_it_cannot_use(self):
        # F10: a line status cannot use is named by the file it is in, not by the legacy file
        self.migrate("--writer", "alpha")
        f = self.wfile("ops_ledger", "beta")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"ts": "2026-11-10T00:00:00+00:00", "kind": "work-order-closed", "task": "W-1",
                                 "note": 5}) + "\n")
        p = self.bakehouse("status", "--repo", self.repo)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(f"the ledger {f} cannot be read", p.stderr)
        g = self.wfile("decisions_ledger", "beta")
        g.parent.mkdir(parents=True, exist_ok=True)
        g.write_text(json.dumps({"kind": "ask"}) + "\n")
        f.unlink()
        p = self.bakehouse("status", "--repo", self.repo)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(f"the ledger {g} cannot be read", p.stderr)


class Lanes(Base):
    """Test 8: a lane clone, whose records pointer is the main clone's records worktree, writes to the main clone's
    writer file."""

    def test_a_lane_clone_writes_to_the_main_clones_writer_file(self):
        self.migrate("--writer", "alpha")
        lane = self.repo.parent / "lane"
        subprocess.run(["git", "clone", "-q", str(self.repo), str(lane)], check=True, env=self.env)
        p = self.bakehouse("setup", "--pointer", self.repo, cwd=lane)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.note("from the lane", repo=lane).returncode, 0)
        p = self.tool("lane-event.sh", "lane-opened", "lane=l1", env={"OPS_REPO": str(lane)})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([e["purpose"] for e in self.rows(self.wfile("ops_ledger"))], ["from the lane"])
        self.assertEqual([e["lane"] for e in self.rows(self.wfile("lanes_ledger"))], ["l1"])
        self.assertEqual(sorted(p.name for p in (self.records / "ops" / "events").glob("*.jsonl")), ["alpha.jsonl"])


class ReadOrder(Base):
    """The one read order (locator.ledger_files): the legacy file first, then each writer file sorted by name,
    those on disk and those HEAD holds; in format 1, the legacy file alone."""

    def test_legacy_first_then_writer_files_sorted_and_a_deleted_one_still_read_from_git(self):
        self.assertEqual(locator.ledger_files(self.records, "ops_ledger"), [self.records / "ops/events.jsonl"])
        self.migrate("--writer", "alpha")
        folder = self.records / "ops" / "events"
        folder.mkdir(parents=True)
        for n in ("zed.jsonl", "beta.jsonl", ".beta.jsonl.lock", "not a writer.jsonl"):
            (folder / n).write_text("")
        self.rgit("add", "ops/events/zed.jsonl")
        self.rgit("commit", "-q", "-m", "zed")
        (folder / "zed.jsonl").unlink()
        self.assertEqual(locator.ledger_files(self.records, "ops_ledger"),
                         [self.records / "ops/events.jsonl", folder / "beta.jsonl", folder / "zed.jsonl"])
        p = subprocess.run([sys.executable, str(RUNTIME / "locator.py"), "--write", "decisions_ledger",
                            str(self.repo)], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.stdout.strip(), str(self.records / "decisions/ledger/alpha.jsonl"), p.stderr)


if __name__ == "__main__":
    unittest.main()
