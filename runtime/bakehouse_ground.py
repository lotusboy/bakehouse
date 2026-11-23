"""`bakehouse ground`: which work orders changed the given files or folders lately, from git, and what
reviews found there. Standard library only. Run through bin/bakehouse. It reads git, the binding, the ops ledger and
the findings files in the run folders; it writes nothing and uses no network.

  bakehouse ground [--json] [--days N] [--not <work order>] [--repo <folder>] <path>...

Paths are relative to the repository root. A commit counts when it is no merge, is on a branch of this clone, a
remote branch it has fetched, or HEAD, and touches a code root (locator.code_roots). It belongs to the first
tracker id in its subject. A visit is one work order with any number of commits written (author date: a rebase
keeps it) in the last N days (default 14: how far back to show, not a limit anything is judged by). --not leaves one work order out of every count.

The JSON object: format (1), team, project, codeRoots, days, not, noId (the commits in the window with no tracker
id that touched any of the paths), and paths, each:
  path, inWorkingTree, inCodeRoots (false: not counted, and nothing below is given)
  workOrders              the visits, the newest first (the text shows 8)
  open                    how many of those are open now, as `bakehouse status` judges it; null when not known
  rank, of                a file with a visit: its place by visits among the files with one; ties share a place
  mostVisits              any other path: the 3 files in it with the most, [file, visits]
  everWorkOrders, firstCommit   over the whole history; firstCommit is a date (UTC), or null
  findings                only where there are any, in the code roots or not: the serious review findings (ANDON, P0,
                          P1) ever made there: count, earlier (of them, in code that BASE had), foundBy (the work
                          orders whose reviews found them, the newest first; the text shows 8)
and findingsUnread, only when there are any: the serious findings in run folders dated in the window with no Site line
that names a file git has had on one of those branches, or none. A re-check beside a finding's file that repeats its id
is the same finding: each line as the latest round that has it gives it.

In tree mode the findings files are read from the working tree and from the tip of each branch above: each file
(its run folder, its path in it) is read once, from the working tree when that folder there has it, else from the
newest tip (committer date) that holds it, by `git ls-tree` and `git cat-file`. So a fix round that only a branch
holds is read beside the working tree's first round. An open lane whose branch this clone has not fetched is not
seen. runFolders, in tree mode only: read (how many run folders were read) and onlyInGit (how many of them are not
in the working tree at all; the text gives a line when there are any). A ref that cannot be read is left out with
one stderr line naming it.

Exit 0; 1 the ops ledger is broken (the rest is printed) or git failed; 2 bad usage, or a binding without
tracker.id or code roots; 3 no team here; 4 setup needed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import bakehouse_status
import locator

_records = importlib.util.spec_from_file_location("records_check", Path(__file__).with_name("records-check.py"))
records_check = importlib.util.module_from_spec(_records)
_records.loader.exec_module(records_check)

SHOWN_IDS, SHOWN_FILES = 8, 3
RUN = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})(?![0-9])(.*)", re.S)  # its date, and the rest of its name
FENCE = re.compile(r"\s*(`{3,}(?!.*`)|~{3,})(.*)")
HEADING = re.compile(r" {0,3}(#{1,6})\s+(.*)")
FINDING = re.compile(r"[*_`(\[]*([A-Z][A-Z0-9.-]*[0-9][a-z]?)(?![A-Za-z0-9])")  # the id a finding's heading starts with
SEVERITY = re.compile(r"\b(?:ANDON|P[0-3])\b")
SERIOUS = ("ANDON", "P0", "P1")  # agents/reviewer.md, "Severity"
# a line of a finding, with any bullet, bold or backtick marks
LINE = re.compile(r"\s*(?:[0-9]+[.)])?[\s*+\-_`]*(Site|Origin)[*_`]*:(?:__\s)?(.*)", re.I)
MARKS = " \t\r*`"


def git_log(root, *args) -> list:
    """`git log -z` over the branches that count. The options undo a user's git config."""
    out = subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(root), "log", f"--exclude={locator.RECORDS_BRANCH}", "--branches",
         f"--exclude=*/{locator.RECORDS_BRANCH}", "--remotes", "HEAD", "--full-history", "--root", "--no-renames",
         "--no-show-signature", "-z", "--name-only", *args],
        capture_output=True, check=True, env={**os.environ, "GIT_NO_LAZY_FETCH": "1"}).stdout
    return out.decode("utf-8", "surrogateescape").split("\0")


