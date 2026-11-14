#!/usr/bin/env python3
"""Records against the process: each work order's record must hold what its job type declares, and a
work order that changed code must be closed. Deterministic, no AI, standard library only.

    python3 runtime/records-check.py [--repo .] [--binding FILE]
        [--ref REF] [--as-of YYYY-MM-DD] [--since YYYY-MM-DD] [--code-roots DIR ...]
        [--ledger FILE] [--register FILE] [--no-write]

The project is the repository at --repo (runtime/locator.py): its binding, its ops ledger and its
records folder. --binding and --ledger replace the project's own. It reads the repository with
read-only git commands at one ref and writes one file: the register, `registers/records.json` in the
records folder unless --register names another. docs/PROJECT-TEAM.md, "Records
against the process", is the full reference.

THE CONTRACTS. runtime/record-contracts.json declares, per job type, the files that mark a record
folder as that job's record, who owns its flags, whether it closes through `predict-gate.sh close`,
and the artefacts it must hold. The binding's optional `records` object may set `since` and ADD
artefacts to a job; it can never remove or replace one (exit 2, and preflight check 2 fails).

THE WORK ORDERS. A work order is a tracker id (the binding's `tracker.id`) with at least one code
commit in the window. A commit belongs to the FIRST tracker id in its subject; a code commit touches
one of the code roots (`signal.codeRoots` for a rules signal, else the binding's `records.codeRoots`;
--code-roots replaces either). The window runs from --since, else the
binding's `records.since`, else `windowDays` before --as-of, to the end of --as-of. A work order's
record folders are the folders directly under the binding's `reviews` whose name holds its id as a
whole token, case ignored. A step folder is one whose name matches the binding's `reviewName`, with
{n} any step number. A work order is a refactor when one of its folders holds the refactor proof
(`byte-identical*`) and no prediction; in a refactor, a step folder with no prediction is a refactor
step and owes its proof. Outside a refactor, a step folder or a folder holding `result*.md` is a
change record.

THE TWO RULES. Content, not presence: an empty file always fails.
  (a) `close`: a work order that owes a close has a `work-order-closed` event in the ops ledger. The
      latest one counts when every file it hashes sits at the top level of one of this work order's
      step folders; it carries the sha256 of every file `predict-gate.sh close` hashes in this
      record (each prediction, its measured file and its baseline, in the step folders that close
      reads: {n} = 1); every sha256 it carries matches that file at the ref; and no code commit is
      later than the record's latest measured file's commit (for a record with a round whose
      prediction is first committed on or after predict-gate's ORDER_FROM, by commit time; an older
      record keeps the older rule: the close's time is not before the last code commit). For a SEALED
      work order (one with sealed events) that time rule is replaced: it is stale when the code at the
      ref is not the code its latest measured seal read (predict-gate's sealed_stale, by commits and
      trees). And, unless OVERRIDDEN, predict-gate's own check
      passes on each prediction it carries, paired and read at the ref as close pairs it. An
      OVERRIDDEN close need not carry every file, and counts only when a `gate-refused` event
      `predict-gate.sh close` writes (purpose "work order close overridden") of this work order comes
      after its previous close and before this one; other events of the work order may sit between. A refactor never closes: a step without its proof, or a code commit that comes after
      the latest step proof (a `byte-identical*`, or a meaning-change step's measured file), is
      flagged `refactor.byte-identical`.
  (b) `<job>.<artefact>`: every record folder that marks a job holds each active artefact the job
      requires: a prediction that predict-gate's own checker accepts and that names a move; each
      prediction round's baseline and measured file, as flat JSON; a reviewer's mutation record in
      the mutation-record format (check_mutations), one beside each findings file (the artefact's
      `per`: findings.md owes mutations.json, findings-fix1.md owes mutations-fix1.json); each findings
      file not empty; any other artefact not empty. A findings file whose adding commit is before the job's `rounds.from` (the
      date (c) uses) owes no mutation file: it is history; a mutation file that exists is checked. A job whose contract
      says `nested` is also read in every folder below a record folder: a reviewer writes in a
      subfolder of the step folder it reviews. A survivor with no disposition is flagged. A row that
      carries a `batch` (the mutation runner's) is reconciled with the round's batch log beside it
      (rounds.first.log, rounds.fix.log): every logged row must be in the file with the same id,
      batch, mutation, site, gate, verdict and data (the disposition may change), and a file row's
      batch must be in the log; so a row an edit dropped or changed is caught whenever the edit was
      made. A row with no `batch` is the reviewer's own and is not reconciled. A mutation
      record's paths and gate ids are checked at the commit that last changed it. A `test-added` test
      was changed by a non-merge commit that descends from the one that first added its findings file.
  (c) `<job>.rounds`: a job that declares `rounds` (the reviewer) names each review round's files
      one way: findings.md and mutations.json, then findings-fix<N>.md and mutations-fix<N>.json for
      the re-check of fix round N. A findings-like file under any other name is flagged when the
      commit that added it is on or after `rounds.from`; an older one is history and is read as it is.
One flag per work order and rule. Each names its owner and its next step. A close made by the
owner's override (PREDICT_GATE_CLOSE_OVERRIDE) marks that work order's record flags `overridden`. A
clean close marks `superseded` the flags found only in folders that are not step folders (a design
or planning folder): the closed step record replaces them. A flag that holds an open survivor stays
open whatever the folder: only the round that settles it clears it.
A rule flagged on `promoteAt` work orders or more is a promote-to-code candidate for the owner.

THE KILL RATE. Every mutation row read in the window is counted per gate: killed and survived, the
mutation files (records) and the work orders it came from. A file is counted once, though a folder
named by several work orders is read for each. It is reported, never a target: a target invites easy
mutants.

THE LEDGER'S CURRENCY. For an owned repository, one `stream.currency` flag (work order `(stream)`, owner
pm) when the post-commit hook's ops-gap.sh warns that the ops ledger is behind. Never a promote-to-code
count.

Exit: 0 when the check completed, whatever it flagged; 2 bad input.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import fnmatch
import hashlib
import importlib.util
import io
import json
import os
import posixpath
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTRACTS = HERE / "record-contracts.json"
CHECKS = ("prediction", "baseline", "measured", "nonempty", "mutations")
VERDICTS = ("killed", "survived")
MUTATION_FIELDS = ("id", "mutation", "site", "gate", "verdict")
NO_DISPOSITION = "survived and has no disposition"
OPEN_SURVIVOR = "survived and is open"
REACH = re.compile(r"\breach\b\s*(?:[:=]|is)?\s*\d", re.IGNORECASE)
CLOSE_RULE = "close"
CLOSED_KIND = "work-order-closed"
REFUSED_KIND = "gate-refused"
OVERRIDDEN = "OVERRIDDEN"
CLOSE_REFUSED = "work order close overridden"  # the purpose predict-gate.py's run_close gives its gate-refused event
STREAM = "(stream)"
CURRENCY_RULE = "stream.currency"
CURRENCY_NEXT = "bring the ops ledger up to date with the repository's commits"
PAIR = re.compile(r"(\S+) sha256:([0-9a-f]{64})")
BASE_PAIR = re.compile(r"baseline (\S+) \(sha256 ([0-9a-f]{64})\)")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_spec = importlib.util.spec_from_file_location("predict_gate", HERE / "predict-gate.py")
pg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pg)

OVERRIDE = 'PREDICT_GATE_CLOSE_OVERRIDE="<reason>" predict-gate.sh close {wo}'
NEXT = {
    "close": "Run `predict-gate.sh close {wo}` from the repository. It passes only on a complete change "
             "record: produce each missing artefact by measuring it, never by writing it after the fact. "
             "A record that cannot be completed honestly closes only with the owner's override: " + OVERRIDE,
    "foreign": "The close does not hash this work order's own record. Run `predict-gate.sh close {wo}` on "
               "this work order's record; a close written by hand, or one that carries another work order's "
               "files, does not count.",
    "stale": "Code changed after the close. Record the later change (a new prediction round before its first "
             "edit), then run `predict-gate.sh close {wo}` again; or bring the owner's override: " + OVERRIDE,
    "proof": "A refactor step proves itself byte-identical. Re-run the step's byte-identical gates (BASE against "
             "BASE, then the step against BASE) and commit the result as byte-identical.txt in the step's "
             "record folder. A step whose output changed is not a refactor step: stop, and take it to the "
             "owner as a meaning change.",
    "mismatch": "The record changed after its close. Run `predict-gate.sh close {wo}` again on the record as "
                "it is (it writes a new event), or restore the closed file.",
    "prediction": "A prediction is written before the first edit, so it cannot be produced now. Bring the "
                  "owner's override: " + OVERRIDE + ". A prediction that does not parse is corrected only "
                  "with the owner's decision, cited as a `Prediction-approved:` trailer.",
    "baseline": "Measure it with the repository's own measurement, at the commit before the work order's "
                "first change, and commit it beside its prediction; or bring the owner's override: " + OVERRIDE,
    "measured": "Measure it with the repository's own measurement and commit it beside its prediction; or "
                "bring the owner's override: " + OVERRIDE,
    "nonempty": "Write it from what the run measured and commit it in the record folder; or bring the "
                "owner's override: " + OVERRIDE,
    "rounds": ("A review round's files have one name each (record-contracts.json, rounds): findings.md and "
               "mutations.json for the first review, findings-fix<N>.md and mutations-fix<N>.json for the "
               "re-check of fix round N. Rename the file to the name the flag gives with `git mv`, and commit "
               "its mutation record beside it; or bring the owner's override: ") + OVERRIDE,
    "mutations": ("The reviewer writes mutations.json beside findings.md (a re-check's findings-fix1.md: "
                  "mutations-fix1.json): one row per mutation performed, "
                  "{{id, mutation, site, gate, verdict, disposition}}, transcribed from the review's own "
                  "mutation table. A survivor's disposition is `test-added <test file or path::name>`, "
                  "`accepted: <reason>` stating its reach as a count, or `open: <finding id>`; an open "
                  "survivor stays flagged until the round that settles it rewrites its disposition. Commit "
                  "it in the review folder; or bring the owner's override: ") + OVERRIDE,
}


class BadInput(Exception):
    def __init__(self, msg, gate="records-check:input"):
        super().__init__(msg)
        self.gate = gate  # the gate id of the refusal event main writes (docs/PROJECT-TEAM.md, "Gate refusals")


def refusal_event(e: BadInput, path=None):
    """One gate-refused event for a refused input. Best effort: a failed write changes nothing."""
    try:
        sys.path.insert(0, str(HERE))
        import gate_event
        gate_event.refused(e.gate, "records-check refused its input", f"records-check: bad input: {e}",
                           path=None if path is None else str(path))
    except Exception:  # noqa: BLE001, S110 - the event reports a refusal; it never changes one
        pass


def git(repo, *args, binary=False):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=not binary)
    if p.returncode:
        err = p.stderr if not binary else p.stderr.decode("utf-8", "replace")
        raise BadInput(f"git {' '.join(args[:3])} failed in {repo}: {err.strip()}")
    return p.stdout


# ---------- the contracts ----------

def load_contracts(path=CONTRACTS) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def binding_errors(binding: dict, contracts: dict | None = None) -> list:
    """Why the binding's `records` object is refused; [] when it is absent or only adds."""
    rec = binding.get("records")
    if rec is None:
        return []
    jobs = (contracts or load_contracts())["jobs"]
    if not isinstance(rec, dict):
        return ["'records' must be an object"]
    errs = [f"'records.{k}' is not a records field (since, jobs, testRoots, codeRoots)" for k in rec
            if k not in ("_about", "since", "jobs", "testRoots", "codeRoots")]
    roots = rec.get("testRoots")
    if roots is not None and not (isinstance(roots, list) and roots and all(
            isinstance(r, str) and r.strip("/") and not r.startswith("/") and ".." not in r.split("/") for r in roots)):
        errs.append(f"'records.testRoots' = {json.dumps(roots)} is not a list of repository folders, "
                    "for example [\"tests/\"]")
    code = rec.get("codeRoots")
    if code is not None and not (isinstance(code, list) and code and all(
            isinstance(r, str) and r.strip("/") and not r.startswith("/") and ".." not in r.split("/") for r in code)):
        errs.append(f"'records.codeRoots' = {json.dumps(code)} is not a list of repository folders, "
                    "for example [\"src/\"]")
    since = rec.get("since")
    if since is not None and not (isinstance(since, str) and DAY.match(since) and _date(since)):
        errs.append(f"'records.since' = {json.dumps(since)} is not a date, YYYY-MM-DD")
    elif since is not None and _date(since) > date.today():
        errs.append(f"'records.since' = {json.dumps(since)} is in the future: the window would hold nothing")
    added = rec.get("jobs", {})
    if not isinstance(added, dict):
        return errs + ["'records.jobs' must be an object of job -> {\"require\": [...]}"]
    for job, spec in added.items():
        where = f"records.jobs.{job}"
        if job not in jobs:
            errs.append(f"'{where}': no such job ({', '.join(jobs)}); a binding adds artefacts to a shipped job")
            continue
        if not isinstance(spec, dict):
            errs.append(f"'{where}' must be an object with 'require'")
            continue
        for k in spec:
            if k not in ("_about", "require"):
                errs.append(f"'{where}.{k}': a binding may add artefacts to a job (require), never change or "
                            "remove its defaults")
        req = spec.get("require", [])
        if not isinstance(req, list):
            errs.append(f"'{where}.require' must be a list")
            continue
        own = {r["id"] for r in jobs[job]["require"]}
        seen = set()
        for i, r in enumerate(req):
            at = f"{where}.require[{i}]"
            if not isinstance(r, dict) or set(r) - {"id", "glob"} or not all(
                    isinstance(r.get(k), str) and r.get(k).strip() for k in ("id", "glob")):
                errs.append(f"'{at}' must be exactly {{\"id\", \"glob\"}}, both text: an added artefact is "
                            "checked as present and not empty")
                continue
            if r["id"] in own:
                errs.append(f"'{at}' replaces the default artefact '{r['id']}': a binding may add, never remove "
                            "or replace")
            elif r["id"] in seen:
                errs.append(f"'{at}' repeats the id '{r['id']}'")
            if "/" in r["glob"]:
                errs.append(f"'{at}.glob' = {json.dumps(r['glob'])}: an artefact sits at the folder's top level")
            seen.add(r["id"])
    return errs


