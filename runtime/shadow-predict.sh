#!/usr/bin/env bash
# Writes moves.prediction<R>.json from a shadow measured as a flat before/after pair and a written
# expect file, and refuses any move the expect file does not explain. Optional: an engineer who
# derives the values by reasoning writes the prediction by hand.
#
#   shadow-predict.sh --before B --after S --expect E --out <record>/moves.prediction<R>.json
#       [--scopes K] [--shadow-base SB --sealed-base X]
#
# The expect file, the rules and the exit codes: runtime/shadow-predict.py docstring and
# docs/PREDICT-GATE.md, "Writing the prediction from a shadow".
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo "$(basename "$0"): python3 not found on PATH — this tool needs it (standard library only)." >&2; exit 127; }
exec python3 "$(dirname "$0")/shadow-predict.py" "$@"
