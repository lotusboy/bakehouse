# Walkthrough: the gates by hand

Before you run `/bakehouse:change T1`, see the two checks it relies on, one step at a time:

- **The predicted-moves gate.** You write down which outputs a change will move, before you change
  anything. The gate compares that with what really moved, and stops on any move nobody predicted.
- **Mutations.** You break the fixed code on purpose, in small ways, and see whether the tests notice.
  A break the tests miss shows a missing test.

In the real job the engineer and the reviewer do all of this for you. Here you do it yourself, so you
can see what they do. It takes about 15 minutes, and no AI.

**Before you start:** do steps 1 to 3 of this example's [README](README.md), so that setup has run. Run
the commands below from the Bash tool in a Claude Code session opened in `~/notes-app`, because
`bakehouse` is on the PATH only there. You can also ask the session to run them for you.

You work on a practice branch, and keep the practice files outside the repo. At the end you delete
both, and `main` is as it was, ready for the real job.

```bash
cd ~/notes-app
git switch -c practice
mkdir -p ~/notes-app-practice
```

## 1. Measure before the change

```bash
node tools/measure.mjs ~/notes-app-practice/moves.baseline.json
cat ~/notes-app-practice/moves.baseline.json
```

There are 9 keys: 3 request cases (`ok`, `empty`, `missing`), and for each its status, its body and how
many times it called the model. Today `summarise.empty.status` is `200` and
`summarise.empty.modelCalls` is `1`: an empty note still calls the model. That is the defect T1 fixes.

## 2. Predict the moves, before any edit

```bash
cat > ~/notes-app-practice/moves.prediction.json <<'EOF'
{
  "about": "T1: an empty or missing note gets a 400, and no model call",
  "moves": [
    {"what": "summarise.empty.status", "from": 200, "to": 400},
    {"what": "summarise.empty.body", "to": "{\"error\":\"text is required\"}"},
    {"what": "summarise.empty.modelCalls", "from": 1, "to": 0},
    {"what": "summarise.missing.status", "from": 200, "to": 400},
    {"what": "summarise.missing.body", "to": "{\"error\":\"text is required\"}"},
    {"what": "summarise.missing.modelCalls", "from": 1, "to": 0}
  ],
  "mustNotMove": ["summarise.ok.*"]
}
EOF
```

- `moves` lists the 6 outputs the fix should move, from what to what.
- `mustNotMove` says a good note's reply must stay exactly as it is. `*` matches any text.
- The prediction comes first, so the change is judged against what was intended, not against what it
  happened to do.

## 3. Make the fix, measure, and check

```bash
cat > src/api/summarise.js <<'EOF'
// POST /api/summarise: sends a note's text to an AI model and returns the model's summary.
// The model client is passed in, so a test or the measure step can mock it.
export async function handleSummarise(body, { model }) {
  if (!body.text) return { status: 400, body: { error: "text is required" } };
  const reply = await model.complete(body.text);
  return { status: 200, body: { summary: reply.text } };
}
EOF
git commit -qam "T1: the fix"
node tools/measure.mjs ~/notes-app-practice/moves.measured.json
bakehouse run predict-gate.sh check --measured ~/notes-app-practice/moves.measured.json
```

The gate finds the prediction and the baseline beside the measured file. It prints each move, and ends:

```
Keys measured: 9. Keys that moved: 6.
...
Result: PASS. Every move was predicted, and nothing in mustNotMove moved.
```

## 4. See the gate stop a change

A well-meant extra edit: "while I'm here, check the model is up before using it".

```bash
cat > src/api/summarise.js <<'EOF'
// POST /api/summarise: sends a note's text to an AI model and returns the model's summary.
// The model client is passed in, so a test or the measure step can mock it.
export async function handleSummarise(body, { model }) {
  if (!body.text) return { status: 400, body: { error: "text is required" } };
  await model.complete("ping"); // check the model is up
  const reply = await model.complete(body.text);
  return { status: 200, body: { summary: reply.text } };
}
EOF
npm test
node tools/measure.mjs ~/notes-app-practice/moves.measured.json
bakehouse run predict-gate.sh check --measured ~/notes-app-practice/moves.measured.json
```

`npm test` still passes. The gate does not:

```
5. Must not move, and it moved: 1. Each one stops the commit.
  - summarise.ok.modelCalls: 1 -> 2 (rule: summarise.ok.*).
...
Result: REFUSED.
```

Every good note now pays for two model calls instead of one. No test counts the calls, so the tests
could not see it. The measure step does, and nobody predicted it. In the real job this stops the
engineer, and the move goes to you to decide. Put the fix back:

```bash
git checkout src/api/summarise.js
```

## 5. Break the fix on purpose: mutations

Two small breaks to the committed fix. The binding's `mutation` block tells the runner to test
`src/` with `node --test test/summarise.test.js`.

```bash
cat > ~/notes-app-practice/mutants.json <<'EOF'
[
  {"id": "M1", "file": "src/api/summarise.js",
   "find": "  if (!body.text) return { status: 400, body: { error: \"text is required\" } };\n",
   "replace": "",
   "note": "the fix removed: an empty note reaches the model again"},
  {"id": "M2", "file": "src/api/summarise.js",
   "find": "body: { summary: reply.text }",
   "replace": "body: { summary: \"\" }",
   "note": "the summary dropped from a good note's reply"}
]
EOF
bakehouse run mutate.sh --mutants ~/notes-app-practice/mutants.json --record ~/notes-app-practice --round first
```

The runner copies the commit, checks the tests pass on it unbroken, then plants each break in a fresh
copy and runs the tests:

```
M1 survived src/api/summarise.js:4
M2 killed src/api/summarise.js:6
mutate: 1 killed, 1 survived, 0 timed out.
```

M2 is caught: the existing test checks a good note's summary. M1 survives: the fix can be deleted and
every test still passes, because no test sends an empty note. A survivor is a missing test. In a real
review the reviewer names each survivor, and the fix round adds the test.

## 6. Add the missing test, and break it again

```bash
cat >> test/summarise.test.js <<'EOF'

test("an empty or missing note gets a 400, and no model call", async () => {
  let calls = 0;
  const model = { complete: async () => { calls += 1; return { text: "x" }; } };
  for (const body of [{ text: "" }, {}]) {
    const res = await handleSummarise(body, { model });
    assert.equal(res.status, 400);
    assert.deepEqual(res.body, { error: "text is required" });
  }
  assert.equal(calls, 0);
});
EOF
npm test
git commit -qam "T1: a test for the empty and missing note"
cat > ~/notes-app-practice/mutants-fix1.json <<'EOF'
[
  {"id": "M3", "file": "src/api/summarise.js",
   "find": "  if (!body.text) return { status: 400, body: { error: \"text is required\" } };\n",
   "replace": "",
   "note": "M1 again, after the new test: the fix removed"}
]
EOF
bakehouse run mutate.sh --mutants ~/notes-app-practice/mutants-fix1.json --record ~/notes-app-practice --round fix1
```

`npm test` now runs 2 tests. The same break, as M3, is caught:

```
M3 killed src/api/summarise.js:4
mutate: 1 killed, 0 survived, 0 timed out
```

Each mutant gets a new id, because a record never changes a row it has written.

## 7. Clean up

```bash
git switch main
git branch -D practice
rm -rf ~/notes-app-practice
```

`main` still has the defect, so you can now run the real job on it: [README](README.md), step 4. The
engineer writes the prediction, measures and fixes; the reviewer writes the mutants; the same gate
and runner judge them. Their record folder sits in the records worktree, `~/notes-app.bakehouse-records`.