def _date(s):
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def test_roots(binding: dict, contracts: dict) -> list:
    """The folders a `test-added <path>` may name: the binding's records.testRoots, else the shipped ones."""
    return [r.rstrip("/") + "/" for r in (binding.get("records") or {}).get("testRoots") or contracts["testRoots"]]


def merged_jobs(binding: dict, contracts: dict) -> dict:
    errs = binding_errors(binding, contracts)
    if errs:
        raise BadInput("the binding's records field: " + "; ".join(errs), "records-check:binding-records")
    jobs = copy.deepcopy(contracts["jobs"])
    for job, spec in ((binding.get("records") or {}).get("jobs") or {}).items():
        for r in spec.get("require", []):
            jobs[job]["require"].append({"id": r["id"], "check": "nonempty", "glob": r["glob"], "from": "binding"})
    return jobs


# ---------- the repository ----------

def id_search(form: str) -> re.Pattern:
    """The binding's tracker id form (a whole-id pattern) as a search for a whole token."""
    body = re.sub(r"^(\^|\\A)", "", form)
    body = re.sub(r"(\$|\\Z)$", "", body)
    try:
        return re.compile(rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])")
    except re.error as e:
        raise BadInput(f"the binding's tracker.id does not compile: {e}")


def step_name(form: str, wo: str, n: str = r"[0-9]+", flags=re.I) -> re.Pattern:
    """The binding's reviewName for this work order: {date} a date, {id} the work order, {n} any step
    number, any other placeholder one path part. Case ignored, as the folder match is. With n="1" and
    flags=0 it is the name `predict-gate.sh close` reads (record_folders)."""
    return re.compile("".join({"date": r"\d{4}-\d{2}-\d{2}", "id": re.escape(wo), "n": n}.get(t[1:-1], r"[^/]+")
                              if re.fullmatch(r"\{\w+\}", t) else re.escape(t)
                              for t in re.split(r"(\{\w+\})", form) if t), flags)


