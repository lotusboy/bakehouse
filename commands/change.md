---
description: "Make the current repo's tool do something new, or fix a defect, from one work order — preflight, the work order as the decision, then one engineer step behind the binding's predicted-moves gates, a reviewer the PM dispatches, a fix round and one pull request. The engineer writes every expected output move before its first edit; any move not predicted stops the job. Every repo fact comes from the repo's binding and the work order. Usage: /bakehouse:change <work-order id> [--dry-run [--from <file>]], where the id is the tracker item that holds the work order. Trigger phrases: \"make the change in <id>\", \"run work order <id>\", the owner approving a work order."
harness: claude-code
---

# bakehouse — Change

The `change` job for *this* repo, on one work order. You are the PM. Agents do the building and the
reviewing. The owner takes every decision.

A change makes the tool do something new, or fixes a defect. Its behaviour changes on purpose. So its
gate is **predicted moves**: the engineer writes every expected output move before its first edit,
and any move it did not predict stops the job. A restructure that changes no output is not a change;
it is `/bakehouse:refactor`.

**The work order is the decision.** It lives in the repo's tracker item. Someone wrote it before this
job starts: the owner, or a repo procedure that found the tool must change. Its eight fields are
below. This job never invents a field. If a field is missing, stop and name it.

**Facts come from the binding and the work order.** Every repo path, command, gate, branch form,
tracker, record folder and owner rule comes from the repo's binding, `.team/binding.json`,
from the repo files its pointers name, or from the tracker item. Never use a path, command, gate or
figure from anywhere else. A copied fact would bind every repo to the one it came from. If a step
needs a fact that none of these gives, stop and name the missing field.

**No dispatch without the owner's go on the spend.** Before each agent dispatch, state the estimated
tokens and wait for the owner's go. The one exception is unattended mode: while it is on, the PM skill's
"When the owner is away" section governs, with a headroom check before each dispatch.

## The work order

Eight fields, each written in the tracker item as its name followed by a colon (`need:` and so on).

| Field | Holds | Used in |
|---|---|---|
| `id` | the tracker id; it must equal this job's argument | everything |
| `need` | one sentence: what the tool must do that it does not do now | the engineer brief, the pull request |
| `evidence` | where the failure shows: document, page, table, run, output, count | the engineer brief; the reviewer re-runs it |
| `layer` | the owning layer the requester's root-cause check found | the engineer brief: the fix goes there |
| `gates` | gate ids from the repo's gate list that prove the change; never a copied command | the engineer and reviewer briefs, resolved through the binding |
| `expected` | the moves the requester expects; the prediction must cover each one | the prediction check (step 6) |
| `mustNotMove` | outputs that must stay byte-identical | the prediction check, the gates |
| `decided` | who decided the tool must change, as a role, and the date | the engineer brief, the pull request |

Beside the eight fields, the item carries a **`branch:` line**: the branch the change is made on.
Whoever writes the work order may write it; a repo procedure that writes a work order during its own
work names its own working branch there. If the item has no `branch:` line, the job writes one when
it opens its own branch (step 5). The job works only on the branch that line names. It never opens a
second branch for the same item, and it never copies a work order from one branch to another.

Beside them, too, the item carries a **`serves:` line**: the outcome this change moves for the
owner, in the owner's own terms, with a count of the in-scope items it reaches. "In scope" is what
the owner needs now; where the repo's gate list has a scope gate, that gate says how to count. A
slice or a sample the work was framed on is counted the same way. Whoever writes the work order
writes this line; the job never fills it.