def history(root, roots, rx, leave_out) -> list:
    """[(when it was written, its work order or None, the files it changed)]. --full-history: a merge that dropped a side's
    change does not hide the commit that made it."""
    commits = []
    for token in git_log(root, "--no-merges", "--format=/%at %s", "--", *roots):
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


def git_out(root, *args, data=None) -> bytes:
    """One read-only git command's output; CalledProcessError when it fails."""
    return subprocess.run(["git", "--literal-pathspecs", "-C", str(root), *args], input=data, capture_output=True,
                          check=True, env={**os.environ, "GIT_NO_LAZY_FETCH": "1"}).stdout


def ref_tips(root) -> list:
    """[(its committer date, its commit, the ref)] of the refs git_log reads, the newest first; a ref whose commit
    cannot be read is left out with one stderr line naming it."""
    refs = [line.split(" ", 1) for line in git_out(root, "for-each-ref", "--format=%(objectname) %(refname)",
                                                    "refs/heads", "refs/remotes").decode("utf-8", "surrogateescape")
            .splitlines()]
    refs = [(oid, ref) for oid, ref in refs if ref != f"refs/heads/{locator.RECORDS_BRANCH}"
            and not (ref.startswith("refs/remotes/") and ref.endswith(f"/{locator.RECORDS_BRANCH}"))]
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True)
    refs += [(head.stdout.decode().strip(), "HEAD")] if head.returncode == 0 else []
    objects = read_objects(root, list(dict.fromkeys(oid for oid, _ in refs)))
    tips = []
    for oid, ref in refs:
        when = re.search(rb"^committer .* ([0-9]+) [-+][0-9]{4}$", objects.get(oid, b""), re.M)
        if when:
            tips.append((int(when.group(1)), oid, ref))
        else:
            print(f"bakehouse ground: the ref {ref} cannot be read; its run folders are not read", file=sys.stderr)
    return sorted(tips, key=lambda t: -t[0])


def read_objects(root, oids) -> dict:
    """{object id: its content} by one `git cat-file --batch`; a missing object is left out."""
    out, objects, at = git_out(root, "cat-file", "--batch", data="".join(f"{o}\n" for o in oids).encode()), {}, 0
    for oid in oids:
        end = out.index(b"\n", at)
        head = out[at:end].split(b" ")
        if len(head) == 3:
            size = int(head[2])
            objects[oid], at = out[end + 1:end + 1 + size], end + 2 + size
        else:  # "<oid> missing"
            at = end + 1
    return objects


def git_run_folders(root, name, tree) -> dict:
    """{a run folder's name: {(its folder in the run folder, its name): its object id}}: the findings files in `name`
    on the refs git_log reads that the working tree's real (not linked) run folder of that name, under `tree`, does
    not have as a file; each from the newest ref tip (committer date) that holds it. A linked folder or file in git
    is not followed."""
    prefix, found, listed = os.path.normpath(name) + "/", {}, set()
    for _, oid, ref in ref_tips(root):
        if oid in listed:  # another ref at the same commit: nothing new
            continue
        listed.add(oid)
        try:
            listing = git_out(root, "ls-tree", "-r", "-z", "--full-tree", oid, "--", prefix)
        except subprocess.CalledProcessError:
            print(f"bakehouse ground: the ref {ref} cannot be read; its run folders are not read", file=sys.stderr)
            continue
        for entry in listing.decode("utf-8", "surrogateescape").split("\0"):
            meta, _, path = entry.partition("\t")
            if not path.startswith(prefix):
                continue
            mode, kind, blob = meta.split(" ")
            folder, _, rest = path[len(prefix):].partition("/")
            at, _, n = rest.rpartition("/")
            if not (rest and kind == "blob" and mode in ("100644", "100755") and n.startswith("findings")
                    and n.endswith(".md")) or (at, n) in found.get(folder, {}):
                continue
            here = Path(tree, folder)
            if here.is_dir() and not here.is_symlink() and Path(here, at, n).is_file():
                continue
            found.setdefault(folder, {})[at, n] = blob
    return found


