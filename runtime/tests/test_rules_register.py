"""The rule register, runtime/rules.json: every must / never / always sentence in the process prompts,
with who enforces it (docs/PROJECT-TEAM.md, "The rule register"). Standard library only.

It fails when a prompt gains, loses or rewords a rule sentence the register does not list, when a
file holds a sentence a different number of times, or under a different heading, than the register's
`copies` says, when a new agent or role file is not in the register's `files`, or when a proof does
not hold: a `code` rule's test does not load, or a `reviewer` rule's quoted line is not in the review
brief it names."""

import importlib
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TESTS = Path(__file__).resolve().parent
REGISTER = "runtime/rules.json"
# The text match of the T5 census: a sentence is a piece between sentence ends, list-line starts and
# fence marker lines, holding a rule word, of any length. Fenced text is read: a brief template, and
# a comment in a command block, are what the agent reads.
RULE = re.compile(
    r"\b(must|never|always|refuses?|may not|do not|don't)\b", re.IGNORECASE
)
SPLIT = re.compile(r"(?<=[.!?])\s+|\n(?=\s*[-*\d])|\s*```[^\n]*")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$")
TOP = "(top)"
ENFORCERS = ("code", "reviewer", "prompt")
COMMANDS = (
    "commands/change.md",
    "commands/health.md",
    "commands/refactor.md",
)
REVIEW_BRIEFS = (
    "agents/design-reviewer.md",
    "agents/reviewer.md",
)
NO_PROOF = "code exists, no proof test:"


def norm(s):
    return " ".join(s.split())


def located(text):
    """(sentence, heading) for every rule sentence: the heading is the nearest markdown heading above
    it, outside fenced blocks, or TOP before the first one."""
    heads, off, fence = [], 0, False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fence = not fence
        elif not fence and HEADING.match(line):
            heads.append((off, HEADING.match(line).group(1)))
        off += len(line) + 1
    out, start = [], 0
    for m in [*SPLIT.finditer(text), None]:
        s = norm(text[start : m.start() if m else len(text)])
        if s and RULE.search(s):
            out.append((s, next((h for o, h in reversed(heads) if o <= start), TOP)))
        if m:
            start = m.end()
    return out


class NotATestCase:
    """A fixture: a class with a test-named method that is not a test, for a planted proof."""

    def test_shape(self):
        pass


def test_loads(test_id):
    """True when <module>.<Class>.<test> names a test method in runtime/tests."""
    parts = test_id.split(".")
    if (
        len(parts) != 3
        or not parts[0].startswith("test_")
        or not parts[2].startswith("test")
    ):
        return False
    if str(TESTS) not in sys.path:
        sys.path.insert(0, str(TESTS))
    try:
        mod = importlib.import_module(parts[0])
    except ImportError:
        return False
    cls = getattr(mod, parts[1], None)
    return (
        isinstance(cls, type)
        and issubclass(cls, unittest.TestCase)
        and callable(getattr(cls, parts[2], None))
    )


