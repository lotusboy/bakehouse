#!/usr/bin/env bash
# The driver signal: finds the same driver script written again and again in a repo's run folders
# (content near-duplicates folded into same-name families) and raises a coherent family seen in 3 or
# more runs within 30 days, or a name-only family seen in 5, as a build candidate. Rules from the
# binding's `drivers` object; writes the driver register in the records folder, beside the health register.
#
#   driver-signal.sh [--repo <repo>] [--as-of YYYY-MM-DD] [--no-write] [--binding <binding.json>]
#       [--threshold X] [--window-days N] [--min-runs N] [--register FILE] [--top N]
#
# Rules, states and the register's shape: runtime/driver-signal.py docstring and docs/PROJECT-TEAM.md.
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo "$(basename "$0"): python3 not found on PATH — this tool needs it (standard library only)." >&2; exit 127; }
exec python3 "$(dirname "$0")/driver-signal.py" "$@"
