# Agreement table — <run name>, <date>

Rule applied: <the rule>
Readers: <A, B>. Models: <A opus, B fable>. Reports: `<run-folder>/reader-a.md`, `<run-folder>/reader-b.md`.

An item agrees only when every reader gives the same label AND the same action, and their quotes
point at the same passage (or passages that say the same thing).

| Id | A label | A action | B label | B action | Same passage? | Agree? |
|---|---|---|---|---|---|---|
| <C-01> | CORRECT | accept | CORRECT | accept | yes | yes |
| <C-02> | WRONG | reject | PARTLY | hold | — | **no** |

Agreed: <n> of <total>. Disagreed: <n>.

## Held (disagreements) — not sent to the owner

| Id | A quote and reading | B quote and reading | Next step |
|---|---|---|---|
| <C-02> | "<quote>" (p.N) — WRONG | "<quote>" (p.M) — PARTLY | <reword the rule and re-run both on C-02 / render p.N and re-run both / park> |

## Owner questions (agreed items, grouped by cause)

| Group | Cause | Items | Action | Confidence | Decision id |
|---|---|---|---|---|---|
| 1 | <one sentence> | <C-01, C-04> | <accept> | <0.9> | <d2026…> |
