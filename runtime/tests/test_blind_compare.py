"""Tests for runtime/blind-compare.sh and the paths the blind-readers skill names.
Standard library only: python3 -m unittest runtime/tests/test_blind_compare.py"""
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
ROOT = RUNTIME.parent
TOOL = RUNTIME / "blind-compare.sh"
SKILL = ROOT / "skills" / "blind-readers" / "SKILL.md"


def item(i, label="CORRECT", action="accept", **extra):
    return dict({"id": i, "label": label, "action": action, "confidence": 0.9,
                 "quote": f"The fee is 10 per unit ({i})", "where": "terms.pdf p.2 cl 3"}, **extra)


def report(reader, items, prose="### C-01\nFACT: the clause states the fee.\n"):
    block = json.dumps({"reader": reader, "items": items} if reader else {"items": items})
    return f"# Reader {reader}\n\n{prose}\n```json\n{block}\n```\n"


class BlindCompare(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        p = self.t / name
        p.write_text(text)
        return str(p)

    def run_(self, *paths):
        return subprocess.run([str(TOOL), *paths], capture_output=True, text=True)

    def pair(self, a_items, b_items):
        return self.run_(self.write("reader-a.md", report("A", a_items)),
                         self.write("reader-b.md", report("B", b_items)))

    def row(self, out, i):
        return next(line for line in out.splitlines() if line.startswith(f"| {i} |"))

    def test_all_items_agree_exits_0(self):
        p = self.pair([item("C-01"), item("C-02", "WRONG", "reject")],
                      [item("C-01"), item("C-02", "WRONG", "reject")])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("| Id | A label | A action | B label | B action | Agree? |", p.stdout)
        self.assertIn("| C-01 | CORRECT | accept | CORRECT | accept | yes |", p.stdout)
        self.assertIn("Agreed: 2 of 2. Disagreed: 0.", p.stdout)
        self.assertNotIn("Held", p.stdout)

    def test_a_label_split_is_held_with_both_quotes(self):
        p = self.pair([item("C-01"), item("C-02")], [item("C-01"), item("C-02", "PARTLY")])
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertTrue(self.row(p.stdout, "C-02").endswith("| **no** |"))
        self.assertIn("Agreed: 1 of 2. Disagreed: 1.", p.stdout)
        held = p.stdout.split("## Held")[1]
        self.assertIn("**C-02**", held)
        self.assertIn('A: CORRECT / accept — "The fee is 10 per unit (C-02)" (terms.pdf p.2 cl 3)', held)
        self.assertIn("B: PARTLY / accept", held)
        self.assertNotIn("**C-01**", held)

    def test_an_action_split_is_a_disagreement(self):
        p = self.pair([item("C-01")], [item("C-01", action="hold")])
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertTrue(self.row(p.stdout, "C-01").endswith("| **no** |"))

    def test_case_and_surrounding_spaces_are_ignored(self):
        p = self.pair([item("C-01", "correct ", " Accept")], [item("C-01", "CORRECT", "accept")])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Agreed: 1 of 1.", p.stdout)

    def test_an_item_missing_from_one_reader_is_a_disagreement(self):
        p = self.pair([item("C-01"), item("C-02")], [item("C-01")])
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("| C-02 | CORRECT | accept | MISSING | — | **no** |", p.stdout)
        self.assertIn("  - B: no answer", p.stdout)

    def test_an_empty_or_missing_label_or_action_is_a_disagreement_not_a_crash(self):
        no_label = {k: v for k, v in item("C-03").items() if k != "label"}
        no_action = {k: v for k, v in item("C-04").items() if k != "action"}
        p = self.pair([item("C-01", label=""), item("C-02", action="  "), no_label, no_action],
                      [item("C-01", label=""), item("C-02", action="  "), dict(no_label), dict(no_action)])
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        for i in ("C-01", "C-02", "C-03", "C-04"):
            with self.subTest(item=i):
                self.assertTrue(self.row(p.stdout, i).endswith("| **no** |"))
        self.assertIn("Agreed: 0 of 4. Disagreed: 4.", p.stdout)
        self.assertIn("| C-03 | — | accept | — | accept | **no** |", p.stdout)
        self.assertIn("| C-04 | CORRECT | — | CORRECT | — | **no** |", p.stdout)
        # a null label is empty too
        p = self.pair([item("C-05", label=None)], [item("C-05", label=None)])
        self.assertEqual(p.returncode, 1, p.stderr)

    def test_three_readers_agree_only_when_all_three_do(self):
        a = self.write("reader-a.md", report("A", [item("C-01"), item("C-02")]))
        b = self.write("reader-b.md", report("B", [item("C-01"), item("C-02")]))
        c = self.write("reader-c.md", report("C", [item("C-01"), item("C-02", "WRONG", "reject")]))
        p = self.run_(a, b, c)
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("| Id | A label | A action | B label | B action | C label | C action | Agree? |", p.stdout)
        self.assertIn("|---|---|---|---|---|---|---|---|", p.stdout)
        self.assertTrue(self.row(p.stdout, "C-01").endswith("| yes |"))
        self.assertTrue(self.row(p.stdout, "C-02").endswith("| **no** |"))
        self.assertEqual(self.run_(a, b, self.write("reader-c2.md", report("C", [item("C-01"), item("C-02")])))
                         .returncode, 0)

    def test_a_report_with_no_json_block_exits_2_and_names_the_file(self):
        a = self.write("reader-a.md", report("A", [item("C-01")]))
        b = self.write("reader-b.md", "# Reader B\n\nC-01 is correct.\n")
        p = self.run_(a, b)
        self.assertEqual(p.returncode, 2)
        self.assertIn("reader-b.md", p.stderr)
        self.assertIn("no ```json block", p.stderr)
        self.assertEqual(p.stdout, "")

    def test_a_block_that_does_not_parse_exits_2(self):
        a = self.write("reader-a.md", report("A", [item("C-01")]))
        b = self.write("reader-b.md", '# Reader B\n\n```json\n{"reader": "B", "items": [\n```\n')
        p = self.run_(a, b)
        self.assertEqual(p.returncode, 2)
        self.assertIn("reader-b.md", p.stderr)
        self.assertIn("does not parse", p.stderr)

    def test_the_last_json_block_in_a_report_is_the_one_read(self):
        draft = "```json\n" + json.dumps({"reader": "B", "items": [item("C-01", "WRONG", "reject")]}) + "\n```\n"
        a = self.write("reader-a.md", report("A", [item("C-01")]))
        b = self.write("reader-b.md", "# Reader B\n\nA first draft:\n\n" + draft + "\n"
                       + report("B", [item("C-01")]))
        p = self.run_(a, b)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("| C-01 | CORRECT | accept | CORRECT | accept | yes |", p.stdout)

    def test_an_item_with_no_id_exits_2_and_names_the_file(self):
        no_id = {k: v for k, v in item("C-02").items() if k != "id"}
        a = self.write("reader-a.md", report("A", [item("C-01"), item("C-02")]))
        for bad in (no_id, dict(no_id, id=""), dict(no_id, id=None), dict(no_id, id="  ")):
            with self.subTest(id=bad.get("id", "absent")):
                b = self.write("reader-b.md", report("B", [item("C-01"), bad]))
                p = self.run_(a, b)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertIn("reader-b.md", p.stderr)
                self.assertIn("no id", p.stderr)
                self.assertNotIn("Traceback", p.stderr)
                self.assertEqual(p.stdout, "")

    def test_a_block_with_no_items_list_exits_2(self):
        a = self.write("reader-a.md", report("A", [item("C-01")]))
        for block in ('{"reader": "B"}', '{"reader": "B", "items": {}}', '[1, 2]', '{"reader": "B", "items": ["C-01"]}'):
            with self.subTest(block=block):
                b = self.write("reader-b.md", f"# Reader B\n\n```json\n{block}\n```\n")
                p = self.run_(a, b)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertIn("reader-b.md", p.stderr)
                self.assertNotIn("Traceback", p.stderr)

    def test_a_reader_with_no_name_is_named_by_its_file(self):
        p = self.run_(self.write("reader-a.md", report(None, [item("C-01")])),
                      self.write("reader-b.md", report("B", [item("C-01")])))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("| Id | reader-a label | reader-a action | B label | B action | Agree? |", p.stdout)

    def test_fewer_than_two_reports_or_a_missing_file_exits_2(self):
        a = self.write("reader-a.md", report("A", [item("C-01")]))
        p = self.run_(a)
        self.assertEqual(p.returncode, 2)
        self.assertIn("blind-compare", p.stderr)
        p = self.run_(a, str(self.t / "reader-x.md"))
        self.assertEqual(p.returncode, 2)
        self.assertIn("reader-x.md", p.stderr)
        self.assertNotIn("Traceback", p.stderr)


class SkillPaths(unittest.TestCase):
    """Every ${CLAUDE_PLUGIN_ROOT}/... path the blind-readers skill names is a file in the plugin."""

    def test_every_plugin_path_the_skill_names_exists(self):
        text = SKILL.read_text()
        paths = {m.rstrip(".,:;") for m in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\s`\"'<>)\]]+)", text)}
        self.assertGreaterEqual(len(paths), 6, sorted(paths))
        for rel in sorted(paths):
            with self.subTest(path=rel):
                self.assertTrue((ROOT / rel).is_file(), f"{rel} is named by the skill but is not in the plugin")

    def test_the_skill_names_no_old_personal_paths(self):
        text = SKILL.read_text()
        self.assertNotIn("~/permanence", text)
        self.assertNotIn("record-prediction", text)
        self.assertNotIn("~/.claude/skills", text)


if __name__ == "__main__":
    unittest.main()
