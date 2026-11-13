#!/usr/bin/env python3
"""Driver signal: the same script written again and again in a repository's run folders. No AI.

    python3 runtime/driver-signal.py [--binding <binding.json>] [--repo .]
        [--register FILE] [--as-of YYYY-MM-DD] [--threshold X] [--window-days N] [--min-runs N]
        [--top N] [--no-write]

Standard library only. It reads files and nothing else: the driver scripts (`*.py`, `*.sh`) in the
run folders under the binding's `reviews` folder. A driver is a file under a `drivers*` folder that
sits directly in a run folder (`<reviews>/<run>/drivers*/`), or in any folder of the run when
`nested` is true. It writes one file: the driver register, `registers/drivers.json` in the project's
records folder beside the health register (runtime/locator.py; docs/PROJECT-TEAM.md, "The driver
signal"). The binding is the project's; --binding and --register replace them.

WHAT IT SKIPS, and reports in `skipped`: a symbolic link (never followed: a linked run or drivers
folder, a linked folder found while searching, any link inside a drivers folder); a file over
`maxBytes`; a file or folder it cannot read. None of them stops the run.

THE RULES come from the binding's optional `drivers` object. Every key has a shipped default:

    "drivers": {
      "threshold": 0.5,        content: two files are near-duplicates at this Jaccard or more
      "boilerplate": 0.75,     a shingle in more than this share of the run folders is boilerplate
      "windowDays": 30,        the window, ending on --as-of; at most 3650
      "minRuns": 3,            a coherent family in this many runs inside the window is raised
      "minRunsNameOnly": 5,    a family below `coherent` is raised at this many; not under minRuns
      "coherent": 0.5,         the coherence at or over which a family is coherent
      "maxBytes": 1000000,     a larger file is skipped
      "nested": false,         true: also read drivers* folders deeper inside a run
      "register": "drivers.json",   in the registers/ folder, beside the health register
      "owner": "pm",           who a raised family goes to
      "families": [            optional: names the team knows are one tool, first match wins
        {"id": "mutation-harness", "names": ["mut*", "*mutant*"]}
      ]
    }

TWO SIGNALS, one family list

1. Content clusters. Each file is reduced to identifier, number and operator tokens; comments and
   string contents are dropped (Python's own tokenizer for `.py`; for `.sh`, and for a `.py` the
   tokenizer refuses, which is counted in `tokenizeFailed`, a regular expression). The tokens are cut
   into 6-token shingles. A file with fewer than 30 shingles is too small to compare. Two files in
   DIFFERENT run folders pair when the Jaccard of their shingle sets is at or over `threshold`. A
   shingle in more than `boilerplate` of the run folders, and in more than 10 of them, is
   boilerplate: it is left out of the shared count, never out of the union. Except: two files that share
   30 or more boilerplate shingles count them all, because a block that size is a copied tool, not
   boilerplate (a tool copied into nearly every run folder stays visible). A cluster is a connected
   group of pairs.
2. Name families (kind `name`). Every driver file has a name key: the first declared family whose
   `names` glob matches the file name, else the name rule. The name rule lowercases the stem, splits
   it on `_`, `-` and `.`, strips trailing digits (and one letter after them) from each piece, and
   drops pieces left empty, one letter long, or a round word (fix, fx, fr, new, old, tmp, final). So
   `make_prediction_fix2.py`, `d8_mutants.py` and `cut3.py` are `make_prediction`, `mutants` and
   `cut`. A key in 2 or more run folders is a name family, id `name:<key>` or `name:<declared id>`.

THE FOLD. A content cluster folds into the name family that holds the most of its files, when that
family holds at least half of them (a tie goes to the first key). The family takes the cluster's
files as its own and lists the cluster's id under `clusters`. A cluster no family covers is a family
on its own (kind `content`, id `content:<its first file>`).

COHERENCE. For each file of a family, its best Jaccard (all shingles) with a file of the family in
another run folder; the family's coherence is the upper median of these, to 2 places.

A run folder's date is the `YYYY-MM-DD` its name starts with; a folder with no date counts in
`runCount` and never in the window.

THE STATE. A family's count is `minRuns` when its coherence is `coherent` or more, else
`minRunsNameOnly`. A family in its count or more run folders dated inside the window, with no
decision, is `build-candidate`; under that it is `watch`. A decision (`build`, `adopt` or `accept`,
with a `date`) is written by a person under the family's `decision` key and is NEVER overwritten. A
decided family is `decided`, and is raised again (`build-candidate-again`) when its count or more run
folders dated after the decision and inside the window hold it. Each raised family carries a `flag`:
its owner and its next step, a build, adopt or accept proposal to the owner.

POINT, DON'T CARRY. A decision never moves to another family by itself. A decided id that is no
longer listed (a cluster whose first file changed, a cluster folded into a name family) is kept as a
stub with `"present": false`, its decision and its files. Every listed family that holds any of those
files carries a `priorDecision` note per such id: `{id, decision, date, holds, of}`, how many of the
old files it holds out of how many. It keeps its own state, so it can be raised; its flag then says so,
and the owner confirms with one answer: a decision written on the family's own id.

THE LIMIT. A rewrite from scratch under a new name is invisible to both signals: its content does not
match, and its name does not either.
"""

