"""Work-order ids reserved before use (records format 2, bakehouse 0.10.0). Standard library only.
docs/PROJECT-TEAM.md, "Work-order ids reserved before use", is the reference.

  bakehouse reserve [--repo <folder>]           print the next free work-order id, and record it
  bakehouse reserve --start [--repo <folder>]   once per project: record the floor

On only when the binding has `tracker.reserve` ({"prefix": "A"}) and the records are in format 2. An id is the prefix
and a number; a tracker id with a letter after the number (A1063b) counts as its number.

The records branch holds them: `reservations/FLOOR` (JSON: floor, writer, time, branches), the highest id number over
the tracker file on every local and remote-tracking branch of the main clone, read by --start after a fetch; and one
file per reserved id, `reservations/<id>` (JSON: id, writer, time). Every id at or under the floor counts as taken.

reserve, under the sync lock (records_sync.LOCK) from its sync to its last push:
1. syncs (records_sync.sync_held). No remote, or a sync that fails: it refuses, since an id needs the remote;
2. takes the highest number among the reservations and the floor, and adds one;
3. writes reservations/<id> and commits it alone, by path, under the writer's ops ledger lock;
4. pushes. A push refused: it fetches and drops its own commit (the tip, holding only that file: else it stops and
   says so), then merges. When the remote now holds reservations/<id>, it goes back to 2; else it commits the same id
   again on top of the merge and pushes it. At most TRIES pushes, then it refuses. Any other failure drops the commit
   too, so a refusal leaves nothing committed.
The sync lock, not the writer lock, is held throughout: every sync takes it, so no fixed-point sync merges or pushes
between the reservation commit and its push, and two reserves on one machine (lanes share the records worktree) run
one after the other. The writer lock is held only for each local step that moves HEAD (the commit, the drop, the
merge), as the sync does, so a seal never waits on the network.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import locator  # noqa: E402
import records_sync as rs  # noqa: E402

FOLDER = "reservations"
FLOOR = f"{FOLDER}/FLOOR"
TRIES = 5
PREFIX = re.compile(r"[A-Za-z][A-Za-z_-]{0,15}")
USAGE = "usage: bakehouse reserve [--start] [--repo <folder>]"
START = "bakehouse reserve --start"
# a push refused because the remote moved (a race); any other refusal (a hook, a protected branch) is declined
RACED = ("non-fast-forward", "fetch first", "incorrect old value", "cannot lock ref")


class Refused(Exception):
    """reserve refuses; the message is the line it prints."""


def prefix_problem(binding: dict) -> str | None:
    """Preflight check 2: what is wrong with the binding's tracker.reserve, or None (well formed, or absent)."""
    tracker = binding.get("tracker")
    if not isinstance(tracker, dict) or "reserve" not in tracker:
        return None
    v = tracker["reserve"]
    if isinstance(v, dict) and not set(v) - {"prefix", "_about"} and isinstance(v.get("prefix"), str) \
            and PREFIX.fullmatch(v["prefix"]):
        form = tracker.get("id")
        try:
            ok = not isinstance(form, str) or re.fullmatch(form, f"{v['prefix']}1") is not None
        except re.error:
            ok = True  # a tracker.id that does not compile is reported on its own
        if ok:
            return None
        return (f"field 'tracker.reserve' = {json.dumps(v)} hands out ids such as {v['prefix']}1, which do not match "
                f"tracker.id {json.dumps(form)}: make the prefix the start of the tracker's id form")
    return (f"field 'tracker.reserve' = {json.dumps(v)} is not {{\"prefix\": \"<prefix>\"}}: an id is the prefix and a "
            "number, so the prefix is 1 to 16 letters, hyphens or underscores, starting with a letter; for example "
            '{"prefix": "A"}')


def prefix(binding: dict) -> str | None:
    """The binding's reservation prefix, or None when tracker.reserve is absent or not well formed."""
    tracker = binding.get("tracker")
    if not isinstance(tracker, dict) or prefix_problem(binding) or "reserve" not in tracker:
        return None
    return tracker["reserve"]["prefix"]


def number(pfx: str, wo: str) -> int | None:
    """The id's number: the prefix, a number, and at most one letter after it (A1063b counts as 1063); else None."""
    m = re.fullmatch(rf"{re.escape(pfx)}([0-9]+)[A-Za-z]?", wo)
    return int(m.group(1)) if m else None


