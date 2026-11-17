"""Tests for runtime/predict-gate.sh. Standard library only:
python3 -m unittest discover -s runtime/tests"""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
GATE = RUNTIME / "predict-gate.sh"
CLOSE_OVERRIDE = "PREDICT_GATE_CLOSE_OVERRIDE"
GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

BASE = {"premium:clean-risk": 1000.00, "premium:referral-risk": 2000.00, "gaps:unfaithful-logic": 9,
        "rules:fired": 120, "definition:file-hash": "aaa", "gaps:capture": 370}


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_gate(self, prediction, measured, baseline=BASE, *extra, env=None):
        (self.d / "moves.prediction.json").write_text(json.dumps(prediction))
        (self.d / "moves.baseline.json").write_text(json.dumps(baseline))
        (self.d / "moves.measured.json").write_text(json.dumps(measured))
        return subprocess.run([str(GATE), "check", "--prediction", str(self.d / "moves.prediction.json"),
                               "--measured", str(self.d / "moves.measured.json"), *extra],
                              capture_output=True, text=True, env=env)

    def after(self, **changes):
        return {**BASE, **{k.replace("__", ":").replace("_", "-"): v for k, v in changes.items()}}

    def test_predicted_and_happened_passes(self):
        p = self.run_gate({"moves": [{"what": "gaps:unfaithful-logic", "from": 9, "to": 11, "scope": "product-a"}],
                           "mustNotMove": ["premium:*"]}, self.after(gaps__unfaithful_logic=11))
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("1. Predicted, and it happened: 1.", p.stdout)
        self.assertIn("gaps:unfaithful-logic: 9 -> 11. Predicted: 9 -> 11. [scope: product-a]", p.stdout)
        self.assertIn("Result: PASS", p.stdout)

    def test_the_2026_10_10_case_unpredicted_move_refuses(self):
        p = self.run_gate({"moves": [], "mustNotMove": ["premium:*"]}, self.after(gaps__unfaithful_logic=11))
        self.assertEqual(p.returncode, 1)
        self.assertIn("4. Moved, and nobody predicted it: 1. Each one stops the commit.", p.stdout)
        self.assertIn("gaps:unfaithful-logic: 9 -> 11.", p.stdout)
        self.assertIn("Result: REFUSED", p.stdout)

    def test_predicted_but_did_not_happen_is_reported_not_refused(self):
        p = self.run_gate({"moves": [{"what": "rules:fired", "delta": 2}, {"what": "rules:nope", "to": 1}]}, BASE)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("2. Predicted, and it did not happen (reported; does not stop): 2.", p.stdout)
        self.assertIn("rules:fired: 120 -> 120. Predicted: change of +2. Not met: it did not move.", p.stdout)
        self.assertIn("Not met: no measured key matches it.", p.stdout)

    def test_s1_a_no_moves_prediction_whose_pair_moves_refuses_like_an_unpredicted_move(self):
        p = self.run_gate({"noMoves": True}, self.after(rules__fired=121))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("4. Moved, and nobody predicted it: 1. Each one stops the commit.", p.stdout)

    def test_s1_no_moves_with_a_listed_move_or_not_true_or_false_is_bad_input(self):
        for pred in ({"noMoves": True, "moves": [{"what": "rules:fired"}]}, {"noMoves": "yes"}):
            p = self.run_gate(pred, BASE)
            self.assertEqual(p.returncode, 2, (pred, p.stdout, p.stderr))
            self.assertIn("noMoves", p.stderr)

    def test_moved_to_a_different_value_refuses(self):
        p = self.run_gate({"moves": [{"what": "rules:fired", "delta": 2}]}, self.after(rules__fired=125))
        self.assertEqual(p.returncode, 1)
        self.assertIn("rules:fired: 120 -> 125. Predicted: change of +2. Not met: it changed by +5.", p.stdout)

    def test_wrong_from_refuses(self):
        p = self.run_gate({"moves": [{"what": "gaps:unfaithful-logic", "from": 8, "to": 11}]},
                          self.after(gaps__unfaithful_logic=11))
        self.assertEqual(p.returncode, 1)
        self.assertIn("baseline was 9, the prediction said 8", p.stdout)

    def test_range_and_any_value(self):
        pred = {"moves": [{"what": "gaps:capture", "range": [360, 375]}, {"what": "definition:file-hash"}]}
        ok = self.run_gate(pred, self.after(gaps__capture=365, definition__file_hash="bbb"))
        self.assertEqual(ok.returncode, 0, ok.stdout)
        bad = self.run_gate(pred, self.after(gaps__capture=380, definition__file_hash="bbb"))
        self.assertEqual(bad.returncode, 1)
        self.assertIn("gaps:capture: 370 -> 380. Predicted: after in [360, 375]. Not met: it reached 380.", bad.stdout)

    def test_must_not_move_breach_refuses(self):
        p = self.run_gate({"moves": [], "mustNotMove": ["premium:clean-risk"]}, self.after(premium__clean_risk=1900.0))
        self.assertEqual(p.returncode, 1)
        self.assertIn("5. Must not move, and it moved: 1.", p.stdout)
        self.assertNotIn("4. Moved, and nobody predicted it: 1", p.stdout)

    def test_must_not_move_not_measured_refuses(self):
        p = self.run_gate({"mustNotMove": ["quotes:*"]}, BASE)
        self.assertEqual(p.returncode, 1)
        self.assertIn("6. Must not move, and it was not measured, so it is unproven: 1.", p.stdout)

    def test_a_named_move_beats_a_must_not_move_glob(self):
        p = self.run_gate({"moves": [{"what": "premium:clean-risk", "to": 1900.0}], "mustNotMove": ["premium:*"]},
                          self.after(premium__clean_risk=1900.0))
        self.assertEqual(p.returncode, 0, p.stdout)
        p = self.run_gate({"moves": [{"what": "premium:clean-risk", "to": 1900.0}], "mustNotMove": ["premium:*"]},
                          self.after(premium__clean_risk=1900.0, premium__referral_risk=1.0))
        self.assertEqual(p.returncode, 1)
        self.assertIn("premium:referral-risk: 2000.0 -> 1.0 (rule: premium:*).", p.stdout)

    def test_tolerance(self):
        pred = {"moves": [], "tolerance": 0.005}
        self.assertEqual(self.run_gate(pred, self.after(premium__clean_risk=1000.004)).returncode, 0)
        self.assertEqual(self.run_gate(pred, self.after(premium__clean_risk=1000.01)).returncode, 1)

    def test_b6_a_tolerance_of_true_or_infinity_or_nan_is_bad_input(self):
        for tol in (True, False, float("inf"), float("nan"), -1, "0.1"):
            with self.subTest(tolerance=tol):
                p = self.run_gate({"moves": [], "tolerance": tol}, self.after(premium__clean_risk=1900.0))
                self.assertEqual(p.returncode, 2, (p.stdout, p.stderr))
                self.assertIn("'tolerance' must be a finite number >= 0", p.stderr)
        for tol in (0, 0.5, 2):
            with self.subTest(tolerance=tol):
                self.assertEqual(self.run_gate({"moves": [], "tolerance": tol}, BASE).returncode, 0)

    def test_b6_true_and_1_are_different_values(self):
        base = {**BASE, "flag:on": 1}
        p = self.run_gate({"moves": []}, {**base, "flag:on": True}, base)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("flag:on: 1 -> true.", p.stdout)
        p = self.run_gate({"moves": [{"what": "flag:on", "from": 1, "to": True}]}, {**base, "flag:on": True}, base)
        self.assertEqual(p.returncode, 0, p.stdout)
        p = self.run_gate({"moves": [{"what": "flag:on", "to": 1}]}, {**base, "flag:on": True}, base)
        self.assertEqual(p.returncode, 1, p.stdout)
        # the same value, and 1 against 1.0, still do not move
        self.assertEqual(self.run_gate({"moves": []}, base, base).returncode, 0)
        self.assertEqual(self.run_gate({"moves": []}, {**base, "flag:on": 1.0}, base).returncode, 0)

    def test_key_appearing_or_disappearing_is_a_move(self):
        gone = {k: v for k, v in BASE.items() if k != "rules:fired"}
        p = self.run_gate({}, {**gone, "rules:new": 1})
        self.assertEqual(p.returncode, 1)
        self.assertIn("rules:fired: 120 -> (absent).", p.stdout)
        self.assertIn("rules:new: (absent) -> 1.", p.stdout)

    def test_bad_input_exits_2(self):
        cases = [
            ({"moves": [{"what": "x", "to": 1, "delta": 1}]}, BASE),
            ({"moves": [{"what": "x", "from": 1}]}, BASE),
            ({"moves": [{"what": "x"}], "mustNotMove": ["x"]}, BASE),
            ({"moves": [], "extra": 1}, BASE),
            ({}, {"nested": {"a": 1}}),
        ]
        for pred, meas in cases:
            p = self.run_gate(pred, meas)
            self.assertEqual(p.returncode, 2, (pred, p.stdout, p.stderr))
            self.assertIn("bad input", p.stderr)

    def test_yaml_named_prediction_written_as_json_reads_without_pyyaml(self):
        (self.d / "moves.prediction.yaml").write_text(json.dumps({"moves": [{"what": "rules:fired", "to": 121}]}))
        (self.d / "moves.baseline.json").write_text(json.dumps(BASE))
        (self.d / "moves.measured.json").write_text(json.dumps(self.after(rules__fired=121)))
        p = subprocess.run([str(GATE), "check", "--prediction", str(self.d / "moves.prediction.yaml"),
                            "--measured", str(self.d / "moves.measured.json")], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_override_without_ledger_stays_refused(self):
        env = dict(os.environ)
        p = self.run_gate({}, self.after(rules__fired=121), BASE, "--override", "owner said so", env=env)
        self.assertEqual(p.returncode, 1)
        self.assertIn("OVERRIDE REFUSED", p.stdout)


class RepoFixture(unittest.TestCase):
    """An owned repo with a team (.team/binding.json and a records worktree), so ops-event.sh can write."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name).resolve()
        self.repo = t / "repo"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.env = dict(os.environ, **GIT)
        self.env.pop("PREDICT_GATE_OVERRIDE", None)
        self.env.pop("PREDICT_GATE_CLOSE_OVERRIDE", None)
        self.env.pop("CLAUDE_PROJECT_DIR", None)
        self.git("commit", "-q", "--allow-empty", "-m", "first")
        self.binding = self.repo / ".team" / "binding.json"
        self.binding.parent.mkdir()
        self.binding.write_text(json.dumps({"owned": True}))
        self.records = t / "repo.team-records"
        self.git("worktree", "add", "-q", "--orphan", "-b", "bakehouse-records", str(self.records))
        self.git("config", "bakehouse.records", str(self.records))
        self.ledger = self.records / "ops/events.jsonl"
        self.run_dir = self.repo / "runs" / "x"
        self.run_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, env=None, check=True):
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True,
                              env=env or self.env, check=check)

    def write(self, prediction, measured, baseline=BASE):
        for name, data in (("moves.prediction.json", prediction), ("moves.baseline.json", baseline),
                           ("moves.measured.json", measured)):
            (self.run_dir / name).write_text(json.dumps(data))

    def gate(self, *args, env=None):
        return subprocess.run([str(GATE), *args], capture_output=True,
                              text=True, env=env or self.env)

    def install_hook(self):
        """The gate's own pre-commit line; how `bakehouse setup` installs it is tested in test_setup."""
        hook = self.repo / ".git/hooks/pre-commit"
        hook.write_text(f'#!/bin/sh\nexec "{GATE}" hook\n')
        hook.chmod(0o755)

    def events(self):
        return [json.loads(x) for x in self.ledger.read_text().splitlines()] if self.ledger.exists() else []


class Ledger(RepoFixture):
    """The override and the hook, end to end."""

    def test_override_is_recorded_in_the_ops_ledger(self):
        self.write({}, {**BASE, "rules:fired": 121})
        p = self.gate("check", "--prediction", str(self.run_dir / "moves.prediction.json"),
                      "--measured", str(self.run_dir / "moves.measured.json"),
                      "--override", "owner go 13:49: the +1 is the T1 fix", "--task", "T1")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("OVERRIDDEN by the owner", p.stdout)
        ev = json.loads(self.ledger.read_text().splitlines()[-1])
        self.assertEqual((ev["kind"], ev["task"], ev["outcome"], ev["count"]), ("gate-refused", "T1", "done", 1))
        self.assertIn("owner go 13:49", ev["note"])
        self.assertIn("rules:fired", ev["note"])

    def test_override_not_needed_is_not_recorded(self):
        self.write({}, BASE)
        p = self.gate("check", "--prediction", str(self.run_dir / "moves.prediction.json"),
                      "--measured", str(self.run_dir / "moves.measured.json"), "--override", "x")
        self.assertEqual(p.returncode, 0)
        self.assertFalse(self.ledger.exists())

    def test_hook_and_commit(self):
        self.install_hook()
        # 1. unpredicted move: refused
        self.write({"moves": []}, {**BASE, "gaps:unfaithful-logic": 11})
        self.git("add", "-A")
        p = self.git("commit", "-m", "x", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("4. Moved, and nobody predicted it: 1.", p.stdout + p.stderr)
        # 2. the owner's override: committed, and recorded
        env = dict(self.env, PREDICT_GATE_OVERRIDE="owner accepted 9 -> 11", PREDICT_GATE_TASK="T1")
        p = self.git("commit", "-m", "x", env=env, check=False)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("owner accepted 9 -> 11", self.ledger.read_text())
        # 3. predicted: committed without override
        self.write({"moves": [{"what": "gaps:unfaithful-logic", "from": 11, "to": 12}]},
                   {**BASE, "gaps:unfaithful-logic": 12}, {**BASE, "gaps:unfaithful-logic": 11})
        self.git("add", "-A")
        p = self.git("commit", "-m", "y", check=False)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        # 4. the staged copy is what is checked, not the working tree
        self.write({"moves": []}, {**BASE, "gaps:unfaithful-logic": 13}, {**BASE, "gaps:unfaithful-logic": 12})
        self.git("add", "-A")
        self.write({"moves": [{"what": "gaps:unfaithful-logic"}]}, {**BASE, "gaps:unfaithful-logic": 13},
                   {**BASE, "gaps:unfaithful-logic": 12})
        self.assertNotEqual(self.git("commit", "-m", "z", check=False).returncode, 0)

    def test_d4_the_git_hooks_refusal_still_names_git_commit(self):
        self.install_hook()
        self.write({"moves": []}, {**BASE, "gaps:unfaithful-logic": 11})
        self.git("add", "-A")
        p = self.git("commit", "-m", "x", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('Owner-approved exception only: PREDICT_GATE_OVERRIDE="<reason>" [PREDICT_GATE_TASK=<id>] '
                      "git commit ...", p.stdout + p.stderr)
        self.assertNotIn("bakehouse record commit", p.stdout + p.stderr)

    def test_hook_ignores_commits_without_a_measured_file(self):
        self.install_hook()
        (self.run_dir / "moves.prediction.json").write_text(json.dumps({"moves": []}))
        (self.run_dir / "measured.json").write_text(json.dumps({"some": {"nested": 1}}))
        self.git("add", "-A")
        self.assertEqual(self.git("commit", "-m", "prediction first", check=False).returncode, 0)

    def test_hook_refuses_measured_without_prediction_or_baseline(self):
        self.install_hook()
        (self.run_dir / "moves.measured.json").write_text(json.dumps(BASE))
        self.git("add", "-A")
        p = self.git("commit", "-m", "x", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("moves.prediction.json, moves.baseline.json is not in the index", p.stdout + p.stderr)


class Close(RepoFixture):
    """`close <work-order>`: the record folder through the binding, every round checked, one event."""

    def setUp(self):
        super().setUp()
        self.binding.write_text(json.dumps({
            "owned": True, "reviews": "runs/", "reviewName": "{date}-{id}-step{n}-review",
            "tracker": {"file": "docs/TRACKER.md", "id": "^T[0-9]+$"}}))
        self.folder = self.repo / "runs" / "2026-01-02-T7-step1-review"
        self.folder.mkdir(parents=True)

    def put(self, name, data):
        (self.folder / name).write_text(json.dumps(data))

    def trio(self, rnd="", prediction=None, measured=None, baseline=BASE):
        self.put(f"moves.prediction{rnd}.json", prediction if prediction is not None
                 else {"moves": [{"what": "rules:fired", "from": 120, "to": 121}]})
        self.put(f"moves.measured{rnd}.json", measured if measured is not None else {**BASE, "rules:fired": 121})
        if baseline is not None:
            self.put(f"moves.baseline{rnd}.json", baseline)

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "record")

    def close(self, wo="T7", env=None, commit=True):
        if commit:
            self.commit()
        return subprocess.run([str(GATE), "close", wo, "--repo", str(self.repo)],
                              capture_output=True, text=True, env=env or self.env)

    def sha(self, name):
        return hashlib.sha256((self.folder / name).read_bytes()).hexdigest()

    def test_close_passes_and_records_each_files_sha256(self):
        self.trio()
        p = self.close()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Result: CLOSED", p.stdout)
        ev = self.events()[-1]
        self.assertEqual((ev["kind"], ev["task"], ev["outcome"], ev["count"]), ("work-order-closed", "T7", "done", 1))
        self.assertIn(f"moves.prediction.json sha256:{self.sha('moves.prediction.json')}", ev["note"])
        self.assertIn(f"moves.measured.json sha256:{self.sha('moves.measured.json')}", ev["note"])

    # ---- R0.1 S1, the owner's rule Q3 option C: a predicted move that did not happen is explained ----

    MISSED_P = {"moves": [{"what": "rules:fired", "from": 120, "to": 121},
                          {"what": "gaps:capture", "range": [370, 370]},
                          {"what": "rules:new", "to": 1}]}

    def test_s1_q3_close_refuses_a_missed_move_with_no_reason(self):
        self.trio(prediction=self.MISSED_P)
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        # check still reports the section and does not stop
        self.assertIn("2. Predicted, and it did not happen (reported; does not stop): 2.", p.stdout)
        self.assertIn("Result: PASS", p.stdout)
        # a predicted new key that never appears is a missed move too
        for key in ("gaps:capture", "rules:new"):
            self.assertIn(f"runs/2026-01-02-T7-step1-review/moves.missed.json: no reason for {key}", p.stdout)
        self.assertIn("not in git at HEAD", p.stdout)

    def test_s1_q3_each_bad_reason_file_refuses_naming_the_file_and_the_key(self):
        self.trio(prediction=self.MISSED_P)
        ok = {"gaps:capture": "held value, written as a move by mistake", "rules:new": "the key is not emitted yet"}
        for missed, why in (
            ({"gaps:capture": ok["gaps:capture"]}, "moves.missed.json: no reason for rules:new"),
            ({**ok, "rules:new": "  "}, "moves.missed.json: the reason for rules:new is empty"),
            ({**ok, "rules:new": "one\ntwo"}, "moves.missed.json: the reason for rules:new is not one line"),
            ({**ok, "rules:fired": "x"}, "moves.missed.json: rules:fired was not missed in this round"),
            (["gaps:capture"], "moves.missed.json is not one JSON object of key -> reason"),
        ):
            with self.subTest(missed=missed):
                self.put("moves.missed.json", missed)
                p = self.close()
                self.assertEqual(p.returncode, 1, p.stdout)
                self.assertIn(why, p.stdout)
        self.put("moves.missed.json", ok)
        p = self.close()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_s1_q3_a_fix_rounds_reasons_sit_in_its_own_file(self):
        self.trio()
        self.trio(rnd=".fix1", prediction=self.MISSED_P)
        self.put("moves.missed.json", {"gaps:capture": "x", "rules:new": "y"})
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("moves.missed.fix1.json: no reason for gaps:capture", p.stdout)
        self.assertIn("moves.missed.json: gaps:capture was not missed in this round", p.stdout)

    def test_close_refuses_when_a_check_refuses(self):
        self.trio(measured={**BASE, "rules:fired": 121, "gaps:capture": 371})
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("4. Moved, and nobody predicted it: 1.", p.stdout)
        self.assertIn("2026-01-02-T7-step1-review/moves.prediction.json: check exit 1", p.stdout)
        self.assertIn("Result: REFUSED", p.stdout)
        # a refused close writes one gate-refused event, and nothing else
        self.assertEqual([(e["kind"], e.get("gate")) for e in self.events()],
                         [("gate-refused", "predict-gate:close")])

    def test_close_refuses_bad_input_in_a_pair(self):
        self.trio(prediction={"moves": [{"what": "x", "from": 1}]})
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("moves.prediction.json: check exit 2", p.stdout)
        # a refused close writes one gate-refused event, and nothing else
        self.assertEqual([(e["kind"], e.get("gate")) for e in self.events()],
                         [("gate-refused", "predict-gate:close")])

    def test_close_refuses_a_prose_only_folder(self):
        (self.folder / "prediction.md").write_text("Every move, in prose.\n")
        self.put("moves.measured.json", BASE)
        self.put("moves.baseline.json", BASE)
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("no moves.prediction*.json. A prose prediction is not checked by a machine.", p.stdout)
        # a refused close writes one gate-refused event, and nothing else
        self.assertEqual([(e["kind"], e.get("gate")) for e in self.events()],
                         [("gate-refused", "predict-gate:close")])

    def test_close_refuses_a_prediction_with_no_measured_file(self):
        self.put("moves.prediction.json", {"moves": []})
        self.put("moves.baseline.json", BASE)
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("2026-01-02-T7-step1-review/moves.measured.json not found: the prediction "
                      "moves.prediction.json has no measurement", p.stdout)

    def test_close_refuses_when_no_folder_matches(self):
        p = self.close("T8")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("no record folder matches runs/*-T8-step1-review", p.stdout)

    def test_close_checks_the_id_against_the_tracker_form(self):
        p = self.close("X7")
        self.assertEqual(p.returncode, 2)
        self.assertIn("does not match the binding's tracker id form", p.stderr)

    def test_close_checks_every_round_with_its_own_files(self):
        self.trio()
        fix = {**BASE, "rules:fired": 121}
        self.trio(".fix3", prediction={"moves": [{"what": "rules:fired", "from": 121, "to": 122}]},
                  measured={**BASE, "rules:fired": 123}, baseline=fix)
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("baseline:   " + str(self.folder / "moves.baseline.fix3.json"), p.stdout)
        self.assertIn("moves.prediction.fix3.json: check exit 1", p.stdout)
        self.put("moves.measured.fix3.json", {**BASE, "rules:fired": 122})
        p = self.close()
        self.assertEqual(p.returncode, 0, p.stdout)
        note = self.events()[-1]["note"]
        self.assertIn(f"moves.prediction.fix3.json sha256:{self.sha('moves.prediction.fix3.json')}", note)
        self.assertIn(f"moves.measured.fix3.json sha256:{self.sha('moves.measured.fix3.json')}", note)
        self.assertEqual(self.events()[-1]["count"], 2)

    def test_close_names_a_rounds_missing_measured_file_and_refuses_an_unpaired_measurement(self):
        self.trio()
        self.put("moves.prediction.fix3.json", {"moves": []})
        self.put("moves.measured.proto.json", BASE)
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("moves.measured.fix3.json not found: the prediction moves.prediction.fix3.json", p.stdout)
        self.assertIn("moves.measured.proto.json: a measured file with no prediction of its round", p.stdout)

    def test_close_override_is_recorded_then_closes(self):
        self.trio(measured={**BASE, "rules:fired": 125})
        covers = ",".join(self.ids(self.close().stdout))  # seal F5: the override names what it covers
        env = dict(self.env, PREDICT_GATE_CLOSE_OVERRIDE=f"owner go: the +5 is accepted | covers: {covers}")
        p = self.close(env=env)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("OVERRIDDEN by the owner", p.stdout)
        refused, closed = self.events()[-2:]
        self.assertEqual((refused["kind"], refused["task"], refused["outcome"]), ("gate-refused", "T7", "done"))
        self.assertIn("owner go: the +5 is accepted", refused["note"])
        self.assertEqual(closed["kind"], "work-order-closed")
        self.assertIn("OVERRIDDEN", closed["note"])

    # ---- seal fixes F5: an override names each refusal it covers ----

    def four_refusals(self):
        """A record close refuses 4 times: an unpredicted move, a round with no measurement, and two
        measured files with no prediction of their round. Returns close's output."""
        self.trio(measured={**BASE, "rules:fired": 121, "gaps:capture": 371})
        self.put("moves.prediction.fix3.json", {"moves": [{"what": "rules:fired", "to": 1}]})
        self.put("moves.measured.proto.json", BASE)
        self.put("moves.measured.proto2.json", BASE)
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        return p.stdout

    def ids(self, out):
        return re.findall(r"^  - (R-[0-9a-f]{6}): ", out, re.M)

    def test_seal_f5_close_names_each_refusal_with_a_stable_id(self):
        out = self.four_refusals()
        ids = self.ids(out)
        self.assertEqual(len(ids), 4, out)
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual(self.ids(self.close().stdout), ids, "the same record gives the same ids")
        self.assertIn(f'{CLOSE_OVERRIDE}="<reason> | covers: <the ids of the refusals you accept>"', out)

    def test_seal_f5_a_refused_close_records_every_refusal_with_its_id(self):
        ids = self.ids(self.four_refusals())
        ev = [e for e in self.events() if e.get("purpose") == "work order close refused"][-1]
        self.assertEqual((ev["kind"], ev["count"]), ("gate-refused", 4))
        for i in ids:
            self.assertIn(i, ev["note"])
        self.assertIn("moves.measured.proto2.json: a measured file with no prediction", ev["note"])

    def test_seal_f5_an_override_that_names_no_refusal_is_refused(self):
        ids = self.ids(self.four_refusals())
        p = self.close(env=dict(self.env, PREDICT_GATE_CLOSE_OVERRIDE="owner go"))
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("OVERRIDE REFUSED", p.stdout)
        for i in ids:
            self.assertIn(f"  - {i}: ", p.stdout, "the ids are in the refusal list")
        self.assertNotIn("work-order-closed", [e["kind"] for e in self.events()])

    def test_seal_f5_an_override_that_leaves_a_refusal_uncovered_is_refused_and_names_it(self):
        ids = self.ids(self.four_refusals())
        env = dict(self.env, PREDICT_GATE_CLOSE_OVERRIDE=f"owner go | covers: {','.join(ids[:3])}")
        p = self.close(env=env)
        self.assertEqual(p.returncode, 1, p.stdout)
        tail = p.stdout.split("OVERRIDE REFUSED")[1].split("\n")[0]
        uncovered = tail.split("does not cover")[1].split("It covers only")[0]
        self.assertIn(ids[3], uncovered)
        self.assertNotIn(ids[0], uncovered)
        self.assertNotIn("work-order-closed", [e["kind"] for e in self.events()])

    def test_seal_f5_an_override_covering_every_refusal_closes_and_records_every_one(self):
        out = self.four_refusals()
        ids = self.ids(out)
        env = dict(self.env, PREDICT_GATE_CLOSE_OVERRIDE=f"owner go, decision 7 | covers: {', '.join(reversed(ids))}")
        p = self.close(env=env)
        self.assertEqual(p.returncode, 0, p.stdout)
        refused, closed = self.events()[-2:]
        self.assertEqual((refused["kind"], refused["purpose"], refused["count"]),
                         ("gate-refused", "work order close overridden", 4))
        self.assertIn("owner go, decision 7", refused["note"])
        for i in ids:
            self.assertIn(i, refused["note"])
        self.assertIn("moves.measured.proto2.json: a measured file with no prediction", refused["note"])
        self.assertEqual(closed["kind"], "work-order-closed")
        self.assertTrue(closed["note"].startswith("OVERRIDDEN"))

    # ---- seal fix2 (review findings) ----

    def refused_override(self, text):
        p = self.close(env=dict(self.env, PREDICT_GATE_CLOSE_OVERRIDE=text))
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertNotIn("work-order-closed", [e["kind"] for e in self.events()])
        return p.stdout.split("OVERRIDE REFUSED: ")[1].split("\n")[0]

    def test_seal_fix2_a_refused_override_prints_no_ready_made_covers_list(self):
        ids = self.ids(self.four_refusals())
        line = self.refused_override("owner go")
        self.assertIn('"<reason> | covers: <the ids of the refusals you accept>"', line)
        for i in ids:
            self.assertNotIn(i, line)

    def test_seal_fix2_a_junk_word_or_an_empty_reason_refuses_the_override(self):
        ids = self.ids(self.four_refusals())
        self.assertIn("junk is not a refusal id", self.refused_override(f"owner go | covers: {','.join(ids)},junk"))
        self.assertIn("gives no reason", self.refused_override(f" | covers: {','.join(ids)}"))

    def test_seal_fix2_the_override_event_lists_the_fourth_refusal_in_its_refused_part(self):
        ids = self.ids(self.four_refusals())
        p = self.close(env=dict(self.env, PREDICT_GATE_CLOSE_OVERRIDE=f"owner go | covers: {','.join(ids)}"))
        self.assertEqual(p.returncode, 0, p.stdout)
        refused_part = self.events()[-2]["note"].split("| refused: ")[1]
        for i in ids:
            self.assertIn(f"{i} ", refused_part)

    def test_close_without_a_ledger_is_refused(self):
        self.trio()
        binding = self.binding
        other = Path(self.tmp.name) / "b.json"
        other.write_text(binding.read_text())
        binding.unlink()  # the repository has no team, so no ledger
        self.commit()
        p = subprocess.run([str(GATE), "close", "T7", "--repo", str(self.repo),
                            "--binding", str(other)], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("The close could not be recorded in the ops ledger", p.stdout)
        self.put("moves.measured.json", {**BASE, "rules:fired": 125})
        self.commit()
        p = subprocess.run([str(GATE), "close", "T7", "--repo", str(self.repo),
                            "--binding", str(other)], capture_output=True, text=True, env=self.env)
        covers = ",".join(self.ids(p.stdout))  # seal F5: the override names what it covers
        p = subprocess.run([str(GATE), "close", "T7", "--repo", str(self.repo),
                            "--binding", str(other)], capture_output=True, text=True,
                           env=dict(self.env, PREDICT_GATE_CLOSE_OVERRIDE=f"x | covers: {covers}"))
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("OVERRIDE REFUSED", p.stdout)
        self.assertFalse(self.ledger.exists())


class Friction(RepoFixture):
    """The gate's own input: bracket keys, and a fix round's file names."""

    def check(self, *args):
        return self.gate("check", *args)

    def test_bracket_key_is_read_literally(self):
        base = {"verdict:== [1]": 1, "verdict:== 1": 5}
        self.write({"moves": [{"what": "verdict:== [1]", "to": 2}], "mustNotMove": ["verdict:== 1"]},
                   {**base, "verdict:== [1]": 2}, base)
        p = self.check("--measured", str(self.run_dir / "moves.measured.json"))
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("1. Predicted, and it happened: 1.", p.stdout)

    def test_bracket_key_in_must_not_move_is_literal(self):
        base = {"k[ab]": 1, "ka": 1}
        self.write({"mustNotMove": ["k[ab]"]}, {**base, "ka": 2}, base)
        p = self.check("--measured", str(self.run_dir / "moves.measured.json"))
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("4. Moved, and nobody predicted it: 1.", p.stdout)
        self.assertIn("5. Must not move, and it moved: 0.", p.stdout)

    def test_bracket_with_wildcards_and_the_old_escapes(self):
        base = {"v:[x]-1": 1, "v:x-1": 1, "v:== [true]": 1, "v:== []": 1}
        self.write({"moves": [{"what": "v:[x]*", "to": 2}, {"what": "v:== [[]true[]]", "to": 2},
                              {"what": "v:== [[][]]", "to": 2}], "mustNotMove": ["v:x-?"]},
                   {**base, "v:[x]-1": 2, "v:== [true]": 2, "v:== []": 2}, base)
        p = self.check("--measured", str(self.run_dir / "moves.measured.json"))
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("1. Predicted, and it happened: 3.", p.stdout)

    def test_check_finds_a_rounds_prediction_and_baseline(self):
        self.write({}, BASE)
        (self.run_dir / "moves.prediction.fix3.json").write_text(json.dumps({"moves": [{"what": "rules:fired", "to": 122}]}))
        (self.run_dir / "moves.measured.fix3.json").write_text(json.dumps({**BASE, "rules:fired": 122}))
        p = self.check("--measured", str(self.run_dir / "moves.measured.fix3.json"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("prediction: " + str(self.run_dir / "moves.prediction.fix3.json"), p.stdout)
        self.assertIn("baseline:   " + str(self.run_dir / "moves.baseline.json"), p.stdout)
        (self.run_dir / "moves.baseline-fix3.json").write_text(json.dumps({**BASE, "rules:fired": 121}))
        (self.run_dir / "moves.prediction-fix3.json").write_text(json.dumps({"moves": [{"what": "rules:fired", "from": 121, "to": 122}]}))
        (self.run_dir / "moves.measured-fix3.json").write_text(json.dumps({**BASE, "rules:fired": 122}))
        p = self.check("--measured", str(self.run_dir / "moves.measured-fix3.json"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("baseline:   " + str(self.run_dir / "moves.baseline-fix3.json"), p.stdout)

    def test_check_without_a_rounds_prediction_says_so(self):
        (self.run_dir / "moves.measured.fix9.json").write_text(json.dumps(BASE))
        p = self.check("--measured", str(self.run_dir / "moves.measured.fix9.json"))
        self.assertEqual(p.returncode, 2)
        self.assertIn("no moves.prediction.fix9.json beside", p.stderr)

    def test_hook_checks_a_fix_round_and_refuses_an_unpaired_measurement(self):
        self.install_hook()
        self.write({"moves": []}, BASE)
        (self.run_dir / "moves.prediction.fix1.json").write_text(json.dumps({"moves": []}))
        (self.run_dir / "moves.measured.fix1.json").write_text(json.dumps({**BASE, "rules:fired": 130}))
        (self.run_dir / "moves.measured.proto.json").write_text(json.dumps({**BASE, "rules:fired": 999}))
        self.git("add", "-A")
        p = self.git("commit", "-m", "x", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("(round .fix1)", p.stdout + p.stderr)
        self.assertIn("rules:fired: 120 -> 130.", p.stdout + p.stderr)
        self.assertNotIn("999", p.stdout + p.stderr)
        self.assertIn("moves.measured.proto.json is staged, and no moves.prediction.proto.json", p.stdout + p.stderr)
        (self.run_dir / "moves.measured.fix1.json").write_text(json.dumps(BASE))
        (self.run_dir / "history").mkdir()
        (self.run_dir / "moves.measured.proto.json").rename(self.run_dir / "history" / "moves.measured.proto.json")
        self.git("add", "-A")
        p = self.git("commit", "-m", "x", check=False)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


class CloseRecord(Close):
    """Fix round 1: close reads the record from git, and every top-level measured file is in a pair."""

    def test_an_unpaired_measured_file_with_an_unpredicted_move_refuses(self):
        self.trio()
        self.put("moves.measured.fix1.json", {**BASE, "rules:fired": 121, "gaps:capture": 999})
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("moves.measured.fix1.json: a measured file with no prediction of its round. If it is not "
                      "part of the closed record, move it into history/ (git mv) and commit.", p.stdout)
        # a refused close writes one gate-refused event, and nothing else
        self.assertEqual([(e["kind"], e.get("gate")) for e in self.events()],
                         [("gate-refused", "predict-gate:close")])

    def test_history_is_listed_in_the_output_and_the_event_and_not_checked(self):
        (self.folder / "history").mkdir()
        (self.folder / "history" / "moves.measured.proto.json").write_text(json.dumps({"x": 999}))
        (self.folder / "history" / "moves.prediction.v1.json").write_text("not even json")
        self.commit()  # before the record's first measurement: no approval needed
        self.trio()
        p = self.close()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("history/ (listed, not checked): moves.measured.proto.json, moves.prediction.v1.json", p.stdout)
        self.assertIn("history/ (listed, not checked): moves.measured.proto.json", self.events()[-1]["note"])

    def test_a_moves_file_in_another_subfolder_refuses(self):
        self.trio()
        (self.folder / "round2").mkdir()
        (self.folder / "round2" / "moves.measured.json").write_text(json.dumps(BASE))
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("moves file(s) in a subfolder other than history/: round2/moves.measured.json", p.stdout)

    def test_a_chained_round_names_its_baseline_and_that_file_is_in_the_record(self):
        self.trio()
        self.put("moves.measured.built.json", {**BASE, "rules:fired": 121, "gaps:capture": 371})
        self.commit()  # the named baseline is committed before the prediction that names it
        self.put("moves.prediction.fix1.json", {"moves": [{"what": "rules:fired", "from": 121, "to": 122}],
                                                "baseline": "moves.measured.built.json"})
        self.put("moves.measured.fix1.json", {**BASE, "rules:fired": 122, "gaps:capture": 371})
        p = self.close()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("baseline:   " + str(self.folder / "moves.measured.built.json"), p.stdout)
        self.assertIn("baseline runs/2026-01-02-T7-step1-review/moves.measured.built.json (sha256 ",
                      self.events()[-1]["note"])

    def test_untracked_files_refuse(self):
        self.trio()
        p = self.close(commit=False)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("uncommitted or untracked file(s)", p.stdout)
        self.assertIn("?? runs/2026-01-02-T7-step1-review/moves.measured.json", p.stdout)

    def test_an_uncommitted_edit_refuses_and_close_reads_head(self):
        self.trio()
        self.commit()
        (self.folder / "moves.measured.json").write_text(json.dumps({**BASE, "rules:fired": 121}, indent=1))
        p = self.close(commit=False)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn(" M runs/2026-01-02-T7-step1-review/moves.measured.json", p.stdout)

    def test_the_event_records_each_predictions_commits(self):
        self.put("moves.prediction.json", {"moves": [{"what": "rules:fired", "to": 121}]})
        self.commit()
        first = self.git("rev-parse", "--short", "HEAD").stdout.strip()
        self.trio()
        p = self.close()
        self.assertEqual(p.returncode, 0, p.stdout)
        second = self.git("rev-parse", "--short", "HEAD").stdout.strip()
        self.assertIn(f"commits {second},{first}, ", self.events()[-1]["note"])

    def test_a_prediction_changed_after_its_measurement_refuses_unless_approved(self):
        self.trio()
        self.commit()
        self.put("moves.prediction.json", {"moves": [{"what": "rules:fired", "range": [121, 122]}]})
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "prediction widened")
        p = self.close(commit=False)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("no 'Prediction-approved: <decision id>' trailer", p.stdout)
        decide(self.records, OK_ID)
        self.put("moves.prediction.json", {"moves": [{"what": "rules:fired", "range": [121, 123]}]})
        self.git("add", "-A")
        self.git("commit", "-q", "-m", f"prediction widened again\n\nPrediction-approved: {OK_ID}")
        p = self.close(commit=False)
        self.assertEqual(p.returncode, 1, p.stdout)  # the first, unapproved change still refuses
        self.git("reset", "-q", "--soft", "HEAD~2")
        self.git("commit", "-q", "-m", f"both changes\n\nPrediction-approved: {OK_ID}")
        p = self.close(commit=False)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("approved: ", self.events()[-1]["note"])
        self.assertIn(f"({OK_ID})", self.events()[-1]["note"])

    def test_a_stale_measured_file_refuses(self):
        self.trio(measured=BASE)
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("0 keys moved, and the prediction names 1 move(s)", p.stdout)
        # a refused close writes one gate-refused event, and nothing else
        self.assertEqual([(e["kind"], e.get("gate")) for e in self.events()],
                         [("gate-refused", "predict-gate:close")])

    def test_a_symlinked_folder_or_file_refuses(self):
        self.trio()
        (self.repo / "runs" / "2026-01-02-T8-step1-review").symlink_to(self.folder.name)
        p = self.close("T8")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("2026-01-02-T8-step1-review is a symbolic link", p.stdout)
        other = self.repo / "runs" / "2026-01-02-T9-step1-review"
        other.mkdir()
        for n in ("moves.prediction.json", "moves.baseline.json", "moves.measured.json"):
            (other / n).symlink_to(f"../{self.folder.name}/{n}")
        p = self.close("T9")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("symbolic link(s) in the record: moves.baseline.json, moves.measured.json, "
                      "moves.prediction.json", p.stdout)

    def test_files_already_closed_by_another_work_order_refuse(self):
        self.trio()
        self.assertEqual(self.close().returncode, 0)
        other = self.repo / "runs" / "2026-01-02-T8-step1-review"
        other.mkdir()
        for n in ("moves.prediction.json", "moves.baseline.json", "moves.measured.json"):
            (other / n).write_bytes((self.folder / n).read_bytes())
        p = self.close("T8")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("sha256 already recorded by the close of T7", p.stdout)
        self.assertEqual(self.close().returncode, 0)  # the same work order may close again

    def test_close_ignores_the_hooks_override_variable(self):
        self.trio(measured={**BASE, "rules:fired": 125})
        p = self.close(env=dict(self.env, PREDICT_GATE_OVERRIDE="exported for a commit"))
        self.assertEqual(p.returncode, 1, p.stdout)
        # a refused close writes one gate-refused event, and nothing else
        self.assertEqual([(e["kind"], e.get("gate")) for e in self.events()],
                         [("gate-refused", "predict-gate:close")])

    def test_close_writes_to_the_project_the_locator_finds_for_its_repo_and_names_its_ledger(self):
        self.trio()
        p = self.close(env=dict(self.env, CLAUDE_PROJECT_DIR=str(Path(self.tmp.name) / "elsewhere")))
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn(f"Recorded in {self.ledger}", p.stdout)
        self.assertIn(f"ledger: {self.ledger}", self.events()[-1]["note"])

    def test_the_id_must_match_the_tracker_form_whole(self):
        self.trio()
        p = self.close("T7\n")
        self.assertEqual(p.returncode, 2, p.stdout)
        b = self.binding
        b.write_text(b.read_text().replace("^T[0-9]+$", "T[0-9]+"))
        self.assertEqual(self.close("xT7").returncode, 2)

    def test_the_date_part_must_be_a_date(self):
        odd = self.repo / "runs" / "2026-01-02-T99-T5-step1-review"
        odd.mkdir()
        for n, d in (("moves.prediction.json", {"moves": [{"what": "rules:fired", "to": 121}]}),
                     ("moves.baseline.json", BASE), ("moves.measured.json", {**BASE, "rules:fired": 121})):
            (odd / n).write_text(json.dumps(d))
        p = self.close("T5")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("no record folder matches runs/*-T5-step1-review (with {date} as YYYY-MM-DD)", p.stdout)


OK_ID = "d20260101-a0a0a1"
FAILED = {**BASE, "rules:fired": 121, "gaps:capture": 371}  # an unpredicted move (gaps:capture)
FIX = {"moves": [{"what": "rules:fired", "from": 120, "to": 121}, {"what": "gaps:capture", "from": 370, "to": 371}]}
P1 = {"moves": [{"what": "rules:fired", "from": 120, "to": 121}]}
M1 = {**BASE, "rules:fired": 121}
FIX_P = {"baseline": "moves.measured.json", "moves": [{"what": "rules:fired", "from": 121, "to": 122}]}
FIX_M = {**BASE, "rules:fired": 122}


EARLY, LATE = "2026-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00"


def decide(records, did, answers=((EARLY, True),)):
    """A synthetic decision in the project's decision ledger: an ask, then each (time, agreed) answer."""
    led = Path(records) / "decisions" / "ledger.jsonl"
    led.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"kind": "ask", "id": did, "ts": EARLY, "stream": "repo", "category": "process", "question": "q",
             "options": ["yes", "no"], "recommendation": "yes", "confidence": 0.9, "blind": False,
             "autonomous": False}]
    rows += [{"kind": "answer", "id": did, "ts": ts, "choice": "yes", "agreed": ok} for ts, ok in answers]
    with led.open("a") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows))


class CloseLedger(RepoFixture):
    """Fix round 2: every exception to close cites an owner decision the decision ledger holds."""

    put, trio, commit, close = Close.put, Close.trio, Close.commit, Close.close

    def setUp(self):
        super().setUp()
        self.binding.write_text(json.dumps({
            "owned": True, "reviews": "runs/", "reviewName": "{date}-{id}-step{n}-review",
            "tracker": {"file": "docs/TRACKER.md", "id": "^T[0-9]+$"}}))
        self.folder = self.repo / "runs" / "2026-01-02-T7-step1-review"
        self.folder.mkdir(parents=True)

    def change_prediction(self, *message):
        self.trio()
        self.commit()
        self.put("moves.prediction.json", {"moves": [{"what": "rules:fired", "range": [121, 122]}]})
        self.git("add", "-A")
        self.git("commit", "-q", *[a for m in message for a in ("-m", m)])
        return self.close(commit=False)

    def test_a_trailer_naming_an_agreed_decision_approves_a_changed_prediction(self):
        decide(self.records, OK_ID)
        p = self.change_prediction("prediction widened", f"Prediction-approved: {OK_ID}")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("approved: ", self.events()[-1]["note"])
        self.assertIn(f"({OK_ID})", self.events()[-1]["note"])
        self.assertIn(str(self.records / "decisions/ledger.jsonl"), p.stdout)

    def test_a_trailer_that_is_not_a_ledger_backed_decision_refuses(self):
        decide(self.records, "d20260101-f0f0f1", ((EARLY, False),))
        decide(self.records, "d20260101-1a7e01", ((LATE, True),))
        decide(self.records, "d20260101-4e4e01", ((EARLY, True), ("2026-01-02T00:00:00+00:00", False)))
        for value, why in (("engineer, myself", "does not name a decision id"),
                           ("no", "does not name a decision id"),
                           ("d20260101-ffffff", "is not in the decision ledger"),
                           ("d20260101-f0f0f1", "the latest answer before the commit is not agreed true"),
                           ("d20260101-1a7e01", "has no answer recorded before the commit"),
                           ("d20260101-4e4e01", "the latest answer before the commit is not agreed true")):
            with self.subTest(value=value):
                self.git("rm", "-rq", "--ignore-unmatch", "runs")
                self.folder.mkdir(parents=True, exist_ok=True)
                p = self.change_prediction("prediction widened", f"Prediction-approved: {value}")
                self.assertEqual(p.returncode, 1, p.stdout)
                self.assertIn(why, p.stdout)
        # a refused close writes one gate-refused event, and nothing else
        self.assertEqual([(e["kind"], e.get("gate")) for e in self.events()],
                         [("gate-refused", "predict-gate:close")] * 6)

    def test_a_trailer_in_the_subject_is_not_a_trailer(self):
        decide(self.records, OK_ID)
        p = self.change_prediction(f"Prediction-approved: {OK_ID}")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("no 'Prediction-approved: <decision id>' trailer", p.stdout)

    def test_a_file_moved_into_history_after_the_measurement_needs_an_approval(self):
        self.trio()
        self.put("moves.measured.fix1.json", {**BASE, "rules:fired": 999})
        self.commit()
        (self.folder / "history").mkdir()
        self.git("mv", str(self.folder / "moves.measured.fix1.json"), str(self.folder / "history"))
        self.git("commit", "-q", "-m", "unpaired to history")
        p = self.close(commit=False)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("history/ changed in or after the commit of the record's first measurement", p.stdout)
        decide(self.records, OK_ID)
        self.git("commit", "-q", "--amend", "-m", "unpaired to history", "-m", f"Prediction-approved: {OK_ID}")
        p = self.close(commit=False)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("changed after the first measurement, approved: ", self.events()[-1]["note"])
        self.assertIn(f"({OK_ID})", self.events()[-1]["note"])

    def test_a_file_added_to_history_with_the_measurement_needs_an_approval(self):
        self.trio()
        (self.folder / "history").mkdir()
        self.put("history/moves.prediction.fix1.json", {"moves": [{"what": "rules:fired", "to": 122}]})
        self.put("history/moves.measured.fix1.json", {**BASE, "rules:fired": 122, "gaps:capture": 999})
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("history/ changed in or after the commit of the record's first measurement", p.stdout)

    def test_history_committed_before_the_measurement_closes(self):
        (self.folder / "history").mkdir()
        self.put("history/moves.measured.proto.json", {"x": 1})
        self.commit()
        self.trio()
        p = self.close()
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_a_prediction_with_no_move_refuses(self):
        for pred, base in (({"moves": [], "mustNotMove": ["rules:fired"]}, BASE),
                           ({"moves": [], "baseline": "moves.measured.json"}, None)):
            with self.subTest(pred=pred):
                self.trio(prediction=pred, measured=BASE, baseline=base)
                p = self.close()
                self.assertEqual(p.returncode, 1, p.stdout)
                self.assertIn("moves.prediction.json names no move", p.stdout)
        # a refused close writes one gate-refused event, and nothing else
        self.assertEqual([(e["kind"], e.get("gate")) for e in self.events()],
                         [("gate-refused", "predict-gate:close")] * 2)

    def test_s1_a_declared_no_moves_round_closes(self):
        self.trio(prediction={"noMoves": True, "mustNotMove": ["rules:*"]}, measured=BASE)
        p = self.close()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Result: CLOSED", p.stdout)

    def test_a_named_baseline_committed_with_or_after_its_prediction_refuses_unless_approved(self):
        self.trio()
        self.put("moves.measured.fix1.json", {**BASE, "rules:fired": 121, "gaps:capture": 999})
        self.put("moves.prediction.fix2.json", {"moves": [{"what": "q"}], "baseline": "moves.measured.fix1.json"})
        self.put("moves.measured.fix2.json", {**BASE, "rules:fired": 121, "gaps:capture": 999, "q": 1})
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("moves.measured.fix1.json, the baseline moves.prediction.fix2.json names, was not committed "
                      "before it", p.stdout)
        decide(self.records, OK_ID)
        self.git("commit", "-q", "--amend", "-m", "record", "-m", f"Prediction-approved: {OK_ID}")
        p = self.close(commit=False)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn(f"({OK_ID})", self.events()[-1]["note"])


    def ask(self, did, task, approves=None, choice="yes", agreed=True, options=("yes", "no")):
        """An ask of the review-A2 form: its approving option, the work order it serves, and one answer."""
        led = self.records / "decisions/ledger.jsonl"
        led.parent.mkdir(parents=True, exist_ok=True)
        ask = {"kind": "ask", "id": did, "ts": EARLY, "stream": "repo", "category": "process", "question": "q",
               "options": list(options), "recommendation": options[0], "confidence": 0.9, "blind": False,
               "autonomous": False, "task": task}
        if approves:
            ask["approves"] = approves
        with led.open("a") as f:
            f.write(json.dumps(ask) + "\n" + json.dumps(
                {"kind": "answer", "id": did, "ts": EARLY, "choice": choice, "agreed": agreed}) + "\n")

    def again(self):
        """A fresh repository and project: a record path keeps its history."""
        self.tearDown()
        self.setUp()

    def test_a_failed_round_moved_to_history_before_the_next_round_needs_an_approval(self):
        self.trio(measured=FAILED)
        self.commit()
        (self.folder / "history").mkdir()
        for n in ("moves.prediction.json", "moves.measured.json"):
            self.git("mv", str(self.folder / n), str(self.folder / "history"))
        self.git("commit", "-q", "-m", "superseded round to history/")
        self.trio(rnd=".fix1", prediction=FIX, measured=FAILED, baseline=None)
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("history/ changed in or after the commit of the record's first measurement", p.stdout)

    def test_a_failed_round_deleted_and_renamed_needs_an_approval(self):
        self.trio(measured=FAILED)
        self.commit()
        self.git("rm", "-q", str(self.folder / "moves.prediction.json"), str(self.folder / "moves.measured.json"))
        self.trio(rnd=".r2", prediction=FIX, measured=FAILED, baseline=None)
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("moves.measured.json was removed in or after the commit of the record's first measurement", p.stdout)
        self.assertIn("moves.prediction.json was removed in or after", p.stdout)
        decide(self.records, OK_ID)
        self.git("commit", "-q", "--amend", "-m", "round 2", "-m", f"Prediction-approved: {OK_ID}")
        p = self.close(commit=False)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("a moves file removed after the first measurement, approved: ", self.events()[-1]["note"])

    def test_a_round_moved_out_and_back_is_not_an_exception(self):
        # a real record's shape (a move to history/ reverted 8 seconds later): nothing escaped, so nothing to approve
        self.trio()
        self.commit()
        (self.folder / "history").mkdir()
        self.git("mv", str(self.folder / "moves.measured.json"), str(self.folder / "history"))
        self.git("commit", "-q", "-m", "to history/")
        self.git("mv", str(self.folder / "history" / "moves.measured.json"), str(self.folder))
        self.git("commit", "-q", "-m", "revert the move")
        p = self.close(commit=False)
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_an_approval_needs_its_approving_option_whatever_agreed_says(self):
        self.ask("d20260101-a2a2a1", "T7", approves="revise", choice="keep", agreed=True, options=("keep", "revise"))
        p = self.change_prediction("prediction widened", "Prediction-approved: d20260101-a2a2a1")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("chose 'keep', not the approving option 'revise'", p.stdout)
        self.again()
        self.ask("d20260101-a2a2a2", "T7", approves="revise", choice="Revise", agreed=False, options=("keep", "revise"))
        p = self.change_prediction("prediction widened", "Prediction-approved: d20260101-a2a2a2")
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_an_approval_from_another_work_orders_question_refuses(self):
        self.ask("d20260101-a5a5a5", "T9", approves="yes")
        p = self.change_prediction("prediction widened", "Prediction-approved: d20260101-a5a5a5")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("decision d20260101-a5a5a5 was asked for T9, not T7", p.stdout)

    def test_a_question_with_no_approving_option_keeps_the_agreed_rule(self):
        # named limit (owner D1): a question asked before --approves existed is read as before, task unchecked
        self.ask("d20260101-d1d1d1", "T9", agreed=True)
        p = self.change_prediction("prediction widened", "Prediction-approved: d20260101-d1d1d1")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.again()
        self.ask("d20260101-d1d1d2", "T7", agreed=False)
        p = self.change_prediction("prediction widened", "Prediction-approved: d20260101-d1d1d2")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("is not agreed true", p.stdout)


class CloseMerged(RepoFixture):
    """`close <work-order> --merged`, /bakehouse:change step 10 (owner D2): only step 9's sha256 values. Close's
    helpers without its tests."""

    put, trio, commit, close, sha = Close.put, Close.trio, Close.commit, Close.close, Close.sha

    def setUp(self):
        super().setUp()
        self.binding.write_text(json.dumps({
            "owned": True, "reviews": "runs/", "reviewName": "{date}-{id}-step{n}-review",
            "tracker": {"file": "docs/TRACKER.md", "id": "^T[0-9]+$"}}))
        self.folder = self.repo / "runs" / "2026-01-02-T7-step1-review"
        self.folder.mkdir(parents=True)

    def merged(self):
        return subprocess.run([str(GATE), "close", "T7", "--merged", "--repo",
                               str(self.repo)], capture_output=True, text=True, env=self.env, check=False)

    def test_merged_close_after_a_squash_checks_only_the_step_9_hashes(self):
        default = self.git("symbolic-ref", "--short", "HEAD").stdout.strip()
        self.git("checkout", "-q", "-b", "job")
        self.trio()
        self.commit()
        self.put("moves.prediction.fix1.json", {"baseline": "moves.measured.json",
                                                "moves": [{"what": "rules:fired", "from": 121, "to": 122}]})
        self.commit()
        self.put("moves.measured.fix1.json", {**BASE, "rules:fired": 122})
        self.assertEqual(self.close().returncode, 0)
        self.git("checkout", "-q", default)
        self.git("merge", "--squash", "-q", "job")
        self.git("commit", "-q", "-m", "squash")
        self.assertEqual(self.close(commit=False).returncode, 1)
        p = self.merged()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        ev = self.events()
        self.assertEqual([e["kind"] for e in ev].count("work-order-closed"), 1)
        self.assertEqual(ev[-1]["kind"], "note")
        self.assertIn(f"sha256:{self.sha('moves.measured.fix1.json')}", ev[-1]["note"])
        self.assertIn("compared with the work-order-closed event at", ev[-1]["note"])

    def test_merged_close_refuses_a_changed_file(self):
        self.trio()
        self.assertEqual(self.close().returncode, 0)
        self.put("moves.measured.json", {**BASE, "rules:fired": 121, "extra": 1})
        self.git("rm", "-q", str(self.folder / "moves.baseline.json"))
        self.commit()
        p = self.merged()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("moves.measured.json: sha256 ", p.stdout)
        self.assertIn("moves.baseline.json: not in git at HEAD", p.stdout)
        self.assertIn("--merged has no override", p.stdout)
        p = subprocess.run([str(GATE), "close", "T7", "--merged", "--repo",
                            str(self.repo)], capture_output=True, text=True,
                           env=dict(self.env, PREDICT_GATE_CLOSE_OVERRIDE="owner go"), check=False)
        self.assertEqual(p.returncode, 1, p.stdout)

    def test_s1_fix1_close_records_the_missed_file_and_merged_refuses_it_changed(self):
        self.trio(prediction={"moves": [{"what": "rules:fired", "from": 120, "to": 121}, {"what": "rules:new", "to": 1}]})
        self.put("moves.missed.json", {"rules:new": "the key is not emitted yet"})
        self.assertEqual(self.close().returncode, 0)
        self.assertIn(f"moves.missed.json sha256:{self.sha('moves.missed.json')}", self.events()[-1]["note"])
        self.put("moves.missed.json", {"rules:new": "a reason rewritten after the close"})
        self.commit()
        p = self.merged()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("moves.missed.json: sha256 ", p.stdout)

    def test_merged_close_refuses_with_no_step_9_close(self):
        self.trio()
        self.commit()
        p = self.merged()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("no work-order-closed event for T7", p.stdout)
        self.assertEqual([(e["kind"], e.get("gate")) for e in self.events()], [("gate-refused", "predict-gate:close")])


class CloseOrder(RepoFixture):
    """The code-order rules (review A4, owner D5): each round's prediction is committed before its code,
    and no code comes after the latest measurement. By commit time, from ORDER_FROM."""

    def setUp(self, roots=("src/",)):
        super().setUp()
        b = {"owned": True, "reviews": "runs/", "reviewName": "{date}-{id}-step{n}-review",
             "tracker": {"file": "docs/TRACKER.md", "id": "^T[0-9]+$"}}
        if roots:
            b["signal"] = {"codeRoots": list(roots)}
        self.binding.write_text(json.dumps(b))
        self.rec = "runs/2026-11-10-T7-step1-review"
        self.n, self.day = 0, "2026-11-10"

    def at(self, msg, files=None, *trailers, mv=()):
        self.n += 1
        when = f"{self.day}T09:{self.n:02d}:00+00:00"
        for path, data in (files or {}).items():
            f = self.repo / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(data if isinstance(data, str) else json.dumps(data))
        for a, b in mv:
            (self.repo / b).parent.mkdir(parents=True, exist_ok=True)
            self.git("mv", a, b)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", msg, *[x for t in trailers for x in ("-m", t)],
                 env=dict(self.env, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when))

    def r(self, n):
        return f"{self.rec}/{n}"

    def close(self, **env):
        return subprocess.run([str(GATE), "close", "T7", "--repo", str(self.repo)],
                              capture_output=True, text=True, env=dict(self.env, **env), check=False)

    def round1(self, then=()):
        self.at("T7: prediction", {self.r("moves.prediction.json"): P1, self.r("moves.baseline.json"): BASE})
        self.at("T7: the change", {"src/a.py": "a = 2\n"})
        self.at("T7: measurement", {self.r("moves.measured.json"): M1})

    def test_code_committed_after_the_latest_measurement_refuses(self):
        self.round1()
        self.at("T7: one more fix", {"src/a.py": "a = 2  # fixed\n"})
        self.at("T8: another work order's code", {"src/b.py": "b\n"})
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(p.stdout.count("comes after the latest measured file's commit"), 1, p.stdout)

    def test_a_prediction_committed_with_or_after_the_code_refuses(self):
        self.at("T7: change, no prediction yet", {"src/a.py": "a = 2\n"})
        self.at("T7: record", {self.r("moves.prediction.json"): P1, self.r("moves.baseline.json"): BASE,
                               self.r("moves.measured.json"): M1})
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("has no round whose prediction was committed before it", p.stdout)

    def test_a_prediction_committed_in_the_same_commit_as_the_code_refuses(self):
        self.at("T7: prediction and code together", {self.r("moves.prediction.json"): P1,
                                                     self.r("moves.baseline.json"): BASE, "src/a.py": "a = 3\n"})
        self.at("T7: measurement", {self.r("moves.measured.json"): M1})
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("has no round whose prediction was committed before it", p.stdout)

    def test_a_round_predicted_on_the_cut_off_day_is_judged(self):
        self.day = "2026-11-04"
        self.round1()
        self.at("T7: one more fix", {"src/a.py": "a = 2  # fixed\n"})
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("comes after the latest measured file's commit", p.stdout)

    def test_an_old_rounds_code_is_not_judged_beside_a_new_round(self):
        self.day = "2026-10-10"
        self.round1()
        self.day = "2026-11-10"
        self.at("T7: fix 1 prediction", {self.r("moves.prediction.fix1.json"): FIX_P})
        self.at("T7 fix 1: the fix", {"src/a.py": "a = 3\n"})
        self.at("T7: fix 1 measurement", {self.r("moves.measured.fix1.json"): FIX_M})
        p = self.close()
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_uncommitted_or_untracked_code_refuses(self):
        self.round1()
        (self.repo / "src/a.py").write_text("a = 4\n")
        (self.repo / "src/new.py").write_text("n = 1\n")
        (self.repo / "notes.txt").write_text("outside the code roots\n")
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        line = next(x for x in p.stdout.split("\n") if "under the code roots" in x)
        self.assertIn("M src/a.py", line)
        self.assertIn("?? src/new.py", line)
        self.assertNotIn("notes.txt", line)

    def test_a_fix_rounds_code_before_its_prediction_refuses(self):
        self.round1()
        self.at("T7 fix 1: the fix", {"src/a.py": "a = 3\n"})
        self.at("T7: fix 1 record", {self.r("moves.prediction.fix1.json"): FIX_P,
                                     self.r("moves.measured.fix1.json"): FIX_M})
        p = self.close()
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("has no round whose prediction was committed before it", p.stdout)

    def test_a_superseded_rounds_code_stays_covered_by_its_prediction_in_history(self):
        decide(self.records, OK_ID)
        self.round1()
        self.at("T7: superseded round to history/", None, f"Prediction-approved: {OK_ID}",
                mv=[(self.r(n), self.r(f"history/{n}")) for n in ("moves.prediction.json", "moves.measured.json")])
        self.at("T7: fix 1 prediction", {self.r("moves.prediction.fix1.json"): {"moves": [
            {"what": "rules:fired", "from": 120, "to": 122}]}})
        self.at("T7 fix 1: the fix", {"src/a.py": "a = 3\n"})
        self.at("T7: fix 1 measurement", {self.r("moves.measured.fix1.json"): FIX_M})
        p = self.close()
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_rounds_before_the_cut_off_keep_todays_result(self):
        # named limit (owner d20261103-fb2f2c): a round whose prediction is first committed before ORDER_FROM
        self.day = "2026-10-10"
        self.at("T7: change, no prediction yet", {"src/a.py": "a = 2\n"})
        self.round1()
        self.at("T7: one more fix", {"src/a.py": "a = 2  # fixed\n"})
        p = self.close()
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_close_ignores_ops_ts_and_ops_commit(self):
        self.round1()
        p = self.close(OPS_TS="2020-01-01T00:00:00+00:00", OPS_COMMIT="feedbee")
        self.assertEqual(p.returncode, 0, p.stdout)
        ev = self.events()[-1]
        self.assertNotEqual(ev["ts"], "2020-01-01T00:00:00+00:00")
        self.assertNotEqual(ev["commit"], "feedbee")
        self.assertTrue(self.git("rev-parse", "HEAD").stdout.startswith(ev["commit"]))

    def test_a_binding_with_no_code_roots_skips_the_order_checks_and_says_so(self):
        self.tearDown()
        self.setUp(roots=())
        self.round1()
        self.at("T7: one more fix", {"src/a.py": "a = 2  # fixed\n"})
        p = self.close()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("the binding names no signal.codeRoots, so the code-order checks were not run", p.stdout)


class HookRecord(RepoFixture):
    """Fix round 1: the commit hook follows close's rule, and a prediction may name its baseline."""

    def setUp(self):
        super().setUp()
        self.install_hook()

    def put(self, name, data):
        (self.run_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (self.run_dir / name).write_text(json.dumps(data))

    def commit(self):
        self.git("add", "-A")
        return self.git("commit", "-m", "x", check=False)

    def test_hook_skips_history_and_takes_a_named_baseline(self):
        self.write({"moves": [{"what": "rules:fired", "to": 121}]}, {**BASE, "rules:fired": 121})
        self.put("history/moves.measured.proto.json", {"x": 1})
        self.put("moves.measured.built.json", {**BASE, "rules:fired": 121, "gaps:capture": 371})
        self.put("moves.prediction.fix1.json", {"moves": [{"what": "rules:fired", "from": 121, "to": 122}],
                                                "baseline": "moves.measured.built.json"})
        self.put("moves.measured.fix1.json", {**BASE, "rules:fired": 122, "gaps:capture": 371})
        p = self.commit()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("moves.measured.built.json: a prediction in this folder names it as its baseline",
                      p.stdout + p.stderr)
        self.assertNotIn("history", p.stderr)

    def test_hook_refuses_history_in_a_subfolder_and_skips_history_in_history(self):
        self.write({"moves": [{"what": "rules:fired", "to": 121}]}, {**BASE, "rules:fired": 121})
        self.put("history/history/moves.measured.x.json", {"x": 1})
        p = self.commit()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.put("sub/history/moves.measured.x.json", {"x": 2})
        p = self.commit()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("sub/history/moves.measured.x.json is staged in a history/ folder whose parent holds no "
                      "moves.prediction*", p.stdout + p.stderr)

    def test_hook_refuses_an_unpaired_round_measurement(self):
        self.write({"moves": []}, BASE)
        self.put("moves.measured.after-x.json", {**BASE, "rules:fired": 999})
        p = self.commit()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("moves.measured.after-x.json is staged, and no moves.prediction.after-x.json",
                      p.stdout + p.stderr)

    def test_check_uses_the_predictions_baseline_field(self):
        self.put("moves.measured.v5.json", {**BASE, "rules:fired": 130})
        self.put("moves.prediction.v6.json", {"moves": [{"what": "rules:fired", "from": 130, "to": 131}],
                                              "baseline": "moves.measured.v5.json"})
        self.put("moves.measured.v6.json", {**BASE, "rules:fired": 131})
        self.put("moves.baseline.json", BASE)
        p = self.gate("check", "--measured", str(self.run_dir / "moves.measured.v6.json"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("baseline:   " + str(self.run_dir / "moves.measured.v5.json"), p.stdout)

    def test_the_baseline_field_is_a_file_name_beside_the_prediction(self):
        for bad in ("../x.json", "sub/moves.baseline.json", "moves.baseline.txt", 3):
            self.put("moves.prediction.json", {"moves": [], "baseline": bad})
            self.put("moves.measured.json", BASE)
            p = self.gate("check", "--measured", str(self.run_dir / "moves.measured.json"))
            self.assertEqual(p.returncode, 2, (bad, p.stdout))
            self.assertIn("'baseline' must be the name of a .json file beside the prediction", p.stderr)


if __name__ == "__main__":
    unittest.main()
