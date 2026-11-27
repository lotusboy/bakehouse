#!/usr/bin/env python3
"""The mutation runner: plant each mutant of a list in a clean copy of a commit, run the tests the
repository's binding names for the mutated file, and write the rows of the reviewer's mutation record.

Usage:
  mutate.py --mutants FILE --record DIR --round first|fix<N> [--repo DIR] [--commit REV]
            [--binding FILE] [--work DIR] [--by reviewer|engineer]

--mutants   a JSON list of {"id", "file", "find", "replace", "note"}: exactly those keys, all text.
            `find` must occur exactly once in `file` at the commit; `note` says what the mutant means
            and becomes the row's `mutation`.
--record    the review's record folder. The rows go into the round's declared mutations file
            (record-contracts.json, jobs.reviewer.rounds): `first` -> mutations.json, `fix<N>` ->
            mutations-fix<N>.json. Rows are appended; a row already in the file is never changed,
            and a mutant whose id is already there, or in the round's batch log, refuses the batch.
            Each verdict is first appended to that log (`log` in the same declaration:
            mutations.log.jsonl, mutations-fix<N>.log.jsonl), which records-check reconciles with the
            file.
--by        who runs the batch (default reviewer). An engineer's rows go into the same names with
            `engineer-` in front (engineer-mutations.json, engineer-mutations-fix<N>.json, and their logs
            engineer-mutations*.log.jsonl), so an engineer's batch never takes the reviewer's file.
--repo      the repository (default: the working directory's). --commit: what is mutated (default
            HEAD). --binding: the binding file (default: the repository's binding, found by
            runtime/locator.py, as team-preflight finds it); only its `mutation` block is read. --work:
            where copies are made (default: a temporary folder); refused when it lies inside the
            repository.

The binding's `mutation` block (docs/PROJECT-TEAM.md, "The mutation runner"):
  {"command": [argv...],        the test command, program first; the test paths are appended
   "env": {"NAME": "value"},    added to the environment of every run
   "timeout": 600,              seconds per run (default 600)
   "tests": [{"files": "<glob>", "tests": ["<path>", ...], "command": [argv...]}],
   "links": {"<path in the copy>": "<path in the checkout>"},
   "loads": {"files": "<glob>", "command": [argv...]}}
`links` names a git repository the checkout links to (a data folder the commit only points at). Per
batch, the runner takes one `git archive` of that repository's committed HEAD into the work folder and
links the path in every copy to that snapshot; the live repository is only read (`rev-parse`,
`archive`). Its HEAD is printed and written in each row as `data`. The snapshot is removed with the
copies. A test that needs an uncommitted file there fails the control.
The first entry whose `files` glob matches the mutated file decides (fnmatch: `*` also crosses `/`);
an entry's `command` replaces the block's. `{root}` in a command or env value is the repository
checkout, for what a clean copy does not carry (an environment folder).

What it does, in order. Steps 1-4 refuse with nothing written (exit 2):
  1. The block, the list, the commit, the record and its batch log (writable files, not folders; the
     log JSON lines), --work (its nearest existing folder writable), the test program (a relative one:
     a file with the exec bit at the commit), and for each mutant: an entry names tests for its file, the file and each named test path exist
     at the commit, `find` occurs there exactly once (overlapping matches counted).
  2. `git archive` of the commit, once (never the working tree), and of each `links` repository's
     HEAD into a read-only snapshot. The archived-copy check: each mutant is planted once in an
     extracted copy; a file missing there (export-ignore), a symlink or a path outside the copy, a
     `find` that does not occur exactly once there (`.gitattributes` applied), or a replace that
     changes no bytes refuses. So does a `links` path whose folders in the copy include a symlink (a
     committed one may point outside the copy) or resolve outside it: the runner replaces that path in
     every copy, and must never delete or follow anything outside its copy.
  3. The control: for each distinct (command, tests) the batch uses, a fresh copy, unmutated. A
     failure or a timeout refuses the whole batch: a mutant cannot be judged by tests that fail. The
     copy is a `git archive` of the commit, with no .git and no uncommitted files, so a test that
     needs either (one that runs `git ls-files`, say) fails the control.
  4. Per mutated file, the probe always runs: the file is emptied in a copy, and the batch is
     refused when the named tests still pass. When the block's `loads` glob matches the file, the
     load check runs first as an extra check: `loads.command` + the file runs in an unmutated copy
     with the block's env, and the last line it prints must be that file in the copy. It shows where
     that command loads the file, not where the tests' own command does, so it never replaces the
     probe.
  5. Each mutant: a fresh copy, the one replacement, the tests with the block's env and timeout,
     then the copy is removed. verdict: killed when the command exits non-zero, else survived.
     The row {id, mutation, site, gate, verdict, disposition: null, batch} is first written to the
     batch log as one line {batch, commit, data, row}, fsynced, and its verdict printed; only then is
     it appended to the record, under a lock on the record's folder, after re-reading the file. A row
     this batch wrote that the file lost (an editor wrote back an older copy) is written again and
     named; one whose runner fields (all but the disposition) were changed is named and left as it is.
     Either ends the batch with exit 3. An edit made after the batch's last append is not seen here:
     records-check reconciles the file with the log and flags it. site is `<file>:<line>`
     (`<a>-<b>` when the find spans lines, leading and trailing blank text not counted); gate is the
     test path that ran, a list when several did. A survivor's disposition is left null for the
     reviewer to write; records-check flags it until then.
  6. A run that times out writes no row: the record has two verdicts and a timeout proves neither.
     It is named, and the exit is 1.

What stays with the reviewer: choosing the mutants, and the disposition of each survivor. A mutant
that needs more than one edit, or whose text occurs twice, is not planted here.

Exit: 0 every mutant has a row; 1 at least one timed out; 2 refused, nothing written; 3 stopped after
a mutant ran, rows may be written (an id written meanwhile, rows restored, a read-back mismatch, an
error); 129, 130, 143 SIGHUP, Ctrl-C, SIGTERM (the tests are killed, the work folder removed).
Standard library only.
"""