def run_folders(project, binding, rx) -> tuple:
    """([(its work order, its date, the folder, its findings files read from git as git_run_folders gives them, or
    None, whether the folder is in the working tree)], (how many run folders were read, how many of them are not in
    the working tree), or None in records mode). A linked folder is not followed. In tree mode each findings file is
    read once: the working tree's copy when it has one, else the copy at the newest ref tip that holds it; a lane
    branch this clone has not fetched is not seen."""
    mode = None
    try:
        base, name, mode = locator.runs_location(project, binding)
        if mode == "tree" and locator.reviews_problem(name):
            return [], (0, 0)
        try:
            entries = list(os.scandir(Path(base) / name))
        except FileNotFoundError:
            if mode != "tree":
                raise
            entries = []
        folders = [(e.name, e.path, None, True) for e in entries if e.is_dir(follow_symlinks=False)]
    except (OSError, ValueError, locator.BadRuns):
        return [], (0, 0) if mode == "tree" else None
    if mode == "tree":
        extra = git_run_folders(project.root, name, Path(base) / name)
        folders = [(n, path, extra.get(n), True) for n, path, _, _ in folders]
        on_disk = {n for n, *_ in folders}
        folders += [(n, f"{os.path.normpath(name)}/{n}", files, False) for n, files in extra.items() if n not in on_disk]
    runs = []
    for n, path, files, disk in folders:
        m = RUN.match(n)
        wo = m and rx.search(m.group(2))
        try:
            if wo:
                runs.append((wo.group(), date.fromisoformat(m.group(1)), path, files, disk))
        except ValueError:  # no such day
            pass
    return runs, (len(runs), sum(not r[4] for r in runs)) if mode == "tree" else None


def findings_in(text, wo) -> list:
    """One findings file: [{id, severity, and its first site and origin lines, as written}]."""
    found, now, level, fence = [], None, 0, None
    for line in text.split("\n"):
        mark, head = FENCE.match(line), HEADING.match(line)
        if fence:
            if mark and mark.group(1).startswith(fence) and not mark.group(2).strip():
                fence = None
        elif mark:
            fence = mark.group(1)
        elif head:
            depth, title = len(head.group(1)), head.group(2)
            m = FINDING.match(title)
            severity = m and (SEVERITY.search(title, m.end()) or SEVERITY.search(m.group(1)))
            if severity and not SEVERITY.fullmatch(m.group(1)) and m.group(1) != wo:  # its own id: a title
                now, level = {"id": m.group(1), "severity": severity.group()}, depth
                found.append(now)
            elif depth <= level:
                now = None
        elif now is not None and (m := LINE.match(line)):
            now.setdefault(m.group(1).lower(), m.group(2).strip(MARKS))
    return found


