"""One gate-refused event per refusal, written through ops-event.sh. docs/PROJECT-TEAM.md, "Gate refusals",
lists every gate id and the event's fields.

Best effort by design: the tool has already refused, so a failed write never changes its exit code or
what it prints. It returns the reason instead, and never raises. (An override is different: it must be
recorded, and its writer refuses when it cannot be. That stays in predict-gate.py.)

Where the event goes: the project of the repository at `path` (runtime/locator.py, through ops-event.sh);
else the project of the working directory's repository.
"""

import os
import signal
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOTE_CHARS = 300
WAIT_S = 3  # below the 10 s timeout the harness gives a hook; a slow write is cut, never waited on


def target(path=None) -> dict:
    """The env that points ops-event.sh at the ledger: OPS_REPO; {} for the cwd."""
    if not path:
        return {}
    p = Path(path).expanduser()
    return {"OPS_REPO": str(p if p.is_dir() else p.parent)}


def refused(
    gate,
    purpose,
    note,
    task=None,
    count=None,
    path=None,
    outcome="refused",
    wait=WAIT_S,
    note_chars=NOTE_CHARS,
):
    """Write one gate-refused event. None when written, else why not. wait: the seconds the write may take
    (at most WAIT_S); a caller with its own deadline (the agent-roles hook) passes what is left of it.
    note_chars: the note is cut to this many characters; None keeps it whole (close's refusal, which lists
    every refusal with its id)."""
    wait = min(wait, WAIT_S)
    if wait <= 0:
        return "no time was left before the caller's deadline; not written"
    try:
        # OPS_TS and OPS_COMMIT would back-date the event or pin it to another commit: a refusal is dated now
        env = {
            k: v for k, v in os.environ.items() if k not in ("OPS_REPO", "OPS_TS", "OPS_COMMIT")
        }
        env.update(target(path))
        argv = [
            str(HERE / "ops-event.sh"),
            "gate-refused",
            task or "-",
            purpose,
            "-",
            "-",
            "-" if count is None else str(count),
            "-",
            "-",
            outcome,
            (note or "")[:note_chars],
            gate,
        ]
        # its own process group, so a write cut at WAIT_S is killed whole: a child left alive would hold
        # the pipes open, and reading them would wait for it
        p = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
        try:
            _, err = p.communicate(timeout=wait)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.communicate()
            return f"ops-event.sh did not finish in {wait:g} s; cut"
    except Exception as e:  # noqa: BLE001 - never break the tool that refused
        return f"cannot run ops-event.sh: {e!r}"
    return (
        None
        if p.returncode == 0
        else (err.strip() or f"ops-event.sh exit {p.returncode}")
    )
