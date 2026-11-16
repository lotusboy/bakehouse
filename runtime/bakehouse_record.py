"""`bakehouse record commit <record folder> -m <message> [-m <message> ...]`: commit one run folder on the records
branch. Standard library only. Run through bin/bakehouse.

In records mode (locator.runs_location) a project's run folders sit under `runs/` in its records worktree. That
worktree shares the main clone's git hooks, and bakehouse's hook launcher exits early on the records branch, so
bakehouse's pre-commit check does not run there (a project's own pre-commit hook still does). This command is the
commit that check would have judged:
1. The folder must be under the records worktree's `runs/` (exit 2). A project that keeps its run folders in
   its code tree commits them with git (exit 2).
2. It runs the predicted-moves gate's commit-time check (predict-gate.py run_hook, the hook's own function) on
   the folder's files, as they would be staged, in a scratch copy of the index. A refusal exits 1 and commits
   nothing. PREDICT_GATE_OVERRIDE="<reason>" [PREDICT_GATE_TASK=<id>] works as it does for the hook.
3. It stages and commits that folder only, by path (`git add -- <folder>`, `git commit -m ... -- <folder>`),
   retrying while git's index.lock is held, as the ledger's anchor does, then reports. Each -m passes to git
   unchanged, so a trailer such as `Prediction-approved: <decision id>` goes in its own -m. When git does not
   commit (a project's own pre-commit hook refuses, say), the folder is unstaged again: nothing is left staged.

Exit 0 committed; 1 the gate refused, or git did not commit (its message is shown); 2 bad usage or a folder this
command does not commit.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import locator
import ops_ledger

USAGE = "usage: bakehouse record commit <record folder> -m <message> [-m <message> ...]"


def say(text):
    print(f"bakehouse record: {text}")


def git(where, *args, env=None):
    return subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, check=False, env=env)


def predict_gate():
    spec = importlib.util.spec_from_file_location("predict_gate", Path(__file__).resolve().with_name("predict-gate.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse(argv):
    """(folder, messages), or None for bad usage."""
    if len(argv) < 4 or argv[0] != "commit" or argv[1].startswith("-"):
        return None
    msgs, rest = [], argv[2:]
    while rest:
        if rest[0] != "-m" or len(rest) < 2:
            return None
        msgs.append(rest[1])
        rest = rest[2:]
    return argv[1], msgs


def checked(rec: Path, rel: str, team: Path) -> int:
    """The hook's check on the folder, staged into a copy of the records worktree's index: the real index is
    never touched before the check passes."""
    index = Path(git(rec, "rev-parse", "--path-format=absolute", "--git-path", "index").stdout.strip())
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "index"
        if index.is_file():
            shutil.copyfile(index, scratch)
        env = dict(os.environ, GIT_INDEX_FILE=str(scratch))
        p = git(rec, "add", "-A", "--", rel, env=env)
        if p.returncode:
            say(f"cannot stage {rel} to check it: {(p.stderr or p.stdout).strip()}")
            return 1
        return predict_gate().run_hook(rec, env, [rel], team)


def commit(rec: Path, rel: str, msgs: list) -> str | None:
    """Stage and commit the folder by path; None when committed, else git's reason. A held index.lock is
    retried as ops_ledger.anchor retries it."""
    why = ""
    for i in range(ops_ledger.ANCHOR_TRIES):
        p = git(rec, "add", "-A", "--", rel)
        if p.returncode == 0:
            p = git(rec, "commit", "-q", *[x for m in msgs for x in ("-m", m)], "--", rel)
        if p.returncode == 0:
            return None
        why = (p.stderr or p.stdout).strip()
        if "index.lock" not in why:
            break
        time.sleep(0.2 * 2**i)
    return why or "git commit failed"


def main(argv, root) -> int:
    got = parse(argv)
    if got is None:
        print(USAGE, file=sys.stderr)
        return 2
    folder = Path(os.path.normpath(Path(got[0]).expanduser().absolute()))
    if not folder.is_dir():
        say(f"{folder} is not a folder")
        return 2
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    try:
        project = locator.records_owner(folder, env) or locator.find(folder, env)
    except locator.SetupNeeded as e:
        say(str(e))
        return 2
    if project is None:
        say(f"{folder} is in no project with a team (no .team/binding.json), so it has no records worktree")
        return 2
    try:
        binding = json.loads(project.binding.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        say(f"cannot read the binding {project.binding}: {e}")
        return 2
    try:
        rec, runs, mode = locator.runs_location(project, binding if isinstance(binding, dict) else {"runs": None})
    except locator.BadRuns as e:
        say(f"the binding {project.binding}: {e}")
        return 2
    if mode == "tree":
        say("this project keeps run folders in its code tree; commit them with git")
        return 2
    base = (rec / runs).resolve()
    real = folder.resolve()
    if base not in real.parents:
        say(f"{folder} is not a run folder: it commits a folder under {base}/ only")
        return 2
    rel = real.relative_to(rec.resolve()).as_posix()
    if checked(rec, rel, project.root):
        say(f"nothing committed: the predicted-moves gate refused {rel}")
        return 1
    why = commit(rec, rel, got[1])
    if why:
        git(rec, "reset", "-q", "--", rel)  # git add staged it; a failed commit leaves the index as it found it
        say(f"nothing committed: git did not commit {rel} in {rec}: {why}")
        return 1
    say(f"committed {rel} on the {locator.RECORDS_BRANCH} branch: "
        f"{git(rec, 'rev-parse', '--short', 'HEAD').stdout.strip()} in {rec}")
    return 0
