"""`bakehouse setup`: make a clone ready for its team (design 05 §4.5, §4.6, §4.8, §10.1 item 8). Standard
library only. Run through bin/bakehouse.

  bakehouse setup [--pre-commit]                          in the main clone
  bakehouse setup --pointer <main clone> [--pre-commit]   in a lane clone

In the main clone it makes the records worktree in the sibling folder `<repo>.bakehouse-records`, on the
orphan branch `bakehouse-records` (checked out instead when the branch already exists, here or on origin),
and writes git config `bakehouse.records`. In a lane clone it only copies the main clone's pointer. Both
then write the git-hook launcher in the machine folder and install the git hooks, which exec it: the
post-commit drift report, and the pre-commit gate only with --pre-commit (as Permanence's
`predict-gate.sh install-hook`, never run automatically; owner ruling 2026-11-14).

The launcher holds one target line naming the plugin root. setup writes it; the plugin's SessionStart
hook moves the target when the plugin folder changes. The hooks never name the plugin folder, which
changes on every update.
"""

import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

import locator

BRANCH = "bakehouse-records"
MARKER = "# installed-by: bakehouse"
USAGE = "usage: bakehouse setup [--pointer <main clone>] [--pre-commit]"
GITIGNORE = """\
# The ledger lock files (ops_ledger.lock_path, decision.locked): never committed.
.*.lock
# The decision view, rebuilt from decisions/ledger.jsonl after every write: never committed (R2-17).
DECISIONS.md
"""
LAUNCHER = """\
#!/bin/sh
# The bakehouse git-hook launcher: the hooks `bakehouse setup` installs exec this file.
# `bakehouse setup` writes it; the plugin's SessionStart hook keeps ROOT on the installed plugin.
# Usage: git-hook pre-commit|post-commit
ROOT=@ROOT@
# The records worktree shares its clone's hooks: a commit there runs no team hook.
rec=$(git config --get bakehouse.records)
if [ -n "$rec" ]; then
  top=$(git rev-parse --show-toplevel 2>/dev/null)
  if [ -n "$top" ] && [ "$(CDPATH= cd -- "$top" && pwd -P)" = "$(CDPATH= cd -- "$rec" 2>/dev/null && pwd -P)" ]; then
    exit 0
  fi
fi
case "$1" in
  pre-commit)
    gate="$ROOT/runtime/predict-gate.sh"
    [ -x "$gate" ] || { echo "bakehouse: $gate is gone, so the pre-commit gate cannot run. Run \\`bakehouse setup\\` to point the git hooks at the installed plugin." >&2; exit 1; }
    exec "$gate" hook ;;
  post-commit)
    gap="$ROOT/runtime/ops-gap.sh"
    [ -x "$gap" ] || { echo "bakehouse: $gap is gone, so the ops-ledger drift report did not run. Run \\`bakehouse setup\\` to point the git hooks at the installed plugin." >&2; exit 0; }
    "$gap" 5 2>/dev/null
    exit 0 ;;
  *)
    echo "usage: git-hook pre-commit|post-commit" >&2; exit 2 ;;
esac
"""


def say(text):
    print(f"bakehouse setup: {text}")


def git(where, *args):
    return subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, check=False)


def launcher_path(env=os.environ) -> Path:
    return locator.machine_dir(env) / "git-hook"


def launcher_target(path: Path) -> str | None:
    """The plugin root the launcher's one target line names."""
    for line in path.read_text().splitlines():
        if line.startswith("ROOT="):
            words = shlex.split(line[len("ROOT="):])
            return words[0] if words else None
    return None


