---
name: blind-readers
description: >-
  Put a set of items to two isolated readers who each read the RAW source, and send the
  owner only the items every reader agrees on, as a phone-sized yes/no with the evidence. Use this
  skill whenever the owner must decide something that a careful reading of source material can
  settle: whether a correction is right, how a passage should be classified or turned into logic,
  whether a gap is real, what effect a clause has, whether a label on a set of rows is correct.
  Also use it when the owner says "blind read it", "get two readers", "independent check", "have
  someone else read the source", or when the PM is about to ask the owner to judge items from its
  own reading alone. Vocabulary mode: also use it to ratify a closed vocabulary (a lexicon, a
  classifier rule table, a pattern-to-class mapping, a controlled list) entry by entry against real
  matches, with ratify.sh, and to gate a vocabulary on that record. Do not use it for pure
  measurement (count it instead) or for spend decisions.
---

# Blind readers — two independent reads before the owner decides

The owner should not have to read source documents to decide. The PM should not ask the owner to
trust the PM's own reading either. This skill closes both gaps. Two readers read the raw source
independently. Only the items they agree on reach the owner. The owner decides those from a phone.

Learned 2026-10-10: the same items went to two isolated agents in two rounds. Both rounds agreed on
every item. The owner decided from a phone in minutes. The evidence went to the run folder and the
decision went to the decision ledger.

## When to use it, and when not

Use it when **all three** are true:

1. The owner must decide something about items in a source (corrections, classifications, gaps,
   effects, how text becomes logic).
2. A careful reader of the source could settle each item.
3. The PM's own reading is the only evidence so far, or the items carry prior labels that might be
   wrong.

Do **not** use it for:

- **Pure measurement.** A count, a diff or a test result is measured, not read. Run it and use
  `--evidence-kind measured`.
- **Spend decisions.** Readers cannot settle whether money or tokens should be spent. Ask the owner
  directly with an estimate.
- **One item the PM can quote outright.** If the verbatim quote settles it and nobody disputes it,
  use `--evidence-kind source-read`.

## Inputs the PM must have before starting

Write these five things down first. If any one is missing, stop and get it. The brief cannot be
filled without them.

| Input | What it is | Example |
|---|---|---|
| Items | A list of ids, and for each id where it sits in the source | `C-07: wording.pdf p.14, clause 4.2` |
| Source location | The folder or files that hold the raw source | `product-data/schemes/x/raw/` |
| Rule or question | The one rule the readers apply to every item | "Is the stored value what the source states?" |
| Answer vocabulary | The closed set of labels, and the closed set of actions | labels `CORRECT / WRONG / PARTLY`; actions `accept / reject / hold` |
| Must not read | Everything that would anchor the reader | other readers' output, the PM's notes, prior labels, and the item's stored value unless the rule is "Is the stored value what the source states?" |

**Prior labels are the main anchor.** If the items already carry a label (a classification, a
"fixed" flag, a gap class), strip it from what the readers see. Give them the id and the location
only.

**The stored value goes to the readers only when the rule tests it.** When the rule is "Is the
stored value what the source states?", the brief gives the stored value, and nothing about why it is
stored. For any other rule the stored value is on the must-not-read list.

## Step 1 — Estimate the spend, then get the go

State the spend before dispatching: N readers × items × source size. Typical: two readers on 10–30
items cost 60k–150k tokens in all. A go on the decision is not a go on the spend.

Two readers are a fan-out, so the PM's fan-out rule applies (the `bakehouse:pm` skill, §2): name the
readers and their models, the rough cost and the alternative, and wait for the owner's yes. Reader A
runs on a model its role does not default to (Step 2); say so in the same question, so the one yes
covers it.

With the yes, open a wave with the estimate as its ceiling:

```bash
${CLAUDE_PLUGIN_ROOT}/runtime/spend.sh wave open <wave-name> --ceiling <tokens>
```

## Step 2 — Brief each reader

Copy `${CLAUDE_PLUGIN_ROOT}/templates/blind-readers-brief.md`. Fill every `<…>` slot. Send the
**same brief** to every reader, changing only the reader letter (A or B).

