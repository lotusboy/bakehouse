"""`bakehouse setup`: make a clone ready for its team (design 05 §4.5, §4.6, §4.8, §10.1 item 8). Standard
library only. Run through bin/bakehouse.

  bakehouse setup [--pre-commit] [--no-remote-check]      in the main clone
  bakehouse setup --pointer <main clone> [--pre-commit]   in a lane clone

In the main clone it makes the records worktree in the sibling folder `<repo>.bakehouse-records`, on the
orphan branch `bakehouse-records` (checked out instead when the branch already exists, here or on origin),
and writes git config `bakehouse.records`. With --no-remote-check the owner asserts that origin has never held
the records branch: setup makes the orphan branch here without asking origin (for a remote that needs a login
setup cannot give). In a lane clone it only copies the main clone's pointer. Both
then write the git-hook launcher in the machine folder and install the git hooks, which exec it: the
post-commit drift report, and the pre-commit gate only with --pre-commit (as Permanence's
`predict-gate.sh install-hook`, never run automatically; owner ruling 2026-11-14).

The launcher holds one target line naming the plugin root. setup writes it; the plugin's SessionStart
hook moves the target when the plugin folder changes. The hooks never name the plugin folder, which
changes on every update.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

import locator

BRANCH = "bakehouse-records"
MARKER = "# installed-by: bakehouse"
USAGE = """\
usage: bakehouse setup [--pointer <main clone>] [--pre-commit] [--no-remote-check]
  --pointer <main clone>  in a lane clone (any clone but the main one). setup copies the main clone's
                     records pointer and makes no records worktree.
  --pre-commit       also install the pre-commit hook, the predict-gate commit check. Without it, setup
                     installs the post-commit drift report only.
  --no-remote-check  main clone only. By giving it, the owner asserts that origin has never held the
                     bakehouse-records branch. setup then makes a new orphan bakehouse-records branch here without
                     asking origin (for a remote that needs a login setup cannot give). Without it, setup asks
                     origin first.
  --help             print this text and exit 0. setup changes nothing."""
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
# The records worktree shares its clone's hooks: a commit there (known by its branch) runs no team hook.
[ "$(git symbolic-ref -q HEAD)" = refs/heads/bakehouse-records ] && exit 0
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


def hook_stub(launcher: Path, name: str) -> str:
    """The git hook: it does nothing in the records worktree (known by its branch, as the launcher knows it), else
    execs the launcher, and names `bakehouse setup` when the launcher is missing (the pre-commit hook then stops
    the commit, as a missing gate does; the post-commit hook does not)."""
    q = shlex.quote(str(launcher))
    return (f"#!/bin/sh\n{MARKER}\n"
            f"[ \"$(git symbolic-ref -q HEAD)\" = refs/heads/{BRANCH} ] && exit 0\n"
            f"[ -x {q} ] || {{ echo \"bakehouse: the git-hook launcher \"{q}\" is missing or not executable, so the {name} hook "
            f"cannot run. Run \\`bakehouse setup\\` to write it again.\" >&2; exit {1 if name == 'pre-commit' else 0}; }}\n"
            f"exec {q} {name}\n")


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
        earlier = Path(git(project.root, "rev-parse", "--path-format=absolute", "--git-path", "hooks/pre-commit")
                       .stdout.strip())
        try:
            left = MARKER in earlier.read_text(errors="replace")
        except OSError:
            left = False
        if left:
            say(f"pre-commit: {earlier}, installed earlier, is left in place; setup without --pre-commit does not "
                "remove it")
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
        write_atomic(hook, hook_stub(launcher, n))
        say(f"{hook} written")
    return ok


def finish(project, root, env, pre_commit) -> int:
    launcher = write_launcher(root, env)
    say(f"{launcher} written, target {root}")
    ok = install_hooks(project, launcher, pre_commit)
    return 0 if ok else 1


def is_worktree_here(top: Path, folder: Path) -> bool:
    """`folder` is a worktree of this repo, at its own top."""
    if not folder.is_dir() or locator.toplevel(folder) is None or locator.toplevel(folder).resolve() != folder.resolve():
        return False
    common = [git(w, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip() for w in (top, folder)]
    return bool(common[0]) and Path(common[0]).resolve() == Path(common[1]).resolve()


def is_records_checkout(top: Path, folder: Path) -> bool:
    """`folder` is a worktree of this repo, at its own top, on the records branch."""
    return is_worktree_here(top, folder) and \
        git(folder, "symbolic-ref", "-q", "HEAD").stdout.strip() == f"refs/heads/{BRANCH}"


def links_here(top: Path, folder: Path) -> bool:
    """folder's .git file names, by a path that is gone, a worktree entry this repo still has: this repo's
    records worktree, whose link broke when the main clone (or both) moved."""
    try:
        line = (folder / ".git").read_text().strip()
    except OSError:
        return False
    if not line.startswith("gitdir: "):
        return False
    gitdir = Path(line[len("gitdir: "):])
    common = git(top, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    return (gitdir.parent.name == "worktrees" and not gitdir.exists() and bool(common)
            and (Path(common) / "worktrees" / gitdir.name).is_dir())


def registered_at(top: Path) -> Path | None:
    """Where this repo's worktree list has the records branch checked out, or None."""
    path = None
    for line in git(top, "worktree", "list", "--porcelain").stdout.splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):])
        elif line == f"branch refs/heads/{BRANCH}":
            return path
    return None


