#!/usr/bin/env bash
# Append ONE lane event to <records>/ops/lanes.jsonl: the record of parallel work inside one
# project (Lanes v0). One JSON object per line, a stable shape, so a later stats recorder or monitor
# can read the ledger without knowing who wrote it. Unknown fields are left out, never guessed.
#
# Usage:
#   lane-event.sh <kind> [key=value ...]
# kinds (v0): lane-opened lane-closed footprint-judged resource-waited resource-started
#             resource-ended merge remeasure question-asked question-answered
# common keys: lane workOrder resource waitedS heldS exit outcome reason files note
# The project: found from the repo you are in ($OPS_REPO or $PWD) through runtime/locator.py, as
# ops-event.sh does (a lane clone's records pointer names the main clone's records).
# $BAKEHOUSE_LANE and $BAKEHOUSE_WO fill `lane` and `workOrder` when not given.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[ $# -ge 1 ] || { echo "usage: lane-event.sh <kind> [key=value ...]" >&2; exit 2; }
REPO="$(git -C "${OPS_REPO:-$PWD}" rev-parse --show-toplevel 2>/dev/null)" \
  || { echo "lane-event: not inside a git repository" >&2; exit 1; }
OUT="$(env -u CLAUDE_PROJECT_DIR python3 "$HERE/locator.py" lanes_ledger "$REPO")" || {
  [ $? -eq 3 ] && echo "lane-event: $REPO has no team (no .team/binding.json)" >&2
  exit 1; }
mkdir -p "$(dirname "$OUT")"
python3 - "$OUT" "$@" <<'PY'
import datetime, json, os, sys
out, kind, *pairs = sys.argv[1:]
KINDS = {"lane-opened", "lane-closed", "footprint-judged", "resource-waited", "resource-started",
         "resource-ended", "merge", "remeasure", "question-asked", "question-answered"}
if kind not in KINDS:
    sys.exit(f"lane-event: unknown kind {kind!r}; one of {sorted(KINDS)}")
ev = {"ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "kind": kind}
for k, env in (("lane", "BAKEHOUSE_LANE"), ("workOrder", "BAKEHOUSE_WO")):
    if os.environ.get(env):
        ev[k] = os.environ[env]
for p in pairs:
    if "=" not in p:
        sys.exit(f"lane-event: {p!r} is not key=value")
    k, v = p.split("=", 1)
    try:
        v = int(v) if v.lstrip("-").isdigit() else float(v) if v.replace(".", "", 1).lstrip("-").isdigit() else v
    except ValueError:
        pass
    ev[k] = v
with open(out, "a", encoding="utf-8") as f:
    f.write(json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n")
PY