- Each reader is the `human-expert` agent (`${CLAUDE_PLUGIN_ROOT}/agents/human-expert.md`). Dispatch
  it as `subagent_type: "role:expert"`; that resolves to `bakehouse:human-expert` unless the project
  maps the `expert` role to another agent. Paste the project's expert role into the brief under
  "Your role".
- **Reader A uses `opus`. Reader B uses `fable`.** The two readers run on two different models,
  because two runs of one model are one reader (the `dev-team` skill, "Independence"). The `expert` role forces `fable` on every dispatch
  (`${CLAUDE_PLUGIN_ROOT}/runtime/agent-roles.json`). So reader A's dispatch sets `model: "opus"` and
  puts `[model-approved]` in its description, under the owner's yes from Step 1.
- Start each reader as a **fresh agent**. Never `SendMessage` an agent that has seen another
  reader's output or the PM's view.
- Start the readers **in parallel**, in one message. Neither may wait on the other.
- Do not tell a reader that another reader exists beyond the line the template already has.
- Do not swap in a smaller model to save spend. A reading task is where a weak model guesses.
- Readers write nothing except page renders, in the scratch folder the brief names for them. Readers
  report back; they do not edit files or commit.

The brief enforces six rules. Do not remove any of them:

1. Read the raw source first: render the page or sheet, or extract the text. Never a summary,
   an index or a derived file.
2. Quote verbatim, with file, page or sheet, and cell or clause.
3. Mark every sentence as FACT (it is in the quote) or INFERENCE (the reader's reading of it).
4. One label per item, from the closed vocabulary.
5. One action and one recommendation per item.
6. Do not read the other readers, the PM's notes, prior labels or anything on the must-not-read
   list.

The brief also asks each reader to end the report with one JSON block. `blind-compare.sh` reads it
(Step 4).

## Step 3 — Save both reports verbatim

Save each reader's whole report, unedited, to the run folder **before** comparing. The comparison
and the owner's question both point at these files.

```bash
${CLAUDE_PLUGIN_ROOT}/runtime/save-report.sh <run-folder>/reader-a.md --title "Reader A" --source-agent <id>   # report text on stdin
```

Pipe the report text exactly as the agent returned it. Do not summarise, trim or reformat it.

## Step 4 — Compare into an agreement table

```bash
${CLAUDE_PLUGIN_ROOT}/runtime/blind-compare.sh <run-folder>/reader-a.md <run-folder>/reader-b.md \
  > <run-folder>/agreement.md
```

The tool reads the last ```json block in each report. It prints one row per item. An item
**agrees** only when every reader gives the same label **and** the same action. An item missing
from any reader counts as a disagreement. An item with an empty or missing label or action counts as
a disagreement. The exit code is 0 when all items agree and 1 when any disagree. It is 2 when one
reader is passed twice (the same file twice, or two reports naming the same reader), or when a
report has no valid JSON block (none; a ```json opener after the last complete block; one that does
not parse; an empty or missing `items` list; an item with no `id`; an id that appears twice).

Check the table against the brief. Its row count must equal the item count in the brief, and every
label and action must be in the closed vocabulary. An extra or missing id, or a label or action
outside the vocabulary, means that item is held.

A report without a valid JSON block: fill `${CLAUDE_PLUGIN_ROOT}/templates/blind-readers-agreement.md`
by hand from the reports. The same rule applies.

Then **check the quotes, not just the labels**. For every agreed item, confirm the readers quoted
the same passage, or passages that say the same thing. Two readers who agree on a label for
different reasons have not agreed. Mark such an item as disagreed.

## Step 5 — Only unanimous items go to the owner

- **Agreed items** go to the owner (Step 6).
- **Disagreed items are held.** They do not go to the owner as a question. Report them in one line
  each with both readers' quotes side by side, and say which of three things happens next:
  1. reword the rule, and re-run both readers on that item;
  2. render the exact page, and re-run both readers on that item;
  3. park it.

  Never settle a disagreement by a majority vote, and never by the PM's own reading. A held item is
  never sent as agreed.

## Step 6 — Write the phone-sized question

First run `${CLAUDE_PLUGIN_ROOT}/runtime/decision.sh should-ask <category>`. It prints one word:

