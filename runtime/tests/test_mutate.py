"""Tests for runtime/mutate.py, the mutation runner. Standard library only:
python3 -m unittest discover runtime/tests"""

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
SCRIPT = RUNTIME / "mutate.py"
_spec = importlib.util.spec_from_file_location("mutate", SCRIPT)
mutate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mutate)
_rc = importlib.util.spec_from_file_location("records_check", RUNTIME / "records-check.py")
records_check = importlib.util.module_from_spec(_rc)
_rc.loader.exec_module(records_check)

LIB = "def add(a, b):\n    return a + b\n\n\ndef double(x):\n    return x * 2\n"
TEST = (
    "import unittest\n\nimport lib\n\n\nclass T(unittest.TestCase):\n"
    "    def test_add(self):\n        self.assertEqual(lib.add(2, 3), 5)\n"
)
TEST_ENV = (
    "import os\nimport unittest\n\nimport envlib\n\n\nclass T(unittest.TestCase):\n"
    "    def test_x(self):\n        self.assertEqual(envlib.X, 1)\n\n"
    "    def test_env(self):\n        self.assertEqual(os.environ.get('T9_MARK'), 'set')\n"
    "        self.assertTrue(os.path.isdir(os.path.join(os.environ['T9_ROOT'], '.git')))\n"
    "        self.assertFalse(os.path.isdir('.git'))\n"
)
GIT = dict(
    os.environ,
    GIT_AUTHOR_NAME="t",
    GIT_AUTHOR_EMAIL="t@t",
    GIT_COMMITTER_NAME="t",
    GIT_COMMITTER_EMAIL="t@t",
)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.repo, self.rec, self.work = t / "repo", t / "rec", t / "work"
        (self.repo / "tests").mkdir(parents=True)
        self.write(
            {
                "lib.py": LIB,
                "tests/test_lib.py": TEST,
                "tests/test_lib2.py": TEST.replace("T(", "U("),
                "envlib.py": "X = 1\n",
                "tests/test_env.py": TEST_ENV,
                "README.md": "lib\n",
            }
        )
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.commit("base")
        self.binding = t / "binding.json"
        self.bind()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, files):
        for k, v in files.items():
            (self.repo / k).write_text(v)

    def commit(self, msg):
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-q", "-m", msg], check=True, env=GIT, capture_output=True
        )

    def bind(self, **over):
        block = {
            "command": [sys.executable, "-m", "unittest"],
            "env": {"PYTHONDONTWRITEBYTECODE": "1"},
            "timeout": 60,
            "tests": [{"files": "lib.py", "tests": ["tests/test_lib.py"]}],
        }
        block.update(over)
        self.binding.write_text(json.dumps({"mutation": block}))

    def mutants(self, *rows):
        f = Path(self.tmp.name) / "mutants.json"
        f.write_text(json.dumps([dict(zip(mutate.MUTANT_KEYS, r)) if isinstance(r, tuple) else r for r in rows]))
        return f

    def run_(self, mutants, *extra, round_="first", env=None):
        p = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--mutants",
                str(mutants),
                "--repo",
                str(self.repo),
                "--binding",
                str(self.binding),
                "--record",
                str(self.rec),
                "--round",
                round_,
                "--work",
                str(self.work),
                *extra,
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        return p.returncode, p.stdout + p.stderr

    def rows(self, name="mutations.json"):
        return json.loads((self.rec / name).read_text())


LOADS = "import sys, importlib, pathlib; print(importlib.import_module(pathlib.Path(sys.argv[1]).stem).__file__)"
KILL = ("M1", "lib.py", "return a + b", "return a - b", "add subtracts")
LIVE = ("M2", "lib.py", "return x * 2", "return x * 3", "double triples")


