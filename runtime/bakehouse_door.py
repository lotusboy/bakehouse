"""`bakehouse door`: one look at an area's history before a work order starts, and the route it gives: fast, normal
or look-around. Standard library only. Run through bin/bakehouse. It reads what `bakehouse ground` reads (by its own
functions) and the decision ledger's asks (decision.sh's ledger, by the same locator); it writes nothing and uses no
network.

  bakehouse door [--json] [--task <id>] [--as-of <ISO time>] [--repo <folder>] <path>...

--task leaves that work order out of every count, as `ground --not` does. The window is the setting `days`.

look-around when any one of these holds (a work order "here" changed the paths in the window):
  look-visits     lookVisits or more other work orders here
  look-findings   serious review findings on the paths (ever, as ground counts them), found by lookFindingOrders or
                  more different work orders
  look-fix-round  a work order here reached fix round lookFixRound or more: the highest --fix-round of its asks, or
                  the highest N of a findings-fixN.md in its run folders
  look-altitude   a work order here had an altitude trigger on an ask: layer out, owners 2 or more, or a cluster
fast when none of those holds and all of these do:
  fast-files      fastMaxFiles or fewer files in the paths (the files git tracks under each; a path git does not
                  track counts as one)
  fast-visits     fastMaxVisits or fewer other work orders here
  fast-findings   no serious finding on the paths in a run folder dated in the window, and none in the window
                  with no Site line that can be read (ground's findingsUnread)
  fast-fix-round  no work order here with any fix round
  fast-counted    every path is in the code roots: ground counts nothing outside them, so such a path has no history
                  the door can read and is never fast (printed only when it fails). A path under the code roots that
                  is not in the working tree yet (a new file, or a misspelt one) counts as one file with no history.
A look-around also gives look-covered when a work order here, or the one at the door, has an architect run in the
spend ledger recorded in the window: one architect look per area, so the PM reads that report first.
normal otherwise. The reasons printed are the look-around rules that hold; for normal, the fast rules that fail; for
fast, the fast rules. The six settings are under "door" in runtime/decision-config.json; a project overrides them in
.team/decision-config.json.

--as-of <ISO time> (no offset: local time): the window ends there; commits written after it, run folders dated after
its day and asks recorded after it are left out, so a past work order's door can be replayed. It cannot undo: a
findings file written or rewritten after that time in a run folder dated on or before its day (a later fix round's
findings-fixN.md included); a commit whose author date is earlier than when it was written; a branch deleted since;
how many work orders are open now; the settings as they are now; the files the paths hold now.

The JSON object: format (1), team, project, route, reasons (each: rule, setting, threshold, figure, workOrders),
settings, asOf (null when not given), task, and ground (the summary `ground --json` gives for the same paths).

Exit codes as `bakehouse ground`: 0; 1 the ops ledger is broken (the rest is printed) or git failed; 2 bad usage, a
binding without tracker.id or code roots, or a door setting that is not a whole number; 3 no team here; 4 setup
needed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime

import bakehouse_ground as ground
import decision
import spend

SETTINGS = ("days", "lookVisits", "lookFindingOrders", "lookFixRound", "fastMaxFiles", "fastMaxVisits")
FIX = re.compile(r"findings-fix([0-9]+)\.md")


def settings(project):
    """The "door" settings, merged as decision.sh merges its config; ValueError when one is not a whole number."""
    cfg = decision.config(project).get("door")
    if not isinstance(cfg, dict):
        raise ValueError('"door" is not an object')
    unknown = sorted(set(cfg) - set(SETTINGS))
    if unknown:
        raise ValueError(f'"door" has no setting {", ".join(unknown)} (the settings: {", ".join(SETTINGS)})')
    for k in SETTINGS:
        v = cfg.get(k)
        if not isinstance(v, int) or isinstance(v, bool) or v < (1 if k in ("days", "lookVisits", "lookFixRound",
                                                                               "lookFindingOrders") else 0):
            raise ValueError(f'"door".{k} is {v!r}, not a whole number in range')
    if cfg["fastMaxVisits"] >= cfg["lookVisits"]:
        raise ValueError(f'"door".fastMaxVisits ({cfg["fastMaxVisits"]}) must be below lookVisits ({cfg["lookVisits"]})')
    return {k: cfg[k] for k in SETTINGS}


def asks(project, until):
    """The decision ledger's asks; with until, only those recorded at or before it (an ask whose time cannot be read
    is then left out)."""
    out = [e for e in decision.read_events(project) if e.get("kind") == "ask"]
    if until is None:
        return out
    return [e for e in out if decision.when(e.get("ts")) and decision.when(e.get("ts")).timestamp() <= until]


def fix_rounds(runs, events) -> dict:
    """{work order (lower case): the highest fix round seen}: from asks' fixRound and from findings-fixN.md names."""
    out = {}

    def seen(wo, n):
        if wo and isinstance(n, int) and not isinstance(n, bool) and n > 0:
            out[wo.lower()] = max(n, out.get(wo.lower(), 0))
    for e in events:
        seen(str(e.get("task") or ""), e.get("fixRound"))
    for wo, _, folder, files, disk in runs:
        names = [n for _, _, ns in os.walk(folder) for n in ns] if disk else []
        for n in names + [n for _, n in (files or {})]:
            m = FIX.fullmatch(n)
            if m:
                seen(wo, int(m.group(1)))
    return out