import argparse
import fcntl
import fnmatch
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import locator  # noqa: E402

_rc = importlib.util.spec_from_file_location("records_check", HERE / "records-check.py")
records_check = importlib.util.module_from_spec(_rc)
_rc.loader.exec_module(records_check)

TIMEOUT = 600
MUTANT_KEYS = ("id", "file", "find", "replace", "note")


class Refused(Exception):
    pass


class Stopped(Exception):
    """A refusal or an error after a mutant ran: rows may already be written (exit 3)."""


def _argv(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, str) and x.strip() for x in v)


def repo_path_ok(p) -> bool:
    return (
        isinstance(p, str)
        and bool(p.strip("/"))
        and not p.startswith("/")
        and ".." not in p.split("/")
        and not re.search(r"\s", p)
    )


def mutation_errors(block) -> list:
    """Why a binding's `mutation` block is refused; [] when it is well formed. team-preflight reports
    each at check 2."""
    if not isinstance(block, dict):
        return ["'mutation' must be an object with 'command' and 'tests'"]
    errs = [
        f"'mutation.{k}' is not a mutation field (command, env, timeout, tests, links, loads)"
        for k in block
        if k not in ("_about", "command", "env", "timeout", "tests", "links", "loads")
    ]
    loads = block.get("loads")
    if loads is not None and not (
        isinstance(loads, dict)
        and set(loads) <= {"_about", "files", "command"}
        and isinstance(loads.get("files"), str)
        and loads["files"].strip()
        and _argv(loads.get("command"))
    ):
        errs.append(
            '\'mutation.loads\' must be {"files": <glob>, "command": [argv...]}: a command that, given a '
            "file, prints the path the tests' environment loads it from"
        )
    links = block.get("links", {})
    if not (isinstance(links, dict) and all(repo_path_ok(k) and repo_path_ok(v) for k, v in links.items())):
        errs.append(
            "'mutation.links' must be an object of <path in the copy> -> <path in the checkout>, both inside it"
        )
    if "command" in block and not _argv(block["command"]):
        errs.append(
            '\'mutation.command\' must be a list of text, the program first, for example ["python3", "-m", "pytest"]'
        )
    env = block.get("env", {})
    if not (isinstance(env, dict) and all(isinstance(k, str) and k and isinstance(v, str) for k, v in env.items())):
        errs.append("'mutation.env' must be an object of NAME -> text")
    t = block.get("timeout", TIMEOUT)
    if not (isinstance(t, int) and not isinstance(t, bool) and t > 0):
        errs.append(f"'mutation.timeout' = {json.dumps(t)} must be a whole number of seconds above 0")
    tests = block.get("tests")
    if not (isinstance(tests, list) and tests):
        return errs + ['\'mutation.tests\' must be a list of {"files": <glob>, "tests": [<path>, ...]}']
    for i, e in enumerate(tests):
        at = f"mutation.tests[{i}]"
        if not (
            isinstance(e, dict)
            and isinstance(e.get("files"), str)
            and e["files"].strip()
            and isinstance(e.get("tests"), list)
            and e["tests"]
        ):
            errs.append(f"'{at}' must have 'files' (a glob) and 'tests' (a list of repository paths)")
            continue
        errs += [
            f"'{at}.{k}' is not an entry field (files, tests, command)"
            for k in e
            if k not in ("_about", "files", "tests", "command")
        ]
        errs += [
            f"'{at}.tests' = {json.dumps(p)} is not a path inside the repository"
            for p in e["tests"]
            if not repo_path_ok(p)
        ]
        if "command" in e and not _argv(e["command"]):
            errs.append(f"'{at}.command' must be a list of text, the program first")
        elif "command" not in e and "command" not in block:
            errs.append(f"'{at}' has no command, and the block has none")
    return errs


