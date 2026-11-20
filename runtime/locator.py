"""The one place bakehouse finds a project and the files it keeps. Standard library only.

Every hook and script asks this module, so path identity is computed once (design 05 §5):
1. The project folder is the given folder, else the current one: the repo git runs in comes first.
   $CLAUDE_PROJECT_DIR is used only when that folder is in no git repo. The harness sets the variable for
   plugin hooks only, not for Bash tool commands or git hooks, so it cannot be the first answer.
2. `git rev-parse --show-toplevel` there gives the repo.
3. The repo's team facts are in `<repo>/.team/` when `.team/binding.json` exists, else in the sidecar
   folder named by the repo's git config `bakehouse.sidecar` (a repo another party owns).
4. The team records are in the folder named by git config `bakehouse.records`, which `bakehouse setup`
   writes: the records worktree beside the main clone, or, for a lane clone, the main clone's one.
5. No repo, or no binding: the repo has no team, and the caller does nothing. Inside the records
   worktree itself (on the records branch, with no `.team/binding.json` at its top) the answer is the same.

Ledgers (format 2, 0.8.0): once `bakehouse setup migrate` has written `FORMAT` at the records root, each of the five
ledgers is read from its legacy file (frozen: never appended again) and then from one file per writer, in the
folder named like the legacy file without `.jsonl` (`ops/events/<writer>.jsonl`, ...). A tool appends only to
its own writer's file; the writer id is the records worktree's git config `bakehouse.writer`. ledger_files is the
one read order, writer_file the one write path. With no FORMAT (format 1) both are the legacy file, as before.

There is no registry and no text matching of paths. git gives the repo's real path; the records path
is compared after resolving both sides.

Usage from a shell script: `python3 locator.py [--owner] [--read | --write] <field> [folder]` prints one path; with
--read, a ledger's files in read order, one per line; with --write, the file this writer appends to. Exit 3: no team here.
Exit 4: a team, but setup is needed; the message says what to run. --owner: a folder inside a project's records
worktree answers for that project (records_owner), as a path a brief names is judged.

Run folders (record folders): runs_location says where a project keeps them, from its binding's `runs` field:
`runs/` in the records worktree (records mode), or the binding's `reviews` in the code tree (tree mode).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

NO_TEAM, SETUP_NEEDED = 3, 4
RECORDS_BRANCH = "bakehouse-records"


class SetupNeeded(Exception):
    """The repo has a binding, but its records cannot be found. The message names the fix."""


class BadFormat(SetupNeeded):
    """The records folder's FORMAT is one this bakehouse cannot read, or a frozen legacy ledger changed after
    migration. The message names the file and the fix."""


# The five ledgers: the locator field and the legacy file, a path in the records worktree. In format 2 each one's
# writer files are <the legacy file without .jsonl>/<writer>.jsonl.
LEDGERS = {"ops_ledger": "ops/events.jsonl", "lanes_ledger": "ops/lanes.jsonl",
           "decisions_ledger": "decisions/ledger.jsonl", "spend_ledger": "spend/ledger.jsonl",
           "ratify_ledger": "ratifications/ledger.jsonl"}
FORMAT_FILE = "FORMAT"
FORMAT = 2  # the format this bakehouse writes; no FORMAT file is format 1
FORMAT_VERSION = "0.8.0"  # the first bakehouse that reads FORMAT, written into it for an older one to name
ABSENT = "absent"
WRITER_KEY = "bakehouse.writer"
WRITER = re.compile(r"[A-Za-z0-9-]{1,32}")
MIGRATE = "bakehouse setup migrate"


def machine_dir(env=os.environ) -> Path:
    """The fixed per-machine folder: logs, locks, the seen-agents file, the personal role table, the role
    choice. Outside git and outside the plugin's folders, which the harness replaces on update."""
    if env.get("BAKEHOUSE_MACHINE_DIR"):
        return Path(env["BAKEHOUSE_MACHINE_DIR"])
    state = env.get("XDG_STATE_HOME") or str(Path(env.get("HOME", str(Path.home()))) / ".local" / "state")
    return Path(state) / "bakehouse"


