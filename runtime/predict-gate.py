#!/usr/bin/env python3
"""The predicted-moves gate: compare a structured prediction with what a repo measured, and refuse
any move nobody predicted. A stop rule enforced by a machine, not by an agent choosing to stop.

Why (2026-10-10): an engineer committed past a stop rule. The gap register moved 9 -> 11, no move
had been predicted, the engineer judged the move fine and committed. The prediction was prose, and
nothing compared it with the measurement.

Standard library only. Usage (normally through predict-gate.sh):

  predict-gate.py check [--prediction P] [--baseline B] --measured M [--override REASON] [--task ID]
  predict-gate.py close WORK-ORDER [--repo DIR] [--binding FILE] [--merged]
  predict-gate.py seal WORK-ORDER ROUND FILE [--stream S] [--report R]
  predict-gate.py revise|retire WORK-ORDER ROUND FILE --decision ID [--stream S]
  predict-gate.py seal-code WORK-ORDER ROUND [--stream S] -- GIT-COMMIT-ARGS   # the round's first code commit
  predict-gate.py verify-chain [--stream S | --ledger FILE]
  predict-gate.py hook                     # run by the pre-commit hook, inside the repo
  predict-gate.py install-hook REPO

The three files:
  P  moves.prediction.json (or .yaml when PyYAML is installed). Written before the first edit.
       {"moves": [{"what": KEY, "from": X, "to": Y, "scope": TEXT}, ...],
        "mustNotMove": [KEY, ...], "tolerance": 0, "baseline": FILE}
     A move says how its key moves by exactly one of: "to" (with "from" optional), "delta",
     "range" [lo, hi] (inclusive, the value after), or nothing ("moves, any value", for a hash).
     KEY may hold * (any text) and ? (one character). Every other character, [ and ] included, is
     literal; the fnmatch escapes [[] and []] still read as [ and ]. "scope" is a label for the
     reader; it does not change matching.
     A key a move names is judged by that move even when a mustNotMove glob also matches it, so
     "mustNotMove": ["premium:*"] beside one predicted premium move means "no OTHER premium moves".
  B  moves.baseline.json  flat {KEY: value}, measured BEFORE the change.
  M  moves.measured.json  flat {KEY: value}, measured AFTER the change.

Rounds. A fix round names its files moves.prediction<R>.json, moves.measured<R>.json and, when it
has its own, moves.baseline<R>.json, where <R> is "." or "-" and a label (".fix3", "-fix2"). The
baseline is, in order: --baseline; the prediction's "baseline" field (a file name beside it, for a
round chained on another measurement, e.g. "moves.measured.built-v5.json"); moves.baseline<R>.json;
moves.baseline.json. `check` finds P and B beside M from M's round when they are not given.

close WORK-ORDER: see docs/PREDICT-GATE.md, "Closing a work order". Reads the record folder from git
at HEAD; refuses unless every top-level prediction names a move and passes against its own round's
measured file, and every top-level measured file is part of a pair. history/ is listed, never checked.
Four exceptions need the owner's decision, cited in a `Prediction-approved: <decision id>` git
trailer on the commit that made them and answered in the stream's decision ledger before that
commit: a prediction changed after its measurement; a commit touching history/ that is not before
the record's first measurement (the first commit that added a top-level measured file, over the
folder's whole history); a top-level prediction or measured file removed (deleted or renamed) in or
after that commit and absent at HEAD; a named baseline not committed before its prediction. A
decision asked with an approving option (decision.sh ask --approves) approves only when its answer
chose that option and its task is this work order; one asked without it keeps the older rule, an
answer agreed true. With the binding's signal.codeRoots, the code-order rules (review A4, owner D5):
each code commit of the work order (the first tracker id in its subject) comes after its round's
prediction commit and no later than that round's measurement, and none comes after the latest
measured file's commit. Ordered by commit time; judged only for work orders with a round whose
prediction is first committed on or after ORDER_FROM. An uncommitted or untracked change under the code
roots refuses any close, with no cut-off. The owner's override:
PREDICT_GATE_CLOSE_OVERRIDE="<reason>", recorded as a `gate-refused` event first.

close WORK-ORDER --merged (step 10, owner D2): checks only that the files the latest work-order-closed
event of the work order hashed hash the same at HEAD; refuses when there is no such event. No
override. It writes a `note` event, never a work-order-closed one.

seal, revise, retire, verify-chain (owner d20261103-2a691d): docs/PREDICT-GATE.md, "Sealing". Each writes
one event to the stream's ops ledger, under the ledger lock, chained to the line before it
(runtime/ops_ledger.py). close does not read them yet.

Exit: 0 pass (or failed and overridden, recorded); 1 the gate refused; 2 bad input.
"""

from __future__ import annotations

import datetime
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PREDICTION_NAMES = ("moves.prediction.json", "moves.prediction.yaml", "moves.prediction.yml")
BASELINE_NAME = "moves.baseline.json"
MEASURED_NAME = "moves.measured.json"
ROUND = r"((?:[.-][A-Za-z0-9][A-Za-z0-9_.-]*)?)"
PREDICTION_RE = re.compile(rf"^moves\.prediction{ROUND}\.(?:json|ya?ml)$")
MEASURED_RE = re.compile(rf"^moves\.measured{ROUND}\.json$")
LEGACY_ESCAPE = re.compile(r"\[\[\]|\[\]\]")
HOOK_MARKER = "# installed-by: permanence runtime/predict-gate.sh"
HISTORY = "history"
CLOSE_OVERRIDE = "PREDICT_GATE_CLOSE_OVERRIDE"
TRAILER = "Prediction-approved"
DECISION_ID = re.compile(r"d\d{8}-[0-9a-f]{6}")
# The code-order rules judge only rounds whose prediction is first committed on or after this day
# (owner d20261103-fb2f2c): records made before /perma-change said to commit the prediction first
# keep their result. Move it to the day the install takes this change if that is later.
ORDER_FROM = "2026-11-04"
ABSENT = object()


class BadInput(Exception):
    pass


def load(path: Path, text: str | None = None):
    if text is None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise BadInput(f"cannot read {path}: {e.strerror}")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            try:
                return json.loads(text)
            except ValueError:
                raise BadInput(f"{path} is YAML and PyYAML is not installed: write it as JSON")
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise BadInput(f"{path} is not valid YAML: {e}")
    try:
        return json.loads(text)
    except ValueError as e:
        raise BadInput(f"{path} is not valid JSON: {e}")


def flat(data, path: Path) -> dict:
    if not isinstance(data, dict):
        raise BadInput(f"{path} must be one JSON object of key -> value")
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            raise BadInput(f"{path}: key {k!r} holds a {type(v).__name__}; values must be flat")
    return data


def prediction_names(rnd: str) -> list:
    return [f"moves.prediction{rnd}{n[len('moves.prediction'):]}" for n in PREDICTION_NAMES]


def baseline_name(rnd: str, exists) -> str:
    """The round's own baseline when it has one, else the first round's."""
    own = f"moves.baseline{rnd}.json"
    return own if rnd and exists(own) else BASELINE_NAME


def key_matcher(pat: str):
    """`*` and `?` are wildcards; every other character, `[` and `]` included, is literal. The
    fnmatch escapes `[[]` and `[]]`, which predictions used for a literal bracket before, still read
    as `[` and `]`. A pattern equal to a key always matches that key."""
    literal = LEGACY_ESCAPE.sub(lambda m: m.group()[1], pat)
    if "*" not in literal and "?" not in literal:
        return lambda k: k == pat or k == literal
    parts = re.split(r"(\[\[\]|\[\]\]|\*|\?)", pat)
    rx = re.compile("".join({"[[]": r"\[", "[]]": r"\]", "*": ".*", "?": "."}.get(t) or re.escape(t)
                            for t in parts), re.S)
    return lambda k: k == pat or rx.fullmatch(k) is not None


def check_prediction(p, path: Path) -> dict:
    if not isinstance(p, dict):
        raise BadInput(f"{path} must be one object with 'moves' and 'mustNotMove'")
    unknown = set(p) - {"moves", "mustNotMove", "tolerance", "about", "baseline"}
    if unknown:
        raise BadInput(f"{path}: unknown field(s) {sorted(unknown)}")
    moves, mnm = p.get("moves") or [], p.get("mustNotMove") or []
    if not isinstance(moves, list) or not isinstance(mnm, list):
        raise BadInput(f"{path}: 'moves' and 'mustNotMove' must be lists")
    tol = p.get("tolerance", 0)
    if not isinstance(tol, (int, float)) or tol < 0:
        raise BadInput(f"{path}: 'tolerance' must be a number >= 0")
    for i, m in enumerate(moves):
        if not isinstance(m, dict) or not isinstance(m.get("what"), str):
            raise BadInput(f"{path}: move {i + 1} needs a 'what' key")
        extra = set(m) - {"what", "from", "to", "delta", "range", "scope"}
        if extra:
            raise BadInput(f"{path}: move {m['what']!r} has unknown field(s) {sorted(extra)}")
        kinds = [k for k in ("to", "delta", "range") if k in m]
        if len(kinds) > 1:
            raise BadInput(f"{path}: move {m['what']!r} gives {kinds}; give one of to/delta/range")
        if "from" in m and "to" not in m:
            raise BadInput(f"{path}: move {m['what']!r} gives 'from' without 'to'")
        r = m.get("range")
        if r is not None and not (isinstance(r, list) and len(r) == 2 and all(_num(x) for x in r)):
            raise BadInput(f"{path}: move {m['what']!r}: 'range' must be [low, high], numbers")
        if "delta" in m and not _num(m["delta"]):
            raise BadInput(f"{path}: move {m['what']!r}: 'delta' must be a number")
    base = p.get("baseline")
    if base is not None and not (isinstance(base, str) and base.endswith(".json") and base == Path(base).name
                                 and base != ".json"):
        raise BadInput(f"{path}: 'baseline' must be the name of a .json file beside the prediction")
    for k in mnm:
        if not isinstance(k, str):
            raise BadInput(f"{path}: every mustNotMove entry must be a key")
        if any(m["what"] == k for m in moves):
            raise BadInput(f"{path}: {k!r} is both in mustNotMove and predicted to move")
    return {"moves": moves, "mustNotMove": mnm, "tolerance": tol, "baseline": base}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def same(a, b, tol) -> bool:
    if _num(a) and _num(b):
        return abs(a - b) <= tol
    return a == b


def show(v) -> str:
    return "(absent)" if v is ABSENT else json.dumps(v)


def expected(m: dict) -> str:
    if "to" in m:
        return (f"{json.dumps(m['from'])} -> " if "from" in m else "-> ") + json.dumps(m["to"])
    if "delta" in m:
        return f"change of {m['delta']:+}"
    if "range" in m:
        return f"after in [{m['range'][0]}, {m['range'][1]}]"
    return "moves, any value"


