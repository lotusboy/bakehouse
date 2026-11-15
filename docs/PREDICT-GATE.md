# Predicted-moves gate

> Script paths on this page, `runtime/<script>`, are in the plugin's folder (`${CLAUDE_PLUGIN_ROOT}`
> in a command or agent body).

**The problem.** A stop rule written in prose is enforced only when an agent chooses to stop. On
2026-10-10 an engineer's change moved a gap count from 9 to 11. No move had been predicted. The
engineer judged the move fine and committed. Nothing compared the prediction with the measurement.

**What the gate does.** It compares a structured prediction with two flat measurements, taken
before and after the change. It exits non-zero on any move nobody predicted. It is generic: it knows
no repo. Each key is a name the repo's own measurement scripts emit.

**The files**, kept together in one run folder. The fourth, the missed file, is needed only when a
predicted move did not happen:

| File | Written | Holds |
|---|---|---|
| `moves.prediction.json` | before the first edit | the predicted moves |
| `moves.baseline.json` | measured before the change | flat `{key: value}` |
| `moves.measured.json` | measured after the change | flat `{key: value}` |
| `moves.missed.json` | after the measurement, when a predicted move did not happen | `{key: one-line reason}` |

The prediction file is JSON. A `.yaml` file works too, when PyYAML is installed. Example:

```json
{
  "about": "T12: the example rule",
  "moves": [
    {"what": "gaps:unfaithful-logic", "from": 9, "to": 11, "scope": "product-a"},
    {"what": "rules:fired:product-a", "delta": 2},
    {"what": "gaps:capture", "range": [360, 375]},
    {"what": "definition:file-hash:product-a"}
  ],
  "mustNotMove": ["premium:*"],
  "tolerance": 0.005
}
```

Rules for the prediction:

1. A move gives exactly one of: `to` (with `from` optional), `delta`, `range` [low, high] for the
   value after, or nothing. Nothing means "moves, any value". Use it for a hash.
2. `what` and `mustNotMove` entries may use `*` (any text) and `?` (one character). Every other
   character is literal, `[` and `]` included, so a key such as `verdict:== [true]` is written as it
   is. The fnmatch escapes `[[]` and `[]]` that older predictions used still read as `[` and `]`.
3. A key a move names is judged by that move, even when a `mustNotMove` glob also matches it. So
   `"mustNotMove": ["premium:*"]` beside one predicted premium move means "no other premium moves".
4. `scope` is a label for the reader. It does not change matching.
5. `tolerance` is an absolute numeric tolerance. The default is 0.
6. `baseline` (optional) names the file the prediction is measured against, as a plain file name
   beside it. A chained fix round uses it: `"baseline": "moves.measured.built-v5.json"`.
7. `noMoves` (optional, `true` or `false`) declares a round that moves nothing:
   `{"noMoves": true, "mustNotMove": ["out:*"]}`. A prediction with `"noMoves": true` that also lists
   a move is bad input (exit 2). It is accepted only beside its measured pair, and only when that pair
   shows 0 moved keys. A key that moves in a `noMoves` round is an unpredicted move, and `check`
   refuses it (section 4). Close, records-check and the stop check accept it by one rule
   (`no_move_refusal` in `runtime/predict-gate.py`). The stop check adds one case: it also accepts a
   `noMoves` prediction while its round has no measured file yet, because the engineer stops after
   writing the prediction and before measuring.

**Run it.**

```bash
runtime/predict-gate.sh check --measured moves.measured.json
# --prediction and --baseline default to the files beside the measured file, by its round
```

**Rounds.** A fix round writes its own files beside the first round's: `moves.prediction<R>.json`,
`moves.measured<R>.json`, when it has its own, `moves.baseline<R>.json`, and, when it needs one,
`moves.missed<R>.json`. `<R>` is `.` or `-` and
a label, such as `.fix3` or `-fix2`. `check --measured moves.measured.fix3.json` finds
`moves.prediction.fix3.json` by itself. The round's baseline is found in this order:

1. `--baseline`, when given.
2. The prediction's `baseline` field.
3. `moves.baseline<R>.json`.
4. `moves.baseline.json`.

**A chained round.** A fix round measured against an earlier measurement (the build after round
v5, say) names that file in its prediction: `"baseline": "moves.measured.built-v5.json"`. Nothing
guesses it from the file names. Without the field, the round is measured against
`moves.baseline.json`, which is the wrong "before" for a chained round.

