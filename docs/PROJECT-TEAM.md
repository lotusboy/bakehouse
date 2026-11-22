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
   `bakehouse-records`; a relative pointer is read from the repository root. It refuses a records folder
   that is a separate clone, or a worktree of a repository that is neither the project's own nor, for a lane
   clone, its main clone (judged as `bakehouse setup --pointer` judges it): records committed there would
   never reach the project. `bakehouse record commit` and a seal without `--repo` name a folder in a
   separate clone as that.
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
| `ops/events.jsonl` (in records format 2, frozen, and `ops/events/<writer>.jsonl`) | records | the ops ledger | `runtime/ops-event.sh` only |
| `FORMAT` | records | the records format (2) and the frozen legacy ledgers' sha256 ([One records file per writer](#one-records-file-per-writer)) | `bakehouse setup migrate` |
| the run folders (`runs/<record folder>/`), in records mode | records | each job's record: prediction, baseline, measured files, findings | the agents; committed with `bakehouse record commit` |

### Where the run folders live

A run folder (a record folder) holds one job's prediction, baseline, measured files and findings. The
binding's `runs` field says where the run folders live. `runtime/locator.py` `runs_location` gives the one
answer, and every reader of a record folder asks it: close, seal, records-check, the two signals and the
role hook.

1. **Records mode** (`"runs": "records"`, or a binding with no `runs` and no `reviews`). The run folders
   are `runs/<record folder>/` in the records worktree, on the `bakehouse-records` branch. They are never
   on a code branch, so they never ship in a project's release.
2. **Tree mode** (`"runs": "tree"`, or a binding with `reviews` and no `runs`). The run folders are
   `<reviews>/<record folder>/` in the code tree, as before this field existed. Preflight notes a
   binding that is in tree mode only because it names `reviews`: add `"runs": "tree"` to say so.
3. `"runs": "records"` beside a `reviews` field fails preflight. So does any other `runs` value. Close
   and the other readers refuse such a binding too.
4. In records mode, bakehouse's pre-commit check does not run in the records worktree. So a record file is
   committed with `bakehouse record commit <record folder> -m <message>`. It runs the pre-commit
   hook's own check on that folder, then commits that folder only (docs/TOOLS.md). A plain `git commit` in
   the records worktree is not checked at commit time: bakehouse's hook launcher skips the records branch.
   Close still checks the record.
5. Do not change the mode while a work order is open. Close reads the record only where the mode says.
   After a switch, close finds no record folder for that work order and refuses. A work order that
   is already closed stays closed: `bakehouse status` reads the ledger, not the record folder.
6. Nothing moves an existing record. The records of work orders closed in tree mode stay in the code
   tree.

### One records file per writer

**The problem.** Each ledger on the records branch was one file. Two people on two machines appended to
the same file. The second push was refused, because it was not a fast forward. A merge then conflicted,
or, with a line-union merge, reordered lines and broke the ops ledger's hash chain.

**The fix (records format 2, bakehouse 0.8.0).** Each machine appends only to its own files. Two
machines then never write the same file, so a plain `git pull` merges their records with no conflict.

1. **The writer id.** Each records worktree has one writer id, in its repository's own git config
   `bakehouse.writer` (`git config --local`; a global or system value is never used).
   - `bakehouse setup` in the main clone sets it when it is unset.
   - `bakehouse setup --writer <name>` sets `<name>`: 1 to 32 letters, digits or hyphens. Without
     `--writer`, setup sets `w` followed by 8 random hex characters.
   - Setup never changes an id that is set. A `--writer` that differs from it is refused.
   - A lane clone's records worktree is its main clone's records worktree. So every lane on one machine
     shares the main clone's writer id.
2. **The layout.** Each of the five ledgers keeps its legacy file and gains a folder of writer files.
   The folder is the legacy file's path without `.jsonl`.

   | Ledger | Legacy file (frozen) | Writer files |
   |---|---|---|
   | ops ledger | `ops/events.jsonl` | `ops/events/<writer>.jsonl` |
   | lane ledger | `ops/lanes.jsonl` | `ops/lanes/<writer>.jsonl` |
   | decision ledger | `decisions/ledger.jsonl` | `decisions/ledger/<writer>.jsonl` |
   | spend ledger | `spend/ledger.jsonl` | `spend/ledger/<writer>.jsonl` |
   | ratification ledger | `ratifications/ledger.jsonl` | `ratifications/ledger/<writer>.jsonl` |

   - A tool appends only to its own writer's file (`runtime/locator.py` `writer_file`).
   - The hash chain is unchanged. Each ops writer file is its own chain. A seal's anchor commits that
     writer file, by path. `predict-gate.sh verify-chain` reports each file on its own line.
3. **The read order.** Every reader reads the legacy file first, then every writer file, sorted by
   name (`runtime/locator.py` `ledger_files`, the one helper every reader uses).
   - A writer file is read from disk, and also from the records worktree's HEAD. So a deleted writer
     file is still read as git holds it.
   - Order rules (for example "the prediction was sealed before the code") are judged within one
     file's order. They are never judged across writers by timestamp.
   - A work order whose events began in the legacy file and go on in a writer file reads in that
     order. So its seal and close order holds across the migration.
4. **The migration.** `bakehouse setup migrate [--writer <name>]`, run once per project, in the main
   clone or a lane clone.
   - It writes `FORMAT` at the records root. `FORMAT` holds the format number (2), the bakehouse
     version that reads it (0.8.0), and the sha256 of each of the five legacy files. A legacy file
     that does not exist is recorded as `absent`.
   - It commits `FORMAT` in the records worktree only, by path. After that commit, it sets the writer
     id when it is unset. A refused migration changes nothing, the writer id included.
   - From then on the five legacy files are frozen: no tool appends to them again.
   - It is refused when the records worktree has uncommitted changes to tracked files. The ops ledger
     is often one of them, because only the seal anchors and closes commit it. Commit it by
     path first, then run the migration again.
   - It is also refused when a legacy file exists but git does not hold it (untracked). Such a file
     would be frozen on this machine only, and every other clone would then refuse. Commit it first.
   - A second run changes nothing and says so.
   - Order: every machine moves to bakehouse 0.8.0 first (limit 2, below). Then one person migrates
     and pushes the records branch. Each other machine pulls it and runs `bakehouse setup` in its main
     clone, which sets that machine's writer id.
5. **The refusals.**
   - A `FORMAT` this bakehouse does not know: every tool refuses, naming the bakehouse version that
     `FORMAT` says it needs.
   - A frozen legacy file whose sha256 is not the one `FORMAT` records (or an absent one that now
     exists): every tool refuses, naming the file. This is how an older bakehouse, still writing the
     old path, is caught.
   - `FORMAT` missing on disk while the records branch's HEAD holds it: every tool refuses, naming
     `git -C <records> checkout -- FORMAT`. Without this, the tools would read format 1 again and
     append to the frozen files.
   - After migration, a records worktree with no writer id: every write refuses, naming
     `bakehouse setup`. Reads still work.
   - A records folder with no `FORMAT` is format 1: one file per ledger, exactly as before 0.8.0.
