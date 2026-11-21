"""Tests for 0.11.1 (docs/PREDICT-GATE.md, "A chained round"): close reads a chained round's code from the head of
the sealed measurement its prediction names as its baseline, not from its report's base; and a paired round (one
that measures the same code as another round) is covered by that round's sealed code commit. Built on
test_sealed_close's scratch project. Standard library only.
python3 -m unittest runtime/tests/test_chained_paired_close.py
"""

import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
import test_sealed_close as tsc  # noqa: E402 - a module, so its tests are not collected here again

GOOD_P, BASE_M, PASS_M = tsc.GOOD_P, tsc.BASE_M, tsc.PASS_M
EARLY = "which already holds code this round measured"
NOT_FIRST = "is not the round's first code change"
NO_SEAL = "no code commit of the round is sealed"
LATE = "its prediction was sealed after its first code commit"


def chained(name, frm, to):
    return {"moves": [{"what": "out:v", "from": frm, "to": to}], "mustNotMove": ["out:w"], "baseline": name}


class Base(tsc.Sealed):
    """test_sealed_close's scratch project. None of Sealed's own tests run here."""

    def first_and_definition(self, late_definition=False):
        """The first round and its paired definition round: two predictions, one sealed code commit, two
        measurements of that one code, each report naming the work order's base. Returns (base, head)."""
        wo = self.head()
        self.commit("W-1: predictions", {
            self.r("moves.prediction.json"): GOOD_P, self.r("moves.baseline.json"): BASE_M,
            self.r("moves.prediction.definition.json"): GOOD_P, self.r("moves.baseline.definition.json"): BASE_M,
        })
        self.assertEqual(self.seal("first", "moves.prediction.json"), 0)
        self.assertEqual(self.seal("first", "moves.baseline.json"), 0)
        if not late_definition:
            self.assertEqual(self.seal("definition", "moves.prediction.definition.json"), 0)
            self.assertEqual(self.seal("definition", "moves.baseline.definition.json"), 0)
        self.assertEqual(self.code("W-1: the change", {"src/app.py": "v = 2\n"}), 0)
        h = self.head()
        if late_definition:
            self.assertEqual(self.seal("definition", "moves.prediction.definition.json"), 0)
            self.assertEqual(self.seal("definition", "moves.baseline.definition.json"), 0)
        self.assertEqual(self.measure("first", PASS_M, wo, h), 0)
        self.assertEqual(self.measure("definition", PASS_M, wo, h), 0)
        self.commit("W-1: measurements")
        return wo, h

    def code_seal(self, rnd):
        _, evs, _ = tsc.pg.sealed_state(self.ledger, "W-1")
        return next(e["commit"] for e in evs if e.get("fileKind") == "code" and e.get("round") == rnd)

    def fix1_chained(self, wo, seal_first=True, outside=False):
        """fix1, chained on the first round's measured file, its report naming the work order's base."""
        self.commit("W-1: fix1 prediction", {self.r("moves.prediction.fix1.json"): chained("moves.measured.json", 2, 3)})
        if seal_first:
            self.assertEqual(self.seal("fix1", "moves.prediction.fix1.json"), 0)
        if outside:
            self.commit("W-1: fix1 change, outside the tool", {"src/app.py": "v = 3\n"})
            self.assertEqual(self.code("W-1: fix1 tidy", {"src/extra.py": "x = 1\n"}, "fix1"), 0)
        else:
            self.assertEqual(self.code("W-1: fix1 change", {"src/app.py": "v = 3\n"}, "fix1"), 0)
        if not seal_first:
            self.assertEqual(self.seal("fix1", "moves.prediction.fix1.json"), 0)
        self.assertEqual(self.measure("fix1", {"out:v": 3, "out:w": 5}, wo, self.head()), 0)
        self.commit("W-1: fix1 measurement")


for _n in [n for n in dir(tsc.Sealed) if n.startswith("test")]:
    setattr(Base, _n, None)  # not callable: the loader skips Sealed's own tests


