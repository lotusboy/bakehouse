#!/usr/bin/env bash
# The agreement table for two or more blind readers' reports (the blind-readers skill, item mode).
# Each report ends with a ```json block, {"reader": "A", "items": [{"id", "label", "action", ...}]};
# the last such block in each report is read. An item agrees only when every reader gives the same
# label and the same action (case and surrounding spaces ignored). An item missing from a reader, or
# with an empty or missing label or action, is a disagreement. Pure computation, standard library
# only, no AI. Prints the table on stdout; writes nothing.
#
#   blind-compare.sh <run>/reader-a.md <run>/reader-b.md [<run>/reader-c.md ...] > <run>/agreement.md
#
# Exit codes: 0 every item agrees; 1 any item disagrees or is missing; 2 usage, or a report has no
# valid ```json block (none, it does not parse, no "items" list, or an item with no id).
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo "$(basename "$0"): python3 not found on PATH — this tool needs it (standard library only)." >&2; exit 127; }
exec python3 "$(dirname "$0")/blind-compare.py" "$@"
