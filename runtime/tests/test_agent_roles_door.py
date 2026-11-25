"""Tests for 0.17.0 (b): a change engineer dispatch with a `Work order <id>:` line needs a door event for that id in
the project's ops ledger (gate dispatch:door). Not gated: an architect, a reviewer, a dispatch with no Work order
line, a later engineer on a work order that has a door event. Records format 2 is read; a ledger that cannot be read
lets the dispatch pass, with one message."""
import json
import unittest

import test_gate_events

REASON = ("bakehouse agent-roles: work order T7 has no door route recorded (change.md step 4.3). Run bakehouse door "
          "--task T7; it records the route. Then dispatch again.")


class DispatchDoor(test_gate_events.TestRefusalsWriteOneEvent):
    def home(self):
        h = super().home()
        (h / "plugin" / "agents" / "architect.md").write_text("---\nname: architect\n---\n")
        return h

    def door_event(self, task="T7"):
        p = self.run_([str(self.rt / "ops-event.sh"), "door", task, "door", "-", "-", "-", "-", "-", "fast",
                       "1 file in the paths"])
        self.assertEqual(p.returncode, 0, p.stderr)

    def answer(self, st, prompt):
        p = self.hook(self.pre(st, prompt))
        return json.loads(p.stdout) if p.stdout.strip() else {}

    def decision(self, out):
        return (out.get("hookSpecificOutput") or {}).get("permissionDecision") or "none"

    def test_refused_with_no_door_event(self):
        self.door_event("T9")  # the project uses the door
        out = self.answer("role:change:engineer", "Work order T7: x\n")
        self.assertEqual((self.decision(out), out["hookSpecificOutput"]["permissionDecisionReason"]), ("deny", REASON))
        (ev,) = self.refusals()
        self.assertEqual((ev["gate"], ev["task"], ev["purpose"], ev["note"]),
                         ("dispatch:door", "T7", "agent dispatch refused", REASON))

    def test_refused_by_plain_name_with_the_line(self):
        self.door_event("T9")
        out = self.answer("engineer", "Work order T7: x\n")
        self.assertEqual(self.decision(out), "deny")
        self.assertEqual([e["gate"] for e in self.refusals()], ["dispatch:door"])

    def test_passes_with_a_door_event_and_again_for_a_fix_round(self):
        self.door_event("T9")
        self.assertEqual(self.decision(self.answer("role:change:engineer", "Work order T7: x\n")), "deny",
                         "another work order's door event")
        self.door_event()
        for prompt in ("Work order T7: x\n", "Work order T7: fix round 1\n"):
            self.assertEqual(self.decision(self.answer("role:change:engineer", prompt)), "allow", prompt)
        self.assertEqual(len(self.refusals()), 1)

    def test_a_project_that_has_never_run_the_door_is_not_gated(self):
        out = self.answer("role:change:engineer", "Work order T7: x\n")
        self.assertEqual(self.decision(out), "allow")
        self.assertEqual(self.refusals(), [])

    def test_an_architect_and_a_reviewer_pass(self):
        for st in ("role:architect", "role:reviewer"):
            out = self.answer(st, "Work order T7: x\n")
            self.assertNotEqual(self.decision(out), "deny", (st, out))
        self.assertEqual(self.refusals(), [])

    def test_no_work_order_line_is_not_gated(self):
        out = self.answer("engineer", "Do a thing.\n")
        self.assertNotEqual(self.decision(out), "deny")
        self.assertIn("plain name", out.get("systemMessage", ""))
        self.assertEqual(self.refusals(), [])

    def test_records_format_2_writer_files_are_read(self):
        writer = self.records / "ops" / "events" / "w1.jsonl"
        writer.parent.mkdir(parents=True)
        writer.write_text(json.dumps({"ts": "2026-01-02T12:00:00+00:00", "kind": "door", "task": "T7",
                                      "purpose": "door", "outcome": "fast", "note": "x"}) + "\n")
        self.assertEqual(self.decision(self.answer("role:change:engineer", "Work order T7: x\n")), "allow")

    def test_an_unreadable_ledger_passes_with_one_message(self):
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.mkdir()  # a folder where the ledger file should be: it cannot be read
        out = self.answer("role:change:engineer", "Work order T7: x\n")
        self.assertEqual(self.decision(out), "allow")
        msg = out.get("systemMessage", "")
        self.assertIn("door", msg)
        self.assertIn(str(self.ledger), msg)


def load_tests(loader, tests, pattern):
    """Only this file's own tests, not the ones inherited."""
    own = sorted(n for n in DispatchDoor.__dict__ if n.startswith("test_"))
    return unittest.TestSuite(DispatchDoor(n) for n in own)


if __name__ == "__main__":
    unittest.main()
