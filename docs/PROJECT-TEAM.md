# The project team (experimental)

> **Status: experimental.** Built and used on one repository so far. The file
> shapes below may change in a later release; a change that affects an existing file will carry a
> **Migration notes** section in `CHANGELOG.md`.
>
> **Claude Code only.** bakehouse is a Claude Code plugin. It works with or without Permanence
> installed, and it never reads or writes Permanence's files.
>
> **Opt-in by file.** A repository takes part only when it holds `.team/binding.json` (a repository
> another party owns: its sidecar folder holds the binding). With no binding, the team commands stop
> and say so.
>
> Script paths on this page, `runtime/<script>`, are in the plugin's folder (`${CLAUDE_PLUGIN_ROOT}`
> in a command or agent body).
>
> **Python.** The tools need Python 3.10 or later. The plugin's hooks run the first `python3` on the
> hook's PATH and load under Python 3.9 too (macOS's `/usr/bin/python3`), so the dispatch gate and the
> session start keep working there.

## What it is

The PM role (`skills/pm/SKILL.md`) holds the thread for one owner. The project team is what the PM
hands work to: named roles, filled by agents, running two jobs behind gates, plus a health watch
that says where to look. The `dev-team` skill is the playbook for running it: whether to do a piece of
work yourself or hand it to one agent or several, which model, and which checker to pass before a
finding or a change ships.

| Command | What it does |
|---|---|
| `/bakehouse:health` | Rewrites the repo's health register and lists the parts that are ready for a refactor. Also lists the `shortcut:` markers it finds in the code. It never starts work. |
| `/bakehouse:refactor <part>` | Restructures one part. Every step must leave outputs byte-identical, except a meaning change the owner named in advance. |
| `/bakehouse:change <work-order id>` | Makes the tool do something new, or fixes a defect. The engineer writes every expected output move before its first edit; any move not predicted stops the job. |

Each command runs a preflight first and stops before any work if the binding is wrong.

## Where the team's files live

One module, `runtime/locator.py`, finds every file. It takes the project folder from the working
folder (or the folder it is given), and asks `git rev-parse --show-toplevel` for the repository. It uses
`CLAUDE_PROJECT_DIR` only when that folder is in no git repository: the harness sets the variable for
plugin hooks, not for Bash tool commands or git hooks, so the repository git runs in comes first.
The session-start hooks (`runtime/session-start.py`, `agent-roles.py --check-new`) give the locator
`CLAUDE_PROJECT_DIR` itself, else their hook input's `cwd`, else the process cwd: after a compaction a
hook's process cwd is wherever the shell last was, not the project. The dispatch hook (`runtime/agent-roles.py`) first asks about the working folder its hook input
names. When that folder's repository has no team, it asks about `CLAUDE_PROJECT_DIR` (the folder the session
was started in), so the stricter of the two holds. The running plugin's own agents (`agents/` in the plugin
folder, named `<plugin>:<agent>`) always count as installed, because a hook that runs proves its plugin is
switched on. For other plugins it reads the switch-on (`.claude/settings*.json`) and an install for the
working folder's repository root: a local-scope install, then a project-scope one (each names that root as
its `projectPath`, compared resolved), then a user-scope one. There is no registry. The files live in three places:

1. **Project facts**, committed with the project: `.team/` in the repository. A repository another
   party owns keeps them in a sidecar folder instead, named by its git config `bakehouse.sidecar`.
2. **Team records**: the records folder, a git worktree of an orphan branch of the project repository,
   in a sibling folder. Its git config `bakehouse.records` names it; `bakehouse setup` makes it. The
   locator accepts it only when it is outside the project tree and checked out on the branch
   `bakehouse-records`; a relative pointer is read from the repository root.
3. **Machine state**: the machine folder, `$XDG_STATE_HOME/bakehouse`, else
   `~/.local/state/bakehouse` (`BAKEHOUSE_MACHINE_DIR` replaces it). It holds the logs, the locks, the
   seen-agents file, the personal role table and the role choice. It is never in git.

| File | Where | Holds | Written by |
|---|---|---|---|
| `binding.json` | `.team/` | the repo's facts for the team | the owner, with the PM |
| `OPERATIONS.md` | `.team/` | the ops ledger's rules | the owner, with the PM |
| `approvals.json` | beside the binding | approvals for work in a repo another party owns | the owner, with the PM |
| the register (`registers/<binding.register>`) | records | each part's health signal, state and decision | the signal command; decisions by the owner |
| the driver register (`registers/<drivers.register>`, default `drivers.json`) | records | each driver family seen in 2 or more run folders, its coherence, state and decision | `runtime/driver-signal.py`; decisions by the owner |
| `ops/events.jsonl` | records | the ops ledger | `runtime/ops-event.sh` only |

## Roles

A role names a job. An agent fills a role. A job dispatches `role:<job>:<role>`, for example
`role:refactor:engineer`.

| Role | Does | Shipped agent |
|---|---|---|
| `architect` | lays out a restructure-or-accept decision for one part | `bakehouse:architect` |
| `engineer` | makes the change behind its job's gate | `bakehouse:engineer` |
| `reviewer` | reviews the diff and runs the risky case | `bakehouse:reviewer` |
| `absence-check`, `count-check`, `blast-radius` | check a claim of absence, a count, the reach of a change | `bakehouse:` and the role's name |
| `design-author`, `design-reviewer` | write and review a design | `bakehouse:` and the role's name |
| `expert` | an independent reading of one question about what a source means, standing in for a human expert in the project's field; it reads blind and advises, never rules | `bakehouse:human-expert` |
| `code-review` | single-pass code review | none shipped; falls back to the built-in `general-purpose` agent |

Before R0.1 the agents `bakehouse:engineer` and `bakehouse:reviewer` were named `refactor-engineer` and `refactor-reviewer`; an older record that names those agents means these two.

Resolution checks these levels in order. At each level the key `<job>:<role>` is tried before
`<role>`. A level that names only agents that are not installed is skipped, and the skip is logged.

1. The repo binding's `roles`.
2. `agent-roles.json` in the machine folder — the owner's own table. Put an employer's or a private
   plugin's agent here. The machine folder is outside the plugin, so an update never overwrites it.
3. The shipped table, `runtime/agent-roles.json`.

A review role never falls to the session that did the work: with no match it goes to
`general-purpose`. `runtime/team_roles.py` is the one implementation of these rules;
`runtime/agent-roles.py` enforces them as hooks and logs every change to
`logs/agent-roles.log` in the machine folder. The hook fails open, with one exception: a dispatch whose decision needs a
binding that cannot be read (binding.json does not parse, is not an object, cannot be read or is a
dangling symlink, or its `reviews` field is not a safe path, for a dispatch whose record folder line names a
folder in that repo; the project needs setup, with the locator's message, for a team dispatch only; the project locator cannot be
run or does not answer in time) is refused, and the reason names that
cause and the file or command to fix (`dispatch:binding-unreadable`). A team dispatch is a `role:` dispatch, a
dispatch of or by an agent a role table lists, or one whose brief has a `Work order <id>:` line or a record
folder line; in a clone that needs setup, any other dispatch passes unchanged. When the run's deadline was used up
by earlier calls, the reason says so. A stop still fails open. The whole hook run has one deadline of 6
seconds, counted from hook start; each path's binding is read once per run.

Outside a repository with a binding, the shipped table acts only on a `role:` dispatch. It does not
change a dispatch made by plain agent name, and it never sends an agent back for a run record. The
owner's own table in the machine folder is the owner's explicit choice and applies everywhere.

The shipped agents are the plugin's (`agents/`), so the harness names them `bakehouse:<agent>`, and
the shipped table names them so. The hooks accept a plugin agent reported with or without its
`bakehouse:` prefix. To fill a role with an agent of the owner's own, name it in the owner's table.

## The binding

`.team/binding.json` in the repository (a repository another party owns: `binding.json` in its
sidecar folder). JSON, read with the standard library only. An `_about` key carries comments. Paths
are relative to the repo root, except `register`, which is relative to the records folder's
`registers/`.

Every gate and command is either a command, or a pointer to the repo file that holds it:
`file#key`, or `file#key.id` for the list item under `key` whose `id` is `id`. Prose is not allowed
in the binding, because preflight cannot check it.

