"""Tests for runtime/shadow-predict.sh: it writes moves.prediction.json from a shadow's flat before/after
pair and a written expect file. Standard library only:
python3 -m unittest discover -s runtime/tests"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
TOOL = RUNTIME / "shadow-predict.sh"
GATE = RUNTIME / "predict-gate.sh"
MARK = "[value from the shadow] "

BEFORE = {"page:home/title": "Welcome", "page:about/title": "About", "score:demo|a|lost": 3,
          "score:demo|b|lost": 5, "count:pages": 12, "hash:site": "aaa", "corpus:demo/v1/rules.json": "h1"}


class ShadowPredict(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.out = self.d / "moves.prediction.json"
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.d)}

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data):
        (self.d / name).write_text(json.dumps(data))
        return self.d / name

    def run_tool(self, expect, after, before=BEFORE, *extra, out=None):
        args = [str(TOOL), "--before", str(self.write("before.json", before)),
                "--after", str(self.write("after.json", after)),
                "--expect", str(self.write("expect.json", expect)), "--out", str(out or self.out), *extra]
        return subprocess.run(args, capture_output=True, text=True, env=self.env)

    def after(self, **changes):
        return {**BEFORE, **changes}

    def moves(self, out=None):
        return {m["what"]: m for m in json.loads((out or self.out).read_text())["moves"]}

    def ok(self, p):
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def refused(self, p, code, *words):
        self.assertEqual(p.returncode, code, p.stdout + p.stderr)
        self.assertFalse(self.out.exists(), "a refusal writes nothing")
        for w in words:
            self.assertIn(w, p.stdout + p.stderr)

    # --- what it writes

    def test_a_key_in_both_files_gets_from_and_to_copied_and_marked(self):
        p = self.run_tool({"expect": [{"what": "page:home/title", "why": "the title is reworded"}]},
                          self.after(**{"page:home/title": "Hello"}))
        self.ok(p)
        self.assertEqual(self.moves(), {"page:home/title": {
            "what": "page:home/title", "from": "Welcome", "to": "Hello", "scope": MARK + "the title is reworded"}})

    def test_a_new_key_gets_to_only_and_is_marked(self):
        p = self.run_tool({"expect": [{"what": "page:new/title", "why": "a new page"}]},
                          self.after(**{"page:new/title": "New"}))
        self.ok(p)
        self.assertEqual(self.moves()["page:new/title"],
                         {"what": "page:new/title", "to": "New", "scope": MARK + "a new page"})

    def test_a_key_that_goes_gets_no_value_and_no_mark(self):
        after = dict(BEFORE)
        del after["page:about/title"]
        p = self.run_tool({"expect": [{"what": "page:about/title", "why": "the page is removed"}]}, after)
        self.ok(p)
        self.assertEqual(self.moves()["page:about/title"],
                         {"what": "page:about/title", "scope": "the page is removed"})

    def test_value_any_writes_no_value_and_no_mark(self):
        p = self.run_tool({"expect": [{"what": "hash:*", "why": "the hash changes", "value": "any"}]},
                          self.after(**{"hash:site": "bbb"}))
        self.ok(p)
        self.assertEqual(self.moves()["hash:site"], {"what": "hash:site", "scope": "the hash changes"})

    def test_a_change_within_tolerance_is_not_a_move(self):
        p = self.run_tool({"tolerance": 0.5, "expect": [{"what": "count:pages", "why": "one page more"}]},
                          self.after(**{"count:pages": 13, "score:demo|a|lost": 3.2}))
        self.ok(p)
        self.assertEqual(list(self.moves()), ["count:pages"])
        self.assertEqual(json.loads(self.out.read_text())["tolerance"], 0.5)

    def test_the_first_matching_entry_wins(self):
        p = self.run_tool({"expect": [{"what": "score:demo|a|*", "why": "case a"},
                                      {"what": "score:demo|*|lost", "why": "every case"}]},
                          self.after(**{"score:demo|a|lost": 4, "score:demo|b|lost": 6}))
        self.ok(p)
        m = self.moves()
        self.assertEqual(m["score:demo|a|lost"]["scope"], MARK + "case a")
        self.assertEqual(m["score:demo|b|lost"]["scope"], MARK + "every case")

    def test_brackets_in_a_key_are_literal(self):
        before = {**BEFORE, "verdict:== [true]": 1, "verdict:== t": 1}
        p = self.run_tool({"expect": [{"what": "verdict:== [true]", "why": "the verdict flips"},
                                      {"what": "verdict:*", "why": "another verdict"}]},
                          {**before, "verdict:== [true]": 0, "verdict:== t": 0}, before)
        self.ok(p)
        m = self.moves()
        self.assertEqual(m["verdict:== [true]"]["scope"], MARK + "the verdict flips")
        self.assertEqual(m["verdict:== t"]["scope"], MARK + "another verdict")

    def test_scopes_override_why_for_each_key(self):
        scopes = self.write("scopes.json", {"score:demo|a|lost": "case a, from the census"})
        p = self.run_tool({"expect": [{"what": "score:demo|*|lost", "why": "lost counts change"}]},
                          self.after(**{"score:demo|a|lost": 4, "score:demo|b|lost": 6}),
                          BEFORE, "--scopes", str(scopes))
        self.ok(p)
        m = self.moves()
        self.assertEqual(m["score:demo|a|lost"]["scope"], MARK + "case a, from the census")
        self.assertEqual(m["score:demo|b|lost"]["scope"], MARK + "lost counts change")

    def test_a_shadow_false_entry_is_copied_as_written_with_no_mark(self):
        p = self.run_tool({"expect": [{"what": "page:home/title", "why": "reworded"},
                                      {"what": "tests:passed", "why": "5 new tests", "delta": 5, "shadow": False},
                                      {"what": "hash:edited-file", "why": "the file is edited", "shadow": False}]},
                          self.after(**{"page:home/title": "Hello"}))
        self.ok(p)
        m = self.moves()
        self.assertEqual(m["tests:passed"], {"what": "tests:passed", "delta": 5, "scope": "5 new tests"})
        self.assertEqual(m["hash:edited-file"], {"what": "hash:edited-file", "scope": "the file is edited"})

    def test_the_mark_is_on_copied_values_only(self):
        after = self.after(**{"page:home/title": "Hello", "page:new/title": "New", "hash:site": "bbb",
                              "count:pages": 13})
        del after["page:about/title"]
        p = self.run_tool({"expect": [
            {"what": "page:home/title", "why": "copied from and to"},
            {"what": "page:new/title", "why": "copied to"},
            {"what": "page:about/title", "why": "goes"},
            {"what": "hash:site", "why": "any value", "value": "any"},
            {"what": "count:pages", "why": "written", "to": 13},
            {"what": "tests:passed", "why": "not in the shadow", "range": [10, 20], "shadow": False}]}, after)
        self.ok(p)
        marked = {k for k, m in self.moves().items() if m["scope"].startswith(MARK)}
        self.assertEqual(marked, {"page:home/title", "page:new/title"})

    def test_a_written_value_the_shadow_matches_is_kept_as_written(self):
        p = self.run_tool({"expect": [{"what": "count:pages", "why": "one page more", "to": 13},
                                      {"what": "score:demo|a|lost", "why": "two fewer lost", "delta": -2},
                                      {"what": "score:demo|b|lost", "why": "a few fewer", "range": [1, 4]}]},
                          self.after(**{"count:pages": 13, "score:demo|a|lost": 1, "score:demo|b|lost": 2}))
        self.ok(p)
        self.assertEqual(self.moves(), {
            "count:pages": {"what": "count:pages", "to": 13, "scope": "one page more"},
            "score:demo|a|lost": {"what": "score:demo|a|lost", "delta": -2, "scope": "two fewer lost"},
            "score:demo|b|lost": {"what": "score:demo|b|lost", "range": [1, 4], "scope": "a few fewer"}})

    def test_a_written_value_the_shadow_does_not_match_refuses(self):
        for entry, value in (({"to": 14}, 13), ({"delta": 2}, 13), ({"range": [20, 30]}, 13), ({"to": 13}, None)):
            with self.subTest(entry):
                after = self.after(**{"count:pages": value})
                if value is None:
                    del after["count:pages"]
                p = self.run_tool({"expect": [{"what": "count:pages", "why": "written", **entry}]}, after)
                self.refused(p, 1, "count:pages")

    def test_the_output_copies_about_baseline_and_must_not_move_and_sorts_moves(self):
        p = self.run_tool({"about": "T1: the demo change", "baseline": "moves.measured.json",
                           "mustNotMove": ["corpus:*"],
                           "expect": [{"what": "page:home/title", "why": "b"}, {"what": "count:pages", "why": "a"}]},
                          self.after(**{"page:home/title": "Hello", "count:pages": 13}))
        self.ok(p)
        out = json.loads(self.out.read_text())
        self.assertEqual(out["about"], "T1: the demo change")
        self.assertEqual(out["baseline"], "moves.measured.json")
        self.assertEqual(out["mustNotMove"], ["corpus:*"])
        self.assertEqual([m["what"] for m in out["moves"]], ["count:pages", "page:home/title"])

    def test_a_key_a_must_not_move_glob_covers_may_still_be_expected(self):
        p = self.run_tool({"mustNotMove": ["page:*"], "expect": [{"what": "page:home/title", "why": "reworded"}]},
                          self.after(**{"page:home/title": "Hello"}))
        self.ok(p)

    # --- refusals

    def test_an_unexplained_move_refuses(self):
        p = self.run_tool({"expect": [{"what": "page:home/title", "why": "reworded"}]},
                          self.after(**{"page:home/title": "Hello", "count:pages": 13}))
        self.refused(p, 1, "count:pages")

    def test_an_expected_key_that_did_not_move_refuses(self):
        p = self.run_tool({"expect": [{"what": "page:home/title", "why": "reworded"},
                                      {"what": "count:pages", "why": "one page more"}]},
                          self.after(**{"page:home/title": "Hello"}))
        self.refused(p, 1, "count:pages")

    def test_an_optional_entry_may_match_nothing(self):
        p = self.run_tool({"expect": [{"what": "page:home/title", "why": "reworded"},
                                      {"what": "count:pages", "why": "maybe", "optional": True}]},
                          self.after(**{"page:home/title": "Hello"}))
        self.ok(p)
        self.assertEqual(list(self.moves()), ["page:home/title"])

    def test_a_count_mismatch_refuses_and_a_match_passes(self):
        after = self.after(**{"score:demo|a|lost": 4, "score:demo|b|lost": 6})
        p = self.run_tool({"expect": [{"what": "score:demo|*|lost", "why": "lost", "count": 3}]}, after)
        self.refused(p, 1, "score:demo|*|lost")
        p = self.run_tool({"expect": [{"what": "score:demo|*|lost", "why": "lost", "count": 2}]}, after)
        self.ok(p)

    def test_a_shadow_base_that_differs_from_the_sealed_base_refuses(self):
        expect = {"expect": [{"what": "page:home/title", "why": "reworded"}]}
        after = self.after(**{"page:home/title": "Hello"})
        sealed = self.write("sealed.json", BEFORE)
        shadow_base = self.write("shadow-base.json", {**BEFORE, "count:pages": 11})
        p = self.run_tool(expect, after, BEFORE, "--shadow-base", str(shadow_base), "--sealed-base", str(sealed))
        self.refused(p, 1, "count:pages")
        same_base = self.write("same-base.json", BEFORE)
        p = self.run_tool(expect, after, BEFORE, "--shadow-base", str(same_base), "--sealed-base", str(sealed))
        self.ok(p)

    def test_a_shadow_base_without_a_sealed_base_is_bad_input(self):
        p = self.run_tool({"expect": [{"what": "page:home/title", "why": "reworded"}]},
                          self.after(**{"page:home/title": "Hello"}), BEFORE,
                          "--shadow-base", str(self.write("sb.json", BEFORE)))
        self.refused(p, 2, "--sealed-base")

    def test_an_existing_output_refuses_and_is_left_as_it_is(self):
        self.out.write_text("sealed\n")
        p = self.run_tool({"expect": [{"what": "page:home/title", "why": "reworded"}]},
                          self.after(**{"page:home/title": "Hello"}))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(self.out.read_text(), "sealed\n")

    def test_a_yaml_prediction_of_the_same_round_counts_as_existing(self):
        (self.d / "moves.prediction.fix1.yaml").write_text("moves: []\n")
        out = self.d / "moves.prediction.fix1.json"
        p = self.run_tool({"expect": [{"what": "page:home/title", "why": "reworded"}]},
                          self.after(**{"page:home/title": "Hello"}), out=out)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertFalse(out.exists())

    def test_an_output_name_that_is_not_a_json_prediction_is_bad_input(self):
        for name in ("prediction.json", "moves.prediction.yaml", "moves.measured.json"):
            with self.subTest(name):
                p = self.run_tool({"expect": [{"what": "page:home/title", "why": "reworded"}]},
                                  self.after(**{"page:home/title": "Hello"}), out=self.d / name)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertFalse((self.d / name).exists())

    def test_a_nested_input_value_is_bad_input(self):
        p = self.run_tool({"expect": [{"what": "page:home/title", "why": "reworded"}]},
                          self.after(**{"page:home/title": {"text": "Hello"}}))
        self.refused(p, 2, "page:home/title")

    def test_an_unknown_expect_field_is_bad_input(self):
        for expect in ({"expect": [{"what": "page:home/title", "why": "r"}], "moves": []},
                       {"expect": [{"what": "page:home/title", "why": "r", "from": "Welcome"}]},
                       {"expect": [{"what": "page:home/title", "why": "r", "to": "Hello", "delta": 1}]},
                       {"expect": [{"what": "page:home/title", "why": "r", "value": "any", "to": "Hello"}]},
                       {"expect": [{"what": "page:home/title"}]},
                       {"expect": [{"what": "page:home/title", "why": "r", "value": "all"}]},
                       {"expect": []}):
            with self.subTest(expect):
                p = self.run_tool(expect, self.after(**{"page:home/title": "Hello"}))
                self.refused(p, 2)

    def test_an_expect_what_that_is_also_in_must_not_move_is_bad_input(self):
        p = self.run_tool({"mustNotMove": ["page:*"], "expect": [{"what": "page:*", "why": "titles"}]},
                          self.after(**{"page:home/title": "Hello"}))
        self.refused(p, 2, "page:*")

    def test_a_moved_key_that_only_a_shadow_false_entry_matches_refuses(self):
        p = self.run_tool({"expect": [{"what": "count:pages", "why": "written", "to": 13, "shadow": False}]},
                          self.after(**{"count:pages": 13}))
        self.refused(p, 1, "count:pages", "shadow")

    def test_the_self_check_refuses_a_must_not_move_entry_that_matches_no_key(self):
        p = self.run_tool({"mustNotMove": ["nothing:*"], "expect": [{"what": "page:home/title", "why": "r"}]},
                          self.after(**{"page:home/title": "Hello"}))
        self.refused(p, 1, "nothing:*")

    def test_a_result_with_no_move_refuses(self):
        p = self.run_tool({"expect": [{"what": "page:home/title", "why": "maybe", "optional": True}]}, BEFORE)
        self.refused(p, 1, "noMoves")

    # --- the gate accepts what it writes

    def test_the_written_prediction_passes_the_gate_check(self):
        after = self.after(**{"page:home/title": "Hello", "page:new/title": "New", "hash:site": "bbb",
                              "count:pages": 13, "score:demo|a|lost": 1})
        del after["page:about/title"]
        out = self.d / "moves.prediction.fix1.json"
        p = self.run_tool({"tolerance": 0, "mustNotMove": ["corpus:*", "score:*"], "expect": [
            {"what": "page:*", "why": "pages change"},
            {"what": "hash:*", "why": "any value", "value": "any"},
            {"what": "count:pages", "why": "written", "to": 13},
            {"what": "score:demo|a|lost", "why": "two fewer", "delta": -2},
            {"what": "tests:passed", "why": "new tests", "range": [10, 20], "shadow": False}]}, after, out=out)
        self.ok(p)
        self.write("moves.baseline.json", BEFORE)
        self.write("moves.measured.fix1.json", after)
        g = subprocess.run([str(GATE), "check", "--measured", str(self.d / "moves.measured.fix1.json")],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(g.returncode, 0, g.stdout + g.stderr)
        self.assertIn("Result: PASS", g.stdout)


if __name__ == "__main__":
    unittest.main()
