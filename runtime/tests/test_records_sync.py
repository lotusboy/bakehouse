"""Tests for 0.9.0 R1c, the records sync at fixed points (docs/PROJECT-TEAM.md, "Records sync"): after each seal,
revise and retire, after each close (which commits its own line first), at session start, and `bakehouse sync`, the
records worktree fetches its upstream, merges it and pushes, never prompting and within a bound. Records format 2
only. Every remote is a local bare repository, except the HTTP remotes of the credential tests, which are a fake
server on 127.0.0.1 in this process. Standard library only.
python3 -m unittest runtime/tests/test_records_sync.py
"""

import base64
import fcntl
import http.server
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
import test_writer_files as twf  # noqa: E402 - a module, so its tests are not collected here again

tsc = twf.tsc
RUNTIME = twf.RUNTIME
sys.path.insert(0, str(RUNTIME))
import records_sync  # noqa: E402

TOKEN = "tok-5a1t\\nOT-IN-ANY-FILE-9c2e"  # a backslash, which an `echo` in the helper would corrupt


class Base(twf.Base):
    """Machine A is test_sealed_close's scratch project (writer alpha). two() adds a bare origin and machine B, a clone
    of it with its own records worktree (writer beta)."""

    def setUp(self):
        super().setUp()
        self.env["GIT_CONFIG_NOSYSTEM"] = "1"  # the machine's own credential helper is never asked
        self.env["GIT_CONFIG_GLOBAL"] = str(Path(self.env["HOME"]) / ".gitconfig")  # this test's own global config

    def g(self, where, *args, ok=True):
        p = subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, env=self.env,
                           check=False)
        if ok:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout.strip()

    def origin_setup(self, migrate=True):
        self.origin = self.repo.parent / "origin.git"
        self.g(self.repo.parent, "init", "-q", "--bare", str(self.origin))
        (self.records / ".gitignore").write_text(twf.load("setup_sync_test", "bakehouse_setup.py").GITIGNORE)
        self.commit_records("the records branch's .gitignore, as setup writes it")
        if migrate:
            self.migrate("--writer", "alpha")
        self.git("remote", "add", "origin", str(self.origin))
        self.g(self.repo, "push", "-q", "origin", self.default, "bakehouse-records")
        self.rgit("branch", "-q", "--set-upstream-to", "origin/bakehouse-records")

    def two(self):
        self.origin_setup()
        self.b = self.repo.parent / "b"
        self.brec = self.repo.parent / "b.bakehouse-records"
        self.g(self.repo.parent, "clone", "-q", "-b", self.default, str(self.origin), str(self.b))
        self.g(self.b, "worktree", "add", "-q", "--track", "-b", "bakehouse-records", str(self.brec),
               "origin/bakehouse-records")
        self.g(self.b, "config", "bakehouse.records", str(self.brec))
        p = self.bakehouse("setup", "--writer", "beta", cwd=self.b)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def tip(self):
        return self.g(self.origin, "rev-parse", "refs/heads/bakehouse-records")

    def sealp(self, repo, wo, frm):
        """wo's prediction and baseline, committed and sealed in repo (tree mode); each work order moves its own
        values, so no two seal the same bytes."""
        folder = repo / f"runs/2026-11-10-{wo}-step1-review"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "moves.prediction.json").write_text(json.dumps({"moves": [{"what": "out:v", "from": frm,
                                                                             "to": frm + 1}]}))
        (folder / "moves.baseline.json").write_text(json.dumps({"out:v": frm}))
        self.g(repo, "add", "-A")
        self.g(repo, "commit", "-q", "-m", f"{wo}: prediction")
        outs = []
        for n in ("moves.prediction.json", "moves.baseline.json"):
            p = self.run_([sys.executable, self.rt / "predict-gate.py", "seal", wo, "first", folder / n], cwd=repo)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            outs.append(p.stdout + p.stderr)
        return "".join(outs)

    def session_start(self, repo):
        p = self.run_([sys.executable, self.rt / "session-start.py"], cwd=repo,
                      env={"CLAUDE_PROJECT_DIR": str(repo)})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout + p.stderr

    def status_of(self, repo):
        p = self.bakehouse("status", "--json", "--repo", repo)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads(p.stdout)

    def sync(self, repo, rc=0):
        p = self.bakehouse("sync", "--repo", repo)
        self.assertEqual(p.returncode, rc, p.stdout + p.stderr)
        return p.stdout + p.stderr

    def local_commit(self, rec, name, text):
        (rec / name).parent.mkdir(parents=True, exist_ok=True)
        (rec / name).write_text(text)
        self.g(rec, "add", "--", name)
        self.g(rec, "commit", "-q", "-m", f"records: {name}", "--", name)