def code_commits(repo, ref, since, until, roots, rx):
    """({work order: commits}, {work order: last committer time}, ["hash subject" naming no tracker id])."""
    out = git(repo, "log", ref, "--no-merges", f"--since={since.isoformat()}T00:00:00",
              f"--until={until.isoformat()}T23:59:59", "--format=%h%x09%cI%x09%s", "--", *roots)
    by_id, last, uncredited = defaultdict(int), {}, []
    for line in out.splitlines():
        if line.count("\t") < 2:
            continue
        h, at, subject = line.split("\t", 2)
        m = rx.search(subject)
        if m:
            by_id[m.group()] += 1
            t = datetime.fromisoformat(at)
            last[m.group()] = max(last.get(m.group(), t), t)
        else:
            uncredited.append(f"{h} {subject}")
    return dict(by_id), last, uncredited


def record_tree(repo, ref, reviews) -> tuple[dict, dict]:
    """({folder name: {top-level file name: blob}}, {folder name: {subfolder path: {file name: blob}}})
    under `reviews` at the ref. A symbolic link is not a file of the record."""
    tree, below = {}, {}
    for e in git(repo, "ls-tree", "-r", "-z", ref, "--", reviews + "/").split("\0"):
        if not e:
            continue
        meta, path = e.split("\t", 1)
        mode, kind, blob = meta.split()
        parts = path[len(reviews) + 1:].split("/")
        files = tree.setdefault(parts[0], {})
        if kind != "blob" or mode == "120000":
            continue
        if len(parts) == 2:
            files[parts[1]] = blob
        elif len(parts) > 2:
            below.setdefault(parts[0], {}).setdefault("/".join(parts[1:-1]), {})[parts[-1]] = blob
    return tree, below


class Reader:
    def __init__(self, repo, ref):
        self.repo, self.ref, self.cache = repo, ref, {}

    def blob(self, blob) -> bytes:
        if blob not in self.cache:
            self.cache[blob] = git(self.repo, "cat-file", "blob", blob, binary=True)
        return self.cache[blob]

    def text(self, blob) -> str:
        return self.blob(blob).decode("utf-8", "replace")

    def at_ref(self, path) -> bytes | None:
        p = subprocess.run(["git", "-C", str(self.repo), "cat-file", "blob", f"{self.ref}:{path}"],
                           capture_output=True)
        return p.stdout if p.returncode == 0 else None

    def exists(self, path) -> bool:
        """A file or a folder at the ref."""
        if path not in self.cache:
            self.cache[path] = subprocess.run(["git", "-C", str(self.repo), "cat-file", "-e", f"{self.ref}:{path}"],
                                              capture_output=True, check=False).returncode == 0
        return self.cache[path]

    def file_text(self, path) -> str | None:
        """A file's text at the ref; None for a folder or a missing path."""
        data = self.at_ref(path)
        return data.decode("utf-8", "replace") if data is not None else None

    def sha_at_ref(self, path) -> str | None:
        data = self.at_ref(path)
        return hashlib.sha256(data).hexdigest() if data is not None else None


# ---------- the reviewer's mutation record ----------

def gate_ids(binding: dict, text_at) -> set:
    """Every gate id the binding's `gates` pointers (`file#key`, `file#key.id`) name. text_at(path)
    returns a repository file's text, or None. Read with preflight's own pointer reader."""
    tp = None
    out = set()
    stack = [binding.get("gates")]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack += list(node.values())
            continue
        if not isinstance(node, str) or not re.fullmatch(r"[^\s#]+#[^\s#]+", node):
            continue
        fname, ref = node.split("#")
        key, _, item = ref.partition(".")
        text = text_at(fname)
        if text is None:
            continue
        if fname.endswith(".json"):
            try:
                rows = json.loads(text).get(key)
            except (ValueError, AttributeError):
                continue
            ids = {r["id"] for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)} \
                if isinstance(rows, list) else set()
        elif fname.endswith((".yaml", ".yml")):
            if tp is None:  # lazy: team-preflight loads this module when it is imported
                spec = importlib.util.spec_from_file_location("team_preflight", HERE / "team-preflight.py")
                tp = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(tp)
            block = tp.yaml_block(text.split("\n"), key)
            ids = set(tp.yaml_items(block)) if block is not None else set()
        else:
            continue
        out |= ({item} & ids) if item else ids
    return out


def _gate_ok(g, gates: set, exists) -> bool:
    if not isinstance(g, str) or not g.strip() or g != g.strip():
        return False
    if g in gates:
        return True
    path = g.split("::", 1)[0]
    return not (path.startswith("/") or ".." in path.split("/") or " " in path) and exists(path.rstrip("/"))


def gate_label(g) -> str:
    return " + ".join(g) if isinstance(g, list) else g


def defines(text: str, name: str, last: bool) -> bool:
    """The file defines `name` as a class, or, for the last part of a path::name, as a `def test…`."""
    n = re.escape(name)
    return bool(re.search(rf"^[ \t]*class[ \t]+{n}(?=[\s(:])", text, re.MULTILINE) or last and name.startswith("test")
                and re.search(rf"^[ \t]*(?:async[ \t]+)?def[ \t]+{n}[ \t]*\(", text, re.MULTILINE))


def test_added_problem(target: str, exists, text_of, roots, changed=None) -> str | None:
    """Why `test-added <target>` names no test, or None. The path is a file, and either it lies under a
    test root or the target is `path::name` (pytest form) and the file defines each part: the last a
    `def test…` or a class, the parts before it classes. changed(path), when given, says whether a
    commit changed the file after the review that found the survivor (records-check only)."""
    path, _, name = target.partition("::")
    if not _gate_ok(path, set(), exists):
        return f"names {json.dumps(path)}, which is not a path in the repository"
    text = text_of(path) if text_of else None
    if text is None:
        return f"names {json.dumps(path)}, which is a folder, not a test file"
    if name:
        parts = [re.sub(r"\[.*\]$", "", x) for x in name.split("::")]
        if not all(x and defines(text, x, i == len(parts) - 1) for i, x in enumerate(parts)):
            return f"names {json.dumps(name)}, which {path} does not hold"
    elif not any(path.startswith(r) for r in roots):
        return (f"names {json.dumps(path)}, which is not a test file: it is not under the test roots "
                f"({', '.join(roots) or 'none'}) and names no test (path::name)")
    if changed is not None and not changed(path):
        return (f"names {json.dumps(path)}, which no commit changed after its findings file was first committed: "
                "a test-added test is written after the review that found the survivor")
    return None