def matches(m: dict, before, after, tol) -> str | None:
    """None when the move happened as predicted; else why not."""
    if "to" in m:
        if "from" in m and not same(before, m["from"], tol):
            return f"baseline was {show(before)}, the prediction said {json.dumps(m['from'])}"
        return None if same(after, m["to"], tol) else f"it reached {show(after)}"
    if "delta" in m:
        if not (_num(before) and _num(after)):
            return "a delta needs numbers before and after"
        return None if abs((after - before) - m["delta"]) <= tol else f"it changed by {after - before:+}"
    if "range" in m:
        lo, hi = m["range"]
        if not _num(after):
            return f"it reached {show(after)}, not a number"
        return None if lo - tol <= after <= hi + tol else f"it reached {show(after)}"
    return None


def compare(pred: dict, base: dict, meas: dict) -> dict:
    tol = pred["tolerance"]
    keys = sorted(set(base) | set(meas))
    moved = {
        k for k in keys
        if (k in base) != (k in meas) or not same(base.get(k), meas.get(k), tol)
    }
    out = {"keys": len(keys), "moved": len(moved), "hit": [], "missed": [], "wrong": [],
           "unpredicted": [], "breach": [], "unmeasured": []}
    covered = set()
    for m in pred["moves"]:
        hit = key_matcher(m["what"])
        hits = [k for k in keys if hit(k)]
        if not hits:
            out["missed"].append((m, m["what"], ABSENT, ABSENT, "no measured key matches it"))
            continue
        for k in hits:
            covered.add(k)
            before, after = base.get(k, ABSENT), meas.get(k, ABSENT)
            if k not in moved:
                out["missed"].append((m, k, before, after, "it did not move"))
                continue
            why = matches(m, before, after, tol)
            out["wrong" if why else "hit"].append((m, k, before, after, why))
    predicted = set(covered)  # a key a move names is judged by that move, not by a mustNotMove glob
    for pat in pred["mustNotMove"]:
        hit = key_matcher(pat)
        hits = [k for k in keys if hit(k)]
        if not hits:
            out["unmeasured"].append(pat)
        for k in hits:
            if k in predicted:
                continue
            covered.add(k)
            if k in moved:
                out["breach"].append((pat, k, base.get(k, ABSENT), meas.get(k, ABSENT)))
    out["unpredicted"] = [(k, base.get(k, ABSENT), meas.get(k, ABSENT)) for k in sorted(moved - covered)]
    out["failures"] = len(out["wrong"]) + len(out["unpredicted"]) + len(out["breach"]) + len(out["unmeasured"])
    return out


def report(r: dict, files: dict) -> str:
    lines = ["Predicted-moves gate", f"  prediction: {files['prediction']}",
             f"  baseline:   {files['baseline']}", f"  measured:   {files['measured']}",
             f"Keys measured: {r['keys']}. Keys that moved: {r['moved']}.", ""]

    def section(title, rows, stops):
        lines.append(f"{title}: {len(rows)}." + (" Each one stops the commit." if stops and rows else ""))
        lines.extend(f"  - {row}" for row in rows)
        lines.append("")

    def scope(m):
        return f" [scope: {m['scope']}]" if m.get("scope") else ""

    section("1. Predicted, and it happened",
            [f"{k}: {show(b)} -> {show(a)}. Predicted: {expected(m)}.{scope(m)}"
             for m, k, b, a, _ in r["hit"]], False)
    section("2. Predicted, and it did not happen (reported; does not stop)",
            [f"{k}: {show(b)} -> {show(a)}. Predicted: {expected(m)}. Not met: {why}.{scope(m)}"
             for m, k, b, a, why in r["missed"]], False)
    section("3. Predicted to move, and it moved to a different value",
            [f"{k}: {show(b)} -> {show(a)}. Predicted: {expected(m)}. Not met: {why}.{scope(m)}"
             for m, k, b, a, why in r["wrong"]], True)
    section("4. Moved, and nobody predicted it",
            [f"{k}: {show(b)} -> {show(a)}." for k, b, a in r["unpredicted"]], True)
    section("5. Must not move, and it moved",
            [f"{k}: {show(b)} -> {show(a)} (rule: {pat})." for pat, k, b, a in r["breach"]], True)
    section("6. Must not move, and it was not measured, so it is unproven",
            [f"{pat}: no key in the baseline or the measured file matches it." for pat in r["unmeasured"]], True)
    if r["failures"]:
        lines.append(f"Result: REFUSED. {r['failures']} line(s) in sections 3 to 6. "
                     "An unpredicted move goes to the owner. Do not edit the prediction, a test or an "
                     "output to make this pass; a new prediction needs the owner's go.")
    else:
        lines.append("Result: PASS. Every move was predicted, and nothing in mustNotMove moved.")
    return "\n".join(lines)


def perma_dir() -> Path:
    return Path(os.environ.get("PERMA_DIR") or Path.home() / "permanence")


def ops_event(repo: Path, kind: str, task: str, purpose: str, count, note: str, gate: str | None = None) -> str | None:
    """One line in the repository's ops ledger, outcome done. Returns an error text when it could
    not be written."""
    env = dict(os.environ, OPS_REPO=str(repo))
    try:
        p = subprocess.run([str(perma_dir() / "runtime" / "ops-event.sh"), kind, task or "-", purpose,
                            "-", "-", str(count), "-", "-", "done", note] + ([gate] if gate else []),
                           capture_output=True, text=True, env=env)
    except OSError as e:
        return f"cannot run ops-event.sh: {e.strerror}"
    return None if p.returncode == 0 else (p.stderr.strip() or f"ops-event.sh exit {p.returncode}")


def record_override(reason: str, task: str, repo: Path, r: dict, gate: str = "predict-gate:check") -> str | None:
    """Write the override to the ops ledger. Returns an error text when it could not be written."""
    note = (f"predicted-moves gate overridden: {reason} | refused lines: wrong {len(r['wrong'])}, "
            f"unpredicted {len(r['unpredicted'])}, mustNotMove moved {len(r['breach'])}, "
            f"unmeasured {len(r['unmeasured'])}; first: "
            + "; ".join(k for k, _, _ in r["unpredicted"][:3]))
    return ops_event(repo, "gate-refused", task, "predicted-moves gate overridden", r["failures"], note, gate)


def refusal_event(gate: str, purpose: str, note: str, task: str | None, count, path, perma: Path | None = None):
    """One gate-refused event for a refusal that stands (docs/PROJECT-TEAM.md, "Gate refusals"). Best
    effort: the gate has refused already, so a failed write changes nothing it prints or returns. Never
    called from run_check, which records-check also runs on every register build."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import gate_event
        gate_event.refused(gate, purpose, note, task=None if task in (None, "-") else task, count=count,
                           path=str(path), perma=perma or perma_dir())
    except Exception:  # noqa: BLE001, S110 - the event reports a refusal; it never changes one
        pass


def refused_lines(r: dict) -> str:
    return (f"wrong {len(r['wrong'])}, unpredicted {len(r['unpredicted'])}, mustNotMove moved {len(r['breach'])}, "
            f"unmeasured {len(r['unmeasured'])}; first: " + "; ".join(k for k, _, _ in r["unpredicted"][:3]))


def named_baseline(pred_path: Path, text: str | None = None) -> str | None:
    """The prediction's own "baseline" field, or None."""
    return check_prediction(load(pred_path, text), pred_path)["baseline"]


def run_check(pred_path: Path, base_path: Path, meas_path: Path, override: str, task: str,
              texts: dict | None = None, repo: Path | None = None, result: dict | None = None,
              gate: str = "predict-gate:check") -> int:
    texts = texts or {}
    pred = check_prediction(load(pred_path, texts.get("prediction")), pred_path)
    base = flat(load(base_path, texts.get("baseline")), base_path)
    meas = flat(load(meas_path, texts.get("measured")), meas_path)
    r = compare(pred, base, meas)
    if result is not None:
        result.update(r, named=len(pred["moves"]))
    print(report(r, {"prediction": pred_path, "baseline": base_path, "measured": meas_path}))
    if not r["failures"]:
        if override:
            print("The override was not needed and was not recorded.")
        return 0
    if not override:
        return 1
    repo = repo or Path(subprocess.run(["git", "-C", str(pred_path.parent), "rev-parse", "--show-toplevel"],
                                       capture_output=True, text=True).stdout.strip() or pred_path.parent)
    err = record_override(override, task, repo, r, gate)
    if err:
        print(f"\nOVERRIDE REFUSED: it could not be recorded in the ops ledger ({err}). "
              "An override is never silent, so the gate stays refused.")
        return 1
    print(f"\nOVERRIDDEN by the owner, recorded in the ops ledger: {override}")
    return 0


def git(repo, *args, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check)


def run_hook() -> int:
    """Inside a repo, at pre-commit: check every staged moves.measured<R>.json against its round's
    prediction in the index. A measured file with no prediction of its round, that no prediction
    names as its baseline, is refused: a measurement that is not part of the record goes in a
    history/ folder, which the hook skips."""
    repo = Path(git(".", "rev-parse", "--show-toplevel").stdout.strip())
    staged = git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").stdout.split("\0")
    rounds, stray = set(), []
    for f in staged:
        m = MEASURED_RE.match(Path(f).name)
        if not m:
            continue
        parts = Path(f).parent.parts
        if HISTORY not in parts:
            rounds.add((str(Path(f).parent), m.group(1)))
        elif not holds_prediction(repo, parts[:parts.index(HISTORY)]):
            stray.append(f)  # close checks history/ only directly inside a record folder
    if not rounds and not stray:
        return 0
    override = os.environ.get("PREDICT_GATE_OVERRIDE", "")
    task = os.environ.get("PREDICT_GATE_TASK", "-")
    worst = 0
    refused = []  # what stopped the commit; an override that could not be recorded is not counted here
    for f in stray:
        refused.append(f)
        print(f"\nPredicted-moves gate: REFUSED. {f} is staged in a {HISTORY}/ folder whose parent holds no "
              f"moves.prediction*: close lists only <record>/{HISTORY}/ and refuses a moves file in any other "
              "subfolder of a record.")
        worst = 1
    for folder, rnd in sorted(rounds):
        d = Path(folder)

        def staged_text(name):
            p = git(repo, "show", f":{(d / name).as_posix()}", check=False)
            return p.stdout if p.returncode == 0 else None

        pred_name = next((n for n in prediction_names(rnd) if staged_text(n) is not None), None)
        measured = f"moves.measured{rnd}.json"
        print(f"\n== {folder}" + (f" (round {rnd})" if rnd else ""))
        if rnd and pred_name is None:
            if measured in named_baselines(repo, d, staged_text):
                print(f"{measured}: a prediction in this folder names it as its baseline; not a round.")
                continue
            print(f"Predicted-moves gate: REFUSED. {folder}/{measured} is staged, and no "
                  f"moves.prediction{rnd}.json is in the index beside it. A measurement that is not part "
                  f"of the record goes in {folder}/{HISTORY}/ (git mv).")
            worst = 1
            refused.append(f"{folder}/{measured}")
            continue
        try:
            named = named_baseline(repo / d / pred_name, staged_text(pred_name)) if pred_name else None
        except BadInput:
            named = None  # reported by the check below
        base_name = named or baseline_name(rnd, lambda n: staged_text(n) is not None)
        missing = [n for n, t in ((f"moves.prediction{rnd}.json", pred_name),
                                  (base_name, staged_text(base_name))) if t is None]
        if missing:
            print(f"Predicted-moves gate: REFUSED. {folder}/{measured} is staged, and "
                  f"{', '.join(missing)} is not in the index beside it. A measurement with no "
                  "prediction or no baseline cannot be checked.")
            worst = 1
            refused.append(f"{folder}/{measured}")
            continue
        texts = {"prediction": staged_text(pred_name), "baseline": staged_text(base_name),
                 "measured": staged_text(measured)}
        try:
            rc = run_check(repo / d / pred_name, repo / d / base_name, repo / d / measured,
                           override, task, texts, repo, gate="predict-gate:hook")
            if rc and not override:
                refused.append(f"{folder}/{measured}")
        except BadInput as e:
            print(f"Predicted-moves gate: bad input: {e}")
            rc = 1
            refused.append(f"{folder}/{measured} (bad input)")
        worst = max(worst, rc)
    if worst:
        print("\nCommit stopped by the predicted-moves gate. Owner-approved exception only: "
              'PREDICT_GATE_OVERRIDE="<reason>" [PREDICT_GATE_TASK=<id>] git commit ...')
    if refused:
        refusal_event("predict-gate:hook", "predicted-moves commit refused",
                      f"predicted-moves gate stopped a commit: {len(refused)} refused; " + "; ".join(refused[:3]),
                      task, len(refused), repo)
    return worst