def repair(top: Path, folder: Path) -> bool:
    """`git worktree repair` of this repo's records worktree at folder; True when it is then the checkout."""
    p = git(top, "worktree", "repair", str(folder))
    if is_records_checkout(top, folder):
        say(f"{folder} is this repo's {BRANCH} checkout: reconnected after a move (git worktree repair)")
        return True
    say(f"{folder} holds this repo's records, and `git worktree repair` could not reconnect it:\n"
        f"{(p.stderr or p.stdout).strip()}\nLeft as it is. Run `git -C {top} worktree repair {folder}`, then "
        "`bakehouse setup` again.")
    return False


def origin_path(top: Path) -> Path | None:
    """origin's folder when origin is a local path (a file:// URL, or no scheme and no `host:`), else None."""
    url = git(top, "config", "--get", "remote.origin.url").stdout.strip()
    if not url or ("://" in url and not url.startswith("file://")) or ":" in url.split("/")[0]:
        return None
    path = Path(url[len("file://"):] if url.startswith("file://") else url).expanduser()
    return path if path.is_absolute() else top / path


def origin_main_clone(top: Path) -> Path | None:
    """origin's folder when origin is a local clone with team records (git config bakehouse.records), else None."""
    path = origin_path(top)
    if path is not None and path.is_dir() and git(path, "config", "--local", "--get", "bakehouse.records").stdout.strip():
        return path.resolve()
    return None


def lane_reason(top: Path) -> str | None:
    """Why this clone looks like a lane, whose records are its main clone's: a records pointer that is neither
    its own sibling folder nor a worktree this repo lists, or a local origin path that no longer exists."""
    rec = git(top, "config", "--get", "bakehouse.records").stdout.strip()
    if rec:
        r = Path(os.path.normpath(top / rec)).resolve()
        own = {Path(line[len("worktree "):]).resolve()
               for line in git(top, "worktree", "list", "--porcelain").stdout.splitlines()
               if line.startswith("worktree ")}
        if r != (top.parent / f"{top.name}.{BRANCH}").resolve() and r not in own:
            return f"its records pointer (git config bakehouse.records) names {rec}, which is not this clone's own"
    path = origin_path(top)
    if path is not None and not path.exists():
        return f"its origin, {path}, is a local path that no longer exists (the main clone may have moved)"
    return None


def fetch_origin_records(top: Path) -> bool | None:
    """Look for the records branch on origin before a new one is made: True when it was there and is now
    fetched as origin/<branch>; False when origin has none (or there is no origin); None (after saying why)
    when origin cannot be asked."""
    if not git(top, "config", "--get", "remote.origin.url").stdout.strip():
        return False
    p = git(top, "ls-remote", "--heads", "origin", BRANCH)
    if p.returncode:
        say(f"could not look for {BRANCH} on origin, so a new records branch is not made:\n{p.stderr.strip()}\n"
            "Nothing changed. Run `bakehouse setup` again when origin can be reached.")
        return None
    if not p.stdout.strip():
        return False
    # a single-branch clone fetches only its branch: add this one, so origin/<branch> can be tracked
    specs = git(top, "config", "--get-all", "remote.origin.fetch").stdout
    if f"refs/heads/{BRANCH}:" not in specs and "refs/heads/*:" not in specs:
        git(top, "remote", "set-branches", "--add", "origin", BRANCH)
    p = git(top, "fetch", "-q", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}")
    if p.returncode:
        say(f"origin has {BRANCH}, but it could not be fetched:\n{p.stderr.strip()}\nNothing changed.")
        return None
    say(f"fetched {BRANCH} from origin")
    return True


