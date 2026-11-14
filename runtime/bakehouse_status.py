"""`bakehouse status`: the public read contract (design 05 §9), the one way anything outside the tool reads
team state. Standard library only. Run through bin/bakehouse. It reads the ledgers; it writes nothing.

  bakehouse status [--json] [--since <ISO time>] [--repo <folder>]   (default folder: the current one)

The JSON object (docs/PROJECT-TEAM.md, "Reading team state"):
  format            1
  team              true
  project, records  the repo root and its records folder
  workOrders        each work order with events in the ops ledger and no work-order-closed event:
                    {workOrder, events, latestRound, latestKind, latestAt}
  waitingDecisions  each decision asked, not decided autonomously and not answered:
                    {id, category, question, askedAt}
  closedSince       each work-order-closed event at or after --since (all of them without it):
                    {workOrder, closedAt, commit, overridden}
A work-order-closed event is written by `predict-gate.sh close` only when the close passes, or fails and
the owner's override is recorded first; such a close's note starts OVERRIDDEN.

Exit 0; 3 no team here (JSON: {"format": 1, "team": false}); 4 setup needed (the locator's message on
stderr); 1 a broken ledger (named on stderr); 2 bad usage.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

import decision
import locator
import ops_ledger

USAGE = "usage: bakehouse status [--json] [--since <ISO time>] [--repo <folder>]"
CLOSED_KIND = "work-order-closed"
OVERRIDDEN = "OVERRIDDEN"  # the start of an overridden close's note (predict-gate.py run_close)


def when(ts) -> datetime.datetime | None:
    try:
        t = datetime.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.astimezone()


def broken(path: Path, ops: bool) -> str | None:
    """What is wrong with a ledger, or None when it reads whole (or does not exist yet). The ops ledger is
    judged by ops_ledger.verify, as verify-chain and close judge it; the decision ledger line by line."""
    if not path.is_file():
        return None
    if ops:
        whole, text = ops_ledger.verify(path)
        return None if whole else text.replace("\n", " ")
    for n, line in enumerate(path.read_bytes().split(b"\n"), 1):
        if line.strip() and ops_ledger.parse(line) is None:
            return f"line {n} is not a JSON object"
    return None


def state(project, since) -> dict:
    events = [ops_ledger.parse(x) for x in ops_ledger.lines_of(project.ops_ledger.read_bytes())] \
        if project.ops_ledger.is_file() else []
    by_wo, closed = {}, []
    for ev in events:
        wo = ev.get("task")
        if not wo:
            continue
        by_wo.setdefault(wo, []).append(ev)
        at = when(ev.get("ts"))
        if ev.get("kind") == CLOSED_KIND and (since is None or (at is not None and at >= since)):
            closed.append({"workOrder": wo, "closedAt": ev.get("ts"), "commit": ev.get("commit"),
                           "overridden": (ev.get("note") or "").startswith(OVERRIDDEN)})
    open_wos = []
    for wo, evs in by_wo.items():
        if any(e.get("kind") == CLOSED_KIND for e in evs):
            continue
        rounds = [e["round"] for e in evs if e.get("round")]
        open_wos.append({"workOrder": wo, "events": len(evs), "latestRound": rounds[-1] if rounds else None,
                         "latestKind": evs[-1].get("kind"), "latestAt": evs[-1].get("ts")})
    decided, _ = decision.decisions(project)
    waiting = [{"id": d["ask"]["id"], "category": d["ask"].get("category"), "question": d["ask"].get("question"),
                "askedAt": d["ask"].get("ts")}
               for d in decided.values() if not d["ask"].get("autonomous") and not d["answers"]]
    return {"format": 1, "team": True, "project": str(project.root), "records": str(project.records),
            "workOrders": open_wos, "waitingDecisions": waiting, "closedSince": closed}


def plain(s: dict, since_arg) -> str:
    out = [f"project: {s['project']}", f"records: {s['records']}", f"open work orders: {len(s['workOrders'])}"]
    out += [f"  {w['workOrder']}: {w['events']} event(s), latest {w['latestKind']} at {w['latestAt']}"
            + (f", round {w['latestRound']}" if w["latestRound"] else "") for w in s["workOrders"]]
    out.append(f"waiting decisions: {len(s['waitingDecisions'])}")
    out += [f"  {d['id']} [{d['category']}] asked {d['askedAt']}: {d['question']}" for d in s["waitingDecisions"]]
    out.append(f"closed since {since_arg}: {len(s['closedSince'])}" if since_arg else
               f"closed: {len(s['closedSince'])}")
    out += [f"  {c['workOrder']}: at {c['closedAt']}, commit {c['commit']}"
            + (" (overridden)" if c["overridden"] else "") for c in s["closedSince"]]
    return "\n".join(out)


def main(argv, root) -> int:
    as_json, since_arg, folder, args = False, None, None, list(argv)
    while args:
        a = args.pop(0)
        if a == "--json":
            as_json = True
        elif a in ("--since", "--repo") and args:
            v = args.pop(0)
            since_arg, folder = (v, folder) if a == "--since" else (since_arg, v)
        else:
            print(USAGE, file=sys.stderr)
            return 2
    since = None
    if since_arg is not None:
        since = when(since_arg)
        if since is None:
            print(f"bakehouse status: --since {since_arg!r} is not an ISO time", file=sys.stderr)
            return 2
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    try:
        project = locator.find(folder or Path.cwd(), env)
    except locator.SetupNeeded as e:
        print(f"bakehouse: {e}", file=sys.stderr)
        return locator.SETUP_NEEDED
    if project is None:
        print(json.dumps({"format": 1, "team": False}) if as_json else
              f"{folder or Path.cwd()}: no team here (no .team/binding.json)")
        return locator.NO_TEAM
    for path, ops in ((project.ops_ledger, True), (project.decisions_ledger, False)):
        why = broken(path, ops)
        if why:
            print(f"bakehouse status: the ledger {path} is BROKEN: {why}", file=sys.stderr)
            return 1
    s = state(project, since)
    print(json.dumps(s, ensure_ascii=False, indent=2) if as_json else plain(s, since_arg))
    return 0
