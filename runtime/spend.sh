#!/usr/bin/env bash
# Measured wave spend: record the token count the HARNESS reported for each finished agent
# (`subagent_tokens` in its completion notification, never the agent's own estimate), group agents
# into waves with a ceiling, and warn at 80% and 100% of that ceiling.
# The ledger is <stream>/spend/ledger.jsonl, append-only. The stream resolves from the current
# directory through _meta/REGISTRY.md (as decision.sh does), or is named with --stream.
#
#   spend.sh wave open wave-1 --ceiling 400000
#   spend.sh record --agent a1b2c3 --label "answers r1" --tokens 131204 --kind answering-agent \
#       --why "answer the 40 open rows" --predicted "36 of 40 answered" --predicted-tokens 120000 \
#       --result "38 of 40 answered" --outcome better [--wave wave-1]
#       [--task T12] [--model M] [--note "..."]
#       [--source harness|self-reported|estimate] [--correct] [--no-ops]
#   --kind is the role the agent ran as: one of runtime/agent-roles.json's roles or its spend_kinds.
#   'agent' names no role: a new first record may not take it; old rows and backfills keep it.
#   A first record states the tool's result, not only its cost: --kind, --why, --predicted,
#   --predicted-tokens, --result and --outcome are required (--kind since T6a; the rest since v1.11.0).
#   A --correct row with no --kind keeps the kind of the row it corrects. --kind architect also needs
#   --task, and a second architect run on one task needs --stepped-back.
#   spend.sh status [--wave W] [--all] [--json]
#   spend.sh report [--since 2026-10-10] [--json]
#   spend.sh wave close [wave-1]
#
# `record` run from inside an owned repo also writes that repo's ops ledger agent-run row (through
# ops-event.sh), so one command records both. --no-ops skips it; --stream and --correct never write it.
# BACKFILL ONLY: SPEND_TS=<iso> stamps the row with that time, marks it backfilled, and needs --note.
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo "$(basename "$0"): python3 not found on PATH — this tool needs it (standard library only)." >&2; exit 127; }
exec python3 "$(dirname "$0")/spend.py" "$@"