class Chained(Base):
    def test_1_six_rounds_chained_and_paired_close(self):
        wo, h1 = self.first_and_definition()
        self.commit("W-1: fix1 predictions", {
            self.r("moves.prediction.fix1.json"): chained("moves.measured.json", 2, 3),
            self.r("moves.prediction.definition-fix1.json"): chained("moves.measured.definition.json", 2, 3),
        })
        self.assertEqual(self.seal("fix1", "moves.prediction.fix1.json"), 0)
        self.assertEqual(self.seal("definition-fix1", "moves.prediction.definition-fix1.json"), 0)
        self.assertEqual(self.code("W-1: fix1 change", {"src/app.py": "v = 3\n"}, "fix1"), 0)
        h2 = self.head()
        self.assertEqual(self.measure("fix1", {"out:v": 3, "out:w": 5}, wo, h2), 0)
        self.assertEqual(self.measure("definition-fix1", {"out:v": 3, "out:w": 5}, h1, h2), 0)
        self.commit("W-1: fix1 measurements")
        still = {"noMoves": True, "mustNotMove": ["out:v", "out:w"]}
        self.commit("W-1: fix2 predictions", {
            self.r("moves.prediction.fix2.json"): dict(still, baseline="moves.measured.fix1.json"),
            self.r("moves.prediction.definition-fix2.json"): dict(still, baseline="moves.measured.definition-fix1.json"),
        })
        self.assertEqual(self.seal("fix2", "moves.prediction.fix2.json"), 0)
        self.assertEqual(self.seal("definition-fix2", "moves.prediction.definition-fix2.json"), 0)
        self.commit("W-1: fix2 tests only", {"tests/test_app.py": "assert True\n"})
        h3 = self.head()
        self.assertEqual(self.measure("fix2", {"out:v": 3, "out:w": 5}, wo, h3), 0)
        self.assertEqual(self.measure("definition-fix2", {"out:v": 3, "out:w": 5}, h2, h3), 0)
        self.commit("W-1: fix2 measurements")
        self.assertCloses()

    def test_2_a_chained_round_whose_prediction_is_sealed_after_its_own_code_is_still_refused(self):
        wo = self.flow()
        self.fix1_chained(wo, seal_first=False)
        self.assertRefuses(LATE, "a prediction is sealed before its code")

    def test_3_a_chained_round_whose_code_seal_is_not_its_first_code_change_is_still_refused(self):
        wo = self.flow()
        self.fix1_chained(wo, outside=True)
        self.assertRefuses(NOT_FIRST)

    def test_6_a_chained_round_whose_baseline_has_no_sealed_measurement_keeps_todays_rule(self):
        # fix1's measured file is never sealed; fix2 chains on it, its report naming the work order's base
        wo = self.flow()
        self.commit("W-1: fix1 prediction", {self.r("moves.prediction.fix1.json"): GOOD_P})
        self.assertEqual(self.seal("fix1", "moves.prediction.fix1.json"), 0)
        self.commit("W-1: fix1 measurement (unsealed)", {self.r("moves.measured.fix1.json"): PASS_M})
        self.commit("W-1: fix2 prediction", {self.r("moves.prediction.fix2.json"): chained("moves.measured.fix1.json", 2, 3)})
        self.assertEqual(self.seal("fix2", "moves.prediction.fix2.json"), 0)
        self.assertEqual(self.code("W-1: fix2 change", {"src/app.py": "v = 3\n"}, "fix2"), 0)
        self.measure("fix2", {"out:v": 3, "out:w": 5}, wo, self.head())
        self.commit("W-1: fix2 measurement")
        self.assertRefuses(EARLY, NOT_FIRST)

    def test_6_a_chained_round_whose_baseline_seal_is_retired_keeps_todays_rule(self):
        wo = self.flow()
        self.fix1_chained(wo)
        self.commit(
            "W-1: first measurement to history/",
            mv=[(self.r("moves.measured.json"), self.r("history/moves.measured.json"))],
        )
        self.decide("d20261101-bbbbb2", approves="retire", choice="retire", options=("keep", "retire"))
        p = self.gate("retire", "W-1", "first", str(self.repo / self.r("history/moves.measured.json")),
                      "--decision", "d20261101-bbbbb2")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRefuses(EARLY, NOT_FIRST)

    def test_7_an_unchained_round_keeps_todays_rule(self):
        wo = self.flow()
        self.commit("W-1: fix1 prediction", {self.r("moves.prediction.fix1.json"): {
            "moves": [{"what": "out:v", "from": 1, "to": 3}], "mustNotMove": ["out:w"]}})
        self.assertEqual(self.seal("fix1", "moves.prediction.fix1.json"), 0)
        self.assertEqual(self.code("W-1: fix1 change", {"src/app.py": "v = 3\n"}, "fix1"), 0)
        self.assertEqual(self.measure("fix1", {"out:v": 3, "out:w": 5}, wo, self.head()), 0)
        self.commit("W-1: fix1 measurement")
        self.assertRefuses(EARLY, NOT_FIRST)

    def test_f1_two_rounds_chained_on_each_other_are_still_refused(self):
        # each prediction chains on the other round's measurement, sealed after it: neither chain is accepted
        wo = self.flow()
        self.commit("W-1: code outside the tool, never sealed", {"src/app.py": "v = 3\n"})
        h2 = self.head()
        still = {"noMoves": True, "mustNotMove": ["out:v", "out:w"]}
        self.commit("W-1: late predictions", {
            self.r("moves.prediction.fix1.json"): dict(still, baseline="moves.measured.definition.json"),
            self.r("moves.prediction.definition.json"): dict(still, baseline="moves.measured.fix1.json"),
            self.r("moves.baseline.extra.json"): {"out:v": 3, "out:w": 5}})
        self.assertEqual(self.seal("fix1", "moves.prediction.fix1.json"), 0)
        self.assertEqual(self.seal("definition", "moves.prediction.definition.json"), 0)
        self.assertEqual(self.seal("extra", "moves.baseline.extra.json"), 0)
        self.put("moves.measured.definition.json", {"out:v": 3, "out:w": 5})
        self.measure("fix1", {"out:v": 3, "out:w": 5}, wo, h2)
        self.measure("definition", {"out:v": 3, "out:w": 5}, wo, h2)
        self.commit("W-1: measurements")
        self.assertRefuses(f"round fix1 of W-1: its measurement changed code under the code roots, and {NO_SEAL}",
                           f"round definition of W-1: its measurement changed code under the code roots, and {NO_SEAL}")

    def test_f3_a_named_measurement_whose_latest_live_seal_has_no_head_keeps_todays_rule(self):
        wo = self.flow()
        self.forge({"ts": "2026-11-10T09:40:00+00:00", "kind": "sealed", "task": "W-1", "round": "first",
                    "fileKind": "measured", "path": self.r("moves.measured.json"),
                    "sha256": self.sha_of("moves.measured.json"), "commit": self.head()[:7]})
        self.fix1_chained(wo)
        self.assertRefuses(EARLY, NOT_FIRST)

    def test_t1_a_chained_round_reads_the_latest_live_measurement_of_its_named_file(self):
        # the first round's measured file is sealed again, same bytes, at a later head: fix1's code starts there
        wo = self.flow()
        self.commit("W-1: more first-round code", {"src/app.py": "v = 2  # tidied\n"})
        self.assertEqual(self.measure("first", PASS_M, wo, self.head()), 0)
        self.commit("W-1: first re-measured")
        self.fix1_chained(wo)
        self.assertCloses()

    def test_t5_a_named_measurement_whose_head_is_not_in_the_repository_keeps_todays_rule(self):
        wo = self.flow()
        self.commit("W-1: first-round tests", {"tests/test_app.py": "assert True\n"})
        gone = self.head()
        self.assertEqual(self.measure("first", PASS_M, wo, gone), 0)
        self.git("commit", "-q", "--amend", "-m", "W-1: first-round tests, reworded")
        for a in (["reflog", "expire", "--expire=now", "--all"], ["gc", "-q", "--prune=now"]):
            self.git(*a)
        self.assertEqual(self.git("cat-file", "-t", gone), "", "the head is gone")
        self.commit("W-1: first re-measured")
        self.fix1_chained(wo)
        rc, out = self.close(EARLY, NOT_FIRST)
        self.assertEqual(rc, 1, out)
        self.assertNotIn("is not in this repository", out)