def numbers_in(pfx: str, text: str):
    """Every id number in a tracker file's text: the prefix and a number as a whole token, a letter after it allowed."""
    for m in re.finditer(rf"(?<![A-Za-z0-9]){re.escape(pfx)}([0-9]+)[A-Za-z]?(?![A-Za-z0-9])", text):
        yield int(m.group(1))


def git(where, *args, **kw):
    return subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, check=False, **kw)


def floor_at(records, ref: str = "HEAD") -> int | None:
    """The floor reservations/FLOOR holds at ref, or None when it has none. Refused: one it cannot read."""
    p = git(records, "show", f"{ref}:{FLOOR}")
    if p.returncode:
        return None
    try:
        n = json.loads(p.stdout)["floor"]
    except (ValueError, KeyError, TypeError):
        n = None
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise Refused(f"{FLOOR} on the records branch cannot be read as a floor (JSON with a whole number `floor`): "
                      f"restore it from git (`git -C {records} log -- {FLOOR}`)")
    return n


def reserved_at(records, pfx: str, ref: str = "HEAD") -> list:
    """The numbers of the reservations ref holds for this prefix."""
    out = git(records, "ls-tree", "--name-only", ref, "--", f"{FOLDER}/").stdout
    return [n for x in out.splitlines() if (n := number(pfx, Path(x).name)) is not None and Path(x).name == f"{pfx}{n}"]


