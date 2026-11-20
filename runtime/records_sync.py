"""Records sync (records format 2, bakehouse 0.9.0): move the records branch between machines at fixed points.
Standard library only. docs/PROJECT-TEAM.md, "Records sync", is the reference.

  bakehouse sync [--repo <folder>]   (default folder: the current one)

sync(records) is the one sync step every caller shares: after a seal, revise or retire (predict-gate.py, after the
anchor commit), after a close (which commits its own line first), at session start (session-start.py), and
`bakehouse sync` (the PM's records commit runs it). In the records worktree it:

1. takes the sync lock, a file in the records worktree's git folder; when another sync holds it, it skips. Lanes on
   one machine share the records worktree, so they share the lock;
2. untracks the derived register `registers/records.json` once (it is rewritten on each machine, so it is never
   committed in format 2): adds it to .gitignore and removes it from git, in one commit;
3. finds the upstream: the branch's own upstream (git config branch.bakehouse-records.remote and .merge), else the
   remote `origin` with the branch's own name (setup's worktree from origin/bakehouse-records may have none set);
   neither: it does nothing and says nothing;
4. fetches that branch, merges it (a merge, never a rebase: writer files never overlap) and pushes; a push refused
   because the remote moved is fetched, merged and pushed once more; a merge with a conflict is aborted, the local
   commits kept, the files named.

A token the user provides: `bakehouse setup --token-file <path> --token-key <NAME> --token-user <name>` stores the three
values (a path and two names, never the token) in the records worktree's local git config (TOKEN_CONFIG). At each sync
the token is read from that KEY=VALUE file and given to git for each network command only, through an inline
credential helper that reads it from the child's environment (never argv), after an empty credential.helper that
clears the helpers git is configured with, so no other helper stores it. A file, key or value that is missing is one
failure line naming the file and key; the sync goes on without the token. With the three unset, git uses whatever
login it already has.

It never prompts (GIT_TERMINAL_PROMPT=0; for SSH, BatchMode=yes and a connect timeout, unless the owner set
GIT_SSH_COMMAND, GIT_SSH or core.sshCommand) and its network steps share one bound, BOUND seconds. A failure leaves
the commits local and returns one line naming why (offline, refused, no credential, conflict, failed); the last one,
with its time, is kept in the records worktree's git folder for `bakehouse status`, and cleared by a sync that
ends level. Format 1 (no FORMAT file): nothing is fetched, merged or pushed.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import locator  # noqa: E402
import ops_ledger  # noqa: E402

try:
    import fcntl
except ImportError:  # no fcntl on native Windows: no lock, as ops_ledger
    fcntl = None

BOUND = 20  # seconds: every network step of one sync shares it
BRANCH = locator.RECORDS_BRANCH
REGISTER = "registers/records.json"
IGNORE = ("# The records register, rewritten by records-check on each machine: not committed in records format 2.\n"
          f"{REGISTER}\n")
LOCK = "bakehouse-sync.lock"
FAILURE = "bakehouse-sync-failure.json"
USAGE = "usage: bakehouse sync [--repo <folder>]"
TOKEN_CONFIG = ("bakehouse.tokenFile", "bakehouse.tokenKey", "bakehouse.tokenUser")
TOKEN_ENV, USER_ENV = "BAKEHOUSE_SYNC_TOKEN", "BAKEHOUSE_SYNC_USER"
# the helper names the variables only: the token is in the child's environment, never in argv
HELPER = (f'!f() {{ test "$1" = get || exit 0; printf "username=%s\\npassword=%s\\n" "${USER_ENV}" "${TOKEN_ENV}"; }}; f')
# the local steps (untrack's commit, the merge) never wait for a passphrase or stash writer lines away
LOCAL = ("-c", "commit.gpgsign=false", "-c", "merge.autoStash=false")


class Result(NamedTuple):
    state: str  # "level", "none" (format 1, or no upstream and no remote), "skipped" (locked), "failed"
    line: str | None  # what to print: the failure line; for "level" and "skipped", bakehouse sync's own line


def git(records, *args, env=None, timeout=None):
    """git in the records worktree. With timeout (a network step): the whole process group is stopped when it runs
    out, so an ssh child does not outlive it; returncode None then."""
    p = subprocess.Popen(["git", "-C", str(records), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         stdin=subprocess.DEVNULL, text=True, env=env, start_new_session=timeout is not None)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        for sig, wait in ((signal.SIGTERM, 1), (signal.SIGKILL, None)):  # TERM first: git removes its lock files
            try:
                os.killpg(p.pid, sig)
            except OSError:
                pass
            try:
                p.wait(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                continue
        try:  # a child that left the group may still hold the pipes: wait a moment, then let them go
            p.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            for f in (p.stdout, p.stderr):
                f.close()
        return subprocess.CompletedProcess(p.args, None, "", "")
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


def git_dir(records) -> Path:
    return Path(git(records, "rev-parse", "--absolute-git-dir").stdout.strip())


def upstream(records) -> tuple[str, str] | None:
    """(remote, branch on the remote) for the records branch: its upstream when git config names one, else origin
    and the branch's own name when origin already holds that branch (refs/remotes/origin/bakehouse-records exists);
    None otherwise. The sync never publishes a records branch the remote does not have: the first publish is the
    owner's own `git push -u origin bakehouse-records`."""
    def cfg(key):
        return git(records, "config", "--get", key).stdout.strip()

    remote, merge = cfg(f"branch.{BRANCH}.remote"), cfg(f"branch.{BRANCH}.merge")
    if remote and remote != "." and merge.startswith("refs/heads/"):
        return remote, merge[len("refs/heads/"):]
    if cfg("remote.origin.url") and git(records, "rev-parse", "-q", "--verify", tracking("origin", BRANCH)).returncode == 0:
        return "origin", BRANCH
    return None