- `ask`: the question leads with the recommendation and the confidence (the order below).
- `ask-blind`: the question carries **no recommendation and no confidence**. Leave out item 1
  below. The ask in Step 7 passes `--blind`.
- `decide-and-log`: no question goes to the owner. Log the decision in Step 7 with `--autonomous`
  in place of `--blind`.

Group agreed items by **cause**. Items that share one cause and one action get one yes/no. Each
question reads in this order:

1. **Recommendation and confidence first** (not for `ask-blind`). "Do X. Confidence 0.9."
2. **What and how many.** "7 items, all the same cause: the stored rate is the 2024 row; the
   source prints the 2025 row."
3. **One example**, with the verbatim quote and its place. One, not seven.
4. **Who agreed.** "Two blind readers agreed on all 7. Reports: `<run-folder>/reader-a.md`,
   `reader-b.md`."
5. **The question.** "Yes / no?"

Keep each question under about 8 lines, so it fits a phone screen. One question per message where
the project asks for one decision at a time.

`decision.sh ask` checks the question as sent. A guess word in it must be labelled `guess:`; the
words are `GUESS_WORDS` in `${CLAUDE_PLUGIN_ROOT}/runtime/decision.py`. In a correction question the
one most likely to bite is "should": "the stored value should be 10" is refused, while a sentence
that opens with "Should" is not counted. A question that proposes a fix needs `--upstream`. State
what the readers quoted instead of guessing.

## Step 7 — Log it in the decision ledger

Before sending each question:

```bash
${CLAUDE_PLUGIN_ROOT}/runtime/decision.sh ask --category <correction|engine-design|…> \
  --question "<the question as sent>" --option yes --option no \
  --recommendation yes --confidence <0-1> \
  --evidence-kind blind-readers-agreed --evidence "<run-folder>/agreement.md: <n>/<n> agreed" \
  --read <the example's source file> --read-what "<its page and clause>"   # add --blind for ask-blind
```

- `--recommendation` and `--confidence` are recorded even for `ask-blind`. The question as sent
  leaves them out; the ledger keeps them, so the score can be measured.
- `--read` names the source file of the example in the question, opened by the PM itself to check
  the quote. `--read-what` says which page and clause; it needs at least one word of two letters or
more, and digits do not count (`p.14 cl 4.2` passes, `p.14` alone is refused). A reader's report is
not a `--read` source.
  The categories in the project's `groundingExempt` list need no `--read`.

When the owner replies:

```bash
${CLAUDE_PLUGIN_ROOT}/runtime/decision.sh answer <id> --choice "<the owner's words>" --agreed <yes|no>
```

`--agreed yes` means the owner's choice is the recommendation; `--agreed no` means it is not. Always
pass it. Without it, `decision.sh answer` refuses a choice that is not the recommendation word for
word, and a blind answer is left unscored, so a blind "no" would never count against the score.

Held items are not logged as asks. They are logged when they come back agreed.

## Step 8 — Record what happened

Record each reader's measured tokens (the harness's figure), with the other fields its usage line
asks for:

```bash
${CLAUDE_PLUGIN_ROOT}/runtime/spend.sh record ... --kind expert --model <opus|fable> --wave <wave-name>
```

Compare the spend with the Step 1 estimate, and close the wave: `spend.sh wave close <wave-name>`.
Note the agreement rate (agreed / total). A low
agreement rate is a finding about the rule or the source, not about the readers. If more than a
third of items disagree, the rule or the vocabulary is probably ambiguous. Rewrite it before a
second round.

## Vocabulary mode — ratify a closed vocabulary entry by entry

**Problem.** A system holds a closed vocabulary. Each entry maps a pattern (a regex, a phrase, a
keyword rule) to a class. For example, "We will not cover …" maps to `exclude`. Entries get added
during engineering without proof. Asking the owner to judge the list by eye puts the whole load on
the owner, and the owner cannot see what each pattern actually catches.

**What this mode does.** Each entry is judged on real matches from the project's own corpus. Two
blind readers judge each entry. An entry is ratified only when both readers say the class is RIGHT.
The verdict goes to a ledger, and the ledger becomes a gate.