def write_atomic(path: Path, text: str):
    """A temp file beside it, then a rename: never half-written, and never written through a symlink."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o755)
        os.replace(tmp, path)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise


def write_launcher(root, env=os.environ) -> Path:
    path = launcher_path(env)
    write_atomic(path, LAUNCHER.replace("@ROOT@", shlex.quote(str(root))))
    return path


def owned(project) -> bool:
    try:
        return json.loads(project.binding.read_text()).get("owned") is True
    except (OSError, ValueError, AttributeError):
        return False


def install_hooks(project, launcher: Path, pre_commit: bool) -> bool:
    """pre-commit only when asked; post-commit only when the binding says "owned": true (another party's
    commits are not the owner's to log). A hook without the marker is left alone, and core.hooksPath stops
    the install: each is reported with the line to add instead. False when a hook was not installed."""
    names = []
    if pre_commit:
        names.append("pre-commit")
    else:
        say("pre-commit: not installed; the predict-gate commit check is opt-in: bakehouse setup --pre-commit")
    if owned(project):
        names.append("post-commit")
    else:
        say('post-commit: not installed, because the binding does not say "owned": true')
    add = {"pre-commit": f"{shlex.quote(str(launcher))} pre-commit || exit 1",
           "post-commit": f"{shlex.quote(str(launcher))} post-commit"}
    hp = git(project.root, "config", "--get", "core.hooksPath").stdout.strip()
    if hp:
        say(f"{project.root} sets core.hooksPath ({hp}), so git ignores its hooks folder. Hooks not installed. "
            "Add these lines instead:")
        for n in names:
            print(f"  to {hp}/{n}:  {add[n]}")
        return False
    hooks = Path(git(project.root, "rev-parse", "--path-format=absolute", "--git-path", "hooks").stdout.strip())
    ok = True
    for n in names:
        hook = hooks / n
        if os.path.lexists(hook):
            try:
                mine = MARKER in hook.read_text(errors="replace")
            except OSError:
                mine = False
            if not mine:
                say(f"{hook} exists and was not written by bakehouse. Left alone. Add this line to it instead:\n"
                    f"  {add[n]}")
                ok = False
                continue
        write_atomic(hook, f"#!/bin/sh\n{MARKER}\nexec {shlex.quote(str(launcher))} {n}\n")
        say(f"{hook} written")
    return ok


def exclude_local_settings(project):
    """A repo another party owns: the plugin is switched on in .claude/settings.local.json, which must never
    be committed there (§4.8)."""
    line = ".claude/settings.local.json"
    path = Path(git(project.root, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude").stdout.strip())
    text = path.read_text() if path.is_file() else ""
    if line in (l.strip() for l in text.splitlines()):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + ("" if text.endswith("\n") or not text else "\n") + line + "\n")
    say(f"{line} added to {path}")


def finish(project, root, env, pre_commit) -> int:
    launcher = write_launcher(root, env)
    say(f"{launcher} written, target {root}")
    ok = install_hooks(project, launcher, pre_commit)
    if project.team != project.root / ".team":
        exclude_local_settings(project)
    return 0 if ok else 1


def is_records_checkout(top: Path, folder: Path) -> bool:
    """`folder` is a worktree of this repo, at its own top, on the records branch."""
    if not folder.is_dir() or locator.toplevel(folder) is None or locator.toplevel(folder).resolve() != folder.resolve():
        return False
    common = [git(w, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip() for w in (top, folder)]
    if not common[0] or Path(common[0]).resolve() != Path(common[1]).resolve():
        return False
    return git(folder, "symbolic-ref", "-q", "HEAD").stdout.strip() == f"refs/heads/{BRANCH}"


def make_records(top: Path) -> Path | None:
    """The records checkout in the sibling folder, made or reused. None (after saying why) when it cannot be."""
    folder = top.parent / f"{top.name}.{BRANCH}"
    if os.path.lexists(folder):
        if not is_records_checkout(top, folder):
            say(f"{folder} exists and is not this repo's {BRANCH} checkout. Refused: move it away, then run "
                "`bakehouse setup` again.")
            return None
        say(f"{folder} is already the {BRANCH} checkout; using it")
    elif git(top, "show-ref", "--verify", "--quiet", f"refs/heads/{BRANCH}").returncode == 0:
        p = git(top, "worktree", "add", str(folder), BRANCH)
        if p.returncode:
            say(f"could not check out {BRANCH} into {folder}:\n{p.stderr.strip()}")
            return None
        say(f"checked out the branch {BRANCH} into {folder}")
    elif git(top, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{BRANCH}").returncode == 0:
        p = git(top, "worktree", "add", "--track", "-b", BRANCH, str(folder), f"origin/{BRANCH}")
        if p.returncode:
            say(f"could not check out origin/{BRANCH} into {folder}:\n{p.stderr.strip()}")
            return None
        say(f"checked out origin/{BRANCH} into {folder}")
    else:
        p = git(top, "worktree", "add", "--orphan", "-b", BRANCH, str(folder))
        if p.returncode:
            say(f"could not create {folder}:\n{p.stderr.strip()}")
            return None
        say(f"created {folder} on the new branch {BRANCH}")
    if git(folder, "rev-parse", "-q", "--verify", "HEAD").returncode:
        (folder / ".gitignore").write_text(GITIGNORE)
        git(folder, "add", ".gitignore")
        p = git(folder, "commit", "-q", "-m", "bakehouse records: the branch's first commit")
        if p.returncode:
            say(f"could not make the branch's first commit in {folder} (is this repo's git identity set?):\n"
                f"{(p.stderr or p.stdout).strip()}\nThen run `bakehouse setup` again.")
            return None
    return folder


def setup_main(top: Path, root, env, pre_commit) -> int:
    try:
        project = locator.find(top, env)
    except locator.SetupNeeded:
        project = None
    else:
        if project is None:
            say(f"{top} has no team: no .team/binding.json, and no bakehouse.sidecar in its git config. "
                "Nothing set up.")
            return 1
    if project is not None:
        say(f"already set up: records at {project.records}. Installing the hooks again.")
        return finish(project, root, env, pre_commit)
    folder = make_records(top)
    if folder is None:
        return 1
    git(top, "config", "bakehouse.records", str(folder))
    say(f"bakehouse.records = {folder}")
    return finish(locator.find(top, env), root, env, pre_commit)


def setup_pointer(top: Path, main_clone: str, root, env, pre_commit) -> int:
    try:
        if locator.find(top, env) is None:
            say(f"{top} has no team: no .team/binding.json, and no bakehouse.sidecar in its git config. "
                "Nothing set up.")
            return 1
    except locator.SetupNeeded:
        pass
    try:
        main = locator.find(Path(main_clone), env)
    except locator.SetupNeeded as e:
        say(f"{main_clone} has no usable records: {e}. Refused.")
        return 1
    if main is None:
        say(f"{main_clone} is not a clone with a team. Refused: give the main clone.")
        return 1
    git(top, "config", "bakehouse.records", str(main.records))
    say(f"bakehouse.records = {main.records} (the records of {main.root})")
    return finish(locator.find(top, env), root, env, pre_commit)


def main(argv, root) -> int:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    pre_commit = "--pre-commit" in argv
    argv = [a for a in argv if a != "--pre-commit"]
    if argv and not (len(argv) == 2 and argv[0] == "--pointer"):
        print(USAGE)
        return 2
    top = locator.toplevel(Path.cwd())
    if top is None:
        say(f"{Path.cwd()} is not inside a git repository")
        return 1
    if argv:
        return setup_pointer(top, argv[1], root, env, pre_commit)
    return setup_main(top, root, env, pre_commit)