def check_mutations(text: str, gates: set, exists, *, text_of=None, findings: str | None = None,
                    test_roots=(), open_ok: bool = False, changed=None) -> tuple[list, list]:
    """(rows that pass their own checks, [what is wrong]) for one mutations file. exists(path) says
    whether a repository path exists; text_of(path) is a file's text, or None. A row's disposition is
    checked only when it survived; a survivor with none is reported as "<id> survived and has no
    disposition". `open: <finding id>` must name a finding in `findings` (the findings file beside it);
    it is reported as still open unless open_ok (the stop check: a reviewer writes it before anyone
    has decided). changed: test_added_problem's commit-order check, when given."""
    try:
        data = json.loads(text)
    except ValueError as e:
        return [], [f"is not JSON: {e}"]
    if not isinstance(data, list):
        return [], ["is not a JSON list of mutation rows"]
    rows, bad, ids = [], [], set()
    for i, r in enumerate(data):
        at = f"row {i + 1}"
        if not isinstance(r, dict):
            bad.append(f"{at} is not an object")
            continue
        if isinstance(r.get("id"), str) and r["id"].strip():
            at = r["id"]
        missing = [k for k in MUTATION_FIELDS if not (isinstance(r.get(k), str) and r[k].strip()
                                                      or k == "gate" and isinstance(r.get(k), list) and r[k])]
        if missing:
            bad.append(f"{at} has no {', '.join(missing)}")
            continue
        if at in ids:
            bad.append(f"{at} repeats an id")
            continue
        ids.add(at)
        g = r["gate"]
        if not (all(_gate_ok(x, gates, exists) for x in g) if isinstance(g, list) else _gate_ok(g, gates, exists)):
            bad.append(f"{at}'s gate {json.dumps(g)} is neither a gate id from the binding's gate list nor a path "
                       "in the repository")
            continue
        verdict = r["verdict"].strip().lower() if isinstance(r["verdict"], str) else None
        if verdict not in VERDICTS:
            bad.append(f"{at}'s verdict {json.dumps(r['verdict'])} is not killed or survived")
            continue
        rows.append(dict(r, verdict=verdict))
        d = r.get("disposition")
        if verdict == "killed":
            if d is not None:
                bad.append(f"{at} is killed and carries a disposition: a disposition is for a survivor")
        elif d is None or (isinstance(d, str) and not d.strip()):
            bad.append(f"{at} {NO_DISPOSITION}")
        elif not isinstance(d, str):
            bad.append(f"{at}'s disposition is not text")
        elif d.startswith("test-added "):
            why = test_added_problem(d[len("test-added "):].strip(), exists, text_of, test_roots, changed)
            if why:
                bad.append(f"{at}'s disposition {why}")
        elif d.startswith("accepted:"):
            if not REACH.search(d):
                bad.append(f"{at}'s accepted disposition does not state its reach as a count "
                           '("reach <n>"; 0 when it reaches nothing)')
        elif d.startswith("open:"):
            fid = (d[len("open:"):].split() or [""])[0].rstrip(".,;:)")
            if not (fid and findings and re.search(rf"(?<![\w-]){re.escape(fid)}(?![\w-])", findings)):
                bad.append(f"{at}'s disposition names finding {json.dumps(fid)}, which the findings file "
                           "beside it does not name")
            elif not open_ok:
                bad.append(f"{at} {OPEN_SURVIVOR} ({fid}): the round that settles it writes `test-added` "
                           "or `accepted:` in its row")
        else:
            bad.append(f"{at}'s disposition is neither `test-added <path>` nor `accepted: <reason>`, nor "
                       "`open: <finding id>` at review time")
    return rows, bad


# ---------- rule (b): the record folder ----------

def marked(files, job) -> bool:
    return any(fnmatch.fnmatchcase(n, g) for n in files for g in job["marks"])


def owed_name(req, name) -> str:
    """The artefact a file matching req's `per` glob owes: what per's * matched, put into req's glob."""
    head, _, tail = req["per"].partition("*")
    return req["glob"].replace("*", name[len(head):len(name) - len(tail)], 1)


def round_name(job, n: int, kind: str) -> str:
    """The declared name of round n's file of this kind (findings or mutations); round 0 is the first review."""
    r = job["rounds"]
    return r["first"][kind] if n == 0 else r["fix"][kind].replace("{n}", str(n))


def declared(name: str, job) -> int | None:
    """The round a declared findings name belongs to, or None."""
    r = job.get("rounds")
    if not r:
        return None
    if name == r["first"]["findings"]:
        return 0
    head, _, tail = r["fix"]["findings"].partition("{n}")
    m = re.fullmatch(re.escape(head) + "([1-9][0-9]*)" + re.escape(tail), name)
    return int(m.group(1)) if m else None


def claimed(jobs: dict, name: str) -> list:
    """The file globs every other job's marks claim: such a file is that job's, never a findings file."""
    return [g for n, j in jobs.items() if n != name for g in j["marks"] + j.get("marksOutsideRefactor", [])]


def lookalike(name: str, job, claimed_globs=()) -> bool:
    """A findings-like file: a .md whose name holds one of the job's rounds.like words, case ignored, and
    that no other job claims."""
    low = name.lower()
    return bool(job.get("rounds")) and low.endswith(".md") and any(w in low for w in job["rounds"]["like"]) \
        and not any(fnmatch.fnmatchcase(name, g) for g in claimed_globs)


def misnamed(names, job, claimed_globs=()) -> list:
    """[(name, the declared findings name it should have, its mutations name)] for each findings-like name
    in one folder that is not a declared round name. The round is the name's own fix<N>, else the
    folder's next free round."""
    taken = {r for n in names if (r := declared(n, job)) is not None}
    out = []
    for n in sorted(names):
        if declared(n, job) is not None or not lookalike(n, job, claimed_globs):
            continue
        m = re.search(r"fix-?([1-9][0-9]*)", n, re.IGNORECASE)
        k = int(m.group(1)) if m else (max(taken) + 1 if taken else 0)
        taken.add(k)
        out.append((n, round_name(job, k, "findings"), round_name(job, k, "mutations")))
    return out


def log_name(job, name: str) -> str | None:
    """The batch log the job's rounds declare beside mutations file `name` (mutations.json ->
    rounds.first.log), or None when the job declares none or `name` is no round's mutations file."""
    r = job.get("rounds") if job else None
    if not r or "log" not in r["first"]:
        return None
    if name == r["first"]["mutations"]:
        return r["first"]["log"]
    head, _, tail = r["fix"]["mutations"].partition("{n}")
    m = re.fullmatch(re.escape(head) + "([1-9][0-9]*)" + re.escape(tail), name)
    return r["fix"]["log"].replace("{n}", m.group(1)) if m else None


