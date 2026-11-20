"""The ops ledger's one append path: every line is written under the ledger's lock, and once the chain
has started, every line carries `prev`, the sha256 of the bytes of the line before it.

The lock is an exclusive flock on a lock file beside the ledger, `.<ledger name>.lock`, which the
records worktree's .gitignore names. It is not the ledger file: the ledger is tracked in git, and a checkout,
merge or reset replaces the file (a new inode) while a writer holds a lock on the old one; git never
touches an ignored file. No part of its path comes from the environment, so every writer of one
ledger takes one lock, through a symlinked path too.

The anchor: `anchor` commits the ledger file, by path, into the git repository
that holds it, with a `Sealed-line: <sha256 of the line>` trailer. `committed` reads the ledger as the
repository's HEAD holds it; close judges only the sealed events in it.

The ledger is read as bytes, so a line that is not UTF-8 never stops a write (the old ops-event.sh
only appended). A ledger whose last line has no newline gets the newline first, so a new line is
never joined onto a torn one. The chain starts at the first sealed event (`predict-gate.sh seal`);
events written before it stay as they are. `verify` reports every line it cannot read, every seal
without prev, and every broken link. Standard library only.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import subprocess
import time
from pathlib import Path

try:
    import fcntl
except (
    ImportError
):  # no fcntl on native Windows: single-writer behaviour, as decision.py
    fcntl = None


SEALED_KINDS = (
    "sealed",
    "revised",
    "retired",
)  # written only by predict-gate.sh seal | revise | retire


def line_sha(line: bytes) -> str:
    return hashlib.sha256(line).hexdigest()


def parse(line: bytes):
    """The line's JSON object, or None when it is not UTF-8 or not a JSON object."""
    try:
        ev = json.loads(line.decode("utf-8"))
    except ValueError:  # UnicodeDecodeError is a ValueError
        return None
    return ev if isinstance(ev, dict) else None


def chained(ev) -> bool:
    return isinstance(ev, dict) and "prev" in ev


ANCHOR_TRIES = 5


def lock_path(path: Path) -> Path:
    """Beside the ledger itself: a symlinked path, to the ledger or to a folder above it, takes one lock."""
    real = path.resolve()
    return real.with_name(f".{real.name}.lock")


@contextlib.contextmanager
def locked(path: Path):
    """Hold the ledger's lock (its own lock file, never the ledger)."""
    lp = lock_path(path)
    lp.parent.mkdir(parents=True, exist_ok=True)
    with lp.open("a") as f:
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)
        yield


def lines_of(raw: bytes) -> list:
    return [x for x in raw.split(b"\n") if x.strip()]


