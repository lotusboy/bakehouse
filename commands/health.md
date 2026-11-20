---
description: "Health watch for the current repo — run preflight, rewrite the repo's health register in its records folder from the binding's signal, list the parts that are ready, check each work order's record against the process, then offer a refactor (a repo you own) or draft a proposal for the repo's approver (a repo another party owns). It never starts work. Trigger phrases: \"health check this repo\", \"what is ready to refactor\"."
harness: claude-code
---

# bakehouse — Health

The health watch for *this* repo. It says where to look. It never starts work.

Steps 1 to 7 and step 9 are deterministic and cost no AI spend: they run one command and read one
file. Every repo fact — the register's name, the signal, who owns the repo, who approves — comes from the repo's
binding, `.team/binding.json`. Never use a path, command, threshold or part name that the
binding and the register do not give you. The exceptions are steps 8 and 9: each runs one fixed
command that is the same for every repo, and asks you to read its output. A copied fact would bind every repo to the one it came from.

## How to run it

1. **Preflight.** From the repo root, run:

   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/runtime/team-preflight.py" health --repo "$(git rev-parse --show-toplevel)"
   ```

   - It exits non-zero: show its output and stop. Change nothing. A missing binding means the team
     does not apply to this repo; say that plainly. Never guess a repo's commands.
   - It exits 0: carry on. A `note` line is not a failure. Keep a note that the generic signal
     script is not installed yet: step 3 needs it.

2. **Find the records folder and read the binding.**
   - Run `python3 "${CLAUDE_PLUGIN_ROOT}/runtime/locator.py" records`. It prints the project's
     records folder, `{records}`. Run it with `binding` in place of `records` for the binding's path.
   - Read the binding. Keep `owned`, `approver`, `register` and `signal`.
   - The register's full path is `{records}/registers/<register>`. This is
     the value of `{register}`. The value of `{root}` is the repo root from step 1. The value of
     `{date}` is today, as `YYYY-MM-DD`.

3. **Get the signal command.** `signal` takes one of three forms.
   - **A command** (a string with spaces and no `#`): use it as it stands.
   - **A pointer** (`file#key.id`): open `file` in the repo root. Find the item under the top-level
     `key` whose `id` is `id`. Its `command` is the signal command. A pointer that does not lead to
     exactly one command: stop and say so.
   - **Rules for the generic script** (an object with `script`): the command is
     `python3 "${CLAUDE_PLUGIN_ROOT}/runtime/<script>.py" --binding <the binding's path> --register
     {register} --repo {root}`. If preflight noted that the script is not installed, or
     `${CLAUDE_PLUGIN_ROOT}/runtime/<script>.py` does not exist, stop here and say "the generic signal script
     is not installed yet". Run nothing, write nothing, and skip steps 4 to 7. This is a clean stop,
     not an error.

   Fill `{root}`, `{register}` and `{date}`. If any `{…}` or `<…>` is left, stop and say which: the
   health watch has no value for it.

4. **Run the signal.** Run the filled command from the repo root, once. Show its printed output.
   - It exits non-zero: show the error and stop. Do not retry with other arguments.
   - It rewrites the register in the records folder and nothing in the repo. The signal never overwrites a
     part's `decision`. Neither does this command.

5. **List the ready parts.** Read the register. List every part under `parts` whose `state` is
   `ready` or `ready-again`. For each, give one line:
   - the part id, its `signal`, its `fixCommits`, and its `topIds` if any;
   - for `ready-again`, also the earlier `decision`: its `date`, its `decision` and its
     `signalAtDecision`, because the signal has regrown since then.

   Say the count outright, and the `computed.asOf` and `computed.threshold` it was measured against.
   No ready part is a normal result: say "no part is ready" and skip step 6.

6. **Offer or propose, by ownership.** A ready part is an offer, never an order.
   - **`owned` is true.** Offer the owner one refactor per ready part, in one line each:
     `/bakehouse:refactor <part>`, and that it starts only when the owner says so. If `/bakehouse:refactor`
     is not installed, say so; the decision is still the owner's: restructure or accept. Do not start
     the refactor, write a decision, open a branch or brief an agent.
   - **`owned` is false.** Draft a proposal for the owner to take to the binding's `approver` — name
     the approver by that role, never by a person's name. For each ready part, the proposal states:
     the part id; the figures from step 5; what a refactor would do (restructure the part, behaviour
     unchanged, output only a pull request); and the approval it needs, a scope of exactly
     `{"part": "<part id>"}`. Show it in chat, under a short proposal id: an approval later cites that id. Nothing is
     started. Nothing is written to the repo, its tracker or its branches.