def holds_prediction(repo: Path, parts: tuple) -> bool:
    """True when the folder made of parts has a moves.prediction* of its own in the index."""
    if not parts:
        return False
    d = Path(*parts)
    names = git(repo, "ls-files", "--cached", "-z", "--", f"{d.as_posix()}/").stdout.split("\0")
    return any(n and Path(n).parent == d and PREDICTION_RE.match(Path(n).name) for n in names)


def named_baselines(repo: Path, d: Path, staged_text) -> set:
    """The baseline names the predictions in the index at folder d give in their "baseline" field."""
    names = git(repo, "ls-files", "--cached", "-z", "--", f"{d.as_posix()}/").stdout.split("\0")
    out = set()
    for n in names:
        if n and Path(n).parent == d and PREDICTION_RE.match(Path(n).name):
            try:
                b = named_baseline(repo / n, staged_text(Path(n).name))
            except BadInput:
                continue
            if b:
                out.add(b)
    return out


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_binding(repo: Path, binding_arg: str | None) -> dict:
    if binding_arg:
        path = Path(binding_arg)
    else:
        path, stream = stream_binding(repo)
        if path is None:
            raise BadInput(f"no stream is registered for {repo} in _meta/REGISTRY.md, so there is no binding")
    try:
        b = json.loads(path.read_text())
    except OSError as e:
        raise BadInput(f"cannot read the binding {path}: {e.strerror}")
    except ValueError as e:
        raise BadInput(f"the binding {path} is not valid JSON: {e}")
    if not isinstance(b, dict):
        raise BadInput(f"the binding {path} is not a JSON object")
    return b


def stream_binding(repo: Path):
    """(binding path, stream) for the repo, through the registry of perma_dir()."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import team_roles
    return team_roles.binding_path(perma_dir(), repo)


def record_folders(repo: Path, b: dict, wo: str) -> tuple[str, list]:
    """(the glob, the matching folders) for the work order: `reviews` + `reviewName`. {date} must be a
    date (YYYY-MM-DD), {n} is 1, {id} is the work order; any other placeholder is one path part."""
    reviews = b.get("reviews") or "axis/runs/"
    name = b.get("reviewName") or "{date}-{id}-step{n}-review"
    if not isinstance(reviews, str) or os.path.isabs(reviews) or ".." in Path(reviews).parts:
        raise BadInput(f"binding reviews {reviews!r} is not a folder inside the repository")
    if not isinstance(name, str):
        raise BadInput(f"binding reviewName {name!r} is not a name")
    fill = {"date": "*", "id": glob.escape(wo), "n": "1"}
    pattern = re.sub(r"\{(\w+)\}", lambda m: fill.get(m.group(1), "*"), name)
    rx = {"date": r"\d{4}-\d{2}-\d{2}", "id": re.escape(wo), "n": "1"}
    exact = re.compile("".join(rx.get(t[1:-1], r"[^/]+") if re.fullmatch(r"\{\w+\}", t) else re.escape(t)
                               for t in re.split(r"(\{\w+\})", name) if t))
    found = sorted(p for p in (repo / reviews).glob(pattern) if p.is_dir() and exact.fullmatch(p.name))
    return f"{reviews.rstrip('/')}/{pattern}", found


def closed_hashes(ledger: Path | None, wo: str) -> dict:
    """sha256 -> work order, for every prediction and measured file an earlier close of ANOTHER work
    order recorded."""
    out = {}
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines() if ledger else []
    except OSError:
        lines = []
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("kind") == "work-order-closed" and ev.get("task") != wo:
            for h in re.findall(r"sha256:([0-9a-f]{64})", ev.get("note") or ""):
                out.setdefault(h, ev.get("task"))
    return out


def decisions(perma: Path, stream: str | None) -> tuple[dict, Path | None]:
    """(decision id -> {ask, answers, outcomes}, the ledger file) for the stream, read by decision.py."""
    if not stream:
        return {}, None
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import decision
    decision.PERMA = perma
    return decision.fold(decision.read_events(stream)), perma / stream / decision.LEDGER


def when(ts) -> datetime.datetime | None:
    try:
        t = datetime.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.astimezone()


def trailer_values(repo: Path, commit: str) -> list:
    """The values of the commit's Prediction-approved trailers: git's own trailer block, never the subject."""
    out = git(repo, "log", "-1", f"--format=%(trailers:key={TRAILER},valueonly,separator=%x00)", commit).stdout
    return [v.strip() for v in out.rstrip("\n").split("\0") if v.strip()]


def approval(repo: Path, commit: str, decided: dict, wo: str) -> tuple[list, str | None]:
    """(the decision ids the commit cites, None) when every one is answered, before the commit's
    committer time, with its approving option and asked for this work order (an ask with no approving
    option: answered agreed true); else (ids, why not)."""
    ids = trailer_values(repo, commit)
    if not ids:
        return [], f"no '{TRAILER}: <decision id>' trailer"
    at = when(git(repo, "log", "-1", "--format=%cI", commit).stdout.strip())
    for i in ids:
        if not DECISION_ID.fullmatch(i):
            return ids, f"'{TRAILER}: {i}' does not name a decision id (d<yyyymmdd>-<6 hex>)"
        why = decision_refusal(decided, i, wo, at)
        if why:
            return ids, why
    return ids, None


def decision_refusal(decided: dict, i: str, wo: str, at, before: str = "the commit") -> str | None:
    """Why decision i does not approve an exception for work order wo at time at; None when it does
    (D1: answered before at, with its approving option and asked for wo; an ask with no approving option:
    answered agreed true). Shared by close's trailers and by revise and retire."""
    d = decided.get(i)
    if not d:
        return f"decision {i} is not in the decision ledger"
    prior = sorted((t, a) for a in d["answers"] if (t := when(a.get("ts"))) and t < at)
    if not prior:
        return f"decision {i} has no answer recorded before {before}"
    ask, last = d["ask"], prior[-1][1]
    if ask.get("approves") is None:
        if last.get("agreed") is not True:
            return f"decision {i}: the latest answer before {before} is not agreed true"
        return None
    if ask.get("task") != wo:
        return f"decision {i} was asked for {ask.get('task') or 'no work order'}, not {wo}"
    import decision
    if decision.norm(last.get("choice")) != decision.norm(ask["approves"]):
        return (f"decision {i}: the latest answer before {before} chose {last.get('choice')!r}, "
                f"not the approving option {ask['approves']!r}")
    return None


def rounds(repo, folder: str, ref: str = "HEAD") -> list:
    """[[first commit time of the round's top-level prediction, last commit time of its top-level
    measured file], ...] over the folder's whole history: a round later moved to history/ or removed
    counts. A round label used again reads as one round. Commit times are the committer's. Public:
    records-check reads it too."""
    out = {}
    at = None
    log = git(repo, "log", "--reverse", "--no-renames", "--format=@%cI", "--name-status", ref, "--",
              f":(glob){glob.escape(folder)}/moves.*").stdout
    for line in log.split("\n"):
        if line.startswith("@"):
            at = when(line[1:])
            continue
        status, _, path = line.partition("\t")
        if not path or status[:1] not in ("A", "M"):
            continue
        name = path.rsplit("/", 1)[-1]
        if m := PREDICTION_RE.match(name):
            r = out.setdefault(m.group(1), [None, None])
            r[0] = r[0] or at
        elif m := MEASURED_RE.match(name):
            out.setdefault(m.group(1), [None, None])[1] = at
    return list(out.values())


def code_order(repo: Path, roots: list, form, wo: str, folders: list) -> list:
    """Refusals of the code-order rules (review A4, owner D5), by commit time, not ancestry: a lane
    branch merges after the record is committed on the run branch. Only a work order with a round whose
    prediction is first committed on or after ORDER_FROM is judged, and only its code after the earlier
    rounds' last record commit."""
    rs = [r for f in folders for r in rounds(repo, f) if r[0]]
    new = [r for r in rs if r[0].date().isoformat() >= ORDER_FROM]
    if not new:
        return []
    old_end = max((max(t for t in r if t) for r in rs if r[0].date().isoformat() < ORDER_FROM), default=None)
    latest = max((r[1] for r in rs if r[1]), default=None)
    # records-check's id_search: the tracker id form as a search for a whole token
    body = re.sub(r"(\$|\\Z)$", "", re.sub(r"^(\^|\\A)", "", form)) if isinstance(form, str) else re.escape(wo)
    rx = re.compile(rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])")
    out = []
    for line in git(repo, "log", "HEAD", "--no-merges", "--format=%H%x09%cI%x09%s", "--", *roots).stdout.splitlines():
        h, at, subject = line.split("\t", 2)
        m = rx.search(subject)
        t = when(at)
        if not m or m.group() != wo or (old_end and t <= old_end):
            continue
        if latest is None or t > latest:
            out.append(f"code commit {h[:8]} ({at}) comes after the latest measured file's commit "
                       f"({latest.isoformat() if latest else 'none'}): measure it in a new round")
        elif not any(r[0] < t <= r[1] for r in new if r[1]):
            out.append(f"code commit {h[:8]} ({at}) has no round whose prediction was committed before it and "
                       "measured no later: a round commits its prediction before its first code edit")
    return out


