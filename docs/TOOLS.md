# Every command and script, on one page

bakehouse is the project team as a Claude Code plugin. It works only in a repository that holds a
binding, `.team/binding.json` ([PROJECT-TEAM.md](./PROJECT-TEAM.md)). This page is the fixed
reference. Script paths are in the plugin's folder (`${CLAUDE_PLUGIN_ROOT}` in a command or agent
body).

## Commands

| Command | What it does |
|---|---|
| `/bakehouse:health` | Rewrites the repo's health register, lists the parts that are ready, and lists the `shortcut:` markers it finds. Never starts work. |
| `/bakehouse:refactor` | Restructures one part behind byte-identical gates. |
| `/bakehouse:change` | Makes one change from a work order behind predicted-moves gates. |

## `bakehouse`, in the Bash tool

The plugin's `bin/` is on the Bash tool's PATH, so these run from any Bash command in a session.

| Command | What it does |
|---|---|
| `bakehouse setup [--pointer <main clone>] [--pre-commit] [--no-remote-check] [--token-file <path> --token-key <NAME> --token-user <name>]` | Makes a clone ready for its team (`bakehouse_setup.py`, below). |
| `bakehouse status [--json] [--since <ISO time>] [--repo <folder>]` | The team's state (`bakehouse_status.py`, below). |
| `bakehouse sync [--repo <folder>]` | In records format 2: fetches, merges and pushes the records branch, never prompting, within 20 seconds of network time (`records_sync.py`, below). Exit 1 on a failure, with one line naming why. |
| `bakehouse reserve [--start] [--repo <folder>]` | When the binding has `tracker.reserve` and the records are in format 2: prints the next free work-order id and records it as `reservations/<id>` on the records branch, through the remote. `--start`, once per project, records the floor, the highest id on every branch (`reserve.py`, below). Exit 1 on a refusal (offline, before `--start`, refused 5 times), with one line naming why. A refusal takes back the reservation commit it made (at the tip, the branch moves back; under a later commit, a new commit deletes the file), so no unpushed reservation is left. A later `--start` raises the floor when this clone's branches hold a higher id, and never lowers it. |
| `bakehouse record commit <record folder> -m <message> [-m ...]` | Commits one run folder on the records branch, in records mode (`bakehouse_record.py`, below). |
| `bakehouse run <script> [args]` | Runs `runtime/<script>` with the args, and exits with its exit code. The plugin folder changes on every update, so a project file that names a plugin script (a binding command, a project's own script) names it this way: `bakehouse run health-signal.py ...`. A `.py` script runs under the same python as `bakehouse`. A name with a `/`, or one that is not a file in `runtime/`, is refused (exit 2). |

## Scripts in `runtime/`

You rarely run these by hand: hooks and commands call them. Each script's header comment is its full
reference.

Nine tools are a `.sh` file you call and a `.py` file beside it that does the work: `decision`,
`spend`, `ratify`, `blind-compare`, `save-report`, `predict-gate`, `shadow-predict`, `driver-signal`
and `mutate`. The table names the `.sh`.

"Records" below is the project's records folder; "machine" is the machine folder
([PROJECT-TEAM.md](./PROJECT-TEAM.md), "Where the team's files live").

| Script | What it is for | Writes |
|---|---|---|
| `locator.py` | Finds the project for a folder: its binding, its records folder and every file in them. Every other script asks it. | nothing |
| `agent-roles.py`, `team_roles.py`, `agent-roles.json` | The role table and the three hooks that enforce it. | `logs/agent-roles.log`, in the machine folder |
| `team-preflight.py` | Checks a repo's binding before a job or the health watch. | nothing |
| `health-signal.py` | Writes a repo's health register from rules in the binding. | the register, in the records folder |
| `driver-signal.sh` | Finds the same driver script written again in run after run, and raises a family as a build candidate when it is seen in 3 or more runs within 30 days (a coherent family) or in 5 or more (a name-only family). | the driver register, in the records folder |
| `mutate.sh` | The mutation runner: plants each mutant of a reviewer's list in a clean `git archive` copy of a commit, runs the tests the binding's `mutation` block names for the file (an unmutated control first), and appends the rows of the reviewer's mutation record. The copy has no `.git` and no uncommitted files, so a test that needs either fails the control. Reviewers use it instead of writing a harness. | the round's `mutations.json` or `mutations-fix<N>.json`, in the review's record folder |
| `records-check.py`, `record-contracts.json` | Checks each work order's record against what its job type must leave (a closed work order, the moves trio, a result, a reviewer's `mutations.json`), by content, and reports the kill rate per gate. | `registers/records.json`, in the records folder |
| `predict-gate.sh` | The predicted-moves gate: refuses any measured move nobody predicted ([PREDICT-GATE.md](./PREDICT-GATE.md)). | `ops/events.jsonl` (seals and closes), in the records folder |
| `shadow-predict.sh` | Optional. Writes a prediction from a shadow measured as a flat before/after pair and a written expect file, and refuses any move the expect file does not explain ([PREDICT-GATE.md](./PREDICT-GATE.md), "Writing the prediction from a shadow"). | the `--out` file, `moves.prediction<R>.json`, in the record folder |
| `save-report.sh` | Saves an agent's report to a file byte for byte. | the file it is given |
| `decision.sh` | Records each question put to the owner, the recommendation, the answer and the outcome. Also switches unattended mode on and off, and records the PM's assumed answers while the owner is away ([PROJECT-TEAM.md](./PROJECT-TEAM.md), "Unattended mode"). | `decisions/ledger.jsonl` and `DECISIONS.md`, in the records folder |
| `spend.sh` | Records measured tokens per agent, and what each run was for and produced, grouped into waves with a ceiling. `spend.sh headroom <wave> --est <tokens>` says whether the next agent fits the ceiling (exit 0) or not (exit 1); `--est` is the next agent's estimate plus every agent still running. | `spend/ledger.jsonl`; a lesson stub in `TOOLS.md` when a run did worse than predicted; both in the records folder |
| `ratify.sh` | Records which entries of a closed vocabulary two blind readers agreed on. | `ratifications/ledger.jsonl`, in the records folder |
| `blind-compare.sh` | Prints the agreement table for two or more blind readers' reports (the `blind-readers` skill): an item agrees only when every reader gives the same label and action. Exit 0 all agree, 1 any disagree, 2 one reader passed twice or a report has no valid JSON block. | nothing (the table goes to stdout) |
| `ops-event.sh` | Appends one event to the repo's ops ledger. In records format 2 every ledger tool in this table appends to its writer's own file, `<ledger without .jsonl>/<writer>.jsonl`, instead of the file named here ([PROJECT-TEAM.md, "One records file per writer"](./PROJECT-TEAM.md#one-records-file-per-writer)). | `ops/events.jsonl`, in the records folder |
| `ops-gap.sh` | Says how many commits the ops ledger is behind. | nothing |
| `lane-event.sh`, `heavy-run.sh` | Record parallel work inside one project; run one heavy job under its lane's lock. | `ops/lanes.jsonl`, in the records folder; `locks/`, in the machine folder |
| `bakehouse_setup.py` | `bakehouse setup`: makes a clone ready for its team. It makes the records worktree and its pointer, and writes the git hooks: pre-commit (`predict-gate.sh hook`) only with `--pre-commit`, and post-commit (`ops-gap.sh`) in repos the owner owns. Before it makes a new records branch it asks origin whether it already holds one, and refuses when origin cannot be asked. `--no-remote-check` (main clone only) skips that ask: by giving it, the owner asserts that origin has never held the `bakehouse-records` branch, and setup makes the new orphan branch locally. Use it for a remote that needs a login setup cannot give. `--token-file`, `--token-key` and `--token-user` (main clone, all three together) store where the records remote's token is, never the token ([PROJECT-TEAM.md, "Records sync"](./PROJECT-TEAM.md#records-sync)). | the records worktree and git config `bakehouse.records`; `.git/hooks/pre-commit` and `post-commit`; `git-hook`, in the machine folder |
| `session-start.py` | The plugin's SessionStart hook: keeps the git-hook launcher on the installed plugin, and, in a project with a team, tells the session to load the `bakehouse:pm` skill (the role choice `role`, in the machine folder, can turn that off). In records format 2 it also runs the records sync, and prints one line when it fails. | `git-hook`, in the machine folder |
| `bakehouse_record.py` | `bakehouse record commit`: in records mode, checks one run folder under `runs/` in the records worktree with the pre-commit hook's own check (`predict-gate.py run_hook`, in a scratch copy of the index), then stages and commits that folder only, by path, retrying while git's `index.lock` is held. Each `-m` passes to git unchanged. Exit 1 when the gate refuses (nothing is committed); exit 2 in tree mode ("commit them with git") or for a folder outside `runs/`. `PREDICT_GATE_OVERRIDE` works as for the hook. | one commit on the records branch |
| `bakehouse_status.py` | `bakehouse status`: the team's state for any reader outside the tool ([PROJECT-TEAM.md](./PROJECT-TEAM.md), "Reading team state"). | nothing |
| `records_sync.py` | `bakehouse sync`, and the one sync step that seals, closes and session start run too ([PROJECT-TEAM.md, "Records sync"](./PROJECT-TEAM.md#records-sync)). Records format 2 only. | merge commits on the records branch; `bakehouse-sync.lock` and `bakehouse-sync-failure.json`, in the records worktree's git folder |
| `reserve.py` | `bakehouse reserve` and `bakehouse reserve --start` ([PROJECT-TEAM.md, "Work-order ids reserved before use"](./PROJECT-TEAM.md#work-order-ids-reserved-before-use)), and preflight's check 7. Records format 2, with `tracker.reserve`, only. | `reservations/FLOOR` and `reservations/<id>`, one commit each, on the records branch |

### `ratify.sh`: the files it reads

`ratify.sh sample` reads three JSON files. `compare` reads each reader's report.

**Entries** (`--entries`): a JSON array. Each entry is an object with four non-empty strings:

| Key | Holds |
|---|---|
| `id` | the entry's id; no two entries share one |
| `pattern` | the text the entry matches |
| `patternKind` | `regex`, `phrase` or `keyword` |
| `class` | the class the entry assigns to a match |

The three pattern kinds:

- `regex`: a Python regular expression, used as written.
- `phrase`: the words in order. Case and the spaces between words are ignored. A word at either end
  matches a whole word only. `…` or `...` inside the phrase matches any text between its parts.
- `keyword`: every word, in any order, each as a whole word, case ignored.

Any other key (`kind`, `source`, notes) is kept out of what the readers see. `check` compares each
entry with its latest ledger row: a changed `pattern`, `patternKind` or `class` needs a new round.

**Corpus** (`--corpus`): a JSON array of records. Each record is an object with three strings:
`docId` (the document it comes from), `pointer` (where in that document, for example a page) and `text`
(the whole record, shown to the readers in full). The sample takes matches round-robin across
documents, so one long document cannot fill it.

**Classes** (`--classes`, optional): one JSON object of class name to its meaning, for example
`{"exclude": "the clause removes cover"}`. The readers see each meaning above the entries.

**A reader's report** (`compare`): a Markdown file whose last ```` ```json ```` block is one object:
`{"reader": "<name>", "items": [{"id": "<entry id>", "label": "RIGHT", ...}]}`. `reader` is optional (the
file name stands in). Each item is an object with an `id` and a `label` of `RIGHT`, `WRONG` or `MIXED`
(case ignored). A block that is not an object, an `items` that is not a list, or an item that is not an
object or has no `id` is bad input: exit 2, with one line that names the report.

`agents/` holds the nine agents the team's roles are filled by (`bakehouse:<agent>`). `skills/pm/` holds
the role the main session can take, the skill `bakehouse:pm`. `skills/` also holds four method skills. The
five skills:

| Skill | Use it when |
|---|---|
| `pm` | the main session takes the PM role (`bakehouse:pm`); the SessionStart hook asks for it in a project with a team |
| `dev-team` | deciding whether to do work yourself or hand it to one agent or several, which model, and which checker to run before a finding or a change ships |
| `step-back` | a chain of work shows strain — an estimate overrunning twice, a fix round producing a new finding of the same kind, a third agent on one item |
| `live-prism` | one item keeps resisting a fix — look at its real input from at least three sides before writing another rule |
| `blind-readers` | the owner must judge items read from a source — two blind readers read it, and only the items both agree on go to the owner; also, ratifying a closed vocabulary with `ratify.sh` |