def reconcile(text: str, log_text: str | None, name: str, log: str) -> list:
    """[what is wrong] between a mutations file and its batch log. The runner writes each row to the log
    first, so the log is what the batch measured: every logged row is in the file with the same runner
    fields (all but the disposition), and every file row that carries a batch is in the log."""
    try:
        data = json.loads(text)
    except ValueError:
        return []  # check_mutations reports it
    if not isinstance(data, list):
        return []
    batched = [r for r in data if isinstance(r, dict) and "batch" in r]
    if log_text is None:
        return [f"{r.get('id')} carries batch {json.dumps(r['batch'])}, and there is no {log} beside {name}: "
                "a runner row is checked against its batch log" for r in batched]
    bad, logged = [], []
    for i, line in enumerate(log_text.split("\n")):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            row = e["row"]
            if not (isinstance(row, dict) and isinstance(row.get("id"), str) and e.get("batch") == row.get("batch")):
                raise ValueError("no row with an id and the line's batch")
        except (ValueError, KeyError, TypeError) as x:
            bad.append(f"{log} line {i + 1} is not a batch log line ({x})")
            continue
        logged.append(row)
    have = {}
    for r in data:
        if isinstance(r, dict):
            have.setdefault(r.get("id"), r)
    for row in logged:
        r = have.get(row["id"])
        if r is None:
            bad.append(f"{row['id']} ({row.get('verdict')}) is in {log} (batch {row['batch']}) but not in {name}: "
                       f"an edit dropped it; copy the row back from {log}")
            continue
        for k in row:
            if k != "disposition" and r.get(k) != row[k]:
                bad.append(f"{row['id']}'s {k} in {name} is {json.dumps(r.get(k))}; {log} has {json.dumps(row[k])} "
                           f"(batch {row['batch']}): an edit changed what the runner measured")
    held = {(row["id"], row["batch"]) for row in logged}
    bad += [f"{r.get('id')} carries batch {json.dumps(r['batch'])}, which {log} does not hold for it: a runner row "
            "is checked against its batch log" for r in batched if (r.get("id"), r["batch"]) not in held]
    return bad


def mutations_file(n, files, rid, kind, read, gates, rows, record_at, roots, open_ok, findings=None,
                   changed=None, job=None) -> list:
    text = read.text(files[n])
    if not text.strip():
        return [(rid, kind, f"{n} is empty")]
    at, at_gates = record_at(n) if record_at else (read, gates)
    got, bad = check_mutations(text, at_gates, at.exists, text_of=at.file_text,
                               findings=read.text(files[findings]) if findings else None,
                               test_roots=roots, open_ok=open_ok,
                               changed=changed(findings, n) if changed and findings else None)
    if rows is not None:
        rows[n] = got
    log = log_name(job, n)
    if log:
        bad += reconcile(text, read.text(files[log]) if log in files else None, n, log)
    return [(rid, kind, f"{n}: {b}") for b in bad]


def check_folder(files, job, read: Reader, gates: set = frozenset(), rows: dict | None = None,
                 record_at=None, roots=(), open_ok: bool = False, changed=None, added=None) -> list:
    """[(artefact id, check kind, what is wrong)] for one record folder against one job's contract.
    A mutations check puts the rows it read into `rows`, by file name (an unreadable file holds []).
    record_at(file name) -> (Reader, gate ids) at the commit that last changed that file: a mutation
    record's paths are checked where it was written, not where the register reads it. Without it,
    `read` and `gates` are used. roots: the test roots; open_ok: an `open:` survivor is not reported.
    changed(findings name, mutations name) -> changed(path) or None: test-added's commit-order check.
    added(file name) -> the day its adding commit was made: a `per` file added before the job's
    rounds.from owes nothing. Without it, every one owes its file (the stop check)."""
    out = []
    preds = sorted(n for n in files if pg.PREDICTION_RE.match(n))
    rounds = []
    for n in preds:
        named = None
        try:
            named = pg.check_prediction(pg.load(Path(n), read.text(files[n])), Path(n))["baseline"]
        except pg.BadInput:
            pass
        rounds.append((pg.PREDICTION_RE.match(n).group(1), named))
    rounds = rounds or [("", None)]
    for req in job["require"]:
        if req.get("active") is False:
            continue
        kind, rid = req["check"], req["id"]
        if kind == "prediction":
            if not preds:
                out.append((rid, kind, "no moves.prediction*.json: a prose prediction is not checked by a machine"))
            for n in preds:
                text = read.text(files[n])
                if not text.strip():
                    out.append((rid, kind, f"{n} is empty"))
                    continue
                try:
                    if not pg.check_prediction(pg.load(Path(n), text), Path(n))["moves"]:
                        out.append((rid, kind, f"{n} names no move"))
                except pg.BadInput as e:
                    out.append((rid, kind, f"{n} does not parse under predict-gate's checker: {e}"))
        elif kind in ("baseline", "measured"):
            for rnd, named in rounds:
                name = (named or pg.baseline_name(rnd, lambda x: x in files)) if kind == "baseline" \
                    else f"moves.measured{rnd}.json"
                if name not in files:
                    out.append((rid, kind, f"{name} is missing"))
                    continue
                text = read.text(files[name])
                if not text.strip():
                    out.append((rid, kind, f"{name} is empty"))
                    continue
                try:
                    pg.flat(pg.load(Path(name), text), Path(name))
                except pg.BadInput as e:
                    out.append((rid, kind, f"{name} is not flat JSON: {e}"))
        elif req.get("per"):
            owed = {}
            for m in sorted(files):
                if fnmatch.fnmatchcase(m, req["per"]):
                    owed.setdefault(owed_name(req, m), m)
            for n, m in owed.items():
                if n not in files:
                    if not (added and job.get("rounds") and added(m) < job["rounds"]["from"]):
                        out.append((rid, kind, f"no file matches {n}"))
                elif kind == "mutations":
                    if rows is not None:
                        rows.setdefault(n, [])
                    out += mutations_file(n, files, rid, kind, read, gates, rows, record_at, roots, open_ok, m, changed,
                                          job)
                elif not read.text(files[n]).strip():
                    out.append((rid, kind, f"{n} is empty"))
        else:
            hits = sorted(n for n in files if fnmatch.fnmatchcase(n, req["glob"]))
            if kind == "mutations" and rows is not None:
                for n in hits:
                    rows.setdefault(n, [])
            if not hits:
                out.append((rid, kind, f"no file matches {req['glob']}"))
            elif not any(read.text(files[n]).strip() for n in hits):
                out.append((rid, kind, f"{', '.join(hits)} is empty"))
            elif kind == "mutations":
                for n in hits:
                    out += mutations_file(n, files, rid, kind, read, gates, rows, record_at, roots, open_ok,
                                          job=job)
    return out


# ---------- rule (a): the close ----------

def closes(ledger: Path) -> dict:
    """{task: [every event of that task, in ledger order]}."""
    out = defaultdict(list)
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("task"):
            out[ev["task"]].append(ev)
    return out


def gate_fails(pairs, read: Reader) -> list:
    """Each carried prediction that predict-gate's own check refuses, paired as `predict-gate.sh close`
    pairs it, every file read at the ref."""
    out = []
    for p, _ in pairs:
        folder, name = posixpath.split(p)
        m = pg.PREDICTION_RE.match(name)
        if not m:
            continue
        rnd, texts = m.group(1), {"prediction": read.at_ref(p)}
        try:
            named = pg.named_baseline(Path(p), (texts["prediction"] or b"").decode("utf-8", "replace"))
        except pg.BadInput:
            named = None
        files = {"prediction": p, "measured": f"{folder}/moves.measured{rnd}.json",
                 "baseline": f"{folder}/" + (named or pg.baseline_name(
                     rnd, lambda x: read.at_ref(f"{folder}/{x}") is not None))}
        texts.update((k, read.at_ref(files[k])) for k in ("measured", "baseline"))
        absent = [files[k] for k in files if texts[k] is None]
        if absent:
            out.append(f"{p} (not at the ref: {', '.join(absent)})")
            continue
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = pg.run_check(Path(p), Path(files["baseline"]), Path(files["measured"]), "", "",
                                  {k: v.decode("utf-8", "replace") for k, v in texts.items()})
        except pg.BadInput as e:
            out.append(f"{p} (bad input: {e})")
            continue
        if rc:
            out.append(f"{p} (check exit {rc} against {files['measured']}, baseline {files['baseline']})")
    return out