def merged_check(repo: Path, ledger: Path | None, wo: str) -> tuple[list, list, list]:
    """Step 10 (owner D2): (refusals, matched files, notes) for the record at HEAD against the latest
    work-order-closed event of the work order. Its sha256 values only."""
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines() if ledger else []
    except OSError:
        lines = []
    evs = []
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("kind") == "work-order-closed" and ev.get("task") == wo:
            evs.append(ev)
    if not evs:
        return [(f"no work-order-closed event for {wo} in {ledger or '(no ledger)'}: --merged compares the "
                 f"record with step 9's close, so run `predict-gate.sh close {wo}` first")], [], []
    note = evs[-1].get("note") or ""
    pairs = re.findall(r"(\S+) sha256:([0-9a-f]{64})", note) + re.findall(r"baseline (\S+) \(sha256 ([0-9a-f]{64})\)", note)
    if not pairs:
        return [(f"the work-order-closed event at {evs[-1].get('ts')} hashes no file, so --merged has nothing to "
                 f"compare: run `predict-gate.sh close {wo}`")], [], []
    refused, matched = [], []
    for path, h in pairs:
        blob = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", f"HEAD:{path}"], capture_output=True, check=False)
        got = sha256(blob.stdout) if blob.returncode == 0 else None
        if got == h:
            matched.append(f"{path} sha256:{h}")
        else:
            refused.append(f"{path}: " + ("not in git at HEAD" if got is None else
                                          f"sha256 {got} at HEAD; the close at {evs[-1].get('ts')} recorded {h}"))
    return refused, matched, [f"compared with the work-order-closed event at {evs[-1].get('ts')}"]



# ---------- close of a SEALED work order (V12c step 2a; architect report option 1, points 4 to 6) ----------

def ledger_refusals(ledger: Path | None, wo: str) -> list:
    """Run on EVERY close, before close decides whether the work order is sealed (review H1): the ledger
    must read whole (G3), and the ledger file must still begin with the ledger its last commit holds. A
    ledger file that is gone reads as empty, so its committed lines refuse."""
    if ledger is None:
        return []
    ol = ledger_module()
    out = []
    whole, text = ol.verify(ledger) if ledger.is_file() else (True, "")
    if not whole:
        out.append(f"the ops ledger {ledger} is BROKEN, and close did not write it: "
                   + " ".join(x.removeprefix("Chain: ") for x in text.split("\n"))
                   + f" The owner decides its repair, through a decision (decision.sh ask --task {wo} "
                   "--approves <option>); close judges nothing on it until verify-chain is whole")
    blob = ol.committed(ledger)
    if blob is not None and not (ledger.read_bytes() if ledger.is_file() else b"").startswith(blob):
        out.append(f"the ledger {ledger} differs from its last commit in lines that commit holds: a committed "
                   "line was edited, moved or removed")
    return out


def sealed_scan(ledger: Path) -> dict:
    """Everything sealed_state reads from one ledger, parsed once: HEAD's ledger events, every Sealed-line
    trailer whose line HEAD's ledger no longer holds (with that line, read from its commit), and the
    ledger file's lines no commit holds. A caller that judges many work orders (records-check) makes it once
    per run, so each work order only filters it."""
    ol = ledger_module()
    blob = ol.committed(ledger)
    head_lines = ol.lines_of(blob or b"")
    at_head = {ol.line_sha(x): x for x in head_lines}
    removed, old = [], {}
    for commit, digest, rel in ol.sealed_trailers(ledger):
        if digest in at_head:
            continue
        if (commit, rel) not in old:
            old[commit, rel] = ol.lines_of(ol.committed_at(ledger, commit, rel) or b"")
        line = next((x for x in old[commit, rel] if ol.line_sha(x) == digest), None)
        if line is not None:
            removed.append((commit, ol.parse(line)))
    held = set(head_lines)
    raw = ledger.read_bytes() if ledger.is_file() else b""  # a deleted ledger file reads as empty: it never unseals
    return {"head": [ol.parse(x) for x in head_lines], "removed": removed,
            "loose": [ol.parse(x) for x in ol.lines_of(raw) if x not in held]}


def sealed_state(ledger: Path | None, wo: str, scan: dict | None = None) -> tuple[bool, list, list]:
    """(sealed, the work order's sealed events in the ledger at HEAD, refusals). One answer for close and
    records-check (review H1, H4). Sealed is a fact in git, by any writer (review J1): the ledger at HEAD
    holds a sealed, revised or retired line of the work order, or a Sealed-line trailer in the ledger's
    history names one (read from HEAD's ledger, or, when HEAD no longer holds it, from that commit's own;
    then it refuses as removed). An event of the work order in the ledger file that no commit holds yet
    also counts, and refuses until a new seal anchors; deleting lines never unseals."""
    if ledger is None:
        return False, [], []
    ol = ledger_module()

    def mine(e):
        return isinstance(e, dict) and e.get("kind") in ol.SEALED_KINDS and e.get("task") == wo

    scan = scan or sealed_scan(ledger)
    kept = [e for e in scan["head"] if mine(e)]
    removed = [(c, e) for c, e in scan["removed"] if mine(e)]
    loose = [e for e in scan["loose"] if mine(e)]
    if not (kept or removed or loose):
        return False, [], []
    refused = [f"a sealed event of {wo} was removed after its commit: {c[:8]} sealed its {e.get('kind')} "
               f"{e.get('fileKind')} {e.get('path')}, and the ledger at HEAD no longer holds that line"
               for c, e in removed]
    if loose:
        refused.append(f"{len(loose)} sealed event(s) of {wo} are not in a commit of the ledger: close counts only "
                       "committed seals; seal again, so the anchor commits a new line")
    return True, kept, refused


def history_path(path: str) -> str:
    return (Path(path).parent / HISTORY / Path(path).name).as_posix()


def blob_sha(repo: Path, ref_path: str) -> str | None:
    p = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", ref_path], capture_output=True, check=False)
    return sha256(p.stdout) if p.returncode == 0 else None


def is_ancestor(repo: Path, a: str, b: str) -> bool:
    return git(repo, "merge-base", "--is-ancestor", a, b, check=False).returncode == 0


def names_wo(subject: str, wo: str, form) -> bool:
    """The subject's first tracker id is the work order: code_order's tie, by the binding's id form."""
    body = re.sub(r"(\$|\\Z)$", "", re.sub(r"^(\^|\\A)", "", form)) if isinstance(form, str) else re.escape(wo)
    m = re.compile(rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])").search(subject)
    return bool(m) and m.group() == wo


def head_refusal(repo: Path, ref: str, head: str, sealed_trees: dict | None, roots: list, wo: str | None = None,
                 form=None) -> str | None:
    """Why the code at ref is not the code the latest measurement read; None when it is. By code-root tree:
    a head on ref's first-parent line is compared with ref itself; a head a merge brought in, with that
    merge's lane parent; a head ref cannot reach (not merged, or amended away), with ref and every merged
    lane parent."""
    have = code_trees(repo, head, roots)
    want = have or sealed_trees
    if want is None:
        return (f"the sealed head {head[:8]} is not in this repository, and its seal holds no code trees: fetch "
                "the lane branch that holds it (git fetch <lane clone> <branch>), then close again")
    merges = git(repo, "rev-list", "--first-parent", "--merges", ref).stdout.split()
    if have is not None and is_ancestor(repo, head, ref):
        full = git(repo, "rev-parse", head).stdout.strip()
        if full in git(repo, "rev-list", "--first-parent", ref).stdout.split():
            return None if code_trees(repo, ref, roots) == want else (
                f"the sealed head {head[:8]} is on {ref}'s own line, and the code at {ref} differs from the code "
                "it measured: code came after the latest measurement; measure it in a new round")
        for m in reversed(merges):
            if is_ancestor(repo, head, f"{m}^2"):
                if code_trees(repo, f"{m}^2", roots) != want:
                    return (f"the sealed head {head[:8]} was merged by {m[:8]}, and that merge's lane parent holds "
                            "other code than the latest measurement read: measure it in a new round")
                # review H3: the same work order's code committed straight on ref after the merge
                after = git(repo, "log", "--first-parent", "--no-merges", "--format=%H%x09%s", f"{m}..{ref}",
                            "--", *roots).stdout.splitlines()
                late = [h for h, s in (x.split("\t", 1) for x in after) if wo and names_wo(s, wo, form)]
                return None if not late else (
                    f"code commit {late[-1][:8]} names {wo}, touches the code roots and came on {ref}'s own line "
                    f"after the merge {m[:8]} of its measured lane: measure it in a new round")
    if any(code_trees(repo, c, roots) == want for c in [ref] + [f"{m}^2" for m in merges[:100]]):
        return None
    return (f"the sealed head {head[:8]} is not merged into {ref}, or was amended: no code at {ref} or at a merged "
            "lane's parent equals the code the latest measurement read")


def sealed_stale(repo: Path, ref: str, evs: list, roots: list | None, wo: str | None = None,
                 form=None) -> tuple[bool, str | None]:
    """records-check's stale rule for a sealed work order (point 6): (True, why) when the code at ref is not
    the code its latest measured seal read, by head_refusal; (True, None) when it is; (False, None) for a
    work order with no sealed event. It compares commits and trees, never times."""
    ol = ledger_module()
    mine = [e for e in evs if isinstance(e, dict) and e.get("kind") in ol.SEALED_KINDS]
    if not mine:
        return False, None
    retired = {(r.get("path"), r.get("sha256")) for r in mine if r.get("kind") == "retired"}
    measured = [e for e in mine if e.get("kind") in ("sealed", "revised") and e.get("fileKind") == "measured"
                and isinstance(e.get("path"), str) and (history_path(e["path"]), e.get("sha256")) not in retired]
    if not measured or not roots:
        return True, None
    m = measured[-1]
    if not m.get("head"):
        return True, f"{m['path']}: the latest measured seal names no head commit"
    return True, head_refusal(repo, ref, m["head"], m.get("headTrees"), roots, wo, form)


