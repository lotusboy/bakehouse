#!/usr/bin/env bash
# Append ONE team-operations event to the ops ledger of the repository you are in. The ledger is the
# owner's working file, so it lives in that repository's stream, never in the repository:
#   <stream>/repo/ops/events.jsonl   (rules beside it in OPERATIONS.md)
# The stream is found from the repository root through _meta/REGISTRY.md, as session start does.
# THE canonical writer: a hand-typed line in a different shape is a silent gap in any report built
# from the ledger. Fields are positional; anything you do not know is left empty, never guessed.
#
# Usage (from inside the repository, or set OPS_REPO to its path):
#   ops-event.sh <kind> <task> <purpose> [model] [effort] [count] [tokens] [duration_s] [outcome] [note] [gate]
#
# OPS_STREAM=<stream> names the stream directly, for a tool that knows it (decision.sh, spend.sh) or whose
# record sits in a stream's own folder. The binding must still say "owned": true. `commit` is then the HEAD
# of the repository at OPS_REPO or the working directory, or null outside one.
#
# BACKFILL ONLY — two env overrides, for an event written after the fact:
#   OPS_TS=2026-09-12T23:01:14+01:00 OPS_COMMIT=5308be8 ops-event.sh ...
# Always pair with `backfilled ...` in `note`, naming where the figure came from: the ledger is
# append-only and a reader must be able to tell a measurement taken at the time from one recovered
# later.
#     kind      agent-run | fan-out | loop | handshake | gate-refused | diagnosis | decision-asked |
#               decision-answered | status-run | session-start | session-end | work-order-closed | note
#               (sealed | revised | retired are written only by predict-gate.sh seal | revise | retire)
#     task      the tracker id of the work, or "-"
#     purpose   one short phrase
#     model     the model id, or -
#     effort    high | medium | low | -
#     count     fan-out width / loop iterations / items offered, or -
#     tokens    subagent tokens as the harness reported them, or -
#     duration_s  seconds, or -
#     outcome   done | refused | partial | stopped | pending | turn-cap | -
#               turn-cap: agent-run only; the agent stopped at its turn cap. `count` = turns used, as the
#               harness reported them (tool uses), and `note` starts "briefed <N|-> of cap <M>": the turns
#               the brief gave it and the agent type's maxTurns. Both are kept as turnsBriefed / turnCap.
#     note      free text, kept short
#     gate      gate-refused only: the refusing gate's id, <tool>:<check> (docs/PROJECT-TEAM.md, "Gate
#               refusals"). Written as the last field, and only when given: every other event keeps its shape.
# Every line is appended under the ledger lock, through runtime/ops_ledger.py. Once the chain has started
# (the first sealed event), every new line carries prev, the sha256 of the line before it.
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo "$(basename "$0"): python3 not found on PATH — this tool needs it (standard library only)." >&2; exit 127; }
PERMA="${PERMA_DIR:-$HOME/permanence}"
if [ -n "${OPS_STREAM:-}" ]; then
  STREAM="${OPS_STREAM%/}"
  case "/$STREAM/" in (*/../*|//*) echo "ops-event: OPS_STREAM $OPS_STREAM is not a stream folder inside $PERMA" >&2; exit 1 ;; esac
  REPO="$(git -C "${OPS_REPO:-$PWD}" rev-parse --show-toplevel 2>/dev/null || true)"
else
  REPO="$(git -C "${OPS_REPO:-$PWD}" rev-parse --show-toplevel 2>/dev/null)" \
    || { echo "ops-event: ${OPS_REPO:-$PWD} is not inside a git repository" >&2; exit 1; }
  STREAM="$("$PERMA/runtime/resolve-stream.sh" "$REPO")"
  [ -n "$STREAM" ] || { echo "ops-event: no stream is registered for $REPO in _meta/REGISTRY.md" >&2; exit 1; }
fi
[ -d "$PERMA/$STREAM/repo" ] || { echo "ops-event: stream $STREAM has no repo/ folder: this repository keeps no ledger" >&2; exit 1; }
# Only the owner's own repositories keep a ledger: another party's commits are not the owner's to log.
python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("owned") is True else 1)' \
  "$PERMA/$STREAM/repo/binding.json" 2>/dev/null \
  || { echo "ops-event: $STREAM/repo/binding.json does not say \"owned\": true: this repository keeps no ledger" >&2; exit 1; }
OUT="$PERMA/$STREAM/repo/ops/events.jsonl"
mkdir -p "$(dirname "$OUT")"
KIND="${1:?kind}"; TASK="${2:?task}"; PURPOSE="${3:?purpose}"
MODEL="${4:--}"; EFFORT="${5:--}"; COUNT="${6:--}"; TOKENS="${7:--}"; DUR="${8:--}"; OUTCOME="${9:--}"; NOTE="${10:-}"
GATE="${11:-}"
python3 - "$(cd "$(dirname "$0")" && pwd)" "$OUT" "$REPO" "$KIND" "$TASK" "$PURPOSE" "$MODEL" "$EFFORT" "$COUNT" "$TOKENS" "$DUR" "$OUTCOME" "$NOTE" "$GATE" <<'PY'
import json, re, sys, datetime, subprocess, os
from pathlib import Path
sys.path.insert(0, sys.argv.pop(1))
import ops_ledger
out, repo, kind, task, purpose, model, effort, count, tokens, dur, outcome, note, gate = sys.argv[1:]
if kind in ops_ledger.SEALED_KINDS:
    sys.exit(f"ops-event: a {kind} event is written only by predict-gate.sh seal, revise or retire")
if gate and kind != "gate-refused":
    sys.exit(f"ops-event: a gate ({gate}) is written only on a gate-refused event, not on {kind}")
if gate and not re.fullmatch(r"[a-z][a-z0-9-]*:[a-z0-9][a-z0-9-]*", gate):
    sys.exit(f"ops-event: gate {gate!r} is not <tool>:<check> in lower case")
if outcome == "turn-cap":
    if kind != "agent-run":
        sys.exit(f"ops-event: turn-cap is an agent-run outcome, not a {kind} one")
    if not count.isdigit():
        sys.exit("ops-event: turn-cap needs count = the turns the agent used, as the harness reported them")
    m = re.match(r"briefed (\d+|-) of cap (\d+)\b", note)
    if not m:
        sys.exit('ops-event: turn-cap needs a note starting "briefed <N|-> of cap <M>"')
def num(v):
    try: return int(v)
    except ValueError: return None if v in ("-", "") else v
ev = {"ts": os.environ.get("OPS_TS") or datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
      "role": "engine", "kind": kind,
      "task": None if task == "-" else task, "purpose": purpose, "model": None if model == "-" else model,
      "effort": None if effort == "-" else effort, "count": num(count), "tokens": num(tokens), "durationS": num(dur),
      "outcome": None if outcome == "-" else outcome, "note": note or None,
      "commit": os.environ.get("OPS_COMMIT")
      or (repo and subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"], capture_output=True,
                                  text=True).stdout.strip())
      or None}
if outcome == "turn-cap":
    ev["turnsBriefed"], ev["turnCap"] = num(m.group(1)), int(m.group(2))
if gate:
    ev["gate"] = gate
ev = ops_ledger.append(Path(out), lambda events: ev)
print(json.dumps(ev, ensure_ascii=False))
PY