def serious_findings(project, runs) -> list:
    """[(its work order, its run folder's date, its site: a path, "" for none, or None when not readable, earlier)]."""
    latest = {}  # (work order, date, the findings file's folder, id): a finding
    wanted = [oid for *_, files, _ in runs if files for oid in files.values()]
    blobs = read_objects(project.root, list(dict.fromkeys(wanted))) if wanted else {}  # one git call for them all
    for wo, day, folder, files, disk in runs:
        walk = {at: dict.fromkeys(names) for at, _, names in os.walk(folder)} if disk else {}  # None: read the disk
        for (a, n), oid in (files or {}).items():
            walk.setdefault(os.path.join(folder, a) if a else folder, {})[n] = blobs.get(oid, b"")
        for at, data_of in walk.items():
            rounds = [(re.fullmatch(r"findings-fix([0-9]+)\.md", n), n) for n in data_of
                      if n.startswith("findings") and n.endswith(".md")]
            for _, n in sorted((int(m.group(1)) if m else 0, n) for m, n in rounds):
                try:
                    data = data_of[n] if data_of[n] is not None else (
                        Path(at, n).read_bytes() if Path(at, n).is_file() else b"")  # never a pipe
                except OSError:
                    continue
                for f in findings_in(data.decode("utf-8-sig", "surrogateescape"), wo):
                    latest[wo, day, at, f["id"]] = {**latest.get((wo, day, at, f["id"]), {}), **f}
    serious = [(key, f) for key, f in latest.items() if f["severity"] in SERIOUS]
    had = set(git_log(project.root, "-c", "--format=")) if serious else set()  # -c: a merge's own files too
    return [(key[0], key[1], site(f.get("site", ""), had), bool(re.match(r"earlier\b", f.get("origin", ""), re.I)))
            for key, f in serious]


def site(text, had):
    """The path a Site line names: the line as written, else up to a backtick, a line number, or a space."""
    for cut in ("$", "`", ":", r"\s"):
        path = os.path.normpath(re.split(cut, text, maxsplit=1)[0].rstrip(MARKS + ",;.:") or ".")
        if path in had:
            return path
    return "" if re.split(r"[\s*`,;.:]", text, maxsplit=1)[0].lower() == "none" else None


def at_path(path, serious):
    """The `findings` of one path, or None."""
    mine = [f for f in serious if f[2] and under(f[2], path)]
    last = {}  # work order: the date of its newest run folder that found one here
    for wo, day, _, _ in mine:
        last[wo] = max(day, last.get(wo, day))
    return {"count": len(mine), "earlier": sum(f[3] for f in mine),
            "foundBy": sorted(last, key=lambda wo: (last[wo], len(wo), wo), reverse=True)} if mine else None


def under(file, path) -> bool:
    return path in (".", file) or file.startswith(path + "/")


def facts(path, root, roots, commits, start, open_ids) -> dict:
    known = {"path": path, "inWorkingTree": os.path.lexists(root / path),
             "inCodeRoots": any(under(path, r) or under(r, path) for r in map(os.path.normpath, roots))}
    if not known["inCodeRoots"]:
        return known
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
    return {**known,
            "workOrders": sorted(last, key=last.get, reverse=True),
            "open": None if open_ids is None else len(open_ids & last.keys()),
            "rank": 1 + sum(n > count[path] for n in count.values()) if ranked else None,
            "of": len(count) if ranked else None,
            "mostVisits": [] if ranked else inside[:SHOWN_FILES],
            "everWorkOrders": len({wo for _, wo, _ in mine if wo}),
            "firstCommit": first}


def ordinal(n) -> str:
    return f"{n}{'th' if n % 100 in (11, 12, 13) else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def shown(ids) -> str:
    return ", ".join(ids[:SHOWN_IDS]) + (f" and {len(ids) - SHOWN_IDS} more" if len(ids) > SHOWN_IDS else "")


def visits_text(p, s, lately) -> list:
    if not p["inCodeRoots"]:
        return [f"  outside the code roots: not counted{' for work orders' if 'findings' in p else ''}"]
    if not p["inWorkingTree"] and not p["firstCommit"]:
        return ["  no history, and not in the working tree"]
    line = f"  work orders, {lately}: {len(p['workOrders'])}"
    if p["workOrders"]:
        line += f" ({shown(p['workOrders'])}), "
        line += "open not known" if p["open"] is None else f"{p['open']} open now"
    out = [line + (f"; {ordinal(p['rank'])} of {p['of']} files changed" if p["rank"] else "")]
    if p["mostVisits"]:
        out.append("  most visits: " + ", ".join(f"{f} {n}" for f, n in p["mostVisits"]))
    return [*out, f"  work orders, ever: {p['everWorkOrders']}, "
            + (f"first commit {p['firstCommit']}" if p["firstCommit"] else f"no {'other ' if s['not'] else ''}commits")]