def sealed_refusals(repo: Path, folders: list, evs: list, decided: dict, wo: str, roots: list | None,
                    form=None) -> list:
    """The sealed rules: the first seal of a file is its prediction or measurement, a later different
    hash needs an approved revise; every sealed file is at HEAD with its latest sealed bytes, or retired
    to history/ by an approved retire; every moves file at HEAD is sealed; every refused check is
    retired; a round's prediction is sealed before its code; the code at HEAD is the code the latest
    measurement read. Decisions are judged again here, by the rule revise and retire apply."""
    out = []
    now = datetime.datetime.now().astimezone()
    at_head = {}
    for f in folders:
        for e in git(repo, "ls-tree", "-r", "-z", "HEAD", "--", f"{f}/").stdout.split("\0"):
            if e:
                at_head[e.split("\t", 1)[1]] = blob_sha(repo, e.split()[2])

    def approved(e, verb):
        did = e.get("decision")
        if did not in decided:
            return f"its decision {did} is not in the decision ledger"
        if decided[did]["ask"].get("approves") is None:
            return f"decision {did} was asked without --approves"
        return decision_refusal(decided, did, wo, when(e.get("ts")) or now, before=f"the {verb}")

    seals = [e for e in evs if e.get("kind") in ("sealed", "revised") and isinstance(e.get("path"), str)]
    retires = [e for e in evs if e.get("kind") == "retired"]

    def retire_of(path, digest):
        return next((r for r in reversed(retires) if r.get("sha256") == digest and r.get("path") == history_path(path)),
                    None)

    by_path = {}
    for e in seals:
        by_path.setdefault(e["path"], []).append(e)
    for path, es in sorted(by_path.items()):
        if es[0]["kind"] != "sealed":
            out.append(f"{path}: its first sealed event is a {es[0]['kind']} event, not a seal")
        cur = es[0].get("sha256")
        for e in es[1:]:
            if e.get("sha256") == cur:
                continue
            if e["kind"] != "revised":
                out.append(f"{path}: sealed again with other bytes (sha256:{str(e.get('sha256'))[:12]}) and no revise: "
                           "the first seal stands")
            elif why := approved(e, "revise"):
                out.append(f"{path}: revised to sha256:{str(e.get('sha256'))[:12]} without the owner's approval: {why}")
            cur = e.get("sha256")
        if path in at_head:
            if at_head[path] != cur:
                out.append(f"{path} at HEAD hashes sha256:{str(at_head[path])[:12]}, and its latest seal is "
                           f"sha256:{str(cur)[:12]}: a sealed file changes only through predict-gate.sh revise")
        elif not (r := retire_of(path, cur)):
            out.append(f"{path} is sealed and is not at HEAD, and no retire of it is sealed: a sealed file leaves the "
                       "record only through predict-gate.sh retire, with the owner's decision")
        elif why := approved(r, "retire"):
            out.append(f"{history_path(path)}: retired without the owner's approval: {why}")
        elif at_head.get(history_path(path)) != cur:
            out.append(f"{history_path(path)}, the retired copy, is not at HEAD with its sealed bytes")
    for path in sorted(at_head):
        if Path(path).parent.as_posix() in folders and SEAL_FILE.match(Path(path).name) and path not in by_path:
            out.append(f"{path} is at HEAD and has no committed seal: predict-gate.sh seal {wo} <round> {path}")
    for e in seals:
        if e.get("fileKind") == "measured" and (e.get("check") or {}).get("result") == "refused" \
                and not retire_of(e["path"], e.get("sha256")):
            out.append(f"{e['path']}: its sealed check REFUSED (sha256:{str(e.get('sha256'))[:12]}), and it is not "
                       "retired: a refused measurement leaves the record only through predict-gate.sh retire")
    measured = [e for e in seals if e.get("fileKind") == "measured" and not retire_of(e["path"], e.get("sha256"))]
    if not roots:
        return out
    for e in seals:  # a round's prediction is sealed before its code (review A4, repro 2)
        if e["kind"] != "sealed" or e.get("fileKind") != "prediction" or retire_of(e["path"], e.get("sha256")) \
                or e is not by_path[e["path"]][0]:
            continue
        m = next((x for x in reversed(measured) if x.get("round") == e.get("round")), None)
        if not m or not m.get("base") or not e.get("commit"):
            continue
        s, b, h = e["commit"], m["base"], m.get("head")
        if code_trees(repo, s, roots) is None or code_trees(repo, b, roots) is None:
            out.append(f"{e['path']}: its seal's commit {s} or its measurement's base {b[:8]} is not in this "
                       "repository: fetch the lane branch that holds it, then close again")
            continue
        if is_ancestor(repo, s, b):
            continue
        span = [b, h] if h and code_trees(repo, h, roots) is not None else [b, s]
        changed = git(repo, "diff", "--name-only", *span, "--", *roots).stdout.split()
        early = [f for f in changed if git(repo, "rev-parse", "-q", "--verify", f"{s}:{f}", check=False).stdout
                 != git(repo, "rev-parse", "-q", "--verify", f"{b}:{f}", check=False).stdout]
        if early:
            out.append(f"{e['path']}: its first seal was made at {s}, which already holds code this round measured "
                       f"({', '.join(early[:3])}): a prediction is sealed before its code")
    codes = [e for e in evs if e.get("kind") == "sealed" and e.get("fileKind") == "code"]
    for rnd in dict.fromkeys(x.get("round") for x in measured):  # Step 2b, review H2: the code seal
        m = [x for x in measured if x.get("round") == rnd][-1]
        if not m.get("base"):
            continue
        at_base = code_trees(repo, m["base"], roots)
        at_head = (code_trees(repo, m["head"], roots) if m.get("head") else None) or m.get("headTrees")
        if at_base is None or at_head is None or at_base == at_head:
            continue  # rule 5 or 6 names a missing commit; a round that changed no code has no code commit
        cs = next((x for x in codes if x.get("round") == rnd), None)
        if cs is None:
            out.append(f"round {rnd} of {wo}: its measurement changed code under the code roots, and no code commit of "
                       f"the round is sealed: commit the round's first code through predict-gate.sh seal-code {wo} "
                       f"{rnd} -- <git commit args>")
            continue
        p = next((x for x in evs if x.get("kind") == "sealed" and x.get("fileKind") == "prediction"
                  and x.get("round") == rnd), None)
        if p is None or evs.index(p) > evs.index(cs):
            out.append(f"round {rnd} of {wo}: its prediction was sealed after its first code commit "
                       f"{str(cs.get('commit'))[:8]} was sealed: a prediction is sealed before its code (review H2)")
        before = code_trees(repo, f"{cs.get('commit')}^", roots) if code_trees(repo, str(cs.get("commit")), roots) \
            else None
        if before is None:
            out.append(f"round {rnd} of {wo}: the sealed code commit {str(cs.get('commit'))[:8]} is not in this "
                       "repository: fetch the lane branch that holds it, then close again")
        elif before != at_base:
            out.append(f"round {rnd} of {wo}: the sealed code commit {str(cs.get('commit'))[:8]} is not the round's "
                       f"first code change: the code before it differs from the measured base {m['base'][:8]}")
    if measured:
        m = measured[-1]
        if not m.get("head"):
            out.append(f"{m['path']}: the latest measured seal names no head commit: seal it with --report")
        elif why := head_refusal(repo, "HEAD", m["head"], m.get("headTrees"), roots, wo, form):
            out.append(why)
    return out


