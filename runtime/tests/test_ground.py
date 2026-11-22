"""Tests for `bakehouse ground` (runtime/bakehouse_ground.py). Standard library only. Each test builds scratch
projects with a records worktree; nothing outside a temporary folder is read or written."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from test_status import BAKEHOUSE, IDENT, ROOT, chained


def event(kind, task):
    return {"ts": "2026-11-01T10:00:00+00:00", "role": "engine", "kind": kind, "task": task, "purpose": "p",
            "outcome": "done", "commit": "1111111"}


class Ground(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        # no background work by git after a commit: the likely cause of test_nothing_is_written's one failure on CI
        (self.tmp / ".gitconfig").write_text("[maintenance]\n\tauto = false\n[gc]\n\tauto = 0\n")
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp),
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(self.tmp / ".gitconfig"),
                    "BAKEHOUSE_MACHINE_DIR": str(self.tmp / "machine"), **IDENT}
        self.now = int(time.time())
        self.repo = self.project("proj", {"tracker": {"id": "^A[0-9]+[a-z]?$"}, "records": {"codeRoots": ["src"]}})

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def git(self, where, *args, days_ago=0):
        at = f"{self.now - int(days_ago * 86400)} +0000"
        return subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, check=True,
                              env={**self.env, "GIT_AUTHOR_DATE": at, "GIT_COMMITTER_DATE": at}).stdout

    def project(self, name, binding):
        repo = self.tmp / name
        (repo / ".team").mkdir(parents=True)
        (repo / ".team" / "binding.json").write_text(json.dumps(binding))
        self.git(repo, "init", "-q", "-b", "main")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "first", days_ago=400)
        records = self.tmp / f"{name}.bakehouse-records"
        self.git(repo, "worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(records))
        self.git(repo, "config", "bakehouse.records", str(records))
        self.ops = records / "ops" / "events.jsonl"
        self.ops.parent.mkdir()
        return repo

    def commit(self, subject, *files, days_ago=1, repo=None):
        repo = repo or self.repo
        for f in files:
            (repo / f).parent.mkdir(parents=True, exist_ok=True)
            with (repo / f).open("a") as out:
                out.write(subject + "\n")
        self.git(repo, "--literal-pathspecs", "add", "--", *files)
        self.git(repo, "commit", "-q", "-m", subject, days_ago=days_ago)

    def run_ground(self, *args, cwd=None):
        p = subprocess.run([str(BAKEHOUSE), "ground", *args], cwd=cwd or self.repo, capture_output=True, text=True,
                           env=self.env)
        return p.returncode, p.stdout, p.stderr

    def text(self, *args):
        rc, out, err = self.run_ground(*args)
        self.assertEqual((rc, err), (0, ""), out)
        return out

    def as_json(self, *args, cwd=None):
        rc, out, err = self.run_ground("--json", *args, cwd=cwd)
        self.assertEqual((rc, err), (0, ""), out)
        return json.loads(out)

    def path(self, path, *args):
        return self.as_json(*args, path)["paths"][0]

    def test_two_projects_with_different_ids_roots_and_languages(self):
        self.commit("A1: parse", "src/parse.py", days_ago=3)
        self.commit("A2b: parse again", "src/parse.py", "docs/x.md", days_ago=2)
        self.commit("BD7: another project's id", "src/parse.py", days_ago=2)
        other = self.project("web", {"tracker": {"id": "^BD[0-9]+$"}, "signal": {"codeRoots": ["packages/", "app"]}})
        self.commit("BD7 card", "packages/card.ts", "app/main.ts", days_ago=2, repo=other)
        self.commit("BD31 card", "packages/card.ts", repo=other)
        self.commit("A1: not an id here", "packages/card.ts", repo=other)
        one, two = self.as_json("src/parse.py"), self.as_json("packages/card.ts", "app", cwd=other)
        self.assertEqual((one["paths"][0]["workOrders"], one["noId"]), (["A2b", "A1"], 1))
        self.assertEqual([p["workOrders"] for p in two["paths"]], [["BD31", "BD7"], ["BD7"]])
        self.assertEqual(two["noId"], 1)
        self.assertEqual(self.path("docs/x.md"), {"path": "docs/x.md", "inWorkingTree": True, "inCodeRoots": False},
                         "docs is not a code root: not counted, which is not a 0")
        self.assertIn("docs/x.md\n  outside the code roots: not counted\n", self.text("docs/x.md"))
        self.assertEqual([self.path(p)["inCodeRoots"] for p in (".", "src", "src/parse.py")], [True] * 3)
        self.assertEqual(self.as_json("--repo", str(other), "app")["project"], str(other))

    def test_the_text(self):
        for n in range(1, 11):
            self.commit(f"A{n}: x", "src/a.py", days_ago=11 - n)
        self.commit("A3: y", "src/b.py", days_ago=8)
        self.commit("tidy", "src/b.py")
        self.ops.write_bytes(chained([event("sealed", "A3")]))
        first = time.strftime("%Y-%m-%d", time.gmtime(self.now - 10 * 86400))
        self.assertEqual(self.text("src/a.py", "src", "src/new.py"), f"""\
