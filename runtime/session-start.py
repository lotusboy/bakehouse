#!/usr/bin/env python3
"""The plugin's SessionStart hook (design 05 §9, §10.1 item 8). Standard library only. Always exits 0: a
hook must never stop a session from starting.

1. Moves the git-hook launcher's target to $CLAUDE_PLUGIN_ROOT when it differs (the plugin folder changes
   on every update), only when that root holds runtime/predict-gate.sh. A missing launcher is written again
   only when this project's git hooks are bakehouse's (`bakehouse setup` wrote them); otherwise only setup
   creates it.
2. Finds the project through the locator, from the folder locator.hook_folder names ($CLAUDE_PROJECT_DIR,
   else the hook input's cwd, else the process cwd: after a compaction the process cwd is wherever the
   shell last was), and prints, for the session's context:
   nothing when there is no team; the locator's message when setup is needed; with a team and the role
   choice `pm`, one instruction to load the PM skill; nothing with `none`.
3. With a team whose binding names `mainFollows`: one line when the main clone's branch has engine commits (commits
   touching the code roots) that this checkout's HEAD does not have; nothing when it has none.
4. With a team in records format 2: the records sync (runtime/records_sync.py), within its bound; one line when it
   fails, naming why. It never stops the session.

The role choice is the file `role` in the machine folder: `pm` or `none`. A missing file means `pm`; any
other content is read as `pm`, and one line says so.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PM = ("bakehouse: this project has a team. Be its PM: load the `bakehouse:pm` skill now and follow it "
      "for the whole session.")


def has_bakehouse_hooks(folder) -> bool:
    """This project's git hooks folder holds a hook `bakehouse setup` wrote (its marker line)."""
    p = bakehouse_setup.git(folder, "rev-parse", "--path-format=absolute", "--git-path", "hooks")
    if p.returncode or not p.stdout.strip():
        return False
    for name in ("pre-commit", "post-commit"):
        try:
            if bakehouse_setup.MARKER in (Path(p.stdout.strip()) / name).read_text(errors="replace"):
                return True
        except OSError:
            pass
    return False


def retarget(env, folder):
    root = env.get("CLAUDE_PLUGIN_ROOT")
    if not root or not (Path(root) / "runtime" / "predict-gate.sh").is_file():
        return
    path = bakehouse_setup.launcher_path(env)
    if path.is_file():
        if bakehouse_setup.launcher_target(path) != root:
            bakehouse_setup.write_launcher(root, env)
    elif has_bakehouse_hooks(folder):
        bakehouse_setup.write_launcher(root, env)


def role(env):
    path = locator.machine_dir(env) / "role"
    try:
        text = path.read_text().strip()
    except FileNotFoundError:
        return "pm", None
    except OSError as e:
        return "pm", f"bakehouse: cannot read {path} ({e}); treating the role choice as `pm`."
    if text in ("pm", "none"):
        return text, None
    return "pm", f"bakehouse: {path} holds {text!r}, not `pm` or `none`; treating it as `pm`."


def main_follows_notice(project):
    """The line saying the main clone's mainFollows branch has N > 0 engine commits this checkout's HEAD lacks, or
    None (the switch off, none missing, or anything git or the binding cannot answer)."""
    try:
        binding = json.loads(project.binding.read_text())
        branch = locator.main_follows(binding)
        tip = locator.main_tip(project, branch) if branch else None
        n = locator.missing_engine_commits(project, tip, "HEAD", locator.code_roots(binding)) if tip else None
    except Exception:  # noqa: BLE001 - a notice never stops a session
        return None
    if not n:
        return None
    return (f"bakehouse: {branch} has {n} engine commit{'s' if n != 1 else ''} this branch does not have: merge it "
            "in before the next work order's baseline")


def main(env=os.environ, stream=sys.stdin):
    folder = locator.hook_folder(env, stream)
    try:
        retarget(env, folder)
    except Exception as e:
        print(f"bakehouse: could not retarget the git-hook launcher: {e}", file=sys.stderr)
    try:
        project = locator.find(folder, env)
    except locator.SetupNeeded as e:
        print(f"bakehouse: {e}")
        return
    if project is None:
        return
    choice, note = role(env)
    if note:
        print(note)
    if choice == "pm":
        print(PM)
    notice = main_follows_notice(project)
    if notice:
        print(notice)
    try:
        import records_sync
        line = records_sync.quiet(project.records)
    except Exception as e:  # noqa: BLE001 - a sync never stops a session
        line = f"bakehouse sync: failed: {e!r}"
    if line:
        print(line)


if __name__ == "__main__":
    try:
        import bakehouse_setup  # noqa: E402 - inside the try: a failed import must not stop the session
        import locator  # noqa: E402
        main()
    except Exception as e:
        print(f"bakehouse: session-start failed: {e!r}", file=sys.stderr)
    sys.exit(0)