class TestRun(Fixture):
    def test_killed_and_survived_rows_are_written_in_the_record_format(self):
        span = ("M3", "lib.py", "def double(x):\n    return x * 2", "def double(x):\n    return x * 5", "double x5")
        code, out = self.run_(self.mutants(KILL, LIVE, span))
        self.assertEqual(code, 0, out)
        rows = self.rows()
        self.assertEqual(len({r.pop("batch") for r in rows}), 1, "one batch id, in every row")
        self.assertEqual(
            rows,
            [
                {
                    "id": "M1",
                    "mutation": "add subtracts",
                    "site": "lib.py:2",
                    "gate": "tests/test_lib.py",
                    "verdict": "killed",
                    "disposition": None,
                },
                {
                    "id": "M2",
                    "mutation": "double triples",
                    "site": "lib.py:6",
                    "gate": "tests/test_lib.py",
                    "verdict": "survived",
                    "disposition": None,
                },
                {
                    "id": "M3",
                    "mutation": "double x5",
                    "site": "lib.py:5-6",
                    "gate": "tests/test_lib.py",
                    "verdict": "survived",
                    "disposition": None,
                },
            ],
        )
        # records-check reads it as a record: the one problem is the survivor left for the reviewer
        _, bad = records_check.check_mutations(
            (self.rec / "mutations.json").read_text(), set(), lambda p: (self.repo / p).exists(), open_ok=True
        )
        self.assertEqual(bad, [f"M2 {records_check.NO_DISPOSITION}", f"M3 {records_check.NO_DISPOSITION}"])
        self.assertFalse(any(self.work.iterdir()), "every copy is removed")

    def test_gate_is_a_list_when_several_test_paths_ran(self):
        # the first entry that matches decides
        self.bind(
            tests=[
                {"files": "*.py", "tests": ["tests/test_lib.py", "tests/test_lib2.py"]},
                {"files": "lib.py", "tests": ["tests/test_lib.py"]},
            ]
        )
        code, out = self.run_(self.mutants(KILL))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.rows()[0]["gate"], ["tests/test_lib.py", "tests/test_lib2.py"])

    def test_a_malformed_mutant_list_is_refused(self):
        cases = {
            "not a list": {"id": "M1"},
            "empty": [],
            "extra key": [dict(zip(mutate.MUTANT_KEYS, KILL), count=0)],
            "missing note": [{k: v for k, v in zip(mutate.MUTANT_KEYS, KILL) if k != "note"}],
            "empty find": [dict(zip(mutate.MUTANT_KEYS, KILL), find="  ")],
            "find equals replace": [dict(zip(mutate.MUTANT_KEYS, KILL), replace="return a + b")],
            "repeated id": [dict(zip(mutate.MUTANT_KEYS, KILL)), dict(zip(mutate.MUTANT_KEYS, LIVE), id="M1")],
        }
        for name, data in cases.items():
            with self.subTest(name):
                f = Path(self.tmp.name) / "bad.json"
                f.write_text(json.dumps(data))
                code, out = self.run_(f)
                self.assertEqual(code, 2, out)
                self.assertIn("refused", out)
                self.assertFalse(self.rec.exists())

    def test_find_must_occur_exactly_once(self):
        self.write({"README.md": "aaa\n"})
        self.commit("overlap")
        self.bind(tests=[{"files": "*", "tests": ["tests/test_lib.py"]}])
        for find, n, f in (("return a * b", 0, "lib.py"), ("return", 2, "lib.py"), ("aa", 2, "README.md")):
            with self.subTest(find):
                code, out = self.run_(self.mutants(("M1", f, find, "pass", "x")))
                self.assertEqual(code, 2, out)
                self.assertIn(f"find occurs {n} times in {f}", out)
                self.assertFalse(self.rec.exists())

    def test_a_failing_control_refuses_the_whole_batch(self):
        self.write({"lib.py": LIB.replace("a + b", "a + b + 1")})
        self.commit("broken")
        code, out = self.run_(self.mutants(LIVE))
        self.assertEqual(code, 2, out)
        self.assertIn("the control (unmutated", out)
        self.assertIn("no .git and no uncommitted files", out)
        self.assertFalse(self.rec.exists(), "nothing is written")

    def test_the_copy_is_the_commit_never_the_working_tree(self):
        self.write({"lib.py": LIB.replace("x * 2", "x * 4").replace("a + b", "b + a")})
        self.commit("four")
        dirty = LIB.replace("a + b", "a - b").replace("x * 2", "x * 4")
        self.write({"lib.py": dirty})  # uncommitted, and it would fail the control
        before = subprocess.run(
            ["git", "-C", str(self.repo), "status", "--porcelain"], capture_output=True, text=True, check=True
        ).stdout
        code, out = self.run_(self.mutants(LIVE, KILL), "--commit", "HEAD~1")
        self.assertEqual(code, 0, out)
        # KILL's text is only at HEAD~1: a copy of any other commit could not plant it
        self.assertEqual([r["verdict"] for r in self.rows()], ["survived", "killed"])
        self.assertEqual((self.repo / "lib.py").read_text(), dirty, "the working tree is not touched")
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(self.repo), "status", "--porcelain"], capture_output=True, text=True, check=True
            ).stdout,
            before,
        )
        code, out = self.run_(self.mutants(dict(zip(mutate.MUTANT_KEYS, LIVE), id="M4")))
        self.assertEqual(code, 2, out)
        self.assertIn("find occurs 0 times", out, "HEAD holds x * 4, whatever the working tree says")
        code, out = self.run_(self.mutants(KILL), "--work", str(self.repo / "scratch"))
        self.assertEqual(code, 2, out)
        self.assertIn("inside the repository", out)

    def test_rows_are_appended_and_a_reviewers_disposition_is_kept(self):
        self.rec.mkdir()
        old = [
            {
                "id": "R1",
                "mutation": "by hand",
                "site": "lib.py:2",
                "gate": "tests/test_lib.py",
                "verdict": "survived",
                "disposition": "accepted: reach 0 of 3 inputs",
                "evidence": "kept",
            }
        ]
        (self.rec / "mutations.json").write_text(json.dumps(old))
        code, out = self.run_(self.mutants(KILL, LIVE))
        self.assertEqual(code, 0, out)
        rows = self.rows()
        self.assertEqual(rows[0], old[0])
        self.assertEqual([r["id"] for r in rows], ["R1", "M1", "M2"])

    def test_an_id_already_in_the_file_is_refused(self):
        self.rec.mkdir()
        text = json.dumps(
            [
                {
                    "id": "M1",
                    "mutation": "m",
                    "site": "s",
                    "gate": "tests/test_lib.py",
                    "verdict": "survived",
                    "disposition": "open: F1",
                }
            ]
        )
        (self.rec / "mutations.json").write_text(text)
        code, out = self.run_(self.mutants(KILL, LIVE))
        self.assertEqual(code, 2, out)
        self.assertIn("M1 is already a row of mutations.json", out)
        self.assertEqual((self.rec / "mutations.json").read_text(), text)

    def test_the_round_names_come_from_the_record_contract(self):
        job = records_check.load_contracts()["jobs"]["reviewer"]
        code, out = self.run_(self.mutants(KILL), round_="fix2")
        self.assertEqual(code, 0, out)
        self.assertEqual(
            sorted(p.name for p in self.rec.iterdir()),
            sorted(records_check.round_name(job, 2, k) for k in ("mutations", "log")),
        )
        for bad in ("fix0", "second", "Fix1"):
            with self.subTest(bad):
                code, out = self.run_(self.mutants(LIVE), round_=bad)
                self.assertEqual(code, 2, out)
                self.assertIn("neither `first` nor `fix<N>`", out)

    def labelled_round(self):
        code, out = self.run_(self.mutants(LIVE), round_="golive")
        self.assertEqual(code, 2, out)
        self.assertIn("neither `first` nor `fix<N>`", out)
        self.assertIn("A labelled prediction round (for example `golive`) is reviewed as the next fix round", out)
        return out

    def test_a_labelled_round_is_told_the_folders_next_fix_round(self):
        self.rec.mkdir()
        (self.rec / "findings.md").write_text("first\n")
        (self.rec / "findings-fix2.md").write_text("fix 2\n")
        out = self.labelled_round()
        self.assertIn("`--round fix3`", out)
        self.assertEqual(sorted(p.name for p in self.rec.iterdir()), ["findings-fix2.md", "findings.md"])

    def test_a_labelled_round_with_no_round_in_the_folder_is_told_first(self):
        for state in ("missing", "empty"):
            with self.subTest(state):
                if state == "empty":
                    self.rec.mkdir()
                out = self.labelled_round()
                self.assertIn("`--round first`", out)
                self.assertNotIn("`--round fix", out)

    def test_a_labelled_round_after_the_first_review_is_told_fix1(self):
        self.rec.mkdir()
        (self.rec / "findings.md").write_text("first\n")
        out = self.labelled_round()
        self.assertIn("`--round fix1`", out)

    def test_a_timeout_writes_no_row_and_exits_1(self):
        self.bind(timeout=3)
        hang = ("M9", "lib.py", "return a + b", "import time\n    time.sleep(30)", "add hangs")
        code, out = self.run_(self.mutants(hang, LIVE))
        self.assertEqual(code, 1, out)
        self.assertIn("M9 TIMEOUT", out)
        self.assertEqual([r["id"] for r in self.rows()], ["M2"])

    def data_fixture(self, test_body):
        data = Path(self.tmp.name) / "data"
        data.mkdir()
        (data / "value.txt").write_text("v1")
        subprocess.run(["git", "init", "-q", str(data)], check=True)
        subprocess.run(["git", "-C", str(data), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(data), "commit", "-q", "-m", "v1"], check=True, env=GIT)
        (data / "value.txt").write_text("dirty")  # uncommitted: the snapshot must not see it
        (data / "extra.txt").write_text("untracked")
        (self.repo / "data").symlink_to("../data")
        self.write(
            {
                "tests/test_data.py": "import unittest\n\nimport envlib\n\n\nclass D(unittest.TestCase):\n"
                "    def test_x(self):\n        self.assertEqual(envlib.X, 1)\n\n    def test_d(self):\n" + test_body
            }
        )
        self.commit("data link")
        self.bind(links={"data": "data"}, tests=[{"files": "envlib.py", "tests": ["tests/test_data.py"]}])

        def state():
            return [
                subprocess.run(["git", "-C", str(data), *c], capture_output=True, text=True, check=True).stdout
                for c in (["rev-parse", "HEAD"], ["status", "--porcelain"])
            ]

        return state

    def test_a_data_link_is_a_snapshot_of_the_committed_head(self):
        state = self.data_fixture("        self.assertEqual(open('data/value.txt').read(), 'v1')\n")
        before = state()
        code, out = self.run_(self.mutants(("E1", "envlib.py", "X = 1", "X = 2", "x is two")))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.rows()[0]["data"], {"data": before[0].strip()})
        self.assertIn(f"snapshot of its repository's HEAD {before[0][:12]}", out)
        self.assertEqual(state(), before, "the live data repository is not touched")
        self.assertFalse(any(self.work.iterdir()), "the snapshot is removed with the copies")
        self.bind(links={"data": "tests"}, tests=[{"files": "envlib.py", "tests": ["tests/test_data.py"]}])
        code, out = self.run_(self.mutants(("E2", "envlib.py", "X = 1", "X = 3", "x is three")))
        self.assertEqual(code, 2, out)
        self.assertIn("is not the top of a git repository", out)

    def test_a_test_that_needs_an_uncommitted_data_file_fails_the_control(self):
        state = self.data_fixture("        self.assertEqual(open('data/extra.txt').read(), 'untracked')\n")
        before = state()
        code, out = self.run_(self.mutants(("E1", "envlib.py", "X = 1", "X = 2", "x is two")))
        self.assertEqual(code, 2, out)
        self.assertIn("the control", out)
        self.assertFalse(self.rec.exists())
        self.assertEqual(state(), before)

    def test_a_file_no_entry_names_is_refused(self):
        code, out = self.run_(self.mutants(("M1", "README.md", "lib", "lab", "readme")))
        self.assertEqual(code, 2, out)
        self.assertIn("no entry of the binding's mutation.tests names tests for README.md", out)
        self.bind(tests=[{"files": "lib.py", "tests": ["tests/test_gone.py"]}])
        code, out = self.run_(self.mutants(KILL))
        self.assertEqual(code, 2, out)
        self.assertIn("tests/test_gone.py do not exist", out)

    def test_without_binding_the_projects_own_binding_is_read(self):
        argv = [sys.executable, str(SCRIPT), "--mutants", str(self.mutants(KILL)), "--repo", str(self.repo),
                "--record", str(self.rec), "--round", "first", "--work", str(self.work)]
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        p = subprocess.run(argv, capture_output=True, text=True, check=False, env=env)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("no binding for", p.stderr)
        (self.repo / ".team").mkdir()
        (self.repo / ".team" / "binding.json").write_text(self.binding.read_text())
        records = Path(self.tmp.name) / "repo.team-records"
        for args in (("worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(records)),
                     ("config", "bakehouse.records", str(records))):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        p = subprocess.run(argv, capture_output=True, text=True, check=False, env=env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual([r["verdict"] for r in self.rows()], ["killed"])

    def test_the_binding_env_and_root_placeholder_reach_the_tests(self):
        entry = [{"files": "envlib.py", "tests": ["tests/test_env.py"]}]
        mutant = ("E1", "envlib.py", "X = 1", "X = 2", "x is two")
        self.bind(tests=entry, env={"T9_MARK": "set", "T9_ROOT": "{root}", "PYTHONDONTWRITEBYTECODE": "1"})
        code, out = self.run_(self.mutants(mutant), env=dict(os.environ, T9_MARK="the caller's"))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.rows()[0]["verdict"], "killed", "the binding's env wins over the caller's")
        self.bind(tests=entry)  # without the env, the control fails: the env is what made it pass
        code, out = self.run_(self.mutants(dict(zip(mutate.MUTANT_KEYS, mutant), id="E2")))
        self.assertEqual(code, 2, out)
        self.assertIn("the control", out)

    def test_rows_others_write_during_a_batch_are_kept_and_a_colliding_id_is_refused(self):
        rec = self.rec / "mutations.json"
        self.rec.mkdir()
        rec.write_text(
            json.dumps(
                [{"id": "X0", "mutation": "m", "site": "s", "gate": "g", "verdict": "survived", "disposition": None}]
            )
        )
        hand = (
            "import json, os, unittest\n\nimport lib\n\n\nclass H(unittest.TestCase):\n"
            "    def test_h(self):\n        self.assertTrue(callable(lib.add))\n"
            "        if lib.add(2, 3) != 5:  # only in the mutant: a reviewer edits the record meanwhile\n"
            "            p = os.environ['T9_REC']\n            rows = json.load(open(p))\n"
            "            rows[0]['disposition'] = 'open: F1'\n"
            "            rows += [dict(rows[0], id='H1', disposition=None), dict(rows[0], id='M3', disposition=None)]\n"
            "            json.dump(rows, open(p, 'w'))\n"
        )
        self.write({"tests/test_hand.py": hand})
        self.commit("hand")
        self.bind(
            tests=[{"files": "lib.py", "tests": ["tests/test_hand.py"]}],
            env={"T9_REC": str(rec), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        code, out = self.run_(
            self.mutants(
                ("A1", "lib.py", "return a + b", "return a + b + 1", "add + 1"),
                ("M3", "lib.py", "return x * 2", "return x * 3", "double triples"),
            )
        )
        self.assertEqual(code, 3, out)  # a refusal after a mutant ran: rows may already be written
        self.assertIn("M3 is already a row", out)
        rows = self.rows()
        self.assertEqual([r["id"] for r in rows], ["X0", "H1", "M3", "A1"])
        self.assertEqual(rows[0]["disposition"], "open: F1", "a disposition written during the batch is kept")

    def test_the_snapshot_is_read_only(self):
        # passes only when neither a new file nor an existing one can be written in the snapshot
        state = self.data_fixture(
            "        for p in ('data/marker.txt', 'data/value.txt'):\n"
            "            with self.assertRaises(PermissionError):\n                open(p, 'w').write('x')\n"
        )
        before = state()
        code, out = self.run_(self.mutants(("E1", "envlib.py", "X = 1", "X = 2", "x is two")))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.rows()[0]["verdict"], "killed")
        self.assertEqual(state(), before)
        self.assertFalse(any(self.work.iterdir()), "the read-only snapshot is still removed")

    def test_a_mutant_that_changes_no_bytes_in_the_copy_is_refused(self):
        self.write({".gitattributes": "*.py text eol=crlf\n"})
        self.commit("crlf")
        code, out = self.run_(
            self.mutants(
                ("M1", "lib.py", "def double(x):\n    return x * 2", "def double(x):\n    return x * 3", "two lines")
            )
        )
        self.assertEqual(code, 2, out)
        self.assertIn("in the archived copy", out)
        self.assertNotIn("control ok", out)

    def test_a_symlinked_mutant_file_is_refused(self):
        outside = Path(self.tmp.name) / "outside.py"
        outside.write_text("# outside\n")
        (self.repo / "alias.py").symlink_to(outside)
        self.commit("alias")
        self.bind(tests=[{"files": "*.py", "tests": ["tests/test_lib.py"]}])
        code, out = self.run_(self.mutants(("H1", "alias.py", "outside", "inside", "through a link")))
        self.assertEqual(code, 2, out)
        self.assertIn("symlink", out)
        self.assertEqual(outside.read_text(), "# outside\n")

    def test_a_probe_refuses_tests_that_do_not_read_the_copy(self):
        (self.repo / "src").mkdir()
        self.write(
            {
                "src/pkglib.py": "def one():\n    return 1\n",
                "tests/test_pkg.py": "import unittest\n\nimport pkglib\n\n\nclass P(unittest.TestCase):\n"
                "    def test_one(self):\n        self.assertEqual(pkglib.one(), 1)\n",
            }
        )
        self.commit("src layout")
        mutant = ("P1", "src/pkglib.py", "return 1", "return 2", "one is two")
        entry = [{"files": "src/*", "tests": ["tests/test_pkg.py"]}]
        self.bind(tests=entry, env={"PYTHONPATH": "{root}/src"})  # the checkout's code, not the copy's
        code, out = self.run_(self.mutants(mutant))
        self.assertEqual(code, 2, out)
        self.assertIn("still pass with src/pkglib.py emptied", out)
        self.bind(tests=entry, env={"PYTHONPATH": "src"})
        code, out = self.run_(self.mutants(mutant), env=dict(os.environ, PYTHONPATH=str(self.repo / "src")))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.rows()[0]["verdict"], "killed")

    def test_malformed_inputs_refuse_with_exit_2_before_any_test_runs(self):
        def check(why):
            code, out = self.run_(self.mutants(KILL))
            self.assertEqual(code, 2, (why, out))
            self.assertNotIn("Traceback", out, why)
            self.assertNotIn("control ok", out, why)

        (self.rec / "mutations.json").mkdir(parents=True)
        check("the record path is a folder")
        (self.rec / "mutations.json").rmdir()
        (self.rec / "mutations.json").write_text("[]")
        (self.rec / "mutations.json").chmod(0)
        check("the record is unreadable")
        (self.rec / "mutations.json").chmod(0o644)
        (self.rec / "mutations.json").unlink()
        self.rec.chmod(0o555)
        check("the record folder is read-only")
        self.rec.chmod(0o755)
        self.binding.chmod(0)
        check("the binding is unreadable")
        self.binding.chmod(0o644)
        self.bind(command=[str(Path(self.tmp.name) / "no-such-program")])
        check("the test program does not exist")

    def test_an_interrupted_batch_kills_its_tests_and_removes_its_work_folder(self):
        pidf = Path(self.tmp.name) / "pid"
        self.write(
            {
                "tests/test_sleep.py": "import os, time, unittest\n\n\nclass S(unittest.TestCase):\n"
                "    def test_s(self):\n        open(os.environ['T9_PID'], 'w').write(str(os.getpid()))\n"
                "        time.sleep(30)\n"
            }
        )
        self.commit("sleep")
        self.bind(tests=[{"files": "lib.py", "tests": ["tests/test_sleep.py"]}], env={"T9_PID": str(pidf)})
        for sig, want in ((signal.SIGTERM, 143), (signal.SIGINT, 130), (signal.SIGHUP, 129)):
            with self.subTest(sig):
                pidf.unlink(missing_ok=True)
                p = subprocess.Popen(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "--mutants",
                        str(self.mutants(KILL)),
                        "--repo",
                        str(self.repo),
                        "--binding",
                        str(self.binding),
                        "--record",
                        str(self.rec),
                        "--round",
                        "first",
                        "--work",
                        str(self.work),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                for _ in range(100):
                    if pidf.exists() and pidf.read_text():
                        break
                    time.sleep(0.1)
                p.send_signal(sig)
                self.assertEqual(p.wait(timeout=20), want)
                time.sleep(0.5)
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(pidf.read_text()), 0)
                self.assertFalse(any(self.work.iterdir()), "the work folder is removed")

    def test_the_site_skips_leading_blank_text(self):
        code, out = self.run_(
            self.mutants(("M1", "lib.py", "\n\n\ndef double(x):", "\n\n\ndef double(x):  # c", "a comment"))
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(self.rows()[0]["site"], "lib.py:5")

    def test_every_distinct_test_command_has_its_own_control(self):
        self.bind(
            tests=[
                {"files": "lib.py", "tests": ["tests/test_lib.py"]},
                {"files": "envlib.py", "tests": ["tests/test_env.py"]},
            ]
        )  # test_env fails without its env
        code, out = self.run_(self.mutants(KILL, ("E1", "envlib.py", "X = 1", "X = 2", "x is two")))
        self.assertEqual(code, 2, out)
        self.assertIn("tests/test_env.py", out.split("refused")[-1])

    def test_a_timeout_kills_the_whole_test_process_group(self):
        self.write(
            {
                "tests/test_child.py": "import subprocess, time, unittest\n\n\nclass C(unittest.TestCase):\n"
                "    def test_c(self):\n        subprocess.Popen(['sleep', '30'])\n        time.sleep(30)\n"
            }
        )
        self.commit("child")
        self.bind(timeout=2, tests=[{"files": "lib.py", "tests": ["tests/test_child.py"]}])
        t = time.time()
        code, out = self.run_(self.mutants(KILL))
        self.assertEqual(code, 2, out)
        self.assertLess(time.time() - t, 15, "a child holding the pipes does not outlive the timeout")

    def test_a_real_folder_at_a_link_path_is_replaced_by_the_snapshot(self):
        data = Path(self.tmp.name) / "data2"
        data.mkdir()
        (data / "value.txt").write_text("v1")
        subprocess.run(["git", "init", "-q", str(data)], check=True)
        subprocess.run(["git", "-C", str(data), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(data), "commit", "-q", "-m", "v1"], check=True, env=GIT)
        (self.repo / "data").mkdir()
        self.write(
            {
                "data/value.txt": "tracked",
                "tests/test_data.py": "import unittest\n\nimport envlib\n\n\nclass D(unittest.TestCase):\n    def test_d(self):\n"
                "        self.assertEqual(envlib.X, 1)\n"
                "        self.assertEqual(open('data/value.txt').read(), 'v1')\n",
            }
        )
        self.commit("a tracked data folder")
        (self.repo / "datarepo").symlink_to(data)
        self.bind(links={"data": "datarepo"}, tests=[{"files": "envlib.py", "tests": ["tests/test_data.py"]}])
        code, out = self.run_(self.mutants(("E1", "envlib.py", "X = 1", "X = 2", "x is two")))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.rows()[0]["verdict"], "killed")

    def test_a_row_lost_to_a_racing_edit_is_restored_and_flagged(self):
        rec = self.rec / "mutations.json"
        self.rec.mkdir()
        rec.write_text(
            json.dumps(
                [{"id": "X0", "mutation": "m", "site": "s", "gate": "g", "verdict": "survived", "disposition": None}]
            )
        )
        start = rec.read_text()  # a reviewer's editor read the file before the batch, and writes it back later
        race = (
            "import os, unittest\n\nimport lib\n\n\nclass R(unittest.TestCase):\n"
            "    def test_r(self):\n        self.assertTrue(callable(lib.double))\n"
            "        if lib.double(2) == 6:\n            open(os.environ['T9_REC'], 'w').write(os.environ['T9_OLD'])\n"
        )
        self.write({"tests/test_race.py": race})
        self.commit("race")
        self.bind(
            tests=[{"files": "lib.py", "tests": ["tests/test_race.py"]}], env={"T9_REC": str(rec), "T9_OLD": start}
        )
        code, out = self.run_(
            self.mutants(
                ("B1", "lib.py", "return a + b", "return a - b", "b1"),
                ("B2", "lib.py", "return x * 2", "return x * 3", "b2"),
            )
        )
        self.assertEqual(code, 3, out)
        self.assertIn("restored B1", out)
        self.assertEqual([r["id"] for r in self.rows()], ["X0", "B1", "B2"])

    def test_the_load_check_refuses_tests_that_load_the_checkout(self):
        (self.repo / "src").mkdir()
        self.write(
            {
                "src/pkglib.py": "def one():\n    return 1\n",
                "tests/test_pkg.py": "import unittest\n\nimport pkglib\n\n\nclass P(unittest.TestCase):\n"
                "    def test_one(self):\n        self.assertEqual(pkglib.one(), 1)\n\n"
                "    def test_text(self):\n        self.assertIn('def one', open('src/pkglib.py').read())\n",
            }
        )
        self.commit("src layout")
        mutant = ("P1", "src/pkglib.py", "return 1", "return 2", "one is two")
        entry = [{"files": "src/*", "tests": ["tests/test_pkg.py"]}]
        loads = {"files": "src/*.py", "command": [sys.executable, "-c", LOADS]}
        self.bind(tests=entry, env={"PYTHONPATH": "{root}/src"})  # the text test fails the empty-file probe
        code, out = self.run_(self.mutants(mutant))
        self.assertIn("the weaker check", out)
        self.bind(tests=entry, env={"PYTHONPATH": "{root}/src"}, loads=loads)
        code, out = self.run_(self.mutants(dict(zip(mutate.MUTANT_KEYS, mutant), id="P2")))
        self.assertEqual(code, 2, out)
        self.assertIn("is not the copy's src/pkglib.py", out)
        self.bind(tests=entry, env={"PYTHONPATH": "src"}, loads=loads)
        code, out = self.run_(self.mutants(dict(zip(mutate.MUTANT_KEYS, mutant), id="P3")))
        self.assertEqual(code, 0, out)
        self.assertIn("loads ok: src/pkglib.py", out)
        self.assertEqual(self.rows()[-1]["verdict"], "killed")

    def test_crash_paths_refuse_cleanly(self):
        self.data_fixture("        import shutil\n        shutil.copytree('data', 'out-of-snapshot')\n")
        code, out = self.run_(
            self.mutants(("E1", "envlib.py", "X = 1", "X = 2", "x2"), ("E2", "envlib.py", "X = 1", "X = 3", "x3"))
        )
        self.assertEqual(code, 0, out)
        self.assertEqual([r["id"] for r in self.rows()], ["E1", "E2"])
        self.assertFalse(any(self.work.iterdir()))
        self.write({".gitattributes": "envlib.py export-ignore\n"})
        self.commit("export-ignore")
        code, out = self.run_(self.mutants(("E3", "envlib.py", "X = 1", "X = 4", "x4")))
        self.assertEqual(code, 2, out)
        self.assertIn("not in the archived copy", out)
        self.assertNotIn("Traceback", out)

    def test_a_find_that_doubles_in_the_archived_copy_is_refused(self):
        self.write({".gitattributes": "lib.py export-subst\n", "lib.py": LIB + "\n\nA = '$Format:%an$'\nB = 't'\n"})
        self.commit("subst")
        code, out = self.run_(self.mutants(("M1", "lib.py", "'t'", "'u'", "b is u")))
        self.assertEqual(code, 2, out)
        self.assertIn("occurs 2 times in lib.py in the archived copy", out)

    def test_a_relative_test_program_runs_from_the_copy(self):
        (self.repo / "bin").mkdir()
        self.write({"bin/t.sh": f'#!/bin/sh\nexec {sys.executable} -m unittest "$@"\n'})
        (self.repo / "bin/t.sh").chmod(0o755)
        self.commit("script")
        self.bind(command=["bin/t.sh"])
        p = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--mutants",
                str(self.mutants(KILL)),
                "--repo",
                str(self.repo),
                "--binding",
                str(self.binding),
                "--record",
                str(self.rec),
                "--round",
                "first",
                "--work",
                str(self.work),
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=self.tmp.name,
        )
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.rows()[0]["verdict"], "killed")

    def test_a_load_check_that_passes_does_not_skip_the_probe(self):
        # the reviewer's c9: a console-script runner puts its own folder first, so with PYTHONPATH={root} the
        # tests load the checkout's lib.py, while `python -c` (the loads command) finds the copy's
        (self.repo / "bin").mkdir()
        self.write(
            {
                "bin/runtests": f"#!{sys.executable}\nimport os, sys, unittest\nsys.path.append(os.getcwd())\n"
                "unittest.main(module=None, argv=['unittest'] + sys.argv[1:])\n"
            }
        )
        (self.repo / "bin/runtests").chmod(0o755)
        self.commit("console script")
        self.bind(
            command=["bin/runtests"],
            env={"PYTHONPATH": "{root}"},
            loads={"files": "*.py", "command": [sys.executable, "-c", LOADS]},
        )
        code, out = self.run_(self.mutants(KILL))
        self.assertIn("loads ok: lib.py", out)
        self.assertEqual(code, 2, out)
        self.assertIn("still pass with lib.py emptied", out)
        self.assertFalse(self.rec.exists())