def altitude(events, here) -> list:
    """The work orders here with an ask that recorded an altitude trigger (layer out, owners 2+, or a cluster)."""
    hit = {str(e.get("task") or "").lower() for e in events if decision.fires(e, "layer") or decision.fires(e, "owners")
           or "altitude-cluster" in (e.get("triggers") or [])}
    return [wo for wo in here if wo.lower() in hit]


def looks(project, candidates, start, until) -> list:
    """The candidates (work orders here, and the one at the door) with an architect run in the spend ledger recorded
    in the window: one look per area, so a look-around there reads that report before asking for another."""
    want = {w.lower(): w for w in candidates if w}
    seen = {}
    for r in spend.read_events(project):
        w = want.get(str(r.get("task") or "").lower())
        t = decision.when(r.get("ts")) if r.get("kind") == "architect" and w else None
        if t is not None and start <= t.timestamp() <= (until if until is not None else float("inf")):
            seen[w] = max(t.timestamp(), seen.get(w, 0))
    return sorted(seen, key=seen.get, reverse=True)


def file_count(root, paths) -> int:
    total = 0
    for p in paths:
        n = ground.git_out(root, "ls-files", "-z", "--", p).count(b"\0")
        total += n or 1
    return total


def route(cfg, here, finders, recent, rounds, alt, files, uncounted=(), unread=0, covered=()):
    """(the route, its reasons)."""
    def reason(rule, setting, figure, wos):
        return {"rule": rule, "setting": setting, "threshold": cfg[setting] if setting else None, "figure": figure,
                "workOrders": wos}
    top = max((rounds.get(w.lower(), 0) for w in here), default=0)
    at_top = [w for w in here if rounds.get(w.lower(), 0) == top] if top else []
    with_round = [w for w in here if rounds.get(w.lower(), 0)]
    look = [r for ok, r in (
        (len(here) >= cfg["lookVisits"], reason("look-visits", "lookVisits", len(here), here)),
        (len(finders) >= cfg["lookFindingOrders"], reason("look-findings", "lookFindingOrders", len(finders), finders)),
        (top >= cfg["lookFixRound"], reason("look-fix-round", "lookFixRound", top, at_top)),
        (bool(alt), reason("look-altitude", None, len(alt), alt))) if ok]
    if look:
        if covered:
            look.append(reason("look-covered", None, len(covered), list(covered)))
        return "look-around", look
    fast = [(files <= cfg["fastMaxFiles"], reason("fast-files", "fastMaxFiles", files, [])),
            (len(here) <= cfg["fastMaxVisits"], reason("fast-visits", "fastMaxVisits", len(here), here)),
            (not recent and not unread, reason("fast-findings", None, len(recent) + unread, recent)),
            (not with_round, reason("fast-fix-round", None, top, with_round))]
    if uncounted:  # printed only when it fails: a path ground does not count has no history the door can read
        fast.append((False, reason("fast-counted", None, len(uncounted), [])))
    if all(ok for ok, _ in fast):
        return "fast", [r for _, r in fast]
    return "normal", [r for ok, r in fast if not ok]