It reports six sections:

1. Predicted, and it happened.
2. Predicted, and it did not happen. This is reported. It does not stop the commit. Close refuses
   until each one has a reason (see "A predicted move that did not happen", below).
3. Predicted to move, and it moved to a different value. This stops the commit.
4. Moved, and nobody predicted it. This stops the commit.
5. A `mustNotMove` key moved. This stops the commit.
6. A `mustNotMove` entry matched no measured key, so it is unproven. This stops the commit.

A key that appears or disappears counts as a move. Exit codes: 0 pass, 1 refused, 2 bad input.

**The owner's exception.** `--override "<reason>" [--task <id>]` turns a refusal into a pass. The
gate writes one `gate-refused` line, outcome `done`, to the repository's ops ledger through
`ops-event.sh`. The line carries the reason and the refused keys. If the line cannot be written, the
override is refused. An override is never silent. An override on a passing check records nothing.

**Closing a work order.** `predict-gate.sh close <work-order> [--repo DIR] [--binding FILE]`, run
from the repository, is the gate a team job's close step runs (`/bakehouse:change`, step 9). It is the
hard gate: the role hook's stop check (below) can miss a dispatch, close cannot be skipped.

What close reads:

1. It finds the work order's record folder(s) through the project's binding: `reviews` +
   `reviewName`. `{date}` must be a date, `YYYY-MM-DD`; `{n}` is `1`. The id must match `tracker.id`
   whole (a full match, not a search).
2. It reads the record from git at `HEAD`, not from the disk. A record folder that holds an
   untracked or uncommitted file refuses. Commit the record first.
3. The record is every file at the folder's top level. The `history/` subfolder is not part of the
   record (see below). A `moves.measured*` or `moves.prediction*` file in any other subfolder refuses
   on purpose: without it, an unpredicted measurement escapes the record by sitting in `round2/`.

When close refuses (exit 1). Each reason names the file:

1. No folder matches, or a folder holds no `moves.prediction*.json`. A prose prediction is not
   checked.
2. A prediction has no measured file of its own round, or no baseline.
3. A top-level `moves.measured*` file has no prediction of its own round, and no prediction names it
   as its `baseline`.
4. A check does not exit 0.
5. A prediction that names no move. A change predicts what it moves. The exception is a prediction
   that declares `"noMoves": true` (rule 7 of the prediction) and whose measured pair moved 0 keys.
6. A pair in which 0 keys moved while the prediction names moves. The measured file is stale.
7. One of the four exceptions below without the owner's approval (see "The owner's approval"):
   - a commit after the measured file's first commit that changes the prediction;
   - a commit that touches `history/` and is not before the commit of the record's first top-level
     measured file (the same commit counts as not before). The first measurement is read over the
     folder's whole history, so a round moved to `history/` or removed still counts;
   - a top-level prediction or measured file that is absent at `HEAD`, removed (`git rm`, or a rename)
     by a commit that is not before the first measurement and is not judged as a `history/` commit.
     A file moved out and back is not absent, so it needs nothing;
   - a file a prediction names as its `baseline` whose first commit is not before the prediction's
     first commit (the same commit counts as not before).
8. The folder, or any file in it, is a symbolic link.
9. A prediction or measured file whose sha256 an earlier close of ANOTHER work order recorded. Close
   may run again for the same work order.
10. The code-order rules, when the binding names `signal.codeRoots`. A code
    commit of the work order is a non-merge commit that touches a code root and whose subject's first
    tracker id is the work order. Close refuses one that comes after the latest top-level measured
    file's commit, and one that no round covers: a round covers a code commit that comes after its
    prediction's first commit and no later than its measured file's last commit. So each round,
    `/bakehouse:change` step 6.1 and every fix round, commits its prediction before its first code edit.
    Rounds moved to `history/` count. The order is commit time, not ancestry: code made on a lane branch
    merges after its record is committed on the run branch.
11. An uncommitted or untracked change under the binding's `signal.codeRoots` (`git status`, untracked
    files included). No record measured it. This applies to every close whose binding names code
    roots, with no cut-off: a dirty tree is wrong for any record.
12. A predicted move that did not happen and has no reason in the round's missed file (below).