for _n in [n for n in dir(twf.Base) if n.startswith("test")]:
    setattr(Base, _n, None)  # not callable: the loader skips the base classes' tests


class BetweenMachines(Base):
    """Tests 1 and 2: a seal, and a close, reach the other machine at its next sync."""

    def test_a_seal_reaches_the_other_machine_at_its_session_start_and_back(self):
        self.two()
        out = self.sealp(self.repo, "W-1", 1)
        self.assertNotIn("bakehouse sync", out)  # level: a fixed point says nothing
        self.assertEqual(self.tip(), self.rgit("rev-parse", "HEAD"))
        self.assertEqual(self.session_start(self.b).count("bakehouse sync"), 0)
        self.assertEqual([w["workOrder"] for w in self.status_of(self.b)["workOrders"]], ["W-1"])
        self.sealp(self.b, "W-2", 10)
        self.session_start(self.repo)
        self.assertEqual(sorted(w["workOrder"] for w in self.status_of(self.repo)["workOrders"]), ["W-1", "W-2"])
        self.assertEqual(self.status_of(self.repo)["sync"],
                         {"upstream": "origin/bakehouse-records", "waiting": 0, "lastFailure": None})

    def test_a_close_commits_its_own_line_and_the_other_machine_sees_it_after_its_sync(self):
        self.two()
        out = twf.work_order(self, self.repo, "W-1", "src/a.py", "a = 1\n", "2026-11-10T09:30:00+00:00")
        self.assertIn("Result: CLOSED", out)
        self.assertEqual(self.rgit("status", "--porcelain", "--", "ops/events"), "")  # the close line is committed
        msg = self.rgit("log", "-1", "--format=%B")
        self.assertIn("work-order-closed W-1", msg)
        self.assertRegex(msg, r"Closed-line: [0-9a-f]{64}")
        self.assertEqual(self.tip(), self.rgit("rev-parse", "HEAD"))  # and synced
        self.assertEqual(self.status_of(self.b)["closedSince"], [])
        self.sync(self.b)
        self.assertEqual([c["workOrder"] for c in self.status_of(self.b)["closedSince"]], ["W-1"])
        self.assertEqual(self.status_of(self.b)["workOrders"], [])


