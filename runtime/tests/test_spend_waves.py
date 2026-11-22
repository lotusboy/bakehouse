"""0.14.1: a wave's figure is the tokens its agents added while in it (runtime/spend.py, `segments`), whatever a
continued agent's running total is. Standard library only: python3 -m unittest discover -s runtime/tests"""
import json
import random
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_gate_events import Scratch  # noqa: E402

RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNTIME))
import locator  # noqa: E402
import spend  # noqa: E402


def row(agent, tokens, wave):
    return {"event": "agent", "agent": agent, "label": f"job {agent}", "tokens": tokens, "wave": wave,
            "kind": "engineer", "source": "harness", "ts": "2026-01-02T12:00:00+00:00"}


def opened(*names):
    return [{"event": "wave-open", "wave": n, "ceiling": 1000, "ts": "2026-01-02T11:00:00+00:00"} for n in names]


def spent(*rows):
    """wave -> (its figure, {agent: (share, total)}) of the rows, each (agent, running total, wave)."""
    names = sorted({w for _, _, w in rows if w})
    ev = opened(*names) + [row(*r) for r in rows]
    out = {}
    for n in names:
        s = spend.wave_summary(ev, n)
        out[n] = (s["spent"], {a["agent"]: (a["tokens"], a["total"]) for a in s["agents"]})
    return out