**A new work order's id comes from `bakehouse reserve`** when the binding has `tracker.reserve` and the
records are in format 2 (bakehouse `docs/PROJECT-TEAM.md`, "Work-order ids reserved before
use"). Whoever writes the work order runs it before writing the tracker row, and writes the id it
prints. The id must be reserved first: preflight (step 1) fails an id above the floor with no
reservation, naming `bakehouse reserve`.

The job writes back to the same item, on that branch: `BASE`, the record folder, the prediction file,
the owner's go on the prediction, each gate's result, the commits and the pull request. The item is
the job's re-entry state.

## Words used below

- **`{root}`**: the repo root, `git rev-parse --show-toplevel`. Stay in it. Run every git command
  in the records folder as `git -C {records} …`; never `cd` away from the repo.
- **`{records}`**: the project's records folder, where the ops ledger and the other team records
  live: `python3 "${CLAUDE_PLUGIN_ROOT}/runtime/locator.py" records`.
- **The binding**: `{root}/.team/binding.json`, or, for a repo another party owns, the one in its
  sidecar folder: `python3 "${CLAUDE_PLUGIN_ROOT}/runtime/locator.py" binding` prints its path.
- **The default branch**: run `git fetch` first. Then `git rev-parse --abbrev-ref origin/HEAD` gives
  `origin/<default>`. Every "merged" and every "on the default branch" below means on
  `origin/<default>`, never a local branch. If `origin/HEAD` is not set, ask the owner which branch
  pull requests go into. In a repository with no remote there is no `origin/<default>`: the default
  branch is the local one the owner names (for example `main`), and `predict-gate.sh close --merged`
  reads only `HEAD`, so it runs on that branch after the local merge.
- **The tracker item**: the lines in `<binding.tracker.file>` that carry `{id}`, where `{id}` matches
  `binding.tracker.id`. An item may span a register row and a section under its own heading; read
  every place that carries `{id}` and a field label. Until its branch merges, the item may exist
  only on that branch. To find it on any ref: `git for-each-ref --format='%(refname)' refs/heads
  refs/remotes`, then `git grep -n -E '<{id} as a whole word>' <ref> -- <binding.tracker.file>` per
  ref.
- **The job's branch**: the branch the item's `branch:` line names.
- **One step.** A change is one work step on one branch, so `{n}` is `1`. Work that needs more than
  one step is more than one work order.
- **`BASE`**: the commit the change starts from. On a branch the job opens, it is the head of
  `origin/<default>` when the branch is opened. On a branch the job did not open, it is that
  branch's head when step 5 runs. Record it in the item; never recompute it.
- **A pointer** (`file#key` or `file#key.id`): open `file` in the repo root. `file#key` means the
  whole list under the top-level `key`. `file#key.id` means the one item under `key` whose `id` is
  `id`. Each gate item gives its `id`, `command`, `pass` and, if present, `when` and `per`. A gate
  whose `command` is prose, not a command, is a **prose gate**; preflight lists them (check 4).
- **The job's gate list**: `binding.gates.predictedMoves.gates`, resolved. Every gate in it passes,
  each under its own `when`. The work order's `gates` names the ones that prove the need; each must
  be in this list.
- **Placeholders.** `{root}`; `{id}`; `{n}` (`1`); `{date}` (fixed once, when step 5 runs,
  as `YYYY-MM-DD`; never refilled on a later day); `{name}` (a short name the PM chooses when it
  opens the branch). Leave every `<…>` in a repo-held command for the agent that runs it; the
  reviewer checks it was filled.
- **The record folder**: `<binding.reviews>` + `<binding.reviewName>`, filled with the fixed
  `{date}`, in tree mode; `runs/` + `<binding.reviewName>` in `{records}`, in records mode.
  The binding's `runs` names the mode: `"records"` keeps the run folders on the records branch, `"tree"`
  in the code tree; with no `runs`, a binding that names `reviews` is in tree mode, any other in records
  mode.
  Defaults when the binding omits them: `reviewName` is `{date}-{id}-step{n}-review`; `reviews`, in
  tree mode, is `axis/runs/`. The folder sits directly under `reviews`: the role hook sends a
  reviewer back once if no new file appears in a folder there. To find an existing folder, match
  `reviewName` with `*` for `{date}`.
  A brief names it on its `Record folder:` line as a full path, `{root}/` + the folder, in `~/`
  form: the role hook decides the folder at dispatch and refuses a relative line.
  In records mode that full path is `{records}/runs/` + the folder's name.
  The line holds the path only (backticks or quotes may wrap it, and a path with a space needs them);
  notes go on the next line: the hook refuses any text after the path.
- **The prediction file**: `binding.gates.predictedMoves.prediction`, inside the record folder. It
  is the machine file the predicted-moves gate reads, `moves.prediction.json`; preflight refuses a
  binding that names anything else. A prose prediction may sit beside it, and nothing checks it.
- **A round**: a fix round that moves an output writes its own files beside the first round's:
  `moves.prediction.<round>.json`, `moves.measured.<round>.json` and, when it is measured against
  its own baseline, `moves.baseline.<round>.json` (for example `.fix1`), and, when a predicted move
  did not happen, `moves.missed.<round>.json`. A round with no baseline of
  its own is measured against `moves.baseline.json`. A round chained on an earlier measurement names
  it in its prediction: `"baseline": "moves.measured.built-v5.json"`; nothing guesses it.
- **`history/`**: a superseded or revised prediction, a prototype measurement and an older
  measurement are not part of the closed record. Each goes in `<record>/history/`, moved with
  `git mv` and committed. Close lists `history/` and checks none of it. A commit that touches
  `history/` in or after the commit of the record's first measurement needs the owner's decision,
  cited on that commit as `Prediction-approved: <decision id>` (see step 6.2). Any other top-level
  `moves.measured*` file with no prediction of its round makes close refuse (bakehouse
  `docs/PREDICT-GATE.md`, "Closing a work order").
- **Record commits.** In records mode a record file (a prediction, a measured file, `history/`,
  `disposition.md`) is committed with `bakehouse record commit <record folder> -m <message>`, one `-m`
  per paragraph and a trailer in its own; `git mv` into `history/` runs in `{records}`. In tree mode a
  record file is committed with git, by path, as this file says.
- **Commit trailer.** Every commit this job makes or asks for ends with the session's own
  `Co-Authored-By` line, taken from its attribution instructions. Never hardcode a model.

## Dry run

`/bakehouse:change <id> --dry-run` runs steps 1 to 4 as reads only, then fills the
[engineer brief](#engineer-brief) and the [reviewer brief](#reviewer-brief) as far as they can be
filled before any work exists. It writes the briefs into a scratch folder outside the repo and shows
them. It opens no branch, commits nothing, writes nothing to the repo or the records, records no ops
event and dispatches nothing. It reports what step 3 finds and carries on to step 4 whatever that
is, so a finished work order can be dry-run. A field that only exists after the engineer runs (its report, the
diff) is left as `[after the engineer]`. Every other `{…}` and `[…]` must be filled; any left is a
defect of the work order or the binding, and the dry run names it.

`--from <file>` reads the work order from that file instead of the tracker item, for a past change
written up as a work order after the fact. It is allowed only with `--dry-run`.

## The steps

Each step says who does it. "PM" is this session.

### 1. Preflight (PM)

From the repo root, run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/runtime/team-preflight.py" change <id> --repo "$(git rev-parse --show-toplevel)"
```

- It exits non-zero: show its output and stop. Change nothing. A missing binding means the team does
  not apply to this repo. Say that plainly. Never guess a repo's commands.
- It exits 0: carry on. A `note` line is not a failure. Keep two kinds of note:
  - the prose gates (`check 4`), by id;
  - the role lines (`check 5`): which agent fills `change:engineer` and `change:reviewer`. "The
    session does it" means you do that role's work yourself, behind the same gates, and say so in
    the report. The reviewer role never falls to the session.

Then, when the binding has a `tracker`, check the argument: `<id>` must match `binding.tracker.id`.
If it does not, stop and show the form. When the binding has no `tracker`, skip this check.

### 2. Ownership (PM)

Read `binding.owned`.

- **`true`**: carry on.
- **`false`**: stop after this step. Say this to the owner:
  - preflight check 6 has confirmed an unconsumed approval in `approvals.json` beside the binding whose
    scope is exactly `{"workOrder": "<id>"}`. Without one, preflight failed: offer to draft a
    proposal for `binding.approver`, as `/bakehouse:health` step 6 does, naming the approver by role,
    never by a person's name;
  - the blocker is design question Q8: where a job in another party's repo keeps its work order and
    step state is not yet decided. This job reads and writes the work order in the repo's tracker,
    and in another party's repo it may write only a pull request. So it runs no further than this
    step until Q8 is decided and the binding says where the work order lives;
  - when it does run, the approval covers this work order only, the only output is a pull request,
    and the repo's tracker, hooks and other branches are never written. The ops ledger refuses this
    repo; do not work around it.

### 3. Re-entry (PM)

Find where this work order stands before doing anything else. Run `git fetch` first.

1. **Find the item.** Read it on `origin/<default>`. If it is not there, find it on any ref (see
   "The tracker item"). If no ref holds it, stop: there is no work order to run.
2. **Find the job's branch.** Read the item's `branch:` line. If there is none, look for a branch
   matching `binding.branches.change`, ignoring case (fill `{id}`, use `*` for `{name}`, and run
   `git branch -a -i --list '<pattern>' '*/<pattern>'`; the second pattern finds the remote).
3. **Done**: the item on `origin/<default>` carries the done-mark, or the job's branch is merged into
   `origin/<default>`. The done-mark covers a squash merge, which leaves the branch unmerged. Say it
   is done, with its pull request, and stop.
4. **In flight**: not done, and the job's branch exists. Work on that branch, at the first output
   missing, in this order. Never open another branch; never start the job again.
   1. No `BASE` and record folder in the item on the branch → step 5, on that branch.
   2. No prediction file in the record folder (the folder named in the item on the branch; else
      match the folder with `*` for the date) → step 6.1.
   3. A prediction file, and no owner's go on it in the item on the branch → step 6.2.
   4. The owner's go, and no engineer's result entry in the item on the branch → step 6.3.
   5. No `findings.md` in the record folder → step 7. A folder without `findings.md` is a review that
      did not finish.
   6. Findings with no `disposition.md` beside them → step 8.
   7. No pull request that carries the branch → step 9.
   8. A pull request open, not merged → wait for the merge. Say so and stop.
5. **Fulfilled outside the job**: the item's branch has no `BASE` or record-folder line, but the item
   already holds a prediction, or the branch holds commits that name this id. The session that wrote
   the work order did the work itself. This is in flight, not "not started". Never dispatch the
   engineer. Say what was found, and go to step 7 (review) on that branch, with `BASE` set to the
   branch's merge base with `origin/<default>`.
6. **Not started**: no job's branch yet, or a `branch:` line whose branch has no job lines, no
   prediction in the item and no commits that name this id. Go to step 4.

### 4. The work order (PM → owner)

1. **Read it.** Read the item where step 3 found it: on `origin/<default>`, or on the ref that holds
   it. With `--from`, read the file.
2. **Check it.** Each check that fails stops the job and names the field:
   - all eight fields are present and not empty;
   - `id` equals the argument;
   - every id in `gates` is a gate in the job's gate list;
   - `expected` lists at least one move, or says `none` with a reason;
   - `mustNotMove` names outputs, or says `none` with a reason;
   - `decided` names who decided, as a role, and a date;
   - the item carries a `serves:` line with a count. A change whose count of in-scope items is zero,
     or is the smaller share, stops here and goes to the owner as one decision before any spend:
     passing every gate does not make a change worth making;
   - when the part being changed has an architecture decision on record (a restructure or redesign
     decision in the health register, or an architect's report the work order or binding names), the
     work order names the design step or stage the change belongs to. A change that fits no stage, or
     does a stage's job in a different place, stops here and goes to the architect before the owner.
     This applies to fix rounds that add a new mechanism, too.

   Never fill `expected`, `mustNotMove` or `decided` yourself. They belong to whoever wrote the work
   order. Offer to write the missing field with them.
3. **Put it to the owner** as one message, in this order: the `need`; the `evidence`; the `layer`;
   the gates that prove it and the rest of the job's list; the `expected` moves and the `mustNotMove`
   outputs; the `serves` line and its count; the branch the job will work on (the `branch:` line, or the one it will open); the size in hours and tokens; one question: go or not. A
   work order from a repo procedure was already decided there; the go here is the go on the spend
   and the branch.
4. **A dry run stops here** and fills the briefs.

### 5. The branch (PM)

Owned repos only. Run `git fetch`. Fix `{date}`.

- **The item has a `branch:` line**: check that branch out and work on it. Do not open a branch.
  `BASE` is its head now.
- **It has none**: choose `{name}`. `BASE` is the head of `origin/<default>`. Open the branch from
  `BASE`: `binding.branches.change`, filled. The item must be on `origin/<default>` for this; if it
  exists only on another branch without a `branch:` line, stop and ask the owner which branch the
  change belongs on. Never copy the work order.

Then commit the job lines into the item on the branch: the `branch:` line (when the job opened the
branch), `BASE` and the record folder. Commit by path, with the trailer. Fill the
[engineer brief](#engineer-brief) from the work order and the binding. Go to step 6.

### 6. Engineer (PM, then `role:change:engineer`)

1. **Dispatch.** Check that no `{…}` and no `[…]` field is left in the brief. Save the filled brief in
   the record folder it names, as `brief.engineer.first.md`; it is committed with the round's other
   records (the PM skill, "The records the PM keeps"). State the token
   estimate and wait for the owner's go. Dispatch it with `subagent_type: "role:change:engineer"`,
   on the branch. The engineer writes the prediction file and commits it by its path before its
   first code edit, then stops and reports. Close refuses a code commit of the work order that no
   round's prediction commit comes before (by commit time; bakehouse `docs/PREDICT-GATE.md`). The role hook sends it back once if the record folder its brief names holds no
   prediction file that parses and either names a move or declares `"noMoves": true`. A `noMoves`
   prediction passes this stop while its round has no measured file yet, and beside its measured pair
   only when that pair shows 0 moved keys. A dispatch by the agent's plain name is checked
   only when the brief keeps its `Work order {id}:` line; otherwise the hook warns. Either way,
   step 9's close is the hard gate.

   **Seals (bakehouse `docs/PREDICT-GATE.md`, "Sealing").** Whoever commits a prediction or a
   baseline seals it at that commit: `predict-gate.sh seal {id} <round> <file>`, `<round>` being
   `first` or the round label. When the PM commits it in the main checkout, the project is found
   from the file's repository. The engineer seals each measured file the moment it writes it, with the measure
   report: `predict-gate.sh seal {id} <round> <measured file> --report <measure report>`. Seal the
   round's baseline before its measured file: a measured seal is refused while the baseline its check
   uses has no seal (a chained round's baseline that holds an earlier round's sealed measured file is
   already covered). With the binding's `mainFollows` set, the first round's baseline seals only when the main
   clone's `<branch>` is merged into this branch: engine changes merge into `<branch>` when each work order
   closes, and `<branch>` merges into a product branch only between work orders, before the next baseline. A measured seal with no report, or a report with no `head.sha`, is refused. A clone of
   the project runs `bakehouse setup --pointer <the main clone>` first; without it the seal refuses (or give
   `--repo <the main clone>`). Never set `OPS_TS` or `OPS_COMMIT` for a seal: it refuses them.
   The engineer commits each round's first code through `predict-gate.sh seal-code {id} <round> --
   <git commit args>`, which commits the staged code and seals that commit in one step; close compares
   that seal with the round's prediction seal. That commit is not amended or rebased afterwards; a later
   change goes in a new commit. The role hook also sends the engineer back once for a
   moves file whose bytes no seal names.
2. **The prediction check (PM → owner).** Compare the prediction with the work order:
   - every move in `expected` appears in the prediction;
   - every move in the prediction appears in `expected`;
   - no output in `mustNotMove` is predicted to move.

   Bring the prediction to the owner as one decision, before any site is switched, with each failed
   check named. A predicted move that `expected` does not name is a new decision, never a detail. On
   the owner's go, commit one line into the item on the branch: the prediction file and "approved by
   the owner, {date}". In unattended mode, a go the PM takes itself on a routine seal (the PM skill,
   "When the owner is away") is an assumed yes, and the line reads "assumed by the PM, decision <id>, to
   confirm", never "approved by the owner". Then tell the engineer to continue (SendMessage). A prediction changed after
   its measured file was committed needs that go recorded as a decision (`decision.sh ask`, then the
   owner's `decision.sh answer`) and cited in git: the commit that changes it ends with the trailer
   `Prediction-approved: <decision id>`, in the last paragraph of the message, never the subject.
   Ask it with `--task <id>` and `--approves <option>`, the one option that approves. Close accepts
   the decision only when its answer, recorded before the commit, chose that option and its task is
   this work order. A decision asked without `--approves` is read as before: answered `agreed: true`.
   An assumed yes, recorded by the PM while the owner is away, never approves: close refuses a decision
   whose latest answer is one. Without it, close refuses. The same trailer approves a commit into `history/` after the first
   measurement, a top-level prediction or measured file removed after it, and a named baseline
   committed with or after its prediction.

   A prediction changed after its first seal is also sealed again, by the PM, at the commit that
   changes it: `predict-gate.sh revise {id} <round> <file> --decision <decision id>`, citing the
   same decision, asked with `--approves` and `--task {id}`. It refuses a decision that does not approve
   this work order's revise. It refuses a prediction that has not changed.
3. **Continue.** If the engineer is no longer running, dispatch a fresh `role:change:engineer` with
   the same brief, the approved prediction and "switch now". Save that brief first, as step 6.1 does, as
   `brief.engineer.first-continue.md` (in fix round N: `brief.engineer.fix<N>-continue.md`).
4. **An andon** from the engineer — an unpredicted move, a gate that cannot pass, a `mustNotMove`
   output that moved, a need the evidence does not show — goes to the owner as one decision. The job
   waits.
5. The engineer cannot start agents. Never ask it to start the reviewer.
6. Record each run (owned repos only), with `{id}` as the task:
   `${CLAUDE_PLUGIN_ROOT}/runtime/ops-event.sh agent-run - "engineer: <id>" <model> - - <tokens> <seconds> <outcome>`.

### 7. Review (PM, then `role:change:reviewer`)

Dispatch the reviewer from this session as soon as the engineer reports. A reviewer started inside
another agent dies when that agent hands back. Fill the [reviewer brief](#reviewer-brief). Save it
in the record folder it names, as `brief.reviewer.first.md`, as step 6.1 does. State the
token estimate and wait for the owner's go. Dispatch it with
`subagent_type: "role:change:reviewer"`. Record the run (owned repos only).

Before you treat any agent's work as lost, check its record folder for `findings.md`, its branch and
the ops ledger. An empty folder alone is not proof.

### 8. Fix round (PM, then an engineer)

Send the findings to the same engineer with SendMessage, if it is still running. Otherwise dispatch a
fresh `role:change:engineer` with the findings, the branch and the brief. Each fresh dispatch of fix
round N saves its filled brief first, as step 6.1 does, as `brief.engineer.fix<N>.md` or
`brief.reviewer.fix<N>.md`.

- Fix every ANDON, P0 and P1 finding before the pull request.
- Fix real P2 and P3 findings too. Skip only the cosmetic.
- Before the round, check its altitude (`skills/pm/SKILL.md`, "Check the altitude before a fix round"):
  a finding outside the owning layer gets a short architect check first, and a P2 or P3 finding with
  reach 0 in code the work order did not change goes into a new work order, not this round.
- A fix that moves an output the approved prediction did not name is a new prediction, and goes to
  the owner first (step 6.2). It is written as its own round (see "A round").
- Each fix round commits its prediction file before its first code edit, as step 6.1 does.
- A prediction or measurement the round supersedes goes to `history/` with `git mv`, in its own
  commit that cites the owner's decision (see "`history/`" and step 6.2). Never edit or delete it.
  The PM retires each moved file that was sealed, at that commit:
  `predict-gate.sh retire {id} <round> <history file> --decision <decision id>`.
- The fix round's prediction and baseline are sealed when they are committed, and each measured file
  when it is written, as in step 6.1.
- The engineer re-runs every command gate after the fixes.
- Each prose gate in the job's gate list is re-run on the fix commits by a reviewer: SendMessage to
  the same reviewer if it is still running, otherwise a fresh `role:change:reviewer` given only the
  fix diff. Its result goes into the first review's
  folder as findings-fix<N>.md and mutations-fix<N>.json for fix round N, the names
  runtime/record-contracts.json declares (fill the reviewer brief's Round line with them).
- The engineer updates its result entry in the item with the fix commits and the re-run gate
  results.
- Write `disposition.md` in the record folder: each finding, what was done, and the commit.
- The PM commits the record folder — the reviewer's files and `disposition.md` — on the branch, by
  path, with the trailer. The reviewer writes them; it never commits.

### 9. Gates and pull request (PM)

1. Every gate in the job's list has passed after the last commit. Each gate's own `when` applies.
   Each prose gate has a recorded result. Every `mustNotMove` output is byte-identical. The
   engineer's result entry in the item gives each gate's result and the commits.
   Then run, from the repo root:

   ```
   "${CLAUDE_PLUGIN_ROOT}/runtime/predict-gate.sh" close <id>
   ```

   Commit the whole record folder first: close reads it from git at `HEAD`, and an untracked or
   uncommitted file refuses. It finds the record folder through the binding and checks every
   top-level prediction file against its own round's measured file. It exits 0 only when every
   check passes and every top-level measured file belongs to a pair, and then writes one
   `work-order-closed` line to the ops ledger with each file's sha256 and each prediction's commits.
   A non-zero exit stops the job here: no pull request. Every refusal reason is listed in bakehouse
   `docs/PREDICT-GATE.md`, "Closing a work order". The pass is the command's exit 0, never a reading.
   The owner's exception only:
   `PREDICT_GATE_CLOSE_OVERRIDE="<reason> | covers: R-xxxxxx,..." "${CLAUDE_PLUGIN_ROOT}/runtime/predict-gate.sh" close <id>`,
   naming every refusal id the close lists (each refusal is listed as `R-xxxxxx: <text>`), which is
   recorded in the ops ledger. Close needs an owned repository: the ops ledger exists only there. In
   another party's repository close cannot record a pass, so a change job there stops at this step
   until the owner decides how such a job closes.
2. **A branch the job opened**: push it and open a pull request into `origin/<default>`, with the
   repo host's own tool. Its text opens with the `need` and `decided`, then the measured moves
   against the prediction. Then commit the pull request's link into the item, by path, with the
   trailer, and push the branch again. If no tool is available, give the owner the branch name and
   the pull request text.
3. **A branch the job did not open** (the `branch:` line named it): open no pull request. The
   branch's own pull request carries the change. Tell the owner the change is done on that branch,
   with its commits and gate results.
4. The owner merges. In another party's repo, that party merges, and may ask for changes first.
5. Tell the owner the result as a win: what the tool now does, measured.

### 10. Close (PM)

After the pull request that carries the job's branch **has merged**:

- **Another party's repo**: write the merged pull request's link into the approval's `consumed`
  field in `approvals.json`, and commit that one file by its path. A consumed approval never starts
  a job again.
- **The owner's repo**: the engineer's result entry marks the item done. Check that it is there on
  `origin/<default>`. Run `predict-gate.sh close <id> --merged` on the merged default branch. It
  checks one thing: the record's files at the default branch's head hash to the sha256 values in
  step 9's `work-order-closed` line. It does not re-run the history, trailer or named-baseline checks,
  so a squash merge closes too. With no step-9 line, it says so and refuses. It writes a `note` line,
  not a second close. The work order is not done until it exits 0.
- Commit the ops ledger when it changed, by its path, with `git -C {records}`.

## The predicted-moves gate

Before any change, run BASE against BASE and prove it identical. A gate that differs from itself is
not a gate.

1. Write the prediction before the first edit, using both methods:
   - a **shadow run**: the old rule and the new rule side by side over the repo's real inputs, the old
     result returned, every disagreement recorded, the calls per site counted, and every site no
     input reaches listed;
   - a **static scan** of every stated value in the latest outputs and their data files, rows
     included, for the cases no input reaches.
   A shadow measured as a flat before/after pair can go through
   `"${CLAUDE_PLUGIN_ROOT}/runtime/shadow-predict.sh"`, which writes the prediction file from it and an
   expect file written first (bakehouse `docs/PREDICT-GATE.md`, "Writing the prediction from a shadow").
   The tool is optional: an engineer who derives the values by reasoning keeps doing so.
2. Each predicted move names the output, the case, the value before and the value after. Each
   `mustNotMove` output is listed as unchanged. A bounded count (a test count, for example) is
   written in the prediction file as a `range`, never as words under `to`: the check compares `to`
   for equality.
3. The prediction goes to the owner before any site is switched (step 6.2).
4. Then measure into `moves.measured.json` (the binding's measurement, flat key → value), with
   `moves.baseline.json` measured on BASE, and run `"${CLAUDE_PLUGIN_ROOT}/runtime/predict-gate.sh" check
   --measured <record>/moves.measured.json`; the prediction and baseline beside it are found from its
   round. A non-zero exit is an andon. The pass is the gate's exit 0, not the engineer's reading. The
   prediction file is `moves.prediction.json` (format: bakehouse `docs/PREDICT-GATE.md`); each
   `expected` and `mustNotMove` entry of the work order appears in it by key. A prose prediction may
   sit beside it; it is not the prediction.
5. An unpredicted move stops the job. It goes to the owner, not into a test fix. Never edit a test or
   an output to make a gate pass.

A round that moves nothing says so: its prediction declares `"noMoves": true` and lists no move. It is
accepted only beside its measured pair, and only when that pair shows 0 moved keys; a key that moves in
it is an unpredicted move. A predicted move that did not happen (`check` reports it in section 2 and
does not stop) needs a reason: one line per key in `moves.missed<R>.json` beside the round's measured
file (`moves.missed.json`, `moves.missed.fix1.json`), as `{"<key>": "<reason>"}`. Close refuses a
work order with a predicted move that did not happen and has no reason (bakehouse
`docs/PREDICT-GATE.md`, "A predicted move that did not happen").

Where a gate's `pass` text allows a "predicted", "intended" or "named" move, it means a move in the
approved prediction file, and nothing else.

## Rhythm with the owner

- One decision at a time, each with a concrete example: one real input, the output today, the output
  after.
- State spend in tokens before each dispatch, and wait for the owner's go. If the owner tracks usage,
  ask for a reading at each stop point. A usage reading never goes into the repo: not into a branch,
  a commit, a review record or the tracker.
- Recommend the proper fix. Never offer a stopgap as an equal option.
- Open every report to the owner with two lines: what the change reaches, in scope against out of
  scope, as counts; and what has moved in the owner's live outputs since this chain of work began.
  "Nothing has moved" is a warning, not a reassurance.
- Step back before the next dispatch — use the `step-back` skill, whose helicopter view is the page
  for the owner (the goal, the scope count, what has moved in the live outputs, the spend) — when
  either happens: a second review finds a P1 whose
  cause lies outside the diff under review; or a second architect run is asked for in the same
  chain. The cause is then upstream of the step being built: look at what feeds it first.
- Say the result as a win: what now holds, measured, not what changed.

## Lessons

- The reviewer must **run** the risky case, not only read it. A widened rule can pass every test and
  still move an output that only a run shows.
- The reviewer re-runs the work order's `evidence` on the new code. A change that passes its gates
  and leaves the evidence unchanged has not met the need.
- The reviewer writes its record early and keeps within its turn budget. A reviewer that hits its cap
  keeps only what is already on disk.
- Dispatch the reviewer from this session. A reviewer started inside the engineer dies when the
  engineer hands back.
- If the Write tool is refused, write the file with a shell heredoc.
- Never declare an agent's work lost without checking its output and the ops ledger.
- A latent change that no real input reaches is still a change. Name it, run it on a constructed case,
  and pin it with a test.
- A defect found mid-job that the work order does not name gets its own tracker item. It is not fixed
  inside this job.

## Brief templates

Fill every field. A `{…}` field is a placeholder from the list above. A `[…]` field names its source:
`work order: <field>`, `binding.<field>`, `tracker`, `git`, `preflight`, the engineer's report, or
`PM` (your own estimate or choice, stated to the owner first). Leave every `<…>` that sits inside a
repo-held command for the agent. A filled brief has no `{…}` and no `[…]` left.

Keep each brief short: the agent's own definition already holds the method. Briefs reach committed
review records, so a brief never carries a person's name, a usage reading or a working-style note.

### Engineer brief

```text
Repo: the working directory's repository; every path below is relative to its root.
Branch: [tracker: the item's branch: line] from BASE [tracker: BASE]. Work on it. Do not push or
open a pull request.
Work order {id}: [work order: need]
Decided: [work order: decided].
Evidence: [work order: evidence]. Reproduce it on BASE first.
Owning layer: [work order: layer]. Fix it there; a fix in another layer is an andon.
Least change that is still complete: in that layer, stop at the first of these that fully meets the
  need — the need is already met (build nothing, report it); code this repo already has there; the
  standard library or the platform; a dependency already installed; one plain line; otherwise the
  least code that works. Never leave out a check at a trust boundary, error handling that keeps data
  from being lost, anything security depends on, or anything the work order asks for. Finish what
  the change reaches: callers, tests, fixtures.
A shortcut taken on purpose (right for today's inputs, with a known limit) gets a comment at its
  line: shortcut: <the limit>, <when to upgrade>. Never without the upgrade condition.
Expected moves: [work order: expected].
Must not move: [work order: mustNotMove].
Serves: [tracker: the item's serves: line]. The FIRST line of your prediction and of your result is
a count of what the change touches or is judged on, in scope against out of scope; the result also
says what moved in a live output, as a number ("nothing" is said plainly).
Record folder: [tree mode: {root}/ + binding.reviews + binding.reviewName; records mode: {records}/runs/ + binding.reviewName; filled: a full path, in ~/ form; this line holds the path only, in backticks if it has a space; notes go on the next line]
Record commits: [tree mode: "commit record files with git, by path"; records mode: "commit record files with `bakehouse record commit <the record folder> -m <message>`, a trailer in its own -m"]
Gate (predicted moves): every gate in [binding.gates.predictedMoves.gates, resolved: each gate's id,
command, pass and when]. The gates that prove the need: [work order: gates].
  Before your first edit, write the prediction (shadow run with calls per site and the unreached
  sites; static scan of every stated value, data rows included) to the record folder as
  [the file name in binding.gates.predictedMoves.prediction]: every expected move with its case and its value before
  and after, and every must-not-move output as unchanged.
  A shadow measured as a flat before/after pair can go through `${CLAUDE_PLUGIN_ROOT}/runtime/shadow-predict.sh`,
  which writes the prediction from it and an expect file you write first. The tool is optional: values
  you derive by reasoning stay hand-written. A round that moves nothing writes
  "noMoves": true and lists no move; it passes only when its measured pair shows 0 moved keys. Commit it by its path, its subject starting
  with the work-order id, before your first code edit; a fix round's prediction too, before that
  round's first code edit. Then STOP and report it. Switch no site
  until the PM says go. Afterwards read it back exactly; stop on any unpredicted move. Never edit a
  test or an output to make a gate pass.
  The record folder holds, before close: the prediction, moves.baseline.json, moves.measured.json
  (one of each per round) and result.md. For each predicted move that did not happen (section 2 of
  `predict-gate.sh check`), write one line of reason in moves.missed.json beside the measured file
  (moves.missed.fix<N>.json for fix round N), as {"<key>": "<reason>"}: close refuses a missed move
  with no reason. /bakehouse:health flags the record folder when it lacks one or
  holds one empty: the folder named above, and any other folder of the work order that holds a
  prediction or a result.md (a design folder's flags clear when the work order closes cleanly).
  Every commit subject, a fix round's included, starts with the work-order id: a commit that names
  no id belongs to no work order.
  Mutation testing: run `${CLAUDE_PLUGIN_ROOT}/runtime/mutate.sh` with `--by engineer`; it writes
  engineer-mutations.json (engineer-mutations-fix<N>.json for fix round N) in the record folder.
Run BASE against BASE first. Scratch dir: [PM: a folder under the system temp directory, outside
the repo, named by the work order id, never by the user's name].
  Drivers (the scripts you write to measure, compare or check the change): in drivers/, directly
  inside the record folder above. The driver signal (runtime/driver-signal.py) reads driver scripts
  only from a drivers* folder directly inside a run's record folder, so it can count the same script
  written again in run after run.
  Runs that must not run in parallel: [binding.gates.oneAtATime, or "none named"].
  A clone of this repository (a lane clone, or a clone made for a run): before any bakehouse tool in
  it, run `bakehouse setup --pointer [PM: the main clone's full path]`. Without it every team tool
  there refuses, and heavy-run's lane events are lost.
  A lane's commands carry `BAKEHOUSE_LANE=[PM: the lane]` and `BAKEHOUSE_WO={id}`: heavy-run.sh takes that
  lane's lock and lane-event.sh labels its events with them. The older lane variable names are not read.
Leave alone: [binding.leaveAlone, or "none named"].
Tracker (only when the binding says owned is true): in your last commit, write your result entry in
the item {id} in [binding.tracker.file]: the record folder, the prediction file, each gate's result,
the moves measured against the prediction, the commits, found-not-fixed, and the done-mark.
Commits: one job each; end each with this trailer: [PM: the session's own Co-Authored-By line].
Report: result line, what moved against the prediction, each gate's result, tests, commits,
found-not-fixed. Andon: stop and report. Budget: [PM: tokens].
```

### Reviewer brief

```text
Repo: the working directory's repository; every path below is relative to its root.
Diff: [tracker: BASE]..[git: the branch head] on [tracker: the item's branch: line]. Do not edit
code. Do not commit.
Work order {id}: [work order: need]. Evidence: [work order: evidence]. Owning layer: [work order:
layer]. Expected moves: [work order: expected]. Must not move: [work order: mustNotMove].
Engineer's report: [the engineer's report, pasted, or tracker: the engineer's result entry].
Claim: exactly the moves in the approved prediction, [the Record folder below, filled,
+ binding.gates.predictedMoves.prediction]; every
must-not-move output byte-identical; the evidence no longer shows the failure.
Gate commands: [as given to the engineer, resolved from the binding].
Prose gates to run and record: [preflight: check 4 ids, read from the binding's gate list, or "none"].
Known suspects: [the engineer's report: its found-not-fixed list].
Serves: [tracker: the item's serves: line]. Open findings.md with your own count of what the change
touches, in scope against out of scope, and say whether the prediction's first line is true.
Give each finding its "Reach:" and "Layer:" lines, the layer judged against the owning layer above.
RUN the risky case and the work order's evidence on real inputs in a scratch dir (a constructed case
is fine); say "read, not run" where you could not run it.
Drivers (the scripts you write to run it, measure or attack the change): in drivers/, directly inside
the record folder below, not in a subfolder of it. The driver signal (runtime/driver-signal.py) reads
driver scripts only from a drivers* folder directly inside a run's record folder, so it can count the
same script written again in run after run.
Record folder: [tree mode: {root}/ + binding.reviews + binding.reviewName; records mode: {records}/runs/ + binding.reviewName; filled: a full path, in ~/ form; this line holds the path only, in backticks if it has a space; notes go on the next line]
Write contract.md first, then each pass file as you finish it, then findings.md, into
that record folder. Round: [PM: "the first review: findings.md and
mutations.json" | "the re-check of fix round <N>: findings-fix<N>.md and mutations-fix<N>.json,
beside the first review's files", N filled]. These are the only names a findings file may have
(runtime/record-contracts.json, rounds); the stop hook refuses any other. Record every mutation you
perform in the round's mutations file: {id, mutation, site,
gate, verdict, disposition}, a survivor's disposition `open: <finding id>` until someone decides,
`test-added <test file>` or `accepted: <reason>` with its reach as a count. If Write is refused, use a
shell heredoc.
Turn budget: [PM: turns] of your cap. Leave 15 turns to write findings.md.
```

## Guardrails

- **The owner decides.** The job never starts without the owner's go on the work order, never
  switches a site before the owner's go on the prediction (the one exception: in unattended mode, the
  PM's own go on a routine seal, recorded as an assumed yes, as the PM skill's "When the owner is away"
  section allows), and never runs a move the work order and the approved prediction do not name.
- **One work order, one branch.** The branch is the item's `branch:` line; the job never opens a
  second one and never copies a work order between branches.
- **Behaviour changes only as predicted.** Any output move not predicted stops the job.
- **The reviewer did not do the work.** The PM dispatches it; the engineer never does; the session
  that built the change never reviews it.
- **Commit by path.** In the records folder, add only the files this job changed, each by its path,
  with `git -C {records}`. Never `git add -A`: other sessions may have writes in flight.
- **Plain report.** Counts, not adjectives. Say what holds, what is next, and why.

If this repo has no team (no binding), or the locator says setup is needed, say so plainly rather than
inventing content.
