#!/usr/bin/env bash
# The predicted-moves gate. A stop rule enforced by a machine: compares a structured prediction
# (moves.prediction.json) with a repo's flat before/after measurements (moves.baseline.json,
# moves.measured.json) and exits non-zero on any move nobody predicted.
#
#   predict-gate.sh check [--prediction P] --measured M [--baseline B] [--override "<reason>"] [--task ID]
#   predict-gate.sh close <work-order> [--repo DIR] [--binding FILE] [--merged]   # refuses unless every check passes;
#                                           # override: PREDICT_GATE_CLOSE_OVERRIDE="<reason>"
#   predict-gate.sh seal <work-order> <round> <file> [--repo DIR] [--report R]   # one sealed ledger event
#   predict-gate.sh revise|retire <work-order> <round> <file> --decision <id> [--repo DIR]
#   predict-gate.sh seal-code <work-order> <round> [--repo DIR] -- <git commit args>   # commit + seal the first code
#   predict-gate.sh verify-chain [--repo DIR | --ledger FILE]   # the ledger's first broken link
#   predict-gate.sh install-hook <repo>     # pre-commit hook in that repo; never run automatically
#   predict-gate.sh hook                    # what the hook runs
#
# Format, outcomes and exit codes: runtime/predict-gate.py docstring and docs/PREDICT-GATE.md.
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo "$(basename "$0"): python3 not found on PATH — this tool needs it (standard library only)." >&2; exit 127; }
exec python3 "$(dirname "$0")/predict-gate.py" "$@"