**A predicted move that did not happen.** The owner ruled on 2026-11-15 (rule Q3, option C). `check`
reports such a move in section 2 and does not stop. Close refuses until every one, in every round, has
a reason.

1. The reasons sit in `moves.missed<R>.json`, beside the round's measured file. `<R>` is the round's,
   as for the other moves files: `moves.missed.json`, `moves.missed.fix1.json`.
2. It is one JSON object: `{"<key>": "<reason>"}`. Each key is a key from section 2 of the round's
   check. Each reason is one line, written by the engineer.
3. A predicted new key that never appears is a move that did not happen. Its key is the move's `what`.
4. Close reads the file from git at `HEAD`, like the other record files.
5. Close refuses, naming the file and the key, for each of: the file is missing; a key from section 2
   has no reason; a reason is empty, not text, or more than one line; the file names a key that was not
   missed in that round. It refuses a file that is not one JSON object, naming the file.
6. Example. The check reports `tests:passed: 5815 -> 5815. Predicted: after in [5815, 5815]. Not met:
   it did not move.` The reason might be `{"tests:passed": "held value, written as a move by mistake"}`.
7. The missed file is not sealed. Close's rule 3 for a sealed work order does not ask for its seal.
8. When the round has a missed file, close's `work-order-closed` line carries its path and sha256. So
   `close --merged` checks it like the other record files, and a reason changed after close refuses.

On a pass it writes one `work-order-closed` line to the ops ledger. The line carries, for each pair:
the prediction's sha256 and its commits, the measured file's sha256, the baseline's name and sha256,
the missed file's name and sha256 when the round has one, and each approved exception with its commit and decision id. It also lists the `history/` files and
names the ops ledger file; the output also names the decision ledger. A close that
cannot be recorded is refused. Exit 2 is bad input: an id that does not match, no commit, or a
repository with no team (below).

**Close needs an owned repository.** The ops ledger is kept only for a repository whose binding says
`"owned": true` (`ops-event.sh` refuses any other). In another party's repository close cannot record
a pass, so it cannot close. That party's own review closes the work there.

