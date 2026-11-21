# Getting started

This guide takes one project from nothing installed to its first `/bakehouse:change` job. Each step is
a command to run or a file to write. The reference is `docs/PROJECT-TEAM.md`. This guide links to it
and does not repeat it.

The example project is made up. It is `notes-app`, a small web app. Its API route
`POST /api/summarise` sends a note's text to an AI model and returns the model's summary. Its code is in
`src/`, its tests run with `npm test`, and it lives in git with a remote `origin`.

To try bakehouse before you write any of these files, copy the finished project:
[`examples/notes-app`](../examples/notes-app/README.md) holds every file this guide writes.

## What you need

- Claude Code.
- Python 3.10 or later, and Git 2.42 or later ([README, "Requirements"](../README.md#requirements)).
- The project in git, with a remote `origin`. Setup asks `origin` whether it already holds the team's
  records branch.
- This guide starts on one machine. A second machine, or a second developer, comes in at
  [section 10](#10-a-second-machine-or-a-second-developer)
  ([README, "People and machines"](../README.md#people-and-machines)).

## 1. Install the plugin

1. Open the project's main clone.
2. Run the install command in [README, "Install"](../README.md#install), step 1. It installs the plugin
   for this project only.
3. Write `.claude/settings.json` as step 2 there shows, and commit it.

## 2. Make the folders the binding names

Preflight checks that the folders and files the binding names exist (checks 3 and 4 in
[Preflight](PROJECT-TEAM.md#preflight)). So make them first:

```bash
mkdir -p .team docs tools
```

- `.team/` holds the binding and the gate list.
- The run folders need no folder here. The binding below names no `runs` and no `reviews`, so it is in
  records mode: each job writes its record folder under `runs/` in the records worktree, which
  `bakehouse setup` makes (step 7). It sits beside the main clone, on the `bakehouse-records` branch,
  never in the project's release. To keep the record folders in the code tree instead, add
  `"runs": "tree", "reviews": "team/runs/"` to the binding and make that folder
  ([Where the run folders live](PROJECT-TEAM.md#where-the-run-folders-live)).
- `docs/` holds the tracker file. `tools/` holds the measure script.

## 3. Write the gate list

A gate list is a JSON (or YAML) file with a list of gates under one key. Each gate has these fields:

| Field | Holds |
|---|---|
| `id` | the name a work order uses for this gate |
| `command` | the command that runs the gate |
| `pass` | what counts as a pass |
| `when` | when the gate runs (optional) |

Write `.team/gates.json`:

```json
{
  "gates": [
    {"id": "unit", "command": "npm test", "pass": "exit 0", "when": "after every commit"},
    {"id": "api-moves",
     "command": "node tools/measure.mjs <record folder>/moves.measured.json",
     "pass": "predict-gate.sh check --measured <record folder>/moves.measured.json exits 0",
     "when": "on BASE into moves.baseline.json first; then after the change"}
  ]
}
```

- A `<...>` in a gate command is left for the agent that runs it to fill.
- The binding itself may not hold a `<...>`. Preflight refuses one there (check 2).
- A gate whose command does not start with a program on the PATH, or a file in the repo, is a prose gate.
  Preflight lists it, and a reviewer runs it by hand.

## 4. Write the measure step

The predicted-moves gate compares two flat JSON files: the outputs before the change
(`moves.baseline.json`) and after it (`moves.measured.json`). Each key is one output. Each value is a
string, a number, `true`, `false` or `null`. A web app's outputs are its API responses, so the measure
step sends a fixed set of requests and writes each response as keys.

The model is mocked. A real model gives a different reply each time, so two runs on the same code would
differ. The engineer's first check is that BASE measured against BASE moves nothing. That check only
passes when the model is mocked.

Write `tools/measure.mjs`:

```js
// The app's API outputs as flat key -> value, with the model mocked.
// Usage: node tools/measure.mjs <out.json>
import { writeFileSync } from "node:fs";
import { handleSummarise } from "../src/api/summarise.js";

const cases = {
  ok: { text: "Water the tomatoes on Friday." },
  empty: { text: "" },
  missing: {},
};

const out = {};
for (const [name, body] of Object.entries(cases)) {
  let calls = 0;
  // The mocked model: a fixed reply, so the same request always gives the same output.
  const model = {
    complete: async (prompt) => {
      calls += 1;
      return { text: `summary of ${String(prompt).length} characters` };
    },
  };
  let res;
  try {
    res = await handleSummarise(body, { model });
  } catch (e) {
    res = { status: "threw", body: String(e) };
  }
  out[`summarise.${name}.status`] = res.status;
  out[`summarise.${name}.body`] = JSON.stringify(res.body);
  out[`summarise.${name}.modelCalls`] = calls;
}
writeFileSync(process.argv[2], JSON.stringify(out, null, 2) + "\n");
```

- This sketch assumes the handler takes its model client as an argument. If your handler imports the
  client itself, mock that import with your test runner instead.
- The keys (`summarise.empty.status` and so on) are the names a work order and a prediction use.
- A response body is an object, so the script stores it as a JSON string. A value in the measured file
  may not be an object or a list.
- The script is the project's own tool. It lives in the repo, and the gate list names it.
- The file formats, `check`, and the measure report a seal reads are in
  [`docs/PREDICT-GATE.md`](PREDICT-GATE.md) ("Run it", "Sealing").

## 5. Write the binding

The binding is the project's facts for the team. Write `.team/binding.json`:

```json
{
  "_about": "Repository facts for the project team.",
  "owned": true,
  "tracker": {"file": "docs/TRACKER.md", "id": "^T[0-9]+$"},
  "measure": {"commands": ["git log --no-merges --numstat -- src/{part}"]},
  "gates": {
    "byteIdentical": ".team/gates.json#gates",
    "predictedMoves": {"gates": ".team/gates.json#gates", "prediction": "moves.prediction.json"}
  },
  "branches": {"refactor": "refactor/{id}-step{n}-{name}", "change": "fix/{id}-{name}"},
  "records": {"codeRoots": ["src/"]},
  "mutation": {"command": ["node", "--test"],
               "tests": [{"files": "src/*", "tests": ["test/summarise.test.js"]}]}
}
```

- `.team/gates.json#gates` is a pointer: the list under the key `gates` in that file.
- `records.codeRoots` names the code folders. Close uses it to tell code commits apart. Preflight refuses a
  binding that names no code roots for `change` and for `refactor`.
- `/bakehouse:change` needs no `register` and no `signal`. `/bakehouse:health` and `/bakehouse:refactor`
  need both: add them before you run those jobs ([The signal](PROJECT-TEAM.md#the-signal)).
- `prediction` is the file the predicted-moves gate reads, `moves.prediction.json`. Preflight refuses a
  name the gate does not read.
- `mutation` tells the mutation runner how to test a mutated file: the test command, and which tests
  to run for which files. The reviewer uses it to check that the tests catch a deliberate break
  ([The binding](PROJECT-TEAM.md#the-binding), field `mutation`). Preflight checks that the test files
  exist: name your own.
- Every field, and which ones are required: [The binding](PROJECT-TEAM.md#the-binding).

## 6. Write the tracker item

A work order lives in a tracker item. Write `docs/TRACKER.md`:

```markdown
# Tracker

## T1: an empty note gets a 400, and no model call

id: T1
need: POST /api/summarise with no text returns status 400 and the error "text is required", and calls no model.
evidence: node tools/measure.mjs on BASE: summarise.empty and summarise.missing each return status 200 and make 1 model call.
layer: src/api/summarise.js, the request handler: it passes the text to the model without checking it.
gates: unit, api-moves
expected: summarise.empty.status 200 -> 400; summarise.empty.body -> {"error":"text is required"}; summarise.empty.modelCalls 1 -> 0; the same three keys for summarise.missing.
mustNotMove: summarise.ok.*
decided: owner, 2026-11-15
serves: a person who submits an empty note gets a clear message, and no model call is paid for; 2 of the 3 request cases the measure step sends.
```

- The eight fields are `id`, `need`, `evidence`, `layer`, `gates`, `expected`, `mustNotMove` and
  `decided`. Each is written as its name and a colon. The job stops and names any field that is missing.
- `id` matches the binding's `tracker.id`.
- `gates` names gate ids from the gate list. It never holds a command.
- `expected` and `mustNotMove` name keys of the measured file.
- `decided` names a role and a date, never a person's name.
- `serves:` is required. It names the outcome for the owner and a count of what the change reaches.
- `branch:` is optional. Without it, the job opens a branch in the binding's `branches.change` form and
  writes the line itself.
- Each field in full: [`commands/change.md`](../commands/change.md#the-work-order), "The work order".

## 7. Commit, push, and run setup

1. Commit the files and push them. The job reads the tracker item on `origin/<default branch>`.

   ```bash
   git add .team/binding.json .team/gates.json tools/measure.mjs docs/TRACKER.md
   git commit -m "Add the team binding, the gate list, the measure step and the tracker"
   git push
   ```

2. If `git rev-parse --abbrev-ref origin/HEAD` prints an error, set it:

   ```bash
   git remote set-head origin --auto
   ```

3. Open a Claude Code session in the main clone. The plugin's `bin/` is on the Bash tool's PATH only in
   such a session. Run, from the Bash tool:

   ```bash
   bakehouse setup
   ```

   - It makes the records worktree `../notes-app.bakehouse-records` on the branch `bakehouse-records`.
   - It writes the git config `bakehouse.records`, and installs the post-commit drift report.
   - It sets the records worktree's writer id, the git config `bakehouse.writer`: `w` and 8 random hex
     characters, or the name you give with `bakehouse setup --writer <name>` (1 to 32 letters, digits or
     hyphens). Each tool writes its records to files named by this id, so two machines never write the
     same file. Lane clones share the main clone's id. A project set up before bakehouse 0.8.0 moves to
     these files once, with `bakehouse setup migrate`
     ([One records file per writer](PROJECT-TEAM.md#one-records-file-per-writer)).
   - `bakehouse setup --pre-commit` also installs the commit gate. `bakehouse setup --help` lists every
     option. When to use `--no-remote-check`: [README, "Install"](../README.md#install), step 4.

4. Check the binding and the team's state:

   ```bash
   bakehouse run team-preflight.py change T1
   bakehouse status
   ```

   Preflight prints `ok` on its last line when the binding is right. Each `FAIL` line names what to fix.

## 8. Where drivers go, and why

- A driver is a one-off script an agent writes during one run, to measure, compare or attack a change.
- A driver goes in `drivers/`, directly inside the run's record folder. In this project that is
  `runs/<run>/drivers/` in the records worktree.
- Why: the driver signal (`runtime/driver-signal.py`) reads driver scripts only from
  `runs/<run>/drivers*/` (in tree mode, `<reviews>/<run>/drivers*/`). A script kept anywhere else is not counted. (With `drivers.nested: true`
  in the binding, it also reads `drivers*` folders deeper inside a run.)
- The signal counts the same script written again in run after run. That count is how the team learns
  that a script should become one shared tool.
- The engineer and reviewer briefs already say where drivers go.
- The measure script in `tools/` is not a driver. It is the project's own tool.
- To see what the signal finds, from the repo root: `bakehouse run driver-signal.sh --repo . --no-write`.
- The rules and the register: [The driver signal](PROJECT-TEAM.md#the-driver-signal).

## 9. Run the first change

Run the job in a Claude Code session opened in the project. A session opened in another repo cannot run
it here.

1. Try it with reads only. The dry run checks the work order and fills the briefs. It changes nothing.

   ```
   /bakehouse:change T1 --dry-run
   ```

2. Run it:

   ```
   /bakehouse:change T1
   ```

What happens:

1. The session is the PM. It runs preflight, reads T1, and asks you for a go on the work order.
2. It opens the branch `fix/T1-<name>` and names the record folder, for example
   `<the records worktree>/runs/2026-11-15-T1-step1-review/` (`bakehouse status` prints the records
   worktree's path as `records:`).
3. Before each agent, it states a token estimate and waits for your go.
4. The engineer writes `moves.prediction.json` before its first edit, commits it with
   `bakehouse record commit`, and stops.
5. The PM brings you the prediction. You give a go, or not. Nothing is switched before your go.
6. The engineer makes the change and measures it. Any move it did not predict stops the job.
7. A reviewer reviews the diff. A fix round follows when it finds something.
8. `predict-gate.sh close T1` must exit 0. Then the PM opens a pull request. You merge it.

Every step in full: [`commands/change.md`](../commands/change.md), "The steps".

## 10. A second machine or a second developer

A second main clone of the project can be yours on another machine, or another developer's. Each main
clone writes its own records files. bakehouse moves them through the `bakehouse-records` branch on
`origin` ([Records sync](PROJECT-TEAM.md#records-sync)).

### Once per project, on the first machine

1. Every machine that runs bakehouse in this project runs the same bakehouse version.
2. Move the records to format 2:

   ```bash
   bakehouse setup migrate
   ```

   When it is refused because the records worktree has uncommitted changes, commit them by path and run
   it again ([One records file per writer](PROJECT-TEAM.md#one-records-file-per-writer), point 4).
3. When `origin` does not hold the records branch yet (`git ls-remote --heads origin bakehouse-records`
   prints nothing), publish it once:

   ```bash
   git -C ../notes-app.bakehouse-records push -u origin bakehouse-records
   ```

4. When more than one machine will write new tracker items, reserve their ids. Add `reserve` to the
   binding's `tracker` block, with the letters that start every id:

   ```json
   "tracker": {"file": "docs/TRACKER.md", "id": "^T[0-9]+$", "reserve": {"prefix": "T"}}
   ```

   Commit and push the binding. Add each lane clone of this machine as a remote of the main clone
   (`git remote add <lane> <the lane clone's folder>`); its branches are then read from disk, and nothing
   is pushed. Then record the highest id used so far:

   ```bash
   bakehouse reserve --start
   ```

5. Check that the records move: `bakehouse sync` prints no `bakehouse sync:` line and exits 0.

### On the new machine

1. Clone the project and open it:

   ```bash
   git clone <the project's URL> notes-app
   cd notes-app
   ```

2. Open a Claude Code session in the clone. The committed `.claude/settings.json` names the
   marketplace. If `claude plugin list` does not show `bakehouse`, run the install command in
   [README, "Install"](../README.md#install), step 1.
3. If git cannot push to `origin` without asking for a login, write a token file outside the project:
   one line, `<NAME>=<the token>`, readable only by you (`chmod 600`). bakehouse reads it at each sync
   and never stores the token ([Records sync](PROJECT-TEAM.md#records-sync), point 7).
4. From the session's Bash tool, run setup with a writer name for this main clone (1 to 32 letters,
   digits or hyphens; for example your name and the machine's):

   ```bash
   bakehouse setup --writer <name>
   ```

   With a token file, add `--token-file <path> --token-key <NAME> --token-user <your login>`. Setup
   finds `bakehouse-records` on `origin` and makes the records worktree from it, so this machine reads
   the project's records at once.
5. Check:

   ```bash
   bakehouse status
   bakehouse sync
   ```

   `bakehouse status` lists the work orders and decisions the other machines have pushed. `bakehouse
   sync` prints no `bakehouse sync:` line.
6. When the binding has `tracker.reserve`, run `bakehouse reserve --start` once here too. When the
   recorded floor already covers this machine's branches, it changes nothing and says so. From then on,
   take each new id from `bakehouse reserve` before the tracker item is written; the PM does this.
7. Each lane clone on this machine: `bakehouse setup --pointer <this main clone>`, as on the first.

### Working side by side

bakehouse moves the records at each seal and close, at session start, and with `bakehouse sync`.
Offline, a seal or close still works, and its records go at the next sync. A new reserved id needs the
remote.

A sync moves only committed records. Seals, closes and reservations commit themselves. A decision or a
spend line does not: it reaches the other machines only after it is committed in the records worktree.
Commit the `decisions` folder, and the `spend` folder once it exists, by path, then sync. Leave `ops`
to the seals and closes.

```bash
git -C ../notes-app.bakehouse-records add decisions
git -C ../notes-app.bakehouse-records add spend   # only once the spend folder exists
git -C ../notes-app.bakehouse-records commit -m "records: decisions and spend"
bakehouse sync
```

What bakehouse does not do yet, and how to work until it does:

1. **Two people can take the same work item.** Nothing stops it. Agree who takes which. The simplest
   split is by product: each product on its own branch.
2. **Only one person runs the health and driver passes** (`/bakehouse:health` and the driver signal).
   Each pass rewrites one register file, so two people running them conflict at the next sync.
3. **A change to shared code** (code that another developer's product also uses). Nothing records or
   enforces an agreement. Until a later release does:
   1. Before the work order, the developer who wants the change agrees it with each other developer
      whose product uses that code, in a message or a call.
   2. The PM records the agreement as a decision on the work order: the question names the other
      developer, the date and what was agreed, and the owner answers it. Commit it and sync, as above;
      the other machines' `bakehouse status` then shows it.
   3. The same ask declares the change with `--change --owners 2 --area <the shared code>`. Two owners
      fire the altitude trigger, so the ask needs an architect check: an `--architect-option`, or a
      recorded architect run ([Decision triggers](PROJECT-TEAM.md#decision-triggers)).

   A developer who skips step 1 is not stopped.

## Read next

- [`docs/PROJECT-TEAM.md`](PROJECT-TEAM.md): the team, the binding, the roles, the records.
- [Lanes](PROJECT-TEAM.md#lanes): running two or three work orders at once, each in its own clone.
- [`docs/PREDICT-GATE.md`](PREDICT-GATE.md): the gates, the files, sealing and close.
- [`docs/TOOLS.md`](TOOLS.md): every script.
