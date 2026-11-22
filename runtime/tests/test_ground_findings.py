"""Tests for the review findings `bakehouse ground` shows (runtime/bakehouse_ground.py), and for the two lines the
reviewer's prompt asks of each finding. Standard library only. Each test builds scratch projects; nothing outside a
temporary folder is read or written."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from test_status import BAKEHOUSE, IDENT, ROOT

TREE = {"tracker": {"id": "^A[0-9]+[a-z]?$"}, "records": {"codeRoots": ["src"]}, "runs": "tree", "reviews": "notes/done"}
RECORDS = {"tracker": {"id": "^BD[0-9]+$"}, "signal": {"codeRoots": ["packages"]}}
UNREAD = "serious review findings, last 14 days, with no Site line that can be read: "


def finding(fid, severity, site=None, origin=None):
    lines = [f"## {fid} ({severity}): what", "What it does."]
    lines += [f"Site: {site}"] if site else []
    lines += [f"Origin: {origin}"] if origin else []
    return "\n".join([*lines, "If not fixed: it stays.", ""])


class GroundFindings(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        (self.tmp / ".gitconfig").write_text("[maintenance]\n\tauto = false\n[gc]\n\tauto = 0\n")
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp),
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(self.tmp / ".gitconfig"),
                    "BAKEHOUSE_MACHINE_DIR": str(self.tmp / "machine"), **IDENT}
        self.repo = self.project("proj", TREE)
        self.commit("A1: x", "src/a.py", "src/ab.py", "src/a/one.py", "src/ab/one.py", "tests/test_a.py",
                    "src/two words.py", "src/__init__.py")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def git(self, where, *args):
        return subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, check=True,
                              env=self.env).stdout

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

    def commit(self, subject, *files, repo=None):
        repo = repo or self.repo
        for f in files:
            (repo / f).parent.mkdir(parents=True, exist_ok=True)
            with (repo / f).open("a") as out:
                out.write(subject + "\n")
        self.git(repo, "add", "--", *files)
        self.git(repo, "commit", "-q", "-m", subject)

    def day(self, days_ago):
        return time.strftime("%Y-%m-%d", time.localtime(time.time() - days_ago * 86400))

    def review(self, work_order, *found, days_ago=1, file="findings.md", base=None, name="{date}-{id}-review"):
        """Write one findings file; the folder it is in."""
        run = (base or self.repo / "notes" / "done") / name.format(date=self.day(days_ago), id=work_order)
        (run / file).parent.mkdir(parents=True, exist_ok=True)
        (run / file).write_text("# Findings\n\n" + "\n".join(found))
        return run

    def run_ground(self, *args, cwd=None):
        p = subprocess.run([str(BAKEHOUSE), "ground", *args], cwd=cwd or self.repo, capture_output=True, text=True,
                           env=self.env)
        self.assertEqual((p.returncode, p.stderr), (0, ""), p.stdout)
        return p.stdout

    def as_json(self, *args, cwd=None):
        return json.loads(self.run_ground("--json", *args, cwd=cwd))

    def found(self, path, *args, cwd=None):
        """(count, earlier, foundBy) of one path; None when the path has no findings."""
        f = self.as_json(*args, path, cwd=cwd)["paths"][0].get("findings")
        return f and (f["count"], f["earlier"], f["foundBy"])

    def unread(self, *args):
        return self.as_json(*args, "src").get("findingsUnread", 0)

    def test_two_projects_one_in_tree_mode_and_one_in_records_mode(self):
        other = self.project("web", RECORDS)
        self.commit("BD7 card", "packages/card.ts", repo=other)
        records = self.tmp / "web.bakehouse-records" / "runs"
        self.review("A12", finding("F1", "P1", "src/a.py:4", "this"))
        self.review("BD31", finding("F1", "P1", "packages/card.ts:9", "this"),
                    finding("N2", "ANDON", "packages/card.ts:30", "earlier BD7"), base=records, name="{date}-x-{id}")
        self.review("A12", finding("F1", "P1", "packages/card.ts:9"), base=records, name="{date}-{id}")
        self.assertEqual(self.found("src/a.py"), (1, 0, ["A12"]))
        self.assertEqual(self.found("packages/card.ts", cwd=other), (2, 1, ["BD31"]), "A12 is not an id there")
        self.assertIn("packages/card.ts\n", self.run_ground("packages/card.ts", cwd=other))
        self.assertIn("  serious review findings: 2, 1 of them in earlier code (found by BD31)\n",
                      self.run_ground("packages/card.ts", cwd=other))

    def test_the_text(self):
        for n in range(1, 11):
            self.review(f"A{n}", finding("F1", "P1", "src/a.py:4", "this"), days_ago=40 - n)
        self.review("A11", finding("F1", "P1"))
        out = self.run_ground("src/a.py", "src/ab.py")
        one, other = out.split("src/ab.py\n")
        self.assertIn("\n  serious review findings: 10 (found by A10, A9, A8, A7, A6, A5, A4, A3 and 2 more)\n", one)
        self.assertEqual(out.splitlines()[-1], UNREAD + "1")
        self.assertNotIn("serious review findings:", other, "no line for a path that has none")

    def test_the_newest_review_is_first_and_a_days_ids_go_by_number(self):
        for wo, days_ago in (("A9", 5), ("A10", 5), ("A2", 1), ("A30", 8)):
            self.review(wo, finding("F1", "P1", "src/a.py:4"), days_ago=days_ago)
        self.assertEqual(self.found("src/a.py")[2], ["A2", "A10", "A9", "A30"])
        for n in range(40, 48):  # each has a newer and an older run folder: the newer counts
            self.review(f"A{n}", finding("F1", "P1", "src/ab.py:4"), days_ago=n - 39)
            self.review(f"A{n}", finding("F1", "P1", "src/ab.py:4"), days_ago=90 - n)
        self.assertEqual(self.found("src/ab.py"), (16, 0, [f"A{n}" for n in range(40, 48)]))

    def test_a_file_a_folder_and_a_name_that_begins_the_same(self):
        self.review("A3", finding("F1", "P1", "src/a/one.py:12"), finding("F2", "P1", "src/ab/one.py"))
        self.review("A4", finding("F1", "P1", "src/a.py"), file="second/round/findings.md")
        self.assertEqual(self.found("src/a"), (1, 0, ["A3"]), "src/ab is not under src/a, and src/a.py is not")
        self.assertEqual(self.found("src/ab"), (1, 0, ["A3"]), "a site with no line")
        self.assertEqual(self.found("src/a.py"), (1, 0, ["A4"]), "a nested findings file")
        self.assertEqual(self.found("src")[0], 3)
        self.assertEqual(self.found(".")[0], 3)
        self.assertIsNone(self.found("src/ab.py"))

    def test_the_severities_that_are_serious(self):
        self.review("A3", *[finding(f"F{n}", s, "src/a.py:4") for n, s in
                            enumerate(("ANDON", "P0", "P1", "P2", "P3"), 1)], finding("F9", "P2"),
                    "## F6 — P1: a dash\nSite: src/a.py:4\n", "### **F7** [P1] bold\nSite: src/a.py:4\n",
                    "## F8 (P2): the P1 path\nSite: src/a.py:4\n", "## F10: no severity\nSite: src/a.py:4\n",
                    "## P1-2: the severity in the id\nSite: src/a.py:4\n", "## P1-3 (P2): x\nSite: src/a.py:4\n",
                    "## P1 findings\nSite: src/a.py:4\n")
        self.assertEqual(self.found("src/a.py")[0], 6)
        self.assertEqual(self.unread(), 0, "a P2 finding with no site is not counted")

    def test_the_origins(self):
        self.review("A3", *[finding(f"F{n}", "P1", "src/a.py:4", o) for n, o in
                            enumerate(("earlier", "earlier A12", "this", "this, not checked against BASE", "elsewhere",
                                       None), 1)], finding("F7", "P1", "none", "earlier"))
        self.assertEqual(self.found("src/a.py"), (6, 2, ["A3"]))
        self.assertEqual(self.unread(), 0, "Site: none is readable")

    def test_the_marks_around_a_line(self):
        for n, line in enumerate(("- **Site:** `src/a.py:4`", "Site: src/a.py:4", "* **Site**: src/a.py:4", "**Site: src/a.py:4**",
                                  "`Site: src/a.py:4`", "  - `Site:` src/a.py:4 (the limit check)", "Site: `src/a.py:4`, the check",
                                  "Site: ./src/a.py:4-9", "Site: src/a.py, the check", "- __Site:__ src/a.py:4\r",
                                  "1. Site: src/a.py:4", "Site: src/a.py."), 1):
            self.review(f"A{n}", f"## F1 (P1): x\n{line}\n- **Origin:** `earlier` A2\n")
            self.assertEqual(self.found("src/a.py"), (n, n, [f"A{i}" for i in range(n, 0, -1)]), line)
        self.review("A20", finding("F1", "P1", "`src/two words.py:3`"), finding("F2", "P1", "src/__init__.py:1"),
                    finding("F3", "P1", "**None**."), finding("F4", "P1", "none (the release step)"),
                    finding("F5", "P1", ".team/binding.json:1"))
        self.assertEqual(self.found(".team")[0], 1)
        self.assertEqual(self.found("src/two words.py")[0], 1)
        self.assertEqual(self.found("src/__init__.py")[0], 1)
        self.assertEqual(self.unread(), 0)

    def test_a_line_belongs_to_its_finding(self):
        self.review("A3", "Site: src/ab.py:1\n\n## F1 (P1): x\n```\nSite: src/ab.py:4\n## F8 (P1): y\n```\n"
                    "~~~~text\n~~~\nSite: src/ab.py:4\n~~~~\n### More\nSite: src/a.py:4\nSite: src/ab.py:5\n"
                    "## Checked\nOrigin: earlier\nSite: src/ab.py:4\n# A3 review: 1 P1\nSite: src/ab.py:4\n")
        self.assertEqual(self.found("src/a.py"), (1, 0, ["A3"]), "a deeper heading is inside; the next one ends it")
        self.assertIsNone(self.found("src/ab.py"))
        self.assertEqual(self.unread(), 0, "a fenced heading and the file's own title are no findings")

    def test_the_unread_line(self):
        self.assertNotIn("no Site line", self.run_ground("src"))
        self.review("A3", finding("F1", "P1"), finding("F2", "P0", "a.py:4"), finding("F3", "ANDON", "src/never.py:4"),
                    finding("F4", "P1", "src/a.py:4"), finding("F5", "P3"), finding("F6", "P1", "/src/a.py"),
                    finding("F7", "P1", "../proj/src/a.py"), finding("F8", "P1", "src"))
        self.review("A4", finding("F1", "P1"), days_ago=20)
        self.assertEqual(self.unread(), 6)
        self.assertEqual(self.unread("--days", "30"), 7)
        self.assertEqual(self.run_ground("src").splitlines()[-1], UNREAD + "6")
        self.assertEqual(self.found("src")[0], 1)

    def test_git_had_the_path_in_any_way(self):
        self.commit("A2: x", "src/gone.py", "src/old.py")
        self.git(self.repo, "rm", "-q", "src/gone.py")
        self.git(self.repo, "mv", "src/old.py", "src/new.py")
        self.git(self.repo, "commit", "-q", "-m", "A2: tidy")
        self.git(self.repo, "switch", "-q", "-c", "lane")
        self.commit("A5: x", "src/lane.py")
        self.git(self.repo, "switch", "-q", "main")
        self.git(self.repo, "merge", "-q", "--no-ff", "--no-commit", "lane")
        (self.repo / "src" / "é merge.py").touch()
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-q", "-m", "merge")
        self.git(self.repo, "switch", "-q", "lane")
        for key, value in (("log.showRoot", "false"), ("diff.renames", "copies"), ("core.quotePath", "true")):
            self.git(self.repo, "config", key, value)
        for f in ("src/gone.py", "src/old.py", "src/new.py", "src/lane.py", "src/é merge.py", "tests/test_a.py"):
            self.review("A7", finding("F1", "P1", f + ":2"), file=f"{f}/findings.md")
            self.assertEqual(self.found(f), (1, 0, ["A7"]), f)
        self.assertEqual(self.unread(), 0)

    def test_a_recheck_is_one_finding_and_two_reviews_are_two(self):
        self.review("A3", finding("F1", "P1", "src/ab.py:4", "this"), finding("F2", "P1", "src/ab.py:4"),
                    finding("F3", "P1", "src/a.py:4"), finding("F4", "P2", "src/a.py:4"))
        for n in (10, 2):  # round 10 is after round 2
            self.review("A3", finding("F1", "P1", "src/a.py:4" if n == 10 else "src/ab.py:9", "earlier"),
                        finding("F2", "P2", "src/ab.py:4"), "## F3 (P1): still open\n", file=f"findings-fix{n}.md")
        self.assertEqual(self.found("src/a.py"), (2, 1, ["A3"]), "F3's site is the latest one given")
        self.assertIsNone(self.found("src/ab.py"), "F1 moved and F2 is a P2 now")
        self.assertEqual(self.unread(), 0)
        self.review("A4", finding("F1", "P1", "src/a.py:4"))
        self.assertEqual(self.found("src/a.py"), (3, 1, ["A4", "A3"]))
        self.review("A4", finding("F1", "P1", "src/a.py:4"), file="second/findings.md")
        self.assertEqual(self.found("src/a.py")[0], 4, "another review, in a folder of its own")

    def test_not_leaves_out_what_its_own_reviews_found(self):
        self.review("A3", finding("F1", "P1", "src/a.py:4"), finding("F2", "P1"))
        self.review("A4", finding("F1", "P1", "src/a.py:4", "earlier A3"), finding("F2", "P1"))
        self.assertEqual(self.found("src/a.py", "--not", "A3"), (1, 1, ["A4"]))
        self.assertEqual((self.unread(), self.unread("--not", "A3")), (2, 1))

    def test_a_path_outside_the_code_roots_shows_its_findings(self):
        before = self.run_ground("tests/test_a.py")
        self.assertEqual(before, "only the code roots count: src\ntests/test_a.py\n  outside the code roots: not counted\n")
        self.review("A3", finding("F1", "P1", "tests/test_a.py:4"))
        self.assertEqual(self.run_ground("tests/test_a.py"), """\
