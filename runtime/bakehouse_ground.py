"""`bakehouse ground`: which work orders changed the given files or folders lately, from git. Standard library only.
Run through bin/bakehouse. It reads git, the binding and the ops ledger; it writes nothing and uses no network.

  bakehouse ground [--json] [--days N] [--not <work order>] [--repo <folder>] <path>...

Paths are relative to the repository root. A commit counts when it is no merge, is on a branch of this clone, a
remote branch it has fetched, or HEAD, and touches a code root (locator.code_roots). It belongs to the first
tracker id in its subject. A visit is one work order with any number of commits in the last N days (default 14:
how far back to show, not a limit anything is judged by). --not leaves one work order out of every count.

The JSON object: format (1), team, project, codeRoots, days, not, noId (the commits in the window with no tracker
id that touched any of the paths), and paths, each:
  path, inWorkingTree
  workOrders              the visits, the newest first (the text shows 8)
  open                    how many of those are open now, as `bakehouse status` judges it; null when not known
  rank, of                a file with a visit: its place by visits among the files with one; ties share a place
  mostVisits              any other path: the 3 files in it with the most, [file, visits]
  everWorkOrders, firstCommit   over the whole history; firstCommit is a date (UTC), or null

Exit 0; 1 the ops ledger is broken (the rest is printed) or git failed; 2 bad usage, or a binding without
tracker.id or code roots; 3 no team here; 4 setup needed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import bakehouse_status
import locator

_records = importlib.util.spec_from_file_location("records_check", Path(__file__).with_name("records-check.py"))
records_check = importlib.util.module_from_spec(_records)
_records.loader.exec_module(records_check)

SHOWN_IDS, SHOWN_FILES = 8, 3


def history(root, roots, rx, leave_out) -> list:
    """[(commit time, its work order or None, the files it changed)]. --full-history: a merge that dropped a side's
    change does not hide the commit that made it. The other options undo a user's git config."""
    out = subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(root), "log", f"--exclude={locator.RECORDS_BRANCH}", "--branches",
         f"--exclude=*/{locator.RECORDS_BRANCH}", "--remotes", "HEAD", "--no-merges", "--full-history", "--root",
         "--no-renames", "--no-show-signature", "-z", "--name-only", "--format=/%ct %s", "--", *roots],
        capture_output=True, check=True, env={**os.environ, "GIT_NO_LAZY_FETCH": "1"}).stdout
    commits = []
    for token in out.decode("utf-8", "surrogateescape").split("\0"):
        if token.startswith("/"):  # a commit: git never prints a file name that starts with a slash
            at, subject = token[1:].split(" ", 1)
            m, files = rx.search(subject), []
            commits.append((int(at), m and m.group(), files))
        elif token:
            files.append(token if files else token.removeprefix("\n"))
    return [c for c in commits if leave_out is None or c[1] != leave_out]


def open_work_orders(project):
    """(the ids, None), or (None, why they are not known)."""
    files = project.read_files("ops_ledger")
    for f in files:
        why = bakehouse_status.broken(f, True)
        if why:
            return None, f"the ledger {f} is BROKEN: {why}"
    try:
        rows = [e for f in files for e in bakehouse_status.ops_rows(f)]
        return {w["workOrder"] for w in bakehouse_status.ops_part(rows, None)[0]}, None
    except Exception as e:  # noqa: BLE001 - any line shape status cannot use, as status reports it
        return None, f"the ledger {files[0]} holds a line status cannot use ({e!r})"


def under(file, path) -> bool:
    return path in (".", file) or file.startswith(path + "/")


def facts(path, root, commits, start, open_ids) -> dict:
    mine = [c for c in commits if any(under(f, path) for f in c[2])]
    first = datetime.fromtimestamp(min(c[0] for c in mine), timezone.utc).strftime("%Y-%m-%d") if mine else None
    last = {}  # work order: its latest commit here in the window
    for at, wo, _ in mine:
        if wo and at >= start:
            last[wo] = max(at, last.get(wo, at))
    visits = {}  # file: its work orders in the window
    for at, wo, files in commits:
        for f in files if wo and at >= start else ():
            visits.setdefault(f, set()).add(wo)
    count = {f: len(wos) for f, wos in visits.items()}
    ranked = path in count
    inside = [[f, n] for f, n in sorted(count.items(), key=lambda x: (-x[1], x[0])) if under(f, path)]
    return {"path": path, "inWorkingTree": os.path.lexists(root / path),
            "workOrders": sorted(last, key=last.get, reverse=True),
            "open": None if open_ids is None else len(open_ids & last.keys()),
            "rank": 1 + sum(n > count[path] for n in count.values()) if ranked else None,
            "of": len(count) if ranked else None,
            "mostVisits": [] if ranked else inside[:SHOWN_FILES],
            "everWorkOrders": len({wo for _, wo, _ in mine if wo}),
            "firstCommit": first}