class Refused(Base):
    """Test 3: a push refused because the remote moved is fetched, merged and pushed once more."""

    def test_the_remote_moves_between_fetch_and_push_and_sync_pushes_again(self):
        self.two()
        self.local_commit(self.records, "notes/alpha.txt", "a\n")  # A's commit, not pushed yet
        self.local_commit(self.brec, "notes/beta.txt", "b\n")
        hooks = Path(self.g(self.b, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        flag = self.repo.parent / "moved"
        (hooks / "pre-push").write_text(  # the first push from B: A pushes first, so B's push is not a fast forward
            "#!/bin/sh\nunset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE\n"
            f"[ -e '{flag}' ] && exit 0\n: > '{flag}'\n"
            f"git -C '{self.records}' push -q origin bakehouse-records\n")
        (hooks / "pre-push").chmod(0o755)
        out = self.sync(self.b)
        self.assertTrue(flag.exists())
        self.assertIn("level with origin/bakehouse-records", out)
        self.sync(self.repo)
        heads = {self.tip(), self.rgit("rev-parse", "HEAD"), self.g(self.brec, "rev-parse", "HEAD")}
        self.assertEqual(len(heads), 1, heads)
        for rec in (self.records, self.brec):
            self.assertTrue((rec / "notes/alpha.txt").exists() and (rec / "notes/beta.txt").exists(), rec)


    def test_a_push_still_refused_after_the_retry_is_one_refused_line(self):
        self.two()
        self.local_commit(self.brec, "notes/beta.txt", "b\n")
        hooks = Path(self.g(self.b, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        count = self.repo.parent / "moves"
        (hooks / "pre-push").write_text(  # every push from B: A commits and pushes first, so the retry is refused too
            "#!/bin/sh\nunset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE\n"
            f"echo x >> '{count}'\n"
            f"date +%s%N > '{self.records}/notes/moving.txt'\n"
            f"git -C '{self.records}' add -- notes/moving.txt && "
            f"git -C '{self.records}' commit -q -m move -- notes/moving.txt && "
            f"git -C '{self.records}' push -q origin bakehouse-records\n")
        (hooks / "pre-push").chmod(0o755)
        (self.records / "notes").mkdir(exist_ok=True)
        out = self.sync(self.b, rc=1)
        self.assertEqual(len(count.read_text().split()), 2)  # the push and its one retry
        self.assertEqual(len(out.strip().splitlines()), 1, out)
        self.assertIn("bakehouse sync: refused: push to origin", out)
        self.assertIn("stay local", out)


class Offline(Base):
    """Test 4: an unreachable remote: the seal still succeeds, the commit stays local, one line names the failure;
    the next sync pushes it; status shows the waiting count, then 0."""

    def test_a_seal_offline_stays_local_and_the_next_sync_pushes_it(self):
        self.origin_setup()
        away = self.origin.with_name("away.git")
        self.origin.rename(away)
        out = self.sealp(self.repo, "W-1", 1)  # exit 0, asserted inside
        lines = [x for x in out.splitlines() if x.startswith("bakehouse sync:")]
        self.assertEqual(len(lines), 2, out)  # one line for each of the two seals
        self.assertIn("bakehouse sync: offline: fetch to origin", lines[0])
        self.assertIn("stay local", lines[0])
        s = self.status_of(self.repo)["sync"]
        self.assertGreaterEqual(s["waiting"], 2)
        self.assertIn("offline", s["lastFailure"]["line"])
        self.assertRegex(s["lastFailure"]["at"], r"^\d{4}-\d\d-\d\dT")
        p = self.bakehouse("status", "--repo", self.repo)
        self.assertIn(f"records sync: {s['waiting']} commit(s) waiting to be pushed to origin/bakehouse-records", p.stdout)
        away.rename(self.origin)
        self.assertIn("level with origin/bakehouse-records", self.sync(self.repo))
        self.assertEqual(self.status_of(self.repo)["sync"],
                         {"upstream": "origin/bakehouse-records", "waiting": 0, "lastFailure": None})
        self.assertNotIn("records sync:", self.bakehouse("status", "--repo", self.repo).stdout)
        self.assertEqual(self.tip(), self.rgit("rev-parse", "HEAD"))


    def test_a_failing_pull_with_nothing_waiting_shows_in_plain_status(self):
        self.origin_setup()
        self.origin.rename(self.origin.with_name("away.git"))
        self.sync(self.repo, rc=1)
        s = self.status_of(self.repo)["sync"]
        self.assertEqual(s["waiting"], 0)
        plain = self.bakehouse("status", "--repo", self.repo).stdout
        self.assertIn("records sync: 0 commit(s) waiting to be pushed to origin/bakehouse-records; last failure at", plain)
        self.assertIn("offline", plain)


class SessionStart(Base):
    """Session start with a failing sync, and with the lock held: exit 0 either way."""

    def test_a_failing_sync_at_session_start_is_one_line_and_exit_0(self):
        self.origin_setup()
        self.origin.rename(self.origin.with_name("away.git"))
        out = self.session_start(self.repo)  # exit 0, asserted inside
        lines = [x for x in out.splitlines() if x.startswith("bakehouse sync:")]
        self.assertEqual(len(lines), 1, out)
        self.assertIn("bakehouse sync: offline", lines[0])

    def test_a_held_lock_at_session_start_skips_and_says_nothing(self):
        self.origin_setup()
        self.local_commit(self.records, "notes/a.txt", "a\n")
        before = self.tip()
        lock = Path(self.rgit("rev-parse", "--absolute-git-dir")) / records_sync.LOCK
        with lock.open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            self.assertNotIn("bakehouse sync", self.session_start(self.repo))
        self.assertEqual(self.tip(), before)


class SealCode(Base):
    """seal-code syncs after its anchor, as seal does."""

    def test_seal_code_syncs_after_its_anchor(self):
        self.origin_setup()
        (self.repo / "src").mkdir(exist_ok=True)
        (self.repo / "src" / "x.py").write_text("x = 1\n")
        self.git("add", "-A")
        p = self.run_([sys.executable, self.rt / "predict-gate.py", "seal-code", "W-1", "first", "--", "-q", "-m",
                       "W-1: the change"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("code commit", self.rgit("log", "-1", "--format=%s"))
        self.assertEqual(self.tip(), self.rgit("rev-parse", "HEAD"))


class Bound(Base):
    """The bound: a remote that never answers, with a child that leaves git's process group holding its stderr, is
    stopped within the bound, and the line says offline."""

    def test_a_silent_remote_is_stopped_within_the_bound(self):
        self.origin_setup()
        pidfile = self.repo.parent / "silent.pid"
        script = self.repo.parent / "silent.sh"
        script.write_text("#!/bin/sh\n"
                          "python3 -c 'import os, sys, time; os.setsid(); open(sys.argv[1], \"w\").write(str(os.getpid())); "
                          f"time.sleep(60)' '{pidfile}' &\nsleep 60\n")
        script.chmod(0o755)
        self.addCleanup(_kill, pidfile)
        self.rgit("config", "protocol.ext.allow", "always")
        self.git("remote", "set-url", "origin", f"ext::{script}")
        t = time.monotonic()
        out = self.sync(self.repo, rc=1)
        took = time.monotonic() - t
        self.assertLess(took, records_sync.BOUND + 8, out)
        self.assertGreaterEqual(took, records_sync.BOUND - 1, out)
        self.assertIn(f"bakehouse sync: offline: fetch to origin had no answer within {records_sync.BOUND} seconds", out)


def _kill(pidfile):
    try:  # the child that left git's process group
        os.kill(int(pidfile.read_text()), 9)
    except (ValueError, OSError):
        pass


class FakeHttp:
    """A fake HTTP git remote on 127.0.0.1: every request without the expected Basic credentials gets 401 with a
    challenge; with them, 404. It keeps each request's Authorization header."""

    def __init__(self, user=None, password=None):
        want = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode() if user else None
        seen = self.seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                if want and self.headers.get("Authorization") == want:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="records"')
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_POST = do_GET

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/records.git"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Credentials(Base):
    """Test 5 and the owner's token ruling (H): a remote that asks for a login fails at once with the "no credential"
    line, with no prompt; a token the user gives setup reaches git's credential request and is never stored or
    printed."""

    def helper_log(self):
        """A credential helper in the scratch HOME's global git config, as a machine's own helper: it logs each call."""
        log = Path(self.env["HOME"]) / "helper.log"
        self.g(self.repo, "config", "--global", "credential.helper", f"!f() {{ echo \"$1\" >> '{log}'; }}; f")
        return log

    def http_origin(self, server):
        self.origin_setup()
        self.git("remote", "set-url", "origin", server.url)
        self.addCleanup(server.close)

    def test_a_remote_that_asks_for_a_login_fails_at_once_with_no_prompt(self):
        server = FakeHttp()
        self.http_origin(server)
        log = self.helper_log()
        t = time.monotonic()
        out = self.sync(self.repo, rc=1)
        self.assertLess(time.monotonic() - t, 10)
        self.assertIn("bakehouse sync: no credential: fetch to origin", out)
        self.assertEqual(len(out.strip().splitlines()), 1, out)
        self.assertTrue(server.seen)  # it reached the remote
        self.assertIn("get", log.read_text().split())  # with no token set up, git's own login is used, as before
        env = self.network_env()
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertIn("BatchMode=yes", env["GIT_SSH_COMMAND"])

    def network_env(self, **extra):
        with mock.patch.dict(os.environ, dict(self.env, **extra), clear=True):  # the scratch HOME, as the tools see it
            return records_sync.network_env(self.records)

    def test_an_ssh_command_the_owner_set_is_left_alone(self):
        self.assertEqual(self.network_env(GIT_SSH_COMMAND="ssh -i mine")["GIT_SSH_COMMAND"], "ssh -i mine")
        self.rgit("config", "core.sshCommand", "ssh -i key")
        self.assertNotIn("GIT_SSH_COMMAND", self.network_env())

    def test_a_token_reaches_the_credential_request_and_is_never_stored_or_printed(self):
        server = FakeHttp("records-bot", TOKEN)
        self.http_origin(server)
        log = self.helper_log()
        tf = Path(self.env["HOME"]) / "secrets.env"
        tf.write_text(f"# the user's own file\nOTHER=x\nexport RECORDS_TOKEN='{TOKEN}'\n")
        p = self.bakehouse("setup", "--token-file", tf, "--token-key", "RECORDS_TOKEN", "--token-user", "records-bot")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        printed = p.stdout + p.stderr
        self.assertEqual(self.rgit("config", "--local", "--get", "bakehouse.tokenKey"), "RECORDS_TOKEN")
        self.assertEqual(self.rgit("config", "--local", "--get", "bakehouse.tokenFile"), str(tf))
        printed += self.sync(self.repo, rc=1)  # the fake answers 404 once the login is right
        want = "Basic " + base64.b64encode(f"records-bot:{TOKEN}".encode()).decode()
        self.assertIn(want, server.seen)
        self.assertFalse(log.exists(), "the machine's own credential helper was asked, so it could store the token")
        printed += self.bakehouse("status", "--json", "--repo", self.repo).stdout
        printed += self.bakehouse("status", "--repo", self.repo).stdout
        common = Path(self.git("rev-parse", "--path-format=absolute", "--git-common-dir"))
        files = [common / "config", *[f for f in self.records.rglob("*") if f.is_file()],
                 *[f for f in Path(self.rgit("rev-parse", "--absolute-git-dir")).glob("bakehouse-*")],
                 Path(self.env["HOME"]) / ".gitconfig"]
        for f in files:
            self.assertNotIn(TOKEN, f.read_text(errors="replace"), f)
        self.assertNotIn(TOKEN, printed)
        self.assertNotIn(TOKEN, self.status_of(self.repo)["sync"]["lastFailure"]["line"])

    def test_a_token_file_without_the_key_is_one_failure_line_and_the_sync_goes_on(self):
        self.origin_setup()
        tf = Path(self.env["HOME"]) / "secrets.env"
        tf.write_text("OTHER=x\nRECORDS_TOKEN=\n")
        p = self.bakehouse("setup", "--token-file", tf, "--token-key", "RECORDS_TOKEN", "--token-user", "bot")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("has no RECORDS_TOKEN", p.stdout)
        self.local_commit(self.records, "notes/a.txt", "a\n")
        out = self.sync(self.repo, rc=1)
        self.assertEqual(out.strip().splitlines(), [
            f"bakehouse sync: no credential: token file {tf} has no RECORDS_TOKEN; synced without it. The records "
            "are level with the remote all the same."])
        self.assertEqual(self.tip(), self.rgit("rev-parse", "HEAD"))  # the local remote needed no token: pushed

    def test_an_askpass_program_is_never_called(self):
        server = FakeHttp()
        self.http_origin(server)
        called = self.repo.parent / "askpass-called"
        ap = self.repo.parent / "askpass.sh"
        ap.write_text(f"#!/bin/sh\n: > '{called}'\nsleep 30\necho x\n")
        ap.chmod(0o755)
        self.rgit("config", "core.askPass", str(ap))
        t = time.monotonic()
        p = self.run_([sys.executable, twf.BAKEHOUSE, "sync", "--repo", self.repo],
                      env={"GIT_ASKPASS": str(ap), "SSH_ASKPASS": str(ap)})
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertLess(time.monotonic() - t, 10)
        self.assertIn("bakehouse sync: no credential: fetch to origin", p.stderr)
        self.assertFalse(called.exists())

    def test_the_token_is_never_in_argv(self):
        self.origin_setup()
        tf = Path(self.env["HOME"]) / "secrets.env"
        tf.write_text(f"RECORDS_TOKEN='{TOKEN}'\n")
        p = self.bakehouse("setup", "--token-file", tf, "--token-key", "RECORDS_TOKEN", "--token-user", "bot")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with mock.patch.dict(os.environ, self.env, clear=True):
            args, extra, problem = records_sync.token(self.records)
        self.assertIsNone(problem)
        self.assertEqual(extra, {records_sync.TOKEN_ENV: TOKEN, records_sync.USER_ENV: "bot"})
        self.assertEqual(args, ["-c", "credential.helper=", "-c", f"credential.helper={records_sync.HELPER}"])
        self.assertNotIn(TOKEN, " ".join(args))
        self.assertIn(f'"${records_sync.TOKEN_ENV}"', records_sync.HELPER)  # the helper names the variable only

    def test_a_token_file_inside_the_records_worktree_is_refused(self):
        tf = self.records / "secrets.env"
        tf.write_text(f"RECORDS_TOKEN={TOKEN}\n")
        p = self.bakehouse("setup", "--token-file", tf, "--token-key", "RECORDS_TOKEN", "--token-user", "bot")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("inside the records worktree", p.stdout)
        self.assertEqual(self.rgit("config", "--local", "--get", "bakehouse.tokenFile"), "")

    def test_the_token_options_go_together(self):
        p = self.bakehouse("setup", "--token-file", "f", "--token-key", "K")
        self.assertEqual(p.returncode, 2, p.stdout)
        self.assertEqual(self.rgit("config", "--local", "--get", "bakehouse.tokenFile"), "")


class Conflict(Base):
    """Test 6: a forced merge conflict (a test-only file both machines commit): the merge is aborted, the local
    commits kept, the file named."""

    def test_a_merge_conflict_is_aborted_and_named_and_the_local_commits_stay(self):
        self.two()
        self.local_commit(self.records, "shared.txt", "alpha\n")
        self.sync(self.repo)
        self.local_commit(self.brec, "shared.txt", "beta\n")
        head = self.g(self.brec, "rev-parse", "HEAD")
        out = self.sync(self.b, rc=1)
        self.assertIn("bakehouse sync: conflict: merging origin/bakehouse-records conflicts in shared.txt", out)
        self.assertEqual(self.g(self.brec, "rev-parse", "HEAD"), head)
        self.assertEqual(self.g(self.brec, "rev-parse", "-q", "--verify", "MERGE_HEAD", ok=False), "")
        self.assertEqual(self.g(self.brec, "status", "--porcelain", "--untracked-files=no"), "")
        self.assertEqual((self.brec / "shared.txt").read_text(), "beta\n")
        self.assertIn("conflict", self.status_of(self.b)["sync"]["lastFailure"]["line"])


    def test_staged_changes_that_block_the_merge_are_named(self):
        self.two()
        self.local_commit(self.records, "notes/alpha.txt", "a\n")
        self.sync(self.repo)
        self.local_commit(self.brec, "notes/beta.txt", "b\n")  # not a fast forward: a merge commit is needed
        (self.brec / "notes" / "staged.txt").write_text("s\n")
        self.g(self.brec, "add", "--", "notes/staged.txt")
        out = self.sync(self.b, rc=1)
        self.assertIn("notes/staged.txt", out)
        self.assertIn("bakehouse sync: conflict", out)

    def test_a_merge_commit_never_asks_for_a_signature(self):
        self.two()
        self.local_commit(self.records, "notes/alpha.txt", "a\n")
        self.sync(self.repo)
        self.local_commit(self.brec, "notes/beta.txt", "b\n")
        asked = self.repo.parent / "gpg-asked"
        gpg = self.repo.parent / "gpg.sh"
        gpg.write_text(f"#!/bin/sh\n: > '{asked}'\nexit 1\n")
        gpg.chmod(0o755)
        self.g(self.brec, "config", "commit.gpgsign", "true")
        self.g(self.brec, "config", "gpg.program", str(gpg))
        self.assertIn("level with", self.sync(self.b))
        self.assertFalse(asked.exists())
        self.assertEqual(self.g(self.brec, "rev-list", "--count", "--merges", "HEAD"), "1")


class WriterLock(Base):
    """F1: a seal during a conflicted sync waits for the merge and its abort: it loses no line and does not fail."""

    def test_a_seal_during_a_conflicted_sync_loses_no_line_and_does_not_fail(self):
        self.two()
        self.local_commit(self.records, "shared.txt", "alpha\n")
        self.sync(self.repo)
        self.local_commit(self.brec, "shared.txt", "beta\n")
        note = self.run_([self.rt / "ops-event.sh", "note", "-", "an uncommitted note"], env={"OPS_REPO": str(self.b)})
        self.assertEqual(note.returncode, 0, note.stderr)
        folder = self.b / "runs/2026-11-10-W-2-step1-review"
        folder.mkdir(parents=True)
        (folder / "moves.prediction.json").write_text(json.dumps({"moves": [{"what": "out:v", "from": 7, "to": 8}]}))
        self.g(self.b, "add", "-A")
        self.g(self.b, "commit", "-q", "-m", "W-2: prediction")
        real, seal = records_sync.git, []

        def git(records, *args, **kw):
            r = real(records, *args, **kw)
            if "merge" in args and "--abort" not in args and r.returncode and not seal:
                seal.append(subprocess.Popen(
                    [sys.executable, str(self.rt / "predict-gate.py"), "seal", "W-2", "first",
                     str(folder / "moves.prediction.json")], cwd=self.b, env=self.env, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE))
                time.sleep(2)  # the seal runs now, mid-merge, unless the merge holds the writer's lock
            return r

        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(records_sync, "git", git):
            r = records_sync.sync(self.brec)
        self.assertEqual(r.state, "failed")
        self.assertIn("conflict", r.line)
        out, err = seal[0].communicate(timeout=60)
        self.assertEqual(seal[0].returncode, 0, out + err)
        rows = [json.loads(x) for x in (self.brec / "ops/events/beta.jsonl").read_text().splitlines()]
        self.assertEqual([e["kind"] for e in rows], ["note", "sealed"])
        held = self.g(self.brec, "show", "HEAD:ops/events/beta.jsonl")
        self.assertIn("an uncommitted note", held)
        self.assertIn('"sealed"', held)
        self.assertEqual(self.g(self.brec, "rev-parse", "-q", "--verify", "MERGE_HEAD", ok=False), "")


class Upstream(Base):
    """F3: with no upstream, origin is used only when it already holds the records branch: an orphan records branch
    is never published by a sync."""

    def test_an_orphan_records_branch_is_never_pushed(self):
        self.migrate("--writer", "alpha")
        origin = self.repo.parent / "origin.git"
        self.g(self.repo.parent, "init", "-q", "--bare", str(origin))
        self.git("remote", "add", "origin", str(origin))
        self.assertEqual(self.sync(self.repo), "")
        out = self.sealp(self.repo, "W-1", 1)
        self.assertNotIn("bakehouse sync", out)
        self.assertEqual(self.g(origin, "branch", "--list"), "")
        self.assertIsNone(self.status_of(self.repo)["sync"]["upstream"])


class OneAtATime(Base):
    """Test 7: while one sync holds the lock, another skips: `bakehouse sync` says so; a seal says nothing."""

    def test_a_second_sync_skips_while_the_first_holds_the_lock(self):
        self.origin_setup()
        lock = Path(self.rgit("rev-parse", "--absolute-git-dir")) / records_sync.LOCK
        before = self.tip()
        with lock.open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            out = self.sealp(self.repo, "W-1", 1)
            self.assertNotIn("bakehouse sync", out)
            self.assertEqual(self.tip(), before)  # skipped: nothing pushed
            self.assertIn("another sync of these records is running; skipped", self.sync(self.repo))
            self.assertEqual(self.tip(), before)
        self.sync(self.repo)
        self.assertEqual(self.tip(), self.rgit("rev-parse", "HEAD"))


class Register(Base):
    """Test 8: registers/records.json is not committed after migration; records-check still writes it; a project
    migrated under 0.8.0 is cleaned by its first sync."""

    def records_check(self):
        p = self.run_([sys.executable, self.rt / "records-check.py", "--repo", self.repo, "--as-of", "2026-11-20",
                       "--since", "2026-11-01"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_migration_stops_committing_the_register_and_records_check_still_writes_it(self):
        reg = self.records / records_sync.REGISTER
        self.records_check()
        self.commit_records()  # format 1: the register is committed, as before
        self.assertEqual(self.rgit("ls-files", "--", records_sync.REGISTER), records_sync.REGISTER)
        self.migrate("--writer", "alpha")
        self.assertEqual(self.rgit("ls-files", "--", records_sync.REGISTER), "")
        self.assertIn(records_sync.REGISTER, (self.records / ".gitignore").read_text().splitlines())
        self.assertTrue(reg.is_file())
        reg.unlink()
        self.records_check()
        self.assertTrue(reg.is_file())
        self.assertEqual(self.rgit("status", "--porcelain"), "")
        self.assertEqual(self.rgit("ls-files", "--", records_sync.REGISTER), "")

    def test_a_project_migrated_under_0_8_0_is_cleaned_by_its_first_sync(self):
        self.migrate("--writer", "alpha")
        # what 0.8.0's migration left: no ignore line, the register committed
        (self.records / ".gitignore").write_text(twf.load("setup_sync_test", "bakehouse_setup.py").GITIGNORE)
        self.records_check()
        self.rgit("add", "-f", "--", ".gitignore", records_sync.REGISTER)
        self.rgit("commit", "-q", "-m", "0.8.0 records")
        self.assertEqual(self.rgit("ls-files", "--", records_sync.REGISTER), records_sync.REGISTER)
        self.assertEqual(self.sync(self.repo), "")  # no remote: nothing is said
        self.assertEqual(self.rgit("ls-files", "--", records_sync.REGISTER), "")
        self.assertTrue((self.records / records_sync.REGISTER).is_file())
        self.assertEqual(self.rgit("status", "--porcelain"), "")
        self.assertIn("is derived, so it is not committed", self.rgit("log", "-1", "--format=%s"))

    def test_a_failed_migration_puts_the_register_back(self):
        self.records_check()
        self.commit_records()
        hooks = Path(self.git("rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
        (hooks / "pre-commit").chmod(0o755)
        head = self.rgit("rev-parse", "HEAD")
        p = self.migrate("--writer", "alpha", ok=False)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("Nothing migrated", p.stdout)
        self.assertEqual(self.rgit("rev-parse", "HEAD"), head)
        self.assertEqual(self.rgit("ls-files", "--", records_sync.REGISTER), records_sync.REGISTER)
        self.assertEqual(self.rgit("status", "--porcelain"), "")


    def test_a_failed_format_commit_takes_the_register_commit_back(self):
        self.records_check()
        self.commit_records()
        hooks = Path(self.git("rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "pre-commit").write_text(  # only the FORMAT commit fails: the register's commit goes through
            "#!/bin/sh\ngit diff --cached --name-only | grep -qx FORMAT && exit 1\nexit 0\n")
        (hooks / "pre-commit").chmod(0o755)
        head = self.rgit("rev-parse", "HEAD")
        gi = (self.records / ".gitignore").read_bytes() if (self.records / ".gitignore").exists() else None
        p = self.migrate("--writer", "alpha", ok=False)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("Nothing migrated", p.stdout)
        self.assertEqual(self.rgit("rev-parse", "HEAD"), head)
        self.assertIn("is derived, so it is not committed", self.rgit("reflog", "-3", "--format=%gs"))  # it was made
        self.assertEqual(self.rgit("ls-files", "--", records_sync.REGISTER), records_sync.REGISTER)
        self.assertEqual((self.records / ".gitignore").read_bytes() if (self.records / ".gitignore").exists() else None,
                         gi)
        self.assertFalse((self.records / "FORMAT").exists())
        self.assertEqual(self.rgit("status", "--porcelain"), "")


class FormatOne(Base):
    """Test 9: format 1: nothing fetches, merges or pushes; a close commits nothing of its own."""

    def test_format_1_never_syncs(self):
        self.origin_setup(migrate=False)
        before = self.tip()
        out = self.sealp(self.repo, "W-1", 1)
        self.assertNotIn("bakehouse sync", out)
        self.assertEqual(self.tip(), before)
        self.assertEqual(self.sync(self.repo), "")
        self.assertEqual(self.tip(), before)
        self.assertNotIn("bakehouse sync", self.session_start(self.repo))
        self.assertEqual(self.tip(), before)
        self.assertIsNone(self.status_of(self.repo)["sync"])
        self.assertFalse((self.records / "FORMAT").exists())

    def test_format_1_close_commits_nothing_of_its_own(self):
        self.flow()
        head = self.rgit("rev-parse", "HEAD")
        self.assertCloses()
        self.assertEqual(self.rgit("rev-parse", "HEAD"), head)


if __name__ == "__main__":
    unittest.main()