class Paired(Base):
    def test_1_a_paired_round_is_covered_by_its_pairs_code_seal(self):
        self.first_and_definition()
        self.assertCloses()

    def test_4_a_paired_round_whose_prediction_is_sealed_after_the_covering_code_commit_is_refused(self):
        self.first_and_definition(late_definition=True)
        cs = self.code_seal("first")
        rc, out = self.close(f"round definition of W-1: {LATE} {cs[:8]}")
        self.assertEqual(rc, 1, out)
        self.assertNotIn("round definition of W-1: its measurement changed code", out)

    def test_5_a_round_with_no_code_seal_whose_span_matches_no_sealed_round_is_still_refused(self):
        wo = self.flow()
        self.commit("W-1: definition prediction", {
            self.r("moves.prediction.definition.json"): {"moves": [{"what": "out:v", "from": 1, "to": 3}],
                                                         "mustNotMove": ["out:w"]}, self.r("moves.baseline.definition.json"): BASE_M})
        self.assertEqual(self.seal("definition", "moves.prediction.definition.json"), 0)
        self.assertEqual(self.seal("definition", "moves.baseline.definition.json"), 0)
        self.commit("W-1: more code, outside the tool", {"src/app.py": "v = 3\n"})
        self.assertEqual(self.measure("definition", {"out:v": 3, "out:w": 5}, wo, self.head()), 0)
        self.commit("W-1: definition measurement")
        self.assertRefuses(f"round definition of W-1: its measurement changed code under the code roots, and {NO_SEAL}")

    def test_t2_a_covering_round_whose_own_code_seal_fails_covers_nothing(self):
        wo = self.head()
        self.commit("W-1: predictions", {
            self.r("moves.prediction.json"): GOOD_P, self.r("moves.baseline.json"): BASE_M,
            self.r("moves.prediction.definition.json"): GOOD_P, self.r("moves.baseline.definition.json"): BASE_M,
        })
        for rnd, n in (("first", ""), ("definition", ".definition")):
            self.assertEqual(self.seal(rnd, f"moves.prediction{n}.json"), 0)
            self.assertEqual(self.seal(rnd, f"moves.baseline{n}.json"), 0)
        self.commit("W-1: the change, outside the tool", {"src/app.py": "v = 2\n"})
        self.assertEqual(self.code("W-1: a tidy", {"src/extra.py": "x = 1\n"}), 0)
        h = self.head()
        self.assertEqual(self.measure("first", PASS_M, wo, h), 0)
        self.assertEqual(self.measure("definition", PASS_M, wo, h), 0)
        self.commit("W-1: measurements")
        self.assertRefuses(NOT_FIRST,
                           f"round definition of W-1: its measurement changed code under the code roots, and {NO_SEAL}")

    def test_t3_the_same_head_code_from_another_base_code_is_not_covered(self):
        wo = self.head()
        self.git("checkout", "-q", "-b", "other")
        self.commit("W-1: another base, outside the tool", {"src/x.py": "x = 1\n"})
        other = self.head()
        self.git("checkout", "-q", self.default)
        self.commit("W-1: predictions", {
            self.r("moves.prediction.json"): GOOD_P, self.r("moves.baseline.json"): BASE_M,
            self.r("moves.prediction.definition.json"): GOOD_P, self.r("moves.baseline.definition.json"): BASE_M,
        })
        for rnd, n in (("first", ""), ("definition", ".definition")):
            self.assertEqual(self.seal(rnd, f"moves.prediction{n}.json"), 0)
            self.assertEqual(self.seal(rnd, f"moves.baseline{n}.json"), 0)
        self.assertEqual(self.code("W-1: the change", {"src/app.py": "v = 2\n"}), 0)
        h = self.head()
        self.assertEqual(self.measure("first", PASS_M, wo, h), 0)
        self.assertEqual(self.measure("definition", PASS_M, other, h), 0)
        self.commit("W-1: measurements")
        self.assertRefuses(f"round definition of W-1: its measurement changed code under the code roots, and {NO_SEAL}")

    def test_f4_a_covering_code_commit_outside_the_covered_rounds_history_covers_nothing(self):
        # the definition round's code is the same code, redone on another line that never held the sealed commit
        wo = self.head()
        self.commit("W-1: predictions", {
            self.r("moves.prediction.json"): GOOD_P, self.r("moves.baseline.json"): BASE_M,
            self.r("moves.prediction.definition.json"): GOOD_P, self.r("moves.baseline.definition.json"): BASE_M,
        })
        for rnd, n in (("first", ""), ("definition", ".definition")):
            self.assertEqual(self.seal(rnd, f"moves.prediction{n}.json"), 0)
            self.assertEqual(self.seal(rnd, f"moves.baseline{n}.json"), 0)
        self.git("checkout", "-q", "-b", "redo")
        self.commit("W-1: the change, redone outside the tool", {"src/app.py": "v = 2\n"})
        redo = self.head()
        self.git("checkout", "-q", self.default)
        self.assertEqual(self.code("W-1: the change", {"src/app.py": "v = 2\n"}), 0)
        self.assertEqual(self.measure("first", PASS_M, wo, self.head()), 0)
        self.assertEqual(self.measure("definition", PASS_M, wo, redo), 0)
        self.commit("W-1: measurements")
        self.git("merge", "-q", "--no-ff", "redo", "-m", "Merge redo")
        self.assertRefuses(f"round definition of W-1: its measurement changed code under the code roots, and {NO_SEAL}")


if __name__ == "__main__":
    unittest.main()