X0 = {"id": "X0", "mutation": "m", "site": "s", "gate": "tests/test_lib.py", "verdict": "survived",
      "disposition": "accepted: reach 0"}


Y_ROW = dict(X0, id="B1", verdict="killed", disposition=None, batch="b1", data={"data": "h1"})


class DiskReader:
    """records-check's Reader on a folder, as the stop check reads it."""

    def __init__(self, root):
        self.root = root

    def text(self, path):
        return Path(path).read_text(encoding="utf-8")

    def exists(self, path):
        return (self.root / path).exists()

    def file_text(self, path):
        p = self.root / path
        return p.read_text(encoding="utf-8") if p.is_file() else None


class TestBatchLog(Fixture):
    """T9b: every verdict goes to the round's append-only batch log before the record; records-check
    reconciles the two (T9's O2-O6)."""

    def edit_during(self, cond, body, rows=(X0,)):
        """A record holding rows; a test file that runs `body` (p = the record's path) when cond holds,
        which is true only in the mutant meant."""
        rec = self.rec / "mutations.json"
        self.rec.mkdir()
        rec.write_text(json.dumps(list(rows)))
        (self.rec / "findings.md").write_text("F1\n")
        hand = (
            "import json, os, unittest\n\nimport lib\n\n\nclass H(unittest.TestCase):\n"
            "    def test_h(self):\n        self.assertTrue(callable(lib.add))\n"
            f"        if {cond}:\n            p = os.environ['T9_REC']\n" + "".join(f"            {x}\n" for x in body)
        )
        self.write({"tests/test_hand.py": hand})
        self.commit("hand")
        self.bind(
            tests=[{"files": "lib.py", "tests": ["tests/test_hand.py"]}],
            env={"T9_REC": str(rec), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return rec

    def log(self, name="mutations.log.jsonl"):
        return [json.loads(x) for x in (self.rec / name).read_text().splitlines()]

    def reconcile(self):
        """records-check's own folder check on the record, as the stop check runs it; the survivors' open
        dispositions are the reviewer's and are left out."""
        job = records_check.load_contracts()["jobs"]["reviewer"]
        files = {q.name: q for q in self.rec.iterdir() if q.is_file()}
        got = records_check.check_folder(files, job, DiskReader(self.repo), set(), open_ok=True)
        return [w for _, _, w in got if not w.endswith(records_check.NO_DISPOSITION)]

    B = (("B1", "lib.py", "return a + b", "return a - b", "b1"), ("B2", "lib.py", "return x * 2", "return x * 3", "b2"))

    def test_each_verdict_is_logged_before_the_record_and_the_two_agree(self):
        self.edit_during("False", ["pass"])
        code, out = self.run_(self.mutants(*self.B))
        self.assertEqual(code, 0, out)
        log, rows = self.log(), self.rows()
        self.assertEqual([x["row"] for x in log], rows[1:])
        self.assertEqual(len({x["batch"] for x in log}), 1)
        self.assertEqual({r["batch"] for r in rows[1:]}, {log[0]["batch"]})
        self.assertTrue(all(x["commit"] == x["commit"].strip() and len(x["commit"]) == 40 for x in log))
        self.assertEqual(self.reconcile(), [])
        rows[2]["disposition"] = "accepted: reach 0"  # the reviewer's field may change
        (self.rec / "mutations.json").write_text(json.dumps(rows))
        self.assertEqual(self.reconcile(), [])

    def test_o2_a_verdict_changed_during_the_batch_is_named_and_flagged(self):
        self.edit_during(
            "lib.double(2) == 6",
            ["rows = json.load(open(p))", "rows[1]['verdict'] = 'killed'", "json.dump(rows, open(p, 'w'))"],
        )
        code, out = self.run_(self.mutants(*self.B))
        self.assertEqual(code, 3, out)
        self.assertIn("B1 was changed during the batch (verdict)", out)
        self.assertEqual(self.rows()[1]["verdict"], "killed", "the runner does not overwrite an edit")
        got = self.reconcile()
        self.assertTrue(any("B1" in w and "verdict" in w for w in got), got)

    def test_o3_a_save_after_the_last_append_is_caught_by_records_check(self):
        start = self.edit_during("False", ["pass"])
        old = start.read_text()
        code, out = self.run_(self.mutants(self.B[0]))
        self.assertEqual(code, 0, out)
        start.write_text(old)  # the editor saves its start copy after the batch exits
        got = self.reconcile()
        self.assertTrue(any("B1" in w and "not in mutations.json" in w for w in got), got)

    def test_a_row_claiming_a_batch_the_log_does_not_hold_is_flagged(self):
        self.edit_during("False", ["pass"], rows=(X0, dict(X0, id="Z1", batch="b-none")))
        self.assertTrue(any("Z1" in w and "log" in w for w in self.reconcile()), self.reconcile())
        (self.rec / "mutations.json").write_text(json.dumps([X0]))
        self.assertEqual(self.reconcile(), [], "a row with no batch is the reviewer's own and is not reconciled")

    def test_o4_a_record_that_is_not_json_at_the_reread_keeps_the_verdict(self):
        self.edit_during("lib.double(2) == 6", ["open(p, 'w').write('[{\"id\": ')"])
        code, out = self.run_(self.mutants(*self.B))
        self.assertEqual(code, 3, out)
        self.assertNotIn("Traceback", out)
        self.assertIn("B2 survived", out)
        self.assertEqual([x["row"]["id"] for x in self.log()], ["B1", "B2"])

    def test_w1_every_row_an_older_copy_dropped_is_restored(self):
        start = json.dumps([X0])
        self.edit_during("lib.add(2, 3) == 6", [f"open(p, 'w').write({start!r})"])
        code, out = self.run_(
            self.mutants(*self.B, ("B3", "lib.py", "return a + b", "return a + b + 1", "b3"))
        )
        self.assertEqual(code, 3, out)
        self.assertEqual([r["id"] for r in self.rows()], ["X0", "B1", "B2", "B3"])
        self.assertEqual(self.reconcile(), [])

    def test_w2_an_os_error_after_a_mutant_ran_stops_with_exit_3(self):
        self.edit_during("lib.add(2, 3) == -1", ["os.chmod(os.path.dirname(os.getcwd()), 0o555)"])
        code, out = self.run_(self.mutants(*self.B))
        self.assertEqual(code, 3, out)
        self.assertIn("stopped after a mutant ran", out)
        self.assertNotIn("Traceback", out)
        self.assertFalse(any(self.work.iterdir()))

    def test_t6_an_id_already_in_the_log_refuses_the_batch(self):
        rec = self.edit_during("False", ["pass"])
        code, out = self.run_(self.mutants(self.B[0]))
        self.assertEqual(code, 0, out)
        rec.write_text(json.dumps([X0]))  # a hand edit drops B1; its verdict is still in the log
        code, out = self.run_(self.mutants(self.B[0]))
        self.assertEqual(code, 2, out)
        self.assertIn("B1 is already in mutations.log.jsonl", out)
        self.assertEqual(len(self.log()), 1, "a measured verdict is copied back from the log, not measured again")

    def test_r2_a_change_to_any_runner_field_is_flagged(self):
        rec = self.edit_during("False", ["pass"])
        code, out = self.run_(self.mutants(self.B[0]))
        self.assertEqual(code, 0, out)
        good = rec.read_text()
        for k, v in (("mutation", "other"), ("site", "lib.py:9"), ("gate", "tests/test_lib.py"), ("batch", "b-x")):
            with self.subTest(k):
                rows = json.loads(good)
                rows[1][k] = v
                rec.write_text(json.dumps(rows))
                self.assertTrue(any(w.startswith(f"mutations.json: B1's {k} in") for w in self.reconcile()), self.reconcile())

    def test_r4_a_batch_the_log_does_not_hold_is_flagged_when_a_log_exists(self):
        rec = self.edit_during("False", ["pass"])
        code, out = self.run_(self.mutants(self.B[0]))
        self.assertEqual(code, 0, out)
        rec.write_text(json.dumps(self.rows() + [dict(X0, id="Z2", batch="b-none")]))
        got = self.reconcile()
        self.assertEqual(len(got), 1, got)
        self.assertIn("Z2 carries batch \"b-none\", which mutations.log.jsonl does not hold", got[0])

    def test_r5_a_fix_rounds_file_is_reconciled_with_its_own_log(self):
        self.edit_during("False", ["pass"])
        (self.rec / "findings-fix1.md").write_text("G1\n")
        code, out = self.run_(self.mutants(self.B[0]), round_="fix1")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.log("mutations-fix1.log.jsonl")), 1)
        (self.rec / "mutations-fix1.json").write_text("[]")
        got = self.reconcile()
        self.assertTrue(any("B1" in w and "mutations-fix1.log.jsonl" in w for w in got), got)

    def craft(self, rows, lines):
        """A record and its batch log written directly, for records-check's reconcile alone."""
        self.rec.mkdir(exist_ok=True)
        (self.rec / "findings.md").write_text("F1\n")
        (self.rec / "mutations.json").write_text(json.dumps(rows))
        (self.rec / "mutations.log.jsonl").write_text("".join(x + "\n" for x in lines))

    def line(self, row, batch="b1"):
        return json.dumps({"batch": batch, "commit": "c" * 40, "data": row.get("data", {}), "row": row})

    def test_y1_a_changed_data_field_is_flagged(self):
        self.craft([dict(Y_ROW, data={"data": "h2"})], [self.line(Y_ROW)])
        self.assertTrue(any("B1's data in" in w for w in self.reconcile()), self.reconcile())

    def test_y2_a_torn_log_line_is_flagged(self):
        self.craft([Y_ROW], [self.line(Y_ROW), '{"batch": "b1", "ro'])
        self.assertTrue(any("line 2 is not a batch log line" in w for w in self.reconcile()), self.reconcile())

    def test_y3_a_log_line_whose_envelope_batch_differs_is_flagged(self):
        self.craft([Y_ROW], [self.line(Y_ROW, batch="b-other")])
        self.assertTrue(any("line 1 is not a batch log line" in w for w in self.reconcile()), self.reconcile())

    def test_y8_a_hand_row_copying_a_batch_under_a_new_id_is_flagged(self):
        self.craft([Y_ROW, dict(Y_ROW, id="Z3")], [self.line(Y_ROW)])
        self.assertTrue(any(w.startswith("mutations.json: Z3 carries batch") for w in self.reconcile()), self.reconcile())

    def test_y4_a_torn_log_refuses_the_batch(self):
        self.edit_during("False", ["pass"])
        (self.rec / "mutations.log.jsonl").write_text('{"batch": "b1", "ro')
        code, out = self.run_(self.mutants(self.B[0]))
        self.assertEqual(code, 2, out)
        self.assertIn("is not the runner's batch log", out)
        self.assertEqual((self.rec / "mutations.log.jsonl").read_text(), '{"batch": "b1", "ro')

    def test_y5_a_log_that_is_a_folder_or_read_only_refuses(self):
        self.edit_during("False", ["pass"])
        log = self.rec / "mutations.log.jsonl"
        log.mkdir()
        code, out = self.run_(self.mutants(self.B[0]))
        self.assertEqual(code, 2, out)
        self.assertIn("is a folder or read-only", out)
        log.rmdir()
        log.write_text("")
        log.chmod(0o444)
        code, out = self.run_(self.mutants(self.B[0]))
        self.assertEqual(code, 2, out)
        self.assertIn("is a folder or read-only", out)
        self.assertEqual(self.rows(), [X0])

    def test_y6_work_under_a_read_only_folder_refuses(self):
        ro = Path(self.tmp.name) / "ro"
        ro.mkdir()
        ro.chmod(0o555)
        self.work = ro / "sub"
        try:
            code, out = self.run_(self.mutants(KILL))
        finally:
            ro.chmod(0o755)
        self.assertEqual(code, 2, out)
        self.assertIn("--work", out)
        self.assertFalse(self.rec.exists())

    def test_y7_a_changed_field_other_than_the_verdict_is_named(self):
        self.edit_during(
            "lib.double(2) == 6",
            ["rows = json.load(open(p))", "rows[1]['site'] = 'lib.py:9'", "json.dump(rows, open(p, 'w'))"],
        )
        code, out = self.run_(self.mutants(*self.B))
        self.assertEqual(code, 3, out)
        self.assertIn("B1 was changed during the batch (site)", out)

    def test_o5_a_test_program_without_the_exec_bit_refuses_with_exit_2(self):
        (self.repo / "bin").mkdir()
        self.write({"bin/t.sh": f'#!/bin/sh\nexec {sys.executable} -m unittest "$@"\n'})
        self.commit("script, no exec bit")
        self.bind(command=["bin/t.sh"])
        code, out = self.run_(self.mutants(KILL))
        self.assertEqual(code, 2, out)
        self.assertIn("not executable", out)
        self.assertFalse(self.rec.exists())

    def test_w5_a_missing_relative_test_program_refuses_with_exit_2(self):
        self.bind(command=["bin/missing.sh"])
        code, out = self.run_(self.mutants(KILL))
        self.assertEqual(code, 2, out)
        self.assertIn("bin/missing.sh", out)
        self.assertFalse(self.rec.exists())

    def test_o5_work_under_a_plain_file_refuses_with_exit_2(self):
        plain = Path(self.tmp.name) / "plain"
        plain.write_text("x")
        self.work = plain / "sub"
        code, out = self.run_(self.mutants(KILL))
        self.assertEqual(code, 2, out)
        self.assertIn("--work", out)
        self.assertFalse(self.rec.exists())