only the code roots count for work orders: src
tests/test_a.py
  outside the code roots: not counted for work orders
  serious review findings: 1 (found by A3)
""")
        self.assertEqual(self.as_json("tests/test_a.py")["paths"], [
            {"path": "tests/test_a.py", "inWorkingTree": True, "inCodeRoots": False,
             "findings": {"count": 1, "earlier": 0, "foundBy": ["A3"]}}])

    def test_a_path_with_no_other_history_shows_its_findings(self):
        self.git(self.repo, "switch", "-q", "-c", "lane")
        self.commit("A5: x", "src/lane.py")
        self.git(self.repo, "switch", "-q", "main")
        self.review("A6", finding("F1", "P1", "src/lane.py:1"))
        self.assertIn("src/lane.py\n  no history, and not in the working tree\n  serious review findings: 1 (found by A6)\n",
                      self.run_ground("--not", "A5", "src/lane.py"))

    def test_a_project_with_no_findings_is_as_before(self):
        self.review("A3", finding("F1", "P2", "src/a.py:4"))
        s = self.as_json("src/a.py")
        self.assertNotIn("findings", s["paths"][0])
        self.assertNotIn("findingsUnread", s)
        self.assertTrue(self.run_ground("src/a.py").startswith("only the code roots count: src\nsrc/a.py\n  work orders"))
        self.assertNotIn("review", self.run_ground("src", "src/a.py"))

    def test_what_is_not_read(self):
        good = finding("F1", "P1", "src/a.py:4")
        for name in ("A3-review", "review-{date}-{id}", "{date}-review", "2026-13-41-A3", "{date}9-A3"):
            self.review("A3", good, name=name)
        self.review("A3", good, file="pass1.md")
        self.review("A3", good, file="old-findings.md")
        out = self.review("A3", good, base=self.tmp / "away")
        (self.repo / "notes" / "done" / f"{self.day(1)}-A4").symlink_to(out)
        self.assertIsNone(self.found("src/a.py"))
        self.assertEqual(self.unread(), 0)

    def test_a_findings_file_that_cannot_be_read_never_fails(self):
        run = self.review("A3", finding("F1", "P1", "src/a.py:4"))
        (run / "findings-fix1.md").write_bytes(b"")
        (run / "findings-fix2.md").mkdir()
        (run / "findings-fix3.md").write_bytes(b"\xff\xfe\x00## F7 (P1): x\n## F2 (P1): caf\xe9\nSite: src/a.py:4\n"
                                               b"## F3 (P1): y\nSite: src/\xe9\x00.py:4\n## F4 (P1)\nSite:\n")
        (run / "findings-fix4.md").symlink_to(run / "gone.md")
        self.review("A6", file="findings.md", name="{date}-{id}")
        (self.repo / "notes" / "done" / f"{self.day(1)}-A6" / "findings.md").write_bytes(
            b"\xef\xbb\xbf## F1 (P1): after a byte-order mark\r\nSite: src/ab.py:4\r\n")
        self.assertEqual(self.found("src/ab.py"), (1, 0, ["A6"]))
        os.mkfifo(run / "findings-fix5.md")
        shut = self.review("A5", finding("F1", "P1", "src/a.py:4")) / "findings.md"
        shut.chmod(0)
        if os.geteuid() == 0:  # root reads it all the same
            shut.unlink()
        (self.repo / "notes" / "done" / "findings.md").write_text(finding("F1", "P1", "src/a.py:4"))
        self.assertEqual(self.found("src/a.py"), (2, 0, ["A3"]))
        self.assertEqual(self.unread(), 2)
        binding = self.repo / ".team" / "binding.json"
        for bad in ({"reviews": 7}, {"reviews": "../x"}, {"reviews": "a\0b"}):
            binding.write_text(json.dumps({**TREE, **bad}))
            self.assertIsNone(self.found("src/a.py"), bad)

    def test_nothing_is_written(self):
        self.review("A3", finding("F1", "P1", "src/a.py:4", "earlier"), finding("F2", "P1"))

        def snapshot():
            return {str(p): (p.lstat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
                    for p in self.tmp.rglob("*")}
        before = snapshot()
        self.assertIn("serious", self.run_ground("src", "src/a.py"))
        self.as_json("src")
        after = snapshot()
        self.assertEqual([p for p in {**before, **after} if before.get(p) != after.get(p)], [])

    def test_ids_of_digits_only(self):
        self.repo = self.project("digits", {**TREE, "tracker": {"id": "^[0-9]+$"}})
        self.commit("12 x", "src/a.py")
        self.review("12", finding("F1", "P1", "src/a.py:4"))
        self.review("", finding("F1", "P1", "src/a.py:4"), name="{date}-review")
        self.review("13", finding("F1", "P1", "src/a.py:4"), days_ago=0, name="{date}-fix-{id}-2")
        self.assertEqual(self.found("src/a.py"), (2, 0, ["13", "12"]))

    def test_the_prompt_and_the_two_briefs_ask_for_both_lines(self):
        prompt = (ROOT / "agents" / "reviewer.md").read_text()
        report = " ".join(prompt[prompt.index("## 5. Report"):].split())
        for text in ("Site: ", "Origin: ", "`Origin: this`", "`Origin: earlier`", "`Origin: elsewhere`", "`none`",
                     "`Origin: this, not checked against BASE`"):
            self.assertIn(text, report)
        for command in ("change", "refactor"):
            text = (ROOT / "commands" / f"{command}.md").read_text()
            brief = text[text.index("### Reviewer brief"):]
            self.assertRegex(brief[:brief.index("\n```\n")], r'Give each finding its [^.]*"Site:" and "Origin:" lines')

    def test_the_prompts_own_example_parses(self):
        prompt = (ROOT / "agents" / "reviewer.md").read_text()
        example = re.search(r"^```\n(## F1 \(P1\): .*?)^```$", prompt, re.M | re.S).group(1)
        site = re.search(r"^Site: ([^:\n]+):[0-9]+$", example, re.M).group(1)
        self.commit("A2: x", site)
        self.review("A3", example)
        self.assertEqual(self.found(site), (1, 1, ["A3"]))


if __name__ == "__main__":
    unittest.main()