def make_records(top: Path, remote_check=True) -> Path | None:
    """The records checkout in the sibling folder, made or reused. None (after saying why) when it cannot be.
    remote_check=False (--no-remote-check): origin is never asked for the records branch."""
    folder = top.parent / f"{top.name}.{BRANCH}"
    used = None if os.path.lexists(folder) else registered_at(top)
    if used is not None and os.path.lexists(used):
        # the branch is checked out at another path that still exists: the main clone moved alone
        return used if repair(top, used) else None
    if used is not None:
        # moved or deleted cannot be told apart from here, so nothing is pruned and no replacement is made
        say(f"this repo's {BRANCH} branch is checked out at {used}, which no longer exists. Refused, nothing "
            f"changed. If you moved that folder, run `git worktree repair <new path>` here, then `bakehouse setup` "
            f"again. If you deleted it, run `git worktree prune`, then `bakehouse setup` again.")
        return None
    if os.path.lexists(folder):
        if not is_records_checkout(top, folder):
            if links_here(top, folder):
                if not repair(top, folder):
                    return None
            elif is_worktree_here(top, folder):
                say(f"{folder} is this repo's records worktree, but it is not on the branch {BRANCH} (a detached "
                    f"HEAD, as in a rebase, or another branch). Refused, nothing changed. Run "
                    f"`git -C {folder} switch {BRANCH}`, then `bakehouse setup` again.")
                return None
            else:
                say(f"{folder} exists and is not this repo's {BRANCH} checkout. Refused: move it away, then run "
                    "`bakehouse setup` again.")
                return None
        else:
            say(f"{folder} is already the {BRANCH} checkout; using it")
    elif git(top, "show-ref", "--verify", "--quiet", f"refs/heads/{BRANCH}").returncode == 0:
        p = git(top, "worktree", "add", str(folder), BRANCH)
        if p.returncode:
            say(f"could not check out {BRANCH} into {folder}:\n{p.stderr.strip()}")
            return None
        say(f"checked out the branch {BRANCH} into {folder}")
    else:
        on_origin = git(top, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{BRANCH}").returncode == 0
        if not on_origin and not remote_check:
            say(f"origin was not asked for {BRANCH} (--no-remote-check): the owner asserts it has never held it")
        elif not on_origin:
            on_origin = fetch_origin_records(top)
        if on_origin is None:
            return None  # origin could not be asked; fetch_origin_records said why
        if on_origin:
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


def setup_main(top: Path, root, env, pre_commit, remote_check=True) -> int:
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
    main_clone = origin_main_clone(top)
    if main_clone is not None:
        say(f"this clone's origin, {main_clone}, is a main clone with team records: a clone of it uses those. "
            f"Refused, nothing changed. Run `bakehouse setup --pointer {main_clone}` instead.")
        return 1
    why = lane_reason(top)
    if why:
        say(f"this clone looks like a lane clone: {why}. A lane uses its main clone's records. Refused, nothing "
            "changed. Run `bakehouse setup --pointer <main clone>` instead.")
        return 1
    folder = make_records(top, remote_check)
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
    if git(top, "rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        # a shallow clone's root commit is its cut-off point: the main clone must hold its HEAD commit instead
        head = git(top, "rev-parse", "HEAD").stdout.strip()
        if not head or git(main.root, "cat-file", "-e", f"{head}^{{commit}}").returncode:
            say(f"{main.root} does not hold {top}'s HEAD commit ({head or 'none'}), so it is not the main clone of "
                "this shallow clone. Refused: give this project's main clone.")
            return 1
    else:
        roots = [set(git(w, "rev-list", "--max-parents=0", "HEAD").stdout.split()) for w in (top, main.root)]
        if not roots[0] & roots[1]:
            say(f"{main.root} shares no root commit with {top}, so it is not a clone of this project. Refused: give "
                "this project's main clone.")
            return 1
    git(top, "config", "bakehouse.records", str(main.records))
    say(f"bakehouse.records = {main.records} (the records of {main.root})")
    return finish(locator.find(top, env), root, env, pre_commit)


def main(argv, root) -> int:
    if "--help" in argv:
        print(USAGE)
        return 0
    env ={k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    pre_commit = "--pre-commit" in argv
    remote_check = "--no-remote-check" not in argv
    argv = [a for a in argv if a not in ("--pre-commit", "--no-remote-check")]
    if (argv and not (len(argv) == 2 and argv[0] == "--pointer")) or (argv and not remote_check):
        print(USAGE)
        return 2
    top = locator.toplevel(Path.cwd())
    if top is None:
        say(f"{Path.cwd()} is not inside a git repository")
        return 1
    side = git(top, "config", "--get", "bakehouse.sidecar").stdout.strip()
    if not os.path.lexists(top / ".team" / "binding.json") and side and os.path.lexists(Path(side) / "binding.json"):
        say("bakehouse R0 does not set up records for a repo another party owns (a known limit); nothing was "
            "changed")
        return 1
    if argv:
        return setup_pointer(top, argv[1], root, env, pre_commit)
    return setup_main(top, root, env, pre_commit, remote_check)
