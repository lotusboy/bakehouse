#!/usr/bin/env bash
# The decision ledger: record every question put to the owner, score how often the agent's
# recommendation is the owner's choice, and say when a category has earned autonomy.
# The ledger is <records>/decisions/ledger.jsonl, append-only, and is the source of truth for the
# scores. <records>/DECISIONS.md is its readable view, regenerated after every write. The project
# is found from the current folder by runtime/locator.py (as ops-event.sh does), or is named with
# --repo <folder>.
#
#   decision.sh ask --category spend --question "Run the audit now?" --option yes --option no \
#       --recommendation yes --confidence 0.8 --evidence-kind measured --evidence "estimate in WIP.md"
#   decision.sh ask ... --task <id> --change --layer in|out --owners N --area <name>   # a proposed code change
#       (--fix-round implies --change); every trigger prints `decision: <name> trigger: <why>`
#   decision.sh answer <id> --choice yes            # agreed is worked out when the choice matches
#   decision.sh unattended start --why "owner said unattended" [--wave W]   # only where the project
#   decision.sh unattended stop                      # turns unattended mode on in its config
#   decision.sh answer <id> --assumed --est-tokens N # unattended: the recommendation, as an assumed yes
#   decision.sh outcome <id> --verdict held|reversed|refined --by "what showed it"
#   decision.sh score [--repo R] [--category C] [--since DATE] [--json]
#   decision.sh should-ask <category> [--seed N]    # ask | ask-blind | decide-and-log
#   decision.sh review [--repo R] [--since DATE] [--md]
#   decision.sh render [--repo R]                    # regenerate DECISIONS.md
#
# BACKFILL ONLY: DECISION_BACKFILL=1 DECISION_TS=<iso> marks the line backfilled and relaxes the
# required fields (confidence, evidence, options) that cannot be recovered after the fact.
# Defaults: runtime/decision-config.json; per project: .team/decision-config.json.
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo "$(basename "$0"): python3 not found on PATH — this tool needs it (standard library only)." >&2; exit 127; }
exec python3 "$(dirname "$0")/decision.py" "$@"