def said(r, days, uncounted=(), unread=0) -> str:
    wos = f" ({ground.shown(r['workOrders'])})" if r["workOrders"] else ""
    setting = f" [{r['setting']} {r['threshold']}]" if r["setting"] else ""
    n = r["figure"]
    text = {
        "look-visits": f"{n} other work orders here in {days} days{wos}",
        "look-findings": f"serious review findings on these paths found by {n} work orders{wos}",
        "look-fix-round": f"{', '.join(r['workOrders'])} reached fix round {n}",
        "look-altitude": f"an altitude trigger on an ask of {n} work order{'s' if n != 1 else ''} here{wos}",
        "fast-files": f"{n} file{'s' if n != 1 else ''} in the paths",
        "fast-visits": f"{n} other work order{'s' if n != 1 else ''} here in {days} days{wos}",
        "fast-findings": (f"serious review findings in {days} days: {n}{wos}"
                          + (f", {unread} of them with no Site line that can be read" if unread else "")
                          if n else f"no serious review finding in {days} days"),
        "fast-fix-round": f"a fix round here{wos}" if n else "no fix round here",
        "look-covered": f"covered by an architect look in {days} days{wos}: read its report before any new check",
        "fast-counted": f"{n} path{'s' if n != 1 else ''} not counted (outside the code roots): {', '.join(uncounted)}",
    }[r["rule"]]
    return text + setting


def main(argv, root) -> int:
    ap = argparse.ArgumentParser(prog="bakehouse door", allow_abbrev=False,
                                 description="The route a work order on these paths takes: fast, normal or look-around.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--task", dest="leave_out", metavar="WORK_ORDER", help="the work order at the door: left out")
    ap.add_argument("--as-of", dest="as_of", metavar="ISO_TIME", help="replay: read nothing after this time")
    ap.add_argument("--repo", help="default: the current folder")
    ap.add_argument("paths", nargs="+", metavar="path", help="relative to the repository root")
    a = ap.parse_intermixed_args(argv)
    until = None
    if a.as_of is not None:
        t = decision.when(a.as_of)
        if t is None:
            ap.error(f"--as-of {a.as_of} is not an ISO time")
        until = t.timestamp()
    found = ground.prepare(ap, a, "--task", "bakehouse door")
    if isinstance(found, int):
        return found
    project, _, _, _, paths = found
    try:
        cfg = settings(project)
    except (OSError, ValueError, AttributeError) as e:  # json.JSONDecodeError is a ValueError; AttributeError: a
        # project config that is not an object (decision.merge)
        print(f"bakehouse door: the door settings ({project.decision_config} over the defaults): {e}", file=sys.stderr)
        return 2
    try:
        s, serious, runs, why = ground.gather(*found, cfg["days"], a.leave_out, until)
        files = file_count(project.root, paths)
    except subprocess.CalledProcessError as e:
        print(f"bakehouse door: git failed: {os.fsdecode(e.stderr).strip()}", file=sys.stderr)
        return 1
    events = [e for e in asks(project, until) if str(e.get("task") or "").lower() != (a.leave_out or "").lower()]
    here = list(dict.fromkeys(wo for p in s["paths"] for wo in p.get("workOrders", [])))
    mine = [f for f in serious if f[2] and any(ground.under(f[2], p) for p in paths)]
    finders = list(dict.fromkeys(f[0] for f in sorted(mine, key=lambda f: f[1], reverse=True)))
    start = date.fromtimestamp((until if until is not None else datetime.now().timestamp()) - cfg["days"] * 86400)
    recent = list(dict.fromkeys(f[0] for f in sorted(mine, key=lambda f: f[1], reverse=True) if f[1] >= start))
    rounds = fix_rounds([r for r in runs if r[0] in here], [e for e in events if e.get("task")])
    uncounted = [p["path"] for p in s["paths"] if not p.get("inCodeRoots")]
    unread = s.get("findingsUnread") or 0
    now = until if until is not None else datetime.now().timestamp()
    covered = looks(project, here + [a.leave_out or ""], now - cfg["days"] * 86400, until)
    way, reasons = route(cfg, here, finders, recent, rounds, altitude(events, here), files, uncounted, unread, covered)
    out = {"format": 1, "team": True, "project": str(project.root), "route": way, "reasons": reasons,
           "settings": cfg, "asOf": a.as_of, "task": a.leave_out, "ground": s}
    sys.stdout.reconfigure(errors="surrogateescape")  # a file name that is not UTF-8
    print(json.dumps(out, indent=2) if a.json else
          f"door: {way}: " + "; ".join(said(r, cfg["days"], uncounted, unread) for r in reasons))
    if why:
        print(f"bakehouse door: {why}; open work orders are not known", file=sys.stderr)
    return 1 if why else 0