class TestBlock(unittest.TestCase):
    def test_mutation_errors_name_each_bad_field(self):
        ok = {
            "command": ["python3", "-m", "pytest"],
            "env": {"A": "b"},
            "timeout": 5,
            "links": {"data": "data"},
            "tests": [{"files": "src/*", "tests": ["tests/"]}, {"files": "*", "command": ["x"], "tests": ["t"]}],
        }
        self.assertEqual(mutate.mutation_errors(ok), [])
        cases = [
            ("not an object", [], "'mutation' must be an object"),
            ("unknown field", dict(ok, link={}), "'mutation.link' is not a mutation field"),
            ("command text", dict(ok, command="pytest"), "'mutation.command' must be a list of text"),
            ("env", dict(ok, env={"A": 1}), "'mutation.env' must be an object"),
            ("timeout", dict(ok, timeout=0), "'mutation.timeout' = 0"),
            ("timeout bool", dict(ok, timeout=True), "'mutation.timeout' = true"),
            ("no tests", dict(ok, tests=[]), "'mutation.tests' must be a list"),
            ("entry", dict(ok, tests=[{"files": "src/*"}]), "'mutation.tests[0]' must have 'files'"),
            (
                "entry field",
                dict(ok, tests=[{"files": "a", "tests": ["t"], "env": {}}]),
                "'mutation.tests[0].env' is not an entry field",
            ),
            ("absolute", dict(ok, tests=[{"files": "a", "tests": ["/t"]}]), "is not a path inside the repository"),
            ("dotdot", dict(ok, tests=[{"files": "a", "tests": ["../t"]}]), "is not a path inside the repository"),
            ("space", dict(ok, tests=[{"files": "a", "tests": ["t x"]}]), "is not a path inside the repository"),
            ("no command", {"tests": [{"files": "a", "tests": ["t"]}]}, "has no command, and the block has none"),
            ("loads", dict(ok, loads={"files": "src/*"}), "'mutation.loads' must be"),
            ("empty argument", dict(ok, command=["python3", ""]), "'mutation.command' must be a list of text"),
            ("links not an object", dict(ok, links=["data"]), "'mutation.links' must be an object"),
            ("link outside", dict(ok, links={"data": "../data"}), "'mutation.links' must be an object"),
        ]
        for name, block, want in cases:
            with self.subTest(name):
                errs = mutate.mutation_errors(block)
                self.assertTrue(any(want in e for e in errs), errs)
        self.assertEqual(
            len(mutate.mutation_errors({"command": "pytest", "timeout": -1, "tests": [{"files": "src/*"}]})), 3
        )

    def test_the_docs_and_the_reviewer_prompt_name_the_runner(self):
        root = RUNTIME.parent
        for f in ("docs/TOOLS.md", "docs/PROJECT-TEAM.md", "agents/reviewer.md"):
            with self.subTest(f):
                self.assertIn("mutate.sh", (root / f).read_text())


