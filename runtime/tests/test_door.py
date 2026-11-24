"""Tests for `bakehouse door` (runtime/bakehouse_door.py) and the ops ledger's door event (runtime/ops-event.sh).
Standard library only. Each test builds a scratch project; nothing outside a temporary folder is read or written."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from test_status import BAKEHOUSE, IDENT, ROOT

TREE = {"owned": True, "tracker": {"id": "^A[0-9]+$"}, "records": {"codeRoots": ["src"]}, "runs": "tree",
        "reviews": "notes/done"}
RECORDS = {"owned": True, "tracker": {"id": "^BD[0-9]+$"}, "signal": {"codeRoots": ["packages"]}}


def finding(fid, severity, site):
    return f"## {fid} ({severity}): what\nWhat it does.\nSite: {site}\n"


class Door(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        (self.tmp / ".gitconfig").write_text("[maintenance]\n\tauto = false\n[gc]\n\tauto = 0\n")
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp),
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(self.tmp / ".gitconfig"),
                    "BAKEHOUSE_MACHINE_DIR": str(self.tmp / "machine"), **IDENT}
        self.repo = self.project("proj", TREE)
        self.commit("start", "src/a.py", "src/b.py", "src/many/one.py", "src/many/two.py", "src/many/three.py",
                    "src/many/four.py", days_ago=30)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def git(self, where, *args, env=None):
        return subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, check=True,
                              env=env or self.env).stdout

    def project(self, name, binding):
        repo = self.tmp / name
        (repo / ".team").mkdir(parents=True)
        (repo / ".team" / "binding.json").write_text(json.dumps(binding))
        self.git(repo, "init", "-q", "-b", "main")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "first")
        records = self.tmp / f"{name}.bakehouse-records"
        self.git(repo, "worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(records))
        self.git(repo, "config", "bakehouse.records", str(records))
        (records / "ops").mkdir()
        (records / "ops" / "events.jsonl").touch()
        return repo

    def records(self, repo=None):
        return self.tmp / f"{(repo or self.repo).name}.bakehouse-records"

    def commit(self, subject, *files, repo=None, days_ago=1):
        repo = repo or self.repo
        for f in files:
            (repo / f).parent.mkdir(parents=True, exist_ok=True)
            with (repo / f).open("a") as out:
                out.write(subject + "\n")
        stamp = f"@{int(time.time() - days_ago * 86400)} +0000"
        self.git(repo, "add", "--", *files)
        self.git(repo, "commit", "-q", "-m", subject,
                 env={**self.env, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})

    def day(self, days_ago):
        return time.strftime("%Y-%m-%d", time.localtime(time.time() - days_ago * 86400))

    def review(self, work_order, text, days_ago=1, file="findings.md", base=None):
        run = (base or self.repo / "notes" / "done") / f"{self.day(days_ago)}-{work_order}-review"
        run.mkdir(parents=True, exist_ok=True)
        (run / file).write_text("# Findings\n\n" + text)

    def trouble(self, work_order, days_ago=1):
        """Makes a work order troubled: a serious finding in a run folder dated days_ago, at a site outside every path
        the tests ask about, so no look-findings count moves."""  # 0.16.0: new helper, only troubled work orders count (owner's care-level rules)
        self.review(work_order, finding("F9", "P1", "elsewhere.py:1"), days_ago=days_ago, file="findings-elsewhere.md")

    def ask(self, task, days_ago=1, **fields):
        ts = datetime.fromtimestamp(time.time() - days_ago * 86400).astimezone().isoformat(timespec="seconds")
        ledger = self.records() / "decisions" / "ledger.jsonl"
        ledger.parent.mkdir(exist_ok=True)
        with ledger.open("a") as out:
            out.write(json.dumps({"kind": "ask", "id": f"d{time.time_ns()}", "ts": ts, "task": task,
                                  "question": "q", **fields}) + "\n")

    def door(self, *args, cwd=None):
        p = subprocess.run([str(BAKEHOUSE), "door", "--json", *args], cwd=cwd or self.repo, capture_output=True,
                           text=True, env=self.env)
        self.assertEqual((p.returncode, p.stderr), (0, ""), p.stdout)
        return json.loads(p.stdout)

    def route(self, *args, cwd=None):
        """(the route, the rules of its reasons)."""
        out = self.door(*args, cwd=cwd)
        return out["route"], [r["rule"] for r in out["reasons"]]

    # ---------- each look-around rule, alone, and just below it ----------

    def test_visits(self):
        self.commit("A1: x", "src/a.py")
        self.commit("A2: x", "src/a.py")
        self.assertEqual(self.route("src/a.py")[0], "fast", "2 untroubled visits count for nothing")  # 0.16.0: new case, untroubled work orders do not count (owner's care-level rules)
        self.trouble("A1")
        self.trouble("A2")
        self.assertEqual(self.route("src/a.py"), ("normal", ["fast-visits"]), "2 troubled visits: below lookVisits 3")  # 0.16.0: was 2 visits, now 2 troubled visits (owner's care-level rules)
        self.commit("A3: x", "src/a.py")
        self.trouble("A3")  # 0.16.0: was 3 visits gives look-visits, now 3 troubled visits (owner's care-level rules)
        self.assertEqual(self.route("src/a.py"), ("look-around", ["look-visits"]))
        self.commit("A4: x", "src/a.py", days_ago=20)
        self.trouble("A4")  # 0.16.0: new line (owner's yes 12:29): A4 is troubled, so only its 20-day-old commit leaves it out
        self.assertEqual(self.door("src/a.py")["reasons"][0]["workOrders"], ["A3", "A2", "A1"], "outside the window")

    def test_findings(self):
        self.commit("A1: x", "src/a.py")
        self.review("A1", finding("F1", "P1", "src/a.py:3"))
        self.review("A2", finding("F1", "P2", "src/a.py:3"))  # not serious
        self.assertEqual(self.route("src/a.py")[0], "normal", "one work order found one: below lookFindingOrders 2")  # 0.16.0: unchanged from 0.15 (fast-findings) (owner's care-level rules, 11:10: fast keeps no serious finding on these paths in the window)
        self.review("A2", finding("F2", "P0", "src/a.py:9"), days_ago=40)
        self.assertEqual(self.route("src/a.py")[0], "normal", "older than the window: not look-findings")  # 0.16.0: was look-findings from a 40-day-old finding, now not counted; normal from A1's finding (owner's care-level rules, 11:10: fast keeps no serious finding on these paths in the window)
        self.review("A2", finding("F2", "P0", "src/a.py:9"), days_ago=2)  # 0.16.0: was days_ago=40, now in the window (owner's care-level rules)
        self.assertEqual(self.route("src/a.py"), ("look-around", ["look-findings"]))
        self.assertEqual(self.route("src/b.py")[0], "fast", "findings on another file do not count")

    def test_fix_round_from_an_ask(self):
        self.commit("A1: x", "src/a.py")
        self.ask("A1", fixRound=1)
        self.assertEqual(self.route("src/a.py"), ("fast", ["fast-files", "fast-visits", "fast-limit"]),
                         "round 1: below lookFixRound 2, and not troubled")  # 0.16.0: was ("normal", ["fast-fix-round"]), now fast (owner's care-level rules)
        self.ask("A1", fixRound=2)
        out = self.door("src/a.py")
        self.assertEqual((out["route"], out["reasons"][0]["rule"], out["reasons"][0]["figure"]),
                         ("look-around", "look-fix-round", 2))

    def test_fix_round_from_a_run_folder(self):
        self.commit("A1: x", "src/a.py")
        self.review("A1", "", file="findings-fix1.md")
        self.assertEqual(self.route("src/a.py")[0], "fast")  # 0.16.0: was normal (fast-fix-round), now fast: round 1 is not troubled (owner's care-level rules)
        self.review("A1", "", file="findings-fix2.md")
        self.assertEqual(self.route("src/a.py"), ("look-around", ["look-fix-round"]))

    def test_altitude(self):
        self.commit("A1: x", "src/a.py")
        self.ask("A1", change=True, layer="in", owners=1)
        self.assertEqual(self.route("src/a.py")[0], "fast", "no trigger")
        for fields in ({"layer": "out", "owners": 1}, {"layer": "in", "owners": 2},
                       {"layer": "in", "owners": 1, "triggers": ["altitude", "altitude-cluster"]}):
            ledger = self.records() / "decisions" / "ledger.jsonl"
            ledger.unlink()
            self.ask("A1", change=True, **fields)
            self.assertEqual(self.route("src/a.py"), ("look-around", ["look-altitude"]), fields)
        self.ask("A9", layer="out", owners=3)
        self.assertEqual(self.door("src/a.py")["reasons"][0]["workOrders"], ["A1"], "A9 did not change these paths")

    # ---------- fast ----------

    def test_fast_and_one_visit_more(self):
        self.assertEqual(self.route("src/a.py", "src/b.py"), ("fast", ["fast-files", "fast-visits", "fast-limit"]))  # 0.16.0: was fast-files, fast-visits, fast-findings, fast-fix-round, now fast-files, fast-visits, fast-limit (owner's care-level rules)
        self.commit("A1: x", "src/a.py")
        self.trouble("A1")  # 0.16.0: was an untroubled visit, now a troubled one (owner's care-level rules)
        self.assertEqual(self.route("src/a.py", "src/b.py")[0], "fast", "one troubled visit: fastMaxVisits 1")
        self.commit("A2: x", "src/b.py")
        self.trouble("A2")  # 0.16.0: was 2 visits gives normal, now 2 troubled visits (owner's care-level rules)
        self.assertEqual(self.route("src/a.py", "src/b.py"), ("normal", ["fast-visits"]))
        self.assertEqual(self.route("src/many"), ("normal", ["fast-files"]), "4 files: above fastMaxFiles 3")
        self.assertEqual(self.route("src/new.py")[0], "fast", "a file git does not track counts as one")

    def test_a_recent_finding_is_not_fast(self):
        self.review("A5", finding("F1", "P1", "src/a.py:3"))
        self.assertEqual(self.route("src/a.py"), ("normal", ["fast-findings"]),
                         "a finding on these paths in the window, by a work order not here, stops fast")  # 0.16.0: unchanged from 0.15 (owner's care-level rules, 11:10: fast keeps no serious finding on these paths in the window)

    # ---------- settings, --task, --as-of ----------

    def test_a_project_override_moves_a_threshold(self):
        self.commit("A1: x", "src/a.py")
        self.commit("A2: x", "src/a.py")
        self.trouble("A1")  # 0.16.0: was 2 visits, now 2 troubled visits (owner's care-level rules)
        self.trouble("A2")
        self.assertEqual(self.route("src/a.py")[0], "normal")
        (self.repo / ".team" / "decision-config.json").write_text(json.dumps({"door": {"lookVisits": 2}}))
        out = self.door("src/a.py")
        self.assertEqual((out["route"], out["settings"]["lookVisits"], out["settings"]["days"]), ("look-around", 2, 14))
        (self.repo / ".team" / "decision-config.json").write_text(json.dumps({"door": {"days": 0}}))
        p = subprocess.run([str(BAKEHOUSE), "door", "src/a.py"], cwd=self.repo, capture_output=True, text=True,
                           env=self.env)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn('"door".days', p.stderr)

    def test_the_task_is_left_out_of_every_count(self):
        for wo in ("A1", "A2", "A3"):
            self.commit(f"{wo}: x", "src/a.py")
        self.review("A3", finding("F1", "P1", "src/a.py:3"), file="findings-fix3.md")
        self.review("A1", finding("F1", "P1", "src/a.py:3"))
        self.ask("A3", fixRound=3, layer="out", owners=2)
        self.trouble("A2")  # 0.16.0: was A2 an untroubled visit, now troubled, so 3 troubled visits (owner's care-level rules)
        self.assertEqual(self.route("src/a.py"), ("look-around", ["look-visits", "look-findings", "look-fix-round",
                                                                  "look-altitude"]))
        out = self.door("--task", "A3", "src/a.py")
        self.assertEqual((out["route"], out["task"], out["ground"]["not"]), ("normal", "A3", "A3"))
        self.assertNotIn("A3", json.dumps(out["reasons"]))

    def test_as_of_hides_what_came_after(self):
        self.commit("A1: x", "src/a.py", days_ago=6)
        self.commit("A2: x", "src/a.py", days_ago=6)
        self.commit("A3: x", "src/a.py", days_ago=2)
        self.review("A2", finding("F1", "P1", "src/a.py:3"), days_ago=6)
        self.review("A1", finding("F1", "P1", "src/a.py:3"), days_ago=1)
        self.ask("A1", days_ago=1, fixRound=2)
        self.trouble("A3", days_ago=2)  # 0.16.0: was A3 an untroubled visit, now troubled, so 3 troubled visits (owner's care-level rules)
        self.assertEqual(self.route("src/a.py"), ("look-around", ["look-visits", "look-findings", "look-fix-round"]))
        before = datetime.fromtimestamp(time.time() - 4 * 86400).astimezone().isoformat(timespec="seconds")
        out = self.door("--as-of", before, "src/a.py")
        self.assertEqual((out["route"], out["asOf"]), ("normal", before))  # 0.16.0: unchanged from 0.15: as of then only A2 is troubled (A1's ask and run folder came after), and A2's finding stops fast (owner's care-level rules, 11:10: fast keeps no serious finding on these paths in the window)
        self.assertEqual(out["ground"]["paths"][0]["workOrders"], ["A2", "A1"])

    # ---------- the output ----------

    def test_json_shape_and_the_text_names_each_setting(self):
        for wo in ("A1", "A2", "A3"):
            self.commit(f"{wo}: x", "src/a.py")
        self.ask("A2", fixRound=3)
        self.trouble("A1")  # 0.16.0: was 3 visits, now 3 troubled visits, so look-visits still shows (owner's care-level rules)
        self.trouble("A3")
        out = self.door("src/a.py")
        self.assertEqual(set(out), {"format", "team", "project", "route", "reasons", "settings", "asOf", "task",
                                    "ground"})
        self.assertEqual(set(out["settings"]), {"days", "lookVisits", "lookFindingOrders", "lookFixRound",
                                                "fastMaxFiles", "fastMaxVisits"})
        for r in out["reasons"]:
            self.assertEqual(set(r), {"rule", "setting", "threshold", "figure", "workOrders"})
        self.assertEqual(out["ground"]["paths"][0]["path"], "src/a.py")
        text = subprocess.run([str(BAKEHOUSE), "door", "src/a.py"], cwd=self.repo, capture_output=True, text=True,
                              env=self.env).stdout
        self.assertEqual(text, "door: look-around: 3 troubled work orders here in 14 days (A3, A2, A1) [lookVisits 3];"  # 0.16.0: was "3 other work orders", now "3 troubled work orders" (owner's care-level rules)
                               " A2 reached fix round 3 [lookFixRound 2]\n")

    def test_records_mode_reads_the_records_run_folders(self):
        other = self.project("web", RECORDS)
        self.commit("BD7 card", "packages/card.ts", repo=other)
        runs = self.records(other) / "runs"
        self.review("BD7", finding("F1", "P1", "packages/card.ts:9"), base=runs)
        self.assertEqual(self.route("packages/card.ts", cwd=other)[0], "normal")  # 0.16.0: unchanged from 0.15 (fast-findings) (owner's care-level rules, 11:10: fast keeps no serious finding on these paths in the window)
        self.review("BD8", finding("F1", "ANDON", "packages/card.ts:2"), base=runs)
        self.assertEqual(self.route("packages/card.ts", cwd=other), ("look-around", ["look-findings"]))

    # ---------- the door event ----------

    def test_ops_event_door(self):
        script, env = str(ROOT / "runtime" / "ops-event.sh"), {**self.env, "OPS_REPO": str(self.repo)}
        ok = subprocess.run([script, "door", "A1", "door", "-", "-", "-", "-", "-", "fast", "door: fast: 1 file"],
                            capture_output=True, text=True, env=env)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        lines = (self.records() / "ops" / "events.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1)
        ev = json.loads(lines[0])
        self.assertEqual((ev["kind"], ev["task"], ev["purpose"], ev["outcome"], ev["note"]),
                         ("door", "A1", "door", "fast", "door: fast: 1 file"))
        bad = subprocess.run([script, "door", "A1", "door", "-", "-", "-", "-", "-", "quick", "x"],
                             capture_output=True, text=True, env=env)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("fast, normal or look-around", bad.stderr)
        self.assertEqual(len((self.records() / "ops" / "events.jsonl").read_text().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
