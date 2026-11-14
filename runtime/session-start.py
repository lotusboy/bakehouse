#!/usr/bin/env python3
"""The plugin's SessionStart hook (design 05 §9, §10.1 item 8). Standard library only. Always exits 0: a
hook must never stop a session from starting.

1. Moves the git-hook launcher's target to $CLAUDE_PLUGIN_ROOT when it differs (the plugin folder changes
   on every update). Only `bakehouse setup` creates the launcher.
2. Finds the project ($CLAUDE_PROJECT_DIR) through the locator, and prints, for the session's context:
   nothing when there is no team; the locator's message when setup is needed; with a team and the role
   choice `pm`, one instruction to load the PM skill; nothing with `none`.

The role choice is the file `role` in the machine folder: `pm` or `none`. A missing file means `pm`; any
other content is read as `pm`, and one line says so.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bakehouse_setup  # noqa: E402
import locator  # noqa: E402

PM = ("bakehouse: this project has a team. Be its PM: load the `bakehouse:pm` skill now and follow it "
      "for the whole session.")


def retarget(env):
    root = env.get("CLAUDE_PLUGIN_ROOT")
    path = bakehouse_setup.launcher_path(env)
    if root and path.is_file() and bakehouse_setup.launcher_target(path) != root:
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


def main(env=os.environ):
    try:
        retarget(env)
    except Exception as e:
        print(f"bakehouse: could not retarget the git-hook launcher: {e}", file=sys.stderr)
    try:
        project = locator.find(None, env)
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


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"bakehouse: session-start failed: {e!r}", file=sys.stderr)
    sys.exit(0)