**Which ledger.** Close writes to the ops ledger of the project that `runtime/locator.py` finds for
the repository (`--repo`, else the working directory's): `ops/events.jsonl` in its records folder. A
repository with no team is refused (exit 2). The output and the event name the ledger file.

**Step 10: `close <work-order> --merged`.** After the merge, `/bakehouse:change` step 10 runs close
with `--merged` on the default branch. It checks one thing: each file the work order's latest
`work-order-closed` event hashed hashes the same at `HEAD`. It does not run the history, trailer,
named-baseline or code-order checks again, so a squash merge (which folds every trailer into one
commit) closes. It refuses, saying why, when there is no such event, when that event hashes no
file, and when a file differs or is missing. It has no override: the full close's override is the
owner's exception. A pass writes a `note` event, never a second `work-order-closed` event. Close also unsets `OPS_TS` and `OPS_COMMIT`, the backfill variables: a close is stamped
with its own time and the repository's own commit.

**The owner's exception:** `PREDICT_GATE_CLOSE_OVERRIDE="<reason>" predict-gate.sh close
<work-order>`. It writes a `gate-refused` line first, then the `work-order-closed` line marked as
overridden. Close reads only this variable. The hook's `PREDICT_GATE_OVERRIDE` never overrides a
close, so a value exported for a commit cannot close a work order.

**`history/`: what is not part of the closed record.** A record folder may hold files that close must
not check: a prototype measurement, an older measurement, a superseded or revised prediction. Put each
one in the folder's `history/` subfolder. Close lists every file there, in its output and its event,
and checks none of them. The commit hook skips `history/` too. A file that goes into `history/`
before the record's first measurement is committed needs nothing. Any commit that touches `history/`
from that commit on (an add, a `git mv`, an edit) needs the owner's approval on that commit, because
a measurement parked there escapes the check. To move a superseded file, run from the repository:

```bash
git mv <record>/moves.prediction.v2b.json <record>/history/
git mv <record>/moves.measured.proto.json <record>/history/
git commit -m "<id>: superseded prediction and prototype measurement to history/" \
  -m "Prediction-approved: <decision id>" -- <record>
```

A file in `history/` is still committed. Moving it is a commit, so the move shows in the history.

**The owner's approval.** Each exception above is approved by the owner's decision, recorded in the
stream's decision ledger, and cited on the commit that made the exception:

1. Ask the owner through `decision.sh ask`, with `--task <work-order>` and `--approves <option>`: the
   one option that approves the exception, one of the `--option` values (`--option approve --option
   refuse --approves approve`, for example). The owner's answer is recorded with `decision.sh answer`.
   Agreeing with the recommendation is not approval: an owner who refuses agrees whenever the
   recommendation was to refuse.
2. The commit ends with a git trailer `Prediction-approved: <decision id>`, for example
   `Prediction-approved: d20260101-a1b2c3`. A git trailer is a `Key: value` line in the last
   paragraph of the commit message, as `git interpret-trailers` reads it; git matches the key
   without regard to case. The subject line is never a trailer. A quoted line (`> Prediction-approved:`)
   is not one either.
3. The value is the decision id alone: `d`, 8 digits, `-`, 6 hex digits. Anything else refuses.
4. Close reads the project's decision ledger, `decisions/ledger.jsonl` in its records folder, through
   `decision.py`'s own reader. The decision must have an `answer` recorded before the commit's
   committer time. The latest such answer must choose the approving option (compared as `decision.py`
   compares a choice: case and spacing aside), and the ask's `task` must be the work order being
   closed. An id that is not in the ledger, another choice, another work order's decision, or an
   answer recorded after the commit refuses. Named limit: a decision asked without
   `--approves` (every one asked before it existed) keeps the older rule: its latest answer says
   `agreed: true`, and its task is not checked. Git dates are
   written by the committer: the time order catches a decision cited before it was answered, not a
   forged date.
5. A commit may carry more than one such trailer; every one must pass.
6. Close's event names each decision id it relied on. The trailer records the owner's go; it is not a
   way round it. A commit made without the trailer cannot gain one later without rewriting history,
   so cite the decision when you commit.

**Limits of close, named.**

1. The code-order rules and records-check's stale rule judge only a work order with a round whose
   prediction is first committed on or after `ORDER_FROM` in `runtime/predict-gate.py` (2026-11-04).
   An older record keeps its result: code after its measurement, or before
   its prediction, is not refused.
2. Their order is commit time, which the committer sets. A
   forward-dated commit can pass. Ancestry cannot order a lane branch's code against a record
   committed on the run branch.
3. A round label used again after a supersede (a new `moves.prediction.json` after the old one went
   to `history/`) reads as one round, from its first prediction to its last measurement.
4. A binding with no `signal.codeRoots` gets no code-order check and no dirty-tree check; close says
   so in a note.

**Sealing: the evidence close will judge.** Each prediction, baseline and
measured file is sealed in the project's ops ledger at the moment it is written. A seal is a ledger
event the tool writes, not a git fact the judged party can rewrite. Close judges a sealed work order
by these events ("Closing a sealed work order", below).

1. `predict-gate.sh seal <work-order> <round> <file> [--repo DIR] [--report R]` writes one `sealed`
   event: the work order, the round, the file's kind (`prediction`, `baseline` or `measured`, from its
   name), its path in its repository, its sha256, the tool's own time, the repository's `HEAD`, and
   `prev` (below). `<round>` is `first` for the first round's files and the round label otherwise
   (`fix1` for `moves.prediction.fix1.json` or `moves.prediction-fix1.json`). A file whose name is not
   that round's is refused. When the project's binding names a `tracker.id` form, the work order of a
   record in the bound repository must match it, as close requires.
2. A measured seal also runs `check` against the round's prediction and baseline, found as `check`
   finds them, and carries the result (`pass` or `refused`, the failure count, and the sha256 of the
   prediction and baseline it used). It carries the `base` and `head` commits the measurement read,
   from the measure report's `base.sha` and `head.sha`: `--report`, else `measure-report.json` (first
   round) or `measure-<label>-report.json` beside the file. With no report they are `null`, and the
   event says `"report": null`. A report without both commits is refused. A refused check is still
   sealed, and the command exits 1: close will need an approved retire for it.
3. A prediction or baseline is sealed once per round. Sealing the same bytes again writes another
   seal. Sealing changed bytes is refused: a changed prediction goes through `revise`, and a changed
   baseline is a new round. A round's first prediction or baseline seal is refused once the round has
   a sealed measured file. A seal later retired does not count.
4. The seal refuses `OPS_TS` and `OPS_COMMIT` (exit 2): a seal is never backfilled. Its refusal event
   is stamped now, too.
5. **The project.** The project `runtime/locator.py` finds for `--repo`, else for the file's
   repository. A lane clone with no records pointer needs `--repo <the main clone>`; without it, the
   seal refuses (exit 2, naming `bakehouse setup`). The project's binding must say `"owned": true`.
   The ledger is `ops/events.jsonl` in the project's records folder, the one close reads. A
   repository with no team is refused (exit 2), as close refuses it.
6. `predict-gate.sh revise <work-order> <round> <file> --decision <id>` follows a prediction changed
   after its first seal. It needs an earlier seal or revise of that work order's prediction in that
   round, with a different sha256, and writes a `revised` event.
7. `predict-gate.sh retire <work-order> <round> <file> --decision <id>` follows a sealed file's move to
   `history/`. The file must be `<the sealed file's folder>/history/<its name>`, hold the bytes it was
   sealed with, and the sealed path must be gone. The event takes that seal's kind and round.
   **Which seal a retire retires.** One function, `retire_of`, decides it for close, records-check and
   the sealer. A retire applies to the live seals of its bytes at that path that come before it in the
   ledger. A seal of the same bytes after the retire is a new, live seal, not a retired one.
   **A re-measure after a retire.** To measure a round again after its measured file was retired, write
   the new measured file at the same path, and seal it: `predict-gate.sh seal <work-order> <round>
   <file> --report <measure report>`. That seal starts a new chain for the path: its first seal stands,
   as a file's first seal does. The retire's decision is the owner's go for it. No other command is
   needed. The same holds for a retired prediction or baseline sealed again in its round.
8. For both, the decision must be asked with `--approves` and `--task <work-order>`, answered before
   the call, and its latest answer must choose the approving option. A decision asked without
   `--approves` never approves a revise or retire: close's older rule for such decisions (named limit
   D1) is not used here. On 2026-11-04, 0 of the 737 decisions in the adopting project's ledger carried
   `approves`, so every revise or retire needs a new decision. It is recorded in the event as
   `decision`.