class Rule(unittest.TestCase):
    """The rule, on rows in ledger order."""

    def test_continued_in_a_second_wave(self):
        self.assertEqual(spent(("a", 100, "w1"), ("a", 130, "w2")),
                         {"w1": (100, {"a": (100, 130)}), "w2": (30, {"a": (30, 130)})})

    def test_continued_three_times(self):
        self.assertEqual(spent(("a", 100, "w1"), ("a", 130, "w2"), ("a", 135, "w3"), ("a", 200, "w4")),
                         {"w1": (100, {"a": (100, 200)}), "w2": (30, {"a": (30, 200)}),
                          "w3": (5, {"a": (5, 200)}), "w4": (65, {"a": (65, 200)})})

    def test_two_agents_one_continued(self):
        self.assertEqual(spent(("a", 100, "w1"), ("b", 40, "w1"), ("a", 130, "w2"), ("c", 7, "w2")),
                         {"w1": (140, {"a": (100, 130), "b": (40, 40)}), "w2": (37, {"a": (30, 130), "c": (7, 7)})})

    def test_a_same_wave_correction_up_and_down(self):
        one = [("a", 100, "w1"), ("a", 130, "w2")]
        self.assertEqual(spent(*one, ("a", 150, "w2")), {"w1": (100, {"a": (100, 150)}), "w2": (50, {"a": (50, 150)})})
        self.assertEqual(spent(*one, ("a", 110, "w2")), {"w1": (100, {"a": (100, 110)}), "w2": (10, {"a": (10, 110)})})
        self.assertEqual(spent(("a", 100, "w1"), ("a", 90, "w1")), {"w1": (90, {"a": (90, 90)})})

    def test_a_wrong_wave_put_right(self):
        # the same tokens, or fewer, in another wave: the last segment was in the wrong wave
        self.assertEqual(spent(("a", 100, "w1"), ("a", 100, "w2")), {"w1": (0, {}), "w2": (100, {"a": (100, 100)})})
        self.assertEqual(spent(("a", 100, "w1"), ("a", 90, "w2")), {"w1": (0, {}), "w2": (90, {"a": (90, 90)})})
        self.assertEqual(spent(("a", 100, "w1"), ("a", 130, "w2"), ("a", 130, "w3")),
                         {"w1": (100, {"a": (100, 130)}), "w2": (0, {}), "w3": (30, {"a": (30, 130)})})
        self.assertEqual(spent(("a", 100, "w1"), ("a", 130, "w2"), ("a", 120, "w3")),
                         {"w1": (100, {"a": (100, 120)}), "w2": (0, {}), "w3": (20, {"a": (20, 120)})})
        # two rounds in one wave are one segment: both move
        self.assertEqual(spent(("a", 100, "w1"), ("a", 130, "w1"), ("a", 130, "w2")),
                         {"w1": (0, {}), "w2": (130, {"a": (130, 130)})})

    def test_fewer_tokens_than_the_earlier_segments_held(self):
        self.assertEqual(spent(("a", 100, "w1"), ("a", 130, "w2"), ("a", 80, "w2")),
                         {"w1": (80, {"a": (80, 80)}), "w2": (0, {})})
        self.assertEqual(spent(("a", 100, "w1"), ("a", 130, "w2"), ("a", 80, "w3")),
                         {"w1": (80, {"a": (80, 80)}), "w2": (0, {}), "w3": (0, {})})
        # through more than one segment, and up again
        three = [("a", 100, "w1"), ("a", 130, "w2"), ("a", 150, "w3"), ("a", 60, "w3")]
        self.assertEqual(spent(*three), {"w1": (60, {"a": (60, 60)}), "w2": (0, {}), "w3": (0, {})})
        self.assertEqual(spent(*three, ("a", 75, "w3")),
                         {"w1": (60, {"a": (60, 75)}), "w2": (0, {}), "w3": (15, {"a": (15, 75)})})
        # exactly what the earlier segments held: nothing is left in the last wave, so it lists no agent
        self.assertEqual(spent(("a", 100, "w1"), ("a", 130, "w2"), ("a", 100, "w2")),
                         {"w1": (100, {"a": (100, 100)}), "w2": (0, {})})

    def test_nothing_left_in_the_last_wave_is_still_its_last_segment(self):
        # so a correction to another wave moves that nothing, not what the wave before it holds
        self.assertEqual(spent(("a", 100, "w1"), ("a", 130, "w2"), ("a", 100, "w2"), ("a", 100, "w3")),
                         {"w1": (100, {"a": (100, 100)}), "w2": (0, {}), "w3": (0, {})})

    def test_the_same_row_again_changes_nothing(self):
        # also when it is a figure below what the earlier segments held
        for rows in ([("a", 100, "w1"), ("a", 130, "w2"), ("a", 80, "w2")], [("a", 100, "w1"), ("a", 130, "w2"), ("a", 80, "w3")],
                     [("a", 100, "w1"), ("a", 130, "w2"), ("a", 150, "w3"), ("a", 60, "w3")], [("a", 100, "w1"), ("a", 100, "w2")]):
            with self.subTest(rows=rows):
                self.assertEqual(spent(*rows, rows[-1]), spent(*rows))

    def test_a_row_with_no_wave_key_is_in_no_wave(self):
        old = row("a", 100, None)
        del old["wave"]
        self.assertEqual(spend.segments([old, row("a", 130, "w1")]), {"a": [[None, 100], ["w1", 30]]})

    def test_back_to_an_earlier_wave(self):
        self.assertEqual(spent(("a", 100, "w1"), ("a", 130, "w2"), ("a", 180, "w1")),
                         {"w1": (150, {"a": (150, 180)}), "w2": (30, {"a": (30, 180)})})
        # the last segment only is corrected
        self.assertEqual(spent(("a", 100, "w1"), ("a", 130, "w2"), ("a", 180, "w1"), ("a", 140, "w1")),
                         {"w1": (110, {"a": (110, 140)}), "w2": (30, {"a": (30, 140)})})

    def test_no_wave_at_all_then_a_wave(self):
        self.assertEqual(spent(("a", 100, None), ("a", 130, "w1")), {"w1": (30, {"a": (30, 130)})})
        self.assertEqual(spent(("a", 100, None), ("a", 100, "w1")), {"w1": (100, {"a": (100, 100)})})
        self.assertEqual(spent(("a", 100, "w1"), ("a", 130, None)), {"w1": (100, {"a": (100, 130)})})

    def test_the_latest_row_per_agent_is_unchanged(self):
        ev = [row("a", 100, "w1"), row("b", 40, "w1"), row("a", 130, "w2")]
        self.assertEqual([(r["agent"], r["tokens"], r["wave"]) for r in spend.agents(ev)],
                         [("a", 130, "w2"), ("b", 40, "w1")])

    def test_the_sum_over_all_waves_is_the_sum_of_the_latest_totals_on_200_ledgers(self):
        rnd = random.Random(141)
        names = ["w1", "w2", "w3", "w4"]
        for n in range(200):
            with self.subTest(ledger=n):
                total, rows = {}, []
                for _ in range(rnd.randint(1, 40)):
                    agent = rnd.choice("abcde")
                    was = total.get(agent, rnd.randint(1, 100))
                    total[agent] = max(1, rnd.choice([was, was + rnd.randint(1, 100), was - rnd.randint(1, 100),
                                                      rnd.randint(1, 300)]))
                    rows.append(row(agent, total[agent], rnd.choice([*names, None])))
                ev = opened(*names) + rows
                segs = spend.segments(ev)
                self.assertEqual({a: sum(k for _, k in s) for a, s in segs.items()}, total)
                self.assertTrue(all(k >= 0 for s in segs.values() for _, k in s), segs)
                none = sum(k for s in segs.values() for w, k in s if w is None)
                waves = [spend.wave_summary(ev, w) for w in names]
                self.assertEqual(sum(w["spent"] for w in waves) + none, sum(total.values()))
                for w in waves:
                    self.assertEqual(w["spent"], sum(a["tokens"] for a in w["agents"]))
                    self.assertTrue(all(0 < a["tokens"] <= a["total"] for a in w["agents"]))


