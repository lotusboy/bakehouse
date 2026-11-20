"""Tests for an approval answered in the same whole second as the revise it approves (0.8.0): every stamp is whole
seconds, so an answer written first can carry the revise's own stamp. decision_refusal counts an answer stamped at or
before the time it judges. Standard library only; test_sealed_close's scratch project.
python3 -m unittest runtime/tests/test_same_second_approval.py
"""

import datetime
import json
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
import test_sealed_close as tsc  # noqa: E402 - a module, so its tests are not collected here again

DID = "d20261101-bbbbb1"


class SameSecond(tsc.Sealed):
    def revised_round(self):
        """A sealed round whose prediction was revised with DID's approval, then measured. Returns the revise's
        stamp."""
        self.commit("W-1: prediction", {self.r("moves.prediction.json"): tsc.GOOD_P,
                                        self.r("moves.baseline.json"): tsc.BASE_M})
        self.assertEqual(self.seal("first", "moves.prediction.json"), 0)
        self.assertEqual(self.seal("first", "moves.baseline.json"), 0)
        self.put("moves.prediction.json", tsc.WIDE_P)
        self.decide(DID)
        p = self.gate("revise", "W-1", "first", str(self.repo / self.r("moves.prediction.json")), "--decision", DID)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.commit("W-1: prediction widened")
        b = self.head()
        self.code("W-1: the change", {"src/app.py": "v = 2\n"})
        self.measure("first", tsc.PASS_M, b, self.head())
        self.commit("W-1: measurement")
        return next(e["ts"] for e in map(json.loads, self.ledger.read_text().splitlines()) if e["kind"] == "revised")

    def answer_at(self, ts):
        led = self.records / "decisions/ledger.jsonl"
        rows = [json.loads(x) for x in led.read_text().splitlines()]
        led.write_text("".join(json.dumps(dict(r, ts=ts) if r["kind"] == "answer" else r) + "\n" for r in rows))

    def test_an_answer_in_the_same_second_as_the_revise_approves_it(self):
        self.answer_at(self.revised_round())
        self.assertCloses()

    def test_an_answer_one_second_after_the_revise_still_refuses(self):
        at = datetime.datetime.fromisoformat(self.revised_round())
        self.answer_at((at + datetime.timedelta(seconds=1)).isoformat())
        self.assertRefuses(f"decision {DID} has no answer recorded before the revise")


for _n in [n for n in dir(tsc.Sealed) if n.startswith("test")]:
    setattr(SameSecond, _n, None)  # not callable: the loader skips Sealed's own tests


if __name__ == "__main__":
    unittest.main()
