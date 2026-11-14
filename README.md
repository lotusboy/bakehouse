# bakehouse

A project team for Claude Code. Your main session acts as the project's PM: it plans the work,
delegates it to agents, and checks each result before it lands. The team brings:

- predicted-moves gates: an engineer writes down every output change it expects before its first edit,
  and any change it did not predict stops the job;
- independent review, with the reviewer's run record checked before it may stop;
- append-only records of every gate, decision and agent run, kept on their own branch of your repo.

Status: early. Release R0 is the first move of these tools out of the notes tool where they grew. It
works on one project at a time, on one machine.

## Install

The plugin is switched on per project, never for every repo on the machine.

1. In the project's main clone, install it at project scope (the default, user scope, would switch the
   team on in every repo):

   ```
   claude plugin install bakehouse --marketplace lotusboy/bakehouse --scope project
   ```

2. The install writes `enabledPlugins` into the project's `.claude/settings.json`. Add the marketplace
   beside it, so a clone on another machine knows where to fetch the plugin, then commit the file:

   ```json
   {
     "extraKnownMarketplaces": {
       "bakehouse": { "source": { "source": "github", "repo": "lotusboy/bakehouse" } }
     },
     "enabledPlugins": { "bakehouse@bakehouse": true }
   }
   ```
3. Describe the project for the team in `.team/binding.json` (see `docs/PROJECT-TEAM.md`).
4. In the main clone, run `bakehouse setup` from a Claude Code session (the plugin's `bin/` is on the
   Bash tool's PATH). It makes the records worktree beside the clone, on the branch
   `bakehouse-records`, and installs the post-commit drift report. Add `--pre-commit` to install the
   commit gate as well.
5. In any other clone of the project (a lane, or a clone made for a run), run
   `bakehouse setup --pointer <main clone>` before any team tool.

A new session in the project is told to load the `bakehouse:pm` skill. To turn that off on this
machine, write `none` into `~/.local/state/bakehouse/role`.

## Commands

- `bakehouse setup`, `bakehouse status [--json]`, `bakehouse run <script>` (from the Bash tool).
- `/bakehouse:change`, `/bakehouse:refactor`, `/bakehouse:health` (in a session).

The docs: `docs/PROJECT-TEAM.md` (the team), `docs/PREDICT-GATE.md` (the gates), `docs/TOOLS.md`
(every script).

## Requirements

Python: the tools need Python 3.10 or later. The plugin's hooks run the first `python3` on the hook's
PATH and still load under Python 3.9 (macOS's `/usr/bin/python3`), so the dispatch gate and the session
start keep working there. Git 2.42 or later (`git worktree add --orphan`).

Licence: GNU GPL v3. See `LICENSE`.