def append(path: Path, make, start: bool = False, then=None) -> dict:
    """Append make(earlier events) as one line, under the lock. make may raise to write nothing; it gets
    None for a line it cannot read. With start, or once an earlier line is chained, the event gets
    prev: the sha256 of the last line (None on an empty ledger). then(line bytes), when given, runs
    after the write, still under the lock (the anchor's commit)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path):
        raw = path.read_bytes() if path.exists() else b""
        lines = lines_of(raw)
        events = [parse(x) for x in lines]
        ev = make(events)
        if start or any(chained(e) for e in events):
            ev = dict(ev, prev=line_sha(lines[-1]) if lines else None)
        torn = b"\n" if raw and not raw.endswith(b"\n") else b""
        line = json.dumps(ev, ensure_ascii=False).encode("utf-8")
        with path.open("ab") as f:
            f.write(torn + line + b"\n")
        if then:
            then(line)
    return ev


def _top(path: Path):
    """The git repository that holds the ledger's path, found from its nearest existing folder: the ledger
    file, or its folder, may be gone while git still holds it."""
    here = path.parent
    while not here.is_dir() and here != here.parent:
        here = here.parent
    p = subprocess.run(
        ["git", "-C", str(here), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    return Path(p.stdout.strip()) if p.returncode == 0 else None


def anchor(path: Path, line: bytes, subject: str, trailer: str = "Sealed-line") -> str | None:
    """Commit the ledger file, by path only, with the line's trailer (Sealed-line; a close's own commit gives
    Closed-line, which sealed_trailers does not read). None when committed; else why not. git's index lock held
    by another git command is retried, then reported."""
    top = _top(path)
    if top is None:
        return f"{path.parent} is not inside a git repository"
    rel = path.resolve().relative_to(top.resolve()).as_posix()
    why = ""
    for i in range(ANCHOR_TRIES):
        p = subprocess.run(
            ["git", "-C", str(top), "add", "--", rel],
            capture_output=True,
            text=True,
            check=False,
        )
        if p.returncode == 0:
            p = subprocess.run(
                [
                    "git",
                    "-C",
                    str(top),
                    "commit",
                    "-q",
                    "-m",
                    subject,
                    "-m",
                    f"{trailer}: {line_sha(line)}",
                    "--",
                    rel,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        if p.returncode == 0:
            return None
        why = (p.stderr or p.stdout).strip()
        if "index.lock" not in why:
            break
        time.sleep(0.2 * 2**i)
    return why or "git commit failed"


def committed(path: Path, commit: str = "HEAD") -> bytes | None:
    """The ledger as a commit of its git repository holds it (HEAD by default); None when it does not."""
    top = _top(path)
    if top is None:
        return None
    rel = path.resolve().relative_to(top.resolve()).as_posix()
    p = subprocess.run(
        ["git", "-C", str(top), "show", f"{commit}:{rel}"],
        capture_output=True,
        check=False,
    )
    return p.stdout if p.returncode == 0 else None


def sealed_trailers(path: Path) -> list:
    """[(commit, sha256 of the line, the ledger's path in that commit)] for every Sealed-line trailer in the
    git history of the ledger, oldest first: the anchor's record of every line it committed, which no edit
    of the ledger file removes. --full-history keeps a commit that history simplification would prune (a
    merge resolved to one side); --follow keeps the trailers from before a move of the file."""
    top = _top(path)
    if top is None:
        return []
    rel = path.resolve().relative_to(top.resolve()).as_posix()
    p = subprocess.run(
        [
            "git",
            "-C",
            str(top),
            "log",
            "--full-history",
            "--follow",
            "--name-only",
            "--format=%x1e%H%x00%B%x00",
            "--",
            rel,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    out = []
    for rec in p.stdout.split("\x1e")[1:]:
        commit, body, names = (rec.split("\0") + ["", ""])[:3]
        at = next((x for x in names.split("\n") if x.strip()), rel)
        out += [
            (commit, x.split(":", 1)[1].strip(), at)
            for x in body.split("\n")
            if x.startswith("Sealed-line:")
        ]
    return out[::-1]


def committed_at(path: Path, commit: str, rel: str) -> bytes | None:
    """The file at rel (a path in the ledger's repository) as that commit holds it; None when it does not."""
    top = _top(path)
    if top is None:
        return None
    p = subprocess.run(
        ["git", "-C", str(top), "show", f"{commit}:{rel}"],
        capture_output=True,
        check=False,
    )
    return p.stdout if p.returncode == 0 else None


def verify(path: Path) -> tuple[bool, str]:
    """(whole, what to print). BROKEN, each with its line: a line that is not UTF-8 or not a JSON object,
    anywhere; a sealed, revised or retired event without prev, anywhere (the sealer chains every one);
    a chain whose first line is not a seal; from the chain's first line on, a line without prev, or
    whose prev is not the sha256 of the line before it."""
    rows = [
        (n, x) for n, x in enumerate(path.read_bytes().split(b"\n"), 1) if x.strip()
    ]
    evs = [parse(x) for _, x in rows]
    bad = {}
    for (n, x), ev in zip(rows, evs):
        if ev is None:
            try:
                x.decode("utf-8")
                bad[n] = "it is not a JSON object"
            except UnicodeDecodeError:
                bad[n] = "it is not UTF-8"
        elif ev.get("kind") in SEALED_KINDS and not chained(ev):
            bad[n] = (
                f"a {ev['kind']} event with no prev: the sealer chains every one, so it did not write this one"
            )
    start = next((i for i, ev in enumerate(evs) if chained(ev)), None)
    if start is not None:
        if evs[start].get("kind") != "sealed":
            bad.setdefault(
                rows[start][0],
                f"the chain's first line is a {evs[start].get('kind')} event, not a seal",
            )
        for i in range(start, len(rows)):
            n, ev = rows[i][0], evs[i]
            if ev is None:
                continue
            if not chained(ev):
                bad.setdefault(
                    n,
                    f"it carries no prev (the chain started at line {rows[start][0]})",
                )
            elif ev["prev"] != (line_sha(rows[i - 1][1]) if i else None):
                bad.setdefault(
                    n,
                    f"its prev does not match line {rows[i - 1][0] if i else '(none)'} "
                    "(an earlier line was edited, deleted or moved)",
                )
    if bad:
        return False, "\n".join(
            f"Chain: BROKEN at line {n}: {why}." for n, why in sorted(bad.items())
        )
    if start is None:
        return True, f"Chain: not started. {len(rows)} event(s), none chained."
    return True, (
        f"Chain: whole. {len(rows) - start} chained event(s), lines {rows[start][0]} to {rows[-1][0]}; "
        f"{start} earlier event(s) not chained."
    )