if __name__ == "__main__":
    unittest.main()


class TestLinksOutsideTheCopy(Fixture):
    """Review C1: a `links` path under a committed symlink with an absolute target resolves outside the copy;
    the runner must refuse before touching it, and never delete or follow anything outside its copy."""

    def test_c1_a_links_path_under_a_committed_absolute_symlink_is_refused_and_nothing_outside_is_touched(self):
        ext = Path(self.tmp.name) / "ext"
        data = ext / "data"
        data.mkdir(parents=True)
        (data / "notes.txt").write_text("committed notes\n")
        subprocess.run(["git", "init", "-q", str(data)], check=True)
        subprocess.run(["git", "-C", str(data), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(data), "commit", "-q", "-m", "n"], check=True, env=GIT)
        (data / "draft.txt").write_text("uncommitted work in progress\n")
        (self.repo / "vendor").symlink_to(ext.resolve())  # an absolute target, committed
        self.commit("vendor link")
        self.bind(links={"vendor/data": "vendor/data"})
        before = sorted(p.name for p in data.iterdir())
        code, out = self.run_(self.mutants(KILL))
        self.assertEqual(code, 2, out)
        self.assertIn("mutation.links: vendor/data passes through vendor, a symlink in the commit", out)
        self.assertFalse(data.is_symlink())
        self.assertEqual(sorted(p.name for p in data.iterdir()), before)
        self.assertEqual((data / "draft.txt").read_text(), "uncommitted work in progress\n")
        self.assertFalse((self.rec / "mutations.json").exists(), "nothing written")
        # a links path that IS the committed symlink is only unlinked in the copy: allowed
        self.assertIsNone(mutate.link_problem(self.repo, "vendor"))
        self.assertIn("passes through vendor", mutate.link_problem(self.repo, "vendor/data"))


