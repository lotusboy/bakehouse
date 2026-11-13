#!/usr/bin/env bash
# Run ONE heavy job under its lane's own lock (Lanes v0). Inside a lane, heavy jobs take turns; a lane
# never waits for another lane, and memory is left to the operating system. Each job writes lane
# events (resource-waited, resource-started, resource-ended) through lane-event.sh, so waits and holds
# become statistics. The lock is advisory (lockf on macOS/BSD, flock on Linux); every lane starts its
# heavy jobs here.
#
# Usage:
#   heavy-run.sh <resource> <command> [args ...]
# <resource> names the lock, e.g. `heavy`; the lock is <resource>.lane<$BAKEHOUSE_LANE> (`main` when
# $BAKEHOUSE_LANE is unset), in the machine folder's locks/ (runtime/locator.py). $BAKEHOUSE_LANE /
# $BAKEHOUSE_WO label the events (see lane-event.sh). The command's exit code is returned.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ "${1:-}" = "--held" ]; then
  shift; res="$1"; t_wait="$2"; shift 2
  now=$(date +%s)
  "$HERE/lane-event.sh" resource-started resource="$res" waitedS=$((now - t_wait)) >/dev/null 2>&1 || true
  "$@"; rc=$?
  "$HERE/lane-event.sh" resource-ended resource="$res" heldS=$(( $(date +%s) - now )) exit=$rc >/dev/null 2>&1 || true
  exit $rc
fi
[ $# -ge 2 ] || { echo "usage: heavy-run.sh <resource> <command> [args ...]" >&2; exit 2; }
res="$1"; shift
LOCKS="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import locator; print(locator.machine_dir() / "locks")' "$HERE")" \
  || { echo "heavy-run.sh: cannot find the machine folder (python3 and runtime/locator.py are needed)" >&2; exit 2; }
mkdir -p "$LOCKS"
t0=$(date +%s)
"$HERE/lane-event.sh" resource-waited resource="$res" >/dev/null 2>&1 || true
lock="$LOCKS/$res.lane${BAKEHOUSE_LANE:-main}.lock"
if command -v lockf >/dev/null 2>&1; then
  exec lockf -k "$lock" "$0" --held "$res" "$t0" "$@"
elif command -v flock >/dev/null 2>&1; then
  exec flock "$lock" "$0" --held "$res" "$t0" "$@"
fi
echo "heavy-run.sh: needs lockf (macOS/BSD) or flock (Linux, util-linux) to take the lane lock" >&2
exit 2