def tracking(remote: str, branch: str) -> str:
    return f"refs/remotes/{remote}/{branch}"


def network_env(records) -> dict:
    # GIT_ASKPASS empty: no askpass program runs (it also stops core.askPass and SSH_ASKPASS)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="", GCM_INTERACTIVE="never")
    if not (env.get("GIT_SSH_COMMAND") or env.get("GIT_SSH") or git(records, "config", "--get", "core.sshCommand").stdout.strip()):
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o ConnectTimeout=10"
    return env


def token_file_value(path: Path, key: str) -> str | None:
    """The value of key in the user's KEY=VALUE file (a line may start with `export `; `#` lines are comments; the
    last line for the key wins; one pair of matching quotes is taken off), or None. OSError: the file cannot be read."""
    value = None
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").lstrip()
        name, eq, v = line.partition("=")
        if eq and name.strip() == key:
            v = v.strip()
            value = v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"" else v
    return value


def token(records) -> tuple[list, dict, str | None]:
    """(git -c args, environment additions, the failure text) for the token the user set up; ([], {}, None) when the
    three settings are unset. The failure text names the file and key, never the value."""
    path, key, user = (git(records, "config", "--local", "--get", k).stdout.strip() for k in TOKEN_CONFIG)
    if not (path or key or user):
        return [], {}, None
    if not (path and key and user):
        return [], {}, (f"failed: the token settings ({', '.join(TOKEN_CONFIG)}) are not all set: run "
                        "`bakehouse setup --token-file <path> --token-key <NAME> --token-user <name>`; synced without it")
    try:
        value = token_file_value(Path(path), key)
    except OSError as e:
        return [], {}, f"no credential: token file {path} cannot be read ({e.strerror}); synced without it"
    if not value:
        return [], {}, f"no credential: token file {path} has no {key}; synced without it"
    return (["-c", "credential.helper=", "-c", f"credential.helper={HELPER}"], {TOKEN_ENV: value, USER_ENV: user},
            None)


CREDENTIAL = ("could not read username", "could not read password", "terminal prompts disabled",
              "authentication failed", "permission denied", "host key verification failed", "invalid credentials",
              "invalid username or password", "returned error: 403")
REFUSED = ("[rejected]", "[remote rejected]", "non-fast-forward", "fetch first", "declined")
OFFLINE = ("could not resolve host", "connection refused", "network is unreachable", "timed out",
           "does not appear to be a git repository", "could not read from remote repository", "unable to access",
           "no route to host", "connection reset", "failed to connect")


def detail(err: str) -> str:
    lines = [x.strip() for x in err.splitlines() if x.strip()]
    pick = next((x for x in lines if x.lower().startswith(("fatal:", "error:", "!"))), lines[-1] if lines else "")
    return pick[:200]


def why(p, remote: str, step: str) -> str:
    """The failure line's reason and detail for a failed network step."""
    if p.returncode is None:
        return f"offline: {step} to {remote} had no answer within {BOUND} seconds"
    low = p.stderr.lower()
    for words, reason in ((CREDENTIAL, "no credential"), (REFUSED, "refused"), (OFFLINE, "offline")):
        if any(w in low for w in words):
            return f"{reason}: {step} to {remote}: {detail(p.stderr)}"
    return f"failed: {step} to {remote}: {detail(p.stderr)}"