7. **Commit the register.** In `{records}`, if the register changed, `git add` that one file by
   its path and commit it: `health(<repo>): register as of <computed.asOf>`, where `<repo>` is the
   repo folder's name. Never `git add -A`:
   other sessions may have writes in flight. Nothing changed: say so; make no empty commit. Never
   push.

8. **List the shortcut markers.** A shortcut taken on purpose is marked in the code with a comment,
   `shortcut: <the limit>, <when to upgrade>`. This step runs whenever step 1 passed: after step 7,
   and also when step 3 or step 4 stopped the signal. It does not run after a stop in step 1, and it
   is not part of "steps 3 and 4" when another command refreshes the register.

   From the repo root, count first:

   ```bash
   git grep -cI --untracked -E '(#|//|/\*|<!--|--|;|%)[[:space:]]*shortcut:'
   ```

   The pattern needs a comment opener before the word, so a setting named `shortcut:`, a string and
   a URL do not match. Exit code 1 with no output means none: say "no shortcut markers found" and
   stop. Any other error: show it and stop.
   - **More than 200 matching lines in total:** give the count per file and say the lines were not
     read. Do not read them.
   - **Otherwise** run the same command with `-n` in place of `-c` and ` | cut -c1-300` on the
     end (a minified file can put a whole program on one line), and give one line per marker:
     `file:line`, the limit, and the upgrade condition. The upgrade condition is the last clause,
     the one that says when to upgrade; a limit may hold commas of its own. When no clause says
     when, give the marker with the word `no-trigger` in place of the condition.
   - Leave out a line that only talks about the marker (documentation, a test of this rule), and a
     hit inside a dependency or build folder that happens to be tracked; say how many you left out.
   - Say the count outright, and how many are `no-trigger`.

   What this search does and does not see: tracked files in the checked-out tree, and new files not
   yet added, unless they are ignored. It does not see ignored files, submodules, other branches, a
   marker written with a capital letter, or a comment style outside the list above. So report "found",
   never "all".

   This step is a read. It writes nothing, in a repo of any ownership, and it changes no part's
   state.

9. **Check the records against the process.** This step runs whenever step 1 passed, after step 8,
   even when step 8, step 3 or step 4 stopped.
   It reads the repo at one ref and the project's ops ledger, and rewrites one file in the records
   folder, `{records}/registers/records.json`. The rules are in bakehouse `docs/PROJECT-TEAM.md`,
   "Records against the process". From the repo root, run:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/runtime/records-check.py" --repo "$(git rev-parse --show-toplevel)"
   ```

   - Exit 2: show the error and stop. It names the binding field or the input that is wrong.
   - Exit 0: show its printed block, then report:
     1. The window (`computed.since` to today), the count of work orders with code commits, the
        count of open flags, and any `computed.warnings` line. List the code commits that name no
        tracker id (`uncreditedCommits`): they belong to no work order. When the binding sets no `records.since`, say that
        the window is the default and that the start date is the owner's to set in the binding.
     2. Each open flag in one line: the work order, the rule, what is missing, and its owner.
        Then each rule's next step, once.
     3. Each rule with `promoteToCode: true`: a promote-to-code candidate for the owner, with its
        count of work orders.
   - The flags are their owner's to act on: the PM, for its own runs. This step never acts on one:
     it closes no work order, writes no artefact and records no override.
   - Commit `records.json` as in step 7, by its path: `records(<repo>): as of <today>, <repo>
     at <head>`, where `<head>` is the short commit the printed block's first line names: the
     product-repo commit the register describes. The register holds no date or head, so it changes
     only when the findings do. Nothing changed: say
     so; make no empty commit.
   - In records format 2 (a `FORMAT` file in the records folder) skip that commit: `records.json` is
     ignored there, because each machine rewrites its own (bakehouse `docs/PROJECT-TEAM.md`, "Records sync").

## Guardrails

- **Watch only.** It reads, counts and writes the two registers. It never starts a refactor or a change,
  whatever the figures say, and even when the work looks small.
- **Facts from the binding.** Every repo path, command and owner rule comes from the binding or the
  register; the fixed commands of steps 8 and 9 are the only exceptions. If the binding lacks something this procedure needs, stop and say which field.
- **A repo another party owns is read-only.** The checkout is never written: no file, branch, hook,
  tracker item or commit. The only output is a proposal, shown in chat.
- **Plain report.** Counts, not adjectives. State the result outright: how many parts are ready, and
  what the owner can decide.

If this repo has no team (no binding), or the locator says setup is needed, say so plainly rather than
inventing content.
