# bakehouse

A project team for Claude Code. Your main session acts as the project's PM: it plans the work,
delegates it to agents, and checks each result before it lands. The team brings:

- predicted-moves gates: an engineer writes down every output change it expects before its first edit,
  and any change it did not predict stops the job;
- independent review, with the reviewer's run record checked before it may stop;
- append-only records of every gate, decision and agent run, kept on their own branch of your repo.

Status: early. Release R0 is the first move of these tools out of the notes tool where they grew. It
works on one project at a time, on one machine.

## Docs

- [Getting started](docs/GETTING-STARTED.md): one project, from install to its first `/bakehouse:change` job.
- [Example project](examples/notes-app/README.md): a made-up app with every team file written, to try
  bakehouse on before your own work.
- [The project team](docs/PROJECT-TEAM.md): where the files live, the roles, the binding, lanes, the
  records and the ledgers.
- [The predicted-moves gate](docs/PREDICT-GATE.md): the prediction, baseline and measured files, sealing
  and close.
- [Tools](docs/TOOLS.md): every script, what it does and what it writes.

## People and machines

- One machine: several sessions, and lane clones, work in one history. A lane clone set up with
  `bakehouse setup --pointer <main clone>` writes to the main clone's records.
- Two or more machines, or two or more people, each with their own main clone: the records must be in
  format 2 (`bakehouse setup migrate`). Then each main clone writes its own records files, and bakehouse
  moves them through the `bakehouse-records` branch on the remote. Steps:
  [Getting started, "10. A second machine or a second developer"](docs/GETTING-STARTED.md#10-a-second-machine-or-a-second-developer).
- In format 1 (a project set up before 0.8.0 and not migrated), run bakehouse on one machine only. Each
  main clone would keep its own records branch, nothing would move them, and each machine's status and
  gates would see only its own records.
- Not yet in bakehouse: nothing stops two people taking the same work item, and nothing records an
  agreement to change shared code. Section 10 says how to work until it does.

## One team plugin per repo

Do not switch bakehouse on in a repo where another project-team plugin already runs the work, with its
own plan, build, review or push rules. Their skills compete for the same requests, and the other
plugin's rules may refuse bakehouse's branches. Install bakehouse in a different repo instead.

## Install

New here? [`docs/GETTING-STARTED.md`](docs/GETTING-STARTED.md) takes one project from install to its first
`/bakehouse:change` job. To try it first on a made-up project, use [`examples/notes-app`](examples/notes-app/README.md).

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
3. Describe the project for the team in `.team/binding.json` (see
   [The binding](docs/PROJECT-TEAM.md#the-binding)).
4. In the main clone, run `bakehouse setup` from a Claude Code session (the plugin's `bin/` is on the
   Bash tool's PATH). It makes the records worktree beside the clone, on the branch
   `bakehouse-records`, and installs the post-commit drift report. Add `--pre-commit` to install the
   commit gate as well. Before it makes a new records branch, setup asks the remote `origin` whether it
   already holds one. If origin needs a login setup cannot give, setup refuses. Add `--no-remote-check`
   only when origin has never held the `bakehouse-records` branch: by giving it, you assert that, and
   setup makes the new branch locally without asking origin.
5. In any other clone of the project (a lane, or a clone made for a run), run
   `bakehouse setup --pointer <main clone>` before any team tool.

A new session in the project is told to load the `bakehouse:pm` skill. To turn that off on this
machine, write `none` into `~/.local/state/bakehouse/role`.

## Commands

- `bakehouse setup`, `bakehouse status [--json]`, `bakehouse sync`, `bakehouse reserve [--start]`, `bakehouse run <script>` (from the Bash tool).
- `/bakehouse:change`, `/bakehouse:refactor`, `/bakehouse:health` (in a session).

More on each: [Docs](#docs).

## Requirements

Python: the tools need Python 3.10 or later. The plugin's hooks run the first `python3` on the hook's
PATH and still load under Python 3.9 (macOS's `/usr/bin/python3`), so the dispatch gate and the session
start keep working there. Git 2.42 or later (`git worktree add --orphan`).

Licence: GNU GPL v3. See [`LICENSE`](LICENSE).
