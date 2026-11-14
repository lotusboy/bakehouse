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

## Scripts in `runtime/`

You rarely run these by hand: hooks and commands call them. Each script's header comment is its full
reference.

Seven tools are a `.sh` file you call and a `.py` file beside it that does the work: `decision`,
`spend`, `ratify`, `save-report`, `predict-gate`, `driver-signal` and `mutate`. The table names the
`.sh`.

"Records" below is the project's records folder; "machine" is the machine folder
([PROJECT-TEAM.md](./PROJECT-TEAM.md), "Where the team's files live").

| Script | What it is for | Writes |
|---|---|---|
| `locator.py` | Finds the project for a folder: its binding, its records folder and every file in them. Every other script asks it. | nothing |
| `agent-roles.py`, `team_roles.py`, `agent-roles.json` | The role table and the three hooks that enforce it. | `logs/agent-roles.log`, in the machine folder |
| `team-preflight.py` | Checks a repo's binding before a job or the health watch. | nothing |
| `health-signal.py` | Writes a repo's health register from rules in the binding. | the register, in the records folder |
| `driver-signal.sh` | Finds the same driver script written again in run after run, and raises a family as a build candidate when it is seen in 3 or more runs within 30 days (a coherent family) or in 5 or more (a name-only family). | the driver register, in the records folder |
| `mutate.sh` | The mutation runner: plants each mutant of a reviewer's list in a clean `git archive` copy of a commit, runs the tests the binding's `mutation` block names for the file (an unmutated control first), and appends the rows of the reviewer's mutation record. Reviewers use it instead of writing a harness. | the round's `mutations.json` or `mutations-fix<N>.json`, in the review's record folder |
| `records-check.py`, `record-contracts.json` | Checks each work order's record against what its job type must leave (a closed work order, the moves trio, a result, a reviewer's `mutations.json`), by content, and reports the kill rate per gate. | `registers/records.json`, in the records folder |
| `predict-gate.sh` | The predicted-moves gate: refuses any measured move nobody predicted ([PREDICT-GATE.md](./PREDICT-GATE.md)). | `ops/events.jsonl` (seals and closes), in the records folder |
| `save-report.sh` | Saves an agent's report to a file byte for byte. | the file it is given |
| `decision.sh` | Records each question put to the owner, the recommendation, the answer and the outcome. | `decisions/ledger.jsonl` and `DECISIONS.md`, in the records folder |
| `spend.sh` | Records measured tokens per agent, and what each run was for and produced, grouped into waves with a ceiling. | `spend/ledger.jsonl`; a lesson stub in `TOOLS.md` when a run did worse than predicted; both in the records folder |
| `ratify.sh` | Records which entries of a closed vocabulary two blind readers agreed on. | `ratifications/ledger.jsonl`, in the records folder |
| `ops-event.sh` | Appends one event to the repo's ops ledger. | `ops/events.jsonl`, in the records folder |
| `ops-gap.sh` | Says how many commits the ops ledger is behind. | nothing |
| `lane-event.sh`, `heavy-run.sh` | Record parallel work inside one project; run one heavy job under its lane's lock. | `ops/lanes.jsonl`, in the records folder; `locks/`, in the machine folder |
| `bakehouse_setup.py` | `bakehouse setup`: makes a clone ready for its team. It makes the records worktree and its pointer, and writes the git hooks: pre-commit (`predict-gate.sh hook`) always, and post-commit (`ops-gap.sh`) in repos the owner owns. | the records worktree and git config `bakehouse.records`; `.git/hooks/pre-commit` and `post-commit`; `git-hook`, in the machine folder |
| `bakehouse_status.py` | `bakehouse status`: the team's state for any reader outside the tool ([PROJECT-TEAM.md](./PROJECT-TEAM.md), "Reading team state"). | nothing |

`agents/` holds the nine agents the team's roles are filled by (`bakehouse:<agent>`). `skills/pm/` holds
the role the main session can take, the skill `bakehouse:pm`. `skills/` also holds three method skills:

| Skill | Use it when |
|---|---|
| `dev-team` | deciding whether to do work yourself or hand it to one agent or several, which model, and which checker to run before a finding or a change ships |
| `step-back` | a chain of work shows strain — an estimate overrunning twice, a fix round producing a new finding of the same kind, a third agent on one item |
| `live-prism` | one item keeps resisting a fix — look at its real input from at least three sides before writing another rule |
