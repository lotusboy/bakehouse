#!/usr/bin/env python3
"""Write moves.prediction<R>.json from a shadow measured as a flat before/after pair and a written
expect file. It refuses any move the expect file does not explain. Optional: an engineer who derives
the values by reasoning writes the prediction by hand.

Why (2026-11-16): 28 driver scripts did this same job, each written again, and not all of them
refused an unexplained move. A separate tool from the gate, so the gate never judges its own output.
It reuses the gate's functions (runtime/predict-gate.py), so a key moves here exactly as it moves there.

Standard library only. Usage (normally through shadow-predict.sh):

  shadow-predict.py --before B --after S --expect E --out <record>/moves.prediction<R>.json
                    [--scopes K] [--shadow-base SB --sealed-base X]

  B   flat {key: value}: the round's "before" (moves.baseline.json, or for a chained round the earlier
      moves.measured*.json).
  S   flat {key: value}: the shadow's after side, measured by the repo's own measure tool.
  E   the expect file, written before the shadow is read:
        {"about": TEXT, "baseline": FILE, "tolerance": N, "mustNotMove": [KEY, ...],
         "expect": [{"what": KEY, "why": TEXT, ...}, ...]}
      An entry may add: "value": "any"; "count": N; "optional": true; one of "to", "delta", "range";
      "shadow": false (a key the shadow file does not hold; copied as written, with its "to", "delta"
      or "range", or none; a key only in B that it matches first is left out of the diff).
  K   flat {key: scope text}: replaces "why" for that key.
  SB  the shadow's base side; X the record's sealed baseline. The tool refuses when they differ.

Each moved key takes the first entry (not "shadow": false) whose "what" matches it. A key in both
files gets "from" and "to" copied from the files, a new key gets "to", a key that goes gets no value.
A copied value marks the move's scope with "[value from the shadow] ". An entry that gives "to",
"delta" or "range" is checked against the shadow and written as given, with no mark.

Every refusal: docs/PREDICT-GATE.md, "Writing the prediction from a shadow".
Exit: 0 written; 1 refused (nothing written, each key named); 2 bad input.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

MARK = "[value from the shadow] "
FIELDS = {"about", "baseline", "tolerance", "mustNotMove", "expect"}
ENTRY = {"what", "why", "value", "count", "optional", "shadow", "to", "delta", "range"}
KINDS = ("to", "delta", "range")


def predict_gate():
    spec = importlib.util.spec_from_file_location("predict_gate", Path(__file__).resolve().with_name("predict-gate.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = predict_gate()
BadInput = G.BadInput


def written(x: dict) -> dict:
    return {k: x[k] for k in KINDS if k in x}


def marked(text: str) -> bool:
    return text.lstrip().startswith(MARK.strip())


def diff(a: dict, b: dict, tol) -> list:
    """(key, a's value, b's value) for each key that moved from a to b, by the gate's own rule."""
    return G.compare({"moves": [], "mustNotMove": [], "tolerance": tol}, a, b)["unpredicted"]


def check_expect(e, path: Path) -> dict:
    if not isinstance(e, dict):
        raise BadInput(f"{path} must be one object with an 'expect' list")
    unknown = set(e) - FIELDS
    if unknown:
        raise BadInput(f"{path}: unknown field(s) {sorted(unknown)}")
    if "about" in e and not isinstance(e["about"], str):
        raise BadInput(f"{path}: 'about' must be text")
    entries = e.get("expect")
    if not isinstance(entries, list) or not entries:
        raise BadInput(f"{path}: 'expect' must be a list of one or more entries")
    for i, x in enumerate(entries):
        if not isinstance(x, dict) or not isinstance(x.get("what"), str) or not x["what"]:
            raise BadInput(f"{path}: entry {i + 1} needs a 'what' key")
        where = f"{path}: entry {x['what']!r}"
        extra = set(x) - ENTRY
        if extra:
            raise BadInput(f"{where} has unknown field(s) {sorted(extra)}")
        if not isinstance(x.get("why"), str) or not x["why"].strip():
            raise BadInput(f"{where} needs a 'why'")
        if marked(x["why"]):
            raise BadInput(f"{where}: its 'why' starts with {MARK.strip()}, which the tool writes only on a copied value")
        if "value" in x and x["value"] != "any":
            raise BadInput(f"{where}: 'value' can only be \"any\"")
        if "value" in x and written(x):
            raise BadInput(f"{where} gives 'value': \"any\" and {sorted(written(x))}; give one")
        for f in ("optional", "shadow"):
            if f in x and not isinstance(x[f], bool):
                raise BadInput(f"{where}: '{f}' must be true or false")
        c = x.get("count", 0)
        if not isinstance(c, int) or isinstance(c, bool) or c < 0:
            raise BadInput(f"{where}: 'count' must be a whole number >= 0")
        if "count" in x and "optional" in x:
            raise BadInput(f"{where} gives 'count' and 'optional'; give one (a count of keys, or a key that may not move)")
        if x.get("shadow") is False and {"value", "count", "optional"} & set(x):
            raise BadInput(f"{where}: a \"shadow\": false entry is copied as written; "
                           "'value', 'count' and 'optional' have no meaning on it")
    # The gate's own format rules: tolerance, baseline, mustNotMove, one of to/delta/range, and an
    # entry's "what" that is also in mustNotMove.
    G.check_prediction({f: e[f] for f in ("tolerance", "baseline", "mustNotMove") if f in e} |
                       {"moves": [{"what": x["what"]} | written(x) for x in entries]}, path)
    return e