class TestEngineerNames(Fixture):
    """Owner decision (D6): an engineer's batch writes engineer-mutations*, so the reviewer keeps mutations*."""

    def test_d6_an_engineer_batch_writes_engineer_mutations(self):
        code, out = self.run_(self.mutants(KILL), "--by", "engineer")
        self.assertEqual(code, 0, out)
        self.assertEqual([r["id"] for r in self.rows("engineer-mutations.json")], ["M1"])
        self.assertTrue((self.rec / "engineer-mutations.log.jsonl").is_file())
        self.assertFalse((self.rec / "mutations.json").exists())
        # the reviewer's own batch in the same folder takes its own file, the same id allowed
        code, out = self.run_(self.mutants(KILL))
        self.assertEqual(code, 0, out)
        self.assertEqual([r["id"] for r in self.rows()], ["M1"])
        self.assertEqual(sorted(p.name for p in self.rec.iterdir()),
                         ["engineer-mutations.json", "engineer-mutations.log.jsonl", "mutations.json",
                          "mutations.log.jsonl"])

    def test_d6_an_engineer_fix_round_writes_engineer_mutations_fix1(self):
        code, out = self.run_(self.mutants(KILL), "--by", "engineer", round_="fix1")
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(p.name for p in self.rec.iterdir()),
                         ["engineer-mutations-fix1.json", "engineer-mutations-fix1.log.jsonl"])