def reservation(records, wo: str, ref: str = "HEAD") -> dict | None:
    """reservations/<wo> at ref as a dict ({} when it cannot be read), or None when ref has none."""
    p = git(records, "show", f"{ref}:{FOLDER}/{wo}")
    if p.returncode:
        return None
    try:
        data = json.loads(p.stdout)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def preflight(repo, binding: dict, wo: str) -> tuple[list, list]:
    """Preflight check 7, for `change <wo>`: (fails, notes). Reservations off (no well-formed tracker.reserve, no
    records worktree, or records format 1): nothing. On: the id must be the prefix and a number, and have a
    reservation or be at or under the floor; a reservation by another writer is a note."""
    pfx = prefix(binding)
    if pfx is None:
        return [], []
    try:
        project = locator.find(repo, {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"})
        if project is None or locator.records_format(project.records) != locator.FORMAT:
            return [], []
    except locator.SetupNeeded:
        return [], []
    records = project.records
    n = number(pfx, wo)
    if n is None:
        return [f"check 7: work order {wo} is not {pfx} and a number, the form reservations hand out "
                "(tracker.reserve)"], []
    try:
        floor = floor_at(records)
    except Refused as e:
        return [f"check 7: {e}"], []
    if floor is None:
        return [f"check 7: reservations are on (tracker.reserve) and the records branch has no floor: run `{START}` "
                "once for this project, then `bakehouse reserve` for each new work order id"], []
    if n <= floor:
        return [], []
    data = reservation(records, f"{pfx}{n}")
    if data is None:
        return [f"check 7: work order {wo} has no reservation and is above the floor ({pfx}{floor}): run `bakehouse "
                "reserve` for a new id, and use the id it prints"], []
    me = locator.writer_id(records)
    if data.get("writer") != me:
        return [], [f"check 7: work order {wo} is reserved by writer {data.get('writer', '(unreadable)')}, not by this "
                    f"records worktree's {me}: fine when it is the same person on another machine"]
    return [], []


@contextlib.contextmanager
def sync_lock(records, wait: float = rs.BOUND + 5):
    """The sync lock (records_sync.LOCK), waiting up to `wait` seconds for a sync that holds it."""
    with (rs.git_dir(records) / rs.LOCK).open("a") as f:
        if rs.fcntl:
            end = time.monotonic() + wait
            while True:
                try:
                    rs.fcntl.flock(f, rs.fcntl.LOCK_EX | rs.fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() > end:
                        raise Refused(f"another sync of these records held the sync lock for {wait:.0f} seconds; "
                                      "nothing is reserved. Run it again")
                    time.sleep(0.1)
        yield


def synced(records) -> tuple[str, str]:
    """Step 1: the sync, with the sync lock held. The upstream (remote, branch); Refused when there is none, or the
    sync fails."""
    r = rs.sync_held(records)
    if r.state == "none":
        raise Refused(f"an id needs the remote, and the records branch has none (no upstream, and origin does not hold "
                      f"{rs.BRANCH}): publish it once with `git -C {records} push -u origin {rs.BRANCH}`; nothing is "
                      "reserved")
    if r.state != "level" and ("token settings" in (r.line or "") or "token file" in (r.line or "")):
        raise Refused(f"the records token settings are wrong: {r.line}. Fix them first (`bakehouse setup --token-file "
                      "<path> --token-key <NAME> --token-user <name>`); nothing is reserved")
    if r.state != "level":
        raise Refused(f"an id needs the remote, and the sync could not reach it: {r.line}. Nothing is reserved")
    return rs.upstream(records)


def network(records, token: bool = True):
    """A git runner for network steps: no prompts, one bound of rs.BOUND seconds; with token, the user's records token
    (only for the records remote's URL)."""
    deadline = time.monotonic() + rs.BOUND
    env = rs.network_env(records)
    helper, extra, _ = rs.token(records) if token else ([], {}, None)  # a token problem is the sync's to report
    env.update(extra)

    def net(where, *args):
        left = deadline - time.monotonic()
        if left <= 0:
            return subprocess.CompletedProcess(args, None, "", "")
        return rs.git(where, *helper, *args, env=env, timeout=left)
    return net


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def commit(records, path: str, data: dict, message: str) -> str:
    """Write path and commit it alone, by path, under the writer lock; the commit's sha."""
    with rs.writer_lock(records):
        f = Path(records) / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data) + "\n")
        for args in (("add", "--", path), (*rs.LOCAL, "commit", "-q", "-m", message, "--only", "--", path)):
            p = git(records, *args)
            if p.returncode:
                git(records, "reset", "-q", "--", path)
                git(records, "checkout", "-q", "HEAD", "--", path)
                if git(records, "cat-file", "-e", f"HEAD:{path}").returncode:
                    f.unlink(missing_ok=True)
                raise Refused(f"could not commit {path} in {records}: {rs.detail(p.stderr or p.stdout)}; nothing is "
                              "reserved")
        return git(records, "rev-parse", "HEAD").stdout.strip()


def drop(records, path: str, sha: str) -> None:
    """Take back the commit sha that commit made. It must have one parent and hold only path; else Refused, naming the
    repair. At the tip: HEAD moves to its parent. Not at the tip (a seal in another lane came after it): a new commit,
    by path, puts path back as the parent had it (absent: deleted). Under the writer lock."""
    with rs.writer_lock(records):
        tip = git(records, "rev-parse", "HEAD").stdout.strip()
        parents = git(records, "rev-list", "--parents", "-n", "1", sha).stdout.split()[1:]
        files = git(records, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha).stdout.split()
        if len(parents) != 1 or files != [path]:
            raise Refused(f"stopped: the commit {sha[:12]} of {path} must be taken back, and it does not have one "
                          f"parent holding only that file. Nothing was changed. Take it back by hand before the next "
                          f"sync, or that sync conflicts on {path}: `git -C {records} revert --no-edit {sha}`")
        held = git(records, "cat-file", "-e", f"{parents[0]}:{path}").returncode == 0
        if tip == sha:
            p = git(records, "update-ref", "-m", f"bakehouse reserve: take back {path}", f"refs/heads/{rs.BRANCH}",
                    parents[0], sha)
            if p.returncode:
                raise Refused(f"stopped: could not take back the commit {sha[:12]} of {path}: {rs.detail(p.stderr)}")
            git(records, "reset", "-q", "--", path)
            if held:
                git(records, "checkout", "-q", "HEAD", "--", path)
            else:
                (Path(records) / path).unlink(missing_ok=True)
            return
        steps = (("checkout", "-q", parents[0], "--", path) if held else ("rm", "-q", "--", path),
                 (*rs.LOCAL, "commit", "-q", "-m", f"records: take back {path} (bakehouse reserve)", "--only", "--",
                  path))
        for args in steps:
            p = git(records, *args)
            if p.returncode:
                raise Refused(f"stopped: could not take back the commit {sha[:12]} of {path} ({rs.detail(p.stderr)}). "
                              f"Take it back by hand before the next sync: `git -C {records} revert --no-edit {sha}`")


def stale(records, up, writer: str) -> None:
    """Take back this writer's reservation commits that are not on the upstream (a reserve stopped between its commit
    and its push), newest first, before the sync could push them."""
    ref = rs.tracking(*up)
    if git(records, "rev-parse", "-q", "--verify", ref).returncode:
        return
    for sha in git(records, "log", "--format=%H", f"{ref}..HEAD", "--", f"{FOLDER}/").stdout.split():
        files = git(records, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha).stdout.split()
        if len(files) != 1 or not files[0].startswith(f"{FOLDER}/"):
            continue
        try:
            data = json.loads(git(records, "show", f"{sha}:{files[0]}").stdout)
        except ValueError:
            continue
        msg = git(records, "log", "-1", "--format=%s", sha).stdout
        if isinstance(data, dict) and data.get("writer") == writer and not msg.startswith("records: take back"):
            drop(records, files[0], sha)


def publish(records, up, path: str, data: dict, message: str, net, tries: int, taken) -> tuple[str, int]:
    """Commit path and push it, at most `tries` pushes. ("pushed" | "taken", pushes used): taken when taken(ref) is true
    of the fetched remote (the local commit is taken back and the remote merged). Refused for any other failure, with
    the commit taken back."""
    remote, branch = up
    ref = rs.tracking(remote, branch)
    used = 0
    while used < tries:
        used += 1
        sha = commit(records, path, data, message)
        try:
            p = net(records, "push", "-q", remote, f"refs/heads/{rs.BRANCH}:refs/heads/{branch}")
        except BaseException:
            drop(records, path, sha)
            raise
        if p.returncode == 0:
            git(records, "update-ref", ref, f"refs/heads/{rs.BRANCH}")
            return "pushed", used
        low = (p.stderr or "").lower()
        race = p.returncode is not None and any(w in low for w in RACED)
        text = rs.why(p, remote, "push")
        if race:
            f = net(records, "fetch", "-q", remote, f"+refs/heads/{branch}:{ref}")
            if f.returncode != 0:
                race, text = False, rs.why(f, remote, "fetch")
        drop(records, path, sha)
        if p.returncode is None:
            raise Refused(f"the push to {remote} had no answer within {rs.BOUND} seconds: {path} may be on the remote "
                          "all the same (an id taken that way is burned, never handed out twice). Nothing is reserved "
                          "here; run it again")
        if not race:
            if text.startswith("refused"):
                text = (f"failed: the push to {remote} was declined, not raced (a server hook or a protected branch): "
                        f"{rs.detail(p.stderr)}; not retried")
            raise Refused(f"an id needs the remote: {text}. Nothing is reserved")
        m = rs.merge(records, ref)
        if m:
            raise Refused(f"{m}. Nothing is reserved")
        if taken(ref):
            return "taken", used
    raise Refused(f"the push to {remote} was refused {TRIES} times (the remote kept moving); nothing is reserved. Run "
                  "it again")


def reserve(records, pfx: str, writer: str) -> str:
    """The id reserved and pushed (steps 1 to 4)."""
    with sync_lock(records):
        up = rs.upstream(records)
        if up:
            stale(records, up, writer)
        up = synced(records)
        if floor_at(records) is None:
            raise Refused(f"reservations are on (tracker.reserve), and the records branch has no floor yet: run "
                          f"`{START}` once for this project first")
        net, left = network(records), TRIES
        while left > 0:
            n = max([floor_at(records), *reserved_at(records, pfx)]) + 1
            wo, path = f"{pfx}{n}", f"{FOLDER}/{pfx}{n}"

            def taken(ref):  # another writer took the id, or a raised floor covers it
                return git(records, "cat-file", "-e", f"{ref}:{path}").returncode == 0 or \
                    (floor_at(records, ref) or 0) >= n
            how, used = publish(records, up, path, {"id": wo, "writer": writer, "time": now()},
                                f"records: reserve the work-order id {wo} (writer {writer})", net, left, taken)
            if how == "pushed":
                return wo
            left -= used
        raise Refused(f"the push to {up[0]} was refused {TRIES} times (the remote kept moving); nothing is reserved. "
                      "Run it again")


def tracker_floor(main, tracker_file: str, pfx: str) -> tuple[int, int]:
    """(the highest id number in tracker_file over every local and remote-tracking branch of main, the number of
    branches that hold the file)."""
    refs = [r for r in git(main, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes").stdout.split()
            if not r.endswith("/HEAD")]
    p = subprocess.run(["git", "-C", str(main), "cat-file", "--batch"], capture_output=True, check=False,
                       input="".join(f"{r}:{tracker_file}\n" for r in refs).encode())
    out, i, best, held = p.stdout, 0, 0, 0
    while i < len(out):
        j = out.index(b"\n", i)
        head, i = out[i:j].split(), j + 1
        if not head or head[-1] in (b"missing", b"ambiguous") or len(head) != 3:
            continue
        size = int(head[2])
        if head[1] == b"blob":
            held += 1
            best = max([best, *numbers_in(pfx, out[i:i + size].decode("utf-8", "replace"))])
        i += size + 1
    return best, held


def fetch_code(records, main, up) -> None:
    """Fetch each remote of the main clone, each with its own bound. The records token goes only to a remote whose URL
    is the records upstream's. Refused, naming the remote that failed."""
    url = git(records, "config", "--get", f"remote.{up[0]}.url").stdout.strip()
    for remote in git(main, "remote").stdout.split():
        mine = git(main, "config", "--get", f"remote.{remote}.url").stdout.strip() == url
        p = network(records, token=mine)(main, "fetch", "-q", remote)
        if p.returncode != 0:
            raise Refused(f"the floor needs every remote of {main}: {rs.why(p, remote, 'fetch')}. Fetch it by hand "
                          f"(`git -C {main} fetch {remote}`) or remove it (`git -C {main} remote remove {remote}`); "
                          "nothing is recorded")


def start(project, binding: dict, pfx: str, writer: str) -> str:
    """--start: the line to print. The first one records the floor; a later one raises it when this clone's branches
    hold a higher id, and never lowers it."""
    records = project.records
    with sync_lock(records):
        up = rs.upstream(records)
        if up:
            stale(records, up, writer)
        up = synced(records)
        main = locator.main_clone(project) or project.root
        fetch_code(records, main, up)
        tracker = binding["tracker"]
        if not isinstance(tracker.get("file"), str):
            raise Refused("the binding has no tracker.file, so the floor cannot be read")
        n, branches = tracker_floor(main, tracker["file"], pfx)
        if branches == 0:
            raise Refused(f"no branch of {main} holds the tracker file {tracker['file']} (tracker.file), so there is no "
                          "floor to read; nothing is recorded")
        net, left = network(records), TRIES
        while left > 0:
            have = floor_at(records)
            if have is not None and n <= have:
                return (f"bakehouse reserve --start: the floor is {pfx}{have} ({FLOOR}), and no branch of {main} holds "
                        "a higher id; nothing changed")
            how, used = publish(records, up, FLOOR, {"floor": n, "writer": writer, "time": now(), "branches": branches},
                                f"records: the reservation floor {pfx}{n}, the highest work-order id on {branches} "
                                "branch(es)", net, left, lambda ref, have=have: floor_at(records, ref) != have)
            if how == "pushed":
                was = "" if have is None else f" (raised from {pfx}{have})"
                return (f"bakehouse reserve --start: the floor is {pfx}{n}{was}, the highest id in {tracker['file']} "
                        f"over {branches} branch(es) of {main}; every id at or under it counts as taken. Next: "
                        "`bakehouse reserve`")
            left -= used
        raise Refused(f"the push to {up[0]} was refused {TRIES} times (the remote kept moving); nothing is recorded. "
                      "Run it again")


def main(argv, root=None) -> int:
    args, folder, first = list(argv), None, False
    while args:
        a = args.pop(0)
        if a == "--repo" and args:
            folder = args.pop(0)
        elif a == "--start":
            first = True
        else:
            print(USAGE, file=sys.stderr)
            return 2
    name = START if first else "bakehouse reserve"
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    try:
        project = locator.find(folder or Path.cwd(), env)
    except locator.SetupNeeded as e:
        print(f"bakehouse: {e}", file=sys.stderr)
        return locator.SETUP_NEEDED
    if project is None:
        print(f"{name}: {folder or Path.cwd()}: no team here (no .team/binding.json)", file=sys.stderr)
        return locator.NO_TEAM
    try:
        binding = json.loads(project.binding.read_text())
        if not isinstance(binding, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as e:
        print(f"{name}: refused: the binding {project.binding} cannot be read ({e})", file=sys.stderr)
        return 1
    try:
        if why := prefix_problem(binding):
            raise Refused(f"the binding's {why}")
        pfx = prefix(binding)
        if pfx is None:
            raise Refused("reservations are off: the binding has no tracker.reserve (for example "
                          '"tracker": {..., "reserve": {"prefix": "A"}})')
        if locator.records_format(project.records) != locator.FORMAT:
            raise Refused(f"reservations need records format 2, and {project.records} is in format 1: run "
                          f"`{locator.MIGRATE}` first")
        writer = locator.writer_id(project.records)
        if not writer or not locator.WRITER.fullmatch(writer):
            raise Refused(f"the records worktree {project.records} has no usable writer id (git config "
                          f"{locator.WRITER_KEY}): run `bakehouse setup` in the main clone")
        if first:
            print(start(project, binding, pfx, writer))
        else:
            print(reserve(project.records, pfx, writer))
    except Refused as e:
        print(f"{name}: refused: {e}", file=sys.stderr)
        return 1
    except locator.SetupNeeded as e:
        print(f"bakehouse: {e}", file=sys.stderr)
        return locator.SETUP_NEEDED
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
