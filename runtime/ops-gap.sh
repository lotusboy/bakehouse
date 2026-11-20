#!/usr/bin/env bash
# How far a repository's ops ledger has drifted behind the work, in commits. Reads the ledger in the
# project's team records (<records>/ops/events.jsonl, and in records format 2 each writer's file under
# <records>/ops/events/; see ops-event.sh), never a file in the
# repository.
#
# A ledger once stopped being written and nobody noticed for three days; by then 108 of the period's
# 113 commits were unrecorded. Nothing reported the drift while it was still one or two commits wide
# and free to fix. The post-commit hook that wire writes runs this after every commit.
#
# Prints the drift and exits 0 always: this reports, it never blocks a commit. Exit 1 only if the
# repository has no team or no ledger.
#
# Usage: ops-gap.sh [warn-threshold, default 5]   (from inside the repository, or set OPS_REPO)
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo "$(basename "$0"): python3 not found on PATH — this tool needs it (standard library only)." >&2; exit 127; }
HERE="$(cd "$(dirname "$0")" && pwd)"
THRESHOLD="${1:-5}"
REPO="$(git -C "${OPS_REPO:-$PWD}" rev-parse --show-toplevel 2>/dev/null)" \
  || { echo "ops-gap: ${OPS_REPO:-$PWD} is not inside a git repository" >&2; exit 1; }
locate() { env -u CLAUDE_PROJECT_DIR python3 "$HERE/locator.py" "$1" "$REPO"; }
# The ledger's files in read order, one per line: the ledger itself, then in records format 2 each writer's file.
FILES="$(env -u CLAUDE_PROJECT_DIR python3 "${HERE}/locator.py" --read ops_ledger "${REPO}")" || {
  [ $? -eq 3 ] && echo "ops-gap: ${REPO} has no team (no .team/binding.json)" >&2
  exit 1; }
LEDGER="$(printf '%s\n' "${FILES}" | head -n 1)"
python3 -c 'import os, sys; sys.exit(0 if any(os.path.isfile(f) for f in sys.argv[1].splitlines()) else 1)' \
  "${FILES}" || { echo "ops-gap: no ledger at ${LEDGER}" >&2; exit 1; }

LAST_TS="$(python3 - "${FILES}" <<'PY'
import json, os, sys
rows = [json.loads(l) for f in sys.argv[1].splitlines() if os.path.isfile(f) for l in open(f) if l.strip()]
print(max(r["ts"] for r in rows) if rows else "")
PY
)"
[ -n "$LAST_TS" ] || { echo "ops-gap: ledger is empty"; exit 0; }

BEHIND="$(git -C "$REPO" log --since="$LAST_TS" --oneline | wc -l | tr -d ' ')"
if [ "$BEHIND" -ge "$THRESHOLD" ]; then
  echo "⚠ ops ledger is $BEHIND commit(s) behind — last event $LAST_TS"
  echo "   Record what actually happened with $HERE/ops-event.sh (rules: $(locate operations))."
  echo "   Agent runs, fan-outs, gates and decisions cannot be recovered from commits later."
else
  echo "ops ledger: $BEHIND commit(s) behind (last event $LAST_TS): fine, a warning comes at $THRESHOLD"
fi