class Commands(Scratch):
    """spend.sh in a scratch project (test_gate_events.Scratch), with a scratch HOME."""

    def setUp(self):
        super().setUp()
        (self.root / "home").mkdir()
        self.env["HOME"] = str(self.root / "home")
        self.tools = self.records / "TOOLS.md"

    def run_(self, *args, code=0, env=None):
        p = subprocess.run([str(self.rt / "spend.sh"), *args], cwd=self.work, capture_output=True, text=True,
                           env=dict(self.env, **(env or {})))
        self.assertEqual(p.returncode, code, p.stdout + p.stderr)
        return p

    def wave(self, name, ceiling):
        self.run_("wave", "open", name, "--ceiling", str(ceiling))

    def first(self, agent, tokens, wave=None, est=10000, **kw):
        return self.run_("record", "--agent", agent, "--label", f"job {agent}", "--tokens", str(tokens),
                         "--kind", "engineer", "--why", "w", "--predicted", "p", "--predicted-tokens", str(est),
                         "--result", "r", "--outcome", "as-predicted", *(["--wave", wave] if wave else []), **kw)

    def again(self, agent, tokens, wave=None, *extra, **kw):
        return self.run_("record", "--agent", agent, "--label", f"job {agent}", "--tokens", str(tokens), "--correct",
                         *(["--wave", wave] if wave else []), *extra, **kw)

    def est(self, n, outcome="as-predicted"):
        return ("--why", "w", "--predicted", "p", "--predicted-tokens", str(n), "--result", "r", "--outcome", outcome)

    def status(self):
        return {w["wave"]: w for w in json.loads(self.run_("status", "--all", "--json").stdout)["waves"]}

    def report(self, *extra):
        return json.loads(self.run_("report", "--json", *extra).stdout)

    def test_the_example_a_round_is_its_own_size_and_the_waves_before_it_keep_theirs(self):
        # three rounds of one agent, each wave's ceiling the size of its round; another agent beside it
        for name, ceiling in (("r1", 100000), ("r2", 50000), ("r3", 30000)):
            self.wave(name, ceiling)
        self.first("a", 90000, "r1")
        self.first("b", 8000, "r2")
        for p in (self.again("a", 120000, "r2"), self.again("a", 140000, "r3")):
            self.assertNotIn("STOP", p.stdout + p.stderr)
            self.assertNotIn("WARNING", p.stdout + p.stderr)
        self.assertEqual({n: (w["spent"], w["level"]) for n, w in self.status().items()},
                         {"r1": (90000, "warn"), "r2": (38000, "ok"), "r3": (20000, "ok")})
        self.assertNotIn("STOP", self.run_("status", "--all").stdout)

    def test_stop_and_warning_follow_the_waves_own_spend(self):
        self.wave("w1", 1000)
        self.wave("w2", 100)
        self.first("a", 500, "w1")
        p = self.again("a", 579, "w2")  # 79 of 100
        self.assertEqual(p.stderr, "")
        self.assertIn("79 / 100 (79%)", p.stdout)
        self.assertEqual(self.again("a", 580, "w2").stderr.strip(),
                         "WARNING: wave w2 has spent 80 of its 100 ceiling (80%); 80% reached.")
        self.assertEqual(self.again("a", 600, "w2").stderr.strip(),
                         "STOP: wave w2 has spent 100 of its 100 ceiling (100%).")
        self.assertEqual(self.status()["w1"]["spent"], 500)

    def test_the_wave_an_agent_left_keeps_its_stop(self):
        self.wave("w1", 100)
        self.wave("w2", 1000)
        self.first("a", 120, "w1")
        self.again("a", 130, "w2")
        self.assertEqual(self.status()["w1"]["level"], "over")

    def test_headroom_counts_the_waves_own_spend(self):
        self.wave("w1", 1000)
        self.wave("w2", 100)
        self.first("a", 900, "w1")
        self.again("a", 940, "w2")
        self.assertIn("40 spent + 60 estimated fits", self.run_("headroom", "w2", "--est", "60").stdout)
        self.assertIn("60 left", self.run_("headroom", "w2", "--est", "61", code=1).stderr)
        self.assertIn("900 spent", self.run_("headroom", "w1", "--est", "100").stdout)
        self.assertIn("by 1;", self.run_("headroom", "w1", "--est", "101", code=1).stderr)

    def test_a_closed_wave_keeps_its_figure_and_can_still_be_put_right(self):
        self.wave("w1", 1000)
        self.first("a", 100)
        self.assertIn("spent 100 of 1,000 (10%) across 1 agents", self.run_("wave", "close", "w1").stdout)
        self.wave("w2", 1000)
        self.again("a", 130)  # the one open wave
        st = self.status()
        self.assertEqual((st["w1"]["spent"], st["w1"]["open"], st["w2"]["spent"]), (100, False, 30))
        self.assertIn("spent 30 of 1,000 (3%) across 1 agents", self.run_("wave", "close", "w2").stdout)
        self.again("a", 110, "w2")
        self.assertEqual({n: w["spent"] for n, w in self.status().items()}, {"w1": 100, "w2": 10})

    def test_status_lists_a_share_and_a_total_per_agent(self):
        self.wave("w1", 1000)
        self.wave("w2", 1000)
        self.first("a", 100, "w1")
        self.first("b", 7, "w1")
        self.again("a", 130, "w2", "--source", "estimate")
        st = self.status()
        self.assertEqual([(a["agent"], a["tokens"], a["total"]) for a in st["w1"]["agents"]], [("a", 100, 130), ("b", 7, 7)])
        self.assertEqual([(a["agent"], a["tokens"], a["total"]) for a in st["w2"]["agents"]], [("a", 30, 130)])
        self.assertRegex(self.run_("status", "--wave", "w2").stdout, r"\n +30  engineer +a  job a  \[estimate")

    def test_report_by_wave_is_by_share_and_by_the_agents_since_keeps(self):
        old, new = ({"SPEND_TS": f"2026-01-0{d}T10:00:00+00:00"} for d in (1, 5))
        self.wave("w1", 1000)
        self.wave("w2", 1000)
        self.backfill("a", 100, "w1", old)
        self.backfill("b", 7, "w1", old)
        self.again("a", 130, "w2", "--note", "n", env=new)
        r = self.report()
        self.assertEqual((r["total"], r["wave"]), (137, {"w1": 107, "w2": 30}))
        self.assertEqual(r["day"], {"2026-01-01": 7, "2026-01-05": 130})
        self.assertEqual((r["kind"], r["source"]), ({"engineer": 137}, {"harness": 137}))
        r = self.report("--since", "2026-01-02")  # b goes; every wave a spent in stays
        self.assertEqual((r["total"], r["wave"]), (130, {"w1": 100, "w2": 30}))
        self.again("a", 100, "w2", "--note", "n", env=new)  # nothing left in w2: no line for it
        self.assertEqual(self.report()["wave"], {"w1": 107})
        self.again("a", 130, "w1", "--note", "n", env=new)
        self.assertEqual(self.report()["wave"], {"w1": 137})

    def backfill(self, agent, tokens, wave, env):
        return self.run_("record", "--agent", agent, "--label", f"job {agent}", "--tokens", str(tokens),
                         "--kind", "engineer", "--wave", wave, "--note", "backfilled", env=env)

    def test_report_names_the_tokens_in_no_wave(self):
        self.first("a", 100)
        self.wave("w1", 1000)
        self.again("a", 130)
        self.assertEqual(self.report()["wave"], {"(no wave)": 100, "w1": 30})

    def test_record_says_the_share_of_a_continued_agent_and_only_then(self):
        self.wave("w1", 100000)
        self.wave("w2", 100000)
        self.assertNotIn("tokens are in", self.first("a", 10000, "w1").stdout)
        self.assertNotIn("tokens are in", self.again("a", 11000, "w1").stdout)
        self.assertIn("\na: 4,000 of its 15,000 tokens are in this wave\n", self.again("a", 15000, "w2").stdout)
        self.assertIn("\na: 16,000 of its 20,000 tokens are in this wave\n", self.again("a", 20000, "w1").stdout)
        self.assertNotIn("tokens are in", self.first("b", 10000, "w1").stdout)
        self.run_("wave", "close", "w1")
        self.run_("wave", "close", "w2")
        self.assertIn("\na: 5,000 of its 25,000 tokens are in no wave\n", self.again("a", 25000).stdout)

    def test_record_says_nothing_of_a_share_that_is_the_whole(self):
        self.wave("w1", 100000)
        self.wave("w2", 100000)
        self.first("a", 10000, "w1")
        self.again("a", 15000, "w2")
        self.assertNotIn("tokens are in", self.again("a", 15000, "w1").stdout)  # all of it is in w1 again

    def test_record_says_what_another_wave_lost(self):
        for name in ("w1", "w2", "w3"):
            self.wave(name, 100000)
        self.first("a", 10000, "w1")
        line = "no longer count in"
        self.assertNotIn(line, self.again("a", 12000, "w1").stdout)
        self.assertNotIn(line, self.again("a", 15000, "w2").stdout)
        self.assertNotIn(line, self.again("a", 14000, "w2").stdout)  # the same wave
        self.assertIn("\na: 2,000 of its tokens no longer count in wave w2\n", self.again("a", 13000, "w3").stdout)
        p = self.again("a", 9000, "w3").stdout  # fewer than w1 held
        self.assertIn("\na: 3,000 of its tokens no longer count in wave w1\n", p)
        self.assertEqual(p.count(line), 1)
        self.first("d", 10000, "w1")
        self.again("d", 15000, "w2")
        self.again("d", 18000, "w1")  # w1 entered a second time: one line for it
        p = self.again("d", 18000, "w3").stdout
        self.assertIn("\nd: 3,000 of its tokens no longer count in wave w1\n", p)
        self.assertEqual(p.count(line), 1)
        self.first("b", 10000, "w1")  # its round's own count given in place of its total
        self.assertIn("\nb: 10,000 of its tokens no longer count in wave w1\n", self.again("b", 9000, "w2").stdout)
        for name in ("w1", "w2", "w3"):
            self.run_("wave", "close", name)
        self.first("c", 10000)
        self.run_("wave", "open", "w4", "--ceiling", "100000")
        self.assertIn("\nc: 10,000 of its tokens no longer count outside any wave\n",
                      self.again("c", 10000).stdout)

    def test_a_lesson_compares_the_waves_share_with_the_estimate(self):
        self.tools.write_text("# TOOLS\n")
        self.wave("w1", 1000000)
        self.wave("w2", 1000000)
        self.first("a", 100000, "w1", est=100000)
        self.again("a", 110000, "w2", *self.est(10000))  # the round is as estimated; the total is 11 times it
        self.again("a", 115000, "w2", *self.est(10000))  # off by half exactly: not owed
        self.assertEqual(self.tools.read_text(), "# TOOLS\n")
        self.again("a", 115001, "w2", *self.est(10000))  # off by more than half
        self.assertEqual(self.tools.read_text().count("**Lesson:** TODO"), 1)
        # the total is what the estimate says, the round is not
        self.first("b", 90000, "w1", est=90000)
        self.again("b", 100000, "w2", *self.est(100000))
        self.assertEqual(self.tools.read_text().count("**Lesson:** TODO"), 2)

    def test_a_correction_with_no_estimate_has_no_size_check(self):
        self.tools.write_text("# TOOLS\n")
        self.first("a", 10000)
        self.again("a", 90000, None, "--outcome", "as-predicted")
        self.assertEqual(self.tools.read_text(), "# TOOLS\n")

    def test_the_stub_prints_the_share_and_the_total_when_they_differ(self):
        self.tools.write_text("# TOOLS\n")
        self.wave("w1", 1000000)
        self.wave("w2", 1000000)
        self.first("a", 100000, "w1", est=10000)
        self.again("a", 130000, "w2", *self.est(30000, "worse"))
        self.assertEqual(re.findall(r"- \*\*Actual:\*\* (.*)", self.tools.read_text()),
                         ["r; 100,000 tokens (harness); outcome as-predicted.",
                          "r; 30,000 of its 130,000 tokens (harness); outcome worse."])
        self.assertEqual(re.findall(r"- \*\*Predicted:\*\* (.*)", self.tools.read_text()),
                         ["p; 10,000 tokens.", "p; 30,000 tokens."])

    def test_records_format_2_one_file_per_writer(self):
        self.wave("w1", 1000)
        self.wave("w2", 1000)
        self.first("a", 100, "w1")
        frozen = {rel: locator.file_sha(self.records / rel) for rel in locator.LEDGERS.values()}
        (self.records / "FORMAT").write_text(json.dumps({"format": 2, "bakehouse": "0.13.0", "frozen": frozen}))
        self.git("add", "-A", repo=self.records)
        self.git("commit", "-q", "-m", "format 2", repo=self.records)
        self.git("config", "bakehouse.writer", "alpha", repo=self.records)
        self.assertIn("30 of its 130 tokens are in this wave", self.again("a", 130, "w2").stdout)
        self.git("config", "bakehouse.writer", "beta", repo=self.records)
        self.assertIn("170 of its 200 tokens are in this wave", self.again("a", 200, "w1").stdout)
        files = sorted(p.name for p in (self.records / "spend" / "ledger").iterdir())
        self.assertEqual(files, ["alpha.jsonl", "beta.jsonl"])
        self.assertEqual({n: w["spent"] for n, w in self.status().items()}, {"w1": 170, "w2": 30})
        self.assertEqual(self.report()["wave"], {"w1": 170, "w2": 30})


if __name__ == "__main__":
    unittest.main()
