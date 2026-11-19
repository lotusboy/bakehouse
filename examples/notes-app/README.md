# notes-app: an example project to try bakehouse on

A made-up web app, ready for a first `/bakehouse:change` job. It is the project the
[getting-started guide](../../docs/GETTING-STARTED.md) describes, with every file the guide writes already
written. Try bakehouse here before you use it on your own work.

- The app: `POST /api/summarise` sends a note's text to an AI model and returns the model's summary
  (`src/api/summarise.js`). There is no real model: the test and the measure step pass in a mocked one.
- The defect: an empty or missing note still calls the model and returns 200. Work order T1 in
  `docs/TRACKER.md` asks for a 400 and no model call.
- The team files: `.team/binding.json`, `.team/gates.json` and `tools/measure.mjs`. The guide explains
  each one, in steps 3 to 6.

You need Claude Code, Node.js 18 or later, Python 3.10 or later and Git 2.42 or later.

## 1. Make your own copy, with an origin

Copy this folder out of a clone of the bakehouse repo. Then make it a git repo with a remote `origin`.
A local bare repo works as the origin, so you need no account anywhere.

```bash
git clone https://github.com/lotusboy/bakehouse.git
cp -R bakehouse/examples/notes-app ~/notes-app
cd ~/notes-app
git init -q -b main
git add -A
git commit -q -m "notes-app: the example, before T1"
git init -q --bare -b main ~/notes-app-origin.git
git remote add origin ~/notes-app-origin.git
git push -q -u origin main
git remote set-head origin --auto
npm test
```

`npm test` passes: 1 test.

## 2. Install the plugin in this project only

Follow [README, "Install"](../../README.md#install), steps 1 and 2, in `~/notes-app`. Then commit and push
`.claude/settings.json`:

```bash
git add .claude/settings.json
git commit -q -m "Switch on bakehouse for this project"
git push -q
```

## 3. Run setup and preflight

Open a Claude Code session in `~/notes-app`. Run, from the Bash tool:

```bash
bakehouse setup
bakehouse run team-preflight.py change T1
bakehouse status
```

- Setup makes the records worktree `~/notes-app.bakehouse-records`, on the branch `bakehouse-records`.
- Preflight prints `ok` on its last line.
- Status shows 0 open work orders.

Before the real job, you can see the gates by hand: [WALKTHROUGH.md](WALKTHROUGH.md) shows a prediction
checked, a change stopped, and a mutation that finds a missing test. About 15 minutes, no AI.

## 4. Run the first change

In the same session:

```
/bakehouse:change T1 --dry-run
/bakehouse:change T1
```

- The dry run checks the work order and fills the briefs. It changes nothing.
- The real run uses real agents (an engineer, then a reviewer). The session states each agent's token
  estimate and waits for your go before it dispatches one.
- The engineer predicts the moves first: `summarise.empty` and `summarise.missing` go to 400 with no
  model call, and `summarise.ok.*` must not move. Any move it did not predict stops the job.
- There is no pull-request tool for a local origin. At the end, the session gives you the branch name
  and the pull request text. Merge the branch into `main` yourself and push `main`; the session then
  closes the work order.

What each step does: [the guide, step 9](../../docs/GETTING-STARTED.md#9-run-the-first-change).

## 5. Clean up

```bash
cd ~/notes-app
claude plugin uninstall bakehouse@bakehouse --scope project
cd ~
rm -rf ~/notes-app ~/notes-app.bakehouse-records ~/notes-app-origin.git
```