6. **The limits.**
   1. Until a later release splits the registers, only one person runs the health and driver passes.
      Each pass rewrites one register file (`registers/*.json`), so two people running them conflict.
   2. An older bakehouse on another machine still writes the old paths. The frozen-file check catches
      it at the next read. It cannot stop the write. So every machine moves to 0.8.0 before migrating.
   3. The writer id names a records worktree, not a person. Two people sharing one main clone share
      one id; one person with two main clones has two.
   - `TOOLS.md` is still one file.
   - The ops files (`ops/events.jsonl` and `ops/events/`) are committed only by the seal anchors and the
     close's own commit. So an ops line that is neither a seal nor a close (a note, for example) reaches
     another machine only with that writer's next seal or close.
   - Work-order ids are reserved across machines only when the binding has `tracker.reserve`
     ([Work-order ids reserved before use](#work-order-ids-reserved-before-use)). Without it, two
     machines can still pick the same work order id. Decision ids are not reserved.
   - Moving the records between machines: [Records sync](#records-sync).

### Records sync

**The problem.** Records format 2 lets two machines write without touching each other's files. But
nothing moved the records between them. So each machine saw only its own work orders, decisions and
spend, until someone pushed and pulled `bakehouse-records` by hand.

**The fix (bakehouse 0.9.0, records format 2 only).** bakehouse syncs the records branch at fixed
points. One function does it for every caller: `runtime/records_sync.py` `sync`. In format 1 (no
`FORMAT` file) nothing fetches, merges or pushes.

1. **The fixed points.**
   - After each seal, revise, retire and seal-code, once the anchor commit is made.
   - After each close. The close first commits its own line, by path, as an anchor does (the commit
     carries a `Closed-line: <sha256 of the line>` trailer). Then it syncs.
   - After each line `decision.sh` writes (an ask, an answer, an outcome, an unattended start or stop,
     a backfilled line) and each line `spend.sh` writes (a record, a wave open or close), from bakehouse
     0.12.0. The tool first commits this writer's file, alone and by path: `decisions/ledger/<writer>.jsonl`
     or `spend/ledger/<writer>.jsonl`, with a plain subject such as `decisions: ask d20261121-f1d97f
     (decision.sh)`. It holds this writer's ops ledger lock around the commit, as every seal anchor and close
     commit does, so the commit never lands in the middle of a merge or another lane's anchor. Then it syncs,
     outside the lock. The commit is never signed, as the sync's own local steps are not. A commit that
     finds its line already committed by another call's commit (two asks at once, say) is not a failure: it
     says nothing and syncs. A commit that fails is one line on stderr, naming why; the line stays in the file, the
     next commit of that file carries it; the file is unstaged again, so it never goes into another
     commit (a merge's, say); and that time there is no sync. A sync that fails is its one
     `bakehouse sync:` line on stderr. Neither changes the tool's exit code or its stdout. So each line
     waits for a sync: normally under a second, at most the 20-second bound. A sync skipped because another
     sync holds the sync lock leaves the line local until the next fixed point; `bakehouse status` shows it in
     `sync.waiting`. A backfill makes one commit and one sync per line. A refused ask
     writes no ask line, so it commits nothing (its `gate-refused` line is in the ops files, left to the
     anchors). `DECISIONS.md` is never committed: setup's `.gitignore` names it.
   - At session start (`runtime/session-start.py`). The session always starts, whatever the sync does.
   - `bakehouse sync [--repo <folder>]`, run by hand. It moves commits only: anything else in the records
     worktree (a run folder, say) is committed first, with `bakehouse record commit`, as before. The PM
     skill's records commit (`skills/pm/SKILL.md`) also runs `bakehouse sync` after it.
2. **The steps.** In the records worktree, the sync:
   1. takes the sync lock, the file `bakehouse-sync.lock` in the records worktree's git folder. When
      another sync holds it, this sync skips. Only `bakehouse sync` says so. Lanes on one machine share
      the records worktree, so they share the lock.
   2. stops committing the derived register, once (point 4 below).
   - Steps 2 and 4's merge (with its abort) hold this writer's ops ledger lock, the lock every seal
     anchor and close commit holds. So a seal or close in another lane waits for them, and never
     commits in the middle of a merge. The network steps do not hold it.
   3. finds the upstream (point 3 below). With no upstream and no remote, it does nothing and says nothing.
   4. fetches the upstream branch, merges it, and pushes. It always merges and never rebases: writer
      files never overlap, so a merge is clean.
   5. A push refused because the remote moved: it fetches, merges and pushes once more.
   6. A merge with a conflict: it aborts the merge, keeps the local commits, and names the files.
3. **How the upstream is found.**
   - First, the records branch's own upstream: git config `branch.bakehouse-records.remote` and
     `branch.bakehouse-records.merge`. Setup sets it when it makes the records worktree from
     `origin/bakehouse-records` (`git worktree add --track`).
   - Else, when `origin` already holds the records branch (this clone has
     `refs/remotes/origin/bakehouse-records`): `origin`, and that branch. This covers a records worktree
     made from a local branch with no upstream set. The sync does not set the upstream; it finds it
     again each time.
   - Else nothing: the sync does nothing and says nothing. So a sync never publishes a records branch
     that origin does not have yet (a new orphan branch, made with `--no-remote-check`, for example).
     The first publish is the owner's, by hand, once:
     `git -C <records> push -u origin bakehouse-records`. From then on the sync finds that upstream.
   - The fetch names the branch (`+refs/heads/<branch>:refs/remotes/<remote>/<branch>`), so a
     single-branch clone works too.
4. **The derived register is not committed.** `registers/records.json` is rewritten by records-check
   from a code ref on each machine. Two machines on different branches write different ones, so a
   pull would conflict on it. In format 2 it is ignored (a line in the records branch's `.gitignore`)
   and removed from git, in one commit.
   - A new migration does this, in its own commit just before the `FORMAT` commit. A migration that is
     refused takes that commit back too.
   - A project migrated under 0.8.0 has it done by its first sync.
   - records-check still writes the register to disk. Nothing reads it from git history.
5. **It never blocks and never prompts.**
   - Every network command runs with `GIT_TERMINAL_PROMPT=0`, an empty `GIT_ASKPASS` (so no askpass
     program runs: not `GIT_ASKPASS`, `core.askPass` or `SSH_ASKPASS`) and `GCM_INTERACTIVE=never`.
   - For SSH it adds `BatchMode=yes` and `ConnectTimeout=10`, through `GIT_SSH_COMMAND`. When the
     owner set `GIT_SSH_COMMAND`, `GIT_SSH` or `core.sshCommand`, that is left as it is, and the
     bound below still holds.
   - The network steps of one sync share one bound of 20 seconds. A step still running then is stopped,
     with every process it started in its process group.
   - The local steps (the register's commit and the merge) run with `commit.gpgsign=false` and
     `merge.autoStash=false`, so they never ask for a passphrase and never stash writer lines away.
     They have no time bound of their own. A git hook the owner added to the records worktree can
     still wait or ask.
6. **A failure.** The commits stay local, and the next fixed point tries again. The tool's own result
   and exit code do not change: the seal, the close or the session start still succeeds. The caller
   prints one line, which starts `bakehouse sync:` and then names why:

   | Reason | When |
   |---|---|
   | `offline` | the remote cannot be reached, or gave no answer within the bound |
   | `no credential` | the remote asks for a login git cannot give without asking; or the token file has no value for its key |
   | `refused` | the push was refused, and still refused after the one retry |
   | `conflict` | the merge conflicts, or staged changes in the records worktree block it; the line names the files. Merge by hand (or commit or unstage them) in the records worktree |
   | `failed` | anything else, with git's own line |

   The last failure line and its time are kept in the records worktree's git folder
   (`bakehouse-sync-failure.json`), never in a tracked file. A sync that ends level clears them.
   `bakehouse sync` exits 1 on a failure, and 0 otherwise.
7. **A token the user provides.** `bakehouse setup --token-file <path> --token-key <NAME>
   --token-user <name>` (main clone; all three together).
   - Setup stores only the three values in the records worktree's local git config (`git config
     --local`): `bakehouse.tokenFile` (the file's absolute path), `bakehouse.tokenKey` and
     `bakehouse.tokenUser`. These are a path and two names. The token itself is never stored.
   - The file is the user's own `KEY=VALUE` file. A line may start with `export `. A line starting with
     `#` is a comment.
   - Setup refuses a token file inside the records worktree: a commit there of more than one file by
     path (a `git add -A`, say) would commit it.
   - At each sync, bakehouse reads the token from the file. It gives the token to git for each network
     command only, through an inline credential helper set with `git -c credential.helper=...`. The
     token travels in that command's environment, never in its arguments, which other users can see.
     An empty `credential.helper` comes first, so no helper git is already configured with (a
     keychain, for example) is asked, and none stores the token.
   - The token is never written to git config, a remote URL, the keychain, a log, `bakehouse status` or
     any output. A failure line names the file and the key, never the value.
   - A missing file, a missing key or an empty value: one `no credential` failure line ("token file
     <path> has no <KEY>"). The sync goes on without the token.
   - With the three unset, the sync uses whatever login git already uses.
8. **`bakehouse status`** shows it: the `sync` field ([Reading team state](#reading-team-state)). The
   plain output prints one line when local records commits are waiting to be pushed.
9. **The limits.**
   1. A push needs git to reach the remote without asking: give setup a token file, or use a login git
      already has. Without one, the sync reports `no credential` and carries on.
   2. An offline machine sees the other machines' records only at its next successful sync.
   3. `bakehouse status` is only as current as the last pull. It reads local refs, and never asks the
      remote.
   4. A file two machines both write conflicts, and stops every sync until it is merged by hand. Writer
      files never do. The health and driver registers (`registers/health.json`, `registers/drivers.json`)
      can (limit 1 of [One records file per writer](#one-records-file-per-writer)).
   5. The token is in the environment of what git starts for that fetch or push (a pre-push hook, ssh),
      and the helper gives it for any host that asks.
10. **A second machine, and format 2.** A machine still in format 1 never syncs, so it does not
    get `FORMAT` by itself. After one person migrates and pushes, each other machine, once, by hand:
    `git -C <records> pull --no-rebase`, then `bakehouse setup` in its main clone (which sets its
    writer id). From then on the sync moves its records.

### Work-order ids reserved before use

**The problem.** Two people on two machines both take the next work-order id from the tracker. Both
open the same id, say A1254. Their records then mix under one id: seals, closes and decisions for two
different pieces of work.

**The cause.** Before 0.10.0, bakehouse did not hand out ids. Each machine found the next id by
reading its own branches. A second machine's branches are not there until they are pushed. So two
machines can pick the same number.

**The fix (bakehouse 0.10.0).** An id is reserved on the records branch, through the remote, before
the tracker row is written. It is on only when the binding has `tracker.reserve` and the records are in
format 2. Without either, nothing changes.

1. **The binding field.** `tracker.reserve` is `{"prefix": "<prefix>"}`, for example
   `{"prefix": "A"}`.
   - An id is the prefix and a number: `A1262`.
   - A tracker id with one letter after the number counts as its number: `A1063b` counts as 1063.
   - The prefix is 1 to 16 letters, hyphens or underscores, and starts with a letter. Preflight check 2
     fails any other shape, for every job.
   - The prefix and a number must match `tracker.id`: check 2 fails when `<prefix>1` does not.
2. **The floor: `bakehouse reserve --start [--repo <folder>]`.** Run it once per project.
   1. It syncs the records branch (as `reserve` does, point 3 step 1).
   2. It fetches each remote of the main clone, one at a time, each within its own bound of 20 seconds.
      The records token goes only to the remote whose URL is the records upstream's; the others use the
      user's own login, with no prompts. A remote it cannot fetch stops it, named: fetch that remote by
      hand, or remove it.
   3. It reads the tracker file (`tracker.file`) on every local and remote-tracking branch of the main
      clone, and takes the highest id number there.
   4. It writes that number to `reservations/FLOOR` (JSON: `floor`, `writer`, `time`, `branches`),
      commits that file alone, by path, and pushes it.
   - Every id at or under the floor counts as taken. One file covers every id used before
     reservations.
   - It reads only the branches of this clone: its local branches, and the remote-tracking branches
     its fetch brings. Nothing needs pushing for the floor.
   - A branch that is only in a lane clone is read once the lane clone is a remote of the main clone:
     `git -C <main clone> remote add <lane> <lane clone path>`. `--start` then fetches it from disk.
     `--start` refuses when any remote of the main clone cannot be fetched, so remove the remote of a
     lane clone that is deleted.
   - A branch that is only on another machine is read when that machine runs `--start`. Each run raises
     the shared floor from its own branches.
   - A later `--start` raises the floor, and never lowers it. When this clone's branches hold an id
     above the recorded floor, it commits the new `reservations/FLOOR` and pushes it, the same way a
     reservation is pushed (point 3, step 4). Otherwise it changes nothing and says so.
3. **A new id: `bakehouse reserve [--repo <folder>]`.** It prints the next free id on stdout, and
   records it.
   1. It syncs the records branch. If the sync cannot reach the remote, it refuses and says why: an id
      needs the remote.
   2. It takes the highest number among the reservations and the floor, and adds one.
   3. It writes `reservations/<id>` (JSON: `id`, `writer`, `time`) and commits that file alone, by path.
   4. It pushes. If the push is refused because the remote moved (a race), it fetches. It takes its own
      reservation commit back (below). Then it merges the remote.
      - The remote now holds `reservations/<id>`, or a floor at or above the id: it goes back to step 2.
      - Otherwise: it commits the same id again, on top of the merge, and pushes again.
   5. At most 5 pushes. Then it refuses and says so.
   - **Taking a commit back.** The commit must have one parent and hold only its one file. At the tip
     of the records branch, the branch moves back to its parent. Under a later commit (a seal in another
     lane, for example), a new commit puts the file back as the parent had it (for a reservation:
     deleted), by path, under the writer lock. Any other shape stops reserve, which names the repair:
     `git -C <records> revert --no-edit <commit>`.
   - A push declined by a server hook or a protected branch is not a race: reserve refuses, says so, and
     does not retry.
   - A push with no answer within the bound: reserve refuses, and says the id may be on the remote all
     the same. Such an id is burned: it is never handed out twice.
   - Any refusal takes the unpushed reservation commit back, so the id is not committed locally.
   - Interrupted between its commit and its push (killed, for example), reserve leaves the commit.
     The next `reserve` or `--start` takes back this writer's reservation commits that the upstream does
     not hold, before its sync, so the sync never pushes them.
   - Token settings that are wrong (`bakehouse setup --token-file ...`) refuse, naming them.
   - `reserve` refuses until `--start` has run, naming it.
4. **The lock.** `reserve` and `--start` hold the sync lock (`bakehouse-sync.lock`, in the records
   worktree's git folder) from their sync to their last push. They wait up to 25 seconds for a sync
   that holds it.
   - Why the sync lock: every sync takes it. So no sync at a fixed point merges or pushes between the
     reservation commit and its push, or while a losing commit is taken back. Two reserves on one
     machine share the records worktree, so they share the lock and run one after the other.
   - The writer lock (this writer's ops ledger lock) is held only for each local step that moves the
     records branch: the commit, the take-back and the merge. A seal or close in another lane then waits
     for those steps only. It never waits on the network.
   - The network steps run as the sync's do: no prompts, the same token, and one bound of 20 seconds
     for `reserve`'s own steps, after the sync's.
5. **Preflight `change <id>`** (check 7). When reservations are on:
   - an id with a reservation passes;
   - an id at or under the floor passes;
   - any other id fails, naming `bakehouse reserve`;
   - no floor yet fails, naming `bakehouse reserve --start`;
   - an id that is not the prefix and a number fails;
   - a reservation by another writer is a note, not a failure: one person may move between their own
     two machines.
   - Preflight reads the records branch as this machine has it. A reservation made on another machine
     is seen after the next sync (session start runs one).
6. **The order on a project already in format 2.** Migrate first. Then add `tracker.reserve` to the
   binding: `--start` reads the prefix from it. Then run `bakehouse reserve --start`. Between the last
   two, `/bakehouse:change` fails preflight check 7 ("no floor"). From then on, the PM runs `bakehouse
   reserve` before it writes each new tracker row, and writes the id it prints.
7. **The limits.**
   1. An id needs the remote. Offline, no new work order can start.
   2. An id handed out by `reserve` and never used stays taken.
   3. The tracker row and the reservation can still disagree, if someone writes a row by hand.
      Preflight catches it at `/bakehouse:change`.
   - `reserve` does not write the tracker row. The PM writes it, on the product branch, as before.
   - Ids already used are not rewritten, and get no reservation file: the floor covers them.
   - Decision ids are not reserved.
   - The floor is only as good as the branches `--start` could read. A branch only in a lane clone
     counts once that lane clone is a remote of the main clone. A branch only on another machine counts
     when `--start` runs on that machine.
   - Without `fcntl` (native Windows) there is no sync lock and no writer lock, as for the sync: two
     reserves on one machine at once can then pick the same id, and the push race decides.

## Lanes

A lane is one line of work running beside others in the same project, on the same machine: usually its
own clone, its own branch and its own session. Lanes let one person run two or three work orders at once.
A second person, or a second machine, has its own main clone ([README, "People and machines"](../README.md#people-and-machines)).

**The PM decides the lanes.** How many lanes run, which work orders may run side by side, and when a lane
merges are the project PM's judgement. No tool decides them. Two work orders that change the same files
should not share a time slot. The ledger has an event to record that judgement (`footprint-judged`).
The binding can name runs that must never run in parallel: `gates.oneAtATime` ([The binding](#the-binding)).

**How many lanes.** As many as the owner asks for, within two limits:

- **Safe.** Lanes side by side change different files.
- **Efficient.** Each extra lane must make the whole faster. When the machine is the bottleneck, open
  no further lane, and say why. The lane ledger shows it: heavy jobs waiting longer (`resource-waited`)
  or running longer (`resource-started` to `resource-ended`) than before the lane opened.

**The lane habits.** They are the PM's, and a project's own instructions win over them:

- Whichever lane merges second re-measures before it closes (`remeasure`).
- A lane that cannot run yet does preparation that changes no code.
- Refresh a lane from the default branch before its first prediction is sealed.
- Check each lane against something live (its branch, its last commit, its session) before reporting
  on it.
- Say at once when a lane is not moving the owner's outputs.
- At close, audit the lane's folder: nothing unmerged and nothing untracked. Check that nothing is using
  it (no session, no running job). Then delete it.

**What bakehouse gives a lane:**

1. **One set of records.** A lane's clone writes to the main clone's records, so there is one history.
   Before any team tool in a lane clone, run `bakehouse setup --pointer <main clone>`. Without it, every
   team tool there refuses. Plain `bakehouse setup` in a clone that looks like a lane refuses too, and
   names `--pointer`.
2. **Its labels.** Each lane's commands carry `BAKEHOUSE_LANE=<lane>` and `BAKEHOUSE_WO=<work order id>`.
   The change and refactor briefs say so, with the PM filling in the lane.
3. **A lock per lane.** `runtime/heavy-run.sh <resource> <command>` runs one heavy job, such as the full
   test suite, under the lock `<resource>.lane<BAKEHOUSE_LANE>` in the machine folder's `locks/`
   (`main` when the variable is unset). Heavy jobs in one lane take turns. A lane never waits for
   another lane.
4. **A lane ledger.** `runtime/lane-event.sh <kind> [key=value ...]` appends one event to
   `ops/lanes.jsonl` in the records: `lane-opened`, `lane-closed`, `footprint-judged`,
   `resource-waited`, `resource-started`, `resource-ended`, `merge`, `remeasure`, `question-asked`,
   `question-answered`. `heavy-run.sh` writes its resource events itself. No tool reads the ledger yet; it is
   kept so waits and holds can become statistics later.
5. **Close across lanes.** Close orders a lane's code against its records by commit time, not ancestry,
   and judges code a merge brought in by that merge's lane parent. Close does not fetch: a measured
   seal whose commit is still only in a lane clone needs `git fetch <lane clone> <branch>`, or the
   merge, before the work order closes ([`PREDICT-GATE.md`](PREDICT-GATE.md), "Closing a work order").

**A lane, start to end:**

1. The PM judges the footprint: which files the work order changes, and whether another open lane
   changes them too.
2. Make the lane's clone, and in it run `bakehouse setup --pointer <main clone>`.
3. Run the job there, with `BAKEHOUSE_LANE` and `BAKEHOUSE_WO` on its commands, and heavy jobs through
   `heavy-run.sh`.
4. Close it as the job says: `predict-gate.sh close <id>` before the pull request, then, after the merge
   into the default branch, `predict-gate.sh close <id> --merged` (`/bakehouse:change` steps 9 and 10).

### Two product branches that follow one branch (`mainFollows`)

Some projects keep two product branches and one shared branch, usually `main`. The rule is:

- Engine changes merge into `main` when each work order closes.
- `main` merges into a product branch only between work orders, before the next work order's first baseline.
- `main` never merges into a product branch between a seal and its close. That merge changes `HEAD` after the
  measured seal, and close refuses it.

Why it matters: a work order whose first baseline is measured on a branch that missed `main` never measures
the other product's engine changes. The binding field `"mainFollows": {"branch": "main"}` turns on three
checks. Without the field, nothing below happens.

1. **The first baseline's seal refuses.** `predict-gate.sh seal <id> first <...>/moves.baseline.json` refuses
   when the tip of `main` is not an ancestor of the code checkout's `HEAD`. The refusal names the fix: merge
   `main` into this branch, then measure and seal the baseline again. In a lane clone it names
   `git pull <main clone> main`. A fix round's baseline, a prediction and a measured file are not checked.
   A seal again of a first baseline's same bytes (as "ANCHOR REFUSED" asks) is not checked again: its first
   seal was, and a merge then would change the baseline, which is never sealed.
2. **The session notice.** At session start, when `main` has N > 0 commits that touch the code roots and that
   this checkout's `HEAD` does not have, one line says so: "`main` has N engine commits this branch does not
   have: merge it in before the next work order's baseline". There is no line when N is 0.
3. **The note at close.** When `main` gained commits that touch the code roots after the work order's first
   baseline was sealed (another product's work order closed meanwhile), close still passes. It prints one
   note: this change and those commits are first measured together at the next work order's baseline. The
   commit compared is the one the first baseline's seal event records.

Which `main` is read: always the main clone's own branch, read in the main clone with no fetch. The main clone
is the repository of the records worktree. In a lane clone, the lane's own `main` and its `origin/main` are
never read, because they can be stale. A main clone with no such branch makes the seal refuse, naming it.

## Roles

A role names a job. An agent fills a role. A job dispatches `role:<job>:<role>`, for example
`role:refactor:engineer`.

| Role | Does | Shipped agent |
|---|---|---|
| `architect` | lays out a restructure-or-accept decision for one part | `bakehouse:architect` |
| `engineer` | makes one step of a refactor or a change behind that job's gate: byte-identical outputs for `refactor`, predicted moves for `change` | `bakehouse:engineer` |
| `reviewer` | reviews an engineer's diff in either job, `refactor` or `change`, and runs the risky case | `bakehouse:reviewer` |
| `absence-check`, `count-check`, `blast-radius` | check a claim of absence, a count, the reach of a change | `bakehouse:` and the role's name |
| `design-author`, `design-reviewer` | write and review a design | `bakehouse:` and the role's name |
| `expert` | an independent reading of one question about what a source means, standing in for a human expert in the project's field; it reads blind and advises, never rules; two of them, on two different models, are the readers of the `blind-readers` skill | `bakehouse:human-expert` |
| `code-review` | single-pass code review | none shipped; falls back to the built-in `general-purpose` agent |

Before R0.1 the agents `bakehouse:engineer` and `bakehouse:reviewer` were named `refactor-engineer` and `refactor-reviewer`; an older record that names those agents means these two. A dispatch by an old name in bakehouse's own namespaces (`role:refactor-engineer`, `bakehouse:refactor-engineer`, and the same for the reviewer) is refused, and the reason gives the new name. A bare `refactor-engineer`, or another plugin's (`acme:refactor-engineer`), is left alone: it may be the user's own agent.

Resolution checks these levels in order. At each level the key `<job>:<role>` is tried before
`<role>`. A level that names only agents that are not installed is skipped, and the skip is logged.

1. The repo binding's `roles`.
2. `agent-roles.json` in the machine folder — the owner's own table. Put an employer's or a private
   plugin's agent here. The machine folder is outside the plugin, so an update never overwrites it.
3. The shipped table, `runtime/agent-roles.json`.

A review role never falls to the session that did the work: with no match it goes to
`general-purpose`. `runtime/team_roles.py` is the one implementation of these rules;
`runtime/agent-roles.py` enforces them as hooks and logs every change to
`logs/agent-roles.log` in the machine folder. The hook fails open, with two exceptions. First, a dispatch whose decision needs a
binding that cannot be read (binding.json does not parse, is not an object, cannot be read or is a
dangling symlink, or its `reviews` field is not a safe path, for a dispatch whose record folder line names a
folder in that repo; the project needs setup, with the locator's message, for a team dispatch only; the project locator cannot be
run or does not answer in time) is refused, and the reason names that
cause and the file or command to fix (`dispatch:binding-unreadable`). A team dispatch is a `role:` dispatch, a
dispatch of or by an agent a role table lists, or one whose brief has a `Work order <id>:` line or a record
folder line; in a clone that needs setup, any other dispatch passes unchanged. When the run's deadline was used up
by earlier calls, the reason says so. Second, a role table that cannot be used never changes a dispatch silently.
While the personal table or the binding's `roles` cannot be used (the table cannot be read, does not parse, is not
a JSON object, or its `roles` is not an object), a `role:` dispatch is refused, and the reason names the file and
the error (`dispatch:role-table`). Any other dispatch skips that table, keeps the shipped table's rules (its model,
the prediction marker), and the user is told in a message that names the file. A shipped table that cannot be used
is a bug in the plugin: every dispatch passes unchanged, and the user is told. A stop still fails open. The whole
hook run has one deadline of 6 seconds, counted from hook start; each path's binding is read once per run.

Outside a repository with a binding, the shipped table acts only on a `role:` dispatch. It does not
change a dispatch made by plain agent name, and it never sends an agent back for a run record. The
owner's own table in the machine folder is the owner's explicit choice and applies everywhere.

The shipped agents are the plugin's (`agents/`), so the harness names them `bakehouse:<agent>`, and
the shipped table names them so. The hooks accept a plugin agent reported with or without its
`bakehouse:` prefix. A bare name (for example `reviewer`) counts as the plugin's agent only when no
user agent (`~/.claude/agents`) and no project agent (`.claude/agents`) has that name. When one does,
the bare name is that agent: the hooks force no model on it, never send it back at its stop, and do not
count it as a team dispatch. To fill a role with an agent of the owner's own, name it in the owner's table.

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
| `register` | the register's path | for `health` and `refactor` |
| `signal` | what rewrites the register: a command, a pointer, or rules for the shipped script (see "The signal") | for `health` and `refactor` |
| `tracker` | `file`: where a job opens or reads its item; `id`: the id form, as a regular expression; `reserve`: `{"prefix": "<prefix>"}`, which turns on work-order ids reserved before use, in records format 2 ("Work-order ids reserved before use") | when owned; `reserve` no |
| `measure` | `commands`: what the architect runs; `layerRules`: where the layer rules are (optional) | `commands` when owned |
| `gates.byteIdentical` | the gate list for `refactor` | when owned |
| `gates.predictedMoves` | `gates`: the gate list for `change`; `prediction`: the prediction file's name, `moves.prediction.json` (preflight refuses a prose file; one may sit beside it) | when owned |
| `gates.oneAtATime` | runs that must not run in parallel | no |
| `branches` | the branch form per job: `refactor`, `change` | when owned |
| `roles` | role → agent overrides for this repo | no |
| `runs` | where the run (record) folders live: `"records"`, under `runs/` in the records worktree; `"tree"`, under `reviews` in the code tree. Without it: tree mode when the binding names `reviews`, else records mode ("Where the run folders live") | no |
| `reviews` | tree mode: the folder of review records in the code tree; default `axis/runs/`. A binding that names it and no `runs` is in tree mode | no |
| `reviewName` | one step's record folder name; default `{date}-{id}-step{n}-review` | no |
| `leaveAlone` | files no step may edit | no |
| `records` | `since`: the date the record contracts apply from (`YYYY-MM-DD`); `jobs`: artefacts added to a job's record contract (see "Records against the process"). It may add, never remove. `codeRoots`: the folders whose commits are code, in any binding. It is used when `signal` names no `codeRoots`: a binding with no `signal`, or a `signal` that is a command or a pointer. A rules `signal` that names its own `codeRoots` wins | no; `change` and `refactor` need code roots here or in `signal.codeRoots` |
| `mainFollows` | `{"branch": "<branch>"}`: the branch engine changes merge into as each work order closes, read in the main clone. With it, a work order's first baseline seals only when that branch is merged into the code checkout's `HEAD`; session start and close say when it has engine commits the branch lacks ("Two product branches that follow one branch") | no |
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
3. **Review findings.** In each review folder (the binding's `reviews`; in records mode, `runs/` in the
   records worktree) whose name starts with a
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
folder (in records mode, `runs/` in the records worktree, which need not exist yet) and finds two kinds
of family. A family seen in enough run folders inside the window is raised
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
| `register` | the register file; a relative path is taken in the records `registers/` folder, beside the health register | `drivers.json` |
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
"adopt" | "accept" | "team", "date": "YYYY-MM-DD"}`, with any other fields the person wants, such as a
note or the tracker id of the build. The script never overwrites it. `build` lands one shared tool in
the project. `team` sends the family to bakehouse's own backlog instead, because the signal cannot tell
a tool of the project from a tool of the team. A `team` decision must carry `item`: a non-empty string
naming the bakehouse backlog item it became. Without it the script stops, naming the family. A `team`
decision counts as decided like the other three, so the family is raised again
(`build-candidate-again`) when copies keep appearing after it.

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
owner: build one shared tool, adopt an existing one, accept the copies, or make it a team tool (an item
in bakehouse's own backlog)"}`. A raised family that
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
code roots when `signal` names none (no `signal`, or a command or a pointer). `records.jobs.<job>.require`
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
- **In records mode** the line names `{records}/runs/<record folder>/`. A path in a records worktree is
  mapped back to its project: git's common dir names the main clone, and that clone's
  `bakehouse.records` pointer must name this worktree (the locator's `--owner`). The path is then judged
  by the rules above, with `runs/` in the records worktree in place of `reviews`. A path in the records
  worktree but outside `runs/` is refused, as a path outside `reviews` is in tree mode. A path in a
  worktree no pointer names is refused. In a role's `prompt_append`, `{reviews}` becomes the full path
  of `runs/` in the records worktree.

**The work orders.** A work order is a tracker id (the binding's `tracker.id`) with at least one code
commit in the window. A commit belongs to the first tracker id in its subject. A code commit touches
one of the binding's code roots: `signal.codeRoots` when `signal` names them, else `records.codeRoots`
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
missing one needs the override: `PREDICT_GATE_CLOSE_OVERRIDE="<reason> | covers: R-xxxxxx,..." predict-gate.sh close <id>`,
naming every refusal id the close lists (`docs/PREDICT-GATE.md`, "The owner's exception").
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

**Where a finding is, and where it came from.** Each finding in a findings file is headed `## <id>
(<severity>): <what>` and holds a `Site:` line (a path from the repository root and a line, or `none`) and an
`Origin:` line (`this`, `earlier` or `elsewhere`). `bakehouse ground` reads them; no check asks for them.

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
the runner refuses every mutant judged by it. The copy has no `.git` either, so a test that needs one fails
the control too.

**The stop check.** The reviewer role has `run_record`. Its stop check also reads the reviewer
contract: each folder under the run folders' place (`runs/` in the records worktree in records mode; the
binding's `reviews` in tree mode), at any depth, in which the reviewer wrote a `findings*.md`
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
   (`records`), and uses only the closed placeholder list. `refactor` and `health` also need `register`
   and `signal`. `change` and `startup` do not; when the binding has them, checks 2 and 4 check them as
   for any job. `change` and `refactor` need code roots, as their code commits are told apart by them:
   `signal.codeRoots`, else `records.codeRoots`. With neither, preflight fails: "name the code folders in
   records.codeRoots (or signal.codeRoots)". A field of the wrong type fails, for every job: a `register`
   that is not a string, or a `signal` that is neither a string (a command or a pointer) nor an object
   (rules for the shipped script). An empty `register` or `signal` counts as missing. A `mainFollows`
   that is not an object with a non-empty string `branch` fails.
3. Every path field exists in the checkout. Each `records.codeRoots` entry is one, for every job; an entry
   that starts with `:` or holds `*`, `?` or `[` is a git pathspec or a glob, and fails. `reviews` is one
   folder, read by the role hook's own rule: `null`, a list, a full path (one inside the repository too)
   and a path with a `..` part fail, as they do at dispatch. A `mainFollows` branch the main clone does not
   have fails, naming it.
4. Every pointer finds its key. A gate the repo wrote as prose is listed, not failed. A rules `signal`
   whose `codeRoots` is not a list of one or more folder names fails in one line. An entry of nothing but
   slashes, such as `/`, is not a folder name: check 4 uses the rule close uses for code roots.
5. Every role the job dispatches resolves. A role table (the personal one or the shipped one) that cannot
   be read, does not parse, is not a JSON object or has a `roles` that is not an object fails, named. So
   does a binding whose `roles` is not an object.
6. For a job when `owned` is false: an unconsumed approval of this exact scope exists.
7. For `change <id>`, when reservations are on (`tracker.reserve`, records format 2): the id has a
   reservation on the records branch, or is at or under the floor. A reservation by another writer is a
   note. Check 2 checks `tracker.reserve`'s shape, for every job ("Work-order ids reserved before use").

`startup` mode runs checks 1 to 4, for a session start that knows the repo folder: it exits 0, and the
session carries any failure into its hand-back without stopping.

## The ledgers

All are append-only JSON lines in the project's records folder. A wrong row is never edited; a
correcting row follows it. `docs/TOOLS.md` lists the tools, and each tool's header comment is its full
reference.

| Ledger | Written by | What it is for |
|---|---|---|
| `ops/events.jsonl` | `ops-event.sh` | how the work was run: agent runs, fan-outs, loops, gates refused, decisions asked. `ops-gap.sh` reports how many commits it has fallen behind. |
| `decisions/ledger.jsonl` | `decision.sh` | every question put to the owner, the recommendation, the answer, and later the outcome. `DECISIONS.md`, at the top of the records folder, is the generated view. Scores agreement per category. Also the switch for unattended mode, and the PM's assumed answers ("Unattended mode", below). |
| `spend/ledger.jsonl` | `spend.sh` | measured tokens per agent, grouped into waves with a ceiling. |
| `ratifications/ledger.jsonl` | `ratify.sh` | which entries of a closed vocabulary two blind readers agreed are right. |

In records format 2 each ledger above is its frozen legacy file plus one file per writer, read in that
order ([One records file per writer](#one-records-file-per-writer)).

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

### Unattended mode

When the owner is away, a question can wait for hours on a decision the PM could make well.
Unattended mode lets the PM record its own recommendation as an assumed yes, marked as one, for the
owner to confirm or reverse on return.

**The project turns it on.** It is off by default, because it changes who decides. The project's
`.team/decision-config.json` turns it on; its keys merge over the defaults in
`runtime/decision-config.json`:

```json
"unattended": {
  "enabled": true,
  "afterMinutes": 30,
  "bands": [{"maxTokens": 100000, "above": 0.75}, {"maxTokens": 500000, "above": 0.85}],
  "neverCategories": []
}
```

- `bands`: the confidence an assumed yes needs rises with the step's estimated tokens. A step up to
  100,000 tokens needs a confidence strictly above 0.75. A step up to 500,000 needs one strictly above
  0.85. A step above the top band is never assumed; it waits for the owner. An estimate of 0 is a step
  with no AI. A project's `bands` replaces the default list whole.
- `neverCategories`: the categories that always wait for the owner. The category is compared without
  regard to case or spacing.
- `enabled`: only the JSON value `true` turns the mode on. Any other value, the text `"true"` included,
  leaves it off.
- A block of another shape is refused with gate `decision:unattended-config`, by every command that
  reads it, with a message naming `.team/decision-config.json`. Another shape is: not an object; `bands`
  not a list of `{"maxTokens": <whole number>, "above": <number>}`; `neverCategories` not a list of
  text. `bakehouse status` names the file too, and exits 1.
- `afterMinutes`: how long a question waits with no answer before the PM may start the mode. The PM
  reads it; no tool does, because a session acts only when it is woken.

**The switch.** `decision.sh unattended start --why "<text>" [--wave <spend wave>]` and
`decision.sh unattended stop` each append one event to the decision ledger:
`{"kind": "unattended", "action": "start" | "stop", "ts", "stream", "why", "wave"}` (`why` and `wave`
on a start only). A start is refused while the project has not turned the mode on. A stop is always
taken, because it is the safe direction. A start is also
refused while the mode is on (gate `decision:unattended-on`): stop it first. `--wave` is not checked
against the spend ledger. The mode is on when the project turns it on and the latest switch event is a
start.

**An assumed yes.** `decision.sh answer <id> --assumed --est-tokens <n> [--notes ...]` records the
ask's recommendation as an answer with `"agreed": true`, `"assumed": true` and `"estTokens": <n>`.
`--choice` may be left out; given, it must be the recommendation. It is refused, each with its own gate
id (table below), when the mode is not on, `--agreed` is given, the owner has already answered it, the
ask has no recommendation or no confidence, the ask names `--approves`, the ask was asked `--blind`, its
category is on the never-list, its category is not in the project's categories (`ask` still takes any
category, with a note; a project lists its own in `.team/decision-config.json`, and that list replaces the
default one; a `categories` that is not a non-empty list of text is refused, by `ask` too, with gate
`decision:categories-config`), the estimate is above the top band, or the confidence is not above its
band's line. A blind ask measures the owner's own answer, so it always waits for the owner. The owner confirms or reverses it on return with the normal
`decision.sh answer`; the latest answer counts, as always.

**An assumed yes is never the owner's voice.**
- The scores leave out a decision whose latest answer is assumed: it counts in neither agreement,
  calibration nor autonomy until the owner answers it.
- `predict-gate.sh close` refuses a cited decision whose latest answer before the commit is assumed,
  with the reason "an assumed yes, not the owner's" (gate `predict-gate:close`); `revise` and `retire`
  refuse one the same way (gate `predict-gate:input`).
- `bakehouse status` shows whether the mode is on, and lists each assumed answer to confirm
  ("Reading team state").
- `DECISIONS.md` and `decision.sh review` label an assumed answer as the PM's, to confirm.

A ledger with no switch events and no `assumed` field reads and scores exactly as before.

**Spend headroom.** `spend.sh headroom <wave> --est <tokens>` exits 0 when the wave's spend plus the
estimate fits its ceiling, and prints what is left after it. Otherwise it exits 1 with gate
`spend:headroom` and prints what is left now (0 when the wave is already over). A closed wave is
refused with gate `spend:wave-closed`. It writes nothing. Headroom counts recorded spend only, and an
agent still running has recorded none. So `--est` is the next agent's estimate plus the estimates of
every agent still running. The PM runs it before every dispatch while the mode is on, so a run stops
before its ceiling when the estimates hold.

**A wave's spend.** `record --tokens` is the agent's total so far. A wave's figure is the tokens its agents
added while in it, worked out from each agent's rows in ledger order:
- A row in the same wave as the agent's last one corrects what it added there.
- A row in another wave with more tokens adds the difference to that wave.
- A row in another wave with the same tokens or fewer corrects the last wave: what was added there moves.
- A wave's amount is never below zero: what is missing is taken from the agent's wave before.

The waves, and no wave, always sum to the agents' totals. Only what the agent added last can be corrected, and
`record` prints a line when a wave loses tokens by it. So a ceiling, an estimate and a `--predicted-tokens`
(all the agent's rounds in the wave) are always for this wave alone, never a running total, also when shown to
the owner.

### Decision triggers

`decision.sh ask` has four triggers (0.11.0). Each prints one line on stderr whenever its rule
applies, whether the ask passes or is refused:

```
decision: <name> trigger: <why>
```

| trigger | applies when | the way through |
|---|---|---|
| `upstream` | a fix round (`--fix-round N`) | `--upstream` |
| `re-check` | fix round 2 or later | `--rechecked` |
| `step-back` | fix round 3 or later, or a third round asked | `--stepped-back` |
| `altitude` | a change with `--layer out`, or `--owners` 2 or more | `--architect-option`, or a recorded architect run |

The ask event records `triggers`: the names that fired, in that order (`altitude-cluster` after
`altitude` at a cluster), or an empty list. An older ask event without the field reads as before. A
refused ask writes no ask event; its gate-refused event is written as before.

**A declared change.** `--change` says the question proposes a code change on `--task` (a fix or a
design change). `--fix-round` implies it. The asker declares it; the tool does not detect it from
words, because a word trigger fires on the wrong questions and is met by rewording (0.6.2). The word
"fix" prints the note it printed before; the word "change" in a question with `--task` and no
`--change` prints one note naming `--change`. With `--change`, three flags are required, and the ask
event records them with `change`:

- `--layer in|out`: the change's layer against the work order's owning layer.
- `--owners N`: how many places own the rule the change touches; 1 or more.
- `--area <name>`: the module or data shape whose rule the change touches.

One more flag goes with `--change`: `--architect-option <text>`, the `--option` that is the architect
check. It must equal one `--option` exactly; the ask event records it as `architectOption`.

**The altitude trigger.** With `--layer out`, or `--owners` 2 or more, the line is
`decision: altitude trigger: layer out`, `... N places own the rule`, or
`... both (layer out, N places own the rule)`. The ask is refused (gate `decision:altitude`) unless one
of these holds:

1. `--architect-option` is given. The asker declares which option is the architect check; the tool
   does not judge an option from its words.
2. An architect run is recorded for the task in the spend ledger (`spend.sh record --kind architect
   --task <id>`) at or after the work order opened. The work order opened at its first `sealed` event
   in the ops ledger. A work order with no seal has no recorded run that counts. One run lets one
   altitude ask through: the run counts only when it is recorded after the task's latest earlier ask
   whose `triggers` holds `altitude`. A second defect on the task needs a new run, or the option.

A recorded run is the second way through, not a free-text flag, because a free-text field can be
filled without the check being done.

**A cluster.** When the altitude trigger fires, the tool counts the earlier ask events, over every
task and every writer's file, with the same area and the same kind: layer out, or two or more owners.
Two areas are the same when they match with case ignored and runs of spaces collapsed to one, and
nothing else: "rowshape" is not "row shape", and punctuation makes another area. An ask that fired
both kinds counts for each. The count is of asks, not tasks: the same task asked twice counts twice.
The asks are ordered by their time, not by the text of their `ts`. At the third or later of one kind
on one area, the line is `decision: altitude trigger: cluster: <kind> on <area>, <n> times (<task
ids>)`, `triggers` holds `altitude-cluster`, and the ask is refused (gate `decision:altitude-cluster`)
unless `--architect-option` is given: the option that is one architect check of the area. A recorded
architect run does not let a cluster through: a run for one task is not a check of the area. The PM leads with
that one area check and holds the items it covers (the PM skill, "Name the trigger that fired").

One ask can print two altitude lines: a cluster line for one kind and the single line for the other.

**No decision around the check.** When the altitude trigger fires on an `--autonomous` ask, the ask is
refused unless `--recommendation` is the `--architect-option`. An assumed answer (`answer --assumed`,
unattended mode) on an ask whose `triggers` hold `altitude` is refused unless the ask's recorded
recommendation is its `architectOption`.

**A cluster clears.** When an ask that recorded `altitude-cluster` is answered with a choice equal to
its recorded `architectOption`, the count on that area starts again after that ask: only the
same-kind asks after it count. An answer choosing another option clears nothing.

**Names reused.** When the area has no earlier ask, the altitude line adds `area '<name>' is new
(areas already recorded, most recent first: ...)`, at most 10, so the asker can reuse one.

**Limits.**

- `--layer`, `--owners` and `--area` are the asker's judgement. The tool checks they are given and
  acts on them; it cannot check they are true.
- The asker declares which option is the architect check; the tool cannot check that its text names one.
- The area is free text, so two names for one area split the count. The list of recorded areas
  reduces this; it does not remove it.
- Across machines, the count sees another writer's asks once they are synced (records format 2).
- Only recorded asks count toward a cluster. A refused ask is not recorded.
- `DECISION_BACKFILL=1` skips every check of `decision.sh ask`, these too: a backfilled ask prints no
  trigger line and records `triggers: []`, and it still counts toward a cluster by its recorded
  layer, owners and area.
- Every trigger line prints before the first refusal for `--upstream`, `--rechecked`,
  `--stepped-back` or the altitude trigger, so one try shows every trigger that applies. A refusal
  before those (no `--task`, a round below 1, the grounding checks) comes before any trigger line.

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

- **One writer.** `runtime/gate_event.py` calls `ops-event.sh <kind> ... <note> <gate>`. It cuts the note to
  300 characters, except for `predict-gate:close`, whose note lists every refusal with its id. `ops-event.sh`
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
| `decision:upstream` | decision.sh | a fix round with no `--upstream` |
| `decision:fix-round` | decision.sh | a question about a fix round with no `--fix-round`, or `--fix-round` with no `--task` |
| `decision:fix-round-number` | decision.sh | a round below 1, or a round number already used |
| `decision:rechecked` | decision.sh | fix round 2 or later with no `--rechecked` |
| `decision:stepped-back` | decision.sh | fix round 3 or later (or a third round asked) with no `--stepped-back` |
| `decision:change` | decision.sh | `--change` (or `--fix-round`) without `--task`, `--layer`, `--owners` or `--area`; `--owners` below 1; `--architect-option` that is not one of the options; or `--layer`, `--owners`, `--area` or `--architect-option` without `--change` ("Decision triggers") |
| `decision:altitude` | decision.sh | the altitude trigger fired, with no `--architect-option` and no architect run recorded for the task since its work order opened and its last altitude ask; or an `--autonomous` ask whose recommendation is not the architect option; or `answer --assumed` on an altitude ask whose recommendation is not its architect option |
| `decision:altitude-cluster` | decision.sh | a cluster of altitude triggers on one area, with no `--architect-option` |
| `decision:unknown-id` | decision.sh | `answer` or `outcome` names no decision in the ledger |
| `decision:autonomous-answer` | decision.sh | `answer` on a decision taken autonomously |
| `decision:choice` | decision.sh | `--choice` is empty |
| `decision:agreed` | decision.sh | the choice differs from the recommendation and `--agreed` is not given |
| `decision:by` | decision.sh | `--by` is empty |
| `decision:unattended-disabled` | decision.sh | `unattended start` while the project has not turned unattended mode on |
| `decision:unattended-on` | decision.sh | `unattended start` while unattended mode is already on |
| `decision:unattended-config` | decision.sh | the project's `"unattended"` block in `.team/decision-config.json` has a shape the tools cannot use ("Unattended mode") |
| `decision:unattended-why` | decision.sh | `unattended start` with an empty `--why` |
| `decision:assumed-off` | decision.sh | `answer --assumed` while unattended mode is not on |
| `decision:assumed-estimate` | decision.sh | `--assumed` without `--est-tokens`, or `--est-tokens` and `--choice` without `--assumed` (`--est-tokens` alone, with no `--choice`, is a usage error, exit 2, with no gate event) |
| `decision:assumed-agreed` | decision.sh | `answer --assumed` with `--agreed`: only the owner agrees or not |
| `decision:assumed-answered` | decision.sh | `answer --assumed` on a decision that already has an answer from the owner |
| `decision:assumed-recommendation` | decision.sh | `answer --assumed` on an ask with no recommendation or no confidence |
| `decision:assumed-choice` | decision.sh | `answer --assumed` with a `--choice` that is not the recommendation |
| `decision:assumed-approves` | decision.sh | `answer --assumed` on an ask that names `--approves` (an approval is always the owner's) |
| `decision:assumed-blind` | decision.sh | `answer --assumed` on an ask asked `--blind` |
| `decision:assumed-never` | decision.sh | `answer --assumed` on an ask whose category is on the project's never-list |
| `decision:categories-config` | decision.sh | `ask`, or `answer --assumed`, while `categories` in the project's `.team/decision-config.json` is not a non-empty list of text |
| `decision:assumed-category` | decision.sh | `answer --assumed` on an ask whose category is not in the project's categories (`categories` in `.team/decision-config.json`, which replaces the default list; case and spaces ignored) |
| `decision:assumed-cost` | decision.sh | `answer --assumed` with an estimate above the top cost band |
| `decision:assumed-confidence` | decision.sh | `answer --assumed` with a confidence not above the line of the estimate's band |
| `spend:wave-args` | spend.sh | `wave open` with no name or no `--ceiling` |
| `spend:wave-exists` | spend.sh | the wave name is already used |
| `spend:wave-ambiguous` | spend.sh | no wave named, and not exactly one open |
| `spend:wave-unknown` | spend.sh | the named wave does not exist |
| `spend:wave-closed` | spend.sh | the wave is already closed (`wave close`, `headroom`) |
| `spend:backfill-note` | spend.sh | a backfilled row (SPEND_TS) with no `--note` |
| `spend:already-recorded` | spend.sh | the agent already has a row and `--correct` is not given |
| `spend:correct-no-row` | spend.sh | `--correct` for an agent with no row |
| `spend:architect-task` | spend.sh | an architect run with no `--task` |
| `spend:stepped-back` | spend.sh | a second architect run on one task with no `--stepped-back` |
| `spend:first-record` | spend.sh | a first record without `--why`, `--predicted`, `--predicted-tokens`, `--result` or `--outcome` |
| `spend:kind-required` | spend.sh | a first record without `--kind` |
| `spend:headroom` | spend.sh headroom | the wave's spend plus the estimate is over its ceiling; it prints what is left |
| `spend:kind-not-a-role` | spend.sh | a new first record, or a correction that changes the kind, gives a kind that names no role (`agent`) |
| `predict-gate:check` | predict-gate.sh check | a move nobody predicted, a wrong move, or a mustNotMove key that moved (also the owner's override, which keeps its own purpose) |
| `predict-gate:hook` | predict-gate.sh hook (pre-commit) | a staged measurement the gate refuses: one event per stopped commit (an override writes its own, per folder) |
| `predict-gate:close` | predict-gate.sh close | the work order does not close (also the owner's override, purpose "work order close overridden") |
| `predict-gate:input` | predict-gate.sh check, close | bad input, exit 2: a prediction that does not parse, a missing file, a work order id of the wrong form |
| `predict-gate:anchor` | predict-gate.sh seal, revise, retire | the line is written to the ledger, and its commit (the anchor) failed: git's index lock still held after 5 tries, or no git repository. Exit 1; close refuses the work order until a new seal anchors: seal again once the cause is gone. The ledger file's index entry is put back as it was before the anchor (unstaged, or a merge's or a hand-staged entry), so no commit of other files carries the line; the next anchor of that file does |
| `records-check:binding-records` | records-check.py | the binding's `records` object removes or replaces a contract artefact |
| `records-check:input` | records-check.py | any other bad input, exit 2 |
| `dispatch:role-unresolved` | agent-roles.py (PreToolUse) | a `role:` dispatch that no level resolves; also a dispatch of an agent's name from before R0.1 in bakehouse's own namespaces (`role:refactor-engineer`, `bakehouse:refactor-engineer`, and the same for the reviewer), and its reason gives the new name |
| `dispatch:role-table` | agent-roles.py (PreToolUse) | a `role:` dispatch while a role table it reads cannot be used: the personal `agent-roles.json` cannot be read, does not parse, is not a JSON object or has a `roles` that is not an object, or the binding's `roles` is not an object. The reason names the file and the error |
| `dispatch:binding-unreadable` | agent-roles.py (PreToolUse) | a dispatch whose decision needs a binding that cannot be read: binding.json does not parse, is not an object, cannot be read or is a dangling symlink, or its `reviews` field is not a safe path or its `runs` field is bad, when the dispatch's `Record folder:` line or its prompt needs that field (a `{reviews}` in a role's `prompt_append`, or the change engineer's prediction marker; the reason names that field and the binding file); the project needs setup (the locator's message; a team dispatch only, as in "Roles"); the project locator cannot be run or does not answer in time. The reason names the cause and what to fix. Its event is kept in `logs/agent-roles.log` in the machine folder (`GATE-EVENT ... kept in this log`) with its cause, because the working folder's ledger is found through the input that failed |
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
| `workOrders` | each open work order: one with a `sealed` event in the ops ledger and no `work-order-closed` event after it (a work order with no seal is not open): `workOrder`, `events` (how many), `latestRound` (the `round` of its latest event that has one, else `null`), `latestKind` and `latestAt` (the kind and `ts` of its latest event) |
| `waitingDecisions` | each decision asked, not decided autonomously, and not answered: `id`, `category`, `question`, `askedAt` |
| `unattended` | whether unattended mode is on ("Unattended mode"): `on`, and `since`, `why` and `wave` from its start event (`null` when off) |
| `assumedToConfirm` | each decision whose latest answer is an assumed one, for the owner to confirm or reverse: `id`, `question`, `confidence`, `estTokens` |
| `closedSince` | each `work-order-closed` event at or after `--since` (all of them without it): `workOrder`, `closedAt`, `commit` (the commit the close event names), `overridden` (`true` when the close was the owner's override) |
| `sync` | records format 2 ([Records sync](#records-sync)): `upstream` (`"<remote>/<branch>"`, or `null` when there is none to sync with), `waiting` (the local records commits not on the upstream, from local refs, so only as current as the last sync), `lastFailure` (`at` and `line` of the last sync failure; `null` after a sync that ends level). `null` in format 1 |

`predict-gate.sh close` writes a `work-order-closed` event only when the close passes, or when it
fails and the owner's override is recorded first.

Exit codes match the locator's. 0: the state was printed. 3: no team here; `--json` prints
`{"format": 1, "team": false}`. 4: setup is needed; the locator's message is on stderr. 1: a ledger is
broken (the ops ledger as `verify-chain` judges it, or a decision ledger line that is not a JSON
object), or holds a line whose shape status cannot use; stderr names it. 1 also: the project's
`"unattended"` settings have a shape the tools cannot use; stderr names `.team/decision-config.json`,
not a ledger. 2: bad usage, or a `--repo`
that is not a folder inside a git repository.

## Who has been there lately

`bakehouse ground <path>...` shows which work orders changed a file or folder, from git: commits in the code
roots, each owned by the first tracker id in its subject, over the whole history. `/bakehouse:change` shows it
to the owner with the work order. It judges nothing: its 14 days (`--days`) are how far back it shows, by the
day a commit was written, which a rebase keeps. A path outside the code roots has no work orders counted, and it
says so. It also shows the serious review findings (ANDON, P0, P1) whose `Site:` is in a path, inside the code roots
or not, from every findings file in the run folders on disk, over all time, and how many in the window have no `Site:` line
it can read. They are findings made, not faults open now: a fixed one still counts. A finding is read only under a
heading that starts with its id and holds its severity; in records mode, only from the records worktree. Named limits: a renamed file's history starts at the rename; a lane's commits
show once its branch is in this clone, or on a remote this clone has fetched; merge commits are not counted; a shallow clone's oldest commit seems to
change every file; a revert counts for the work order it names.

## How bakehouse's tools are designed

For whoever adds a tool or changes one.

- **Catch honest mistakes, not deliberate cheats.** The team's agents are trying to do the work right.
  A tool catches the mistakes they make honestly (a missing flag, a stale file, a move nobody
  predicted) and fails safe when it does. A guard against a cheat nobody has seen is written down as a
  named limit in "Known limits", not built.
- **Fail fast, at the first measured breach.** A limit trips the moment a measurement crosses it, and
  stops the next step. A rule that is only written down fails silently, so a rule that matters becomes a
  check. A tool never meets a limit by skipping a check.

## Known limits in this release

- Records format 2 (one records file per writer) has three named limits: one person runs the health and
  driver passes; an older bakehouse still writing the old paths is caught at the next read, not stopped;
  the writer id names a records worktree, not a person ([One records file per writer](#one-records-file-per-writer)).
- The records sync has named limits: a push needs git to reach the remote without asking; an
  offline machine sees other machines' records only at its next successful sync; status is only as current
  as the last pull; a file two machines both write stops the sync until merged by hand; the first publish of
  the records branch is by hand ([Records sync](#records-sync)).
- Work-order ids reserved before use have named limits: an id needs the remote, so offline no new work
  order can start; an id handed out and never used stays taken; a tracker row written by hand can
  disagree with the reservations, and preflight catches it at `/bakehouse:change`
  ([Work-order ids reserved before use](#work-order-ids-reserved-before-use)).
- Only one repository has run the jobs. The binding's field list is complete for that repo and a
  hypothesis for any other.
- A job in a repo another party owns stops before it writes any plan or step state: where that
  state is kept is not yet decided.
- `bakehouse setup` refuses a repo another party owns (a sidecar team): R0 does not set up records for
  such a repo, and changes nothing in it (design 05 §4.8 is not yet met).
- `records-check.py --binding <file>` reads the ledger of the project the locator finds, not a ledger
  beside that file.
- A records pointer set by hand (`git config bakehouse.records`) to the records worktree of another clone
  that shares this project's root commit (a fork, for example) is accepted; the ledgers are then written
  there. One to an unrelated project's records worktree is refused. `bakehouse setup` never writes such a
  pointer: `--pointer` refuses a main clone of another project.
- A fresh main clone whose remote `origin` cannot be reached cannot be set up: setup must first ask origin
  for the records branch, so that it never makes a second one. It says so and changes nothing.
- `bakehouse status` refuses a decision ledger line that is not a JSON object (exit 1), where
  `decision.sh` skips such a line with a warning.
- In records mode, records-check judges a work order by the record under `runs/`. A work order closed
  in tree mode before a switch to records mode, with code commits still inside the window, reads as
  `not-its-record` there: its close hashed files in the code tree. `bakehouse status` still counts it closed.
- `mainFollows` reads the main clone's branch as it is on this machine. On a second machine, the main clone's
  branch is only as current as its last pull, so a work order closed elsewhere and not yet pulled is not seen.
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
