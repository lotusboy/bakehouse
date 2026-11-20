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

There is no registry and no text matching of paths. git gives the repo's real path; the records path
is compared after resolving both sides.

Usage from a shell script: `python3 locator.py [--owner] <field> [folder]` prints one path. Exit 3: no team here.
Exit 4: a team, but setup is needed; the message says what to run. --owner: a folder inside a project's records
worktree answers for that project (records_owner), as a path a brief names is judged.

Run folders (record folders): runs_location says where a project keeps them, from its binding's `runs` field:
`runs/` in the records worktree (records mode), or the binding's `reviews` in the code tree (tree mode).
"""

from __future__ import annotations

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
        return self.records / "ops" / "events.jsonl"

    @property
    def lanes_ledger(self) -> Path:
        return self.records / "ops" / "lanes.jsonl"

    @property
    def decisions_ledger(self) -> Path:
        return self.records / "decisions" / "ledger.jsonl"

    @property
    def decisions_view(self) -> Path:
        return self.records / "DECISIONS.md"

    @property
    def spend_ledger(self) -> Path:
        return self.records / "spend" / "ledger.jsonl"

    @property
    def ratify_ledger(self) -> Path:
        return self.records / "ratifications" / "ledger.jsonl"

    @property
    def tool_lessons(self) -> Path:
        return self.records / "TOOLS.md"

    @property
    def health_register(self) -> Path:
        return self.records / "registers" / "health.json"

    @property
    def driver_register(self) -> Path:
        return self.records / "registers" / "drivers.json"


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
    return Project(root=top, team=team, records=records)


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
    if not argv or argv[0] not in FIELDS:
        print(f"usage: locator.py [--owner] <{'|'.join(FIELDS)}> [folder]", file=sys.stderr)
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
    print(getattr(p, argv[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