def problems(root):
    """Every way the register under root disagrees with the prompts under root."""
    root = Path(root)
    reg = json.loads((root / REGISTER).read_text(encoding="utf-8"))
    out = []
    files = reg.get("files", [])
    prompts = {
        str(p.relative_to(root))
        for d in ("agents", "skills/pm")
        for p in (root / d).glob("*.md")
    }
    prompts |= set(COMMANDS)
    out += [
        f"not in files: {f} is a process prompt" for f in sorted(prompts - set(files))
    ]
    out += [
        f"files lists {f}, which is not a process prompt"
        for f in sorted(set(files) - prompts)
    ]
    listed = {f: set() for f in files}
    ids = set()
    for e in reg.get("rules", []):
        rid = e.get("id")
        if rid in ids:
            out.append(f"{rid}: the id is used twice")
        ids.add(rid)
        if e.get("file") not in listed:
            out.append(f"{rid}: file {e.get('file')!r} is not in files")
            continue
        if e.get("enforcer") not in ENFORCERS:
            out.append(
                f"{rid}: enforcer {e.get('enforcer')!r} is not one of {', '.join(ENFORCERS)}"
            )
        sentences = e.get("sentences")
        if not sentences or not all(isinstance(s, str) for s in sentences):
            out.append(f"{rid}: sentences must be a list of one or more strings")
            sentences = []
        listed[e["file"]].update(norm(s) for s in sentences)
        proof = e.get("proof")
        if not isinstance(proof, str) or not proof.strip():
            out.append(f"{rid}: no proof")
        elif e.get("enforcer") == "code" and not test_loads(proof):
            out.append(f"{rid}: proof {proof!r} is not a test in runtime/tests")
        elif e.get("enforcer") == "reviewer":
            path, _, quote = proof.partition(": ")
            brief = root / path
            if path not in REVIEW_BRIEFS:
                out.append(f"{rid}: proof names {path!r}, which is not a review brief")
            elif (
                not quote
                or not brief.is_file()
                or norm(quote) not in norm(brief.read_text(encoding="utf-8"))
            ):
                out.append(f"{rid}: proof {proof!r}: the quoted line is not in {path}")
        elif e.get("enforcer") == "prompt" and proof.startswith(NO_PROOF):
            words = proof[len(NO_PROOF) :].split()
            path = words[0] if words else ""
            if not (root / path).is_file():
                out.append(f"{rid}: proof names {path!r}, which does not exist")
    for n in reg.get("notRules", []):
        if n.get("file") not in listed or not n.get("why"):
            out.append(
                f"notRules: {n.get('file')!r}: an entry needs a file from files and a why"
            )
            continue
        listed[n["file"]].add(norm(n.get("sentence", "")))
    copies = {}
    for c in reg.get("copies", []):
        if c.get("file") not in listed or not isinstance(c.get("under"), list):
            out.append(
                f"copies: {c.get('file')!r}: an entry needs a file from files and an `under` list"
            )
            continue
        copies[(c["file"], norm(c.get("sentence", "")))] = sorted(c["under"])
    for f in files:
        if not (root / f).is_file():
            out.append(f"files lists {f}, which does not exist")
            continue
        locs = located((root / f).read_text(encoding="utf-8", errors="replace"))
        found = {s for s, _ in locs}
        out += [f"unlisted: {f}: {s}" for s in sorted(found - listed[f])]
        out += [f"stale: {f}: {s}" for s in sorted(listed[f] - found)]
        for s in sorted(found | {s for g, s in copies if g == f}):
            under = sorted(h for x, h in locs if x == s)
            want = copies.get((f, s))
            if want is None and len(under) > 1:
                out.append(
                    f"copies: {f}: held {len(under)} times, under {under}; the register lists one: {s}"
                )
            elif want is not None and under != want:
                out.append(
                    f"copies: {f}: found under {under}; the register says {want}: {s}"
                )
    return out


def entry(reg, f, sentence):
    """The register entry for a key (file, sentence)."""
    return next(
        e for e in reg["rules"] if e["file"] == f and sentence in e["sentences"]
    )


# The register is the template's own: its CI sets BAKEHOUSE_RULES_REGISTER=1. In a user's install the
# prompts are theirs to edit, so the two classes that read the real prompts are skipped there.
TEMPLATE_ONLY = unittest.skipUnless(
    os.environ.get("BAKEHOUSE_RULES_REGISTER") == "1", "the rule register is checked in the template's CI only"
)


@TEMPLATE_ONLY
class TestTheRegister(unittest.TestCase):
    def test_the_register_lists_every_rule_sentence_and_every_proof_holds(self):
        got = problems(ROOT)
        self.assertEqual(
            got, [], "runtime/rules.json is out of date:\n" + "\n".join(got)
        )