def build(e: dict, before: dict, after: dict, scopes: dict) -> tuple[dict, list]:
    """The prediction, and every refusal (empty when there is none)."""
    tol, entries = e.get("tolerance", 0), e["expect"]
    hit = [G.key_matcher(x["what"]) for x in entries]
    shadow = [i for i, x in enumerate(entries) if x.get("shadow", True)]
    moved = diff(before, after, tol)
    taken, matched, moves, refused = [0] * len(entries), [False] * len(entries), [], []
    for k, b, a in moved:
        if "*" in k or "?" in k:
            refused.append(f"{k}: the key holds * or ?, which the gate reads as a wildcard in a move and has no "
                           "escape for; the repo's measure tool renames the key")
            continue
        i = next((i for i in shadow if hit[i](k)), None)
        for j in shadow:
            if hit[j](k):
                matched[j] = True
                if i is not None and j > i and written(entries[j]):
                    refused.append(f"{k}: the earlier entry {entries[i]['what']!r} takes it, so the later entry "
                                   f"{entries[j]['what']!r} never checks its written value "
                                   f"({G.expected(written(entries[j]))}); put the entry with the value first")
        if i is None:
            j = next((j for j, x in enumerate(entries) if j not in shadow and hit[j](k)), None)
            refused.append(f"{k}: {G.show(b)} -> {G.show(a)}, and no expect entry explains it" if j is None else
                           f"{k}: {G.show(b)} -> {G.show(a)} in the shadow, and the entry {entries[j]['what']!r} "
                           "that matches it says \"shadow\": false")
            continue
        x, taken[i] = entries[i], taken[i] + 1
        scope = scopes.get(k, x["why"])
        if "value" in x or (a is G.ABSENT and not written(x)):
            m = {"what": k}
        elif written(x):
            m = {"what": k} | written(x)
            why = G.matches(m, b, a, tol)
            if why:
                refused.append(f"{k}: the expect entry says {G.expected(m)}, the shadow says "
                               f"{G.show(b)} -> {G.show(a)} ({why})")
                continue
        else:
            m = {"what": k} | ({} if b is G.ABSENT else {"from": b}) | {"to": a}
            scope = MARK + scope
        moves.append(m | {"scope": scope})
    for i, x in enumerate(entries):
        if i not in shadow:
            held = [k for k in sorted(after) if hit[i](k)]
            if held:
                refused.append(f"the entry {x['what']!r} says \"shadow\": false, and the shadow file holds {held[0]}"
                               + (f" and {len(held) - 1} more key(s) it matches" if len(held) > 1 else "")
                               + "; the shadow measured it, so give it as an entry the tool checks")
            moves.append({"what": x["what"]} | written(x) | {"scope": scopes.get(x["what"], x["why"])})
        elif "count" in x:
            if taken[i] != x["count"]:
                refused.append(f"{x['what']}: the expect entry says {x['count']} key(s) move, "
                               f"and the shadow moved {taken[i]}")
        elif not taken[i] and not x.get("optional"):
            refused.append(f"{x['what']}: expected to move, and " +
                           ("every moved key it matches was taken by an earlier entry" if matched[i] else
                            "the shadow moved no key it matches") +
                           " (an entry that may not move says \"optional\": true)")
    named = {k for k, _, _ in moved} | {x["what"] for i, x in enumerate(entries) if i not in shadow}
    refused += [f"{k}: the --scopes file gives it a scope, and no move names it" for k in scopes if k not in named]
    pred = {f: e[f] for f in ("about", "baseline", "tolerance") if f in e}
    pred |= {"mustNotMove": e.get("mustNotMove", []), "moves": sorted(moves, key=lambda m: m["what"])}
    return pred, refused


def shadow_before(e: dict, before: dict, after: dict) -> dict:
    """--before without the keys the shadow does not measure: a key absent from --after whose first
    matching entry says "shadow": false. It is not a move that goes; its entry is written as written."""
    hits = [(x, G.key_matcher(x["what"])) for x in e["expect"]]

    def unmeasured(k):
        x = next((x for x, hit in hits if hit(k)), None)
        return k not in after and x is not None and x.get("shadow") is False
    return {k: v for k, v in before.items() if not unmeasured(k)}


