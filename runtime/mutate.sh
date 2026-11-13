#!/usr/bin/env bash
# The mutation runner: plants each mutant of a JSON list in a clean `git archive` copy of a commit,
# runs the tests the repository's binding names for the mutated file (an unmutated control first),
# and appends the rows of the reviewer's mutation record (mutations.json, mutations-fix<N>.json).
#
#   mutate.sh --mutants <list.json> --record <review folder> --round first|fix<N>
#       [--repo <repo>] [--commit <rev>] [--binding <binding.json>] [--work <dir>]
#       [--by reviewer|engineer]   (an engineer's rows: engineer-mutations.json, engineer-mutations-fix<N>.json)
#
# The list, the binding's `mutation` block and the exit codes: runtime/mutate.py docstring and
# docs/PROJECT-TEAM.md, "The mutation runner".
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo "$(basename "$0"): python3 not found on PATH — this tool needs it (standard library only)." >&2; exit 127; }
exec python3 "$(dirname "$0")/mutate.py" "$@"