def git(repo, *args, **kw):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False, **kw)


def round_number(name: str, record=None) -> int:
    if name == "first":
        return 0
    m = re.fullmatch(r"fix([1-9][0-9]*)", name or "")
    if not m:
        raise Refused(
            f"--round {json.dumps(name)} is neither `first` nor `fix<N>` (N from 1). A labelled prediction"
            f" round (for example `golive`) is reviewed as the next fix round: here `--round {next_round(record)}`."
        )
    return int(m.group(1))


def next_round(record) -> str:
    """The record folder's next free round, as records-check.misnamed counts it: the highest declared
    round in the folder plus 1; `first` when the folder is missing or holds no declared round."""
    job = records_check.load_contracts()["jobs"]["reviewer"]
    folder = Path(record).expanduser() if record else None
    names = [p.name for p in folder.iterdir()] if folder and folder.is_dir() else []
    taken = [r for n in names if (r := records_check.declared(n, job)) is not None]
    return f"fix{max(taken) + 1}" if taken else "first"


def load_mutants(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Refused(f"{path} is not a readable JSON file ({e})")
    if not isinstance(data, list) or not data:
        raise Refused(f"{path} is not a non-empty JSON list of mutants")
    bad, seen = [], set()
    for i, m in enumerate(data):
        at = f"mutant {i + 1}"
        if not isinstance(m, dict) or set(m) != set(MUTANT_KEYS):
            bad.append(f"{at} must have exactly the keys {', '.join(MUTANT_KEYS)}")
            continue
        if not all(isinstance(m[k], str) for k in MUTANT_KEYS) or not all(
            m[k].strip() for k in ("id", "file", "find", "note")
        ):
            bad.append(f"{at}: every key is text, and id, file, find and note are not empty")
            continue
        if m["find"] == m["replace"]:
            bad.append(f"{m['id']}: find and replace are the same text")
        if m["id"] in seen:
            bad.append(f"{m['id']} repeats an id")
        seen.add(m["id"])
    if bad:
        raise Refused("; ".join(bad))
    return data


def site(text: str, f: str, find: str) -> str:
    i = text.index(find)
    lead = len(find) - len(find.lstrip())
    body = find.strip()
    a = text.count("\n", 0, i + lead) + 1
    b = a + body.count("\n")
    return f"{f}:{a}" if a == b else f"{f}:{a}-{b}"


def expand(v: str, root: Path) -> str:
    return v.replace("{root}", str(root))


def run(argv, cwd, env, timeout):
    """(exit code or None on a timeout, the last line of stdout, else of stderr)."""
    p = subprocess.Popen(
        argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
    )
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        return None, f"timed out after {timeout} s"
    except BaseException:  # SIGTERM (raised as SystemExit) or ^C: the tests do not outlive the runner
        os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        raise
    lines = [x for x in out.split("\n") if x.strip()] or [x for x in err.split("\n") if x.strip()]
    return p.returncode, lines[-1].strip() if lines else ""


def occurrences(text, find) -> int:
    """How many times find occurs, overlapping matches counted (`==` occurs twice in `===`)."""
    n, i = 0, text.find(find)
    while i >= 0:
        n, i = n + 1, text.find(find, i + 1)
    return n


def append_row(path: Path, row: dict, mine: list) -> list:
    """Append one row: an exclusive lock on the record's folder, the file re-read, the id checked
    against what is there now (a reviewer or another batch may have written meanwhile), an atomic write,
    then the file read back and compared with what was meant. A row this batch wrote earlier (`mine`)
    that the re-read file lacks is written again: an editor that read the file before it landed wrote it
    back without it. One whose runner fields (every field but the disposition) differ is not rewritten:
    the edit may be the reviewer's, and records-check flags it against the batch log. Returns (the ids
    restored, [(id, the fields that differ)])."""
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        cur = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(cur, list):
            raise Refused(f"{path} is no longer a JSON list of rows; {row['id']} ({row['verdict']}) is not written")
        ids = {r.get("id") for r in cur if isinstance(r, dict)}
        if row["id"] in ids:
            raise Refused(
                f"{row['id']} is already a row of {path.name}, written during this batch; its verdict "
                f"({row['verdict']}) is not written"
            )
        lost = [r for r in mine if r["id"] not in ids]
        now = {}
        for r in cur:
            if isinstance(r, dict):
                now.setdefault(r.get("id"), r)
        changed = [
            (r["id"], diff)
            for r in mine
            if r["id"] in now and (diff := [k for k in r if k != "disposition" and now[r["id"]].get(k) != r[k]])
        ]
        new = cur + lost + [row]
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(new, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        if json.loads(path.read_text(encoding="utf-8")) != new:
            raise Refused(f"{path} does not read back as written; stop and compare it with the batch output")
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    return [r["id"] for r in lost], changed


def log_line(log: Path, entry: dict):
    """One line of the batch log: one write on an append-only file, fsynced (and the folder, when the
    file is new) before the record is touched, so the verdict is on disk before any read can fail."""
    new = not log.exists()
    fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    if new:
        d = os.open(log.parent, os.O_RDONLY)
        try:
            os.fsync(d)
        finally:
            os.close(d)


def remove(d: Path):
    """Remove a copy, also when a test made read-only folders in it (a copy out of the snapshot)."""
    if d.exists():
        writable(d)
    shutil.rmtree(d, ignore_errors=True)


def writable(tree: Path):
    """Give back write permission on every folder of a tree (a read-only snapshot), so it can be removed."""
    for d, _, _ in os.walk(tree):
        os.chmod(d, 0o755)


def read_only(tree: Path):
    for d, dirs, files in os.walk(tree):
        for f in files:
            if not os.path.islink(os.path.join(d, f)):
                os.chmod(os.path.join(d, f), 0o444)
    for d, _, _ in sorted(os.walk(tree), key=lambda w: -len(w[0])):
        os.chmod(d, 0o555)


def plan(a) -> dict:
    repo = Path(a.repo or os.getcwd()).resolve()
    top = git(repo, "rev-parse", "--show-toplevel", text=True)
    if top.returncode:
        raise Refused(f"{repo} is not inside a git repository")
    repo = Path(top.stdout.strip())
    if a.binding:
        bpath = Path(a.binding).expanduser().resolve()
    else:
        try:
            project = locator.find(repo, {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"})
        except locator.SetupNeeded as e:
            raise Refused(str(e))
        bpath = project.binding if project else None
    if bpath is None or not Path(bpath).is_file():
        raise Refused(f"no binding for {repo}: name one with --binding")
    try:
        block = json.loads(Path(bpath).read_text(encoding="utf-8")).get("mutation")
    except (OSError, ValueError, AttributeError) as e:
        raise Refused(f"{bpath} is not a JSON object ({e})")
    if block is None:
        raise Refused(f"{bpath} has no 'mutation' block: it names no tests to run")
    errs = mutation_errors(block)
    if errs:
        raise Refused("; ".join(errs))
    work = Path(a.work).expanduser().resolve() if a.work else None
    if work is not None and (work == repo or repo in work.parents):
        raise Refused(f"--work {work} is inside the repository: copies would sit in its working tree")
    if work is not None:
        near = work
        while not near.exists() and near != near.parent:
            near = near.parent
        if not (near.is_dir() and os.access(near, os.W_OK | os.X_OK)):
            raise Refused(f"--work {work} cannot be made: {near} is not a writable folder")
    data = {}
    for at, target in block.get("links", {}).items():
        t = (repo / target).resolve()
        top = git(t, "rev-parse", "--show-toplevel", text=True) if t.is_dir() else None
        if top is None or top.returncode or Path(top.stdout.strip()).resolve() != t:
            raise Refused(f"mutation.links: {target} is not the top of a git repository in {repo}")
        if work is not None and (work == t or t in work.parents):
            raise Refused(f"--work {work} is inside the linked repository {target}")
        head = git(t, "rev-parse", "--verify", "HEAD^{commit}", text=True)
        if head.returncode:
            raise Refused(f"mutation.links: {target} has no committed HEAD")
        data[at] = (t, head.stdout.strip())
    n = round_number(a.round, a.record)
    job = records_check.load_contracts()["jobs"]["reviewer"]
    by = "engineer-" if a.by == "engineer" else ""
    out = Path(a.record).expanduser().resolve() / (by + records_check.round_name(job, n, "mutations"))
    old = []
    if out.is_dir():
        raise Refused(f"{out} is a folder, not the round's mutations file")
    there = out.parent
    while not there.exists():
        there = there.parent
    if not (there.is_dir() and os.access(there, os.W_OK | os.X_OK)) or out.exists() and not os.access(out, os.W_OK):
        raise Refused(f"{out} cannot be written ({there} is not a writable folder, or the file is read-only)")
    if out.exists():
        try:
            old = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise Refused(f"{out} is not JSON ({e}); it is a reviewer's record and is never overwritten")
        if not isinstance(old, list):
            raise Refused(f"{out} is not a JSON list of rows")
    have = {r.get("id") for r in old if isinstance(r, dict)}
    log = out.with_name(by + records_check.round_name(job, n, "log"))
    if log.is_dir() or log.exists() and not os.access(log, os.W_OK):
        raise Refused(f"{log}, the round's batch log, is a folder or read-only")
    logged = set()
    if log.exists():
        try:
            logged = {json.loads(x)["row"]["id"] for x in log.read_text(encoding="utf-8").splitlines() if x.strip()}
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise Refused(f"{log} is not the runner's batch log, one JSON line per verdict ({e!r})")
    rev = git(repo, "rev-parse", "--verify", "--quiet", f"{a.commit}^{{commit}}", text=True)
    if rev.returncode:
        raise Refused(f"--commit {a.commit} is not a commit in {repo}")
    sha = rev.stdout.strip()
    mutants = load_mutants(Path(a.mutants).expanduser())
    bad, todo = [], []
    for m in mutants:
        if m["id"] in have:
            bad.append(f"{m['id']} is already a row of {out.name}")
            continue
        if m["id"] in logged:
            bad.append(f"{m['id']} is already in {log.name}: its verdict was measured; copy that row back into {out.name}")
            continue
        e = next((e for e in block["tests"] if fnmatch.fnmatchcase(m["file"], e["files"])), None)
        if e is None:
            bad.append(f"{m['id']}: no entry of the binding's mutation.tests names tests for {m['file']}")
            continue
        shown = git(repo, "show", f"{sha}:{m['file']}")
        if shown.returncode:
            bad.append(f"{m['id']}: {m['file']} is not a file at {sha[:12]}")
            continue
        try:
            text = shown.stdout.decode("utf-8")
        except UnicodeDecodeError:
            bad.append(f"{m['id']}: {m['file']} is not UTF-8 text at {sha[:12]}")
            continue
        k = occurrences(text, m["find"])
        if k != 1:
            bad.append(f"{m['id']}: find occurs {k} times in {m['file']} at {sha[:12]}; it must occur exactly once")
            continue
        missing = [t for t in e["tests"] if git(repo, "cat-file", "-e", f"{sha}:{t.rstrip('/')}").returncode]
        if missing:
            bad.append(f"{m['id']}: the test path(s) {', '.join(missing)} do not exist at {sha[:12]}")
            continue
        argv = [expand(x, repo) for x in (e.get("command") or block["command"])] + list(e["tests"])
        prog = argv[0]  # a relative path with a / runs from the copy's root, so it is checked at the commit
        rel = "/" in prog and not os.path.isabs(prog)
        mode = (git(repo, "ls-tree", sha, "--", prog, text=True).stdout.split() or [""])[0] if rel else None
        if mode != "100755" if rel else shutil.which(prog) is None:
            bad.append(
                f"{m['id']}: the test program {prog} is not found or not executable"
                + (f" (not a file with the exec bit at {sha[:12]})" if rel else "")
            )
            continue
        gate = e["tests"][0] if len(e["tests"]) == 1 else list(e["tests"])
        todo.append(dict(m, site=site(text, m["file"], m["find"]), argv=argv, gate=gate))
    if bad:
        raise Refused("; ".join(bad))
    env = dict(os.environ, **{k: expand(v, repo) for k, v in block.get("env", {}).items()})
    return {
        "repo": repo,
        "sha": sha,
        "out": out,
        "log": log,
        "old": old,
        "todo": todo,
        "env": env,
        "work": work,
        "timeout": block.get("timeout", TIMEOUT),
        "data": data,
        "loads": block.get("loads"),
    }


def link_problem(d: Path, at: str):
    """Why the copy d's `links` path `at` cannot be replaced safely, or None. A folder on the way to it that
    is a symlink (a committed one may point outside the copy) or that resolves outside the copy would make
    the runner delete or follow something that is not its own. The path itself may be a symlink: it is
    only unlinked."""
    q = d
    for part in Path(at).parts[:-1]:
        q = q / part
        if q.is_symlink():
            return f"mutation.links: {at} passes through {q.relative_to(d)}, a symlink in the commit; the runner never follows one"
    root, parent = d.resolve(), (d / at).parent.resolve()
    if parent != root and root not in parent.parents:
        return f"mutation.links: {at} lies outside the copy"
    return None


def copy(tar: Path, work: Path, name: str, snaps: dict) -> Path:
    d = work / name
    remove(d)
    d.mkdir()
    subprocess.run(["tar", "-xf", str(tar), "-C", str(d)], check=True)
    for at in snaps:  # checked before anything in the copy is touched
        why = link_problem(d, at)
        if why:
            raise Refused(why)
    for at, snap in snaps.items():  # the copy's path points at the batch's snapshot, never the live repo
        link = d / at
        if link.is_symlink() or link.is_file():
            link.unlink()
        elif link.exists():
            shutil.rmtree(link)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(snap, target_is_directory=True)
    return d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mutants", required=True)
    ap.add_argument("--record", required=True)
    ap.add_argument("--round", required=True)
    ap.add_argument("--repo")
    ap.add_argument("--commit", default="HEAD")
    ap.add_argument("--binding")
    ap.add_argument("--work")
    ap.add_argument("--by", choices=("reviewer", "engineer"), default="reviewer")
    a = ap.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(129))
    try:
        return batch(a)
    except Refused as e:
        print(f"mutate: refused: {e}", file=sys.stderr)
        return 2
    except (Stopped, OSError) as e:
        print(f"mutate: stopped after a mutant ran; rows may already be written: {e}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("mutate: interrupted; the tests are stopped and the work folder removed", file=sys.stderr)
        return 130


def batch(a) -> int:
    p = plan(a)
    if p["work"] is not None:
        p["work"].mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="mutate-", dir=p["work"]))
    try:
        tar = work / "commit.tar"
        subprocess.run(["git", "-C", str(p["repo"]), "archive", "-o", str(tar), p["sha"]], check=True)
        snaps = {}
        for i, (at, (t, head)) in enumerate(p["data"].items()):
            snaps[at] = work / f"data-{i}"
            snaps[at].mkdir()
            dtar = work / f"data-{i}.tar"
            subprocess.run(["git", "-C", str(t), "archive", "-o", str(dtar), head], check=True)
            subprocess.run(["tar", "-xf", str(dtar), "-C", str(snaps[at])], check=True)
            dtar.unlink()
            read_only(snaps[at])
            print(f"mutate: {at} is a snapshot of its repository's HEAD {head[:12]}")
        bid = uuid.uuid4().hex[:12]
        print(f"mutate: {len(p['todo'])} mutant(s) at {p['sha'][:12]} -> {p['out']}")
        print(f"mutate: batch {bid}; each verdict goes to {p['log'].name} before the record")
        d = copy(tar, work, "check", {})
        bad = [why for at in snaps if (why := link_problem(d, at))]
        for m in p["todo"]:
            f = d / m["file"]
            if not f.exists() and not f.is_symlink():
                bad.append(f"{m['id']}: {m['file']} is not in the archived copy (export-ignore in .gitattributes?)")
                continue
            if f.is_symlink() or d.resolve() not in f.resolve().parents:
                bad.append(f"{m['id']}: {m['file']} is a symlink, or resolves outside the copy")
                continue
            b, n = f.read_bytes(), occurrences(f.read_bytes(), m["find"].encode())
            if n != 1 or b.replace(m["find"].encode(), m["replace"].encode(), 1) == b:
                bad.append(
                    f"{m['id']}: find occurs {n} times in {m['file']} in the archived copy (git archive "
                    "applies .gitattributes), or the replace changes no bytes there"
                )
        remove(d)
        if bad:
            raise Refused("; ".join(bad))
        for i, argv_ in enumerate(dict.fromkeys(tuple(m["argv"]) for m in p["todo"])):
            d = copy(tar, work, f"control-{i}", snaps)
            code, tail = run(list(argv_), d, p["env"], p["timeout"])
            remove(d)
            if code != 0:
                print(
                    f"mutate: refused: the control (unmutated {p['sha'][:12]}) fails: {' '.join(argv_)}: {tail} "
                    "(the copy is a `git archive` of the commit, with no .git and no uncommitted files, so a test "
                    "that needs either fails here)",
                    file=sys.stderr,
                )
                return 2
            print(f"control ok: {' '.join(argv_)}")
        # per file: the load check when `loads` names it (an extra check: it shows where the `loads` command
        # finds the file, not where the tests' own runner does), then always the probe
        loads = p["loads"]
        for f_, argv_ in dict.fromkeys((m["file"], tuple(m["argv"])) for m in p["todo"]):
            if loads and fnmatch.fnmatchcase(f_, loads["files"]):
                d = copy(tar, work, "loads", snaps)
                code, tail = run([expand(x, p["repo"]) for x in loads["command"]] + [f_], d, p["env"], p["timeout"])
                here = (d / f_).resolve()
                remove(d)
                if code != 0 or not tail or Path(tail).resolve() != here:
                    raise Refused(
                        f"the binding's env loads {f_} from {tail or '(nothing printed)'}, which is not the "
                        f"copy's {f_} (exit {code}): check the binding's env"
                    )
                print(f"loads ok: {f_}")
            # the probe: tests that still pass with the file emptied do not read this copy of it
            d = copy(tar, work, "probe", snaps)
            (d / f_).write_bytes(b"")
            code, tail = run(list(argv_), d, p["env"], p["timeout"])
            remove(d)
            if code == 0:
                raise Refused(
                    f"the named tests still pass with {f_} emptied: they do not read this copy of it "
                    f"(check the binding's env and tests): {' '.join(argv_)}"
                )
            print(
                f"probe ok: {f_}"
                + (
                    ""
                    if loads and fnmatch.fnmatchcase(f_, loads["files"])
                    else " (the weaker check alone: a test that reads the file as text passes it; a `loads` command in the "
                    "binding adds a direct one)"
                )
            )
        p["out"].parent.mkdir(parents=True, exist_ok=True)
        rows, timeouts, restored, changed = [], [], [], {}
        for m in p["todo"]:
            d = copy(tar, work, "mutant", snaps)
            f = d / m["file"]
            b = f.read_bytes()
            if b.replace(m["find"].encode(), m["replace"].encode(), 1) == b:
                raise Stopped(f"{m['id']}: the replace changed no bytes in its copy; nothing was planted")
            f.write_bytes(b.replace(m["find"].encode(), m["replace"].encode(), 1))
            code, tail = run(m["argv"], d, p["env"], p["timeout"])
            remove(d)
            if code is None:
                timeouts.append(m["id"])
                print(f"{m['id']} TIMEOUT {m['site']}: no row written ({tail})")
                continue
            verdict = "killed" if code else "survived"
            row = {
                "id": m["id"],
                "mutation": m["note"],
                "site": m["site"],
                "gate": m["gate"],
                "verdict": verdict,
                "disposition": None,
                "batch": bid,
                **({"data": {at: head for at, (_, head) in p["data"].items()}} if p["data"] else {}),
            }
            try:
                log_line(p["log"], {"batch": bid, "commit": p["sha"], "data": row.get("data", {}), "row": row})
            except OSError as e:
                print(f"{m['id']} {verdict} {m['site']}: {tail}")
                raise Stopped(f"{p['log']} cannot be written ({e}); {m['id']} ({verdict}) is not written") from e
            print(f"{m['id']} {verdict} {m['site']}: {tail}")
            try:
                back, diff = append_row(p["out"], row, rows)
            except (Refused, OSError, ValueError) as e:
                raise Stopped(f"{e}; {m['id']} ({verdict}) is in {p['log'].name}") from e
            if back:
                restored += back
                print(f"mutate: restored {', '.join(back)}: the file was replaced during the batch without them")
            for i, fields in diff:
                if changed.get(i) != fields:
                    changed[i] = fields
                    print(f"mutate: {i} was changed during the batch ({', '.join(fields)}); left as it is")
            rows.append(row)
        k = sum(r["verdict"] == "killed" for r in rows)
        print(
            f"mutate: {k} killed, {len(rows) - k} survived, {len(timeouts)} timed out"
            + (f" ({', '.join(timeouts)}: no row; raise the timeout or record them by hand)" if timeouts else "")
            + (
                ". Each survivor needs a disposition (open:, accepted: or test-added) from the reviewer."
                if len(rows) > k
                else ""
            )
        )
        if restored or changed:
            raise Stopped(
                "; ".join(
                    ([f"rows {', '.join(restored)} were restored"] if restored else [])
                    + [f"{i}'s {', '.join(f)} changed" for i, f in changed.items()]
                )
                + f": an edit replaced {p['out'].name} while the batch ran; records-check compares it with "
                f"{p['log'].name}; check that file before editing it again"
            )
        return 1 if timeouts else 0
    finally:
        writable(work)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
