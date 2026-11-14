"""The one place bakehouse finds a project and the files it keeps. Standard library only.

Every hook and script asks this module, so path identity is computed once (design 05 §5):
1. The project folder is $CLAUDE_PROJECT_DIR when set, else the given folder, else the current one.
   The harness sets the variable for plugin hooks only, not for Bash tool commands or git hooks, so a
   script run from Bash or git uses the repo git runs in.
2. `git rev-parse --show-toplevel` there gives the repo.
3. The repo's team facts are in `<repo>/.team/` when `.team/binding.json` exists, else in the sidecar
   folder named by the repo's git config `bakehouse.sidecar` (a repo another party owns).
4. The team records are in the folder named by git config `bakehouse.records`, which `bakehouse setup`
   writes: the records worktree beside the main clone, or, for a lane clone, the main clone's one.
5. No repo, or no binding: the repo has no team, and the caller does nothing. Inside the records
   worktree itself the answer is the same, so the git hooks do nothing there.

There is no registry and no text matching of paths. git gives the repo's real path; the records path
is compared after resolving both sides.

Usage from a shell script: `python3 locator.py <field> [folder]` prints one path. Exit 3: no team here.
Exit 4: a team, but setup is needed; the message says what to run.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

NO_TEAM, SETUP_NEEDED = 3, 4


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
    if env.get("CLAUDE_PROJECT_DIR"):
        return Path(env["CLAUDE_PROJECT_DIR"])
    return Path(folder) if folder else Path.cwd()


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
    rec = _git(top, "config", "--get", "bakehouse.records")
    if rec and Path(rec).resolve() == top.resolve():
        return None  # inside the records worktree: nothing to do here
    if not rec:
        raise SetupNeeded(f"{top} has a team binding but no records folder: run `bakehouse setup` in the "
                          "main clone, or `bakehouse setup --pointer <main clone>` in a lane clone")
    records = Path(rec)
    if not records.is_dir() or toplevel(records) is None or toplevel(records).resolve() != records.resolve():
        raise SetupNeeded(f"{top}: the records folder {records} is missing or is not a records checkout "
                          "(the main clone may have moved): run `bakehouse setup` again")
    return Project(root=top, team=team, records=records)


def main(argv) -> int:
    if not argv or argv[0] not in FIELDS:
        print(f"usage: locator.py <{'|'.join(FIELDS)}> [folder]", file=sys.stderr)
        return 2
    try:
        p = find(argv[1] if len(argv) > 1 else None)
    except SetupNeeded as e:
        print(f"bakehouse: {e}", file=sys.stderr)
        return SETUP_NEEDED
    if p is None:
        return NO_TEAM
    print(getattr(p, argv[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