def run_close(wo: str, repo_arg: str | None, binding_arg: str | None, merged: bool = False) -> int:
    own = Path(__file__).absolute().parent.parent
    if os.environ.get("PERMA_DIR") and Path(os.environ["PERMA_DIR"]).resolve() != own.resolve():
        raise BadInput(f"PERMA_DIR is {os.environ['PERMA_DIR']}, and this close runs from {own}. Close "
                       "writes only to the ops ledger of the Permanence it runs from: unset PERMA_DIR, or "
                       "run that Permanence's own runtime/predict-gate.sh")
    os.environ["PERMA_DIR"] = str(own)
    for k in ("OPS_TS", "OPS_COMMIT"):  # backfill only: a close is stamped with its own time and commit
        os.environ.pop(k, None)
    top = git(repo_arg or ".", "rev-parse", "--show-toplevel", check=False)
    if top.returncode != 0:
        raise BadInput(f"{repo_arg or os.getcwd()} is not inside a git repository")
    repo = Path(top.stdout.strip())
    b = find_binding(repo, binding_arg)
    form = (b.get("tracker") or {}).get("id") if isinstance(b.get("tracker"), dict) else None
    if isinstance(form, str) and not re.fullmatch(form, wo):
        raise BadInput(f"{wo!r} does not match the binding's tracker id form {form!r}")
    if git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode != 0:
        raise BadInput(f"{repo} has no commit: close reads the record from git")
    stream = stream_binding(repo)[1]
    ledger = own / stream / "repo" / "ops" / "events.jsonl" if stream else None
    earlier = closed_hashes(ledger, wo)
    decided, decision_ledger = decisions(own, stream)
    sealed, seal_events, seal_refused = (False, [], []) if merged else sealed_state(ledger, wo)
    pattern, folders = record_folders(repo, b, wo)
    refused, passed, notes = [], [], []
    if not merged:  # every close, sealed or not (review H1): the ledger reads whole and matches its commit
        refused += ledger_refusals(ledger, wo)

    def rel(p: Path) -> str:
        return p.relative_to(repo).as_posix()

    def first(path: str) -> str | None:
        c = git(repo, "log", "--reverse", "--format=%H", "HEAD", "--", path).stdout.split()
        return c[0] if c else None

    def before(a: str, b: str) -> bool:
        return a != b and git(repo, "merge-base", "--is-ancestor", a, b, check=False).returncode == 0

    def exception(what: str, commit: str, out: list) -> bool:
        """True when the commit carries the owner's approval; else a refusal naming the exception."""
        ids, why = approval(repo, commit, decided, wo)
        if why:
            refused.append(f"{what}: commit {commit[:8]}: {why}. This needs the owner's decision, cited as "
                           f"the trailer '{TRAILER}: <decision id>' on that commit.")
            return False
        out.append(f"{commit[:8]} ({', '.join(ids)})")
        return True

    if merged:
        refused, passed, notes = merged_check(repo, ledger, wo)
    elif not folders:
        refused.append(f"no record folder matches {pattern} (with {{date}} as YYYY-MM-DD)")
    for folder in [] if merged else folders:
        r_folder = rel(folder)
        if folder.is_symlink():
            refused.append(f"{r_folder} is a symbolic link. A record folder is a real folder in git.")
            continue
        dirty = [x for x in git(repo, "--no-optional-locks", "status", "--porcelain", "--untracked-files=all",
                                "--", f"{r_folder}/").stdout.split("\n") if x]
        if dirty:
            refused.append(f"{r_folder}: uncommitted or untracked file(s); close reads the record from git, so "
                           "commit them, or move them out: " + "; ".join(x.strip() for x in dirty))
        tree = {}  # path inside the folder -> (mode, blob)
        for e in git(repo, "ls-tree", "-r", "-z", "HEAD", "--", f"{r_folder}/").stdout.split("\0"):
            if e:
                meta, path = e.split("\t", 1)
                mode, _, blob = meta.split()
                tree[Path(path).relative_to(r_folder).as_posix()] = (mode, blob)
        links = sorted(n for n, (mode, _) in tree.items() if mode == "120000")
        if links:
            refused.append(f"{r_folder}: symbolic link(s) in the record: {', '.join(links)}")
        history = sorted(n for n in tree if n.split("/")[0] == HISTORY)
        measured_at = git(repo, "log", "--reverse", "--diff-filter=A", "--format=%H", "HEAD", "--",
                          f":(glob){glob.escape(r_folder)}/moves.measured*.json").stdout.split()
        if history:
            hist_ok = []
            if measured_at and not sealed:  # a sealed work order's history/ is judged by its retire events
                for c in git(repo, "log", "--format=%H", "HEAD", "--", f"{r_folder}/{HISTORY}/").stdout.split():
                    if not before(c, measured_at[0]):
                        exception(f"{r_folder}/{HISTORY}/ changed in or after the commit of the record's first "
                                  f"measurement ({measured_at[0][:8]})", c, hist_ok)
            notes.append(f"{r_folder}/{HISTORY}/ (listed, not checked): "
                         + ", ".join(n[len(HISTORY) + 1:] for n in history)
                         + (f"; changed after the first measurement, approved: {', '.join(hist_ok)}" if hist_ok else ""))
        if measured_at and not sealed:
            # a round removed outright (git rm, a rename) escapes as surely as one parked in history/ (review A1)
            hist_commits = set(git(repo, "log", "--format=%H", "HEAD", "--", f"{r_folder}/{HISTORY}/").stdout.split()) \
                if history else set()
            ever = {p.rsplit("/", 1)[-1] for p in git(repo, "log", "--format=", "--name-only", "--no-renames", "HEAD",
                                                      "--", f":(glob){glob.escape(r_folder)}/moves.*").stdout.split("\n") if p}
            gone_ok = []
            for n in sorted(n for n in ever if n not in tree and (PREDICTION_RE.match(n) or MEASURED_RE.match(n))):
                c = git(repo, "log", "-1", "--diff-filter=D", "--no-renames", "--format=%H", "HEAD", "--",
                        f"{r_folder}/{n}").stdout.strip()
                if c and c not in hist_commits and not before(c, measured_at[0]):
                    exception(f"{r_folder}/{n} was removed in or after the commit of the record's first measurement "
                              f"({measured_at[0][:8]})", c, gone_ok)
            if gone_ok:
                notes.append(f"{r_folder}: a moves file removed after the first measurement, approved: {', '.join(gone_ok)}")
        hidden = sorted(n for n in tree if "/" in n and n.split("/")[0] != HISTORY
                        and Path(n).name.startswith(("moves.measured", "moves.prediction")))
        if hidden:
            refused.append(f"{r_folder}: moves file(s) in a subfolder other than {HISTORY}/: {', '.join(hidden)}. "
                           f"A file in the record sits at the top level; anything else goes in {HISTORY}/.")
        top_names = {n for n in tree if "/" not in n and tree[n][0] != "120000"}

        def text(n):
            return git(repo, "cat-file", "blob", tree[n][1]).stdout

        def raw(n):
            return subprocess.run(["git", "-C", str(repo), "cat-file", "blob", tree[n][1]],
                                  capture_output=True, check=True).stdout

        preds = sorted(n for n in top_names if PREDICTION_RE.match(n))
        if not preds:
            refused.append(f"{r_folder}: no moves.prediction*.json. A prose prediction is not checked by a machine.")
        named = {}
        for n in preds:
            try:
                named[n] = named_baseline(folder / n, text(n))
            except BadInput:
                named[n] = None  # reported by its check
        paired = {f"moves.measured{PREDICTION_RE.match(n).group(1)}.json" for n in preds} | set(named.values())
        for n in sorted(n for n in top_names if n.startswith("moves.measured") and n not in paired):
            refused.append(f"{r_folder}/{n}: a measured file with no prediction of its round. If it is not "
                           f"part of the closed record, move it into {HISTORY}/ (git mv) and commit.")
        for n in preds:
            rnd = PREDICTION_RE.match(n).group(1)
            meas_n = f"moves.measured{rnd}.json"
            base_n = named[n] or baseline_name(rnd, lambda x: x in top_names)
            pred, meas, base = folder / n, folder / meas_n, folder / base_n
            if meas_n not in top_names:
                refused.append(f"{rel(meas)} not found: the prediction {n} has no measurement (none in git at HEAD)")
                continue
            if base_n not in top_names:
                refused.append(f"{rel(base)} not found: the prediction {n} has no baseline (none in git at HEAD)")
                continue
            commits = git(repo, "log", "--format=%h", "HEAD", "--", rel(pred)).stdout.split()
            meas_at = first(rel(meas))
            late = git(repo, "log", "--format=%H", f"{meas_at}..HEAD", "--", rel(pred)).stdout.split() if meas_at else []
            approved = []
            for c in [] if sealed else late:
                exception(f"{rel(pred)} changed after {meas_n} was first committed ({meas_at[:8]})", c, approved)
            if approved:
                notes.append(f"{rel(pred)} changed after its measurement, approved: {', '.join(approved)}")
            if named[n] and not sealed:
                base_at, pred_at = first(rel(base)), first(rel(pred))
                if not before(base_at, pred_at):
                    exception(f"{rel(base)}, the baseline {n} names, was not committed before it ({pred_at[:8]})",
                              base_at, approved)
            print(f"\n== {r_folder}: {n}")
            result = {}
            try:
                rc = run_check(pred, base, meas, "", wo, {"prediction": text(n), "baseline": text(base_n),
                                                          "measured": text(meas_n)}, result=result)
            except BadInput as e:
                print(f"Predicted-moves gate: bad input: {e}")
                rc = 2
            if rc:
                refused.append(f"{rel(pred)}: check exit {rc}")
                continue
            if not result["named"]:
                refused.append(f"{rel(pred)} names no move. A change predicts what it moves.")
                continue
            if result["moved"] == 0 and result["named"]:
                refused.append(f"{rel(pred)}: 0 keys moved, and the prediction names {result['named']} move(s). "
                               f"{meas_n} is a stale measurement (it equals the baseline).")
                continue
            hp, hm = sha256(raw(n)), sha256(raw(meas_n))
            again = [f"{x} (sha256 already recorded by the close of {earlier[h]})"
                     for x, h in ((rel(pred), hp), (rel(meas), hm)) if h in earlier]
            if again:
                refused.append("evidence of another work order: " + "; ".join(again))
                continue
            passed.append(f"{rel(pred)} sha256:{hp} commits {','.join(commits)}, {rel(meas)} sha256:{hm}, "
                          f"baseline {rel(base)} (sha256 {sha256(raw(base_n))})"
                          + (f", approved: {', '.join(approved)}" if approved else ""))

    sig = b.get("signal") if isinstance(b.get("signal"), dict) else {}
    if folders and not merged:
        if sig.get("codeRoots"):
            # review A4 repro 1: code the record cannot have measured, because git does not hold it
            changed = [x.strip() for x in git(repo, "--no-optional-locks", "status", "--porcelain", "--untracked-files=all",
                                              "--", *sig["codeRoots"]).stdout.split("\n") if x.strip()]
            if changed:
                refused.append("uncommitted or untracked change(s) under the code roots, which no record measured: "
                               "commit them in a measured round, or remove them: " + "; ".join(changed[:10])
                               + (f"; and {len(changed) - 10} more" if len(changed) > 10 else ""))
            if not sealed:
                refused += code_order(repo, sig["codeRoots"], form, wo, [rel(f) for f in folders if not f.is_symlink()])
        else:
            notes.append("the binding names no signal.codeRoots, so the code-order checks were not run")  # nor the dirty-tree one
    if sealed:
        notes.append(f"sealed: judged by {len(seal_events)} committed sealed event(s) in the ledger, not by git order")
        refused += seal_refused + sealed_refusals(repo, [rel(f) for f in folders if not f.is_symlink()], seal_events,
                                                  decided, wo, sig.get("codeRoots"), form)
    print(f"\nWork order {wo}: record folder(s) {', '.join(rel(f) for f in folders) or '(none)'}. "
          f"Pairs passed: {len(passed)}. Ledger: {ledger or '(none)'}. Decisions: {decision_ledger or '(none)'}")
    for n in notes:
        print(f"  note: {n}")
    override = os.environ.get(CLOSE_OVERRIDE, "").strip()
    if refused:
        print(f"Refused: {len(refused)}.")
        for r in refused:
            print(f"  - {r}")
        if not override or merged:
            print("Result: REFUSED. The work order does not close. Owner-approved exception only: "
                  f'{CLOSE_OVERRIDE}="<reason>" predict-gate.sh close {wo}'
                  + (" (the full close; --merged has no override)" if merged else ""))
            refusal_event("predict-gate:close", "work order close refused",
                          "work order close refused | refused: " + "; ".join(refused[:3]), wo, len(refused), repo)
            return 1
        err = ops_event(repo, "gate-refused", wo, "work order close overridden", len(refused),
                        f"work order close overridden: {override} | refused: " + "; ".join(refused[:3]),
                        "predict-gate:close")
        if err:
            print(f"OVERRIDE REFUSED: it could not be recorded in the ops ledger ({err}). "
                  "An override is never silent, so the close stays refused.")
            return 1
        print(f"OVERRIDDEN by the owner, recorded in the ops ledger: {override}")
    note = "; ".join((["OVERRIDDEN, see the gate-refused line"] if refused else []) + (passed or ["no pair passed"])
                     + [f"note: {n}" for n in notes] + [f"ledger: {ledger}"])
    kind, purpose = (("note", "work order close confirmed on the default branch (--merged)") if merged else
                     ("work-order-closed", "work order closed behind the predicted-moves gate"))
    err = ops_event(repo, kind, wo, purpose, len(passed), note)
    if err:
        print(f"Result: REFUSED. The close could not be recorded in the ops ledger ({err}).")
        return 1
    print(f"Result: CLOSED. Recorded in {ledger} as {kind}, with each file's sha256.")
    if not merged:
        wip_line(own, stream, wo, ledger, bool(refused))
    return 0


# ---------- the sealer (owner d20261103-2a691d): close's evidence, written when each file is written ----------

ROUND_ARG = re.compile(r"first|[A-Za-z0-9][A-Za-z0-9_.-]*")
SEAL_FILE = re.compile(rf"^moves\.(prediction|baseline|measured){ROUND}\.(json|ya?ml)$")


def ledger_module():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ops_ledger
    return ops_ledger


def file_round(name: str) -> tuple[str, str]:
    """(file kind, round argument) from a moves file's name; the first round is "first"."""
    m = SEAL_FILE.match(name)
    if not m or (m.group(1) != "prediction" and m.group(3) != "json"):
        raise BadInput(f"{name} is not a moves.prediction, moves.baseline or moves.measured file")
    return m.group(1), m.group(2)[1:] or "first"


def seal_target(path: Path, stream_arg: str | None, wo: str, folder: bool = False) -> tuple[Path, str, str, Path, dict]:
    """(repository, path in it, stream, ledger, stream binding) for a file to seal. The stream is
    --stream, else OPS_STREAM, else the registry's stream for the file's repository: a lane clone is not
    registered. The ledger is the one close reads: the Permanence this tool runs from. The work order
    must match the stream binding's tracker id form, when it names one, for a record in the bound
    repository; a record kept in the Permanence checkout itself (a T- or V-series one) is not judged by it."""
    for k in ("OPS_TS", "OPS_COMMIT"):
        if os.environ.get(k):
            os.environ.pop(k)  # so the refusal event is stamped now too
            raise BadInput(f"{k} is set: a seal is stamped with its own time and commit, never a backfilled one")
    own = Path(__file__).absolute().parent.parent
    if os.environ.get("PERMA_DIR") and Path(os.environ["PERMA_DIR"]).resolve() != own.resolve():
        raise BadInput(f"PERMA_DIR is {os.environ['PERMA_DIR']}, and this seal runs from {own}. A seal writes only "
                       "to the ops ledger of the Permanence it runs from, the one close reads: unset PERMA_DIR, or "
                       "run that Permanence's own runtime/predict-gate.sh")
    os.environ["PERMA_DIR"] = str(own)
    if not (path.is_dir() if folder else path.is_file()):
        raise BadInput(f"cannot read {path}")
    top = git(path if folder else path.parent, "rev-parse", "--show-toplevel", check=False)
    if top.returncode != 0:
        raise BadInput(f"{path} is not inside a git repository")
    repo = Path(top.stdout.strip())
    rel = path.resolve().relative_to(repo.resolve()).as_posix()
    stream = (stream_arg or os.environ.get("OPS_STREAM") or stream_binding(repo)[1] or "").rstrip("/")
    if not stream:
        raise BadInput(f"no stream for {repo}: it is not registered in _meta/REGISTRY.md (a lane clone is "
                       "not). Give --stream <stream> or set OPS_STREAM")
    if stream.startswith("/") or "/../" in f"/{stream}/":
        raise BadInput(f"stream {stream} is not a stream folder inside {perma_dir()}")
    try:
        b = json.loads((perma_dir() / stream / "repo" / "binding.json").read_text())
    except (OSError, ValueError):
        b = None
    if not isinstance(b, dict) or b.get("owned") is not True:
        raise BadInput(f"{stream}/repo/binding.json does not say \"owned\": true, so the stream keeps no ledger")
    form = (b.get("tracker") or {}).get("id") if isinstance(b.get("tracker"), dict) else None
    if isinstance(form, str) and repo.resolve() != own.resolve() and not re.fullmatch(form, wo):
        raise BadInput(f"{wo!r} does not match the binding's tracker id form {form!r}")
    return repo, rel, stream, perma_dir() / stream / "repo" / "ops" / "events.jsonl", b