9. **The chain and the lock.** Every ledger line is appended through `runtime/ops_ledger.py`, which
   `ops-event.sh` and the sealer both use, under an exclusive lock on a lock file beside the ledger,
   `.events.jsonl.lock`, which the records worktree's `.gitignore` names (`*.jsonl.lock`). The lock is not taken
   on the ledger, because the ledger is tracked in git and a checkout, merge or reset writes a new file
   under a lock held on the old one; git never touches an ignored file. No part of the lock's path comes
   from the environment, and it sits beside the resolved ledger, so every writer of one ledger takes one
   lock, through a symlink to the ledger file or to a folder above it.
   `decision.py` locks its own ledger the same way (`.ledger.jsonl.lock`). The ledger is read as bytes,
   so a line that is not UTF-8 never stops a write. When the last line has no newline, the newline
   is written first, so a new line is never joined onto it. The chain starts at the first sealed
   event. From it on, every new event, from any writer, carries `prev`: the sha256 of the bytes of the
   line before it. Events before it stay as they are. `ops-event.sh` refuses the kinds `sealed`,
   `revised` and `retired`.
   **The anchor.** After seal, revise or retire appends its line, still under
   the lock, it commits the ledger file alone, by path, into the git repository that holds it (the records worktree),
   with the trailer `Sealed-line: <sha256 of the line>`. git's index lock, held by another git command,
   is retried 5 times; then the command refuses loudly (exit 1, gate `predict-gate:anchor`): the line is
   written but not in a commit, and close refuses the work order until a new seal anchors: seal again
   once the cause is gone (committing the ledger by hand also counts, but sealing again is the route that
   leaves its trailer). So a seal made
   while a commit or a merge is open in the records worktree (an editor waiting on a commit message
   holds the index lock; a merge in progress refuses a commit by path) is refused: seal again after it.
10. `predict-gate.sh verify-chain [--repo DIR | --ledger FILE]` reports BROKEN, naming each line:
    - a line that is not UTF-8, or not a JSON object, anywhere;
    - a `sealed`, `revised` or `retired` event without `prev`, anywhere (the sealer chains every one);
    - a chain whose first line is not a `sealed` event;
    - from the chain's first line on, a line without `prev`, or whose `prev` is not the sha256 of the
      line before it.

    Otherwise it says the chain is not started, or whole. Exit 0 not started or whole, 1 broken, 2
    bad input. It only reads.

