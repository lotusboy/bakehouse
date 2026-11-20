"""`bakehouse status`: the public read contract (design 05 §9), the one way anything outside the tool reads
team state. Standard library only. Run through bin/bakehouse. It reads the ledgers; it writes nothing.

  bakehouse status [--json] [--since <ISO time>] [--repo <folder>]   (default folder: the current one)

The JSON object (docs/PROJECT-TEAM.md, "Reading team state"):
  format            1
  team              true
  project, records  the repo root and its records folder
  workOrders        each open work order: one with a sealed event in the ops ledger and no
                    work-order-closed event after it (a work order with no seal is not open):
                    {workOrder, events, latestRound, latestKind, latestAt}
  waitingDecisions  each decision asked, not decided autonomously and not answered:
                    {id, category, question, askedAt}
  unattended        whether unattended mode is on: {on, since, why, wave} (null since, why, wave when off)
  assumedToConfirm  each decision whose latest answer is an assumed one, for the owner to confirm or reverse:
                    {id, question, confidence, estTokens}
  closedSince       each work-order-closed event at or after --since (all of them without it):
                    {workOrder, closedAt, commit, overridden}
  sync              records format 2: {upstream, waiting, lastFailure}: the records branch's upstream
                    ("<remote>/<branch>", or null), the local records commits not on it (from local refs, so only as
                    current as the last sync), and the last sync failure {at, line} (null after a sync ends level);
                    null in format 1 (runtime/records_sync.py)
A work-order-closed event is written by `predict-gate.sh close` only when the close passes, or fails and
the owner's override is recorded first; such a close's note starts OVERRIDDEN.

Exit 0; 3 no team here (JSON: {"format": 1, "team": false}); 4 setup needed (the locator's message on
stderr); 1 a broken ledger, a line status cannot read, or an "unattended" settings block of a shape the tools
cannot use (named on stderr); 2 bad usage, a --repo that is not a folder inside a git repository included.
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
import records_sync

USAGE = "usage: bakehouse status [--json] [--since <ISO time>] [--repo <folder>]"
CLOSED_KIND = "work-order-closed"
SEALED_KIND = "sealed"  # as predict-gate.py's seal writes it
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


def last(kinds, kind) -> int:
    return len(kinds) - 1 - kinds[::-1].index(kind)


class Unreadable(Exception):
    """A ledger line that reads whole but that status cannot use: (the ledger, the error)."""


def ops_rows(path: Path) -> list:
    return [ops_ledger.parse(x) for x in ops_ledger.lines_of(path.read_bytes())] if path.is_file() else []


def decision_rows(path: Path) -> list:
    """One decision ledger file's events, as decision.read_events reads each line (a line that is not JSON is
    skipped)."""
    rows = []
    for line in (path.read_text(encoding="utf-8").splitlines() if path.is_file() else []):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def culprit(files, rows, part) -> Path:
    """The ledger file a line status cannot use is in: the first of files whose own events part cannot use, else
    the first file (the line is unusable only beside another file's lines)."""
    for f in files:
        try:
            part(rows(f))
        except Exception:  # noqa: BLE001 - the same failure, looked for one file at a time
            return f
    return files[0]


def ops_part(events, since):
    """(open work orders, closed since) from the ops ledger's events in read order."""
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
        kinds = [e.get("kind") for e in evs]
        if SEALED_KIND not in kinds:
            continue  # no seal: not open
        if CLOSED_KIND in kinds and last(kinds, CLOSED_KIND) > last(kinds, SEALED_KIND):
            continue  # closed after its latest seal
        rounds = [e["round"] for e in evs if e.get("round")]
        open_wos.append({"workOrder": wo, "events": len(evs), "latestRound": rounds[-1] if rounds else None,
                         "latestKind": evs[-1].get("kind"), "latestAt": evs[-1].get("ts")})
    return open_wos, closed


def decision_part(project, events):
    """(waiting decisions, unattended mode, assumed answers to confirm) from the decision ledger's events."""
    decided = decision.fold(events)
    waiting = [{"id": d["ask"]["id"], "category": d["ask"].get("category"), "question": d["ask"].get("question"),
                "askedAt": d["ask"].get("ts")}
               for d in decided.values() if not d["ask"].get("autonomous") and not d["answers"]]
    away = decision.unattended(project, events)
    assumed = [{"id": d["ask"]["id"], "question": d["ask"].get("question"),
                "confidence": d["ask"].get("confidence"), "estTokens": d["answers"][-1].get("estTokens")}
               for d in decided.values() if d["answers"] and d["answers"][-1].get("assumed")]
    return waiting, away, assumed


def state(project, since) -> dict:
    # each ledger's files in read order (locator.ledger_files): the ledger itself, then each writer's file
    files = project.read_files("ops_ledger")
    try:
        open_wos, closed = ops_part([e for f in files for e in ops_rows(f)], since)
    except Exception as e:  # noqa: BLE001 - any line shape status cannot use is reported, never a traceback
        raise Unreadable(culprit(files, ops_rows, lambda evs: ops_part(evs, since)), e) from e
    files = project.read_files("decisions_ledger")
    try:
        waiting, away, assumed = decision_part(project, decision.read_events(project))
    except decision.SettingsError:
        raise  # the project's settings, not the ledger: main names the file
    except Exception as e:  # noqa: BLE001
        raise Unreadable(culprit(files, decision_rows, lambda evs: decision_part(project, evs)), e) from e
    return {"format": 1, "team": True, "project": str(project.root), "records": str(project.records),
            "workOrders": open_wos, "waitingDecisions": waiting, "closedSince": closed,
            "unattended": away, "assumedToConfirm": assumed, "sync": records_sync.status(project.records)}


def plain(s: dict, since_arg) -> str:
    out = [f"project: {s['project']}", f"records: {s['records']}", f"open work orders: {len(s['workOrders'])}"]
    out += [f"  {w['workOrder']}: {w['events']} event(s), latest {w['latestKind']} at {w['latestAt']}"
            + (f", round {w['latestRound']}" if w["latestRound"] else "") for w in s["workOrders"]]
    out.append(f"waiting decisions: {len(s['waitingDecisions'])}")
    out += [f"  {d['id']} [{d['category']}] asked {d['askedAt']}: {d['question']}" for d in s["waitingDecisions"]]
    u = s["unattended"]
    out.append(f"unattended mode: on since {u['since']} ({u['why']})" + (f", wave {u['wave']}" if u["wave"] else "")
               if u["on"] else "unattended mode: off")
    out.append(f"assumed, to confirm: {len(s['assumedToConfirm'])}")
    out += [f"  {d['id']} confidence {d['confidence']}, estimate "
            + (f"{d['estTokens']:,}" if isinstance(d["estTokens"], int) else "-") + f" tokens: {d['question']}"
            for d in s["assumedToConfirm"]]
    out.append(f"closed since {since_arg}: {len(s['closedSince'])}" if since_arg else
               f"closed: {len(s['closedSince'])}")
    out += [f"  {c['workOrder']}: at {c['closedAt']}, commit {c['commit']}"
            + (" (overridden)" if c["overridden"] else "") for c in s["closedSince"]]
    y = s["sync"]
    if y and y["waiting"]:
        f = y["lastFailure"]
        out.append(f"records sync: {y['waiting']} commit(s) waiting to be pushed to {y['upstream']}"
                   + (f"; last failure at {f.get('at')}: {f.get('line')}" if f else ""))
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
    if folder is not None and (not Path(folder).is_dir() or locator.toplevel(folder) is None):
        print(f"bakehouse status: --repo {folder} is not a folder inside a git repository", file=sys.stderr)
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
    for path, ops in [(f, True) for f in project.read_files("ops_ledger")] + \
            [(f, False) for f in project.read_files("decisions_ledger")]:
        why = broken(path, ops)
        if why:
            print(f"bakehouse status: the ledger {path} is BROKEN: {why}", file=sys.stderr)
            return 1
    try:
        s = state(project, since)
    except Unreadable as e:
        print(f"bakehouse status: the ledger {e.args[0]} cannot be read: a line has a shape status cannot use "
              f"({e.args[1]!r})", file=sys.stderr)
        return 1
    except decision.SettingsError as e:
        print(f"bakehouse status: {e}", file=sys.stderr)
        return 1
    print(json.dumps(s, ensure_ascii=False, indent=2) if as_json else plain(s, since_arg))
    return 0