def close_state(events, read: Reader, own: list, need: set, last: datetime | None,
                measured_at: datetime | None = None, sealed: tuple = (False, None)) -> tuple[str, str | None]:
    """(closed | overridden | not-closed | hash-mismatch | not-its-record | stale, what is wrong). The
    latest close counts. events: every event of this work order, in ledger order. own: its step
    folders, as paths in the repository. need: the files `predict-gate.sh close` hashes in them.
    measured_at: the latest measured file's commit time, for a record the code-order rules judge.
    sealed: (whether the work order has sealed events, why its code is stale or None), from
    predict-gate's sealed_stale: a sealed work order is stale by commits and trees, never by times."""
    at = [i for i, e in enumerate(events or []) if e.get("kind") == CLOSED_KIND]
    if not at:
        return "not-closed", None
    ev = events[at[-1]]
    note = ev.get("note") or ""
    pairs = PAIR.findall(note) + BASE_PAIR.findall(note)
    overridden = note.startswith(OVERRIDDEN)
    since = at[-2] + 1 if len(at) > 1 else 0  # any event of this work order may sit between the two
    if overridden and not any((e.get("kind"), e.get("purpose")) == (REFUSED_KIND, CLOSE_REFUSED)
                              for e in events[since:at[-1]]):
        return "not-its-record", ("the latest work-order-closed event says OVERRIDDEN, and no gate-refused event "
                                  f"from `predict-gate.sh close` (purpose \"{CLOSE_REFUSED}\") of this work order "
                                  "comes after its previous close and before it")
    if not pairs and not overridden:
        return "hash-mismatch", "the latest work-order-closed event carries no sha256"
    foreign = [p for p, _ in pairs if not any(p.startswith(d + "/") and "/" not in p[len(d) + 1:] for d in own)]
    if foreign:
        return "not-its-record", ("the latest work-order-closed event hashes file(s) outside this work order's "
                                  f"record folders: {', '.join(foreign)} (a close hashes only files in its step folders)")
    bad = [p for p, h in pairs if read.sha_at_ref(p) != h]
    if bad:
        return "hash-mismatch", f"the latest work-order-closed event's sha256 does not match {', '.join(bad)}"
    missing = sorted(need - {p for p, _ in pairs})
    if not overridden and not need:
        return "not-its-record", ("the record holds no prediction in a step folder `predict-gate.sh close` reads, "
                                  "so a close of it cannot be clean")
    if not overridden and len(missing) == len(need):
        return "not-its-record", ("the latest work-order-closed event carries none of the files `predict-gate.sh "
                                  f"close` hashes in this record: {', '.join(missing)}")
    if not overridden and missing:
        return "hash-mismatch", f"the latest work-order-closed event does not carry the sha256 of {', '.join(missing)}"
    failed = [] if overridden else gate_fails(pairs, read)
    if failed:
        return "not-its-record", ("predict-gate's own check refuses a pair the latest work-order-closed event "
                                  f"carries: {'; '.join(failed)}")
    if sealed[0]:
        return ("stale", sealed[1]) if sealed[1] else (("overridden" if overridden else "closed"), None)
    try:
        at = datetime.fromisoformat(ev.get("ts") or "")
        at = at if at.tzinfo else at.astimezone()
    except (TypeError, ValueError):
        at = None
    if at is None or (last and at < last and measured_at is None):
        return "stale", ("the latest work-order-closed event has no time" if at is None else
                         f"the latest work-order-closed event ({ev['ts']}) is older than the work order's last "
                         f"code commit ({last.isoformat()})")
    if last and measured_at is not None and last > measured_at:
        return "stale", (f"the work order's last code commit ({last.isoformat()}) is later than its latest measured "
                         f"file's commit ({measured_at.isoformat()}); a close run again measures nothing")
    return ("overridden" if overridden else "closed"), None


# ---------- the check ----------

def stream_currency(repo: Path) -> list:
    """Why the ops ledger is behind the repository, or [] when it is not."""
    why = []
    try:
        p = subprocess.run([str(HERE / "ops-gap.sh")], capture_output=True, text=True,
                           env=dict(os.environ, OPS_REPO=str(repo)))
    except OSError:
        return why
    if p.returncode == 0 and p.stdout.startswith("⚠"):
        why.append(p.stdout.splitlines()[0].lstrip("⚠ ") + " (the post-commit hook's warning)")
    return why


def history(repo, ref, binding: dict, base: str):
    """The record's history at the ref, for check_folder: record_at(path) -> (Reader, gate ids) at the commit
    that last changed path; added(path) -> the day its adding commit was made; changed_at(folder) ->
    check_folder's `changed`, for a folder under base."""
    at_commit = {}

    def record_at(path):
        commit = git(repo, "log", "-1", "--format=%H", ref, "--", path).strip() or ref
        if commit not in at_commit:
            r = Reader(repo, commit)
            at_commit[commit] = (r, gate_ids(binding, lambda x: None if (d := r.at_ref(x)) is None
                                             else d.decode("utf-8", "replace")))
        return at_commit[commit]

    def added(path):
        return git(repo, "log", "-1", "--diff-filter=A", "--format=%cs", ref, "--", path).strip()

    def changed_at(w):
        def at(m, n):
            first = git(repo, "log", "-1", "--diff-filter=A", "--format=%H", ref, "--", f"{base}/{w}/{m}").strip()
            if not first:
                return None
            own = record_at(f"{base}/{w}/{n}")[0].ref
            parents = git(repo, "rev-list", "--parents", "-n", "1", first).split()[1:]

            def changed(p):
                # a non-merge commit that changed p and descends from the findings commit: one on a side
                # branch made before the review is reachable from the record without being after it (F2-C)
                return any(subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", first, c],
                                          capture_output=True, check=False).returncode == 0
                           for c in git(repo, "rev-list", "--no-merges", "--full-history", own,
                                        *(f"^{x}" for x in parents), "--", p).split())
            return changed
        return at

    return record_at, added, changed_at