**Closing a sealed work order.** Whether a work order is
sealed is a fact in git, not in the ledger file, whichever writer put the line there: it is
sealed when the ledger at `HEAD` holds a sealed, revised or retired line of it, or when a
`Sealed-line:` trailer in the git history of the ledger names one (that history is read with
`--full-history --follow`, so a merge resolved to one side and a move of the ledger file do not hide
it). A trailer whose line the ledger at `HEAD` no longer holds refuses as removed. An event of it in
the ledger file that no commit holds yet also counts, and refuses until a new seal anchors. Deleting
lines never unseals. Close and records-check read this one answer. A sealed work order is judged by its events as
the ledger at `HEAD` holds them.

On EVERY close, sealed or not, before that decision, close refuses when verify-chain is BROKEN:
close did not write that line, so it names the line and says the owner decides its repair, through
a decision (`decision.sh ask --task <id> --approves <option>`), and judges nothing until verify-chain
is whole. It also refuses when the ledger file no longer begins with the ledger its last commit holds
(a committed line edited, moved or removed). For a sealed work order, close then refuses, naming each:

1. **The ledger it reads.** A sealed event of the work order that no commit holds. A line a
   `Sealed-line` trailer names that the ledger at `HEAD` no longer holds ("a sealed event was removed
   after its commit").
2. **Each sealed file.** The first seal of a path stands. A later different hash needs a `revised`
   event whose decision passes the rule of point 8 again, at the revise's time. The file at `HEAD`
   hashes as its latest seal or revise; or it is gone, and a `retired` event of its latest bytes at
   `<folder>/history/<name>` has an approving decision, and that copy is at `HEAD`.
   A seal that comes after the retire of the path's latest bytes starts a new chain (a re-measure, point
   7 of "Sealing"). Its first seal stands. The retire that ended the earlier chain still needs its
   approving decision, and its copy in `history/` must still be at `HEAD` with the retired bytes.
3. **Each moves file at `HEAD`** (top level of a record folder) has a committed seal.
4. **Each sealed measured file whose check REFUSED** is retired.
5. **A round's prediction is sealed before its code.** Its first seal's commit is an ancestor of the
   round's measured base; or, if not, every file under the code roots that the round's measurement
   changed (base to head) reads the same at the seal's commit as at the base.