**Use it for** any closed lexicon, classifier rule table, mapping table or controlled list whose
entries assign a class to text. **Do not use it** for a list whose entries match nothing you can
export as text. Count or test those instead.

The tool is `${CLAUDE_PLUGIN_ROOT}/runtime/ratify.sh`; its header comment gives every command. The
interchange format is not written up as a document: the input checks in
`${CLAUDE_PLUGIN_ROOT}/runtime/ratify.py` (`load_entries` and `cmd_sample`) define it.

### Steps

1. **Export.** Write the project's own small exporter. It produces `entries.json` (id, pattern,
   patternKind, class) and `corpus.json` (docId, text, pointer). Add `classes.json` with a one-line
   meaning per class. The exporter makes no judgement.
2. **Estimate the spend** (Step 1 above). Each reader reads about `entries × per-entry` passages.
   40 entries at 8 samples each is about 320 passages per reader.
3. **Sample.**
   `ratify.sh sample --entries E --corpus C --classes K --per-entry 8 --seed <n> --out <run>`.
   This writes `sample.json`, `readers-sample.md` (what the readers see) and `brief-A.md`,
   `brief-B.md`. The briefs come from `${CLAUDE_PLUGIN_ROOT}/templates/ratify-vocabulary-brief.md`.
   The same seed and the same files give the same sample.
4. **Two readers.** Before sending each `brief-X.md` to `role:expert`, add to it a "Your role"
   section (the project's expert role) and a length line, as the item-mode brief has. Then start two
   fresh agents in parallel, one per brief (Step 2 above). They see the
   pattern, the class and the full matched passages. They do not see why the entry was added, its
   `kind` or `source`, or the code that holds it. Labels: **RIGHT** (right for every sample),
   **WRONG** (wrong for every sample, or most), **MIXED** (right for some, wrong for others).
5. **Save both reports verbatim** (Step 3 above).
6. **Compare.** `ratify.sh compare <run>/reader-a.md <run>/reader-b.md --out <run>`. This writes
   `agreement.json` and `agreement.md`. Every reader RIGHT means ratified. Every reader WRONG or
   MIXED means rejected. A split, a missing answer, or no matches at all means held.
7. **Check the quotes** for every rejected entry. Two readers who reject for different reasons
   point at two different fixes. Say so when you send it back.
8. **Route.**
   - Rejected entries go back to engineering with both readers' evidence. The fix is to narrow the
     pattern, change the class, or split the entry. A fixed entry needs a new round.
   - Held entries go to the owner only as a phone-sized question with both readers' quotes side by
     side (Step 6 above). The owner may approve a held entry. The PM never breaks the tie.
9. **Record.** `ratify.sh record --repo R --agreement <run>/agreement.json
   [--owner-approved id1,id2]`. This appends one row per entry to `ratifications/ledger.jsonl` in
   the project's records folder. An owner-approved entry is recorded as `ratifiedBy: owner`. Log the
   owner's question in the decision ledger as usual (Step 7 above).
10. **Gate.** Run `ratify.sh check --entries E --repo R` in the project's gate or pre-commit. It
    exits 1 when any entry is unratified, rejected, held, or changed since its row. A changed
    pattern or class needs a new round for that entry only.

### What each reader's report must end with

```json
{"reader": "A", "items": [
  {"id": "EXCL-01", "label": "MIXED", "confidence": 0.8, "wrongSamples": [3, 5],
   "quote": "We will not cover the excess unless …", "where": "sample 3, wording.pdf p.12 cl 4.2",
   "recommendation": "Narrow the phrase: sample 3 is a condition, not an exclusion."}
]}
```

`ratify.sh compare` reads the last such block in each report. A label outside RIGHT / WRONG / MIXED
stops the comparison with exit 2.

## Hard rules

- No item reaches the owner on one reader's word.
- No reader sees another reader's output, the PM's view or prior labels.
- Reports are saved verbatim before the comparison is run.
- Disagreements are held with both quotes. The PM never breaks a tie by its own reading.
- The owner's question leads with the recommendation and the confidence, except when
  `decision.sh should-ask` says `ask-blind`: then it carries neither.
- In vocabulary mode, an entry is ratified only when every reader says RIGHT, or when the owner
  approves a held entry and the ledger records it as the owner's.