def failed(records, text: str, level: bool = False) -> Result:
    line = f"bakehouse sync: {text}. " + ("The records are level with the remote all the same." if level else
                                          "The records commits stay local; the next sync tries again.")
    try:
        (git_dir(records) / FAILURE).write_text(json.dumps(
            {"at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "line": line}) + "\n")
    except OSError:
        pass
    return Result("failed", line)


def last_failure(records) -> dict | None:
    try:
        data = json.loads((git_dir(records) / FAILURE).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def untrack_register(records) -> tuple[bool, str | None]:
    """Ignore registers/records.json and remove it from git, in one commit, when it is tracked or not yet ignored.
    (committed, why not). The commit is made through its own index file, so nothing else staged goes into it; the
    worktree's index then takes the two paths from the new HEAD. A failed commit puts .gitignore back."""
    gi = Path(records) / ".gitignore"
    text = gi.read_text() if gi.is_file() else None
    tracked = git(records, "cat-file", "-e", f"HEAD:{REGISTER}").returncode == 0
    if REGISTER in (text or "").splitlines() and not tracked:
        return False, None
    if REGISTER not in (text or "").splitlines():
        gi.write_text((text or "") + ("" if not text or text.endswith("\n") else "\n") + IGNORE)
    index = git_dir(records) / "bakehouse-untrack.index"
    env = dict(os.environ, GIT_INDEX_FILE=str(index))
    try:
        p = git(records, "read-tree", "HEAD", env=env)
        for args in (("add", "--", ".gitignore"), ("rm", "-q", "--cached", "--ignore-unmatch", "--", REGISTER),
                     (*LOCAL, "commit", "-q", "-m", f"records: {REGISTER} is derived, so it is not committed (records format "
                                            f"{locator.FORMAT})")):
            if p.returncode == 0:
                p = git(records, *args, env=env)
    finally:
        index.unlink(missing_ok=True)
    if p.returncode:
        if text is None:
            gi.unlink(missing_ok=True)
        else:
            gi.write_text(text)
        return False, (p.stderr or p.stdout).strip() or "git commit failed"
    git(records, "reset", "-q", "--", ".gitignore", REGISTER)
    return True, None


@contextlib.contextmanager
def writer_lock(records):
    """This writer's ops ledger lock (ops_ledger.locked), which every seal anchor and close commit holds: a step that
    moves the records worktree's HEAD or index holds it too, so it never runs between a line and its commit. No
    writer id: no lock (nothing of this writer's is written then)."""
    try:
        path = locator.writer_file(records, "ops_ledger")
    except locator.SetupNeeded:
        yield
        return
    with ops_ledger.locked(path):
        yield


def merge(records, ref: str) -> str | None:
    """Merge ref into the records branch, under this writer's ops ledger lock; None when merged (or nothing to merge),
    else the failure text. A merge with a conflict is aborted, so the local commits stay as they were; an abort that
    fails is reported, since the worktree is then mid-merge."""
    name = ref.removeprefix("refs/remotes/")
    with writer_lock(records):
        p = git(records, *LOCAL, "merge", "-q", "--no-edit", ref)
        if p.returncode == 0:
            return None
        files = git(records, "diff", "--name-only", "--diff-filter=U").stdout.split()
        if git(records, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0:
            a = git(records, "merge", "--abort")
            if a.returncode:
                return (f"failed: merging {name} conflicts in {', '.join(files) or 'some files'}, and `git merge "
                        f"--abort` failed ({detail(a.stderr or a.stdout)}): the records worktree is mid-merge. Run "
                        f"`git -C {records} merge --abort` there")
        staged = git(records, "diff", "--cached", "--name-only").stdout.split()
    files = files or [x.strip() for x in (p.stderr + p.stdout).splitlines() if x.startswith("\t")]
    if files:
        return (f"conflict: merging {name} conflicts in {', '.join(files)}; the merge was aborted. Merge it by hand in "
                "the records worktree")
    if staged:
        return (f"conflict: merging {name} is blocked by staged changes in {', '.join(staged)}; commit or unstage them "
                "in the records worktree")
    return f"failed: merging {name}: {detail(p.stderr or p.stdout)}"


def exchange(records, remote: str, branch: str) -> tuple[str | None, bool]:
    """Fetch, merge, push; a push refused because the remote moved: once more. (the failure text or None, level)."""
    deadline = time.monotonic() + BOUND
    env = network_env(records)
    ref = tracking(remote, branch)
    helper, extra, problem = token(records)
    env.update(extra)

    def net(*args):
        left = deadline - time.monotonic()
        if left <= 0:
            return subprocess.CompletedProcess(args, None, "", "")
        return git(records, *helper, *args, env=env, timeout=left)

    text = level(records, remote, branch, ref, net)
    return (f"{problem}; then {text}" if problem and text else problem or text), text is None


def level(records, remote: str, branch: str, ref: str, net) -> str | None:
    """exchange's steps, each network step run through net."""
    for attempt in (1, 2):
        p = net("fetch", "-q", remote, f"+refs/heads/{branch}:{ref}")
        if p.returncode != 0 and "couldn't find remote ref" not in (p.stderr or "").lower():
            return why(p, remote, "fetch")  # a remote with no records branch yet: nothing to merge, the push makes it
        if p.returncode == 0:
            m = merge(records, ref)
            if m:
                return m
        p = net("push", "-q", remote, f"refs/heads/{BRANCH}:refs/heads/{branch}")
        if p.returncode == 0:
            git(records, "update-ref", ref, f"refs/heads/{BRANCH}")
            return None
        text = why(p, remote, "push")
        if attempt == 2 or not text.startswith("refused") or p.returncode is None:
            return text
    return None


def sync(records) -> Result:
    """The one sync step (the module's header). Never raises for a git failure; every failure is a Result."""
    records = Path(records)
    if locator.records_format(records) != locator.FORMAT:
        return Result("none", None)
    lock = git_dir(records) / LOCK
    with lock.open("a") as f:
        if fcntl:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return Result("skipped", "bakehouse sync: another sync of these records is running; skipped")
        return sync_held(records)


def sync_held(records) -> Result:
    """sync's steps 2 to 4, for a caller that holds the sync lock already (sync, and bakehouse reserve, which holds it
    from its sync to its last push)."""
    with writer_lock(records):
        _, err = untrack_register(records)
    if err:
        return failed(records, f"failed: could not stop committing {REGISTER}: {detail(err)}")
    up = upstream(records)
    if up is None:
        return Result("none", None)
    text, even = exchange(records, *up)
    if text:
        return failed(records, text, even)
    (git_dir(records) / FAILURE).unlink(missing_ok=True)
    return Result("level", f"bakehouse sync: the records are level with {up[0]}/{up[1]}")


def quiet(records) -> str | None:
    """sync for a fixed point: the failure line to print, or None. A fixed point says nothing when it skips, and
    never fails because of the sync."""
    try:
        r = sync(records)
    except Exception as e:  # noqa: BLE001 - a sync never changes the tool's own result
        return f"bakehouse sync: failed: {e!r}. The records commits stay local; the next sync tries again."
    return r.line if r.state == "failed" else None


def status(records) -> dict | None:
    """`bakehouse status`'s sync field, from local refs only: None in format 1; else the upstream, the number of local
    records commits not on it, and the last failure line with its time."""
    if locator.records_format(records) != locator.FORMAT:
        return None
    up = upstream(records)
    waiting = None
    if up:
        ref = tracking(*up)
        have = git(records, "rev-parse", "-q", "--verify", ref).returncode == 0
        p = git(records, "rev-list", "--count", f"{ref}..HEAD" if have else "HEAD")
        waiting = int(p.stdout.strip()) if p.returncode == 0 else None
    return {"upstream": f"{up[0]}/{up[1]}" if up else None, "waiting": waiting, "lastFailure": last_failure(records)}


def main(argv, root=None) -> int:
    args, folder = list(argv), None
    while args:
        a = args.pop(0)
        if a == "--repo" and args:
            folder = args.pop(0)
        else:
            print(USAGE, file=sys.stderr)
            return 2
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    try:
        project = locator.find(folder or Path.cwd(), env)
    except locator.SetupNeeded as e:
        print(f"bakehouse: {e}", file=sys.stderr)
        return locator.SETUP_NEEDED
    if project is None:
        print(f"bakehouse sync: {folder or Path.cwd()}: no team here (no .team/binding.json)", file=sys.stderr)
        return locator.NO_TEAM
    r = sync(project.records)
    if r.line:
        print(r.line, file=sys.stderr if r.state == "failed" else sys.stdout)
    return 1 if r.state == "failed" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
