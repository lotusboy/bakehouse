# Blind reader brief — reader <A | B>

Fill every `<…>` slot. Send the same text to every reader; change only the reader letter.

---

You are reader **<A>**, an independent reader. Another reader may be given the same items. You must
not look for, read or ask about anyone else's answers.

## Your role

<the project's expert role, pasted in: which expert you are, what that expert knows, and how the
field reads its documents>

## The task

For each item below, read the raw source and apply this rule:

> <the one rule or question, e.g. "Is the value stored for this item what the source states?">

## The source

<folder or files, e.g. `product-data/schemes/x/raw/`>

## The items

| Id | Where it sits in the source |
|---|---|
| <C-01> | <file, page or sheet, clause or cell> |
| <C-02> | <…> |

<Only when the rule is "Is the stored value what the source states?": give each item's stored value
here, and nothing about why it is stored. For any other rule, leave this out: the stored value is on
the do-not-read list below.>

## Answer vocabulary

- Label, exactly one of: <CORRECT / WRONG / PARTLY>
- Action, exactly one of: <accept / reject / hold>

## Do not read

- Any other reader's report or output.
- The PM's notes, plans, summaries or messages about these items.
- Prior labels or classifications of these items: <name the files or fields>.
- The stored value of these items, unless it is given above: <name the files or fields>.
- <anything else that would anchor you>

If you open one of these by accident, say so in your report.

## How to read

1. **Raw source first.** Render the page or sheet, or extract the text from the original file. On
   macOS, `qlmanage -t -s 1600 -o <render folder> <file>` renders a PDF or xlsx page. Renders go
   in <render folder: a scratch folder for this run>, and nowhere else. `qlmanage` exists only
   on macOS: elsewhere, extract the text from the original file, and say so for that item. Do not
   rely on an index, a summary or a derived file. If you can only reach a derived file, say so for
   that item.
2. **Quote verbatim.** Give the exact words, with file, page or sheet, and clause or cell.
3. **Mark fact and inference.** Begin each sentence of reasoning with FACT (it is in the quote) or
   INFERENCE (your reading of the quote).
4. **One label, one action, one recommendation per item.** The recommendation is one sentence saying
   what should be done.
5. **Confidence.** A number from 0 to 1 for each item.
6. **Not found is a claim.** If you cannot find the passage, say which files you searched and how.
   "Not found in <files>" is not "absent".

Length: about <100–200> words per item, plus the JSON block. This replaces any default length.

Do not edit files, commit, or start other agents. Write nothing except renders in <render folder>.

## Report shape

For each item:

```
### <id>
Quote: "<verbatim>" — <file>, <page/sheet>, <clause/cell>
FACT: …
INFERENCE: …
Label: <label>   Action: <action>   Confidence: <0-1>
Recommendation: <one sentence>
```

End the report with exactly one JSON block, and nothing after it:

```json
{"reader": "<A>", "items": [
  {"id": "<C-01>", "label": "<label>", "action": "<action>", "confidence": 0.9,
   "quote": "<verbatim, short>", "where": "<file p.N clause>", "recommendation": "<one sentence>"}
]}
```