5b. **A round's first code commit is sealed, after its prediction.** When a round's
   latest measurement changed code under the code roots (base to head), the round needs a code seal:
   the engineer commits the round's first code through `predict-gate.sh seal-code <work-order> <round>
   [--repo DIR] -- <git commit args>`, which runs `git commit` on the staged code (it does not read
   `-a`) and seals that commit as a `sealed` event with fileKind `code`, its parent and its code-root
   trees, anchored like every seal; a second code seal of the round is refused. Close refuses when the
   round has no code seal, when the round's prediction seal comes after its code seal in the
   ledger's order, and when the sealed commit is not the round's first code change (the code just before
   it differs from the measured base). The ledger's order is the sealer's own, so a prediction sealed
   after its lane code refuses even though the PM seals in the main checkout. The round's prediction
   seal is the first seal of its live chain, decided by `retire_of`: after a retire and a new seal,
   rule 5b judges the new seal, not the retired one.
6. **The code at `HEAD` is the code the latest measured seal read**, compared by code-root tree. A head
   on `HEAD`'s own first-parent line is compared with `HEAD`. A head a merge brought in is compared with
   that merge's lane parent; and a code commit on `HEAD`'s own line after that merge whose subject's first
   tracker id is this work order refuses, because nothing measured it. A head `HEAD` cannot
   reach (not merged, or amended away) is compared with
   `HEAD` and every merged lane parent, using the seal's `headTrees` when the commit itself is gone. A
   head this repository does not hold, with no trees, refuses and names the fetch
   (`git fetch <lane clone> <branch>`): close does not fetch.

The pairing, `check` on each pair, the dirty-tree checks and the evidence-reuse check run as for any
work order. The git-order rules are NOT run for a sealed work order: the code-order rules and
`ORDER_FROM`, the `history/` and removal walks, and `Prediction-approved` trailers. A work order with no
sealed event keeps every rule above this section, unchanged. records-check judges a sealed work order
stale when rule 6 refuses at its ref.

**Limits of sealing, named.** (L5, the range rule for code committed straight on the run branch: rule
6 compares that code with `HEAD`, so another work order's code committed straight on the run branch
after the measurement refuses the close.)

- L1. A private measurement. An engineer can run a measure script outside the sealer, see the
  result, and discard it. No record shows this. The final change is still checked against the
  sealed prediction.
- L2. Measurement content fabricated and sealed on purpose. Only re-measuring stops it.
- L3. Forged ledger lines. The ledger is a plain file, and an agent runs as the same OS user. The
  chain has no key. It catches an edit, deletion or move only when the editor does not recompute
  `prev` on every later line; a rewrite that recomputes them passes (L7). It does not catch a new line
  appended with a correct `prev`, an edit to the newest line, or lines removed from the end, until a
  commit holds them (L7). Lines before the chain's first line are judged for being readable and for
  holding no seal; the line just before it is also covered, because the first seal's `prev` hashes it.
- L4. The witness's own drift. The prediction seal is written by the PM, which is also an agent.
- L6. Rule 5b compares the round's prediction seal with the seal of its first code commit, so a
  prediction sealed after lane code that was committed through `seal-code` refuses. It does not see code
  written before its commit: code written before the prediction seal and committed through `seal-code`
  after it passes (a form of L1). Rule 5's tree test still runs beside it; for code committed straight
  on the run branch it can also refuse an honest record when another work order changed one of the
  round's files between the base and the prediction seal's commit (a false refusal, not a miss).
- L11 to L14 are what the code seal leaves open. Each needs an engineer to cheat on purpose, or (L12) an
  honest rewrite of the sealed commit. The gate is built to catch honest mistakes and to fail safe, not to
  defend against an agent cheating on purpose. An honest engineer
  avoids L12 by never amending or rebasing the sealed code commit: a later change goes in a new commit.
- L11 (named, not fixed). Rule 5b does not tie the sealed commit to the measured code.
  It checks that a code seal exists, that the prediction seal comes first, and that the code just before
  the sealed commit equals the measured base. It does not check that the sealed commit changes a code
  root, nor that it is an ancestor of the measured head. A throwaway commit sealed on a branch from the
  base, or `seal-code -- <path>` that commits only a non-code file, satisfies the rule.
- L12 (named, not fixed; avoided by the rule above). An honest amend or rebase of the sealed
  first code commit refuses, and no seal-side recovery exists. An amended commit that is not fetched, or
  is gone after gc, refuses as "not in this repository"; a lane rebased onto a moved main refuses as "not
  the round's first code change"; and seal-code refuses a second code seal of the round. Only the owner's
  close override remains.
- L13 (named, not fixed). The stop check's seal test matches a moves file by its bytes
  against any `sealed` or `revised` line of any work order in the working ledger. A measured file
  byte-identical to a sealed baseline (a 0-move round), or to another work order's sealed file, reads as
  sealed, and so does a seal whose anchor failed. Close is the backstop: its rule 3 wants a committed seal
  of that path.
- L14 (named, not fixed). After a failed anchor, seal-code's message says "seal again",
  but a second seal-code of the round is refused, and running it makes one more unsealed commit. The
  code seal's line is already in the ledger file: the next seal of any file, or a commit of the ledger,
  anchors it.
- L15 (named, not fixed). A re-measure after a retire works once per file name. A second retire of the
  same name with other bytes overwrites `history/<name>`, so close finds the first retired copy changed
  and refuses. A further re-measure goes in a new round.
- L8. Seals anchored on a branch of the records worktree that is never merged into the branch close
  runs on are not seen: close reads `HEAD`'s ledger and `HEAD`'s history. A merge of that branch, even
  one resolved to the other side, brings them back into view.
- L9. `--follow` relies on git's rename detection. A move of the ledger file that also rewrites most of
  it in the same commit is not followed, and the trailers from before it are not seen.
- L10. The anchor and the sealed state use git's own discovery from the ledger's folder upward.
  `runtime/locator.py` accepts a records folder only when it is the top of its own git checkout, so
  the ledger is never anchored into an enclosing repository.
- L7. A git history rewrite. Close compares the ledger with its last commit, and reads the git
  trailers of every anchored line, so an edit after a commit refuses, and so does a committed edit that
  removes an anchored line. A rewrite of the records branch's git history itself (amend, rebase,
  reset of an anchor commit) passes; the reflog shows it.

**Before every commit (opt-in).** `bakehouse setup --pre-commit` writes a pre-commit hook into the
repository's local `.git/hooks`, which git never commits. Plain `bakehouse setup` does not: the hook is
never installed automatically. The hook runs `predict-gate.sh hook` through the git-hook
launcher in the machine folder.

1. The hook acts only when a commit stages a `moves.measured*.json`. One under a `history/` folder is
   skipped only when the folder that holds that `history/` has a `moves.prediction*` of its own in
   the index (a record folder): `<record>/history/history/` is skipped, `<record>/sub/history/` is
   refused, as close does. A commit with only the prediction (the "before the first edit" commit)
   passes through. The hook cannot see the commit message, so the owner's approval of an exception
   is checked by close, not by the hook.
2. It checks each staged round. It reads all three files from the index, so it checks what is
   being committed, not the working tree.
3. It follows close's rule. A staged measurement with no prediction of its round is refused, unless a
   prediction beside it names it as its `baseline`. A measurement with no baseline is refused. A
   measurement that is not part of the record goes in `history/`.
4. The owner's exception: `PREDICT_GATE_OVERRIDE="<reason>" PREDICT_GATE_TASK=<id> git commit ...`.
   The override is recorded as above.
5. The installer leaves a pre-commit hook it did not write alone. It also skips a repository that
   sets `core.hooksPath`. In both cases it prints the one line to add to the existing hook.
6. To remove the gate, delete `.git/hooks/pre-commit`.

**The stop check (`runtime/agent-roles.py`).** It catches a missing prediction early; close is the
hard gate.

1. It marks a `role:change:engineer` dispatch. It also marks a dispatch by the change engineer's
   plain agent name, in a repository with a binding, when the brief has a `Work order <id>:` line.
   A plain-name dispatch with no such line cannot be told apart from another job: the hook warns, and
   close is the hard gate.
2. The marker `[bakehouse:prediction-record]` is the prompt's own final line.
3. It runs at SubagentStop only for the agent type that `change:engineer` resolves to. Any other
   agent is never blocked by it, and its own checks (such as `run_record`) still run.
   - Every dispatch of that agent type is logged to `logs/prediction-dispatch.jsonl` in the machine folder, with
     whether it is a change job and the sha256 of its prompt before and after the rewrite. A stop
     whose first prompt is logged follows the log: a refactor job (`role:refactor:engineer`) is not
     checked, even when its brief has a `Work order` line.
   - A stop whose prompt is not logged is checked when the prompt ends with the marker, or when it
     holds a `Work order <id>:` line and the agent is not a built-in one. A built-in agent (such as
     `general-purpose`) is marked only through a `role:` dispatch, so a reviewer that falls back to
     it is not checked.
4. It checks only the engineer's own record folder: the `Record folder:` line of its brief, else the
   binding's folder for the `Work order <id>:`. Another folder never satisfies it.
5. It sends the engineer back once unless that folder holds a `moves.prediction*.json` that `check`
   reads and that names at least one move. A prediction that declares `"noMoves": true` passes too:
   while its round has no measured file yet, and when its measured pair is beside it and moved 0 keys.
   A `noMoves` prediction whose measured pair moved keys is sent back.
5b. Then it sends the engineer back once for any top-level moves file (prediction, baseline
   or measured) whose bytes no `sealed` or `revised` event in its project's ops ledger names: the
   project of the bound repository. The reason names each unsealed file
   and the `predict-gate.sh seal` command. On the second stop it lets the engineer stop and tells the
   user. Gate `stop:seal`. Fail-open: an error in this check reads as sealed.
6. Every stop of that agent type writes a `PROMPT-SOURCE` line to `logs/agent-roles.log` in the machine folder. It
   says whether line 1 of the agent's transcript ends with the marker. On the first live marked
   dispatch, "ends" proves the transcript records the rewritten prompt. "does not end" means it
   records the prompt before the rewrite; the check then still works from the brief's
   `Work order <id>:` line.
