#!/usr/bin/env bash
# Run ONE heavy job under its lane's own lock (Lanes v0). Inside a lane, heavy jobs take turns; a lane
# never waits for another lane, and memory is left to the operating system. Each job writes lane
# events (resource-waited, resource-started, resource-ended) through lane-event.sh, so waits and holds
# become statistics. The lock is advisory (macOS/BSD lockf); every lane starts its heavy jobs here.
#
# Usage:
#   heavy-run.sh <resource> <command> [args ...]
# <resource> names the lock, e.g. `heavy`; the lock is <resource>.lane<$PERMA_LANE> (`main` when
# $PERMA_LANE is unset). $PERMA_LANE / $PERMA_WO / $PERMA_STREAM label the events (see
# lane-event.sh). The command's exit code is returned.
set -uo pipefail
PERMA="${PERMA_DIR:-$HOME/permanence}"
LOCKS="${PERMA_LOCK_DIR:-$HOME/.cache/permanence/locks}"
if [ "${1:-}" = "--held" ]; then
  shift; res="$1"; t_wait="$2"; shift 2
  now=$(date +%s)
  "$PERMA/runtime/lane-event.sh" resource-started resource="$res" waitedS=$((now - t_wait)) >/dev/null 2>&1 || true
  "$@"; rc=$?
  "$PERMA/runtime/lane-event.sh" resource-ended resource="$res" heldS=$(( $(date +%s) - now )) exit=$rc >/dev/null 2>&1 || true
  exit $rc
fi
[ $# -ge 2 ] || { echo "usage: heavy-run.sh <resource> <command> [args ...]" >&2; exit 2; }
res="$1"; shift
mkdir -p "$LOCKS"
t0=$(date +%s)
"$PERMA/runtime/lane-event.sh" resource-waited resource="$res" >/dev/null 2>&1 || true
exec lockf -k "$LOCKS/$res.lane${PERMA_LANE:-main}.lock" "$0" --held "$res" "$t0" "$@"
