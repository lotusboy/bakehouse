"""`bakehouse door`: one look at an area's history before a work order starts, and the route it gives: fast, normal
or look-around. Standard library only. Run through bin/bakehouse. It reads what `bakehouse ground` reads (by its own
functions) and the decision ledger's asks (decision.sh's ledger, by the same locator); it uses no network. It writes
one thing: with --task, the work order's door event (below).

  bakehouse door [--json] [--task <id>] [--as-of <ISO time>] [--prediction <moves file>] [--repo <folder>] [<path>...]

With --task and no paths, the paths are the work order's own `ground:` line in the tracker file (binding
tracker.file): the item runs from its `id:` field line to the next one, and its first `ground:` line counts. The
working tree's copy is read first, else the newest branch tip that has the item with a line. No line: exit 2, naming
it, so the line is written before the door runs.

--task leaves that work order out of every count, as `ground --not` does. The window is the setting `days`.

A work order "here" changed the paths in the window. A troubled work order is a work order here that, inside the
window, asked a fix round of 2 or more (the highest --fix-round of its asks, or the highest N of a findings-fixN.md in
its run folders), or has a run folder with a serious review finding. Inside the window: the ask recorded in the
window; the run folder dated in the window.

look-around when any one of these holds:
  look-visits     lookVisits or more troubled work orders here
  look-findings   serious review findings on the paths in run folders dated in the window, found by lookFindingOrders
                  or more different work orders (ground itself still shows findings from any time)
  look-fix-round  a work order here reached fix round lookFixRound or more in the window
  look-altitude   a work order here had an altitude trigger on an ask: layer out, owners 2 or more, or a cluster
fast when none of those holds and all of these do:
  fast-files      fastMaxFiles or fewer files in the paths (the files git tracks under each; a path git does not
                  track counts as one)
  fast-visits     fastMaxVisits or fewer troubled work orders here (a serious finding or a fix round 2 or more in
                  the window makes a work order troubled, so this rule covers them; ground's findingsUnread does not
                  stop fast: it is in --json under ground, and in ground's own text)
  fast-findings   no serious review finding on the paths in a run folder dated in the window, by any work order,
                  here or not (printed only when it fails)
  fast-counted    every path is in the code roots: ground counts nothing outside them, so such a path has no history
                  the door can read and is never fast (printed only when it fails). A path under the code roots that
                  is not in the working tree yet (a new file, or a misspelt one) counts as one file with no history.
  fast-limit      no path is a hard-limit path: none matches a glob of the binding's careLimits.paths, and none holds a
                  tracked file that does (repository-relative globs; ** matches any depth, * and ? stay inside one
                  folder). A failed fast-limit names the path and the glob. A hard limit is never fast.
  fast-binding    the binding here has main's careLimits (printed only when it fails; see "The binding compared
                  with main's" below). Different hard limits, or careLimits on one side only, are never fast.
An architect look resets the area's count. A look is an architect run in the spend ledger, recorded in the window,
by a work order here or the one at the door; T is the latest. With a look, trouble counts only from T, for the look
rules and the fast rules alike: asks recorded at or after T (fix rounds, the altitude rule), run folders dated on or
after T's day (fix rounds from findings-fixN.md, troubled work orders, finders). The work orders here, the files, the
hard limits, the binding check and --prediction are read as without a look. A look-around then also gives
look-covered, with since (T): one architect look per area, so the PM reads that report first. With a look, the
look-visits and fast-visits text says "since <T>" where it says "in <days> days" without one. When the reset changed
the route (without it the route would be higher: look-around, or normal where it is now fast), the reasons gain
reset (printed, never failing), naming the look's work order and since (T). JSON's countedFrom is T (local time,
with its offset), or null with no look.
normal otherwise. The reasons printed are the look-around rules that hold; for normal, the fast rules that fail; for
fast, the fast rules. The six settings are under "door" in runtime/decision-config.json; a project overrides them in
.team/decision-config.json.

--prediction <moves file> (the predicted-moves gate's moves.prediction.json; JSON only): when the route would be fast
and the "what" key of one of its moves matches a glob of the binding's careLimits.predictionKeys (* any text, ? one
character), the route is normal, with the reason raised-prediction naming the key and the glob. A "what" that holds
* or ? is a pattern and could move a limited key, so it raises too, naming the first glob. A prediction with no moves
(noMoves, or no "moves" key) raises nothing. It never lowers a route and never raises one to look-around. A
prediction file that cannot be read is exit 2.

The binding's careLimits is optional: {"paths": [<glob>...], "predictionKeys": [<glob>...]}. A careLimits that is not
an object, a key other than paths and predictionKeys, or a list that is not a list of non-empty strings, is exit
2, naming the key. A folder is written folder/** (a bare folder name matches only a path of that exact name).

The binding compared with main's: the door reads the binding of the folder it runs in (--repo). It compares that
binding, top-level key by top-level key, with .team/binding.json as committed on branch B in the project's main clone
(the records worktree's repository, so a lane clone whose origin/main is old still compares with the main clone's
own branch), read with git show and never checked out, fetched or written. B is the binding's mainFollows.branch
when the main clone has that local branch, else main when it has a local branch main, else master when it has one
(a mainFollows that is malformed or names no local branch falls back to main and master: the binding under
suspicion does not get to turn the comparison off). Values are compared as JSON writes them (1 and true differ). A key
on one side only differs. When any key differs, the text gains a second line, `door: warning: the binding here
differs from main's (<clone> <B>) in: <keys>`; when careLimits is one of them, fast-binding fails. No main clone, no
such branch, no binding committed there, or one that does not parse: the comparison is not made, JSON says why
(bindingDrift.notMade), the text says nothing and fast is not stopped by it. It never changes a look-around or a
normal route, nor the exit code. When fast-binding is the only fast rule that fails and --prediction would raise
fast, the reasons are raised-prediction then fast-binding. An uncommitted edit to the binding on main's own checkout differs too.

--as-of <ISO time> (no offset: local time): the window ends there; commits written after it, run folders dated after
its day and asks recorded after it are left out, so a past work order's door can be replayed. It cannot undo: a
findings file written or rewritten after that time in a run folder dated on or before its day (a later fix round's
findings-fixN.md included); a commit whose author date is earlier than when it was written; a branch deleted since;
how many work orders are open now; the settings as they are now; the files the paths hold now; the binding
compared with main's (it is about the binding now, so --as-of does not change it).

The JSON object: format (1), team, project, route, reasons (each: rule, setting, threshold, figure, workOrders;
fast-limit also limits, raised-prediction also key and pattern, fast-binding also against, look-covered and reset
also since),
settings, asOf (null when not given), task, bindingDrift ({"against": {"clone", "branch"} or null, "keys": [the
differing top-level keys, sorted], "notMade": null or why the comparison was not made}), recorded, kept, countedFrom,
and ground (the summary `ground --json` gives for the same paths).

The door event: with --task, and without --as-of, in a repository whose binding says "owned": true, the door
appends one ops event through runtime/ops-event.sh: kind door, task the work order, purpose door, outcome the route,
note the reasons in one line (the text after "door: <route>: "), cut to gate_event.NOTE_CHARS. The text is printed
first, then the event written; with --json the event is written first, so the JSON can say whether it was. With
--prediction too, so the latest door event is the route. A work order keeps the route it started with: when the
door gives a route lower than the work order's latest door event (fast < normal < look-around: a PM's raise, an
earlier look-around), it records that latest route again, the note starting "kept <route>: the door now gives
<lower>; a work order keeps the route it started with", and the text prints that line after the first
("door: kept ..."); JSON's "kept" is that route, else null. The first line's route is still the door's own. A higher
route (a rise at --prediction, new trouble) is recorded as it is. Paths alone (no work order), a replay (--as-of)
and a binding that is not owned (it keeps no ledger) record nothing and print no line about it. A write that fails
changes neither the route printed nor the exit code: one stderr line says why and gives the ops-event.sh line, its
note shell-quoted, that records it by hand. JSON's "recorded" says whether it was written (false when nothing was to
be recorded).

Exit codes as `bakehouse ground`: 0; 1 the ops ledger is broken (the rest is printed) or git failed; 2 bad usage, a
binding without tracker.id or code roots, or a door setting that is not a whole number; 3 no team here; 4 setup
needed.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
from datetime import date, datetime

import bakehouse_ground as ground
import bakehouse_status
import decision
import gate_event
import locator
import spend

SETTINGS = ("days", "lookVisits", "lookFindingOrders", "lookFixRound", "fastMaxFiles", "fastMaxVisits")
FIX = re.compile(r"findings-fix([0-9]+)\.md")
HERE = Path(__file__).resolve().parent
WRITE_S = 10  # the door event's write: a writer that hangs is cut, and the route stands
RANK = {"fast": 0, "normal": 1, "look-around": 2}  # a work order keeps the highest: never recorded lower


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


def looks(project, candidates, start, until) -> dict:
    """{candidate: the time of its latest look}, latest first: the candidates (work orders here, and the one at the
    door) with an architect run in the spend ledger recorded in the window. One look per area: trouble counts from
    the latest one (the reset), and a look-around there reads that report before asking for another."""
    want = {w.lower(): w for w in candidates if w}
    seen = {}
    for r in spend.read_events(project):
        w = want.get(str(r.get("task") or "").lower())
        t = decision.when(r.get("ts")) if r.get("kind") == "architect" and w else None
        if t is not None and start <= t.timestamp() <= (until if until is not None else float("inf")):
            seen[w] = max(t.timestamp(), seen.get(w, 0))
    return {w: seen[w] for w in sorted(seen, key=seen.get, reverse=True)}


def item_ground(text, wo):
    """The paths of the `ground:` line of the item whose `id:` field is wo, or None: the item runs from its `id:` line
    to the next `id:` line; its first `ground:` line counts. Quoted paths (" or `) are unquoted."""
    field = re.compile(r"^[\s>*\-]*id:\s*`?([^\s`]+)`?\s*$", re.I)
    mine = False
    for line in text.splitlines():
        m = field.match(line)
        if m:
            mine = m.group(1).lower() == wo.lower()
            continue
        g = re.match(r"^[\s>*\-]*ground:\s*(.*)$", line, re.I) if mine else None
        if g:
            raw = g.group(1).replace("`", '"')
            try:
                words = shlex.split(raw)
            except ValueError:
                words = raw.replace('"', " ").split()
            return [w for w in words if w] or None
    return None


def ground_line(project, binding, wo):
    """(the paths of wo's `ground:` line, where it was read), or (None, why): the tracker file in the working tree
    first, else the newest branch tip (committer date) whose copy has the item with a line."""
    tracker = binding.get("tracker", {}).get("file") if isinstance(binding.get("tracker"), dict) else None
    if not isinstance(tracker, str) or not tracker:
        return None, "the binding has no tracker.file, so a work order's ground: line cannot be read: give the paths"
    try:
        paths = item_ground((project.root / tracker).read_text(encoding="utf-8", errors="replace"), wo)
    except OSError:
        paths = None
    if paths:
        return paths, "the working tree"
    refs = ground.git_out(project.root, "for-each-ref", "--sort=-committerdate", "--format=%(refname)",
                          "refs/heads", "refs/remotes").decode("utf-8", "surrogateescape").split()
    for ref in refs:
        if ref.endswith("/" + ground.locator.RECORDS_BRANCH):
            continue
        try:
            text = ground.git_out(project.root, "show", f"{ref}:{tracker}").decode("utf-8", "replace")
        except subprocess.CalledProcessError:
            continue
        paths = item_ground(text, wo)
        if paths:
            return paths, ref
    return None, (f"{wo} has no ground: line in {tracker}, in the working tree or on any branch: write it first, the "
                  "files or folders the change is expected to touch (commands/change.md, the work order)")


def care_limits(binding) -> dict:
    """The binding's careLimits as {"paths": [...], "predictionKeys": [...]} (absent: no limits); ValueError naming
    the key when its shape is wrong."""
    v = binding.get("careLimits")
    if v is None:
        return {"paths": [], "predictionKeys": []}
    if not isinstance(v, dict):
        raise ValueError("careLimits is not an object: {\"paths\": [<glob>...], \"predictionKeys\": [<glob>...]}")
    unknown = sorted(set(v) - {"paths", "predictionKeys"})
    if unknown:
        raise ValueError(f"careLimits has an unknown key {unknown[0]!r}: the keys are paths and predictionKeys")
    out = {}
    for k in ("paths", "predictionKeys"):
        x = v.get(k, [])
        if not isinstance(x, list) or not all(isinstance(e, str) and e for e in x):
            raise ValueError(f"careLimits.{k} is not a list of globs (non-empty strings)")
        out[k] = x
    return out


def binding_drift(project, binding) -> dict:
    """How the binding here differs from main's (the docstring's "The binding compared with main's"):
    {"against": {"clone", "branch"} or None, "keys": [the differing top-level keys, sorted], "notMade": None or why}.
    Reads only: git show in the main clone."""
    def not_made(why):
        return {"against": None, "keys": [], "notMade": why}
    clone = locator.main_clone(project)
    if clone is None:
        return not_made("no main clone: git cannot say which repository holds the records worktree")
    try:  # the binding under suspicion names the branch, so a mainFollows it cannot use falls back to main, master
        follows = locator.main_follows(binding)
    except locator.BadMainFollows:
        follows = "?"
    has = [b for b in ([follows] if follows else []) + ["main", "master"]
           if b != "?" and locator._git(clone, "rev-parse", "-q", "--verify", f"refs/heads/{b}^{{commit}}")]
    if not has:
        return not_made(f"the binding has no mainFollows and {clone} has no local branch main or master"
                        if not follows else f"{clone} has no local branch that mainFollows names, nor main or master")
    branch = has[0]
    try:
        text = locator._git(clone, "show", f"refs/heads/{branch}:.team/binding.json")
    except ValueError:  # UnicodeDecodeError: git's output is read as text
        return not_made(f"the .team/binding.json committed on {branch} in {clone} is not UTF-8")
    if text is None:
        return not_made(f"{clone} has no .team/binding.json committed on {branch}")
    try:
        main = json.loads(text)
    except ValueError:
        main = None
    if not isinstance(main, dict):
        return not_made(f"the .team/binding.json committed on {branch} in {clone} is not a JSON object")
    def as_json(b, k):
        return json.dumps(b[k], sort_keys=True) if k in b else None  # None: the key is on the other side only
    keys = sorted(k for k in set(binding) | set(main) if as_json(binding, k) != as_json(main, k))
    return {"against": {"clone": str(clone), "branch": branch}, "keys": keys, "notMade": None}


def path_glob(pattern):
    """A repository-relative glob as a regular expression: ** any depth, * and ? inside one folder."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        else:
            out, i = out + {"*": "[^/]*", "?": "[^/]"}.get(pattern[i], re.escape(pattern[i])), i + 1
    return re.compile(out)


def hard_limits(root, paths, globs) -> list:
    """[[the path, or the tracked file under it, that matches, the glob]]: one for each path that is a hard limit."""
    rxs = [(g, path_glob(g)) for g in globs]
    out = []
    for p in paths if rxs else []:
        files = [p] + [f for f in os.fsdecode(ground.git_out(root, "ls-files", "-z", "--", p)).split("\0") if f]
        hit = next(([f, g] for f in files for g, rx in rxs if rx.fullmatch(f)), None)
        if hit:
            out.append(hit)
    return out


def predicted(path) -> list:
    """The "what" key of each move in a prediction file; ValueError (or OSError) when it cannot be read."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    moves = (data.get("moves") or []) if isinstance(data, dict) else None  # noMoves: no "moves", or null
    if not isinstance(moves, list) or not all(isinstance(m, dict) and isinstance(m.get("what"), str) for m in moves):
        raise ValueError('it has no "moves" list whose moves each name a "what" key')
    return [m["what"] for m in moves]


def raised(keys, globs):
    """(the first predicted key that matches a glob of careLimits.predictionKeys, that glob), or None."""
    for k in keys:
        for g in globs:
            if "*" in k or "?" in k:  # a pattern key could move a limited key: raise
                return k, g
            if re.fullmatch("".join({"*": ".*", "?": "."}.get(c, re.escape(c)) for c in g), k, re.S):
                return k, g
    return None


def file_count(root, paths) -> int:
    total = 0
    for p in paths:
        n = ground.git_out(root, "ls-files", "-z", "--", p).count(b"\0")
        total += n or 1
    return total


def troubled(here, rounds, serious, start) -> list:
    """The work orders here that are troubled: rounds (fix_rounds of the window's asks and run folders) 2 or more, or
    a serious finding (any site) in a run folder dated on or after start."""
    found = {f[0].lower() for f in serious if f[1] >= start}
    return [w for w in here if rounds.get(w.lower(), 0) >= 2 or w.lower() in found]


def route(cfg, here, trouble, finders, rounds, alt, files, uncounted=(), covered=(), limits=(), rise=None,
          off=None):
    """(the route, its reasons). here: the work orders here; trouble: those troubled; finders: the work orders whose
    serious findings on the paths are in the window; rounds: fix rounds in the window; limits: hard_limits' list;
    rise: raised's (key, glob) or None; off: bindingDrift's against when careLimits differs from main's, else None."""
    def reason(rule, setting, figure, wos, **more):
        return {"rule": rule, "setting": setting, "threshold": cfg[setting] if setting else None, "figure": figure,
                "workOrders": wos, **more}
    top = max((rounds.get(w.lower(), 0) for w in here), default=0)
    at_top = [w for w in here if rounds.get(w.lower(), 0) == top] if top else []
    look = [r for ok, r in (
        (len(trouble) >= cfg["lookVisits"], reason("look-visits", "lookVisits", len(trouble), trouble)),
        (len(finders) >= cfg["lookFindingOrders"], reason("look-findings", "lookFindingOrders", len(finders), finders)),
        (top >= cfg["lookFixRound"], reason("look-fix-round", "lookFixRound", top, at_top)),
        (bool(alt), reason("look-altitude", None, len(alt), alt))) if ok]
    if look:
        if covered:
            look.append(reason("look-covered", None, len(covered), list(covered)))
        return "look-around", look
    fast = [(files <= cfg["fastMaxFiles"], reason("fast-files", "fastMaxFiles", files, [])),
            (len(trouble) <= cfg["fastMaxVisits"], reason("fast-visits", "fastMaxVisits", len(trouble), trouble)),
            (not limits, reason("fast-limit", None, len(limits), [], limits=list(limits)))]
    if finders:  # printed only when it fails: a serious finding on these paths in the window, by any work order
        fast.append((False, reason("fast-findings", None, len(finders), finders)))
    if uncounted:  # printed only when it fails: a path ground does not count has no history the door can read
        fast.append((False, reason("fast-counted", None, len(uncounted), [])))
    if off:  # printed only when it fails: the hard limits here are not main's, so they cannot be trusted for fast
        fast.append((False, reason("fast-binding", None, 1, [], against=off)))
    if all(ok for ok, _ in fast):
        if rise:  # a predicted key only a measure shows: normal from here, never look-around
            return "normal", [reason("raised-prediction", None, 1, [], key=rise[0], pattern=rise[1])]
        return "fast", [r for _, r in fast]
    failed = [r for ok, r in fast if not ok]
    if rise and [r["rule"] for r in failed] == ["fast-binding"]:  # step 6.2 reads raised-prediction: keep it named
        return "normal", [reason("raised-prediction", None, 1, [], key=rise[0], pattern=rise[1]), *failed]
    return "normal", failed


def record(root, wo, way, note):
    """Append the work order's door event through the canonical writer (runtime/ops-event.sh): None when written,
    else why not. Never raises: a write that fails changes neither the route nor the exit code."""
    env = {k: v for k, v in os.environ.items() if k not in ("OPS_REPO", "OPS_TS", "OPS_COMMIT")}  # dated now
    env["OPS_REPO"] = str(root)
    try:  # its own process group, as gate_event.py: a write cut at WRITE_S is killed whole
        p = subprocess.Popen([str(HERE / "ops-event.sh"), "door", wo, "door", "-", "-", "-", "-", "-", way, note],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                             start_new_session=True)
        try:
            _, err = p.communicate(timeout=WRITE_S)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.communicate()
            return f"ops-event.sh did not finish in {WRITE_S} s; cut"
    except (OSError, subprocess.SubprocessError) as e:
        return f"cannot run ops-event.sh: {e}"
    if p.returncode == 0:
        return None
    lines = err.strip().splitlines()
    return lines[-1] if lines else f"ops-event.sh exit {p.returncode}"


def latest_route(project, wo):
    """The route of the work order's latest door event in the ops ledger (latest by ts, then by place in the
    ledger), or None: no door event, or a ledger that cannot be read (the door then records its own route)."""
    best = None
    try:
        for i, f in enumerate(project.read_files("ops_ledger")):
            for n, e in enumerate(bakehouse_status.ops_rows(f)):
                if not (isinstance(e, dict) and e.get("kind") == "door" and isinstance(e.get("task"), str)
                        and e["task"].lower() == wo.lower() and e.get("outcome") in RANK):
                    continue
                t = decision.when(e.get("ts"))
                key = (t.timestamp() if t is not None else float("-inf"), i, n)
                if best is None or key > best[0]:
                    best = (key, e["outcome"])
    except (OSError, ValueError):
        return None
    return best[1] if best else None


def said(r, days, uncounted=(), since=None) -> str:
    wos = f" ({ground.shown(r['workOrders'])})" if r["workOrders"] else ""
    counted = f"since {since}" if since else f"in {days} days"  # with a look, trouble counts from it
    setting = f" [{r['setting']} {r['threshold']}]" if r["setting"] else ""
    n = r["figure"]
    text = {
        "look-visits": f"{n} troubled work orders here {counted}{wos}",
        "look-findings": f"serious review findings on these paths in {days} days found by {n} work orders{wos}",
        "look-fix-round": f"{', '.join(r['workOrders'])} reached fix round {n}",
        "look-altitude": f"an altitude trigger on an ask of {n} work order{'s' if n != 1 else ''} here{wos}",
        "fast-files": f"{n} file{'s' if n != 1 else ''} in the paths",
        "fast-visits": f"{n} troubled work order{'s' if n != 1 else ''} here {counted}{wos}",
        "look-covered": f"covered by an architect look in {days} days{wos}: trouble counted from {r.get('since')}",
        "reset": f"reset by the architect look of {', '.join(r['workOrders'])}: trouble counted from {r.get('since')}",
        "fast-limit": ("a hard-limit path, never fast: " + ", ".join(f"{f} (careLimits.paths {g})"
                                                                    for f, g in r.get("limits") or [])
                       if n else "no hard-limit path"),
        "raised-prediction": (f"raised from fast: the prediction moves {r.get('key')} "
                              f"(careLimits.predictionKeys {r.get('pattern')})"),
        "fast-findings": f"serious review findings on these paths in {days} days{wos}",
        "fast-binding": (f"the hard limits here differ from main's ({(r.get('against') or {}).get('clone')} "
                         f"{(r.get('against') or {}).get('branch')}): fast needs main's careLimits"),
        "fast-counted": f"{n} path{'s' if n != 1 else ''} not counted (outside the code roots): {', '.join(uncounted)}",
    }[r["rule"]]
    return text + setting


def main(argv, root) -> int:
    ap = argparse.ArgumentParser(prog="bakehouse door", allow_abbrev=False,
                                 description="The route a work order on these paths takes: fast, normal or look-around.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--task", dest="leave_out", metavar="WORK_ORDER", help="the work order at the door: left out")
    ap.add_argument("--as-of", dest="as_of", metavar="ISO_TIME", help="replay: read nothing after this time")
    ap.add_argument("--prediction", metavar="MOVES_FILE",
                    help="the work order's prediction: a predicted key that matches careLimits.predictionKeys raises "
                         "fast to normal")
    ap.add_argument("--repo", help="default: the current folder")
    ap.add_argument("paths", nargs="*", metavar="path",
                    help="relative to the repository root; none: the --task work order's own ground: line")
    a = ap.parse_intermixed_args(argv)
    until = None
    if a.as_of is not None:
        t = decision.when(a.as_of)
        if t is None:
            ap.error(f"--as-of {a.as_of} is not an ISO time")
        until = t.timestamp()
    if not a.paths and not a.leave_out:
        ap.error("give the paths, or --task <work order> to read its ground: line")
    found = ground.prepare(ap, a, "--task", "bakehouse door")
    if isinstance(found, int):
        return found
    if not a.paths:
        line, where = ground_line(found[0], found[1], a.leave_out)
        if not line:
            print(f"bakehouse door: {where}", file=sys.stderr)
            return 2
        a.paths = line
        found = ground.prepare(ap, a, "--task", "bakehouse door")
        if isinstance(found, int):
            return found
    project, binding, _, _, paths = found
    try:
        care = care_limits(binding)
    except ValueError as e:
        print(f"bakehouse door: {project.binding}: {e}", file=sys.stderr)
        return 2
    try:
        keys = predicted(a.prediction) if a.prediction is not None else []
    except (OSError, ValueError) as e:  # json.JSONDecodeError is a ValueError
        print(f"bakehouse door: --prediction {a.prediction}: {e}", file=sys.stderr)
        return 2
    try:
        cfg = settings(project)
    except (OSError, ValueError, AttributeError) as e:  # json.JSONDecodeError is a ValueError; AttributeError: a
        # project config that is not an object (decision.merge)
        print(f"bakehouse door: the door settings ({project.decision_config} over the defaults): {e}", file=sys.stderr)
        return 2
    try:
        s, serious, runs, why = ground.gather(*found, cfg["days"], a.leave_out, until)
        files = file_count(project.root, paths)
        limits = hard_limits(project.root, paths, care["paths"])
    except subprocess.CalledProcessError as e:
        print(f"bakehouse door: git failed: {os.fsdecode(e.stderr).strip()}", file=sys.stderr)
        return 1
    events = [e for e in asks(project, until) if str(e.get("task") or "").lower() != (a.leave_out or "").lower()]
    here = list(dict.fromkeys(wo for p in s["paths"] for wo in p.get("workOrders", [])))
    now = until if until is not None else datetime.now().timestamp()
    start = date.fromtimestamp(now - cfg["days"] * 86400)  # the window's first day, for run folders
    uncounted = [p["path"] for p in s["paths"] if not p.get("inCodeRoots")]
    covered = looks(project, here + [a.leave_out or ""], now - cfg["days"] * 86400, until)
    t = max(covered.values()) if covered else None  # the latest architect look here: trouble counts from it
    since = datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds") if t is not None else None

    def counted(t):
        """(troubled, finders, rounds, altitude) counted from t: asks recorded at or after it, run folders dated on
        or after its day; t None: the whole window, as 0.16.1."""
        first = start if t is None else max(start, date.fromtimestamp(t))
        asked = events if t is None else [e for e in events if decision.when(e.get("ts"))
                                          and decision.when(e.get("ts")).timestamp() >= t]
        mine = [f for f in serious if f[2] and f[1] >= first and any(ground.under(f[2], p) for p in paths)]
        finders = list(dict.fromkeys(f[0] for f in sorted(mine, key=lambda f: f[1], reverse=True)))
        lately = [e for e in asked if e.get("task") and decision.when(e.get("ts"))
                  and decision.when(e.get("ts")).timestamp() >= max(now - cfg["days"] * 86400, t or 0)]
        rounds = fix_rounds([r for r in runs if r[0] in here and r[1] >= first], lately)
        return troubled(here, rounds, serious, first), finders, rounds, altitude(asked, here)

    drift = binding_drift(project, binding)  # the binding now: --as-of does not change it
    rest = (files, uncounted, list(covered), limits, raised(keys, care["predictionKeys"]),
            drift["against"] if "careLimits" in drift["keys"] else None)
    way, reasons = route(cfg, here, *counted(t), *rest)
    for r in reasons:
        if r["rule"] == "look-covered":
            r["since"] = since
    if t is not None and route(cfg, here, *counted(None), *rest)[0] != way:
        # the reset changed the route (look-around to normal or fast, or normal to fast): one reason says so
        reasons.append({"rule": "reset", "setting": None, "threshold": None, "figure": 1,
                        "workOrders": [next(iter(covered))], "since": since})
    text = "; ".join(said(r, cfg["days"], uncounted, since) for r in reasons)
    # a work order's door records its route; a replay (--as-of), a look at paths only (no work order) and a
    # repository whose binding is not "owned": true (it keeps no ledger) do not
    to_record = bool(a.leave_out) and until is None and binding.get("owned") is True
    last = latest_route(project, a.leave_out) if to_record else None
    kept = last if last is not None and RANK[last] > RANK[way] else None  # never recorded lower than it started
    keeps = f"kept {kept}: the door now gives {way}; a work order keeps the route it started with" if kept else None
    logged = kept or way
    note = (f"{keeps}; {text}" if kept else text)[:gate_event.NOTE_CHARS]
    unrecorded = record(project.root, a.leave_out, logged, note) if to_record and a.json else None
    out = {"format": 1, "team": True, "project": str(project.root), "route": way, "reasons": reasons,
           "settings": cfg, "asOf": a.as_of, "task": a.leave_out, "bindingDrift": drift,
           "recorded": to_record and unrecorded is None, "kept": kept, "countedFrom": since, "ground": s}
    sys.stdout.reconfigure(errors="surrogateescape")  # a file name that is not UTF-8
    print(json.dumps(out, indent=2) if a.json else f"door: {way}: " + text)
    if keeps and not a.json:
        print(f"door: {keeps}")
    if drift["keys"] and not a.json:
        print(f"door: warning: the binding here differs from main's ({drift['against']['clone']} "
              f"{drift['against']['branch']}) in: {', '.join(drift['keys'])}")
    if to_record and not a.json:
        sys.stdout.flush()  # printed first, then recorded
        unrecorded = record(project.root, a.leave_out, logged, note)
    if unrecorded:
        print(f"bakehouse door: the route was not recorded ({unrecorded}); record it: ops-event.sh door "
              f"{a.leave_out} door - - - - - {logged} {shlex.quote(note)}", file=sys.stderr)
    if why:
        print(f"bakehouse door: {why}; open work orders are not known", file=sys.stderr)
    return 1 if why else 0