| Field | Holds | Required |
|---|---|---|
| `owned` | `true` when the owner owns the repo | always |
| `approver` | who approves work when `owned` is false — a role, never a name | when `owned` is false |
| `register` | the register's path | always |
| `signal` | what rewrites the register: a command, a pointer, or rules for the shipped script (see "The signal") | always |
| `tracker` | `file`: where a job opens or reads its item; `id`: the id form, as a regular expression | when owned |
| `measure` | `commands`: what the architect runs; `layerRules`: where the layer rules are (optional) | `commands` when owned |
| `gates.byteIdentical` | the gate list for `refactor` | when owned |
| `gates.predictedMoves` | `gates`: the gate list for `change`; `prediction`: the prediction file's name, `moves.prediction.json` (preflight refuses a prose file; one may sit beside it) | when owned |
| `gates.oneAtATime` | runs that must not run in parallel | no |
| `branches` | the branch form per job: `refactor`, `change` | when owned |
| `roles` | role → agent overrides for this repo | no |
| `reviews` | the folder of review records; default `axis/runs/` | no |
| `reviewName` | one step's record folder name; default `{date}-{id}-step{n}-review` | no |
| `leaveAlone` | files no step may edit | no |
| `records` | `since`: the date the record contracts apply from (`YYYY-MM-DD`); `jobs`: artefacts added to a job's record contract (see "Records against the process"). It may add, never remove. `codeRoots`: the folders whose commits are code, for a `signal` that is a command or a pointer (a rules `signal` names its own `codeRoots`) | no |
| `mutation` | what the mutation runner runs for a mutated file: `command`, `env`, `timeout`, `tests` (a list of `{"files": <glob>, "tests": [<path>, ...]}`), `loads` (a command that prints where the tests' environment loads a file from), and `links` (paths in the copy that get a snapshot of a linked git repository; see "The mutation runner") | no |

Placeholders are `{…}` only, from this closed list: `{root}`, `{register}`, `{id}`, `{n}`, `{date}`,
`{part}`, `{name}`. Anything else fails preflight.

No field may hold a person's name or any private context: a field's text reaches agent briefs, and
briefs reach committed review records.

### An example

A small repo the owner owns, with tests as its gate and a markdown tracker. (A pointer, `file#key`,
must name a JSON or YAML file; a Makefile target is written as the command that runs it.)

```json
{
  "_about": "Repository facts for the project team.",
  "owned": true,
  "register": "health.json",
  "signal": "python3 tools/health.py --register {register}",
  "tracker": {"file": "docs/TRACKER.md", "id": "^T[0-9]+$"},
  "measure": {
    "commands": ["git log --no-merges --numstat -- src/{part}"]
  },
  "gates": {
    "byteIdentical": "make check",
    "predictedMoves": {"gates": "make check", "prediction": "moves.prediction.json"}
  },
  "branches": {"refactor": "refactor/{id}-step{n}-{name}", "change": "fix/{id}-{name}"},
  "records": {"codeRoots": ["src/"]}
}
```

## The signal

`/bakehouse:health` and `/bakehouse:refactor` need something that writes the register: each part's fix
pressure and state. `/bakehouse:change` does not need the register to run. The binding's `signal` takes
one of three forms:

- **a command** the repo already has, such as `python3 tools/health.py --register {register}`;
- **a pointer** to the repo file that holds that command (`file#key.id`);
- **rules for the shipped script**, `runtime/health-signal.py`. Use this when the
  repo has no signal command of its own, or belongs to another party so that none can be added.

The shipped script uses the Python standard library only. It reads the repository with read-only
git commands at one ref and writes one file, a JSON register in the records folder.

```json
"register": "health.json",
"signal": {
  "script": "health-signal",
  "ref": "HEAD",
  "fix": "(?i)^fix\\b",
  "codeRoots": ["src/", "scripts/"],
  "part": "^src/([^/]+)/"
}
```

| Rule | Holds | Required |
|---|---|---|
| `script` | `health-signal` | yes |
| `ref` | the commit or ref that is measured. For a repo another party owns, name a remote ref such as `origin/main`, so no branch is ever switched | yes |
| `codeRoots` | the folders whose lines count as code. A fix's lines in tests, docs or data are left out, so they do not hide where the code was fixed | yes |
| `part` | one pattern; group 1 is the part id of a file | `part` or `parts` |
| `parts` | a list of `{"kind", "match", "id"}`; the first `match` that fits a file decides. `id` is optional and builds the part id from the groups, for example `"config/{1}"` | `part` or `parts` |
| `fix` | a commit whose subject matches is a fix. Default: starts with `fix` | no |
| `extensions` | only files ending in one of these belong to a part | no |
| `reviewFinding` | what marks a review finding worth counting. Default: `\bP1\b` | no |

The script measures three things per part:

1. **Fix pressure.** Each fix commit's changed code lines are shared out over the parts they fall
   in. A part's `signal` is the sum of its shares over the window. This is the only input that
   decides `state`.
2. **Size.** Files, lines and the longest file, at the ref. Reported only.
3. **Review findings.** In each review folder (the binding's `reviews`) whose name starts with a
   date inside the window, each paragraph that matches `reviewFinding` and names one of the part's
   files adds one. A file is named by its full path, or by its bare file name when no other part
   has a file of that name. Reported only. It is a text match, so read it as a pointer to go and
   look: a review that describes a fault without naming the file is not counted, and a count of 0
   does not mean no review found anything.

A part at or over the threshold with no decision is `ready`. A person writes a part's `decision`
(`restructure`, `redesign` or `accept`); the script never overwrites it, and raises the part again
(`ready-again`) only when its signal reaches `signalAtDecision` times `regrowth`. The window,
threshold and regrowth live in the register's own `settings` block, which the script also never
overwrites. Defaults: 30 days, 8.0, 1.5.

Run it by hand to try rules before trusting them; `--no-write` prints the block and leaves the
register as it is:

```bash
python3 runtime/health-signal.py --repo <repo> --no-write
# --binding and --register default to the project's binding and register
```

## The driver signal

**The problem.** Agents write small scripts ("drivers") inside a run's record folder to measure,
compare or attack a change. When the same job comes up in run after run, the team writes the same
script again each time, under the same name or a new one, instead of keeping one tool. Nothing
counted that.

**What it does.** `runtime/driver-signal.py` reads the driver scripts in the binding's `reviews`
folder and finds two kinds of family. A family seen in enough run folders inside the window is raised
as a build candidate. It uses the Python standard library only, reads files only, and writes one file:
the driver register, in the records folder beside the health register. It uses no AI and runs in seconds.

**Which files it reads.** A driver is a `*.py` or `*.sh` file under a folder whose name starts with
`drivers`, directly inside a run folder: `<reviews>/<run>/drivers*/`. With `nested: true` it also
reads `drivers*` folders deeper inside a run, such as a review's or a fix round's own
`review/drivers/`. A run folder's date is the `YYYY-MM-DD` its name starts with.

**What it skips, and reports.** It never follows a symbolic link: a linked run folder, a linked
`drivers*` folder, a linked folder met while searching deeper (`nested: true`) and any link inside a
drivers folder are skipped. A file over `maxBytes` is skipped. A file or folder it cannot read is
skipped. None of these stops the run. Each is counted in `computed.skipped` and listed by path in the
register's `skipped`, and the printed block names the counts.

**The two signals.**

1. **Content clusters.** Each file is cut to its identifier, number and operator tokens; comments
   and string contents are dropped. A `.py` file is read with Python's own tokenizer. A `.sh` file,
   and a `.py` file the tokenizer refuses, is read with a regular expression; each refusal is counted
   in `computed.tokenizeFailed`. The tokens form 6-token shingles. Two files in different run folders
   are near-duplicates when the Jaccard of their shingle sets is at or over `threshold` (default
   0.5). A cluster is a connected group of such pairs. A file under 30 shingles is too small to
   compare.
   - **Boilerplate.** A shingle found in more than `boilerplate` (default 0.75) of the run folders,
     and in more than 10 run folders, is boilerplate. It is left out of the shared count. The share
     grows with the repository. The floor of 10 keeps a small repository's real copies: in 3 run
     folders, a tool copied into all 3 is a copy, not boilerplate.
   - **A copied tool is not boilerplate.** Two files that share 30 or more boilerplate shingles count
     every shingle they share. Boilerplate is a short header in many unrelated files. A shared block of
     30 shingles is the size of the smallest file the signal compares, so it is a copied tool. Without
     this, a tool copied into more run folders than the cut (11 of 12, 16 of 20) shares nothing and
     vanishes. A cap on the cut was rejected: it only moves that blind spot to a higher count.
2. **Name families** (`kind: name`). Each driver file has a name key: the first declared family whose
   glob matches its file name, or else its name rule. The name rule drops round markers: the stem
   is lowercased and split on `_`, `-` and `.`; each piece loses its trailing digits (and one letter
   after them); pieces left empty, one letter long, or a round word (`fix`, `fx`, `fr`, `new`,
   `old`, `tmp`, `final`) are dropped. So `make_prediction_fix2.py`, `d8_mutants.py` and `cut3.py`
   have the keys `make_prediction`, `mutants` and `cut`. A key in 2 or more run folders is a name
   family.

**One list: the fold.** A content cluster folds into the name family that holds the most of its
files, when that family holds at least half of them. On a tie, the first key wins. The family takes
the cluster's files as its own, and lists the cluster's id under `clusters`. A cluster that no name
family covers is listed on its own, as `kind: content`. So a file appears in one listed family, and
in a second only when its cluster folded into a family under another name.

**Coherence.** Each family has a `coherence` figure from 0 to 1: for each of its files, the best
Jaccard (all shingles) with a file of the family in another run folder; the family's figure is the
upper median of these. A coherent family is one tool written again. A family with a low figure shares
a name and not a job: generic names such as `measure.py` or `run.sh` gather different scripts. A
name that is generic is not always incoherent, so the figure is measured, never assumed from the name.

**The rules**, all optional, in the binding's `drivers` object:

```json
"drivers": {
  "threshold": 0.5,
  "boilerplate": 0.75,
  "windowDays": 30,
  "minRuns": 3,
  "minRunsNameOnly": 5,
  "coherent": 0.5,
  "maxBytes": 1000000,
  "nested": false,
  "register": "drivers.json",
  "owner": "pm",
  "families": [
    {"id": "mutation-harness", "names": ["mut*", "*mutant*"]}
  ]
}
```

| Rule | Holds | Default |
|---|---|---|
| `threshold` | the Jaccard at or over which two files are near-duplicates, above 0 and at most 1 | `0.5` |
| `boilerplate` | a shingle in more than this share of the run folders (and in more than 10) is boilerplate, above 0 and at most 1 | `0.75` |
| `windowDays` | the window in days, ending on `--as-of` (today by default); both ends count; 1 to 3650 | `30` |
| `minRuns` | a coherent family in this many run folders inside the window is raised | `3` |
| `minRunsNameOnly` | a family below `coherent` is raised at this many; not under `minRuns` (refused) | `5` |
| `coherent` | the coherence at or over which a family is coherent, 0 to 1 | `0.5` |
| `maxBytes` | a driver file larger than this is skipped and reported | `1000000` |
| `nested` | also read `drivers*` folders deeper inside a run | `false` |
| `register` | the register file; a relative path is taken beside the binding | `drivers.json` |
| `owner` | who a raised family goes to: the PM by default, until a role that judges tools exists | `pm` |
| `families` | names the team knows are one tool: `id` and a list of `names` globs (`*` and `?`, case ignored); the first match wins | none |

`drivers`, when present, must be an object. An unknown key, or a value of the wrong type or out of its
range (`true` is not a number), stops the script before it reads anything, with a message naming the
rule. `--threshold`, `--window-days` and `--min-runs` override the binding for one run.

**The state of a family.** A family's count is `minRuns` when its coherence is `coherent` or more, and
`minRunsNameOnly` when it is less.

| State | When |
|---|---|
| `watch` | in 2 or more run folders, but fewer than its count inside the window; no decision |
| `build-candidate` | in its count or more run folders dated inside the window; no decision |
| `decided` | a person has written a decision |
| `build-candidate-again` | decided, and since then its count or more run folders dated after the decision day, inside the window, hold it again |

A key in only one run folder is not listed. A run folder whose name has no date counts in
`runCount`, never in the window.

**A decision** is written by a person under the family's `decision` key: `{"decision": "build" |
"adopt" | "accept", "date": "YYYY-MM-DD"}`, with any other fields the person wants, such as a note or
the tracker id of the build. The script never overwrites it.

**Point, don't carry.** A recorded decision never moves to another family by itself. A family's id
can change: a content cluster's id is its first file, so an older copy joining it, or `nested: true`
adding a deeper copy that sorts first, gives it a new id; and a cluster decided on its own can later
fold into a name family. The rule when a decided id is no longer listed:

- The decided id stays listed as a stub: `"present": false`, `"state": "decided"`, its decision and
  the files the register recorded for it.
- Every listed family that holds any of those files carries a `priorDecision` note, one per such id:
  `{"id": <the old id>, "decision": ..., "date": ..., "holds": <files of the old id it holds>,
  "of": <files the old id recorded>}`.
- That family keeps its own state. A family the owner never decided is raised by its own count, like
  any other; its flag's `next` then says it is not new and that an earlier decision covers some of
  its files. The printed summary shows the note in the family's row. Its first line counts the raised
  families as new and not new. The generic next step is printed for new families only. A last line
  counts and names every raised family that is not new, including one beyond `--top`. The register's
  `computed` block carries the same split: `buildCandidatesNew` and `buildCandidatesNoted`.
- The owner confirms with one answer: a decision written on the family's own id. Until then the
  signal decides nothing for the owner.
- A family with its own decision keeps it, and is noted as well, so the other decision stays visible.
- A decided id whose files are in no listed family stays listed with `"present": false` and its files.

Why (2026-10-20, owner's ruling after a step-back): a decision carried to the family holding most of
its files silenced raised families the owner had never decided, once a few of the decided files
folded into them.

**Who acts.** Each raised family carries a `flag`: `{"owner": <drivers.owner>, "next": "propose to the
owner: build one shared tool, adopt an existing one, or accept the copies"}`. A raised family that
carries a `priorDecision` note is not new: its `next` instead says that an earlier decision covers some
of its files, and asks the owner to confirm it by writing a decision on the family's own id, or to
decide afresh. The owner of the repository decides. The `owner` rule names the role that carries the proposal.

**The register** (`drivers.json`):

```json
{
  "_about": "...",
  "computed": {
    "asOf": "2026-06-30", "windowDays": 30, "threshold": 0.5, "boilerplate": 0.75, "minRuns": 3,
    "minRunsNameOnly": 5, "coherent": 0.5, "maxBytes": 1000000, "nested": false, "reviews": "runs/",
    "runFolders": 4, "driverFiles": 4, "drivers": 4, "tokenizeFailed": 0,
    "skipped": {"symlinks": 0, "tooLarge": 0, "unreadable": 0},
    "pairs": 6, "clusters": 1, "filesInClusters": 4, "linesInClusters": 48, "nameFamilies": 1,
    "buildCandidates": 1, "buildCandidatesNew": 1, "buildCandidatesNoted": 0
  },
  "skipped": {"symlinks": [], "tooLarge": [], "unreadable": []},
  "families": {
    "name:tool": {
      "kind": "name",
      "names": ["renamed.py", "tool.py"],
      "runCount": 4, "runsInWindow": 4, "fileCount": 4, "lines": 48, "coherence": 1.0,
      "firstSeen": "2026-06-20", "lastSeen": "2026-06-25",
      "state": "build-candidate",
      "decision": null,
      "flag": {"owner": "pm", "next": "propose to the owner: build one shared tool, ..."},
      "clusters": ["content:2026-06-20-r0/drivers/tool.py"],
      "runs": ["2026-06-20-r0", "2026-06-21-r1", "2026-06-22-r2", "2026-06-25-r3"],
      "files": ["2026-06-20-r0/drivers/tool.py", "..."]
    }
  }
}
```

- `computed.driverFiles` counts every driver file read; `computed.drivers` counts those big enough to
  compare. `pairs`, `clusters`, `filesInClusters` and `linesInClusters` are the content totals, folded
  clusters included.
- A family's id is `name:<key>` (`name:<id>` for a declared family) or, for a cluster no name family
  covers, `content:<its first file>` (paths sort by run folder, so by date). A content id changes
  when an older or a newly read copy sorts first; the old id's decision stays on its stub, as above.
- `priorDecision` appears on a family holding files of a decided id no longer listed; `clusters` on a
  name family.
- `lines` counts newline characters. `firstSeen` and `lastSeen` are run-folder dates.
- Families are listed raised first, then by runs inside the window.

**Run it.**

```bash
runtime/driver-signal.sh --repo <repo> [--no-write]
```

`--no-write` prints the block and leaves the register as it is, which is the way to try a threshold
or a declared family before trusting it. `--top N` (default 15) limits the printed rows; the register
holds them all.

**The limit.** A rewrite from scratch under a new name is invisible to both signals: its content does
not match any copy, and its name does not either.

## Records against the process

A rule kept only in a prompt lapses. `runtime/records-check.py` checks each work order's record
against what its job type must leave behind, by content. `/bakehouse:health` runs it (step 9). It is
deterministic, uses the standard library only, reads the repository with read-only git commands at
one ref, and writes one file, `registers/records.json` in the records folder.

**The record contracts.** `runtime/record-contracts.json` holds the shipped defaults. Per job type:

| Job | A record folder is this job's record when it holds | It must hold | Flags go to | Closes through `predict-gate.sh close` |
|---|---|---|---|---|
| `change` | a prediction: `moves.prediction*.json` (or `.yaml`), or a prose `prediction*.md`; outside a refactor, also a `result*.md`, or a step folder name | per prediction round: the prediction, its baseline and its measured file; and a `result*.md` | the PM | yes |
| `refactor` | `byte-identical*` in a folder with no prediction; in a refactor, also a step folder name with no prediction | that file, not empty | the PM | never: a step without its proof, or code committed after the latest step proof, is flagged `refactor.byte-identical` |
| `reviewer` | `findings*.md`, in a record folder or in any folder below it (`"nested": true`: a reviewer writes in a subfolder of the step folder it reviews) | for each findings file, its paired mutations file (`findings.md` owes `mutations.json`, `findings-fix1.md` owes `mutations-fix1.json`) in the mutation-record format (below; `"per": "findings*.md"`); each findings file not empty; each round's files under their declared names (`rounds`, below) | the reviewer | — |

An artefact with `"active": false` is listed in the output and never flagged. Switching it on is one
field in the shipped file.

**What a binding may change.** `records.since` sets the start of the window. `records.codeRoots` names the
code roots when `signal` is a command or a pointer. `records.jobs.<job>.require`
adds artefacts, each `{"id", "glob"}`, checked as present and not empty. A binding never removes or
replaces a default: any other key, an id a default already has, a `check` or an `active` field, or an
unknown job is refused. The check exits 2, and preflight check 2 fails.

```json
"records": {"since": "2026-01-01", "jobs": {"change": {"require": [{"id": "gates", "glob": "gates*.md"}]}}}
```

**The record folder line.** A brief names its record folder; the dispatch hook and the stop check
read that line:
- **The record folder is decided at dispatch.** A brief names its record folder on one line, as a
  full path: `Record folder: ~/<path>/` (the bound repo's root + `reviews` + the record's name). For a change job and for a role with `run_record` (the reviewer), the
  dispatch hook reads every line labelled as a record folder and refuses the dispatch, naming the line
  and where it resolves from the dispatching working directory, when a line is not in the reader's form
  (`Record folder:`, optionally bold, indented or a `-`/`*` list item, the path optionally in
  backticks or quotes), when the line holds anything after its path (the line holds the path only: a
  path with a space goes in backticks or quotes, trailing whitespace is fine, and notes go on the next
  line; the refusal shows the line as written; a stop with no dispatch decision reports such a line and
  never reads it, its note naming the path up to the first space and why the line was refused, not the
  line itself), when the path holds a quote, a backtick, `*`, a format character such as a zero-width
  space, or whitespace other than a space inside its quotes (refused, never stripped), when the path is not
  full (it does not start with `/` or `~`), when it is
  not under the `reviews` folder of the bound repo the path itself is in, or when two lines name
  different folders. Whether a dispatch is in the hook's care is
  decided once, for every labelled line, before it is read: through the first path after the label, in any
  form the label takes. A relative line is refused because the PM's
  working directory can move, so the same line names a different folder on each dispatch. A
  plain-name reviewer dispatch is decided only where the working directory has a binding, or a line's
  path is under a bound repo's `reviews` (the opt-in rule); a refactor job is not decided (its stop is
  not checked).
- The decided folder is logged with the dispatch (`logs/prediction-dispatch.jsonl` in the machine
  folder, keyed by the prompt's hash). The stop check uses that folder, whatever its own working
  directory, and never reads the line again: the folder is checked alone, with the binding of the
  repo it is in, so another work order's record under the same `reviews` neither satisfies nor blocks
  the stop. A decided folder counts as written when a file under it, at any depth, is
  newer than the agent's start and matches one of the role's job marks in record-contracts.json
  (`findings*.md` for the reviewer), so a record kept in a subfolder of it (`review/`, a round's
  folder) counts, and a stray driver output, a contract or a report alone does not. One with no such
  file is sent back, naming the folder and the mark. A role that is no job there counts any new file.
- With no dispatch decision (a plain-name dispatch the rule above does not cover, or a dispatch made
  before it), the stop reads the line from its own working directory (`~`, `..` and symlinks resolved;
  a relative path from the repository root). A line it cannot place there is reported to the user,
  never refused: the stop runs as if the brief had no line. A repo line read there is checked across
  that repo's whole `reviews` folder, not only the folder it names.
- A `role:` dispatch of the change engineer is logged with or without a binding, so a refactor job
  is not asked for a machine prediction.

**The work orders.** A work order is a tracker id (the binding's `tracker.id`) with at least one code
commit in the window. A commit belongs to the first tracker id in its subject. A code commit touches
one of the binding's code roots: `signal.codeRoots` for a rules `signal`, else `records.codeRoots`
(`--code-roots` replaces either). The window runs from `records.since` to today; with no
`records.since`, it is the last 30 days. A work order's record folders are the folders directly
under `reviews` whose name holds its id as a whole token, case ignored. A **step folder** is one
whose name matches the binding's `reviewName`, with `{n}` any step number. A work order is a
**refactor** when one of its folders holds `byte-identical*` and no prediction (a folder holding
both is a change record that kept a gate result); in a refactor, a step folder with no prediction is
a refactor step. The record is read from git at the ref: a file that is not committed
is not part of it. A `records.since` in the future is refused; a window that starts after `--as-of`
is reported as a warning.

**The two rules.** Content, not presence. An empty file always fails.

1. **`close`.** A work order with code commits has a `work-order-closed` event in the ops ledger.
   The latest such event counts only when all five hold:
   - every file it hashes sits at the top level of one of this work order's step folders;
   - it carries the sha256 of every file `predict-gate.sh close` hashes in this record: each
     prediction, its round's measured file and its baseline, in the step folders close reads
     (`{n}` = 1, the id's exact case);
   - every sha256 it carries matches that file at the ref;
   - predict-gate's own check (`run_check`, imported, not re-implemented) passes on each
     prediction it carries, paired with its round's measured file and baseline as `predict-gate.sh
     close` pairs them, all three read at the ref. A failing pair makes the close
     `not-its-record`, with the reason;
   - no code commit of the work order is later than the record's latest measured file's commit, by
     commit time. This holds for a record with a round whose prediction is first committed
     on or after predict-gate's `ORDER_FROM` (2026-11-04). An older record keeps the older rule: the
     close's time is not before the last code commit. A close with no time is stale either way. So
     running close again, with nothing measured, does not clear a stale record.

   So a close made before a later prediction round, one carrying a path outside this work order's
   step folders, and one whose pairs predict-gate's check refuses do not count; a close written by
   hand counts only where predict-gate's check passes on every pair it carries. An event with no sha256 does not count. A close that
   records the owner's override (`OVERRIDDEN`) need not carry every file and is not checked by
   predict-gate. It counts only when the work order's ledger event immediately before it (any
   kind, same task) is the `gate-refused` event `predict-gate.sh close` writes, with its own
   purpose `work order close overridden`. A refusal from another gate, one with an event between
   it and the close, or one for another work order does not back it. A refactor never closes. A
   refactor step without a non-empty `byte-identical*`, or a code commit of the work order that
   comes after the latest step proof (a `byte-identical*`, or a meaning-change step's measured file), is flagged
   `refactor.byte-identical`, with the step's gates to re-run as its next step, never a close. A
   repository another party owns keeps no ops ledger, so this rule does not apply there.
2. **`<job>.<artefact>`.** Each record folder that marks a job holds every active artefact the job
   requires. A prediction must parse under predict-gate's own checker and name a move. Each
   prediction round's baseline and measured file must be flat JSON. (When there is no machine
   prediction, the first round's files, `moves.baseline.json` and `moves.measured.json`, are owed.)

There is one flag per work order and rule; it lists every folder it was found in. Each flag names
its **owner** (the PM, for its own runs) and its **next step**: produce the artefact honestly, by
measuring it, or bring the owner's override. A prediction cannot be written after the change, so a
missing one needs the override: `PREDICT_GATE_CLOSE_OVERRIDE="<reason>" predict-gate.sh close <id>`.
A work order closed by that override shows its record flags as `overridden`, not `open`. A work
order closed cleanly shows the flags found only in folders that are not step folders (a design or
planning folder) as `superseded`: the closed step record replaces them. A step folder's flag is
never superseded.

**The reviewer's mutation record (`mutations.json`).** Every mutation a reviewer performs is one
row of a JSON list, written beside its `findings.md`. The refactor reviewer's prompt asks for at least
5 rows; the checks do not count them, so `[]` still passes. Each
findings file owes its own record, so a re-check that writes `findings-fix1.md` into the same folder
writes `mutations-fix1.json` and leaves the earlier rows as they were. A `mutations*.json` that no
findings file owes is not read. **History is read as it is:** a findings file whose adding commit is
before `rounds.from` (the round names' date, below; one date for both rules) owes no mutation file,
because older reviews recorded their mutations as prose. A mutation file that exists is checked
whatever its date.

**The round names (`rounds`, declared once in record-contracts.json).** The first review writes
`findings.md` and `mutations.json`. The re-check of fix round N writes `findings-fix<N>.md` and
`mutations-fix<N>.json`, beside the first review's files. The mutation runner's batch log for a round
is declared beside them (`log`: `mutations.log.jsonl`, `mutations-fix<N>.log.jsonl`). These are the only names. The brief
templates (`/bakehouse:change`, `/bakehouse:refactor`) and the reviewer's prompt state them literally, and a
runtime test fails if any of them names another. A findings-like file is a `.md` whose name holds
`findings`, `recheck`, `re-check` or `review-` (case ignored) and that no other job's marks claim (so
`prediction-review-fix.md` is the change job's). One under an undeclared name is refused by the stop
check, which names the pair to write instead (the name's own `fix<N>`, else the folder's next free
round), and flagged by records-check as `reviewer.rounds`. **History is read as it is:** a file
whose adding commit is before `rounds.from` is never flagged. The bound is the commit date, not
`records.since`, because a binding's `since` may already hold records written under the old names.

| Field | Holds |
|---|---|
| `id` | text, unique in the file |
| `mutation`, `site` | what was changed, and where; text, not empty |
| `gate` | a gate id from the binding's gate list (every `gates.*` pointer, resolved in the repository), or a path in the repository (`path::test` allowed; the path is checked). A list when several ran together; the kill rate counts the list as one gate, joined with ` + ` |
| `verdict` | `killed` (the gate failed on the mutation) or `survived` (it passed); any case |
| `disposition` | a survivor only, one of three. `open: <finding id>`: written at review time, before anyone has decided; the id must occur in the findings file beside it. `test-added <path>`: a test file, under the test roots (`testRoots` in record-contracts.json, `tests/` and `test/`; a binding's `records.testRoots` replaces them), or `path::name` where the file defines each part (the last a `def test…` or a `class`, the parts before it classes). records-check also requires a non-merge commit that changed that test file, that the record's own commit reaches, and that has the commit which first added the paired findings file as an ancestor (or is it): a test-added test is written after the review that found the survivor, on a line that holds it (a test committed on a side branch before the review and merged after it does not count) (the stop check cannot see commits and checks the name only). `accepted: <reason>`: the reason states its reach as a count (`reach 0`, `reach: 3 of 35`). Absent or null on a killed row |

Other fields (evidence such as the failing test) are allowed. A row the mutation runner wrote carries
`batch`, its batch id, and is reconciled with the round's batch log beside the file (below): every row
the log holds must be in the file with the same `id`, `batch`, `mutation`, `site`, `gate`, `verdict` and
`data` (the `disposition` may change), and a row's `batch` must be one the log holds for that id; a
missing log, or a line that is not a log line, is flagged too. A row without `batch` is the reviewer's
own (a two-edit mutant done by hand) and is not reconciled. The flag is `reviewer.mutations`; its
owner is the reviewer. It is raised for a findings file with no paired mutations file, an
empty one, one that is not this format, for **a survivor with no disposition** (`<id> survived and
has no disposition`), and for **an open survivor** (`<id> survived and is open (<finding>)`) until
the round that settles it rewrites that row's disposition to `test-added` or `accepted:`; a clean
close does not supersede it, whatever folder it sits in. A record's
gate paths, `test-added` paths and gate ids are checked at the commit that last changed that file,
so a test deleted later does not reopen a closed record. The next step is to write the file from the
review's own mutation table.

**Kill rate per gate.** Every row read in the window is counted per gate (killed, survived, the
mutation files it came from as `records`, and the work orders), in `records.json` (`mutations`) and
in the report. A file is counted once, though a folder named by several work orders belongs to each
of them; count runs from `records`, not from `workOrders`. It is reported, never a target: a target
invites easy mutants.

**The mutation runner (`runtime/mutate.sh`).** A reviewer, or an engineer, writes the mutants as a
JSON list of `{id, file, find, replace, note}` and runs them through the runner instead of writing a
harness:

```bash
runtime/mutate.sh --mutants <list.json> --record <review folder> --round first \
    [--repo <repo>] [--commit <rev>] [--binding <binding.json>] [--by reviewer|engineer]
```

An engineer passes `--by engineer`: its rows go to `engineer-mutations.json` (a fix round:
`engineer-mutations-fix<N>.json`) and its batch log to `engineer-mutations.log.jsonl`
(`engineer-mutations-fix<N>.log.jsonl`), so an engineer's batch never takes the reviewer's
`mutations.json` in the same folder. No check reads the engineer's file as a reviewer's record.

It refuses the whole batch, before anything runs, when `find` does not occur exactly once in the file
at the commit, when no `mutation.tests` entry names tests for the file, or when an id is already a row
of the round's file. It makes a `git archive` copy of the commit (never the working tree), runs the
named tests on it unmutated first (a failing control refuses the batch), then plants each mutant in a
fresh copy, runs the tests with the block's `env` and `timeout`, and removes the copy. Before any test
runs, every mutant is planted once in an archived copy: a file that is a symlink or resolves outside
the copy, a `find` that does not occur exactly once there (overlapping matches counted; `git archive`
applies `.gitattributes`), or a replace that changes no bytes refuses the batch. After the control, the
probe runs for every mutated file: it empties the file in a copy and refuses when the named tests still
pass (a test that reads the file as text passes it, so alone it is the weaker check, and says so). When
the block's `loads` names the file (`{"files": <glob>, "command": [argv]}`), a load check runs as well,
before the probe: the command plus the file runs in an unmutated copy with the block's `env` and must
print that file in the copy. It proves where that command loads the file, not where the tests' own
runner does (a console script orders the module path differently), so it adds to the probe and never
replaces it. The block's `env` wins
over the caller's environment. Each verdict is first written to the round's batch log
(`mutations.log.jsonl`, `mutations-fix<N>.log.jsonl`, declared in record-contracts.json `rounds`), one
JSON line `{batch, commit, data, row}`, in one append, fsynced, and its verdict printed; only then is
the row appended to the record, under an exclusive lock on the record's folder, after re-reading the
file, so rows and dispositions written during a batch are kept. A row the batch wrote that an editor's
older copy dropped is written again and named; one whose runner fields (all but the disposition) were
changed is named and left as it is; either ends the batch with exit 3. A record that is not JSON at the
re-read stops the batch with exit 3, and the verdict is already printed and in the log. An edit made
after the batch's last append, or after it exits, is not seen by the runner: records-check reconciles
the file with the log and flags it, whenever the edit was made. An id already in the file stops the
batch (exit 3); an id already in the file or in the log at the start refuses it (a measured verdict is
copied back from the log, not measured again). The plan also refuses a relative test program that is
not a file with the exec bit at the commit, and a `--work` whose nearest existing folder is not a
writable folder. Exit 2 means refused with nothing written; exit 3
means stopped after a mutant ran, so rows may be written. SIGTERM, SIGHUP and Ctrl-C stop the tests and remove the work folder. Each result is
appended to the round's declared file (`--round first`: `mutations.json`; `--round fix<N>`:
`mutations-fix<N>.json`) as a row in the format above: `site` is `<file>:<line>`, `gate` the test path
that ran, `disposition` null, and `batch` the batch id. A survivor's disposition is the reviewer's to write; until then
records-check flags it. A run that times out writes no row and the runner exits 1. The reviewer still
chooses the mutants; a mutant that needs two edits is done by hand and recorded the same way.

The binding's `mutation` block names the tests per file glob; the first entry that matches decides
(`*` also crosses `/`). `{root}` in a command or an env value is the repository checkout, for what a
clean copy does not carry, such as an environment folder:

```json
"mutation": {
  "command": ["{root}/.venv/bin/python", "-m", "pytest", "-q", "-x"],
  "env": {"PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1"},
  "timeout": 600,
  "tests": [{"files": "src/pkg/reader/*", "tests": ["tests/reader"]},
            {"files": "*", "tests": ["tests"]}]
}
```

`links` (optional), `{"<path in the copy>": "<path in the checkout>"}`, is for a git repository the
checkout only points at, such as a data folder held in its own repository. Per batch, the runner takes one
`git archive` of that repository's committed HEAD into the work folder and links the path in every copy
to the snapshot, which is read-only, so a test that writes there fails instead of changing what the next
run reads; the live repository is only read. The HEAD is printed with the batch and written in each
row as `data`, so a verdict names the data it ran on. The snapshot is removed with the copies.

Preflight checks the block's shape (check 2) and that each test path and link target exists (check 3). A
test that reads a file neither the commit nor a linked repository's HEAD holds fails the control, and
the runner refuses every mutant judged by it.

**The stop check.** The reviewer role has `run_record`. Its stop check also reads the reviewer
contract: each folder under `reviews`, at any depth, in which the reviewer wrote a `findings*.md`
during its run must hold the paired mutations files and pass the same check (read from the working
tree, before it is committed). Each findings file written during the run needs its mutations file
written during the run too. A findings-like file written during the run under an undeclared name is
refused, naming the pair to write. Each findings file written during the run must not be empty, and
a contract (a `.md` whose name holds `contract`: `contract.md`, or a round's own such as
`contract-fix1.md`) sits beside it or in a folder above it inside the folder checked. An `open:`
survivor lets it stop. It is sent back once, with what is wrong.

**Escalation.** A rule flagged on 2 or more work orders (overridden ones included) is written with
`promoteToCode: true` and `for: "owner"`: a candidate to be enforced in code rather than in a prompt.

**The register.** `records.json` holds no date and no head, so it changes only when the findings
do. It lists each code commit that names no tracker id (`uncreditedCommits`).

**Limits.** The check reads commit subjects, so a code commit whose subject names no tracker id
belongs to no work order; the output counts and lists those commits. A fix round's commit subject
starts with its work-order id. In a refactor, a folder that is neither a step folder nor marked by
a job is not read; code committed after the latest step proof is still flagged, whatever folder
records it. A record folder named without the id is
not found. It runs predict-gate's own check only on the pairs a clean close carries; a record with
no clean close is checked for its artefacts, not for whether its moves were predicted. Refactor
timing reads the latest commit that touches a proof, so a later commit that only edits a proof file
(a typo fix, no re-run) counts as a new proof and hides code committed before it. A squash puts code,
proof and any later code change in one commit, so code after the proof cannot be seen.
Only predict-gate's `check` is shared, not the rest of `close`'s verdict, so three cases that
`predict-gate.sh close` refuses still read as closed here, each needing a hand-written ledger line:
another work order's passing files copied into this work order's step folder; an unpaired measured
file left in the step folder; and a close-purpose refusal followed by nothing, which backs a later
hand-written override. One predict-gate function for the whole per-pair verdict, used by both,
removes all three.

**The ops-currency flag (`stream.currency`).** One flag for the project, not for a work order
(its work order reads `(stream)`; the id and that label are kept from the earlier release, so
registers compare equal), owner the PM, next step "bring the ops ledger up to date with the
repository's commits". It is checked only for an owned repository, and it is raised when the
post-commit hook's own drift check, `runtime/ops-gap.sh` at its default threshold (the 5 the hook
passes), warns that the ops ledger is behind the repository. Why: the hook's "commits behind"
warning once went unread for days. The flag is not counted toward promote-to-code.

```bash
python3 runtime/records-check.py --repo <repo> [--binding <binding.json>] \
    [--since YYYY-MM-DD] [--as-of YYYY-MM-DD] [--ref REF] [--code-roots DIR ...] [--no-write]
```

## The rule register

`runtime/rules.json` lists every rule sentence in the 13 process prompts (`skills/pm/SKILL.md`, the
`/bakehouse:change`, `/bakehouse:health` and `/bakehouse:refactor` commands, and every file in `agents/`)
and says who enforces each one:

| `enforcer` | Means | `proof` |
|---|---|---|
| `code` | a tool or hook refuses | the runtime test that shows the refusal, `<module>.<Class>.<test>` |
| `reviewer` | a review role's brief checks it | `<review brief path>: <the line that checks it>`; the brief is `agents/design-reviewer.md` or `agents/reviewer.md` |
| `prompt` | neither | who or what checks it; `code exists, no proof test: <path> …` when a tool refuses and no test shows it |

A rule sentence is a piece of a prompt between sentence ends, list-line starts and fence marker
lines, holding `must`, `never`, `always`, `refuse(s)`, `may not`, `do not` or `don't`, of any
length. Fenced text is read: a brief template is what an agent receives. The key is the file plus
the sentence with its whitespace collapsed, so a re-wrap moves nothing and a reworded rule is a new
key. A piece that holds a rule word but no rule is listed under `notRules` with the reason.

A file may hold the same sentence more than once, for example a rule in the prose and again in a
brief template. `copies` lists each such sentence with the heading above each copy (the nearest
markdown heading outside fenced blocks, or `(top)`). Every other sentence must appear once.

`runtime/tests/test_rules_register.py` fails when a prompt gains, loses or rewords a rule sentence
the register does not list; when a copy is deleted, added, or moved under another heading; when a
new agent or role file is missing from `files`; when a `code` proof names no test case; or when a
`reviewer` proof names a file that is not a review brief, or a line that is not in it. Edit the
prompt and the register together.

The register checks bakehouse's own prompts, so the test runs only where `BAKEHOUSE_RULES_REGISTER=1`
is set: bakehouse's own CI sets it. In your own install the prompts are yours to edit, and the
test skips.

The register is the input for promotion: a lapse of a `prompt` rule makes it a candidate for code,
and a `code exists, no proof test` entry needs only its test.

Limits:

- The test checks that a proof exists, not that it proves the rule. A `code` proof may name any
  test that loads; a rule may be relabelled, or moved to `notRules`. Only a reader of the
  rules.json diff catches that.
- A new command file, or an agent file in a subfolder of `agents/`, is not read. The three
  commands are the owner's scope; a fourth needs the owner to add it.
- A rule word split by emphasis (`Do **not**`), a curly apostrophe (`don’t`), and a following
  sentence with no rule word that weakens a rule (`Unless asked.`) are not read. Neither is any
  other rule written without a rule word.
- Ids follow census order within each file. A new entry takes the next number, so no id moves when
  the register is edited; a census rebuilt from scratch can renumber them.
- A re-wrap that starts a line with `-`, `*` or a digit inside a sentence splits it, and the test
  fails until the entry is updated.
- A rule repeated in another file is its own entry; only its note names the other.
- A binding cannot yet add the repo's own rule files.

## Ownership

- **`owned: true`** — jobs run, the ops ledger records them, and `bakehouse setup` writes a
  post-commit hook into the repo's local hooks folder, which git never commits. A hook without the
  team's marker is never overwritten.
- **`owned: false`** — the repo belongs to another party. The health watch drafts a proposal for
  the `approver`; it offers no job. A job runs only inside a recorded approval of that exact scope
  in `approvals.json`, and its only output is a pull request. The ops ledger refuses the repo.

`approvals.json` is a list. Each record has a `scope` — `{"part": "<part>"}` or
`{"workOrder": "<id>"}` — and a `consumed` field the job fills in. A consumed approval never starts
a job again.

## Preflight

`runtime/team-preflight.py <refactor <part> | change <id> | health | startup>`. It reads files and
runs read-only git commands; it writes nothing. One line per failure; exit 1 on any failure.

1. The binding exists.
2. It parses, has the fields its `owned` value requires, only adds to the record contracts
   (`records`), and uses only the closed placeholder list.
3. Every path field exists in the checkout.
4. Every pointer finds its key. A gate the repo wrote as prose is listed, not failed.
5. Every role the job dispatches resolves. A role table (the personal one or the shipped one) that cannot
   be read, does not parse, is not a JSON object or has a `roles` that is not an object fails, named.
6. For a job when `owned` is false: an unconsumed approval of this exact scope exists.

`startup` mode runs checks 1 to 4, for a session start that knows the repo folder: it exits 0, and the
session carries any failure into its hand-back without stopping.

## The ledgers

All are append-only JSON lines in the project's records folder. A wrong row is never edited; a
correcting row follows it. `docs/TOOLS.md` lists the tools, and each tool's header comment is its full
reference.

| Ledger | Written by | What it is for |
|---|---|---|
| `ops/events.jsonl` | `ops-event.sh` | how the work was run: agent runs, fan-outs, loops, gates refused, decisions asked. `ops-gap.sh` reports how many commits it has fallen behind. |
| `decisions/ledger.jsonl` | `decision.sh` | every question put to the owner, the recommendation, the answer, and later the outcome. `DECISIONS.md`, at the top of the records folder, is the generated view. Scores agreement per category. |
| `spend/ledger.jsonl` | `spend.sh` | measured tokens per agent, grouped into waves with a ceiling. |
| `ratifications/ledger.jsonl` | `ratify.sh` | which entries of a closed vocabulary two blind readers agreed are right. |

`predict-gate.sh` and `save-report.sh` keep no ledger: the first compares a written prediction with
measured outputs and refuses any move nobody predicted; the second saves an agent's report
byte-for-byte.

### Turn caps

An agent that stops at its turn cap is recorded as an `agent-run` with outcome `turn-cap`: `count` is
the turns it used, as the harness reported them, and the note starts `briefed <N|-> of cap <M>`, the turns
its brief gave it and its agent type's `maxTurns` (`agents/<name>.md`). ops-event.sh keeps both as
`turnsBriefed` and `turnCap`, and refuses a turn-cap event without them. A cap hit means the brief asked
for more work than fits the agent type, so before each dispatch the PM states a turn estimate against
the cap beside the token estimate, and splits or cuts a brief that does not fit.

### Gate refusals

Each refusal by a team gate writes one `gate-refused` event to the ops ledger, so runs and
refusals can be counted per tool and per check. The event is ops-event.sh's, with one more field,
`gate`, written last and only on this kind:

```
{"ts": ..., "role": "engine", "kind": "gate-refused", "task": <work order id or null>,
 "purpose": "<tool> refused", "model": null, "effort": null, "count": <items refused or null>,
 "tokens": null, "durationS": null, "outcome": "refused", "note": <the refusal as printed>,
 "commit": <HEAD of the repository the tool ran in, or null>, "gate": "<tool>:<check>"}
```

- **One writer.** `runtime/gate_event.py` calls `ops-event.sh <kind> ... <note> <gate>`. `ops-event.sh`
  refuses a gate on any other kind, and a gate not of the form `<tool>:<check>` in lower case. An event
  without a gate keeps its old shape.
- **Where it goes.** The project of the record's repository, when the tool knows the record's path;
  else the project of the working directory's repository (`runtime/locator.py`, through `OPS_REPO`).
  A repository with no team, or whose binding does not say `"owned": true`, keeps no ledger, and the
  event is not written.
- **Best effort.** The tool has refused already. A failed write changes neither its exit code nor what
  it prints. The hooks log a failed write to `logs/agent-roles.log` in the machine folder.
- **Overrides keep their events.** The owner's override of `predict-gate.sh check` or `close` writes
  its existing event, purpose and outcome `done` unchanged, now with the gate. records-check still
  matches an overridden close by that purpose. An override that cannot be recorded is refused, as
  before. Its own event is not written, but the refusal's event is, routed like any refusal.
- **What is not a gate refusal**, so writes no event: argparse's own usage errors (a missing or unknown
  flag, a value outside its choices); a refusal before any project is found; the failure of the ledger
  write itself; a hook's second stop, which lets the agent go; the installers and wrappers. A
  records-check flag is a row in a register, not a refusal. Only its bad input (exit 2) is a refusal.
- **Spend kinds.** `spend.sh record --kind` takes one closed list: the roles of
  `runtime/agent-roles.json` plus its `spend_kinds` (`agent`, `answering-agent`, `no-role`: an agent that
  ran in no role, such as an Explore search, an ad-hoc general-purpose agent or a fork). A first record must
  name one; a correction keeps the kind of the row it corrects. `agent` names no role, so its entry says
  `"newRecords": false`: a new first record may not take it, while old rows, backfilled rows and
  corrections that keep it still carry it.

| gate | tool | refuses when |
|---|---|---|
| `decision:category` | decision.sh | `--category` is empty |
| `decision:question` | decision.sh | `--question` is empty |
| `decision:blind-autonomous` | decision.sh | `--blind` with `--autonomous` |
| `decision:options` | decision.sh | fewer than two `--option` |
| `decision:recommendation` | decision.sh | no `--recommendation`, or it is not one of the options |
| `decision:approves` | decision.sh | `--approves` is not one of the options |
| `decision:confidence` | decision.sh | `--confidence` missing or outside 0 to 1 |
| `decision:evidence-kind` | decision.sh | `--evidence-kind` missing or unknown |
| `decision:evidence` | decision.sh | `--evidence` missing |
| `decision:id-exists` | decision.sh | the id is already in the ledger |
| `decision:read-required` | decision.sh | no `--read` for a category that needs one |
| `decision:read-file` | decision.sh | a `--read` path is not a file |
| `decision:read-report` | decision.sh | a `--read` file is an agent's record, not the source |
| `decision:read-what` | decision.sh | no `--read-what` |
| `decision:inference` | decision.sh | the question uses an inference word without `guess:` |
| `decision:recheck` | decision.sh | `--recheck` misused, or no fix round to review |
| `decision:upstream` | decision.sh | a fix with no `--upstream` |
| `decision:fix-round` | decision.sh | a fix or fix round with no `--fix-round` or no `--task` |
| `decision:fix-round-number` | decision.sh | a round below 1, or a round number already used |
| `decision:rechecked` | decision.sh | fix round 2 or later with no `--rechecked` |
| `decision:stepped-back` | decision.sh | fix round 3 or later (or a third round asked) with no `--stepped-back` |
| `decision:unknown-id` | decision.sh | `answer` or `outcome` names no decision in the ledger |
| `decision:autonomous-answer` | decision.sh | `answer` on a decision taken autonomously |
| `decision:choice` | decision.sh | `--choice` is empty |
| `decision:agreed` | decision.sh | the choice differs from the recommendation and `--agreed` is not given |
| `decision:by` | decision.sh | `--by` is empty |
| `spend:wave-args` | spend.sh | `wave open` with no name or no `--ceiling` |
| `spend:wave-exists` | spend.sh | the wave name is already used |
| `spend:wave-ambiguous` | spend.sh | no wave named, and not exactly one open |
| `spend:wave-unknown` | spend.sh | the named wave does not exist |
| `spend:wave-closed` | spend.sh | the wave is already closed |
| `spend:backfill-note` | spend.sh | a backfilled row (SPEND_TS) with no `--note` |
| `spend:already-recorded` | spend.sh | the agent already has a row and `--correct` is not given |
| `spend:correct-no-row` | spend.sh | `--correct` for an agent with no row |
| `spend:architect-task` | spend.sh | an architect run with no `--task` |
| `spend:stepped-back` | spend.sh | a second architect run on one task with no `--stepped-back` |
| `spend:first-record` | spend.sh | a first record without `--why`, `--predicted`, `--predicted-tokens`, `--result` or `--outcome` |
| `spend:kind-required` | spend.sh | a first record without `--kind` |
| `spend:kind-not-a-role` | spend.sh | a new first record, or a correction that changes the kind, gives a kind that names no role (`agent`) |
| `predict-gate:check` | predict-gate.sh check | a move nobody predicted, a wrong move, or a mustNotMove key that moved (also the owner's override, which keeps its own purpose) |
| `predict-gate:hook` | predict-gate.sh hook (pre-commit) | a staged measurement the gate refuses: one event per stopped commit (an override writes its own, per folder) |
| `predict-gate:close` | predict-gate.sh close | the work order does not close (also the owner's override, purpose "work order close overridden") |
| `predict-gate:input` | predict-gate.sh check, close | bad input, exit 2: a prediction that does not parse, a missing file, a work order id of the wrong form |
| `predict-gate:anchor` | predict-gate.sh seal, revise, retire | the line is written to the ledger, and its commit (the anchor) failed: git's index lock still held after 5 tries, or no git repository. Exit 1; close refuses the work order until a new seal anchors: seal again once the cause is gone |
| `records-check:binding-records` | records-check.py | the binding's `records` object removes or replaces a contract artefact |
| `records-check:input` | records-check.py | any other bad input, exit 2 |
| `dispatch:role-unresolved` | agent-roles.py (PreToolUse) | a `role:` dispatch that no level resolves |
| `dispatch:binding-unreadable` | agent-roles.py (PreToolUse) | a dispatch whose decision needs a binding that cannot be read: binding.json does not parse, is not an object, cannot be read or is a dangling symlink, or its `reviews` field is not a safe path (the reason names that field and the binding file); the project needs setup (the locator's message; a team dispatch only, as in "Roles"); the project locator cannot be run or does not answer in time. The reason names the cause and what to fix. Its event is kept in `logs/agent-roles.log` in the machine folder (`GATE-EVENT ... kept in this log`) with its cause, because the working folder's ledger is found through the input that failed |
| `dispatch:record-folder` | agent-roles.py (PreToolUse) | a `Record folder:` line that cannot be decided |
| `stop:prediction` | agent-roles.py (SubagentStop) | a change job stops with no machine prediction |
| `stop:seal` | agent-roles.py (SubagentStop) | a change job stops with a moves file whose bytes no seal in its project's ops ledger names (sent back once) |
| `stop:run-record` | agent-roles.py (SubagentStop) | a role with `run_record` stops without writing its record |
| `stop:record-artefacts` | agent-roles.py (SubagentStop) | a record folder lacks its job's artefacts |

**Limits.**
- `ops-gap.sh` reads the newest `ts` in the ledger, and an automated refusal event moves it. So a lapse
  in hand-written events can be hidden by refusals.
- The runtime tools outside these five (mutate.sh, driver-signal.sh and the rest) write no event.
- A misspelt `--kind` is argparse's own error (exit 2), so it writes no event; only a missing kind or
  `agent` is counted.
- spend.sh reads `runtime/agent-roles.json` when it starts, so that file not parsing stops every
  spend.sh command, not only `record`.
- The event write waits at most 3 seconds (`gate_event.WAIT_S`), below the 10 seconds a hook is given; in
  the agent-roles hook, at most what is left of its 6-second run. A write cut there is lost, and the
  tool's exit and output are unchanged.
- The runtime's guard test (test_gate_events.py, `refusal_forms`) sees a refusal only when it is spelt
  `sys.exit(...)`, `raise SystemExit`, a literal `return 1` or `return 2`, a hook block or deny, or a
  `fail()` without a gate, and it judges "next to an event write" by position: it does not catch
  `os._exit`, a bare `exit()`, an aliased `sys`, a return placed after a covered branch, or an exit code
  passed through a variable.

## Reading team state

`bakehouse status --json [--since <ISO time>] [--repo <folder>]` is the one way anything outside the
tool reads team state. It reads the ops ledger and the decision ledger of the project the locator
finds for the folder (default: the current one). It writes nothing. Without `--json` it prints the same
content as short plain lines.

The JSON object:

| Field | Holds |
|---|---|
| `format` | `1`. A later release adds fields; it does not change these. |
| `team` | `true` |
| `project`, `records` | the repository root, and its records folder |
| `workOrders` | each work order with events in the ops ledger and no `work-order-closed` event: `workOrder`, `events` (how many), `latestRound` (the `round` of its latest event that has one, else `null`), `latestKind` and `latestAt` (the kind and `ts` of its latest event) |
| `waitingDecisions` | each decision asked, not decided autonomously, and not answered: `id`, `category`, `question`, `askedAt` |
| `closedSince` | each `work-order-closed` event at or after `--since` (all of them without it): `workOrder`, `closedAt`, `commit` (the commit the close event names), `overridden` (`true` when the close was the owner's override) |

`predict-gate.sh close` writes a `work-order-closed` event only when the close passes, or when it
fails and the owner's override is recorded first.

Exit codes match the locator's. 0: the state was printed. 3: no team here; `--json` prints
`{"format": 1, "team": false}`. 4: setup is needed; the locator's message is on stderr. 1: a ledger is
broken (the ops ledger as `verify-chain` judges it, or a decision ledger line that is not a JSON
object), or holds a line whose shape status cannot use; stderr names it. 2: bad usage, or a `--repo`
that is not a folder inside a git repository.

## Known limits in this release

- Only one repository has run the jobs. The binding's field list is complete for that repo and a
  hypothesis for any other.
- A job in a repo another party owns stops before it writes any plan or step state: where that
  state is kept is not yet decided.
- `bakehouse setup` refuses a repo another party owns (a sidecar team): R0 does not set up records for
  such a repo, and changes nothing in it (design 05 §4.8 is not yet met).
- `records-check.py --binding <file>` reads the ledger of the project the locator finds, not a ledger
  beside that file.
- A records pointer set by hand (`git config bakehouse.records`) to another project's records worktree is
  accepted, because every project's records worktree is on the same branch; the ledgers are then written
  there. `bakehouse setup` never writes such a pointer: `--pointer` refuses a main clone of another project.
- A fresh main clone whose remote `origin` cannot be reached cannot be set up: setup must first ask origin
  for the records branch, so that it never makes a second one. It says so and changes nothing.
- `bakehouse status` refuses a decision ledger line that is not a JSON object (exit 1), where
  `decision.sh` skips such a line with a warning.
- The locator's git calls have no timeout of their own: a records folder on a stalled mount makes session
  start wait until the harness's hook timeout.
- There is one git-hook launcher per machine. Every session start points it at that session's plugin
  root, so two projects on different plugin versions point it at each other in turn.
- The binding has no default-branch field and no pull-request tool field. Jobs read the default
  branch from `origin/HEAD` and open a pull request with the repo host's own tool, or hand the owner
  the branch name and the pull request text.
- The decision ledger scores calibration only for asks recorded with `--confidence`.
- The shipped signal script reports file length but not the longest function, and it does not
  count layer-rule breaks: both need a per-language rule the binding does not have yet.
- The shipped signal script has been checked against one repository's own signal script, where it
  gave the same result for every part. Its part rules for other kinds of repository are untested.
- The shipped signal script reads and writes a JSON register. A register in another format has to
  be converted once, keeping `settings` and every `decision`.