import argparse
import fnmatch
import io
import json
import os
import re
import sys
import time
import tokenize
from collections import Counter, defaultdict
from datetime import date, timedelta
from itertools import combinations
from pathlib import Path

DEFAULTS = {"threshold": 0.5, "boilerplate": 0.75, "windowDays": 30, "minRuns": 3, "minRunsNameOnly": 5,
            "coherent": 0.5, "maxBytes": 1_000_000, "nested": False, "register": "drivers.json", "owner": "pm",
            "families": []}
DECISIONS = ("build", "adopt", "accept")
SUFFIXES = (".py", ".sh")
SHINGLE, MIN_SHINGLES, BOILERPLATE_FLOOR, MAX_WINDOW = 6, 30, 10, 3650
SKIPS = ("symlinks", "tooLarge", "unreadable")
ROUND_WORDS = {"fix", "fx", "fr", "new", "old", "tmp", "final"}
NEXT = "propose to the owner: build one shared tool, adopt an existing one, or accept the copies"
NEXT_PRIOR = ("not new: an earlier decision covers some of these files (see priorDecision); propose to the owner "
              "to confirm it by writing a decision on this family's own id, or to decide afresh")
DATED = re.compile(r"^(\d{4}-\d{2}-\d{2})")
TOK = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|==|!=|<=|>=|[^\s\w]")
FSTRING_START = getattr(tokenize, "FSTRING_START", None)  # Python 3.12+: an f-string is several tokens
FSTRING_END = getattr(tokenize, "FSTRING_END", None)


def fail(msg):
    sys.exit(f"driver-signal: {msg}")


# ---------- the rules ----------