def plain(s) -> str:
    lately = f"last {s['days']} days"
    out = [f"only the code roots count{' for work orders' if any('findings' in p for p in s['paths']) else ''}: "
           + ", ".join(s["codeRoots"]) + (f"; without {s['not']}" if s["not"] else "")]
    for p in s["paths"]:
        out += [p["path"], *visits_text(p, s, lately)]
        f = p.get("findings")
        if f:
            earlier = f", {f['earlier']} of them in earlier code" if f["earlier"] else ""
            out.append(f"  serious review findings, ever: {f['count']}{earlier} (found by {shown(f['foundBy'])})")
    if s["noId"]:
        out.append(f"commits with no work-order id, {lately}: {s['noId']}")
    if s.get("findingsUnread"):
        out.append(f"serious review findings, {lately}, with no Site line that can be read: {s['findingsUnread']}")
    if s.get("runFolders", {}).get("onlyInGit"):
        out.append(f"run folders read: {s['runFolders']['read']} ({s['runFolders']['onlyInGit']} only in git, "
                   "not in this working tree)")
    return "\n".join(out)


def prepare(ap, a, flag="--not", prog="bakehouse ground"):
    """(project, binding, its id search, its code roots, the paths relative to its root) for a.repo, a.paths and
    a.leave_out (given as `flag`), or the exit code once it has said why (bakehouse door reads the same)."""
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
    full = [project.root / p for p in a.paths]  # realpath: by way of a symlinked folder too
    paths = [os.path.relpath(os.path.join(os.path.realpath(p.parent), p.name), project.root) for p in full]
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
        print(f"{prog}: {project.binding}: {e}", file=sys.stderr)
        return 2
    if a.leave_out is not None and not rx.fullmatch(a.leave_out):
        ap.error(f"{flag} {a.leave_out} is not a work-order id of this project")
    return project, binding, rx, roots, paths


def gather(project, binding, rx, roots, paths, days, leave_out, until=None) -> tuple:
    """(the summary `ground --json` prints, the serious findings, the run folders, why open work orders are not
    known or None). until (seconds since the epoch, default now): the window ends there; commits written after it
    and run folders dated after its day are left out. CalledProcessError when git fails."""
    commits = history(project.root, roots, rx, leave_out)
    runs, read = run_folders(project, binding, rx)
    if until is not None:
        commits = [c for c in commits if c[0] <= until]
        runs = [r for r in runs if r[1] <= date.fromtimestamp(until)]
        read = read and (len(runs), sum(not r[4] for r in runs))
    serious = serious_findings(project, [r for r in runs if r[0] != leave_out])
    open_ids, why = open_work_orders(project)
    start = (time.time() if until is None else until) - days * 86400
    s = {"format": 1, "team": True, "project": str(project.root), "codeRoots": roots, "days": days,
         "not": leave_out, "paths": [facts(p, project.root, roots, commits, start, open_ids) for p in paths],
         "noId": sum(not wo and at >= start and any(under(f, p) for f in files for p in paths)
                     for at, wo, files in commits)}
    for p in s["paths"]:
        found = at_path(p["path"], serious)
        if found:
            p["findings"] = found
    if read is not None:
        s["runFolders"] = {"read": read[0], "onlyInGit": read[1]}
    unread = sum(where is None and day >= date.fromtimestamp(start) for _, day, where, _ in serious)
    if unread:
        s["findingsUnread"] = unread
    return s, serious, runs, why


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
    found = prepare(ap, a)
    if isinstance(found, int):
        return found
    try:
        s, _, _, why = gather(*found, a.days, a.leave_out)
    except subprocess.CalledProcessError as e:
        print(f"bakehouse ground: git log failed: {os.fsdecode(e.stderr).strip()}", file=sys.stderr)
        return 1
    sys.stdout.reconfigure(errors="surrogateescape")  # a file name that is not UTF-8
    print(json.dumps(s, indent=2) if a.json else plain(s))
    if why:
        print(f"bakehouse ground: {why}; open work orders are not known", file=sys.stderr)
    return 1 if why else 0