def build(binding: dict, ledger: Path | None, repo: Path, a, contracts: dict) -> dict:
    jobs = merged_jobs(binding, contracts)
    tracker = binding.get("tracker") if isinstance(binding.get("tracker"), dict) else {}
    if not isinstance(tracker.get("id"), str):
        raise BadInput("the binding has no tracker.id: a work order is a tracker id")
    rx = id_search(tracker["id"])
    sig = binding.get("signal") if isinstance(binding.get("signal"), dict) else {}
    # a command or pointer signal holds no code roots: the binding's records.codeRoots names them
    roots = a.code_roots or (sig.get("codeRoots") if isinstance(binding.get("signal"), dict)
                             else (binding.get("records") or {}).get("codeRoots"))
    if not roots:
        raise BadInput("no code roots: the binding's signal.codeRoots (a rules signal), records.codeRoots "
                       "(a command or pointer signal), or --code-roots")
    ref = a.ref or sig.get("ref") or "HEAD"
    git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    as_of = a.as_of
    since = a.since or _date((binding.get("records") or {}).get("since") or "") \
        or as_of - timedelta(days=contracts["windowDays"])
    warnings = []
    if since > as_of:
        warnings.append(f"the window starts ({since.isoformat()}) after --as-of ({as_of.isoformat()}): it holds nothing")
    reviews = posixpath.normpath(binding.get("reviews") or "axis/runs/")
    form = binding.get("reviewName") or "{date}-{id}-step{n}-review"
    owned = binding.get("owned") is True
    if owned and ledger is None:
        raise BadInput(f"{repo} has no team, so no ops ledger: pass --ledger")

    commits, last, uncredited = code_commits(repo, ref, since, as_of, roots, rx)
    tree, below = record_tree(repo, ref, reviews)
    record_at, added, changed_at = history(repo, ref, binding, reviews)

    per_gate, read_files, read_rows, counted = {}, 0, 0, set()
    test_dirs = test_roots(binding, contracts)
    folder_ids = {f: {m.group().casefold() for m in re.finditer(rx.pattern, f, re.I)} for f in tree}
    closed = closes(ledger) if owned else {}
    seal_scan = None
    read = Reader(repo, ref)
    closer_id = next((n for n, j in jobs.items() if j.get("close") is True), None)
    prover_id = next((n for n, j in jobs.items() if j.get("close") is False), None)
    closer = jobs.get(closer_id, {})

    wos, flags = {}, []
    for wo in sorted(commits):
        folders = sorted(f for f, ids in folder_ids.items() if wo.casefold() in ids)
        step = step_name(form, wo)
        steps = {f for f in folders if step.fullmatch(f)}
        refactor = prover_id is not None and any(
            marked(tree[f], jobs[prover_id]) and not marked(tree[f], closer) for f in folders)
        found = defaultdict(lambda: {"folders": [], "what": [], "kind": None, "owner": None})
        marks = set()
        for f in folders:
            own = {n for n, j in jobs.items() if marked(tree[f], j)}
            if closer_id and not refactor and (f in steps or any(
                    fnmatch.fnmatchcase(n, g) for n in tree[f] for g in closer.get("marksOutsideRefactor", []))):
                own.add(closer_id)
            if refactor and f in steps and closer_id not in own:
                own.add(prover_id)
            held = [(f, tree[f], name) for name in sorted(own)]
            held += [(f"{f}/{sub}", files, name) for sub, files in sorted(below.get(f, {}).items())
                     for name, j in sorted(jobs.items()) if j.get("nested") and marked(files, j)]
            for where, files, name in held:
                job = jobs[name]
                marks.add(name)
                rows = {}
                for rid, kind, what in check_folder(files, job, read, rows=rows, roots=test_dirs,
                                                    record_at=lambda n, w=where: record_at(f"{reviews}/{w}/{n}"),
                                                    changed=changed_at(where),
                                                    added=lambda n, w=where: added(f"{reviews}/{w}/{n}")):
                    hit = found[f"{name}.{rid}"]
                    hit.update(kind="proof" if name == prover_id else kind, owner=job["owner"])
                    if where not in hit["folders"]:
                        hit["folders"].append(where)
                    hit["what"].append(f"{where}: {what}")
                for n, got in sorted(rows.items()):
                    # a folder named by several work orders is read once per work order: count the file once
                    first = f"{where}/{n}" not in counted
                    counted.add(f"{where}/{n}")
                    read_files += first
                    read_rows += len(got) if first else 0
                    for label in sorted({gate_label(r["gate"]) for r in got}):
                        g = per_gate.setdefault(label, {"killed": 0, "survived": 0, "records": 0, "workOrders": []})
                        g["records"] += first
                        if wo not in g["workOrders"]:
                            g["workOrders"].append(wo)
                    for r in got if first else ():
                        per_gate[gate_label(r["gate"])][r["verdict"]] += 1
            for name, job in sorted(jobs.items()):
                if not job.get("rounds"):
                    continue
                places = [(f, tree[f])] + ([(f"{f}/{sub}", files) for sub, files in sorted(below.get(f, {}).items())]
                                           if job.get("nested") else [])
                for where, files in places:
                    for n, exp, mut in misnamed(files, job, claimed(jobs, name)):
                        if added(f"{reviews}/{where}/{n}") < job["rounds"]["from"]:
                            continue
                        hit = found[f"{name}.rounds"]
                        hit.update(kind="rounds", owner=job["owner"])
                        if where not in hit["folders"]:
                            hit["folders"].append(where)
                        hit["what"].append(f"{where}: {n} is not a declared findings name; write it as {exp} and its "
                                           f"rows as {mut} (record-contracts.json, rounds)")
        proof = f"{prover_id}.{jobs[prover_id]['require'][0]['id']}" if refactor else None
        if refactor and proof not in found:
            proofs = [f"{reviews}/{f}/{n}" for f in sorted(steps) for n in tree[f]
                      if (fnmatch.fnmatchcase(n, "moves.measured*.json") if marked(tree[f], closer)
                          else marked([n], jobs[prover_id]))]
            at = git(repo, "log", "-1", "--format=%H", ref, "--", *proofs).strip() if proofs else ""
            after = code_commits(repo, f"{at}..{ref}" if at else ref, since, as_of, roots, rx)[0].get(wo, 0)
            if after:
                found[proof].update(kind="proof", owner=jobs[prover_id]["owner"], folders=sorted(steps),
                                    what=[f"{after} code commit(s) come after the latest step proof"
                                          + (f" ({at[:8]})" if at else ": no step folder holds one")])
        owes = owned and not refactor
        if owes:
            closable = step_name(form, wo, "1", 0)
            need = set()
            for f in folders:
                if not closable.fullmatch(f):
                    continue
                for n in (n for n in tree[f] if pg.PREDICTION_RE.match(n)):
                    rnd = pg.PREDICTION_RE.match(n).group(1)
                    try:
                        named = pg.named_baseline(Path(n), read.text(tree[f][n]))
                    except pg.BadInput:
                        named = None
                    need |= {f"{reviews}/{f}/{x}" for x in
                             (n, f"moves.measured{rnd}.json", named or pg.baseline_name(rnd, lambda x: x in tree[f]))}
            rs = [r for f in folders if closable.fullmatch(f) for r in pg.rounds(repo, f"{reviews}/{f}", ref)]
            judged = any(r[0] and r[0].date().isoformat() >= pg.ORDER_FROM for r in rs)
            measured_at = max((r[1] for r in rs if r[1]), default=None) if judged else None
            # the events close judges, from the same function: sealed is a fact in git
            if owned and seal_scan is None:  # once per run, not once per work order
                seal_scan = pg.sealed_scan(ledger)
            sealed_wo, sealed_evs, _ = pg.sealed_state(ledger, wo, seal_scan) if owned else (False, [], [])
            sealed_close = ((True, pg.sealed_stale(repo, ref, sealed_evs, roots, wo, tracker["id"])[1]) if sealed_wo
                            else (False, None))
            state, why = close_state(closed.get(wo), read, [f"{reviews}/{f}" for f in sorted(steps)], need,
                                     last.get(wo), measured_at, sealed_close)
        elif refactor:
            state, why = ("refactor-unproven" if proof in found else "not-owed"), None
        else:
            state, why = "not-applicable", None
        if owes and state in ("not-closed", "hash-mismatch", "not-its-record", "stale"):
            if state == "not-closed":
                why = f"code commits and no {CLOSED_KIND} event in the ops ledger"
                if not folders:
                    why += "; no record folder names it"
                elif not marks & (work := {n for n, j in jobs.items() if j.get("close") is not None}):
                    why += f"; its record folder(s) ({', '.join(folders)}) mark none of: {', '.join(sorted(work))}"
            found[CLOSE_RULE] = {"folders": folders, "what": [why], "owner": closer.get("owner", "pm"),
                                 "kind": {"not-closed": "close", "not-its-record": "foreign",
                                          "stale": "stale"}.get(state, "mismatch")}
        for rule in sorted(found):
            hit = found[rule]
            flags.append({"workOrder": wo, "rule": rule, "folders": hit["folders"], "what": hit["what"],
                          "owner": hit["owner"],
                          "state": "overridden" if state == "overridden" else
                                   "superseded" if state == "closed" and not steps & {x.split("/")[0] for x in
                                                                                      hit["folders"]}
                                   and not any(OPEN_SURVIVOR in w for w in hit["what"]) else "open",
                          "next": NEXT[hit["kind"]].format(wo=wo)})
        wos[wo] = {"codeCommits": commits[wo], "folders": folders, "jobs": sorted(marks), "close": state}

    rules = {}
    for f in flags:
        r = rules.setdefault(f["rule"], {"workOrders": [], "codeCommits": 0})
        r["workOrders"].append(f["workOrder"])
        r["codeCommits"] += commits[f["workOrder"]]
    for r in rules.values():
        r["promoteToCode"] = len(r["workOrders"]) >= contracts["promoteAt"]
        r["for"] = "owner" if r["promoteToCode"] else None
    if owned:
        stale = stream_currency(repo)
        if stale:
            flags.append({"workOrder": STREAM, "rule": CURRENCY_RULE, "folders": [], "what": stale, "owner": "pm",
                          "state": "open", "next": CURRENCY_NEXT})
    return {
        "_about": "Generated by bakehouse runtime/records-check.py from runtime/record-contracts.json and the "
                  "binding's 'records'. Rewritten on every run; it holds no decision.",
        "computed": {
            "since": since.isoformat(), "ref": ref, "codeRoots": list(roots),
            "reviews": reviews + "/", "ledger": str(ledger) if owned else None, "owned": owned,
            "uncredited": len(uncredited), "workOrders": len(wos), "warnings": warnings,
            "openFlags": sum(1 for f in flags if f["state"] == "open"),
            "promoteAt": contracts["promoteAt"],
        },
        "contracts": {n: {"marks": j["marks"], "owner": j["owner"], "close": j.get("close"),
                          "require": [r["id"] for r in j["require"] if r.get("active") is not False],
                          "inactive": [r["id"] for r in j["require"] if r.get("active") is False]}
                      for n, j in jobs.items()},
        "workOrders": wos,
        "uncreditedCommits": uncredited,
        "flags": flags,
        "rules": dict(sorted(rules.items())),
        "mutations": {"records": read_files, "rows": read_rows, "gates": dict(sorted(per_gate.items()))},
    }