def code_trees(repo: Path, commit: str, roots: list) -> dict | None:
    """{code root: its git tree id at commit, or None when the root is absent}; None when the commit is
    not in the repository."""
    if git(repo, "cat-file", "-e", f"{commit}^{{commit}}", check=False).returncode != 0:
        return None
    return {r: git(repo, "rev-parse", "-q", "--verify", f"{commit}:{r.rstrip('/')}", check=False).stdout.strip()
            or None for r in roots}


def measured_fields(path: Path, repo: Path, report_arg: str | None) -> tuple[dict, int]:
    """The measured seal's check (against the round's prediction and baseline, as `check` finds them)
    and the base and head commits from the measure report: --report, else measure-report.json (first
    round) or measure-<label>-report.json beside the file; null when there is none."""
    rs = MEASURED_RE.match(path.name).group(1)
    pred = next((path.with_name(n) for n in prediction_names(rs) if path.with_name(n).is_file()), None)
    if pred is None:
        raise BadInput(f"no moves.prediction{rs}.json beside {path}: a measured file is sealed with its check")
    base = path.with_name(named_baseline(pred) or baseline_name(rs, lambda n: path.with_name(n).is_file()))
    result = {}
    rc = run_check(pred, base, path, "", "-", result=result)
    report = Path(report_arg).absolute() if report_arg else path.with_name(
        f"measure-{rs[1:]}-report.json" if rs else "measure-report.json")
    shas = {"base": None, "head": None}
    if report_arg or report.is_file():
        d = load(report)
        try:
            shas = {k: d[k]["sha"] for k in ("base", "head")}
        except (KeyError, TypeError):
            raise BadInput(f"{report} has no base.sha and head.sha")

    def rel(p: Path) -> str:
        return os.path.relpath(p.resolve(), repo.resolve())

    return {"check": {"result": "pass" if rc == 0 else "refused", "failures": result["failures"],
                      "prediction": rel(pred), "predictionSha256": sha256(pred.read_bytes()),
                      "baseline": rel(base), "baselineSha256": sha256(base.read_bytes())},
            **shas, "report": rel(report) if report.is_file() else None}, rc


def run_seal(cmd: str, wo: str, rnd: str, file: str, stream_arg: str | None, report: str | None = None,
             decision_id: str | None = None) -> int:
    """seal: one sealed event for a prediction, baseline or measured file. revise: a prediction changed
    after its seal. retire: a sealed round's file moved to history/. Each writes one chained event under
    the ledger lock; the checks that read the ledger run under the lock too."""
    path = Path(file).absolute()
    repo, rel, stream, ledger, binding = seal_target(path, stream_arg, wo)
    data = path.read_bytes()
    digest = sha256(data)
    if cmd == "retire":
        if HISTORY not in Path(rel).parts[:-1]:
            raise BadInput(f"{rel} is not under a {HISTORY}/ folder: retire follows a round moved to {HISTORY}/")
        fkind = None
    else:
        fkind, frnd = file_round(path.name)
        if frnd != rnd:
            raise BadInput(f"{path.name} is a round {frnd!r} file, not round {rnd!r}")
        if cmd == "revise" and fkind != "prediction":
            raise BadInput(f"{rel} is a {fkind} file: revise follows a prediction changed after its first seal")
    extra, rc = {}, 0
    if cmd in ("revise", "retire"):
        if not DECISION_ID.fullmatch(decision_id):
            raise BadInput(f"--decision {decision_id} is not a decision id (d<yyyymmdd>-<6 hex>)")
        decided, _ = decisions(perma_dir(), stream)
        if decision_id in decided and decided[decision_id]["ask"].get("approves") is None:
            raise BadInput(f"decision {decision_id} was asked without --approves, so it cannot approve a {cmd}: "
                           f"ask one with --task {wo} --approves <option>")
        now = datetime.datetime.now().astimezone()
        why = decision_refusal(decided, decision_id, wo, now, before="now")
        if why:
            raise BadInput(why)
        extra["decision"] = decision_id
    if fkind == "measured":
        fields, rc = measured_fields(path, repo, report)
        extra.update(fields)
        roots = (binding.get("signal") or {}).get("codeRoots") if isinstance(binding.get("signal"), dict) else None
        # the code the measurement read, by tree: close compares it when the head commit itself is gone
        extra["headTrees"] = code_trees(repo, fields["head"], roots) if fields["head"] and roots else None

    def make(events):
        mine = [e for e in events if isinstance(e, dict) and e.get("kind") in ledger_module().SEALED_KINDS
                and e.get("task") == wo]
        kind, fk = {"seal": "sealed", "revise": "revised", "retire": "retired"}[cmd], fkind
        retired = {e.get("sha256") for e in mine if e.get("kind") == "retired"}
        live = [e for e in mine if e.get("kind") != "retired" and e.get("sha256") not in retired]
        if cmd == "seal" and fk in ("prediction", "baseline"):
            same = [e for e in live if e.get("round") == rnd and e.get("fileKind") == fk]
            if same and same[-1].get("sha256") != digest:
                raise BadInput(f"{rel} changed since its seal: " + (
                    "a changed prediction is sealed by predict-gate.sh revise, with the owner's decision"
                    if fk == "prediction" else "a changed baseline is a new round, not a new seal"))
            if not same and any(e.get("round") == rnd and e.get("fileKind") == "measured" for e in live):
                raise BadInput(f"round {rnd} of {wo} has a sealed measured file and no sealed {fk}: a {fk} first "
                               "sealed after its measurement is not evidence of a prediction made before it")
        if cmd == "revise":
            same_round = [e for e in mine if e.get("round") == rnd and e.get("fileKind") == "prediction"]
            if not same_round:
                raise BadInput(f"no sealed prediction of {wo} round {rnd}: seal it before revising it")
            if same_round[-1].get("sha256") == digest:
                raise BadInput(f"{rel} is unchanged since its last seal or revise: there is nothing to revise")
        if cmd == "retire":
            hit = [e for e in mine if e.get("kind") != "retired" and e.get("sha256") == digest
                   and isinstance(e.get("path"), str)
                   and (Path(e["path"]).parent / HISTORY / Path(e["path"]).name).as_posix() == rel]
            if not hit:
                raise BadInput(f"no sealed file of {wo} moved to {rel}: retire takes <the sealed file's folder>/"
                               f"{HISTORY}/<its name>, holding the bytes it was sealed with")
            if hit[-1].get("round") != rnd:
                raise BadInput(f"{rel} was sealed as round {hit[-1].get('round')!r}, not round {rnd!r}")
            if (repo / hit[-1]["path"]).exists():
                raise BadInput(f"{hit[-1]['path']} is still there: retire follows its move to {HISTORY}/")
            fk = hit[-1].get("fileKind")
        commit = git(repo, "rev-parse", "--short", "HEAD", check=False).stdout.strip() or None
        return {"ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "role": "engine",
                "kind": kind, "task": wo, "purpose": f"{kind} {fk} file", "round": rnd, "fileKind": fk,
                "path": rel, "sha256": digest, "commit": commit, "outcome": "done", **extra}

    unanchored = []

    def anchor(line: bytes):  # under the ledger lock: sealers never race each other for git's index
        ev = json.loads(line)
        unanchored.append(ledger_module().anchor(
            ledger, line, f"ops ledger: {ev['kind']} {wo} round {rnd} {ev['fileKind']} (predict-gate.sh {cmd})"))

    ev = ledger_module().append(ledger, make, start=True, then=anchor)
    print(f"{ev['kind'].capitalize()}: {wo} round {rnd}, {ev['fileKind']} {rel}, sha256:{digest[:12]}, "
          f"in {stream}/repo/ops/events.jsonl.")
    if unanchored[0]:
        print(f"ANCHOR REFUSED: the {ev['kind']} line is written to the ledger but is not in a commit "
              f"({unanchored[0]}). Close counts only committed seals: seal again once the cause is gone.",
              file=sys.stderr)
        refusal_event("predict-gate:anchor", f"predict-gate {cmd}: line not anchored",
                      f"the {ev['kind']} line of {rel} is written to the ledger but is not in a commit | {unanchored[0]}",
                      wo, None, path)
        return 1
    if rc:
        print("The sealed check REFUSED: the seal records it, and close needs an approved retire for it.")
    return rc


def run_seal_code(wo: str, rnd: str, stream_arg: str | None, commit_args: list) -> int:
    """Commit the round's first code and seal that commit (Step 2b, review H2): `git commit <commit_args>` in
    the working folder's repository, then one sealed event, fileKind code, with the commit, its parent and
    its code-root trees, anchored as every seal is. Close compares it with the round's prediction seal."""
    if not ROUND_ARG.fullmatch(rnd):
        raise BadInput(f"round {rnd!r} is not 'first' or a round label")
    if any(a == "--all" or (re.fullmatch(r"-[A-Za-z]+", a) and "a" in a[1:]) for a in commit_args):
        raise BadInput("stage the round's code with git add first: seal-code does not read -a")
    top = git(".", "rev-parse", "--show-toplevel", check=False)
    if top.returncode != 0:
        raise BadInput(f"{os.getcwd()} is not inside a git repository")
    repo, _, stream, ledger, binding = seal_target(Path(top.stdout.strip()), stream_arg, wo, folder=True)
    roots = (binding.get("signal") or {}).get("codeRoots") if isinstance(binding.get("signal"), dict) else None
    if not roots:
        raise BadInput(f"{stream}/repo/binding.json names no signal.codeRoots, so no code commit can be told apart")
    if not git(repo, "diff", "--cached", "--name-only", "--", *roots).stdout.strip():
        raise BadInput(f"nothing under the code roots ({', '.join(roots)}) is staged: seal-code commits the round's "
                       "first code")
    p = subprocess.run(["git", "-C", str(repo), "commit", *commit_args], check=False)
    if p.returncode != 0:
        raise BadInput(f"git commit exited {p.returncode}: nothing is sealed")
    commit = git(repo, "rev-parse", "HEAD").stdout.strip()
    parent = git(repo, "rev-parse", "-q", "--verify", "HEAD^", check=False).stdout.strip() or None
    trees = code_trees(repo, commit, roots)

    def make(events):
        mine = [e for e in events if isinstance(e, dict) and e.get("kind") == "sealed" and e.get("task") == wo
                and e.get("fileKind") == "code" and e.get("round") == rnd]
        if mine:
            raise BadInput(f"round {rnd} of {wo} already has a sealed first code commit ({mine[0].get('commit', '')[:8]})"
                           f"; commit {commit[:8]} stays, unsealed")
        return {"ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "role": "engine",
                "kind": "sealed", "task": wo, "purpose": "sealed code commit", "round": rnd, "fileKind": "code",
                "path": None, "sha256": None, "commit": commit, "parent": parent, "codeTrees": trees,
                "outcome": "done"}

    unanchored = []

    def anchor(line: bytes):
        unanchored.append(ledger_module().anchor(
            ledger, line, f"ops ledger: sealed {wo} round {rnd} code commit {commit[:8]} (predict-gate.sh seal-code)"))

    ledger_module().append(ledger, make, start=True, then=anchor)
    print(f"Sealed: {wo} round {rnd}, code commit {commit[:8]}, in {stream}/repo/ops/events.jsonl.")
    if unanchored[0]:
        print(f"ANCHOR REFUSED: the code seal is written to the ledger but is not in a commit ({unanchored[0]}). "
              "Close counts only committed seals: seal again once the cause is gone.", file=sys.stderr)
        refusal_event("predict-gate:anchor", "predict-gate seal-code: line not anchored",
                      f"the code seal of {commit[:8]} is written to the ledger but is not in a commit | {unanchored[0]}",
                      wo, None, repo)
        return 1
    return 0


