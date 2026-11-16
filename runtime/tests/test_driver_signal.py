"""Tests for runtime/driver-signal.py. Standard library only. Each test builds scratch run folders."""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "driver-signal.py"
_spec = importlib.util.spec_from_file_location("driver_signal", SCRIPT)
ds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ds)

AS_OF = date(2026, 6, 30)


def body(seed, n=12):
    """A driver whose tokens are its own: two different seeds share no 6-token shingle."""
    return "".join(f"value_{seed}_{i} = compute_{seed}(item_{seed}_{i}, limit_{seed}={i})\n" for i in range(n))


def day(days_back):
    return (AS_OF - timedelta(days=days_back)).isoformat()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.repo, records = t / "repo", t / "repo.team-records"
        (self.repo / "runs").mkdir(parents=True)
        (self.repo / ".team").mkdir()
        # a scratch project: .team/binding.json and a records worktree named by git config bakehouse.records
        for args in (["init", "-q"], ["worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(records)],
                     ["config", "bakehouse.records", str(records)]):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        self.binding = self.repo / ".team" / "binding.json"
        self.register = records / "registers" / "drivers.json"
        self.register.parent.mkdir()
        self.rules()

    def tearDown(self):
        self.tmp.cleanup()

    def rules(self, **drivers):
        b = {"owned": True, "reviews": "runs/"}
        if drivers:
            b["drivers"] = drivers
        self.binding.write_text(json.dumps(b))

    def put(self, run, path, text):
        p = self.repo / "runs" / run / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text) if isinstance(text, bytes) else p.write_text(text)

    def run_signal(self, *args, as_of=AS_OF.isoformat()):
        p = subprocess.run(["python3", str(SCRIPT), "--repo", str(self.repo),
                            "--as-of", as_of, *args], capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def reg(self):
        return json.loads(self.register.read_text())

    def ok(self, *args, **kw):
        code, out = self.run_signal(*args, **kw)
        self.assertEqual(code, 0, out)
        return self.reg()


class TestContentClusters(Fixture):
    def test_a_copy_in_three_runs_is_one_cluster_and_raised(self):
        for i, (d, n) in enumerate(zip((20, 10, 1), ("alpha", "beta", "gamma"))):
            self.put(f"{day(d)}-job{i}", f"drivers/{n}.py", body("shared"))  # three names: no name family
        self.put(f"{day(1)}-job2", "drivers/other.py", body("other"))
        r = self.ok()
        c = r["computed"]
        self.assertEqual((c["clusters"], c["filesInClusters"], c["linesInClusters"], c["pairs"]), (1, 3, 36, 3))
        fid = f"content:{day(20)}-job0/drivers/alpha.py"
        f = r["families"][fid]
        self.assertEqual((f["kind"], f["runCount"], f["runsInWindow"], f["fileCount"], f["lines"]),
                         ("content", 3, 3, 3, 36))
        self.assertEqual((f["firstSeen"], f["lastSeen"], f["state"]), (day(20), day(1), "build-candidate"))
        self.assertEqual(f["flag"], {"owner": "pm", "next": ds.NEXT})
        self.assertEqual((f["names"], f["coherence"]), (["alpha.py", "beta.py", "gamma.py"], 1.0))

    def test_copies_inside_one_run_never_pair(self):
        self.put(f"{day(5)}-job", "drivers/a.py", body("x"))
        self.put(f"{day(5)}-job", "drivers-fix1/b.py", body("x"))
        c = self.ok()["computed"]
        self.assertEqual((c["pairs"], c["clusters"]), (0, 0))

    def test_the_threshold_is_inclusive_and_configurable(self):
        self.put(f"{day(5)}-a", "drivers/a.py", body("x"))
        self.put(f"{day(4)}-b", "drivers/b.py", body("x"))
        self.rules(threshold=1.0)
        self.assertEqual(self.ok()["computed"]["clusters"], 1)  # identical files: Jaccard is exactly 1.0
        half = body("x", 6) + body("y", 6)
        self.put(f"{day(4)}-b", "drivers/b.py", half)
        self.rules()
        self.assertEqual(self.ok()["computed"]["clusters"], 0)
        self.rules(threshold=0.3)
        self.assertEqual(self.ok()["computed"]["clusters"], 1)
        self.assertEqual(self.ok("--threshold", "0.5")["computed"]["clusters"], 0)

    def test_a_small_file_takes_no_part_in_clusters(self):
        for i in range(5):  # coherence 0 (no shingles): raised at the name-only count
            self.put(f"{day(i + 1)}-r{i}", "drivers/tiny.py", "x = 1\n")
        r = self.ok()
        self.assertEqual((r["computed"]["drivers"], r["computed"]["clusters"]), (0, 0))
        self.assertEqual(r["families"]["name:tiny"]["state"], "build-candidate")  # names still count

    def test_boilerplate_in_more_than_60_files_does_not_make_copies(self):
        self.rules(threshold=0.15)  # the short header alone would pair two files at 0.15
        header = body("header", 2)
        for i in range(62):
            self.put(f"{day(2)}-r{i:02d}", "drivers/job.py", header + body(f"own{i}", 3))
        self.assertEqual(self.ok()["computed"]["clusters"], 0)


class TestNameFamilies(Fixture):
    def test_the_name_rule(self):
        cases = {"make_prediction_fix2.py": "make_prediction", "d8_mutants.py": "mutants", "cut3.py": "cut",
                 "survey19c.py": "survey", "r2_s3_compare.py": "compare", "fix.py": "fix",
                 "measure_p45.py": "measure", "Render_New.sh": "render", "v2.py": "v2"}
        for name, key in cases.items():
            self.assertEqual(ds.name_key(name, []), key, name)

    def test_a_family_under_the_count_is_watched_not_raised(self):
        self.put(f"{day(3)}-a", "drivers/check.py", body("a"))
        self.put(f"{day(2)}-b", "drivers/check2.py", body("b"))
        r = self.ok()
        f = r["families"]["name:check"]
        self.assertEqual((f["runCount"], f["state"], f["flag"]), (2, "watch", None))
        self.assertEqual(r["computed"]["buildCandidates"], 0)

    def test_a_name_in_one_run_is_not_a_family(self):
        self.put(f"{day(3)}-a", "drivers/solo.py", body("a"))
        self.put(f"{day(3)}-a", "drivers/solo2.py", body("b"))
        self.assertNotIn("name:solo", self.ok()["families"])

    def test_declared_families_join_names_the_rule_keeps_apart(self):
        for i, n in enumerate(("mut.py", "my_mutants.py", "attack2.py", "mutate3.py", "x_mutant.py")):
            self.put(f"{day(i + 1)}-r{i}", f"drivers/{n}", body(f"s{i}"))
        r = self.ok()
        self.assertEqual(r["computed"]["buildCandidates"], 0)
        self.rules(families=[{"id": "harness", "names": ["mut*", "*mutant*", "ATTACK*"]}])
        f = self.ok()["families"]["name:harness"]
        self.assertEqual((f["runCount"], f["fileCount"], f["coherence"], f["state"]), (5, 5, 0.0, "build-candidate"))


class TestWindowAndRules(Fixture):
    def three(self, days_back):  # one body: a coherent family, raised at minRuns
        for i, d in enumerate(days_back):
            self.put(f"{day(d)}-r{i}", "drivers/probe.py", body("same"))

    def test_the_window_boundary(self):
        self.three((30, 20, 10))
        f = self.ok()["families"]["name:probe"]
        self.assertEqual((f["runsInWindow"], f["state"]), (3, "build-candidate"))

    def test_one_day_outside_the_window(self):
        self.three((31, 20, 10))
        f = self.ok()["families"]["name:probe"]
        self.assertEqual((f["runCount"], f["runsInWindow"], f["state"]), (3, 2, "watch"))

    def test_a_run_after_the_as_of_date_is_outside(self):
        self.three((20, 10, -1))
        self.assertEqual(self.ok()["families"]["name:probe"]["runsInWindow"], 2)

    def test_an_undated_run_counts_runs_not_the_window(self):
        self.three((20, 10))
        self.put("scratch-run", "drivers/probe.py", body("u"))
        f = self.ok()["families"]["name:probe"]
        self.assertEqual((f["runCount"], f["runsInWindow"], f["state"]), (3, 2, "watch"))

    def test_the_binding_overrides_window_count_and_owner(self):
        self.three((40, 10))
        self.assertEqual(self.ok()["families"]["name:probe"]["state"], "watch")
        self.rules(minRuns=2, windowDays=45, owner="owner")
        f = self.ok()["families"]["name:probe"]
        self.assertEqual((f["runsInWindow"], f["state"], f["flag"]["owner"]), (2, "build-candidate", "owner"))
        self.assertEqual(self.ok("--window-days", "30")["families"]["name:probe"]["state"], "watch")
        self.rules()
        self.assertEqual(self.ok("--min-runs", "1")["families"]["name:probe"]["state"], "build-candidate")

    def test_nested_drivers_folders_are_read_only_when_asked(self):
        for i in range(3):
            self.put(f"{day(i + 1)}-r{i}", "review/drivers/deep.py", body("same"))
        self.assertNotIn("name:deep", self.ok()["families"])
        self.rules(nested=True)
        self.assertEqual(self.ok()["families"]["name:deep"]["state"], "build-candidate")

    def test_bad_rules_refuse(self):
        for bad in ({"minRuns": 0}, {"threshold": 0}, {"nested": "yes"}, {"window": 30},
                    {"families": [{"id": "x"}]}):
            self.rules(**bad)
            code, out = self.run_signal()
            self.assertNotEqual(code, 0, bad)
            self.assertIn("driver-signal:", out)
        self.binding.write_text(json.dumps({"owned": True, "runs": "tree"}))  # item 6: no `runs` is records mode
        self.assertIn("reviews", self.run_signal()[1])


class TestRegister(Fixture):
    def test_a_decision_is_never_overwritten_and_regrowth_raises_again(self):
        for i, d in enumerate((25, 20, 15)):
            self.put(f"{day(d)}-r{i}", "drivers/probe.py", body("same"))
        r = self.ok()
        decision = {"decision": "build", "date": day(10), "note": "a shared tool"}
        r["families"]["name:probe"]["decision"] = decision
        self.register.write_text(json.dumps(r))
        f = self.ok()["families"]["name:probe"]
        self.assertEqual((f["decision"], f["state"], f["flag"]), (decision, "decided", None))
        self.put(f"{day(10)}-r3", "drivers/probe.py", body("same"))  # on the decision day: not after it
        self.put(f"{day(5)}-r4", "drivers/probe.py", body("same"))
        self.put(f"{day(2)}-r5", "drivers/probe.py", body("same"))
        self.assertEqual(self.ok()["families"]["name:probe"]["state"], "decided")
        self.put(f"{day(1)}-r6", "drivers/probe.py", body("same"))
        f = self.ok()["families"]["name:probe"]
        self.assertEqual((f["decision"], f["state"]), (decision, "build-candidate-again"))
        self.assertEqual(f["flag"]["owner"], "pm")

    def test_a_team_decision_names_its_backlog_item_and_is_decided_like_the_others(self):
        for i, d in enumerate((25, 20, 15)):
            self.put(f"{day(d)}-r{i}", "drivers/probe.py", body("same"))
        r = self.ok()
        self.assertIn("make it a team tool", r["families"]["name:probe"]["flag"]["next"])
        decision = {"decision": "team", "date": day(10), "item": "bakehouse backlog: a shared probe"}
        r["families"]["name:probe"]["decision"] = decision
        self.register.write_text(json.dumps(r))
        f = self.ok()["families"]["name:probe"]
        self.assertEqual((f["decision"], f["state"], f["flag"]), (decision, "decided", None))
        for i, d in enumerate((5, 3, 1)):
            self.put(f"{day(d)}-s{i}", "drivers/probe.py", body("same"))
        f = self.ok()["families"]["name:probe"]
        self.assertEqual((f["decision"], f["state"]), (decision, "build-candidate-again"))

    def test_a_team_decision_without_a_backlog_item_refuses(self):
        for item in ({}, {"item": ""}, {"item": "  "}, {"item": 7}, {"item": None}):
            with self.subTest(item=item):
                self.register.write_text(json.dumps({"families": {"name:x": {"decision": {
                    "decision": "team", "date": day(10), **item}}}}))
                code, out = self.run_signal()
                self.assertNotEqual(code, 0)
                self.assertIn("families.name:x.decision", out)
                self.assertIn("item", out)

    def test_a_decided_family_that_is_gone_is_kept(self):
        self.register.write_text(json.dumps({"families": {"name:old": {"kind": "name", "decision": {
            "decision": "accept", "date": day(40)}}}}))
        f = self.ok()["families"]["name:old"]
        self.assertEqual((f["present"], f["state"]), (False, "decided"))

    def test_a_bad_decision_refuses(self):
        self.register.write_text(json.dumps({"families": {"name:x": {"decision": {"decision": "maybe"}}}}))
        code, out = self.run_signal()
        self.assertNotEqual(code, 0)
        self.assertIn("families.name:x.decision", out)

    def test_no_write_leaves_the_register_alone(self):
        self.put(f"{day(1)}-a", "drivers/x.py", body("a"))
        code, out = self.run_signal("--no-write")
        self.assertEqual(code, 0, out)
        self.assertFalse(self.register.exists())
        self.assertIn("driver files in 1 run folders", out)

    def test_binding_and_a_full_register_path_need_no_team(self):
        self.put(f"{day(1)}-a", "drivers/x.py", body("a"))
        other = Path(self.tmp.name) / "other.json"
        self.binding.rename(other)
        self.assertNotEqual(self.run_signal()[0], 0)
        self.ok("--binding", str(other), "--register", str(self.register))



class TestPointDontCarry(Fixture):
    """A recorded decision never moves to another family by itself; the families holding its files point at it."""

    def decide(self, path, decision):
        r = self.reg()
        for fid, v in r["families"].items():
            if path in v.get("files", []):
                v["decision"] = decision
        self.register.write_text(json.dumps(r))

    def holder(self, path):
        held = [k for k, v in self.reg()["families"].items() if path in v.get("files", []) and v.get("present", True)]
        self.assertEqual(len(held), 1, held)
        return held[0], self.reg()["families"][held[0]]

    def gone(self, files, decision=None):
        return {"kind": "content", "decision": decision or {"decision": "accept", "date": day(10)}, "files": files}

    def two_clusters(self):
        for i, n in enumerate(("a1", "a2")):
            self.put(f"{day(20 - i)}-x{i}", f"drivers/{n}.py", body("x"))
        for i, n in enumerate(("b1", "b2")):
            self.put(f"{day(15 - i)}-y{i}", f"drivers/{n}.py", body("y"))
        return ([f"{day(20)}-x0/drivers/a1.py", f"{day(19)}-x1/drivers/a2.py"],
                [f"{day(15)}-y0/drivers/b1.py", f"{day(14)}-y1/drivers/b2.py"])

    def test_an_older_copy_rekeys_a_decided_cluster_and_the_new_id_points_at_the_decision(self):
        for i, (d, n) in enumerate(zip((20, 18, 16), ("alpha", "beta", "gamma"))):
            self.put(f"{day(d)}-r{i}", f"drivers/{n}.py", body("same"))
        self.ok()
        old = f"content:{day(20)}-r0/drivers/alpha.py"
        decision = {"decision": "accept", "date": day(14)}
        self.decide(f"{day(20)}-r0/drivers/alpha.py", decision)
        self.put(f"{day(25)}-older", "drivers/delta.py", body("same"))  # sorts first: the id changes
        for _ in range(2):  # stable on the next run
            r = self.ok()
            fid, f = self.holder(f"{day(20)}-r0/drivers/alpha.py")
            self.assertEqual(fid, f"content:{day(25)}-older/drivers/delta.py")
            self.assertEqual((f["state"], f["decision"], f["flag"]["next"]), ("build-candidate", None, ds.NEXT_PRIOR))
            self.assertEqual(f["priorDecision"], [{"id": old, "decision": "accept", "date": day(14), "holds": 3,
                                                   "of": 3}])
            g = r["families"][old]
            self.assertEqual((g["present"], g["state"], g["decision"], len(g["files"])), (False, "decided", decision, 3))
            self.assertNotIn("carriedFrom", json.dumps(r))
            self.assertNotIn("mergedInto", json.dumps(r))
        self.decide(f"{day(25)}-older/drivers/delta.py", {"decision": "accept", "date": day(1)})  # the owner confirms
        f = self.ok()["families"][fid]
        self.assertEqual((f["state"], f["decision"]["date"], f["flag"]), ("decided", day(1), None))

    def test_a_raised_family_the_owner_never_decided_stays_raised(self):
        for i, n in enumerate(("probe", "helper", "widget")):
            self.put(f"{day(29 - i)}-a{i}", f"drivers/{n}.py", body("tool"))
        for i in range(5):
            self.put(f"{day(25 - i)}-u{i}", "drivers/probe.py", body(f"job{i}"))
        r = self.ok()
        cid = f"content:{day(29)}-a0/drivers/probe.py"
        self.assertEqual(r["families"][cid]["state"], "build-candidate")
        self.decide(f"{day(28)}-a1/drivers/helper.py", {"decision": "accept", "date": day(15)})
        self.put(f"{day(5)}-g", "drivers/probe.py", body("tool"))  # the cluster now folds into name:probe
        r = self.ok()
        f = r["families"]["name:probe"]
        self.assertEqual((f["state"], f["decision"]), ("build-candidate", None))
        self.assertEqual([(n["id"], n["holds"], n["of"]) for n in f["priorDecision"]], [(cid, 3, 3)])
        self.assertEqual((r["families"][cid]["present"], r["computed"]["buildCandidates"]), (False, 1))

    def test_every_family_holding_any_of_the_files_is_noted_with_its_share(self):
        xs, ys = self.two_clusters()
        self.register.write_text(json.dumps({"families": {"content:gone": self.gone(xs + ys[:1])}}))
        r = self.ok()
        for path, holds in ((xs[0], 2), (ys[0], 1)):
            fid, f = self.holder(path)
            self.assertEqual((f["state"], f["decision"]), ("watch", None))
            self.assertEqual(f["priorDecision"], [{"id": "content:gone", "decision": "accept", "date": day(10),
                                                   "holds": holds, "of": 3}])
        g = r["families"]["content:gone"]
        self.assertEqual((g["present"], g["files"]), (False, sorted(xs + ys[:1])))

    def test_a_family_with_its_own_decision_keeps_it_and_still_points_at_the_other(self):
        xs, ys = self.two_clusters()
        self.ok()
        own = {"decision": "build", "date": day(12)}
        self.decide(xs[0], own)
        r = self.reg()
        r["families"]["content:gone"] = self.gone(xs)
        self.register.write_text(json.dumps(r))
        r = self.ok()
        fid, f = self.holder(xs[0])
        self.assertEqual((f["decision"], [n["id"] for n in f["priorDecision"]]), (own, ["content:gone"]))
        g = r["families"]["content:gone"]
        self.assertEqual((g["present"], g["state"], g["files"]), (False, "decided", xs))

    def test_a_decided_family_with_a_note_is_not_counted_as_not_new(self):
        xs, ys = self.two_clusters()
        self.ok()
        self.decide(xs[0], {"decision": "build", "date": day(12)})
        r = self.reg()
        r["families"]["content:gone"] = self.gone(xs)
        self.register.write_text(json.dumps(r))
        code, out = self.run_signal()
        self.assertEqual(code, 0, out)
        self.assertTrue(self.holder(xs[0])[1]["priorDecision"])
        self.assertIn("0 family(ies) raised, 0 new and 0 not new", out)
        self.assertNotIn("NOT new", out)
        self.assertEqual(self.reg()["computed"]["buildCandidatesNoted"], 0)

    def test_a_decided_family_whose_files_are_in_no_listed_family_keeps_them(self):
        self.put(f"{day(1)}-a", "drivers/x.py", body("a"))
        self.register.write_text(json.dumps({"families": {"content:gone": self.gone(["old/drivers/x.py"])}}))
        r = self.ok()
        self.assertEqual((r["families"]["content:gone"]["present"], r["families"]["content:gone"]["files"]),
                         (False, ["old/drivers/x.py"]))
        self.assertFalse(any(v.get("priorDecision") for v in r["families"].values()))

    def test_the_summary_marks_a_raised_family_that_holds_a_prior_decision(self):
        for i in range(3):
            self.put(f"{day(20 - i)}-r{i}", f"drivers/c{i}.py", body("same"))
        files = [f"{day(20)}-r0/drivers/c0.py"]
        self.register.write_text(json.dumps({"families": {"content:gone": self.gone(files)}}))
        code, out = self.run_signal()
        self.assertEqual(code, 0, out)
        row = next(line for line in out.splitlines() if line.startswith("| `content:"))
        self.assertIn(f"build-candidate; prior decision on `content:gone`: accept {day(10)}, holds 1 of 1 files", row)
        self.assertIn("1 of them are NOT new", out)
        self.rules()
        self.register.unlink()
        self.assertNotIn("prior decision", self.run_signal()[1])


class TestNotNew(Fixture):
    """A raised family that points at a prior decision is not new: counted, worded and named apart."""

    FRESH = f"content:{day(10)}-s0/drivers/p0.py"
    NOTED = f"content:{day(20)}-r0/drivers/c0.py"

    def scene(self, *args, fresh=True):
        if fresh:
            for i in range(4):
                self.put(f"{day(10 - i)}-s{i}", f"drivers/p{i}.py", body("fresh"))
        for i in range(3):
            self.put(f"{day(20 - i)}-r{i}", f"drivers/c{i}.py", body("same"))
        gone = {"kind": "content", "decision": {"decision": "accept", "date": day(25)},
                "files": [f"{day(20)}-r0/drivers/c0.py"]}
        self.register.write_text(json.dumps({"families": {"content:gone": gone}}))
        code, out = self.run_signal(*args)
        self.assertEqual(code, 0, out)
        return out, self.reg()

    def test_a_noted_family_gets_its_not_new_line_and_not_the_generic_step(self):
        out, _ = self.scene(fresh=False)
        self.assertNotIn(ds.NEXT, out)
        self.assertIn(f"1 of them are NOT new (`{self.NOTED}`)", out)
        shutil.rmtree(self.repo / "runs")
        out, _ = self.scene()
        self.assertEqual(out.count(f"Each new family goes to the pm: {ds.NEXT}."), 1)
        self.assertIn(f"1 of them are NOT new (`{self.NOTED}`)", out)

    def test_the_counts_separate_new_from_noted_families(self):
        out, r = self.scene()
        c = r["computed"]
        self.assertEqual((c["buildCandidates"], c["buildCandidatesNew"], c["buildCandidatesNoted"]), (2, 1, 1))
        self.assertEqual([f for f in r["families"] if r["families"][f].get("priorDecision")], [self.NOTED])
        self.assertIn("2 family(ies) raised, 1 new and 1 not new", out)

    def test_a_noted_family_beyond_the_top_rows_is_still_named(self):
        out, _ = self.scene("--top", "1")
        rows = [line for line in out.splitlines() if line.startswith("| `")]
        self.assertEqual([row.split("`")[1] for row in rows], [self.FRESH])
        self.assertIn(f"1 of them are NOT new (`{self.NOTED}`)", out)


class TestBoilerplate(Fixture):
    def runs_with_header(self, n_runs, with_header, **rules):
        """A short header (15 shingles) in many run folders; at threshold 0.15 it alone would pair two files."""
        self.rules(threshold=0.15, **rules)
        header = body("header", 2)
        for i in range(n_runs):
            self.put(f"{day(2)}-r{i:02d}", f"drivers/job{chr(97 + i % 26)}{i}.py",
                     (header if i < with_header else "") + body(f"own{i}", 3))

    def test_a_shingle_in_more_than_the_share_of_run_folders_is_boilerplate(self):
        self.runs_with_header(16, 12)  # 12 of 16 is exactly 0.75: not more
        self.assertEqual(self.ok()["computed"]["filesInClusters"], 12)
        self.runs_with_header(16, 13)
        self.assertEqual(self.ok()["computed"]["clusters"], 0)
        self.runs_with_header(16, 13, boilerplate=0.9)
        self.assertEqual(self.ok()["computed"]["filesInClusters"], 13)

    def test_a_copy_in_every_run_of_a_small_repo_is_not_boilerplate(self):
        self.runs_with_header(10, 10)  # in 10 run folders: not more than 10
        self.assertEqual(self.ok()["computed"]["filesInClusters"], 10)
        self.runs_with_header(11, 11)
        self.assertEqual(self.ok()["computed"]["clusters"], 0)

    def test_a_tool_copied_into_nearly_every_run_folder_stays_visible(self):
        for runs, copies in ((12, 10), (12, 11), (12, 12), (20, 15), (20, 16)):
            with self.subTest(runs=runs, copies=copies):
                shutil.rmtree(self.repo / "runs")
                for i in range(runs):
                    run = f"{day(i + 1)}-r{i:02d}"
                    self.put(run, f"drivers/own{chr(97 + i)}{chr(97 + i)}.py", body(f"own{i}", 40))
                    if i < copies:
                        self.put(run, f"drivers/name{chr(97 + i)}{chr(98 + i)}.py", body("tool", 40))
                c = self.ok()["computed"]
                self.assertEqual((c["clusters"], c["filesInClusters"], c["buildCandidates"]), (1, copies, 1))

    def test_a_copied_block_of_exactly_the_smallest_size_pairs_its_files(self):
        self.rules(threshold=0.3)
        for size, clusters in ((ds.MIN_SHINGLES, 1), (ds.MIN_SHINGLES - 1, 0)):
            with self.subTest(size=size):
                shutil.rmtree(self.repo / "runs")
                block = " ".join(f"blk_{i}" for i in range(size + ds.SHINGLE - 1)) + "\n"
                for i in range(12):  # in 12 run folders: over the floor of 10, so boilerplate
                    self.put(f"{day(i + 1)}-r{i:02d}", f"drivers/job{chr(97 + i)}{chr(97 + i)}.py",
                             block + body(f"own{i}", 3))
                c = self.ok()["computed"]
                self.assertEqual((c["clusters"], c["filesInClusters"]), (clusters, 12 * clusters))


class TestRegisterRule(Fixture):
    def test_a_cluster_folds_into_the_name_family_holding_most_of_its_files(self):
        for i in range(3):
            self.put(f"{day(10 - i)}-r{i}", "drivers/tool.py", body("same"))
        self.put(f"{day(5)}-r3", "drivers/renamed.py", body("same"))
        r = self.ok()
        f = r["families"]["name:tool"]
        cid = f"content:{day(10)}-r0/drivers/tool.py"
        self.assertEqual((f["fileCount"], f["runCount"], f["clusters"], f["state"]), (4, 4, [cid], "build-candidate"))
        self.assertEqual([k for k in r["families"] if k.startswith("content:")], [])
        self.assertEqual(r["computed"]["buildCandidates"], 1)

    def test_a_family_holding_exactly_half_still_covers_the_cluster(self):
        for i in range(2):
            self.put(f"{day(10 - i)}-r{i}", "drivers/tool.py", body("same"))
        self.put(f"{day(7)}-r2", "drivers/alpha.py", body("same"))
        self.put(f"{day(6)}-r3", "drivers/beta.py", body("same"))
        r = self.ok()
        self.assertEqual(r["families"]["name:tool"]["fileCount"], 4)
        self.assertEqual([k for k in r["families"] if k.startswith("content:")], [])

    def test_a_tie_between_two_name_families_goes_to_the_first_key(self):
        for i, n in enumerate(("bb", "bb", "aa", "aa")):
            self.put(f"{day(10 - i)}-r{i}", f"drivers/{n}.py", body("same"))
        f = self.ok()["families"]
        cid = f"content:{day(10)}-r0/drivers/bb.py"
        self.assertEqual((f["name:aa"]["clusters"], f["name:aa"]["fileCount"]), ([cid], 4))
        self.assertEqual((f["name:bb"]["clusters"], f["name:bb"]["fileCount"]), ([], 2))

    def test_a_cluster_no_family_covers_is_listed_on_its_own(self):
        self.put(f"{day(10)}-r0", "drivers/tool.py", body("same"))
        self.put(f"{day(9)}-r1", "drivers/tool.py", body("same"))
        for i, n in enumerate(("alpha", "beta", "gamma")):
            self.put(f"{day(8 - i)}-s{i}", f"drivers/{n}.py", body("same"))
        r = self.ok()
        self.assertIn(f"content:{day(10)}-r0/drivers/tool.py", r["families"])
        self.assertEqual(r["families"]["name:tool"]["clusters"], [])

    def test_coherence_is_the_upper_median_of_cross_run_best_matches(self):
        for i, seed in enumerate(("a", "a", "b", "c")):
            self.put(f"{day(10 - i)}-r{i}", "drivers/probe.py", body(seed))
        self.assertEqual(self.ok()["families"]["name:probe"]["coherence"], 1.0)  # [0, 0, 1, 1] -> 1

    def test_a_copy_inside_one_run_does_not_make_a_family_coherent(self):
        self.put(f"{day(10)}-r0", "drivers/probe.py", body("a"))
        self.put(f"{day(10)}-r0", "drivers-fix1/probe.py", body("a"))
        self.put(f"{day(9)}-r1", "drivers/probe.py", body("b"))
        self.assertEqual(self.ok()["families"]["name:probe"]["coherence"], 0.0)

    def test_a_name_only_family_is_raised_at_its_own_count(self):
        for i in range(4):
            self.put(f"{day(10 - i)}-r{i}", "drivers/probe.py", body(f"s{i}"))
        f = self.ok()["families"]["name:probe"]
        self.assertEqual((f["coherence"], f["runsInWindow"], f["state"]), (0.0, 4, "watch"))
        self.put(f"{day(5)}-r4", "drivers/probe.py", body("s4"))
        self.assertEqual(self.ok()["families"]["name:probe"]["state"], "build-candidate")
        self.rules(minRunsNameOnly=6)
        self.assertEqual(self.ok()["families"]["name:probe"]["state"], "watch")
        self.rules(coherent=0.0)  # every family coherent: raised at minRuns
        self.assertEqual(self.ok()["families"]["name:probe"]["state"], "build-candidate")

    def test_the_coherent_cut_is_inclusive(self):
        for i in range(3):
            self.put(f"{day(10 - i)}-r{i}", "drivers/probe.py", body("same"))
        self.rules(coherent=1.0)
        f = self.ok()["families"]["name:probe"]
        self.assertEqual((f["coherence"], f["state"]), (1.0, "build-candidate"))

    def test_regrowth_counts_only_runs_inside_the_window(self):
        for i, d in enumerate((70, 65, 62)):
            self.put(f"{day(d)}-r{i}", "drivers/probe.py", body("same"))
        self.register.write_text(json.dumps({"families": {"name:probe": {"kind": "name", "decision": {
            "decision": "build", "date": day(60)}}}}))
        for i, d in enumerate((50, 45, 40)):  # after the decision, before the window
            self.put(f"{day(d)}-g{i}", "drivers/probe.py", body("same"))
        self.assertEqual(self.ok()["families"]["name:probe"]["state"], "decided")
        self.put(f"{day(-2)}-future", "drivers/probe.py", body("same"))  # after the as-of date
        self.put(f"{day(20)}-in0", "drivers/probe.py", body("same"))
        self.put(f"{day(10)}-in1", "drivers/probe.py", body("same"))
        self.assertEqual(self.ok()["families"]["name:probe"]["state"], "decided")


class TestReading(Fixture):
    def test_only_py_and_sh_files_are_drivers(self):
        for i in range(3):
            for ext in (".txt", ".json", ".pyc", ".md"):
                self.put(f"{day(i + 1)}-r{i}", f"drivers/note{ext}", body("same"))
        c = self.ok()["computed"]
        self.assertEqual((c["driverFiles"], c["buildCandidates"]), (0, 0))

    def strings(self, suffix, fill):
        return "".join(f'value_{i} = compute_{i}("{fill}{i} {fill}", limit={i})  # {fill} {i} {fill}\n'
                       for i in range(12)).replace("value_", "export value_" if suffix == ".sh" else "value_")

    def test_string_contents_do_not_count(self):
        for suffix in (".py", ".sh"):
            self.put(f"{day(5)}-a", f"drivers/a{suffix}", self.strings(suffix, "first words here"))
            self.put(f"{day(4)}-b", f"drivers/b{suffix}", self.strings(suffix, "other text there"))
        c = self.ok()["computed"]
        self.assertEqual((c["clusters"], c["filesInClusters"]), (2, 4))

    def test_an_f_string_counts_as_one_empty_string(self):
        def text(names):
            return "".join(f'value_{i} = render_{i}(f"' + " + ".join("{" + f"{n}_{i}" + "}" for n in names)
                           + f' text", limit_{i})\n' for i in range(12))
        self.put(f"{day(5)}-a", "drivers/a.py", text(("alpha", "beta", "gamma", "delta", "omega", "sigma")))
        self.put(f"{day(4)}-b", "drivers/b.py", text(("one", "two", "three", "four", "five", "six")))
        c = self.ok()["computed"]
        self.assertEqual((c["clusters"], c["tokenizeFailed"]), (1, 0))

    def test_a_hash_inside_a_python_string_is_not_a_comment(self):
        line = 'value_{i} = compute_{i}("{c}", item_{i}, size_{i}, limit_{i}, scale_{i})\n'
        self.put(f"{day(5)}-a", "drivers/a.py", "".join(line.format(i=i, c="#") for i in range(12)))
        self.put(f"{day(4)}-b", "drivers/b.py", "".join(line.format(i=i, c="x") for i in range(12)))
        c = self.ok()["computed"]
        self.assertEqual((c["clusters"], c["tokenizeFailed"]), (1, 0))

    def test_every_kind_of_tokenizer_refusal_falls_back_and_is_counted(self):
        for tag, tail in (("an IndentationError", "if x:\n        y\n    z\n"),
                          ("a NUL byte after a dedent", "def f(a):\n    return a\nx = 1\x00\n")):  # 3.12: SystemError
            with self.subTest(tag):
                shutil.rmtree(self.repo / "runs")
                self.put(f"{day(5)}-a", "drivers/a.py", body("same") + tail)
                self.put(f"{day(4)}-b", "drivers/b.py", body("same"))
                c = self.ok()["computed"]
                self.assertEqual((c["tokenizeFailed"], c["clusters"], c["driverFiles"]), (1, 1, 2))

    def test_a_python_file_the_tokenizer_refuses_falls_back_and_is_counted(self):
        self.put(f"{day(5)}-a", "drivers/a.py", body("same") + '"""never closed\n')
        self.put(f"{day(4)}-b", "drivers/b.py", body("same"))
        self.put(f"{day(3)}-c", "drivers/c.py", bytes(range(256)) * 8)
        c = self.ok()["computed"]
        self.assertEqual((c["tokenizeFailed"], c["clusters"], c["driverFiles"]), (2, 1, 3))

    def test_symbolic_links_are_skipped_and_reported(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "copy.py").write_text(body("same"))
        self.put(f"{day(5)}-a", "drivers/tool.py", body("same"))
        self.put(f"{day(4)}-b", "drivers/tool.py", body("same"))
        (self.repo / "runs" / f"{day(3)}-c").mkdir()
        (self.repo / "runs" / f"{day(3)}-c" / "drivers").symlink_to(outside)
        d = self.repo / "runs" / f"{day(2)}-d" / "drivers"
        d.mkdir(parents=True)
        (d / "link.py").symlink_to(outside / "copy.py")
        (d / "loop").symlink_to("..")
        (self.repo / "runs" / f"{day(1)}-e").symlink_to(self.repo / "runs" / f"{day(5)}-a")
        (self.repo / "runs" / f"{day(2)}-d" / "deep").symlink_to(outside)
        r = self.ok()
        self.assertEqual((r["computed"]["driverFiles"], r["computed"]["skipped"]["symlinks"]), (2, 4))
        self.assertEqual(r["skipped"]["symlinks"], sorted([f"{day(3)}-c/drivers", f"{day(2)}-d/drivers/link.py",
                                                           f"{day(2)}-d/drivers/loop", f"{day(1)}-e"]))
        self.rules(nested=True)  # a linked folder met while searching deeper is reported too
        self.assertEqual(self.ok()["computed"]["skipped"]["symlinks"], 5)
        self.assertEqual(self.ok()["families"]["name:tool"]["runCount"], 2)
        out = self.run_signal()[1]
        self.assertIn("5 symlinks.", out)
        self.assertNotIn("tooLarge: over", out)  # the size cap is named only when a file was too large

    def test_a_file_over_the_size_cap_is_skipped_and_reported(self):
        self.put(f"{day(5)}-a", "drivers/big.py", "x = 1\n" * 200)
        self.put(f"{day(4)}-b", "drivers/small.py", "x = 1\n")
        r = self.ok()
        self.assertEqual((r["computed"]["driverFiles"], r["computed"]["skipped"]["tooLarge"]), (2, 0))
        self.rules(maxBytes=1000)
        code, out = self.run_signal()
        self.assertEqual(code, 0, out)
        self.assertEqual((self.reg()["computed"]["driverFiles"], self.reg()["skipped"]["tooLarge"]),
                         (1, [f"{day(5)}-a/drivers/big.py"]))
        self.assertIn("1 tooLarge (tooLarge: over 1000 bytes).", out)

    @unittest.skipIf(os.geteuid() == 0, "root reads every file")
    def test_an_unreadable_file_or_folder_is_reported_not_fatal(self):
        for i in range(3):
            self.put(f"{day(i + 1)}-r{i}", "drivers/tool.py", body("same"))
        locked_file = self.repo / "runs" / f"{day(1)}-r0" / "drivers" / "tool.py"
        locked_dir = self.repo / "runs" / f"{day(2)}-r1" / "drivers"
        os.chmod(locked_file, 0)
        os.chmod(locked_dir, 0)
        try:
            code, out = self.run_signal()
        finally:
            os.chmod(locked_file, 0o644)
            os.chmod(locked_dir, 0o755)
        self.assertEqual(code, 0, out)
        r = self.reg()
        self.assertEqual(r["skipped"]["unreadable"], sorted([f"{day(1)}-r0/drivers/tool.py", f"{day(2)}-r1/drivers"]))
        self.assertEqual(r["computed"]["driverFiles"], 1)
        self.assertIn("2 unreadable", out)

    def test_top_limits_the_printed_rows(self):
        for n in ("aa", "bb", "cc"):
            for i in range(3):
                self.put(f"{day(i + 1)}-r{i}", f"drivers/{n}.py", body(n))
        code, out = self.run_signal("--top", "1")
        self.assertEqual(code, 0, out)
        self.assertEqual(sum(1 for line in out.splitlines() if line.startswith("| `name:")), 1)
        self.assertIn("2 more in the register", out)

    def test_strict_rule_values_refuse(self):
        for bad in ({"threshold": True}, {"boilerplate": 0}, {"boilerplate": False}, {"coherent": 1.5},
                    {"windowDays": 1000000000}, {"windowDays": 3651}, {"windowDays": 1e9}, {"minRunsNameOnly": 0},
                    {"maxBytes": True}, {"minRuns": 4, "minRunsNameOnly": 3}):
            self.rules(**bad)
            code, out = self.run_signal()
            self.assertNotEqual(code, 0, bad)
            self.assertTrue(out.startswith("driver-signal: drivers."), (bad, out))
        self.rules(minRuns=4, minRunsNameOnly=3)
        self.assertIn("drivers.minRunsNameOnly must not be under minRuns, got 3 under 4", self.run_signal()[1])
        self.rules(minRuns=4, minRunsNameOnly=4)
        self.assertEqual(self.run_signal()[0], 0)
        self.rules(windowDays=3650)
        self.assertEqual(self.run_signal()[0], 0)
        for raw in ("[]", "false", "null", '"x"'):
            self.binding.write_text('{"reviews": "runs/", "drivers": ' + raw + "}")
            code, out = self.run_signal()
            self.assertNotEqual(code, 0, raw)
            self.assertTrue(out.startswith("driver-signal: drivers must be an object"), (raw, out))


if __name__ == "__main__":
    unittest.main()
