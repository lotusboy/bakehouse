#!/usr/bin/env python3
"""Measured spend: the token count the harness reported for each agent, grouped into waves with a
ceiling. Pure computation; no AI. Called through runtime/spend.sh.

Why it exists: an agent's own report of its token use was off by up to 2x (2026-10-10). The figure
the harness puts in the completion notification (`subagent_tokens`) is the measured one, so that is
the figure recorded here, and `source` says when a row is anything less.

Ledger: <records>/spend/ledger.jsonl, append-only, a sibling of decisions/ledger.jsonl. It is not
the ops ledger, because a wave can run in any project, owned repo or not. The project is found by
runtime/locator.py.
Event kinds:
  wave-open   wave, ceiling
  wave-close  wave
  agent       agent, label, tokens, wave, kind, source, task, model, note, corrects,
              why, predicted, predictedTokens, result, outcome
Every first record of an agent states the role it ran as (`--kind`), why that tool was chosen, what was
predicted (result and tokens), what happened and the outcome (owner, 2026-09-27 and 2026-10-14: track tool
use in results, not only stats). A run that went worse or failed, or whose tokens are off the prediction
by more than half, gets a stub entry in <records>/TOOLS.md for the lesson to be written.
A wrong agent row is never edited: `record --correct` appends a new row for the same agent id, and
the latest row for an agent id is the one that counts.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import locator  # noqa: E402



def kinds():
    """The spend kinds: the roles of the shipped role table, runtime/agent-roles.json, plus its
    `spend_kinds` (kinds in use that are not roles). One list, kept in that file. Returns (every kind,
    the kinds a new first record may not take: a spend kind marked "newRecords": false)."""
    table = json.loads((Path(__file__).resolve().parent / "agent-roles.json").read_text(encoding="utf-8"))
    extra = table["spend_kinds"]
    return (tuple(sorted(set(table["roles"]) | set(extra))),
            frozenset(k for k, v in extra.items() if v.get("newRecords") is False))


KINDS, OLD_ROWS_ONLY = kinds()
SOURCES = ("harness", "self-reported", "estimate")
OUTCOMES = ("as-predicted", "better", "worse", "failed")
WARN, STOP = 0.8, 1.0


_PROJECT = None  # the project resolve_project found, for the refusal event
_TASK = None


def fail(msg, gate=None):
    """Refuse: exit 1 with the message. A refusal with a gate id first writes one gate-refused event to
    the project's ops ledger (docs/PROJECT-TEAM.md, "Gate refusals"); a failed write changes nothing."""
    if gate and _PROJECT:
        try:
            import gate_event
            gate_event.refused(gate, "spend.sh refused", f"spend: {msg}", task=_TASK, path=_PROJECT.root)
        except Exception:  # noqa: BLE001, S110 - the event reports a refusal; it never changes one
            pass
    sys.exit(f"spend: {msg}")


