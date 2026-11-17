#!/usr/bin/env bash
# Vocabulary ratification: each entry of a closed vocabulary (pattern -> class) is ratified only when
# two isolated blind readers, shown real matches from the project's own corpus, both say the class is
# RIGHT. Pure computation, standard library only, no AI.
# Interchange format (entries, corpus, classes, a reader's report): docs/TOOLS.md, "`ratify.sh`: the files it reads".
#
#   ratify.sh sample --entries entries.json --corpus corpus.json --per-entry 8 --seed 1 --out <run>
#       [--classes classes.json] [--source "<where the raw files are>"] [--readers 2] [--template F]
#   ratify.sh compare <run>/reader-a.md <run>/reader-b.md --out <run> [--sample <run>/sample.json]
#   ratify.sh record [--repo R] --agreement <run>/agreement.json [--owner-approved id1,id2] [--note "..."]
#   ratify.sh check --entries entries.json [--repo R] [--json]     # exit 1 if any entry is not ratified
#   ratify.sh status [--repo R]
#
# Ledger: ratifications/ledger.jsonl in the project's records folder (runtime/locator.py), append-only;
# the latest row per entry id counts. --repo defaults to the current folder's repository.
# Exit codes: 0 ok; 1 compare found an entry not ratified, or check found a problem; 2 usage or input error.
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo "$(basename "$0"): python3 not found on PATH — this tool needs it (standard library only)." >&2; exit 127; }
exec python3 "$(dirname "$0")/ratify.py" "$@"