def refuse(lines: list) -> int:
    print("shadow-predict: refused. Nothing was written.")
    print("\n".join(f"  - {line}" for line in lines))
    return 1


def run(a) -> int:
    if (a.shadow_base is None) != (a.sealed_base is None):
        raise BadInput("--shadow-base and --sealed-base go together: give both or neither")
    rnd = G.PREDICTION_RE.match(a.out.name)
    if not rnd or a.out.suffix != ".json":
        raise BadInput(f"--out {a.out.name}: name it moves.prediction<R>.json "
                       "(moves.prediction.json, moves.prediction.fix1.json)")
    there = [n for n in G.prediction_names(rnd.group(1)) if a.out.with_name(n).exists()]
    if there:
        return refuse([f"{a.out.with_name(n)} exists. A prediction is written once; "
                       "a revision goes through predict-gate.sh revise" for n in there])
    e = check_expect(G.load(a.expect), a.expect)
    before = G.flat(G.load(a.before), a.before)
    after = G.flat(G.load(a.after), a.after)
    scopes = G.flat(G.load(a.scopes), a.scopes) if a.scopes else {}
    for k, v in scopes.items():
        if not isinstance(v, str) or not v.strip():
            raise BadInput(f"{a.scopes}: the scope of {k!r} is not text, or is empty")
        if marked(v):
            raise BadInput(f"{a.scopes}: the scope of {k!r} starts with {MARK.strip()}, "
                           "which the tool writes only on a copied value")
    tol, refused = e.get("tolerance", 0), []
    # the baseline the gate reads for this round, beside the prediction (predict-gate.py `check`)
    gate_base = e.get("baseline") or G.baseline_name(rnd.group(1), lambda n: a.out.with_name(n).is_file())
    if a.out.with_name(gate_base).is_file():
        g = G.flat(G.load(a.out.with_name(gate_base)), a.out.with_name(gate_base))
        refused += [f"{k}: --before says {G.show(b)}, {gate_base} (the gate's baseline for this round) says "
                    f"{G.show(s)}" for k, b, s in diff(before, g, tol)]
    elif e.get("baseline"):
        refused.append(f"the expect file names the baseline {gate_base}, and it is not beside {a.out}: "
                       "the gate reads it there")
    if a.shadow_base:
        sb = G.flat(G.load(a.shadow_base), a.shadow_base)
        sealed = G.flat(G.load(a.sealed_base), a.sealed_base)
        refused += [f"{k}: the shadow's base side says {G.show(s)}, the sealed base says {G.show(b)}"
                    for k, b, s in diff(sealed, sb, tol)]
    measured = shadow_before(e, before, after)
    pred, more = build(e, measured, after, scopes)
    refused += more
    if not refused and not pred["moves"]:
        refused.append('the result names no move. A round that moves nothing is written by hand: {"noMoves": true}')
    if refused:
        return refuse(refused)
    r = G.compare(G.check_prediction(pred, a.out), measured, after)
    if r["failures"]:
        print(G.report(r, {"prediction": f"{a.out} (not written)", "baseline": a.before, "measured": a.after}))
        return refuse([f"the self-check found {r['failures']} failure(s), in sections 3 to 6 above"])
    try:
        with a.out.open("x", encoding="utf-8") as f:
            f.write(json.dumps(pred, indent=2) + "\n")
    except FileExistsError:
        return refuse([f"{a.out} exists. A prediction is written once"])
    except OSError as err:
        raise BadInput(f"cannot write {a.out}: {err.strerror}")
    copied = sum(m["scope"].startswith(MARK) for m in pred["moves"])
    print(f"wrote {a.out}: {len(pred['moves'])} move(s), {copied} with values from the shadow. "
          "The self-check against the pair passed.")
    return 0


def main(argv) -> int:
    ap = argparse.ArgumentParser(prog="shadow-predict.sh",
                                 description="Write moves.prediction<R>.json from a shadow's flat pair.")
    ap.add_argument("--before", required=True, type=Path, help="flat {key: value}: the round's before")
    ap.add_argument("--after", required=True, type=Path, help="flat {key: value}: the shadow's after side")
    ap.add_argument("--expect", required=True, type=Path, help="the expect file, written before the shadow")
    ap.add_argument("--out", required=True, type=Path, help="<record>/moves.prediction<R>.json; must not exist")
    ap.add_argument("--scopes", type=Path, help="flat {key: scope text}; replaces an entry's why for that key")
    ap.add_argument("--shadow-base", type=Path, help="the shadow's base side; needs --sealed-base")
    ap.add_argument("--sealed-base", type=Path, help="the record's sealed baseline; needs --shadow-base")
    a = ap.parse_args(argv)
    try:
        return run(a)
    except BadInput as e:
        print(f"shadow-predict: bad input: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