@TEMPLATE_ONLY
class TestTheCheckBites(unittest.TestCase):
    """Each way the register can go wrong, planted in a copy."""

    BOLD = "\\*\\*)' <file> # bold openers"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        reg = json.loads((ROOT / REGISTER).read_text(encoding="utf-8"))
        named = set(REVIEW_BRIEFS)
        named |= {
            e["proof"][len(NO_PROOF) :].split()[0]
            for e in reg["rules"]
            if e["proof"].startswith(NO_PROOF)
        }
        for f in reg["files"] + [REGISTER] + sorted(named):
            (self.root / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / f, self.root / f)
        self.reg = reg

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, f):
        return (self.root / f).read_text(encoding="utf-8")

    def write(self, f, text):
        (self.root / f).write_text(text, encoding="utf-8")

    def save(self):
        self.write(REGISTER, json.dumps(self.reg))

    def wrapped(self, sentence):
        """A pattern for a sentence however the prompt wraps it."""
        return re.compile(r"\s+".join(map(re.escape, sentence.split())))

    def bold_entry(self):
        f = "agents/design-author.md"
        return entry(
            self.reg,
            f,
            next(s for s, _ in located(self.read(f)) if s.startswith(self.BOLD)),
        )

    def test_the_copy_starts_clean(self):
        self.assertEqual(problems(self.root), [])

    def test_an_unlisted_rule_sentence_fails(self):
        f = "agents/architect.md"
        self.write(f, self.read(f) + "\nNever skip the second count.\n")
        self.assertEqual(
            problems(self.root), [f"unlisted: {f}: Never skip the second count."]
        )

    def test_a_short_rule_sentence_is_read(self):
        f, h = "agents/architect.md", "commands/health.md"
        self.write(f, self.read(f) + "\nDo not push.\n")
        text, n = self.wrapped("Never push.").subn("", self.read(h))
        self.assertEqual(n, 1)
        self.write(h, text)
        self.assertEqual(
            problems(self.root),
            [f"stale: {h}: Never push.", f"unlisted: {f}: Do not push."],
        )

    def test_an_edited_rule_sentence_fails_as_unlisted_and_stale(self):
        f = "skills/pm/SKILL.md"
        text = self.read(f)
        self.assertIn("Never retry a failed fan-out automatically.", text)
        self.write(
            f,
            text.replace(
                "Never retry a failed fan-out automatically.",
                "Never retry a failed fan-out.",
                1,
            ),
        )
        got = problems(self.root)
        self.assertEqual([p.split(":")[0] for p in got], ["unlisted", "stale"], got)
        self.assertIn("Never retry a failed fan-out.**", got[0])
        self.assertIn("Never retry a failed fan-out automatically.**", got[1])

    def test_an_edited_rule_inside_a_fenced_brief_fails(self):
        f = "commands/change.md"
        text = self.read(f)
        self.assertIn("```text", text)
        self.assertIn("Never without the upgrade condition.", text)
        self.write(
            f,
            text.replace(
                "Never without the upgrade condition.",
                "Never without the condition.",
                1,
            ),
        )
        self.assertEqual(
            problems(self.root),
            [
                f"unlisted: {f}: Never without the condition.",
                f"stale: {f}: Never without the upgrade condition.",
            ],
        )

    def test_deleting_one_of_two_copies_fails(self):
        f, s = (
            "commands/refactor.md",
            "Never edit a test or an output to make a gate pass.",
        )
        text = self.read(f)
        hits = list(self.wrapped(s).finditer(text))
        self.assertEqual(len(hits), 2)
        self.write(f, text[: hits[1].start()] + text[hits[1].end() :])
        want = "['Engineer brief', 'The two gate types']"
        self.assertEqual(
            problems(self.root),
            [
                f"copies: {f}: found under ['The two gate types']; the register says {want}: {s}"
            ],
        )

    def test_moving_a_copy_to_another_brief_fails(self):
        f, s = (
            "commands/refactor.md",
            "Never edit a test or an output to make a gate pass.",
        )
        text = self.read(f)
        hit = list(self.wrapped(s).finditer(text))[1]
        text = text[: hit.start()] + text[hit.end() :]
        self.assertEqual(text.count("Do not edit code. Do not commit."), 1)
        self.write(
            f,
            text.replace(
                "Do not edit code. Do not commit.",
                "Do not edit code. Do not commit. " + s,
            ),
        )
        got, want = (
            "['Reviewer brief', 'The two gate types']",
            "['Engineer brief', 'The two gate types']",
        )
        self.assertEqual(
            problems(self.root),
            [f"copies: {f}: found under {got}; the register says {want}: {s}"],
        )

    def test_a_second_copy_of_a_rule_held_once_fails(self):
        f = "agents/design-author.md"
        self.assertEqual(self.read(f).count("Do not commit."), 1)
        self.write(f, self.read(f) + "\nDo not commit.\n")
        self.assertEqual(
            problems(self.root),
            [
                f"copies: {f}: held 2 times, under ['5. Report', '5. Report']; the register lists one: Do not commit."
            ],
        )

    def test_a_rewrap_inside_a_sentence_moves_no_key(self):
        f = "commands/health.md"
        text = self.read(f)
        self.assertIn("Never use a path, command, threshold or part name", text)
        self.write(
            f,
            text.replace(
                "Never use a path, command, threshold",
                "Never use a path,\n   command,   threshold",
                1,
            ),
        )
        self.assertEqual(problems(self.root), [])

    def test_a_register_sentence_is_read_with_its_whitespace_collapsed(self):
        e = self.reg["rules"][0]
        e["sentences"][0] = e["sentences"][0].replace(" ", "\n   ", 2)
        self.save()
        self.assertEqual(problems(self.root), [])

    def test_a_code_proof_that_names_no_test_fails(self):
        e = next(e for e in self.reg["rules"] if e["enforcer"] == "code")
        e["proof"] = "test_predict_gate.Check.test_that_does_not_exist"
        self.save()
        want = f"{e['id']}: proof 'test_predict_gate.Check.test_that_does_not_exist' is not a test in runtime/tests"
        self.assertEqual(problems(self.root), [want])
        for bad in (
            "test_no_such_module.Check.test_x",
            "test_predict_gate.NoClass.test_x",
            "a sentence",
        ):
            with self.subTest(bad=bad):
                e["proof"] = bad
                self.save()
                self.assertEqual(len(problems(self.root)), 1)

    def test_a_code_proof_must_name_a_test_case(self):
        e = next(e for e in self.reg["rules"] if e["enforcer"] == "code")
        e["proof"] = "test_rules_register.NotATestCase.test_shape"
        self.save()
        self.assertEqual(
            problems(self.root),
            [f"{e['id']}: proof {e['proof']!r} is not a test in runtime/tests"],
        )

    def test_a_reviewer_quote_not_in_its_brief_fails(self):
        e = entry(
            self.reg,
            "agents/design-author.md",
            "- A change that grows the file must earn every added line.",
        )
        path, _, quote = e["proof"].partition(": ")
        self.assertIn(quote, self.read(path))
        self.write(path, self.read(path).replace(quote, "X"))
        self.assertEqual(
            problems(self.root),
            [f"{e['id']}: proof {e['proof']!r}: the quoted line is not in {path}"],
        )

    def test_deleting_the_checks_a_reviewer_proof_quotes_fails(self):
        f, e = "agents/design-reviewer.md", self.bold_entry()
        lines = self.read(f).split("\n")
        self.assertEqual(len([x for x in lines if x.startswith("grep -")]), 2)
        self.write(f, "\n".join(x for x in lines if not x.startswith("grep -")))
        self.assertEqual(
            problems(self.root),
            [f"{e['id']}: proof {e['proof']!r}: the quoted line is not in {f}"],
        )

    def test_a_reviewer_proof_must_name_a_review_brief(self):
        e = self.bold_entry()
        e["proof"] = "agents/design-author.md: Do not review your own work."
        self.save()
        want = f"{e['id']}: proof names 'agents/design-author.md', which is not a review brief"
        self.assertEqual(problems(self.root), [want])

    def test_a_reviewer_proof_with_no_quote_fails(self):
        e = next(e for e in self.reg["rules"] if e["enforcer"] == "reviewer")
        e["proof"] = e["proof"].partition(": ")[0]
        self.save()
        self.assertEqual(
            problems(self.root),
            [
                f"{e['id']}: proof {e['proof']!r}: the quoted line is not in {e['proof']}"
            ],
        )

    def test_a_no_proof_path_that_does_not_exist_fails(self):
        e = next(e for e in self.reg["rules"] if e["proof"].startswith(NO_PROOF))
        e["proof"] = NO_PROOF + " runtime/no-such-tool.py (a planted path)"
        self.save()
        self.assertEqual(
            problems(self.root),
            [f"{e['id']}: proof names 'runtime/no-such-tool.py', which does not exist"],
        )

    def test_a_not_rule_needs_a_why(self):
        n = self.reg["notRules"][0]
        n["why"] = ""
        self.save()
        self.assertIn(
            f"notRules: {n['file']!r}: an entry needs a file from files and a why",
            problems(self.root),
        )

    def test_files_may_list_only_process_prompts(self):
        f = "commands/help.md"
        self.write(f, "Lists the commands.\n")
        self.reg["files"].append(f)
        self.save()
        self.assertIn(
            f"files lists {f}, which is not a process prompt", problems(self.root)
        )

    def test_a_new_agent_file_must_be_listed(self):
        self.write("agents/new-role.md", "You read.\n")
        self.assertEqual(
            problems(self.root),
            ["not in files: agents/new-role.md is a process prompt"],
        )

    def test_an_unknown_enforcer_and_a_repeated_id_fail(self):
        self.reg["rules"][1]["id"] = self.reg["rules"][0]["id"]
        self.reg["rules"][2]["enforcer"] = "owner"
        self.save()
        got = problems(self.root)
        self.assertIn(f"{self.reg['rules'][0]['id']}: the id is used twice", got)
        self.assertIn(
            f"{self.reg['rules'][2]['id']}: enforcer 'owner' is not one of code, reviewer, prompt",
            got,
        )


if __name__ == "__main__":
    unittest.main()