def stamp():
    return os.environ.get("SPEND_TS") or datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def resolve_project(repo):
    """The project of --repo, else of the current folder (runtime/locator.py)."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"} if repo else os.environ
    try:
        project = locator.find(repo, env)
    except locator.SetupNeeded as e:
        fail(str(e))
    if project is None:
        fail(f"{repo or os.getcwd()} has no team (no .team/binding.json); pass --repo")
    global _PROJECT
    _PROJECT = project
    return project


def name_of(project):
    """The project's short name, for a row, a heading or a message (its repository folder's name)."""
    return project.root.name


def read_events(project):
    p = project.spend_ledger
    if not p.exists():
        return []
    rows = []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"spend: {p} line {n} is not JSON; skipped", file=sys.stderr)
    return rows


def append(project, event):
    p = project.spend_ledger
    p.parent.mkdir(exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def tokens_arg(v):
    s = v.replace(",", "").replace("_", "")
    if not s.isdigit() or int(s) <= 0:
        raise argparse.ArgumentTypeError(f"'{v}' is not a whole number of tokens above 0")
    return int(s)


# ---------- state ----------

def waves(events):
    """name -> {ceiling, opened, closed} in the order they were opened."""
    out = {}
    for e in events:
        if e["event"] == "wave-open":
            out[e["wave"]] = {"ceiling": e["ceiling"], "opened": e["ts"], "closed": None}
        elif e["event"] == "wave-close" and e["wave"] in out:
            out[e["wave"]]["closed"] = e["ts"]
    return out


def open_waves(events):
    return [n for n, w in waves(events).items() if w["closed"] is None]


def agents(events):
    """The latest row per agent id: a correction replaces the row it corrects."""
    latest = {}
    for e in events:
        if e["event"] == "agent":
            latest[e["agent"]] = e
    return list(latest.values())


def wave_summary(events, name):
    w = waves(events)[name]
    rows = [a for a in agents(events) if a.get("wave") == name]
    spent = sum(a["tokens"] for a in rows)
    frac = spent / w["ceiling"]
    level = "over" if frac >= STOP else "warn" if frac >= WARN else "ok"
    return {"wave": name, "ceiling": w["ceiling"], "spent": spent, "fraction": round(frac, 4), "level": level,
            "open": w["closed"] is None, "opened": w["opened"], "closed": w["closed"],
            "agents": [{k: a.get(k) for k in ("agent", "label", "kind", "tokens", "source", "ts")} for a in rows]}


def flag(s):
    pct = f"{s['fraction'] * 100:.0f}%"
    if s["level"] == "over":
        return f"STOP: wave {s['wave']} has spent {s['spent']:,} of its {s['ceiling']:,} ceiling ({pct})."
    if s["level"] == "warn":
        return f"WARNING: wave {s['wave']} has spent {s['spent']:,} of its {s['ceiling']:,} ceiling ({pct}); 80% reached."
    return None


def bar(frac, width=20):
    filled = min(width, int(frac * width))
    return "[" + "#" * filled + "." * (width - filled) + "]"


# ---------- commands ----------

def cmd_wave(a):
    project = resolve_project(a.repo)
    label = name_of(project)
    ev = read_events(project)
    known = waves(ev)
    if a.action == "open":
        if not a.name:
            fail("wave open needs a name", "spend:wave-args")
        if a.ceiling is None:
            fail("wave open needs --ceiling <tokens>", "spend:wave-args")
        if a.name in known:
            fail(f"wave {a.name} already exists in {label} (opened {known[a.name]['opened']}); pick a new name",
                 "spend:wave-exists")
        append(project, {"ts": stamp(), "event": "wave-open", "wave": a.name, "ceiling": a.ceiling})
        print(f"wave {a.name} open in {label}: ceiling {a.ceiling:,} tokens")
        return
    name = a.name
    if not name:
        live = open_waves(ev)
        if len(live) != 1:
            fail(f"{len(live)} waves are open in {label}; name the one to close", "spend:wave-ambiguous")
        name = live[0]
    if name not in known:
        fail(f"no wave {name} in {label}", "spend:wave-unknown")
    if known[name]["closed"]:
        fail(f"wave {name} was already closed at {known[name]['closed']}", "spend:wave-closed")
    append(project, {"ts": stamp(), "event": "wave-close", "wave": name})
    s = wave_summary(read_events(project), name)
    print(f"wave {name} closed in {label}: spent {s['spent']:,} of {s['ceiling']:,} "
          f"({s['fraction'] * 100:.0f}%) across {len(s['agents'])} agents")


def cmd_headroom(a):
    """Before a dispatch: does the wave's spend plus the agent's estimate fit its ceiling? Writes nothing."""
    project = resolve_project(a.repo)
    ev = read_events(project)
    if a.wave not in waves(ev):
        fail(f"no wave {a.wave} in {name_of(project)}", "spend:wave-unknown")
    closed = waves(ev)[a.wave]["closed"]
    if closed:
        fail(f"wave {a.wave} was closed at {closed}; no agent spends from it", "spend:wave-closed")
    s = wave_summary(ev, a.wave)
    total = s["spent"] + a.est
    if total > s["ceiling"]:
        fail(f"wave {a.wave}: {s['spent']:,} spent + {a.est:,} estimated is over the ceiling of {s['ceiling']:,}"
             f" by {total - s['ceiling']:,}; {max(0, s['ceiling'] - s['spent']):,} left", "spend:headroom")
    print(f"wave {a.wave}: {s['spent']:,} spent + {a.est:,} estimated fits the ceiling of {s['ceiling']:,};"
          f" {s['ceiling'] - total:,} left after it")


def mirror_to_ops(project, a):
    """One write for the PM: an agent-run row in the repo ops ledger, when this project keeps one."""
    try:
        owned = json.loads(project.binding.read_text()).get("owned") is True
    except (OSError, json.JSONDecodeError):
        owned = False
    if not owned:
        return "ops ledger: not written (this project keeps no ops ledger)"
    note = f"spend.sh: agent={a.agent} kind={a.kind} source={a.source}" + (f" wave={a.wave}" if a.wave else "")
    if a.note:
        note += f"; {a.note}"
    tokens = str(a.tokens) if a.source == "harness" else "-"
    p = subprocess.run([str(HERE / "ops-event.sh"), "agent-run", a.task or "-", a.label,
                        a.model or "-", "-", "-", tokens, "-", "done", note],
                       capture_output=True, text=True, env=dict(os.environ, OPS_REPO=str(project.root)))
    if p.returncode != 0:
        return f"ops ledger: NOT written ({p.stderr.strip()}); the spend row stands"
    return "ops ledger: agent-run row written"


def cmd_record(a):
    project = resolve_project(a.repo)
    label = name_of(project)
    ev = read_events(project)
    if os.environ.get("SPEND_TS") and not a.note:
        fail("a backfilled row (SPEND_TS set) needs --note naming where the figure came from", "spend:backfill-note")
    known = waves(ev)
    if a.wave:
        if a.wave not in known:
            fail(f"no wave {a.wave} in {label}; open it first: spend.sh wave open {a.wave} --ceiling <n>",
                 "spend:wave-unknown")
    else:
        live = open_waves(ev)
        if len(live) > 1:
            fail(f"{len(live)} waves are open in {label} ({', '.join(live)}); pass --wave", "spend:wave-ambiguous")
        a.wave = live[0] if live else None
    seen = {r["agent"] for r in agents(ev)}
    if a.agent in seen and not a.correct:
        fail(f"agent {a.agent} is already recorded in {label}; pass --correct to replace its row",
             "spend:already-recorded")
    if a.correct and a.agent not in seen:
        fail(f"--correct given but agent {a.agent} has no row in {label} to correct", "spend:correct-no-row")
    # The gate is on the record: spend.sh runs after an agent finishes, so it cannot stop the run
    # itself. It stops a second architect run on one task being recorded as if nothing happened
    # first, and the PM role makes the step-back come before the dispatch.
    if a.kind == "architect" and not a.correct and not (a.task or "").strip():
        fail("an architect run names its task: give --task (the gate on a second run for one task needs it)",
             "spend:architect-task")
    if a.kind == "architect" and a.task and not a.correct:
        earlier = [r for r in agents(ev) if r.get("kind") == "architect"
                   and (r.get("task") or "").lower() == a.task.lower()]
        if earlier and len(re.findall(r"[^\W\d_]{2,}", a.stepped_back or "")) < 3:
            fail(f"a second architect run for {a.task} ({len(earlier)} before): give --stepped-back, the"
                 " step-back done before dispatching it (owner, 2026-10-15)", "spend:stepped-back")
    if not a.correct and not os.environ.get("SPEND_TS"):
        missing = [f for f in ("why", "predicted", "predicted_tokens", "result", "outcome")
                   if not str(getattr(a, f) if getattr(a, f) is not None else "").strip()]
        if missing:
            fail("a first record states the tool's result, not only its cost: give "
                 + ", ".join("--" + m.replace("_", "-") for m in missing), "spend:first-record")
    if a.kind is None and not a.correct and not os.environ.get("SPEND_TS"):
        fail("a first record names the role the agent ran as: give --kind, one of "
             + ", ".join(k for k in KINDS if k not in OLD_ROWS_ONLY)
             + " (runtime/agent-roles.json: its roles and spend_kinds)", "spend:kind-required")
    earlier = [r for r in agents(ev) if r["agent"] == a.agent]
    kept = earlier[-1].get("kind") if a.correct and earlier else None
    if a.kind is None:
        # a correction keeps the kind of the row it corrects; a backfilled row with none is "agent"
        a.kind = kept or "agent"
    if a.kind in OLD_ROWS_ONLY and a.kind != kept and not os.environ.get("SPEND_TS"):
        fail(f"--kind {a.kind} names no role, so a new record may not take it (it stays on old rows, backfilled "
             "rows and corrections that keep it): give the role the agent ran as, one of "
             + ", ".join(k for k in KINDS if k not in OLD_ROWS_ONLY), "spend:kind-not-a-role")
    row = {"ts": stamp(), "event": "agent", "agent": a.agent, "label": a.label, "tokens": a.tokens,
           "wave": a.wave, "kind": a.kind, "source": a.source, "task": a.task, "model": a.model,
           "note": a.note, "corrects": bool(a.correct), "backfilled": bool(os.environ.get("SPEND_TS")),
           "why": a.why, "predicted": a.predicted, "predictedTokens": a.predicted_tokens,
           "result": a.result, "outcome": a.outcome, "steppedBack": getattr(a, "stepped_back", None)}
    append(project, row)
    if needs_lesson(a):
        print(stub_lesson(project, a))
    print(f"recorded {a.agent} ({a.label}): {a.tokens:,} tokens, {a.source}"
          + (f", wave {a.wave}" if a.wave else ", no wave open"))
    if a.wave:
        s = wave_summary(read_events(project), a.wave)
        print(f"wave {a.wave}: {bar(s['fraction'])} {s['spent']:,} / {s['ceiling']:,} ({s['fraction'] * 100:.0f}%)")
        msg = flag(s)
        if msg:
            print(msg, file=sys.stderr)
    if not a.no_ops and not a.correct and not a.repo:
        print(mirror_to_ops(project, a))


def needs_lesson(a):
    if a.outcome is None:
        return False
    off = a.predicted_tokens and abs(a.tokens - a.predicted_tokens) > a.predicted_tokens / 2
    return a.outcome in ("worse", "failed") or bool(off)


def stub_lesson(project, a):
    path = project.tool_lessons
    if not path.exists():
        return f"TOOLS.md: {name_of(project)} has none; no lesson stub written"
    entry = (f"\n### {stamp()[:10]} — {a.task or '-'} — {a.label} ({a.kind}, {a.agent})\n"
             f"- **Why this tool:** {a.why}\n"
             f"- **Predicted:** {a.predicted}; {a.predicted_tokens:,} tokens.\n"
             f"- **Actual:** {a.result}; {a.tokens:,} tokens ({a.source}); outcome {a.outcome}.\n"
             f"- **Lesson:** TODO — what to do differently with this tool, or the tool this shows we need.\n")
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)
    return f"TOOLS.md: lesson stub added for {a.agent} ({a.outcome}); write the lesson before the next dispatch"


def cmd_status(a):
    project = resolve_project(a.repo)
    label = name_of(project)
    ev = read_events(project)
    names = list(waves(ev)) if a.all else open_waves(ev)
    if a.wave:
        if a.wave not in waves(ev):
            fail(f"no wave {a.wave} in {label}", "spend:wave-unknown")
        names = [a.wave]
    out = [wave_summary(ev, n) for n in names]
    if a.json:
        print(json.dumps({"stream": label, "waves": out}, indent=2, ensure_ascii=False))
        return
    if not out:
        print(f"{label}: no {'' if a.all else 'open '}waves. Open one: spend.sh wave open <name> --ceiling <tokens>")
        return
    for s in out:
        state = "open" if s["open"] else f"closed {s['closed']}"
        print(f"wave {s['wave']} ({state}) {bar(s['fraction'])} {s['spent']:,} / {s['ceiling']:,} "
              f"({s['fraction'] * 100:.0f}%), {s['ceiling'] - s['spent']:,} left")
        for r in s["agents"]:
            mark = "" if r["source"] == "harness" else f"  [{r['source']}, not measured]"
            print(f"  {r['tokens']:>10,}  {r['kind']:<15} {r['agent']}  {r['label']}{mark}")
        msg = flag(s)
        if msg:
            print("  " + msg)


def cmd_report(a):
    project = resolve_project(a.repo)
    label = name_of(project)
    rows = [r for r in agents(read_events(project)) if not a.since or r["ts"][:10] >= a.since]
    by = {"day": defaultdict(int), "wave": defaultdict(int), "kind": defaultdict(int), "source": defaultdict(int)}
    for r in rows:
        by["day"][r["ts"][:10]] += r["tokens"]
        by["wave"][r.get("wave") or "(no wave)"] += r["tokens"]
        by["kind"][r["kind"]] += r["tokens"]
        by["source"][r["source"]] += r["tokens"]
    total = sum(r["tokens"] for r in rows)
    if a.json:
        print(json.dumps({"stream": label, "since": a.since, "agents": len(rows), "total": total,
                          **{k: dict(sorted(v.items())) for k, v in by.items()}}, indent=2, ensure_ascii=False))
        return
    print(f"{label}: {total:,} tokens across {len(rows)} agents" + (f" since {a.since}" if a.since else ""))
    for k in ("day", "wave", "kind", "source"):
        print(f"by {k}:")
        for name, n in sorted(by[k].items()):
            print(f"  {n:>12,}  {name}")


def main():
    p = argparse.ArgumentParser(prog="spend.sh", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="record one finished agent's measured token count")
    r.add_argument("--agent", required=True, help="the agent id from the completion notification")
    r.add_argument("--label", required=True, help="a short name for what it did")
    r.add_argument("--tokens", required=True, type=tokens_arg, help="subagent_tokens from the notification")
    r.add_argument("--wave", help="default: the one open wave, if exactly one is open")
    r.add_argument("--kind", choices=KINDS, help="the role the agent ran as; required on a first record")
    r.add_argument("--source", choices=SOURCES, default="harness",
                   help="harness = the notification's figure (default); anything else is flagged as not measured")
    r.add_argument("--task", help="tracker id the agent served")
    r.add_argument("--model")
    r.add_argument("--note")
    r.add_argument("--why", help="why this tool was chosen over the alternatives")
    r.add_argument("--predicted", help="the result predicted before the run")
    r.add_argument("--predicted-tokens", type=tokens_arg, help="the tokens predicted before the run")
    r.add_argument("--result", help="what actually happened, measured")
    r.add_argument("--outcome", choices=OUTCOMES)
    r.add_argument("--stepped-back", help="a second architect run for --task: the step-back done first")
    r.add_argument("--correct", action="store_true", help="replace this agent's earlier row (appends; never edits)")
    r.add_argument("--no-ops", action="store_true", help="do not also write the repo ops ledger's agent-run row")
    r.add_argument("--repo", help="the project's repository folder; default: the current folder")
    r.set_defaults(fn=cmd_record)

    w = sub.add_parser("wave", help="open or close a wave")
    w.add_argument("action", choices=("open", "close"))
    w.add_argument("name", nargs="?")
    w.add_argument("--ceiling", type=tokens_arg)
    w.add_argument("--repo", help="the project's repository folder; default: the current folder")
    w.set_defaults(fn=cmd_wave)

    s = sub.add_parser("status", help="spent vs ceiling per wave, per agent, with 80%%/100%% warnings")
    s.add_argument("--wave")
    s.add_argument("--all", action="store_true", help="closed waves too")
    s.add_argument("--json", action="store_true")
    s.add_argument("--repo", help="the project's repository folder; default: the current folder")
    s.set_defaults(fn=cmd_status)

    h = sub.add_parser("headroom", help="exit 0 when the wave's spend plus an agent's estimate fits its ceiling")
    h.add_argument("wave")
    h.add_argument("--est", required=True, type=tokens_arg, help="the next agent's estimated tokens")
    h.add_argument("--repo", help="the project's repository folder; default: the current folder")
    h.set_defaults(fn=cmd_headroom)

    t = sub.add_parser("report", help="totals by day, wave, kind and source")
    t.add_argument("--since", help="YYYY-MM-DD, inclusive")
    t.add_argument("--json", action="store_true")
    t.add_argument("--repo", help="the project's repository folder; default: the current folder")
    t.set_defaults(fn=cmd_report)

    a = p.parse_args()
    global _TASK
    _TASK = getattr(a, "task", None)
    a.fn(a)


if __name__ == "__main__":
    main()