def _git(where: Path, *args: str) -> str | None:
    try:
        p = subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, check=False)
    except OSError:
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def toplevel(folder) -> Path | None:
    out = _git(Path(folder), "rev-parse", "--show-toplevel")
    return Path(out) if out else None


@dataclass(frozen=True)
class Project:
    root: Path
    team: Path
    records: Path

    # Project facts: committed with the project (design 05 §6).
    @property
    def binding(self) -> Path:
        return self.team / "binding.json"

    @property
    def expert_role(self) -> Path:
        return self.team / "expert-role.md"

    @property
    def operations(self) -> Path:
        return self.team / "OPERATIONS.md"

    @property
    def rows_to_keep(self) -> Path:
        return self.team / "rows-to-keep.md"

    @property
    def decision_config(self) -> Path:
        return self.team / "decision-config.json"

    # Team records: the records branch (design 05 §7.1).
    @property
    def ops_ledger(self) -> Path:
        return self.records / LEDGERS["ops_ledger"]

    @property
    def lanes_ledger(self) -> Path:
        return self.records / LEDGERS["lanes_ledger"]

    @property
    def decisions_ledger(self) -> Path:
        return self.records / LEDGERS["decisions_ledger"]

    @property
    def decisions_view(self) -> Path:
        return self.records / "DECISIONS.md"

    @property
    def spend_ledger(self) -> Path:
        return self.records / LEDGERS["spend_ledger"]

    @property
    def ratify_ledger(self) -> Path:
        return self.records / LEDGERS["ratify_ledger"]

    @property
    def tool_lessons(self) -> Path:
        return self.records / "TOOLS.md"

    @property
    def health_register(self) -> Path:
        return self.records / "registers" / "health.json"

    @property
    def driver_register(self) -> Path:
        return self.records / "registers" / "drivers.json"

    # A ledger's files (format 2): ledger_files and writer_file, below.
    def read_files(self, field: str) -> list:
        return ledger_files(self.records, field)

    def write_file(self, field: str) -> Path:
        return writer_file(self.records, field)


FIELDS = sorted(n for n, v in vars(Project).items() if isinstance(v, property)) + ["root", "team", "records"]


def project_dir(folder=None, env=os.environ) -> Path:
    """The folder the project is found from: the given folder, else the current one, when it is in a git repo;
    else $CLAUDE_PROJECT_DIR when set (R0.1 R2-8: the repo git runs in comes first)."""
    here = Path(folder) if folder else Path.cwd()
    if env.get("CLAUDE_PROJECT_DIR") and toplevel(here) is None:
        return Path(env["CLAUDE_PROJECT_DIR"])
    return here


def hook_folder(env=os.environ, stream=None) -> Path:
    """The folder a SessionStart hook judges: $CLAUDE_PROJECT_DIR when set, else the hook input's `cwd` (JSON on
    stream, read only then), else the process cwd. A hook never judges the process cwd first: after a
    compaction it is wherever the shell last was (S2 fix 1). Scripts keep project_dir's rule."""
    if env.get("CLAUDE_PROJECT_DIR"):
        return Path(env["CLAUDE_PROJECT_DIR"])
    try:
        import json
        import select
        if stream is not None and not stream.isatty() and select.select([stream], [], [], 0.5)[0]:
            cwd = json.loads(stream.read() or "{}").get("cwd")
            if isinstance(cwd, str) and cwd:
                return Path(cwd)
    except (OSError, ValueError, AttributeError):
        pass
    return Path.cwd()