def run_verify_chain(stream_arg: str | None, ledger_arg: str | None) -> int:
    """Report the ledger's chain: not started, whole, or the first broken link (exit 1)."""
    if ledger_arg:
        ledger = Path(ledger_arg)
    else:
        top = git(".", "rev-parse", "--show-toplevel", check=False)
        stream = stream_arg or os.environ.get("OPS_STREAM") or (
            stream_binding(Path(top.stdout.strip()))[1] if top.returncode == 0 else None)
        if not stream:
            raise BadInput("no stream: give --stream, set OPS_STREAM, or give --ledger")
        ledger = perma_dir() / stream.rstrip("/") / "repo" / "ops" / "events.jsonl"
    if not ledger.is_file():
        raise BadInput(f"no ledger at {ledger}")
    ok, text = ledger_module().verify(ledger)
    print(text)
    return 0 if ok else 1


def wip_line(own: Path, stream: str, wo: str, ledger: Path, overridden: bool = False):
    """One line on the stream's WIP.md board for a close that passed. The board once fell days behind
    while the ledger stayed current: tools wrote the ledger, and only a prompt rule wrote the board."""
    wip = own / stream / "WIP.md"
    if not wip.is_file():
        print(f"note: no {wip}; the close is recorded in the ops ledger only.")
        return
    events = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
    ts = next(ev["ts"] for ev in reversed(events) if ev.get("kind") == "work-order-closed" and ev.get("task") == wo)
    t = datetime.datetime.fromisoformat(ts)
    how = "CLOSED (OVERRIDDEN) by predict-gate close" if overridden else "CLOSED by predict-gate close"
    try:
        text = wip.read_text(encoding="utf-8")
        with wip.open("a", encoding="utf-8") as f:
            f.write(("" if not text or text.endswith("\n") else "\n")
                    + f"  - {t:%H:%M} ({t.day} {t:%b}) — {wo} {how} "
                      f"(ref: {stream}/repo/ops/events.jsonl, work-order-closed at {ts}).\n")
    except OSError as e:
        print(f"note: could not write {wip} ({e}); the close is recorded in the ops ledger only.")
        return
    print(f"Board: one line appended to {wip}.")


def install_hook(repo_arg: str, gate: Path) -> int:
    p = git(repo_arg, "rev-parse", "--show-toplevel", check=False)
    if p.returncode != 0:
        print(f"install-hook: {repo_arg} is not a git repository")
        return 2
    repo = Path(p.stdout.strip())
    hp = git(repo, "config", "--get", "core.hooksPath", check=False).stdout.strip()
    if hp:
        print(f"install-hook: {repo} sets core.hooksPath ({hp}), so git ignores .git/hooks. Not "
              f"installed. Add this line to {hp}/pre-commit instead:\n  \"{gate}\" hook || exit 1")
        return 1
    hook = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-path", "hooks").stdout.strip()) / "pre-commit"
    if (hook.exists() or hook.is_symlink()) and HOOK_MARKER not in hook.read_text(errors="replace"):
        print(f"install-hook: {hook} exists and was not written by Permanence. Left alone. Add this "
              f"line to it instead:\n  \"{gate}\" hook || exit 1")
        return 1
    hook.parent.mkdir(parents=True, exist_ok=True)
    if hook.is_symlink():
        hook.unlink()
    hook.write_text(
        "#!/bin/sh\n"
        f"{HOOK_MARKER}\n"
        "# Refuses a commit whose staged moves.measured.json shows a move its moves.prediction.json\n"
        "# did not predict. Owner-approved exception: PREDICT_GATE_OVERRIDE=\"<reason>\" git commit ...\n"
        "# (recorded in the ops ledger). Delete this file to remove the gate.\n"
        f"exec \"{gate}\" hook\n")
    hook.chmod(0o755)
    print(f"install-hook: {hook} written")
    return 0


def main(argv) -> int:
    gate = Path(__file__).resolve().with_name("predict-gate.sh")
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    where = {}  # for the bad-input event: what the call was about
    try:
        if cmd == "check":
            import argparse
            ap = argparse.ArgumentParser(prog="predict-gate.sh check")
            ap.add_argument("--prediction", type=Path,
                            help="default: the measured file's round's moves.prediction<R>.json beside it")
            ap.add_argument("--measured", required=True, type=Path)
            ap.add_argument("--baseline", type=Path,
                            help=f"default: the round's own moves.baseline<R>.json beside the measured "
                                 f"file, else {BASELINE_NAME}")
            ap.add_argument("--override", default="", help="owner-approved reason; recorded in the ops ledger")
            ap.add_argument("--task", default="-", help="tracker id for the ops ledger line")
            a = ap.parse_args(rest)
            where.update(task=a.task, path=a.measured, perma=perma_dir())
            if "--override" in rest and not a.override.strip():
                raise BadInput("--override needs a reason")
            m = MEASURED_RE.match(a.measured.name)
            rnd = m.group(1) if m else ""
            pred = a.prediction or next((a.measured.with_name(n) for n in prediction_names(rnd)
                                         if a.measured.with_name(n).is_file()), None)
            if pred is None:
                raise BadInput(f"no moves.prediction{rnd}.json beside {a.measured}: give --prediction")
            base = a.baseline or a.measured.with_name(
                named_baseline(pred) or baseline_name(rnd, lambda n: a.measured.with_name(n).is_file()))
            result = {}
            rc = run_check(pred, base, a.measured, a.override.strip(), a.task, result=result)
            if rc == 1:
                # with an override, rc 1 means the override could not be recorded (it keeps its own route, D2);
                # the refusal still stands, and its event routes to the record's stream (review F1)
                unrecorded = " | the owner's override could not be recorded" if a.override.strip() else ""
                refusal_event("predict-gate:check", "predicted-moves gate refused",
                              f"predicted-moves gate refused: {a.measured}{unrecorded} | refused lines: "
                              + refused_lines(result), a.task, result["failures"], a.measured)
            return rc
        if cmd == "close":
            import argparse
            ap = argparse.ArgumentParser(prog="predict-gate.sh close")
            ap.add_argument("work_order")
            ap.add_argument("--repo", help="default: the working directory's repository")
            ap.add_argument("--binding", help="default: the stream's repo/binding.json")
            ap.add_argument("--merged", action="store_true",
                            help="step 10: only check the record's files against the last close's sha256 values")
            a = ap.parse_args(rest)
            # close writes only to the Permanence it runs from (run_close), so its bad input does too
            where.update(task=a.work_order, path=a.repo or os.getcwd(), perma=Path(__file__).absolute().parent.parent)
            return run_close(a.work_order, a.repo, a.binding, a.merged)
        if cmd == "seal-code":
            import argparse
            cut = rest.index("--") if "--" in rest else len(rest)
            head, commit_args = rest[:cut], rest[cut + 1:]
            ap = argparse.ArgumentParser(prog="predict-gate.sh seal-code")
            ap.add_argument("work_order")
            ap.add_argument("round", help='"first" for the first round, else the round label ("fix1")')
            ap.add_argument("--stream", help="default: OPS_STREAM, else the registry's stream for this repository")
            a = ap.parse_args(head)
            where.update(task=a.work_order, path=Path.cwd(), perma=perma_dir())
            return run_seal_code(a.work_order, a.round, a.stream, commit_args)
        if cmd in ("seal", "revise", "retire"):
            import argparse
            ap = argparse.ArgumentParser(prog=f"predict-gate.sh {cmd}")
            ap.add_argument("work_order")
            ap.add_argument("round", help='"first" for the first round\'s files, else the round label ("fix1")')
            ap.add_argument("file")
            ap.add_argument("--stream", help="default: OPS_STREAM, else the registry's stream for the file's repo")
            if cmd == "seal":
                ap.add_argument("--report", help="the measure report holding base.sha and head.sha (measured only)")
            else:
                ap.add_argument("--decision", required=True, help="the owner's decision id that approves it")
            a = ap.parse_args(rest)
            where.update(task=a.work_order, path=Path(a.file).absolute(), perma=perma_dir())
            return run_seal(cmd, a.work_order, a.round, a.file, a.stream, getattr(a, "report", None),
                            getattr(a, "decision", None))
        if cmd == "verify-chain":
            import argparse
            ap = argparse.ArgumentParser(prog="predict-gate.sh verify-chain")
            ap.add_argument("--stream")
            ap.add_argument("--ledger", help="a ledger file, e.g. a scratch copy")
            a = ap.parse_args(rest)
            return run_verify_chain(a.stream, a.ledger)
        if cmd == "hook":
            return run_hook()
        if cmd == "install-hook" and len(rest) == 1:
            return install_hook(rest[0], gate)
    except BadInput as e:
        print(f"predict-gate: bad input: {e}", file=sys.stderr)
        if where:
            refusal_event("predict-gate:input", f"predict-gate {cmd} refused its input",
                          f"predict-gate: bad input: {e}", where["task"], None, where["path"], where["perma"])
        return 2
    print("usage: predict-gate.sh check [--prediction P] --measured M [--baseline B] [--override R] [--task ID]\n"
          "       predict-gate.sh close <work-order> [--repo DIR] [--binding FILE] [--merged]\n"
          "       predict-gate.sh seal <work-order> <round> <file> [--stream S] [--report R]\n"
          "       predict-gate.sh revise|retire <work-order> <round> <file> --decision <id> [--stream S]\n"
          "       predict-gate.sh seal-code <work-order> <round> [--stream S] -- <git commit args>\n"
          "       predict-gate.sh verify-chain [--stream S | --ledger FILE]\n"
          "       predict-gate.sh install-hook <repo>\n"
          "       predict-gate.sh hook", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
