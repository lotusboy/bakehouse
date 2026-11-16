#!/usr/bin/env python3
"""Print the agreement table for two or more blind readers' reports.

Each report ends with a ```json block: {"reader": "A", "items": [{"id", "label", "action", ...}]}.
An item agrees only when every reader gives the same label and the same action. An item missing
from a reader, or with an empty or missing label or action, is a disagreement.
Exit 0: all agree. Exit 1: any disagree or missing. Exit 2: usage, or a report has no valid JSON
block (no block, a block that does not parse, no "items" list, or an item with no id).
Pure computation, standard library only, no AI. Usage: see blind-compare.sh.

  blind-compare.py reader-a.md reader-b.md [reader-c.md ...]
"""

import json
import re
import sys
from pathlib import Path


def bad(msg):
    print(f"blind-compare: {msg}", file=sys.stderr)
    sys.exit(2)


def load(path):
    try:
        text = Path(path).read_text()
    except OSError as e:
        bad(f"{path}: cannot read: {e}")
    blocks = re.findall(r"```json\s*\n(.*?)```", text, re.S)
    if not blocks:
        bad(f"{path}: no ```json block")
    try:
        data = json.loads(blocks[-1])
    except json.JSONDecodeError as e:
        bad(f"{path}: last ```json block does not parse: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        bad(f"{path}: last ```json block has no \"items\" list")
    items = {}
    for n, i in enumerate(data["items"]):
        if not isinstance(i, dict):
            bad(f"{path}: item {n} is not an object")
        if i.get("id") is None or not str(i["id"]).strip():
            bad(f"{path}: item {n} has no id")
        items[str(i["id"])] = i
    name = data.get("reader") or Path(path).stem
    return name, items


def norm(v):
    return str(v or "").strip().upper()


def shown(v):
    return v if norm(v) else "—"


def agrees(answers):
    """Every reader answered, every label and action is non-empty, and all are the same."""
    if not all(answers):
        return False
    pairs = {(norm(a.get("label")), norm(a.get("action"))) for a in answers}
    return len(pairs) == 1 and all(pairs.pop())


def main(paths):
    if len(paths) < 2:
        bad("compare needs at least two reader reports\n" + __doc__)
    readers = [load(p) for p in paths]
    ids = list(dict.fromkeys(i for _, items in readers for i in items))
    head = ["Id"] + [f"{n} label | {n} action" for n, _ in readers] + ["Agree?"]
    print("| " + " | ".join(head) + " |")
    print("|" + "---|" * (2 * len(readers) + 2))
    held = []
    for i in ids:
        answers = [items.get(i) for _, items in readers]
        cells = [f"{shown(a.get('label'))} | {shown(a.get('action'))}" if a else "MISSING | —" for a in answers]
        ok = agrees(answers)
        print(f"| {i} | " + " | ".join(cells) + f" | {'yes' if ok else '**no**'} |")
        if not ok:
            held.append((i, answers))
    print(f"\nAgreed: {len(ids) - len(held)} of {len(ids)}. Disagreed: {len(held)}.")
    if held:
        print("\n## Held (disagreements) — not sent to the owner\n")
        for i, answers in held:
            print(f"- **{i}**")
            for (n, _), a in zip(readers, answers):
                if a:
                    print(f"  - {n}: {shown(a.get('label'))} / {shown(a.get('action'))} — "
                          f"\"{a.get('quote', '')}\" ({a.get('where', '')})")
                else:
                    print(f"  - {n}: no answer")
    return 1 if held else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