def ordinal(n) -> str:
    return f"{n}{'th' if n % 100 in (11, 12, 13) else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def plain(s) -> str:
    lately = f"last {s['days']} days"
    out = [f"code roots: {', '.join(s['codeRoots'])}" + (f"; without {s['not']}" if s["not"] else "")]
    for p in s["paths"]:
        out.append(p["path"])
        if not p["inWorkingTree"] and not p["firstCommit"]:
            out.append("  no history, and not in the working tree")
            continue
        line, ids = f"  work orders, {lately}: {len(p['workOrders'])}", p["workOrders"]
        if ids:
            more = f" and {len(ids) - SHOWN_IDS} more" if len(ids) > SHOWN_IDS else ""
            line += f" ({', '.join(ids[:SHOWN_IDS])}{more}), "
            line += "open not known" if p["open"] is None else f"{p['open']} open now"
        out.append(line + (f"; {ordinal(p['rank'])} of {p['of']} files" if p["rank"] else ""))
        if p["mostVisits"]:
            out.append("  most visits: " + ", ".join(f"{f} {n}" for f, n in p["mostVisits"]))
        out.append(f"  work orders, ever: {p['everWorkOrders']}, "
                   + (f"first commit {p['firstCommit']}" if p["firstCommit"] else "no commits"))
    if s["noId"]:
        out.append(f"commits with no work-order id, {lately}: {s['noId']}")
    return "\n".join(out)


def main(argv, root) -> int:
    ap = argparse.ArgumentParser(prog="bakehouse ground", allow_abbrev=False,
                                 description="Which work orders changed these files or folders lately.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--days", type=int, default=14, help="how far back to show (default 14)")
    ap.add_argument("--not", dest="leave_out", metavar="WORK_ORDER", help="leave this one out of every count")
    ap.add_argument("--repo", help="default: the current folder")
    ap.add_argument("paths", nargs="+", metavar="path", help="relative to the repository root")
    a = ap.parse_intermixed_args(argv)
    if a.days < 1:
        ap.error("--days is 1 or more")
    if a.repo is not None and (not Path(a.repo).is_dir() or locator.toplevel(a.repo) is None):
        ap.error(f"--repo {a.repo} is not a folder inside a git repository")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    try:
        project = locator.find(a.repo or Path.cwd(), env)
    except locator.SetupNeeded as e:
        print(f"bakehouse: {e}", file=sys.stderr)
        return locator.SETUP_NEEDED
    if project is None:
        print(json.dumps({"format": 1, "team": False}) if a.json else
              f"{a.repo or Path.cwd()}: no team here (no .team/binding.json)")
        return locator.NO_TEAM
    paths = [os.path.relpath(os.path.normpath(project.root / p), project.root) for p in a.paths]
    for given, p in zip(a.paths, paths):
        if p.split(os.sep)[0] == "..":
            ap.error(f"{given} is outside the repository {project.root}")
    try:
        binding = json.loads(project.binding.read_text(encoding="utf-8"))
        if not (isinstance(binding, dict) and isinstance(binding.get("tracker"), dict)
                and isinstance(binding["tracker"].get("id"), str) and binding["tracker"]["id"]):
            raise ValueError("the binding has no tracker.id")
        rx, roots = records_check.id_search(binding["tracker"]["id"]), locator.code_roots(binding)
        if not roots:
            raise ValueError("the binding has no code roots (signal.codeRoots or records.codeRoots)")
    except (OSError, ValueError, records_check.BadInput) as e:
        print(f"bakehouse ground: {project.binding}: {e}", file=sys.stderr)
        return 2
    if a.leave_out is not None and not rx.fullmatch(a.leave_out):
        ap.error(f"--not {a.leave_out} is not a work-order id of this project")
    try:
        commits = history(project.root, roots, rx, a.leave_out)
    except subprocess.CalledProcessError as e:
        print(f"bakehouse ground: git log failed: {os.fsdecode(e.stderr).strip()}", file=sys.stderr)
        return 1
    open_ids, why = open_work_orders(project)
    start = time.time() - a.days * 86400
    s = {"format": 1, "team": True, "project": str(project.root), "codeRoots": roots, "days": a.days,
         "not": a.leave_out, "paths": [facts(p, project.root, commits, start, open_ids) for p in paths],
         "noId": sum(not wo and at >= start and any(under(f, p) for f in files for p in paths)
                     for at, wo, files in commits)}
    sys.stdout.reconfigure(errors="surrogateescape")  # a file name that is not UTF-8
    print(json.dumps(s, indent=2) if a.json else plain(s))
    if why:
        print(f"bakehouse ground: {why}; open work orders are not known", file=sys.stderr)
    return 1 if why else 0