only the code roots count: src
src/a.py
  work orders, last 14 days: 10 (A10, A9, A8, A7, A6, A5, A4, A3 and 2 more), 1 open now; 1st of 2 files changed
  work orders, ever: 10, first commit {first}
src
  work orders, last 14 days: 10 (A10, A9, A8, A7, A6, A5, A4, A3 and 2 more), 1 open now
  most visits: src/a.py 10, src/b.py 1
  work orders, ever: 10, first commit {first}
src/new.py
  no history, and not in the working tree
commits with no work-order id, last 14 days: 1
""")
        self.assertNotIn("no work-order id", self.text("src/a.py"), "the tidy commit did not touch it")

    def test_an_old_commit_counts_in_ever_and_days_moves_the_window(self):
        self.commit("A1: x", "src/a.py", days_ago=300)
        self.commit("A2: x", "src/a.py", days_ago=20)
        self.commit("A3: x", "src/a.py", days_ago=13.9)
        p = self.path("src/a.py")
        self.assertEqual((p["workOrders"], p["everWorkOrders"]), (["A3"], 3))
        self.assertEqual(self.path("src/a.py", "--days", "21")["workOrders"], ["A3", "A2"])
        self.assertEqual(self.path("src/a.py", "--days", "5")["workOrders"], [])
        self.assertEqual(self.path("src/a.py", "--days", "5")["rank"], None, "no visit in the window, no rank")
        for bad in ("0", "-1", "x"):
            self.assertEqual(self.run_ground("--days", bad, "src/a.py")[0], 2)

    def test_a_copy_of_an_old_commit_is_not_a_visit_lately(self):
        self.commit("A1: x", "src/a.py", days_ago=40)
        self.git(self.repo, "switch", "-q", "-c", "lane", "main~")
        self.git(self.repo, "cherry-pick", "main", days_ago=1)
        self.assertEqual(self.git(self.repo, "log", "-1", "--since=2.days", "--format=%s").strip(), "A1: x")
        p = self.path("src/a.py")
        self.assertEqual((p["workOrders"], p["everWorkOrders"]), ([], 1), "a rebase or cherry-pick keeps the author date")

    def test_the_first_id_owns_a_commit_and_a_subject_with_none_is_counted_apart(self):
        self.commit("A1 after A2: x", "src/a.py")
        self.commit("no id", "src/a.py", "src/b.py")
        self.commit("no id, old", "src/a.py", days_ago=40)
        s = self.as_json("src/a.py", "src/b.py")
        self.assertEqual([p["workOrders"] for p in s["paths"]], [["A1"], []])
        self.assertEqual([p["everWorkOrders"] for p in s["paths"]], [1, 0])
        self.assertEqual(s["noId"], 1, "one commit on two of the paths is one commit")

    def test_three_commits_are_one_visit_and_not_leaves_a_work_order_out_everywhere(self):
        for _ in range(3):
            self.commit("A1: x", "src/a.py", days_ago=5)
        self.commit("A2: x", "src/b.py", days_ago=4)
        self.commit("A3: x", "src/b.py", days_ago=3)
        self.ops.write_bytes(chained([event("sealed", "A2")]))
        self.assertEqual(self.path("src/a.py")["workOrders"], ["A1"])
        self.assertEqual((self.path("src/a.py")["rank"], self.path("src/a.py")["of"]), (2, 2))
        p = self.path("src/b.py", "--not", "A2")
        self.assertEqual((p["workOrders"], p["open"], p["rank"], p["of"], p["everWorkOrders"]), (["A3"], 0, 1, 2, 1))
        self.assertEqual(self.path("src", "--not", "A2")["mostVisits"], [["src/a.py", 1], ["src/b.py", 1]])
        p = self.path("src/a.py", "--not", "A1")
        self.assertEqual((p["workOrders"], p["rank"], p["everWorkOrders"], p["firstCommit"]), ([], None, 0, None))
        self.assertIn("without A2", self.text("--not", "A2", "src"))
        self.assertIn("ever: 0, no other commits\n", self.text("--not", "A1", "src/a.py"))
        self.assertEqual(self.run_ground("--not", "a2", "src")[0], 2, "not an id: it would leave nothing out")

    def test_open_is_sealed_and_not_closed(self):
        for n in (1, 2, 3):
            self.commit(f"A{n}: x", "src/a.py")
        self.ops.write_bytes(chained([event("agent-run", "A1"), event("sealed", "A2"), event("sealed", "A3"),
                                      event("work-order-closed", "A3"), event("sealed", "A9")]))
        self.assertEqual(self.path("src/a.py")["open"], 1, "A1 has no seal, A3 is closed, A9 never came here")

    def test_a_broken_ledger_leaves_the_git_part_and_exits_1(self):
        self.commit("A1: x", "src/a.py")
        for text in ("not json\n", '{"kind": "sealed", "task": ["A1"]}\n'):  # a broken line; a line status cannot use
            self.ops.write_text(text)
            rc, out, err = self.run_ground("src/a.py")
            self.assertEqual(rc, 1, out + err)
            self.assertIn("work orders, last 14 days: 1 (A1), open not known; 1st of 1 files", out)
            self.assertIn("open work orders are not known", err)
            self.assertIn(str(self.ops), err)
        rc, out, _ = self.run_ground("--json", "src/a.py")
        self.assertEqual((rc, json.loads(out)["paths"][0]["open"]), (1, None))

    def test_folders_and_odd_file_names(self):
        self.commit("A1: x", "src/a/one.py", "src/a/two words.py")
        self.commit("A2: x", "src/ab/one.py", "src/a/st*r.py")
        self.commit("A3: x", "src/a/star.py", "src/a/é.py")
        self.assertEqual(set(self.path("src/a")["workOrders"]), {"A1", "A2", "A3"})
        self.assertEqual(self.path("src/a/")["path"], "src/a")
        self.assertEqual((self.path("src/a")["rank"], len(self.path("src/a")["mostVisits"])), (None, 3))
        self.assertEqual(self.path("src/ab")["workOrders"], ["A2"])
        self.commit("A6: x", "src/abc/one.py")
        self.assertNotIn("A6", self.path("src/ab")["workOrders"], "src/abc is not under src/ab")
        (self.repo / "link").symlink_to("src")
        self.assertEqual(self.path("link/ab")["path"], "src/ab")
        self.assertEqual(self.path("src/a/two words.py")["workOrders"], ["A1"])
        self.assertEqual(self.path("src/a/st*r.py")["workOrders"], ["A2"])
        self.assertEqual(self.path("src/a/é.py")["workOrders"], ["A3"])
        self.assertEqual(self.path(".")["workOrders"], self.path("src")["workOrders"])
        self.git(self.repo, "switch", "-q", "--detach")
        self.commit("A7: x", "src/detached.py")
        self.assertEqual(self.path("src/detached.py")["workOrders"], ["A7"], "HEAD counts when no branch holds it")
        odd = self.project("odd", {"tracker": {"id": "^A[0-9]+$"}, "signal": {"codeRoots": ["lib*"]}})
        self.commit("A4: x", "lib*/x.py", repo=odd)
        self.commit("A5: x", "liba/x.py", repo=odd)
        self.assertEqual(self.as_json(".", cwd=odd)["paths"][0]["workOrders"], ["A4"], "a root is no pattern")

    def test_other_branches_count_and_merge_commits_do_not(self):
        self.commit("A1: x", "src/a.py", days_ago=5)
        self.git(self.repo, "switch", "-q", "-c", "lane")
        self.commit("A2: x", "src/a.py", days_ago=4)
        self.git(self.repo, "switch", "-q", "main")
        self.assertEqual(self.path("src/a.py")["workOrders"], ["A2", "A1"], "only on another local branch")
        self.git(self.repo, "merge", "-q", "--no-ff", "-s", "ours", "-m", "A3: merge", "lane")
        self.git(self.repo, "branch", "-q", "-D", "lane")
        self.assertEqual(self.path("src/a.py")["workOrders"], ["A2", "A1"], "a merge that dropped A2 does not hide it")
        self.git(self.repo, "switch", "-q", "-c", "other", "HEAD~")
        self.git(self.repo, "merge", "-q", "--no-ff", "--no-commit", "main")
        (self.repo / "src" / "a.py").write_text("changed in the merge\n")
        self.git(self.repo, "commit", "-q", "-am", "A5: merge")
        self.assertEqual(len(self.git(self.repo, "log", "-1", "--format=%p").split()), 2)
        self.assertEqual(self.path("src/a.py")["workOrders"], ["A2", "A1"])

    def test_remote_branches_count_and_the_records_branches_do_not(self):
        repo = self.project("all", {"tracker": {"id": "^A[0-9]+$"}, "records": {"codeRoots": ["."]}})
        records = self.tmp / "all.bakehouse-records"
        self.commit("A1: a run folder", "note.md", repo=records)
        self.commit("A2: x", "a.py", repo=repo)
        for ref, at in (("origin/lane", "main"), ("origin/bakehouse-records", "bakehouse-records")):
            self.git(repo, "update-ref", f"refs/remotes/{ref}", at)
        self.git(repo, "reset", "-q", "--hard", "HEAD~")
        p = self.as_json(".", cwd=repo)["paths"][0]
        self.assertEqual((p["workOrders"], p["mostVisits"]), (["A2"], [["a.py", 1]]))

    def test_the_owners_git_config_changes_nothing(self):
        self.commit("A1: x", "src/a.py")
        self.git(self.repo, "mv", "src/a.py", "src/b.py")
        self.git(self.repo, "commit", "-q", "-m", "A2: rename")
        self.git(self.repo, "switch", "-q", "--orphan", "fresh")
        self.commit("A3: a root commit", "src/b.py")
        self.git(self.repo, "switch", "-q", "main")
        raw = self.git(self.repo, "cat-file", "commit", "fresh").replace(
            "\n\n", "\ngpgsig -----BEGIN SSH SIGNATURE-----\n AAAA\n -----END SSH SIGNATURE-----\n\n")
        (self.tmp / "signed").write_text(raw.replace("A3:", "A4:"))
        signed = self.git(self.repo, "hash-object", "-t", "commit", "-w", str(self.tmp / "signed")).strip()
        self.git(self.repo, "branch", "-q", "signed", signed)
        before = self.as_json("src/a.py", "src/b.py")
        self.assertEqual([p["workOrders"] for p in before["paths"]], [["A2", "A1"], ["A2", "A3", "A4"]],
                         "a renamed file's history starts at the rename")
        for key, value in (("log.showRoot", "false"), ("log.showSignature", "true"),
                           ("diff.renames", "copies"), ("core.quotePath", "true"), ("diff.noprefix", "true")):
            self.git(self.repo, "config", key, value)
        self.assertEqual(self.as_json("src/a.py", "src/b.py"), before)

    def test_ties_share_a_rank(self):
        self.commit("A1: x", "src/a.py", "src/b.py", "src/c.py")
        self.commit("A2: x", "src/a.py", "src/b.py")
        self.assertEqual([(p["rank"], p["of"]) for p in self.as_json("src/a.py", "src/b.py", "src/c.py")["paths"]],
                         [(1, 3), (1, 3), (3, 3)])
        self.assertIn("; 3rd of 3 files", self.text("src/c.py"))

    def test_a_path_with_no_history(self):
        (self.repo / "src").mkdir()
        (self.repo / "src" / "new.py").write_text("")
        self.assertIn("src/new.py\n  work orders, last 14 days: 0\n  work orders, ever: 0, no commits\n",
                      self.text("src/new.py"))
        self.assertIn("src/gone.py\n  no history, and not in the working tree\n", self.text("src/gone.py"))
        s = self.as_json("src/new.py", "src/gone.py")
        self.assertEqual([p["inWorkingTree"] for p in s["paths"]], [True, False])
        self.commit("A1: x", "src/new.py")
        (self.repo / "src" / "new.py").unlink()
        self.assertEqual(self.path("src/new.py")["workOrders"], ["A1"], "a deleted file keeps its history")

    def test_bad_usage(self):
        for args in ((), ("../x",), ("/etc/hosts",), ("src/../../x",), ("--nope", "src"),
                     ("--repo", str(self.tmp), "src")):
            rc, out, err = self.run_ground(*args)
            self.assertEqual((rc, out), (2, ""), args)
            self.assertNotIn("Traceback", err)
        self.assertIn("outside the repository", self.run_ground("../x")[2])
        self.assertEqual(self.path(str(self.repo / "src"))["path"], "src", "an absolute path inside it is inside")
        (self.tmp / "way").symlink_to(self.repo)
        self.assertEqual(self.path(str(self.tmp / "way" / "src"))["path"], "src", "by way of a symlink too")

    def test_no_team_setup_needed_and_a_binding_without_the_two_facts(self):
        plain = self.tmp / "plain"
        self.git(self.tmp, "init", "-q", str(plain))
        self.assertEqual(self.run_ground("src", cwd=plain)[0], 3)
        rc, out, _ = self.run_ground("--json", "src", cwd=plain)
        self.assertEqual((rc, json.loads(out)), (3, {"format": 1, "team": False}))
        roots, form = {"codeRoots": ["src"]}, {"id": "^A[0-9]+$"}
        for n, (binding, field) in enumerate((
                ([], "tracker.id"), ({"records": roots}, "tracker.id"),
                ({"tracker": {"id": "("}, "records": roots}, "tracker.id"),
                ({"tracker": form}, "codeRoots"), ({"tracker": form, "records": {"codeRoots": "src"}}, "codeRoots"))):
            rc, out, err = self.run_ground("src", cwd=self.project(f"p{n}", binding))
            self.assertEqual((rc, out, len(err.splitlines())), (2, "", 1), err)
            self.assertIn(field, err)
        self.git(self.repo, "config", "--unset", "bakehouse.records")
        self.assertEqual(self.run_ground("src")[0], 4)

    def test_nothing_is_written(self):
        self.commit("A1: x", "src/a.py")

        def snapshot():
            return {str(p): (p.lstat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
                    for p in self.tmp.rglob("*")}
        before = snapshot()
        self.text("src", "src/a.py", "src/new.py")
        self.as_json("src")
        after = snapshot()
        self.assertEqual(after, before, [p for p in {**before, **after} if before.get(p) != after.get(p)])

    def test_the_command_is_listed(self):
        usage = subprocess.run([str(BAKEHOUSE)], capture_output=True, text=True, env=self.env).stderr
        self.assertIn("|ground|", usage)
        self.assertIn("bakehouse ground", BAKEHOUSE.read_text())

    def test_the_change_job_reads_it(self):
        change = (ROOT / "commands" / "change.md").read_text()
        step = change[change.index("### 4. The work order"):change.index("### 5. The branch")]
        self.assertIn("`bakehouse ground --not {id}", step)
        self.assertIn("`ground:` line", change[:change.index("### 4. The work order")])
        brief = change[change.index("### Engineer brief"):change.index("### Reviewer brief")]
        self.assertIn("[tracker: the item's ground: line]", brief)


if __name__ == "__main__":
    unittest.main()