def render(reg: dict, as_of: str, head: str) -> str:
    c = reg["computed"]
    commits = sum(w["codeCommits"] for w in reg["workOrders"].values())
    lines = [f"Records against the process, {c['since']} to {as_of} at `{head}` (code roots: "
             f"{', '.join(c['codeRoots'])}): {c['workOrders']} work order(s) with {commits} code commit(s); "
             f"{c['uncredited']} code commit(s) name no tracker id; {c['openFlags']} open flag(s).", ""]
    lines += [f"WARNING: {w}." for w in c["warnings"]] + ([""] if c["warnings"] else [])
    if not c["owned"]:
        lines += ["The repository is not owned: it keeps no ops ledger, so rule (a) does not apply.", ""]
    inactive = [f"{n}.{i}" for n, j in reg["contracts"].items() for i in j["inactive"]]
    if inactive:
        lines += [f"Listed, not checked (format not defined yet): {', '.join(inactive)}.", ""]
    lines += ["| work order | code commits | close | open flags |", "|---|---|---|---|"]
    for wo, w in reg["workOrders"].items():
        mine = [f["rule"] for f in reg["flags"] if f["workOrder"] == wo and f["state"] == "open"]
        lines.append(f"| {wo} | {w['codeCommits']} | {w['close']} | {', '.join(mine) or '-'} |")
    if reg["uncreditedCommits"]:
        lines += ["", "Code commits that name no tracker id (they belong to no work order; a fix round's subject "
                      "starts with its work-order id):"] + [f"- {x}" for x in reg["uncreditedCommits"]]
    by_rule = defaultdict(list)
    for f in reg["flags"]:
        by_rule[f["rule"]].append(f)
    for rule, fs in sorted(by_rule.items()):
        lines += ["", f"Rule `{rule}` (owner: {fs[0]['owner']}):"]
        for f in fs:
            lines.append(f"- {f['workOrder']}{STATE_NOTE.get(f['state'], '')}: "
                         + "; ".join(f["what"]))
        lines.append("  Next: " + fs[0]["next"].replace(fs[0]["workOrder"], "<work order>"))
    m = reg.get("mutations") or {}
    lines += ["", (f"Kill rate per gate (reported, never a target): {m.get('rows', 0)} mutation row(s) in "
                   f"{m.get('records', 0)} reviewer mutations.json file(s).")]
    if m.get("gates"):
        lines += ["", "| gate | killed | survived | work orders |", "|---|---|---|---|"]
        lines += [f"| {g} | {v['killed']} | {v['survived']} | {', '.join(v['workOrders'])} |"
                  for g, v in m["gates"].items()]
    cand = [(r, v) for r, v in reg["rules"].items() if v["promoteToCode"]]
    if cand:
        lines += ["", f"Promote-to-code candidates for the owner (a rule flagged on {c['promoteAt']} or more "
                      "work orders): " + "; ".join(f"`{r}` {len(v['workOrders'])} work orders, {v['codeCommits']} "
                                                  f"code commits ({', '.join(v['workOrders'])})" for r, v in cand)]
    return "\n".join(lines)


STATE_NOTE = {"overridden": " (overridden by the owner)",
              "superseded": " (superseded: a design or planning folder; the work order closed cleanly)"}


def write_whole(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def project_of(repo: Path):
    """The project of the repository (runtime/locator.py), or None when it has no team."""
    sys.path.insert(0, str(HERE))
    import locator
    try:
        return locator.find(repo, {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"})
    except locator.SetupNeeded as e:
        raise BadInput(str(e))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--binding", help="default: the project's .team/binding.json")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--ref", help="default: the binding's signal.ref, else HEAD")
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    ap.add_argument("--since", type=date.fromisoformat, help="default: records.since, else windowDays before --as-of")
    ap.add_argument("--code-roots", nargs="+",
                    help="default: the binding's signal.codeRoots (a rules signal), else records.codeRoots")
    ap.add_argument("--ledger", help="default: the project's ops ledger")
    ap.add_argument("--register", help="default registers/records.json in the project's records folder. A "
                                       "relative path is taken in that registers/ folder")
    ap.add_argument("--no-write", action="store_true", help="print the block; write no register")
    a = ap.parse_args(argv)
    try:
        repo = Path(git(a.repo, "rev-parse", "--show-toplevel").strip())
        project = project_of(repo)
        if not a.binding and project is None:
            raise BadInput(f"{repo} has no team (no .team/binding.json): run it in a project's repository, or "
                           "pass --binding")
        bpath = Path(a.binding) if a.binding else project.binding
        try:
            binding = json.loads(bpath.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise BadInput(f"cannot read the binding {bpath}: {e}")
        if not isinstance(binding, dict):
            raise BadInput(f"the binding {bpath} is not a JSON object")
        register = None if a.no_write else Path(a.register or "records.json")
        if register is not None and not register.is_absolute():
            if project is None:
                raise BadInput(f"{repo} has no team, so no records folder: pass --register with a full path")
            register = project.records / "registers" / register
        ledger = Path(a.ledger) if a.ledger else project.ops_ledger if project else None
        reg = build(binding, ledger, repo, a, load_contracts())
    except BadInput as e:
        print(f"records-check: bad input: {e}", file=sys.stderr)
        refusal_event(e, path=Path(a.repo).resolve())
        return 2
    if register is not None:
        write_whole(register, json.dumps(reg, indent=2, ensure_ascii=False) + "\n")
    print(render(reg, a.as_of.isoformat(), git(repo, "rev-parse", "--short", reg["computed"]["ref"]).strip()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