def load_rules(binding_path, overrides=None):
    try:
        binding = json.loads(Path(binding_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        fail(f"cannot read the binding {binding_path}: {e}")
    reviews = binding.get("reviews")
    if not isinstance(reviews, str) or not reviews.strip():
        fail('the binding has no "reviews" folder: the run folders the drivers live in')
    given = binding.get("drivers", {})
    if not isinstance(given, dict):
        fail(f"drivers must be an object of rules, got {json.dumps(given)}")
    unknown = set(given) - set(DEFAULTS) - {"_about"}
    if unknown:
        fail(f"drivers has unknown key(s) {sorted(unknown)}; known: {sorted(DEFAULTS)}")
    rules = {**DEFAULTS, **given, **{k: v for k, v in (overrides or {}).items() if v is not None}}
    for k, lo in (("threshold", "above 0"), ("boilerplate", "above 0"), ("coherent", "0 or more")):
        v = rules[k]
        if (isinstance(v, bool) or not isinstance(v, (int, float))
                or not (0 < v <= 1 if lo == "above 0" else 0 <= v <= 1)):
            fail(f"drivers.{k} must be a number {lo} and at most 1, got {json.dumps(v)}")
    for k, top in (("windowDays", MAX_WINDOW), ("minRuns", None), ("minRunsNameOnly", None), ("maxBytes", None)):
        v = rules[k]
        if isinstance(v, bool) or not isinstance(v, int) or v < 1 or (top and v > top):
            fail(f"drivers.{k} must be a whole number of 1 or more{f' and at most {top}' if top else ''}, "
                 f"got {json.dumps(v)}")
    if rules["minRunsNameOnly"] < rules["minRuns"]:
        fail(f"drivers.minRunsNameOnly must not be under minRuns, got {rules['minRunsNameOnly']} "
             f"under {rules['minRuns']}")
    if not isinstance(rules["nested"], bool):
        fail("drivers.nested must be true or false")
    for k in ("register", "owner"):
        if not isinstance(rules[k], str) or not rules[k].strip():
            fail(f"drivers.{k} must be a non-empty string")
    fams, seen = [], set()
    if not isinstance(rules["families"], list):
        fail("drivers.families must be a list")
    for f in rules["families"]:
        names = f.get("names") if isinstance(f, dict) else None
        if (not isinstance(f, dict) or not isinstance(f.get("id"), str) or not f["id"].strip()
                or not isinstance(names, list) or not names or not all(isinstance(n, str) and n for n in names)):
            fail(f'drivers.families entry needs an "id" and a list of "names" globs, got {f!r}')
        if f["id"] in seen:
            fail(f"drivers.families id {f['id']!r} is given twice")
        seen.add(f["id"])
        fams.append((f["id"], [n.lower() for n in names]))
    rules["families"] = fams
    rules["reviews"] = reviews.rstrip("/") + "/"
    return rules


# ---------- reading the drivers ----------

def driver_files(root, nested, max_bytes, skipped):
    """The driver files under root, sorted by path. Symbolic links are never followed; they, files over
    max_bytes and folders that cannot be listed go into skipped[kind] as paths relative to root."""
    found = []

    def note(kind, p):
        skipped[kind].append(Path(p).relative_to(root).as_posix())

    def entries(d):
        try:
            with os.scandir(d) as it:
                return sorted(it, key=lambda e: e.name)
        except OSError:
            note("unreadable", d)
            return []

    def take(d):  # inside a drivers folder: every file below it
        for e in entries(d):
            if e.is_symlink():
                note("symlinks", e.path)
            elif e.is_dir():
                take(e.path)
            elif e.is_file() and os.path.splitext(e.name)[1] in SUFFIXES:
                try:
                    big = e.stat().st_size > max_bytes
                except OSError:
                    note("unreadable", e.path)
                    continue
                if big:
                    note("tooLarge", e.path)
                else:
                    found.append(Path(e.path))

    def search(d):  # a run folder or, when nested, a folder inside one
        for e in entries(d):
            if e.is_symlink():
                if e.name.startswith("drivers") or (nested and e.is_dir()):
                    note("symlinks", e.path)
            elif e.is_dir():
                if e.name.startswith("drivers"):
                    take(e.path)
                elif nested:
                    search(e.path)

    for e in entries(root):
        if e.is_symlink():
            if e.is_dir():
                note("symlinks", e.path)
        elif e.is_dir():
            search(e.path)
    return sorted(found, key=lambda p: p.relative_to(root).parts)


def regex_tokens(text):
    text = re.sub(r"#.*", "", text)
    text = re.sub(r'("""|\'\'\')(.|\n)*?\1', '""', text)
    text = re.sub(r'"[^"\n]*"|\'[^\'\n]*\'', '""', text)
    return TOK.findall(text)


def py_tokens(text):
    """Python's own tokens: names, numbers and operators; a string (an f-string whole) becomes "";
    comments and layout tokens are dropped. Raises on text the tokenizer refuses."""
    toks, depth = [], 0
    for t in tokenize.generate_tokens(io.StringIO(text).readline):
        if t.type == FSTRING_START:
            if depth == 0:
                toks.append('""')
            depth += 1
        elif t.type == FSTRING_END:
            depth -= 1
        elif depth:
            continue
        elif t.type == tokenize.STRING:
            toks.append('""')
        elif t.type in (tokenize.NAME, tokenize.NUMBER, tokenize.OP):
            toks.append(t.string)
    return toks


def shingles(text, suffix=".sh"):
    """(the 6-token shingle set, True when the Python tokenizer refused the text)."""
    refused = False
    if suffix == ".py":
        try:
            toks = py_tokens(text)
        except Exception:  # TokenError, SyntaxError, and a NUL byte's SystemError on Python 3.12
            toks, refused = regex_tokens(text), True
    else:
        toks = regex_tokens(text)
    return {tuple(toks[i:i + SHINGLE]) for i in range(max(0, len(toks) - SHINGLE + 1))}, refused


def name_key(name, declared):
    low = name.lower()
    for fid, globs in declared:
        if any(fnmatch.fnmatchcase(low, g) for g in globs):
            return fid
    stem = low.rsplit(".", 1)[0]
    pieces = [re.sub(r"\d+[a-z]?$", "", p) for p in re.split(r"[_.\-\s]+", stem)]
    kept = [p for p in pieces if len(p) > 1 and p not in ROUND_WORDS]
    return "_".join(kept) or stem


def run_day(run):
    m = DATED.match(run)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


# ---------- signal 1: content clusters ----------

def clusters(files, threshold, boilerplate):
    """(clusters as lists of file indexes, pairs). Only files with MIN_SHINGLES or more take part."""
    big = [i for i, f in enumerate(files) if len(f["sh"]) >= MIN_SHINGLES]
    cut = max(boilerplate * len({f["run"] for f in files}), BOILERPLATE_FLOOR)
    index = defaultdict(set)
    for i in big:
        for g in files[i]["sh"]:
            index[g].add(i)
    shared, common = defaultdict(int), set()
    for g, ids in index.items():
        if len(ids) > cut and len({files[i]["run"] for i in ids}) > cut:
            common.add(g)
            continue
        for a, b in combinations(sorted(ids), 2):
            shared[(a, b)] += 1
    held = {i: files[i]["sh"] & common for i in big}
    heavy = sorted(i for i, h in held.items() if len(h) >= MIN_SHINGLES)
    for a, b in combinations(heavy, 2):  # a shared block of boilerplate this size is a copied tool
        n = len(held[a] & held[b])
        if n >= MIN_SHINGLES:
            shared[(a, b)] += n
    parent = {i: i for i in big}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pairs = 0
    for (a, b), inter in shared.items():
        if files[a]["run"] == files[b]["run"]:
            continue
        if inter / len(files[a]["sh"] | files[b]["sh"]) >= threshold:
            pairs += 1
            parent[find(a)] = find(b)
    groups = defaultdict(list)
    for i in big:
        groups[find(i)].append(i)
    return [sorted(g) for g in groups.values() if len({files[i]["run"] for i in g}) >= 2], pairs, len(big)


def coherence(members, files):
    best = []
    for i in members:
        mine = files[i]["sh"]
        best.append(max((len(mine & files[j]["sh"]) / len(mine | files[j]["sh"])
                         for j in members if files[j]["run"] != files[i]["run"] and mine | files[j]["sh"]),
                        default=0.0))
    best.sort()
    return round(best[len(best) // 2], 2)


# ---------- the register ----------

def read_register(path):
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return {}
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        fail(f"the register {path} is not JSON")
    if not isinstance(old, dict):
        fail(f"the register {path} is not a JSON object")
    return old


def check_decision(fid, d):
    if (not isinstance(d, dict) or d.get("decision") not in DECISIONS or not isinstance(d.get("date"), str)):
        fail(f"families.{fid}.decision needs a date and one of {' | '.join(DECISIONS)}, got {d!r}")
    try:
        return date.fromisoformat(d["date"])
    except ValueError:
        fail(f"families.{fid}.decision.date is not YYYY-MM-DD, got {d['date']!r}")


def family(kind, members, folded, files, since, as_of, decision, prior, rules):
    runs = sorted({files[i]["run"] for i in members})
    days = sorted(d for d in (run_day(r) for r in runs) if d)
    inside = [d for d in days if since <= d <= as_of]
    coh = coherence(members, files)
    need = rules["minRuns"] if coh >= rules["coherent"] else rules["minRunsNameOnly"]
    if decision:
        decided_on = date.fromisoformat(decision["date"])
        after = [r for r in runs if (d := run_day(r)) and since <= d <= as_of and d > decided_on]
        state = "build-candidate-again" if len(after) >= need else "decided"
    else:
        state = "build-candidate" if len(inside) >= need else "watch"
    raised = state.startswith("build-candidate")
    out = {
        "kind": kind,
        "names": sorted({files[i]["name"] for i in members}),
        "runCount": len(runs),
        "runsInWindow": len(inside),
        "fileCount": len(members),
        "lines": sum(files[i]["lines"] for i in members),
        "coherence": coh,
        "firstSeen": days[0].isoformat() if days else None,
        "lastSeen": days[-1].isoformat() if days else None,
        "state": state,
        "decision": decision,
        **({"priorDecision": prior} if prior else {}),
        "flag": {"owner": rules["owner"], "next": NEXT_PRIOR if prior else NEXT} if raised else None,
    }
    if kind == "name":
        out["clusters"] = folded
    out["runs"] = runs
    out["files"] = sorted(files[i]["path"] for i in members)
    return out


def build(repo, rules, register_path, as_of):
    root = Path(repo) / rules["reviews"]
    if not root.is_dir():
        fail(f"the reviews folder {root} does not exist")
    skipped = {k: [] for k in SKIPS}
    files, refused = [], 0
    for p in driver_files(root, rules["nested"], rules["maxBytes"], skipped):
        rel = p.relative_to(root)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped["unreadable"].append(rel.as_posix())
            continue
        sh, no = shingles(text, p.suffix)
        refused += no
        files.append({"path": rel.as_posix(), "run": rel.parts[0], "name": p.name, "lines": text.count("\n"),
                      "sh": sh, "key": name_key(p.name, rules["families"])})
    groups, pairs, compared = clusters(files, rules["threshold"], rules["boilerplate"])

    by_key = defaultdict(list)
    for i, f in enumerate(files):
        by_key[f["key"]].append(i)
    fams = {f"name:{k}": {"kind": "name", "members": set(m), "clusters": []}
            for k, m in by_key.items() if len({files[i]["run"] for i in m}) >= 2}
    for g in groups:
        cid = f"content:{files[g[0]]['path']}"
        held = Counter(files[i]["key"] for i in g if f"name:{files[i]['key']}" in fams)
        best = min(held.items(), key=lambda kv: (-kv[1], kv[0]), default=None)
        if best and best[1] * 2 >= len(g):
            fams[f"name:{best[0]}"]["members"] |= set(g)
            fams[f"name:{best[0]}"]["clusters"].append(cid)
        else:
            fams[cid] = {"kind": "content", "members": set(g), "clusters": []}

    old = (read_register(register_path).get("families") or {})
    decided = {}
    for fid, v in old.items():
        d = v.get("decision") if isinstance(v, dict) else None
        if d:
            check_decision(fid, d)
            decided[fid] = v
    stubs, prior = {}, defaultdict(list)
    paths = {fid: {files[i]["path"] for i in v["members"]} for fid, v in fams.items()}
    for fid in sorted(set(decided) - set(fams)):
        v = decided[fid]
        recorded = sorted(set(v.get("files") or []))
        stubs[fid] = {"kind": v.get("kind"), "present": False, "state": "decided", "decision": v["decision"],
                      "flag": None, "files": recorded}
        for t in sorted(fams):
            n = len(paths[t].intersection(recorded))
            if n:
                prior[t].append({"id": fid, "decision": v["decision"]["decision"], "date": v["decision"]["date"],
                                 "holds": n, "of": len(recorded)})

    since = as_of - timedelta(days=rules["windowDays"])
    found = {fid: family(v["kind"], v["members"], v["clusters"], files, since, as_of,
                         (decided.get(fid) or {}).get("decision"), prior.get(fid), rules) for fid, v in fams.items()}
    found.update(stubs)
    order = sorted(found, key=lambda f: (not found[f]["state"].startswith("build-candidate"),
                                         -found[f].get("runsInWindow", 0), f))
    raised = [f for f in order if found[f]["state"].startswith("build-candidate")]
    return {
        "_about": "Generated by bakehouse runtime/driver-signal.py, except each family's `decision`, which a "
                  "person owns and the script never overwrites.",
        "computed": {
            "asOf": as_of.isoformat(),
            "windowDays": rules["windowDays"],
            "threshold": rules["threshold"],
            "boilerplate": rules["boilerplate"],
            "minRuns": rules["minRuns"],
            "minRunsNameOnly": rules["minRunsNameOnly"],
            "coherent": rules["coherent"],
            "maxBytes": rules["maxBytes"],
            "nested": rules["nested"],
            "reviews": rules["reviews"],
            "runFolders": len({f["run"] for f in files}),
            "driverFiles": len(files),
            "drivers": compared,
            "tokenizeFailed": refused,
            "skipped": {k: len(v) for k, v in skipped.items()},
            "pairs": pairs,
            "clusters": len(groups),
            "filesInClusters": sum(len(g) for g in groups),
            "linesInClusters": sum(files[i]["lines"] for g in groups for i in g),
            "nameFamilies": sum(1 for f in found.values() if f["kind"] == "name" and f.get("present", True)),
            "buildCandidates": len(raised),
            "buildCandidatesNew": sum(1 for f in raised if not found[f].get("priorDecision")),
            "buildCandidatesNoted": sum(1 for f in raised if found[f].get("priorDecision")),
        },
        "skipped": {k: sorted(v) for k, v in skipped.items()},
        "families": {f: found[f] for f in order},
    }


def render(reg, top, seconds):
    c = reg["computed"]
    fams = reg["families"]
    raised = [f for f, v in fams.items() if v["state"].startswith("build-candidate")]
    noted = [f for f in raised if fams[f].get("priorDecision")]
    new = [f for f in raised if f not in noted]
    lines = [
        f"Drivers, {c['windowDays']} days to {c['asOf']}: {c['driverFiles']} driver files in {c['runFolders']} run "
        f"folders. Content at Jaccard >= {c['threshold']}: {c['clusters']} clusters, {c['filesInClusters']} files, "
        f"{c['linesInClusters']} lines. {len(raised)} family(ies) raised, {len(new)} new and {len(noted)} not new "
        f"(coherent at {c['minRuns']}+ runs, "
        f"name-only at {c['minRunsNameOnly']}+). {seconds:.1f} s, no AI.",
    ]
    if any(c["skipped"].values()):
        lines.append("Skipped, listed in the register's `skipped`: " + ", ".join(
            f"{n} {k}" for k, n in c["skipped"].items() if n)
            + (f" (tooLarge: over {c['maxBytes']} bytes)." if c["skipped"]["tooLarge"] else "."))
    lines += [
        "",
        "| family | runs (in window) | files | lines | coherence | first | last | state |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for f in raised[:top]:
        v = fams[f]
        lines.append(f"| `{f}` ({', '.join(v['names'][:3])}{', ...' if len(v['names']) > 3 else ''}) | "
                     f"{v['runCount']} ({v['runsInWindow']}) | {v['fileCount']} | {v['lines']} | {v['coherence']} | "
                     f"{v['firstSeen']} | {v['lastSeen']} | {v['state']}{prior_cell(v)} |")
    if len(raised) > top:
        lines.append(f"| ... {len(raised) - top} more in the register | | | | | | | |")
    if raised:
        lines.append("")
    if new:
        lines.append(f"Each new family goes to the {fams[new[0]]['flag']['owner']}: {NEXT}.")
    if noted:
        lines.append(f"{len(noted)} of them are NOT new ({', '.join(f'`{f}`' for f in noted)}): a prior decision "
                     "covers some of their files. Confirm it by writing a decision on the family's own id, or "
                     "decide afresh.")
    return "\n".join(lines)


def prior_cell(v):
    return "".join(f"; prior decision on `{n['id']}`: {n['decision']} {n['date']}, holds {n['holds']} of {n['of']} "
                   f"files" for n in v.get("priorDecision") or [])


def project_of(repo):
    """The project of the repository (runtime/locator.py): its binding and its records folder."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import locator
    try:
        project = locator.find(repo, {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"})
    except locator.SetupNeeded as e:
        fail(str(e))
    if project is None:
        fail(f"{repo} has no team (no .team/binding.json): pass --binding and a full --register path")
    return project


def write_whole(path, text):
    """Replace the register in one step, so a reader never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--binding", help="default: the project's .team/binding.json")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--register", help="the register file (default: drivers.register); a relative path is "
                                       "taken in the project's registers/ folder")
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    ap.add_argument("--threshold", type=float)
    ap.add_argument("--window-days", type=int)
    ap.add_argument("--min-runs", type=int)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--no-write", action="store_true", help="print the block; leave the register as it is")
    a = ap.parse_args(argv)
    started = time.monotonic()
    project = None if a.binding and a.register and Path(a.register).is_absolute() else project_of(a.repo)
    rules = load_rules(a.binding or project.binding,
                       {"threshold": a.threshold, "windowDays": a.window_days, "minRuns": a.min_runs})
    register = Path(a.register or rules["register"])
    if not register.is_absolute():
        register = project.driver_register.parent / register
    reg = build(a.repo, rules, register, a.as_of)
    if not a.no_write:
        write_whole(register, json.dumps(reg, indent=2, ensure_ascii=False) + "\n")
    print(render(reg, a.top, time.monotonic() - started))
    return reg


if __name__ == "__main__":
    main()