def find(folder=None, env=os.environ) -> Project | None:
    """The project at `folder`, or None when it has no team. Raises SetupNeeded when it has a binding
    but no usable records pointer; never falls back to a ledger inside the project tree (R2-16)."""
    top = toplevel(project_dir(folder, env))
    if top is None:
        return None
    # A binding that exists but cannot be read (a dangling link, a folder) still means a team: the caller
    # then fails reading it and refuses, naming it, instead of treating the repo as having no team.
    if os.path.lexists(top / ".team" / "binding.json"):
        team = top / ".team"
    else:
        side = _git(top, "config", "--get", "bakehouse.sidecar")
        if not side or not os.path.lexists(Path(side) / "binding.json"):
            return None
        team = Path(side)
    if team != top / ".team" and _git(top, "symbolic-ref", "-q", "HEAD") == f"refs/heads/{RECORDS_BRANCH}":
        # inside the records worktree (known by its branch, with no binding of its own): nothing to do here
        return None
    rec = _git(top, "config", "--get", "bakehouse.records")
    if not rec:
        raise SetupNeeded(f"{top} has a team binding but no records folder: run `bakehouse setup` in the "
                          "main clone, or `bakehouse setup --pointer <main clone>` in a lane clone")
    records = Path(rec)
    if not records.is_absolute():  # a hand-set relative pointer is read from the repo root
        records = Path(os.path.normpath(top / records))
    if records.resolve() == top.resolve() or top.resolve() in records.resolve().parents:
        raise SetupNeeded(f"{top}: the records folder {records} is the project's own folder or inside it, and "
                          "records never live in the project tree: run `bakehouse setup` in the main clone, or "
                          "`bakehouse setup --pointer <main clone>` in a lane clone")
    gitdirs = {_git(records, "rev-parse", "--path-format=absolute", d) for d in ("--git-dir", "--git-common-dir")}
    if not records.is_dir() or toplevel(records) is None or toplevel(records).resolve() != records.resolve() \
            or (_git(records, "symbolic-ref", "-q", "HEAD") != f"refs/heads/{RECORDS_BRANCH}" and len(gitdirs) == 1):
        raise SetupNeeded(f"{top}: the records folder {records} is missing or is not a records checkout (a "
                          f"worktree on the branch {RECORDS_BRANCH}; the main clone may have moved): run "
                          "`bakehouse setup` again")
    if _git(records, "symbolic-ref", "-q", "HEAD") != f"refs/heads/{RECORDS_BRANCH}":
        # a linked worktree off its branch: the records worktree, detached or switched
        raise SetupNeeded(f"{top}: the records folder {records} is not on the branch {RECORDS_BRANCH} (a detached "
                          f"HEAD, as in a rebase, or another branch): run `git -C {records} switch {RECORDS_BRANCH}`")
    if len(gitdirs) == 1:
        # a checkout on the records branch with its own git folder: records committed there never reach the project
        raise SetupNeeded(f"{top}: the records folder {records} is a separate clone on the branch {RECORDS_BRANCH}, "
                          f"not a worktree of {top} or of its main clone, so records committed there never reach the "
                          "project: run `bakehouse setup` in the main clone, or `bakehouse setup --pointer <main "
                          "clone>` in a lane clone")
    common = Path(_git(records, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    own = _git(top, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not own or common.resolve() != Path(own).resolve():
        # a lane clone: its records worktree is a worktree of its main clone, judged as `setup --pointer` judges it
        why = main_clone_problem(top, common.parent)
        if why:
            raise SetupNeeded(f"{top}: the records folder {records} is a worktree of {common.parent}, and {why}: run "
                              "`bakehouse setup --pointer <main clone>` in a lane clone, or `bakehouse setup` in the "
                              "main clone")
    records_format(records)  # BadFormat: an unknown FORMAT, or a frozen ledger that changed
    return Project(root=top, team=team, records=records)


def file_sha(path: Path) -> str:
    """A file's sha256, or ABSENT when it does not exist: what FORMAT records for each frozen ledger."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError:
        return ABSENT


def records_format(records) -> int:
    """The records folder's format: 1 with no FORMAT file (today's single files), else 2. BadFormat: a FORMAT this
    bakehouse cannot read (naming the bakehouse version it says it needs), or a frozen legacy ledger whose sha256 is
    not the one FORMAT records (naming it: an older bakehouse may still be writing it)."""
    path = Path(records) / FORMAT_FILE
    if not os.path.lexists(path):
        if _git(Path(records), "cat-file", "-e", f"HEAD:{FORMAT_FILE}") is not None:
            # git holds it and the disk does not: read as format 1, the tools would append to the frozen files
            raise BadFormat(f"{path} is missing, and the records branch's HEAD holds it (records format {FORMAT}): "
                            f"restore it with `git -C {records} checkout -- {FORMAT_FILE}`")
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BadFormat(f"{path} cannot be read as a records format ({e}): restore it from git "
                        f"(`git -C {records} checkout -- {FORMAT_FILE}`)")
    fmt = data.get("format") if isinstance(data, dict) else None
    if fmt != FORMAT:
        need = data.get("bakehouse") if isinstance(data, dict) else None
        raise BadFormat(f"{path} says records format {fmt!r}, which this bakehouse does not read (it reads format 1, "
                        f"no FORMAT file, and format {FORMAT}): it needs bakehouse "
                        f"{need if isinstance(need, str) else 'of a later version'}; update bakehouse on this machine")
    frozen = data.get("frozen")
    if not isinstance(frozen, dict) or set(frozen) != set(LEDGERS.values()):
        raise BadFormat(f"{path} does not list the five frozen ledgers ({', '.join(sorted(LEDGERS.values()))}): "
                        f"restore it from git (`git -C {records} checkout -- {FORMAT_FILE}`)")
    for rel in sorted(frozen):
        got = file_sha(Path(records) / rel)
        if got != frozen[rel]:
            raise BadFormat(f"the frozen ledger {rel} in {records} is not the file {FORMAT_FILE} froze (sha256 "
                            f"{frozen[rel]}, now {got}): since migration only writer files are appended, so another "
                            f"bakehouse, older than {FORMAT_VERSION}, may still be writing it. Update bakehouse on every "
                            f"machine, then restore {rel} as {FORMAT_FILE} froze it (`git -C {records} log -- "
                            f"{FORMAT_FILE}` names the commit) and record its new lines again with this bakehouse")
    return FORMAT


def writer_folder(legacy: Path) -> Path:
    """A ledger's writer files' folder: the legacy file's path without `.jsonl` (ops/events.jsonl -> ops/events/)."""
    return Path(legacy).with_suffix("")


def writer_id(records) -> str | None:
    """The records worktree's writer id (git config bakehouse.writer), or None when it has none. One per records
    worktree: a lane clone's records worktree is its main clone's, so its lanes share the main clone's id."""
    return _git(Path(records), "config", "--local", "--get", WRITER_KEY) or None  # never a global or system value


def ledger_files(records, field: str) -> list:
    """The files of the ledger `field` (a key of LEDGERS), in read order: the legacy file first (it may not exist),
    then, in format 2, every writer file, sorted by name: those on disk and those the records worktree's HEAD holds,
    so a deleted writer file is still read as git holds it. The one read order for every reader. BadFormat: as
    records_format."""
    legacy = Path(records) / LEDGERS[field]
    if records_format(records) == 1:
        return [legacy]
    folder = writer_folder(legacy)
    names = {p.name for p in folder.glob("*.jsonl")} if folder.is_dir() else set()
    held = _git(Path(records), "ls-tree", "--name-only", "HEAD", "--", f"{writer_folder(LEDGERS[field])}/")
    names |= {Path(x).name for x in (held or "").splitlines() if x.strip()}
    return [legacy] + [folder / n for n in sorted(names) if n.endswith(".jsonl") and WRITER.fullmatch(n[:-6])]


def writer_file(records, field: str) -> Path:
    """The file this records worktree's writer appends to for the ledger `field`: the legacy file in format 1; in
    format 2, <writer folder>/<writer id>.jsonl. SetupNeeded: format 2 and no writer id (or one that is not 1 to 32
    letters, digits or hyphens); BadFormat: as records_format."""
    legacy = Path(records) / LEDGERS[field]
    if records_format(records) == 1:
        return legacy
    w = writer_id(records)
    if not w:
        raise SetupNeeded(f"the records folder {records} keeps one file per writer (records format {FORMAT}), and its "
                          f"records worktree has no writer id (git config {WRITER_KEY}), so nothing is written: run "
                          "`bakehouse setup` in the main clone, which sets one")
    if not WRITER.fullmatch(w):
        raise SetupNeeded(f"the writer id {w!r} (git config {WRITER_KEY} in {records}) is not 1 to 32 letters, digits "
                          f"or hyphens, so nothing is written: set it with `git -C {records} config {WRITER_KEY} "
                          "<name>`, or unset it and run `bakehouse setup` in the main clone")
    return writer_folder(legacy) / f"{w}.jsonl"


def main_clone_problem(top: Path, main: Path) -> str | None:
    """Why the clone at main is not the main clone of the clone at top, or None when it is: a shallow top's HEAD
    commit is not in main, or the two share no root commit. The one rule for `bakehouse setup --pointer` and for
    find, which judges a lane clone's records worktree by its repository."""
    if _git(top, "rev-parse", "--is-shallow-repository") == "true":
        # a shallow clone's root commit is its cut-off point: the main clone must hold its HEAD commit instead
        head = _git(top, "rev-parse", "HEAD")
        if not head or _git(main, "cat-file", "-e", f"{head}^{{commit}}") is None:
            return (f"{main} does not hold {top}'s HEAD commit ({head or 'none'}), so it is not the main clone of "
                    "this shallow clone")
        return None
    roots = [set((_git(w, "rev-list", "--max-parents=0", "HEAD") or "").split()) for w in (top, main)]
    if not roots[0] & roots[1]:
        return f"{main} shares no root commit with {top}, so it is not a clone of this project"
    return None


DEFAULT_REVIEWS = "axis/runs/"
RUNS = "runs/"
# A brief the PM saved in a record folder before a dispatch (skills/pm/SKILL.md, "The records the PM keeps"):
# brief.<role>.<round>.md. It is the PM's, so no reader of a record folder takes it as findings or as an agent's
# own record.
BRIEF = re.compile(r"brief\..+\.md")


def reviews_problem(v, root=None) -> str | None:
    """Why the binding's `reviews` value v is not a folder in the code tree, or None when it is one: it is not
    text, or is blank, or is a full path, or has a `..` part. The one rule for the role hook (agent-roles.py
    reviews_folder) and preflight check 3. root: the repository, so a full path is said to be inside it or
    outside it; without root a full path is only named as one."""
    if not isinstance(v, str) or not v.strip():
        return "is not a path"
    if os.path.isabs(v):
        if root is None:
            return "is a full path: name the folder relative to the repository root"
        p, top = Path(v).resolve(), Path(root).resolve()
        if p != top and top not in p.parents:
            return "is outside the repository"
        return "is a full path inside the repository: name the folder relative to the repository root" + (
            f", as '{p.relative_to(top).as_posix()}/'" if p != top else "")
    if ".." in Path(v).parts:
        n = os.path.normpath(v)
        if n == ".." or n.startswith("../"):
            return "is outside the repository"
        return f"has a '..' part: name the folder without one, as '{n}/'"
    return None


class BadRuns(ValueError):
    """The binding's `runs` field is not one the run-folder rule reads; the message names the fix."""


def runs_mode(binding: dict) -> str:
    """"records" or "tree": the binding's `runs`; without it, "tree" when the binding names `reviews` (a binding
    from before records mode keeps its run folders where they are), else "records". BadRuns: `runs` is any other
    value, or "records" beside a `reviews` folder."""
    if "runs" not in binding:
        return "tree" if "reviews" in binding else "records"
    runs = binding["runs"]
    if runs not in ("records", "tree"):
        raise BadRuns(f'the binding\'s "runs" is {runs!r}: it is "records" (run folders on the records branch, '
                      f'under {RUNS}) or "tree" (in the code tree, under "reviews")')
    if runs == "records" and "reviews" in binding:
        raise BadRuns('the binding says "runs": "records" and names a "reviews" folder: in records mode the run '
                      f'folders are {RUNS} in the records worktree, so drop "reviews", or say "runs": "tree"')
    return runs


def runs_location(project, binding: dict, repo=None) -> tuple[Path, str, str]:
    """Where the project keeps its run (record) folders: (the checkout git reads them in, the folder in it, mode).
    The one answer for every reader of a record folder (close, seal, records-check, the signals, the role hook).
    Records mode: (the records worktree, "runs/", "records"). Tree mode, as before records mode: (repo, else the
    project's root; the binding's `reviews` as written, or "axis/runs/" when it names none; "tree"). Each reader
    checks a `reviews` value as it did before. With no project (a binding file given for a repo with no team)
    there is no records worktree: tree mode in repo with the default folder, as before records mode. BadRuns: a
    bad `runs` (runs_mode)."""
    mode = runs_mode(binding)
    if mode == "records" and project is None and repo is not None:
        mode = "tree"
    if mode == "tree":
        return (Path(repo) if repo is not None else project.root), binding.get("reviews", DEFAULT_REVIEWS), mode
    if project is None:
        raise BadRuns(f"the binding keeps run folders on the {RECORDS_BRANCH} branch, and no project was given, "
                      "so there is no records worktree")
    return project.records, RUNS, mode


class BadCodeRoots(ValueError):
    """The binding's signal.codeRoots or records.codeRoots is not a list of folders; the message names the field
    and the fix."""


OUTSIDE_RECORDS = "(outside the records worktree)"


def ledger_name(ledger, project) -> str:
    """The ops ledger as a register or a refusal names it, never by a machine path: in the project's records
    worktree, by its path there; anywhere else (or with no project), by its file name under OUTSIDE_RECORDS."""
    if project is not None:
        try:
            return Path(ledger).resolve().relative_to(Path(project.records).resolve()).as_posix()
        except ValueError:
            pass
    return f"{OUTSIDE_RECORDS}/{Path(ledger).name}"


def is_code_root(r) -> bool:
    """One entry of a code-roots list: a folder name, a string with more than slashes in it. The one rule for
    code_roots and preflight check 4."""
    return isinstance(r, str) and bool(r.strip("/"))


def code_roots(binding: dict):
    """The folders whose commits are code: the binding's signal.codeRoots when `signal` is an object that names
    them, else its records.codeRoots, else None. The one answer for close, seal, seal-code, records-check and
    preflight. BadCodeRoots: the field used is not a list of folder names (preflight check 2 and records-check
    name the full rule for records.codeRoots, check 4 for signal.codeRoots)."""
    sig = binding.get("signal")
    if isinstance(sig, dict) and sig.get("codeRoots"):
        field, roots = "signal.codeRoots", sig["codeRoots"]
    else:
        rec = binding.get("records")
        field, roots = "records.codeRoots", rec.get("codeRoots") if isinstance(rec, dict) else None
    if not roots:
        return None
    if not isinstance(roots, list) or not all(is_code_root(r) for r in roots):
        raise BadCodeRoots(f"the binding's {field} = {json.dumps(roots)} is not a list of repository folders, "
                           'for example ["src/"]')
    return roots


class BadMainFollows(ValueError):
    """The binding's mainFollows is not {"branch": "<a branch>"}; the message names the fix."""


def main_follows(binding: dict) -> str | None:
    """The branch the binding's mainFollows names: engine changes merge into it as each work order closes, and it
    merges into a product branch only between work orders, before the next baseline. None when the binding has no
    mainFollows (the check is off). BadMainFollows: any other form. The one rule for seal, close, session-start and
    preflight."""
    if "mainFollows" not in binding:
        return None
    v = binding["mainFollows"]
    if not isinstance(v, dict) or not isinstance(v.get("branch"), str) or not v["branch"].strip():
        raise BadMainFollows(f'the binding\'s "mainFollows" = {json.dumps(v)} is not an object naming a branch: '
                             'write {"branch": "main"}, naming the branch engine changes merge into')
    return v["branch"]


def main_clone(project: Project) -> Path | None:
    """The main clone: the repository of the project's records worktree, its git common dir's parent (find makes
    sure a lane clone's records worktree is a worktree of its main clone). None when git cannot say."""
    common = _git(project.records, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common).parent if common else None


def main_tip(project: Project, branch: str) -> str | None:
    """The commit the main clone's own `branch` names, read in the main clone with no fetch: never a checkout's own
    `branch` or its origin/<branch>, which can be stale. None when the main clone has no such branch."""
    main = main_clone(project)
    return _git(main, "rev-parse", "-q", "--verify", f"refs/heads/{branch}^{{commit}}") if main else None


def missing_engine_commits(project: Project, tip: str, since: str, roots) -> int | None:
    """How many commits reachable from tip (a main clone commit) and not from since (a commit of the project's
    checkout) touch the code roots (any commit when roots is None); None when git cannot count them. Counted in the
    checkout with the main clone's objects readable too, so a lane clone needs no fetch."""
    env = dict(os.environ)
    common = _git(project.records, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common:  # git skips it silently when it is the checkout's own object folder (the main clone itself)
        env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = str(Path(common) / "objects")
    try:
        p = subprocess.run(["git", "-C", str(project.root), "rev-list", "--count", tip, "--not", since, "--",
                            *(roots or [])], capture_output=True, text=True, check=False, env=env)
    except OSError:
        return None
    return int(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip().isdigit() else None


def records_owner(folder, env=os.environ) -> Project | None:
    """The project whose records worktree holds folder (a folder not made yet: its nearest parent), or None. git's
    common dir names the main clone, and that clone's `bakehouse.records` pointer must name this checkout.
    SetupNeeded: folder is in a checkout on the records branch that is not a linked worktree (a separate clone)."""
    p = Path(folder)
    while not p.is_dir() and p != p.parent:
        p = p.parent
    top = toplevel(p)
    common = _git(p, "rev-parse", "--path-format=absolute", "--git-common-dir") if top else None
    if not common or Path(common).parent.resolve() == top.resolve():
        if common and not os.path.lexists(top / ".team" / "binding.json") \
                and _git(top, "symbolic-ref", "-q", "HEAD") == f"refs/heads/{RECORDS_BRANCH}":
            raise SetupNeeded(f"{folder} is in {top}, a separate clone on the branch {RECORDS_BRANCH}, not a records "
                              "worktree (a worktree of a main clone), so it maps back to no project: run "
                              "`bakehouse setup` in the main clone, which makes its records worktree")
        return None  # not in a git repo, or in a main checkout: no records worktree
    try:
        project = find(Path(common).parent, env)
    except SetupNeeded:
        return None
    return project if project is not None and project.records.resolve() == top.resolve() else None


def main(argv) -> int:
    owner = bool(argv) and argv[0] == "--owner"
    argv = argv[1:] if owner else argv
    files = argv[0] if argv and argv[0] in ("--read", "--write") else None
    argv = argv[1:] if files else argv
    if not argv or argv[0] not in FIELDS or (files and argv[0] not in LEDGERS):
        print(f"usage: locator.py [--owner] <{'|'.join(FIELDS)}> [folder]\n"
              f"       locator.py [--owner] --read|--write <{'|'.join(LEDGERS)}> [folder]", file=sys.stderr)
        return 2
    try:
        p = find(argv[1] if len(argv) > 1 else None)
        if p is None and owner:
            p = records_owner(argv[1] if len(argv) > 1 else Path.cwd())
    except SetupNeeded as e:
        print(f"bakehouse: {e}", file=sys.stderr)
        return SETUP_NEEDED
    if p is None:
        return NO_TEAM
    try:
        if files == "--read":
            print("\n".join(str(x) for x in p.read_files(argv[0])))
        elif files == "--write":
            print(p.write_file(argv[0]))
        else:
            print(getattr(p, argv[0]))
    except SetupNeeded as e:
        print(f"bakehouse: {e}", file=sys.stderr)
        return SETUP_NEEDED
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
