#!/usr/bin/env python3
"""Agent roles — enforce the role tables on every agent dispatch.

Three entry points, all wired by the plugin's hooks:

  PreToolUse (matcher Agent)  stdin = hook JSON
      - subagent_type 'role:<job>:<role>' or 'role:<role>' is resolved by team_roles (design
        docs/PROJECT-TEAM.md, "Roles"): the repo binding's roles, then the personal agent-roles.json in
        the machine folder, then the shipped agent-roles.json. A review role nothing fills goes to general-purpose; any
        other unfilled role is denied, and the session does the work itself.
      - a dispatch of a role's provider gets the role's model and prompt_append.
      - an agent spawned BY a role's provider is held to that role's nested_model.
  SubagentStop                stdin = hook JSON
      - a role with run_record: the agent is sent back once if it wrote no record folder under the
        binding's `reviews` (default axis/runs/). When the role is a job in runtime/record-contracts.json
        (the reviewer), every folder under `reviews`, at any depth, in which it wrote a file the job's
        `marks` match (findings*.md) must also hold that job's artefacts, checked by records-check's own
        check_folder: a reviewer's mutation record beside each findings file (findings.md owes
        mutations.json, findings-fix1.md owes mutations-fix1.json), in the mutation-record format, every
        survivor with a disposition; each findings file not empty. A contract (a .md whose name holds
        "contract": contract.md, a round's contract-fix1.md) sits beside each findings file written or in
        a folder above it, inside the folder checked (this check is the stop's own). The folder is decided
        at dispatch (PreToolUse, for a change job and a run_record role; a plain-name reviewer only where
        the cwd has a binding, or a line names a folder under a bound repo's `reviews`): every line labelled
        as a record folder is read; a line record_line's reader does not read, a path that is not full
        (`/` or `~`), a full path under no bound repo's `reviews` (that repo found from the path, not the
        cwd), and two lines naming different folders are refused,
        naming the line and where it resolves. The decided folder is logged with the dispatch, and the
        stop checks it (a repo record with its own repo's binding), never re-reading the line from its cwd.
        The line holds one path and nothing else: backticks or straight quotes may wrap it (a space is
        allowed only inside them), trailing whitespace is fine, and any other text after the path refuses
        the line ("put notes on the next line"); nothing after a path is ever guessed away.
        With no dispatch decision, record_line reads the line from the cwd (bold, indented or a list
        item, backticks; `~`, `..`, symlinks resolved; a relative path from the cwd's repository root),
        and a line it cannot place is reported to the user, never refused: the stop runs as if the brief
        had no line. Each findings file written during the run needs its mutation record
        written during the run too. A findings-like file written during the run under a name the job's
        `rounds` do not declare (records-check's misnamed) is refused, naming the expected pair. Sent
        back once, with what is wrong.
      - the change engineer (`prediction_record` in the shipped table: change:engineer). PreToolUse
        marks a `role:change:engineer` dispatch, and a dispatch by the agent's plain name in a repo
        with a binding when its brief has a `Work order <id>:` line (without one it warns: close is
        the hard gate). The marker is the prompt's own final line. At SubagentStop, only for the agent
        type change:engineer resolves to: PreToolUse logs every role: dispatch of that agent type, with or
        without a binding, and every plain-name one in a repo with a binding, with
        whether it is a change job, to logs/prediction-dispatch.jsonl in the machine folder (sha256 of the prompt
        before and after the rewrite). A stop whose transcript line 1 is a logged prompt follows the
        log, so a refactor job is never checked. Otherwise it is checked when line 1 ends with the
        marker, or holds a `Work order <id>:` line and the agent is not a built-in one (a built-in
        agent is marked only through a role: dispatch). A checked agent is sent back once unless the
        record folder its brief names (`Record folder:`, read by record_line; else the binding's folder
        for that id) holds
        a moves.prediction<R>.json that predict-gate's own checker accepts and that names at least one
        move, or declares "noMoves": true (accepted while its round has no measured file yet, and beside
        its measured pair when that pair moved 0 keys). Every
        stop of that agent type logs whether line 1 ends with the marker (PROMPT-SOURCE).
  --check-new                 (SessionStart) print agents installed since the last check, once. It judges
                              the folder locator.hook_folder names ($CLAUDE_PROJECT_DIR, else the hook input's
                              cwd, else the process cwd).

Measured behaviour this relies on (2026-09-30, in a live session): PreToolUse fires for Agent calls
made inside subagents and carries agent_id/agent_type of the caller; updatedInput rewrites prompt
and model; SubagentStop {"decision":"block"} makes the agent continue, with stop_hook_active on
re-entry.

Fail-open, with two exceptions (the second is a broken role table, below). A dispatch whose decision needs a
binding that cannot be read is
refused, and the reason names the cause and what to fix (gate dispatch:binding-unreadable): binding.json
does not parse, is not a JSON object, cannot be read or is a dangling symlink, or its `reviews` field is not a
safe path (a record folder line names a folder in that repo); the project needs setup (the
locator's message; only a team dispatch is refused for it: a role: dispatch, an agent a role table lists, or a
brief with a `Work order <id>:` or record folder line); the project locator cannot be run or does not answer in time. The hook cannot tell
whether that dispatch is in its care, so it does not guess. Its event is kept in agent-roles.log with its
cause: the working folder's ledger is found through the input that cannot be read. A broken role table never
changes a dispatch silently (R0.1 S6 #3): while the personal table, or the binding's `roles` (not an object), cannot
be used, a role: dispatch is refused, the reason naming the file and the error (gate dispatch:role-table), and any
other dispatch skips that table, keeps the shipped table's rules and tells the user (systemMessage). A broken
shipped table is a bug in the plugin: every dispatch passes unchanged, and the user is told. Every other error
lets the dispatch through unchanged: a crash anywhere in the hook. A stop always fails open:
an unreadable binding there is read as none. A `Record folder:` line with any text after its path is
refused at dispatch, showing the line, and reported at a stop, never read; a refused full path under a
bound repo's reviews puts the dispatch in care. The whole run has one deadline, RUN_BUDGET_S, counted from hook
start (STARTUP_S allowed for the interpreter), well below the harness's 10 s: the locator and git get what is
left of it less EVENT_S, the refusal event the rest less EXIT_S; each path's binding is read once per run. Every
change it makes is logged to logs/agent-roles.log in the machine folder (locator.machine_dir()).
"""
import time

STARTUP_S = 0.2  # allowed for the interpreter's start-up: the run's clock starts at hook start
START = time.monotonic() - STARTUP_S
import fnmatch
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import locator  # noqa: E402 - the plugin's own module, beside this file
    LOCATOR_ERROR = None
    MACHINE = locator.machine_dir()
except Exception as e:  # fail open, as for team_roles below: logged, and every dispatch passes unchanged
    LOCATOR_ERROR = e
    # locator.machine_dir()'s rule, so the failure can still be logged
    MACHINE = Path(os.environ.get("BAKEHOUSE_MACHINE_DIR") or Path(
        os.environ.get("XDG_STATE_HOME") or Path(os.environ.get("HOME", str(Path.home()))) / ".local" / "state")
        / "bakehouse")
SEEN = MACHINE / "seen-agents"
LOG = MACHINE / "logs" / "agent-roles.log"
DISPATCHES = MACHINE / "logs" / "prediction-dispatch.jsonl"
# a lookup asks the repo of the path it is given (the hook input's cwd, or a path a brief names), never
# through $CLAUDE_PROJECT_DIR in its env: the session may have been launched in another folder (S16 M2). A cwd
# whose repo has no team falls back to that launch folder's repo by name (lookup_paths, S16 fix2).
NAMED_ENV = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
MARKER = "[bakehouse:agent-roles]"
PREDICTION_MARKER = "[bakehouse:prediction-record]"
APPROVED = "[model-approved]"
DEFAULT_REVIEWS = "axis/runs/"
WORK_ORDER = re.compile(r"^Work order (\S+?):", re.M)
RECORD_FOLDER = re.compile(r"^[ \t]*(?:[-*][ \t]+)?(\*\*|__)?Record folder(?:\1)?:(?:\1)?[ \t]*(\S[^\n]*)", re.M)
# any line labelled as a record folder, in any form, so that no form is dropped at dispatch
RECORD_LABEL = re.compile(r"^[ \t>]*(?:(?:[-*+]|\d+[.)])[ \t]+)?[*_`]*(?:your[ \t]+)?record[ \t]+folder[^\w\n]{0,4}:",
                          re.MULTILINE | re.IGNORECASE)
RUN_BUDGET_S = 6  # the whole hook run; the harness kills a hook at 10 s
EVENT_S = 2  # the end of the run kept for a refusal event
EXIT_S = 0.25  # the last of it kept for the hook to answer and exit


def log(msg):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


if LOCATOR_ERROR is not None:
    log(f"ERROR import locator: {LOCATOR_ERROR!r}")
try:
    import team_roles
except Exception as e:  # fail open: without the module every dispatch passes unchanged
    log(f"ERROR import team_roles: {e!r}")
    team_roles = None


def left(keep=EVENT_S):
    """Seconds left of the run's deadline, less keep."""
    return START + RUN_BUDGET_S - keep - time.monotonic()


class Unreadable(Exception):
    """A binding a dispatch decision needs cannot be read; the message names the cause."""


_BINDINGS = {}


def lookup_paths(path, named):
    """The paths a lookup asks, in order, until one has a team: the cwd's repo, then the session's launch
    project ($CLAUDE_PROJECT_DIR), the stricter of the two (S16 fix2); a path a brief names, its own repo only."""
    launch = None if named else os.environ.get("CLAUDE_PROJECT_DIR")
    return [str(path)] + ([launch] if launch else [])


def binding_at(path, named=False):
    """(binding, problem) for the repo at path (team_roles.read_binding; for the cwd, lookup_paths), read once
    per run, the locator given what is left of the run's deadline. named: a path a brief names, not the cwd; a
    named path in a project's records worktree reads that project's binding (the locator's --owner)."""
    k = str(path)
    if (k, named) not in _BINDINGS:
        for at in lookup_paths(k, named):
            t = min(team_roles.RESOLVE_TIMEOUT, left())
            b, why = team_roles.read_binding(at, log, timeout=t, env=NAMED_ENV, owner=named)
            if b or why:
                break
        if why and t < team_roles.RESOLVE_TIMEOUT and ("did not answer" in why or "was not run" in why):
            why = (f"the hook's {RUN_BUDGET_S} s run deadline ran out: earlier calls in this run (a slow locator or "
                   f"git) used {time.monotonic() - START:.1f} s of it, so {why}")
        _BINDINGS[k, named] = b, why
        if why:
            log(f"UNREADABLE binding for {k}: {why}")
    return _BINDINGS[k, named]


def bound(path, named=False):
    """The binding a dispatch decision reads: raises Unreadable when it cannot be read."""
    b, why = binding_at(path, named)
    if why:
        raise Unreadable(why)
    return b


def refusal_event(gate, reason, prompt, where, keep=False):
    """One gate-refused event for a deny or a block (docs/PROJECT-TEAM.md, "Gate refusals"), in the ledger of
    the project `where` belongs to. keep: kept in agent-roles.log with the reason instead, when no ledger can be
    found without the input that failed. Fail-open like the rest of this hook: it never changes what the hook
    returns."""
    if keep:
        log(f"GATE-EVENT {gate} kept in this log, not written to a ledger: the working folder's ledger is found "
            f"through the input that cannot be read. {reason}")
        return
    try:
        import gate_event
        m = WORK_ORDER.search(prompt or "")
        purpose = "agent dispatch refused" if gate.startswith("dispatch:") else "agent stop refused"
        err = gate_event.refused(gate, purpose, reason, task=m.group(1) if m else None, path=str(where),
                                 wait=left(EXIT_S))
        if err:
            log(f"GATE-EVENT {gate} not written: {err}")
    except Exception as e:  # noqa: BLE001 - fail open
        log(f"ERROR gate event {gate}: {e!r}")


def predict_gate():
    import importlib.util
    spec = importlib.util.spec_from_file_location("predict_gate", Path(__file__).resolve().with_name("predict-gate.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def prediction_jobs():
    """The `<job>:<role>` dispatches that must leave a machine prediction. Read from the shipped
    table only: neither a binding nor the owner's table can take the rule away."""
    try:
        v = json.loads(team_roles.shipped_table().read_text()).get("prediction_record")
    except (OSError, ValueError, AttributeError):
        return set()
    return {x for x in v if isinstance(x, str)} if isinstance(v, list) else set()


def change_engineers(binding, cwd):
    """The agent(s) the prediction_record jobs resolve to in this repo."""
    lvls, have = team_roles.levels(binding), team_roles.installed_agents(cwd)
    return {a for spec in prediction_jobs() if (a := team_roles.resolve(spec, lvls, have)["agent"])}


def digest(text):
    import hashlib
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def record_dispatch(agent, before, after, change, record=None):
    """One line per dispatch of the change engineer's agent type (is it a change job?) and per dispatch
    whose record folder was decided (record). change None: not the change engineer's agent type."""
    e = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "agent": agent}
    if change is not None:
        e["change"] = change
    if record:
        e["record"] = record
    e["prompts"] = sorted({digest(before), digest(after)})
    try:
        DISPATCHES.parent.mkdir(parents=True, exist_ok=True)
        with open(DISPATCHES, "a") as f:
            f.write(json.dumps(e) + "\n")
    except OSError as e:
        log(f"ERROR prediction-dispatch log: {e!r}")


def logged(prompt):
    """The dispatch-log entries for this prompt, oldest first."""
    try:
        lines = DISPATCHES.read_text().splitlines()[-2000:]
    except OSError:
        return []
    h, out = digest(prompt), []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and h in (e.get("prompts") or []):
            out.append(e)
    return out


def dispatched(prompt):
    """True / False when the prompt is a logged dispatch of a change / other job; None when not logged."""
    job = None
    for e in logged(prompt):
        if "change" in e:
            job = e.get("change") is True
    return job


def dispatch_record(prompt):
    """The record folder decided at dispatch for this prompt, as a line (kind, root, folder, shown,
    path); None when no dispatch decided one."""
    rec = None
    for e in logged(prompt):
        r = e.get("record")
        if isinstance(r, dict) and r.get("kind") == "repo" and all(
                isinstance(r.get(k), str) for k in ("root", "folder", "line", "path")):
            rec = (r["kind"], Path(r["root"]), r["folder"], r["line"], r["path"])
    return rec


def work_order(prompt, binding):
    """The id on the brief's `Work order <id>:` line, when it has the binding's tracker id form."""
    m = WORK_ORDER.search(prompt or "")
    form = (binding.get("tracker") or {}).get("id") if isinstance(binding.get("tracker"), dict) else None
    if not m or (isinstance(form, str) and not re.fullmatch(form, m.group(1))):
        return None
    return m.group(1)


def line_value(prompt):
    """The path on the brief's `Record folder:` line, less its quotes, a final `.` and `/`; None when there
    is no line. For a line that holds more than its path (line_reading's problem), the text up to the first
    space."""
    return line_reading(prompt)[0]


def line_reading(prompt):
    """(path, problem). The line holds one path and nothing else: backticks or straight quotes may wrap it
    (a space is allowed only inside them); trailing whitespace is fine. problem is None for such a line;
    else it says what is wrong (text after the path, or a quote that is not closed), and path is the text up
    to the first space. (None, None) when there is no line."""
    m = RECORD_FOLDER.search(prompt or "")
    if not m:
        return None, None
    v = m.group(2).rstrip()
    if v[0] in "`'\"":
        j = v.find(v[0], 1)
        if j < 0:
            return v.split()[0].strip("`'\"").rstrip(".").rstrip("/"), "opens a quote it does not close"
        path, rest = v[1:j], v[j + 1:]
    else:
        parts = v.split(None, 1)
        path, rest = parts[0], parts[1] if len(parts) > 1 else ""
    if rest.strip():
        return path.rstrip(".").rstrip("/"), ("holds text after its path (a path with a space goes in backticks; "
                                              "notes go on the next line)")
    # a path never holds a quote, a backtick, `*`, a format character or whitespace other than a quoted space:
    # refused, never stripped
    bad = next((c for c in path if c in "`'\"*" or unicodedata.category(c) == "Cf" or (c.isspace() and c != " ")), None)
    if bad:
        return path.rstrip(".").rstrip("/"), (f"holds {bad!r} (U+{ord(bad):04X}) in its path, which a path never holds; "
                                              "write the path alone, in backticks if it has a space")
    return path.rstrip(".").rstrip("/"), None


def care_path(t):
    """The path a labelled line names, for the in-care test, whatever the reader makes of the line: the first
    path after the label's colon (label markup and whitespace skipped; in quotes, the text inside them, an
    unclosed quote running to the line's end), less wrapping quotes and `*`; None when it is not full."""
    m = RECORD_LABEL.match(t)
    rest = (t[m.end():] if m else t).lstrip("*_").strip()
    if not rest:
        return None
    if rest[0] in "`'\"":
        j = rest.find(rest[0], 1)
        tok = rest[1:j] if j > 0 else rest[1:].strip()
    else:
        tok = rest.split()[0]
    tok = tok.strip("`'\"*")
    return tok if tok.startswith(("/", "~")) else None


def shown(path):
    home = str(Path.home())
    s = str(path)
    return "~" + s[len(home):] if s == home or s.startswith(home + "/") else s


def place(v):
    """A full path's record folder: (kind, root, folder, v, path) for a folder under the `reviews` folder
    of the bound repo the path itself is in (not the cwd's), or, for a binding in records mode, under runs/ in
    the records worktree of the path's project (root: that worktree); None when it is not. path is the line
    expanded, unresolved."""
    path = Path(os.path.normpath(Path(v).expanduser()))
    p = path.resolve()
    b = bound(path, named=True)
    mode, bad = runs_mode(b) if b else (None, None)
    reviews, why = reviews_folder(b) if mode == "tree" else (None, bad)
    if why:
        # the binding's own field is the cause, never the brief (R0.1 B-N4)
        where = team_roles.locate("binding", path, log, timeout=min(team_roles.RESOLVE_TIMEOUT, left()),
                                  env=NAMED_ENV, owner=True)[0]
        raise Unreadable(f"the binding {where or 'of the repository ' + str(path)} has a bad field "
                         f"`{'runs' if bad else 'reviews'}` ({why}): fix that field")
    if mode == "records":
        # records mode: a folder under runs/ in the project's records worktree (the path's own project)
        rec = records_of(path, named=True)
        if rec and (rec / locator.RUNS).resolve() in p.parents:
            return "repo", rec, p.relative_to(rec).as_posix(), v, str(path)
        return None
    if reviews:
        q = path
        while not q.exists() and q != q.parent:
            q = q.parent
        top = repo_root(str(q)).resolve()
        if (top / reviews).resolve() in p.parents:
            return "repo", top, p.relative_to(top).as_posix(), v, str(path)
    return None


def dispatch_decision(prompt, cwd, in_care, records=False):
    """The record folder a brief names, decided once, at dispatch: ("record", line) / ("refuse", why) /
    None (no line, or not this hook's: no line's full path is under a bound repository's reviews folder
    and in_care is False). Every line labelled as a record folder is read; a line round 1's reader cannot read, a
    path that is not full, a full path no check reads and two lines naming different folders are
    refused, each naming the line and where it resolves from the dispatch's cwd. records: the cwd's binding is in
    records mode, so a refusal names the records runs/ folder, not a reviews folder (R0.3 D5)."""
    texts = [m.group(0) + prompt[m.end():].split("\n", 1)[0] for m in RECORD_LABEL.finditer(prompt or "")]
    if not texts:
        return None
    top = repo_root(cwd).resolve()
    # in this hook's care, decided once, for every labelled line, before it is read: through its path,
    # under a bound repository's reviews folder
    for t in texts:
        c = care_path(t)
        if in_care or c is None:
            continue
        in_care = bool(place(c))
    places, refusals = [], []
    for t in texts:
        r = RECORD_FOLDER.match(t)
        v, problem = line_reading(t) if r else (None, None)
        if v is None:
            refusals.append(f"`{t.strip()}` is not in the form the record check reads")
        elif problem:
            refusals.append(f"`{t.strip()}` {problem}")
        elif not v.startswith(("/", "~")):
            refusals.append(f"`{t.strip()}` is not a full path: read from this working folder it would be "
                            f"{shown((top / v).resolve())}, and from another one somewhere else")
        else:
            got = place(v)
            p = Path(os.path.normpath(Path(v).expanduser())).resolve()
            if got:
                places.append(got)
            else:
                refusals.append(f"`{t.strip()}` resolves to {shown(p)}, which is not under "
                                + ("a project's records runs/ folder" if records
                                   else "a bound repository's reviews folder"))
    if not in_care:
        return None
    if not refusals and len({(k, str(root), f) for k, root, f, _, _ in places}) > 1:
        refusals.append(f"{len(texts)} `Record folder:` lines name different folders: "
                        + ", ".join(f"`{t.strip()}`" for t in texts))
    if refusals:
        return "refuse", "; ".join(refusals)
    return "record", places[0]


def record_line(prompt, cwd, binding):
    """The brief's `Record folder:` line, placed. None when there is no line, or when the line is not
    this hook's to check (no binding: the opt-in rule). Else (kind, root, folder, shown), shown being the
    path as written:
      "repo"     under the binding's `reviews`; root is the cwd's repository, folder is relative to it
      "refused"  not; root is the cwd's repository, folder is why
    A relative path is read from the cwd's repository root; `~`, `..` and symlinks are resolved. A
    "refused" line carries the path it resolved to last. Only a stop with no dispatch decision reads here."""
    v, problem = line_reading(prompt)
    if v is None:
        return None
    top = repo_root(cwd)
    p = Path(v).expanduser()
    p = (p if p.is_absolute() else top / p).resolve()
    if not binding:
        return None
    if problem:
        return "refused", top, f"which {problem}", v, p
    if runs_mode(binding)[0] == "records":
        rec = records_of(top)
        if rec and (rec / locator.RUNS).resolve() in p.parents:
            return "repo", rec, p.relative_to(rec).as_posix(), v
        return "refused", top, f"which is not under this project's {runs_text(binding, top)}", v, p
    reviews = reviews_folder(binding)[0]
    if reviews and (top.resolve() / reviews).resolve() in p.parents:
        return "repo", top, p.relative_to(top.resolve()).as_posix(), v
    return "refused", top, f"which is not under this repository's {reviews or 'reviews folder'}", v, p


def refusal(line):
    return f"your brief's `Record folder:` line names {line[3]}, {line[2]}"


def stop_line(prompt, cwd, binding):
    """(line, note) for a stop. line is the record folder decided at dispatch, never re-read from the
    stop's cwd: (kind, root, folder, shown, path), path being the line as written, expanded. Without a
    dispatch decision, round 1's reading from the cwd (path None), where a line no check can read is not
    refused but reported (note), and the stop runs as if the brief had no line."""
    rec = dispatch_record(prompt)
    if rec:
        return rec, None
    line = record_line(prompt, cwd, binding)
    if line and line[0] == "refused":
        return None, (f"bakehouse agent-roles: {refusal(line)} (it resolves to {shown(line[4])}), so the record "
                      "it names was not checked. Its folder was not decided at dispatch (a dispatch by the agent's "
                      "plain name, or one from before that check); name it as a full path, "
                      "`Record folder: ~/<path>/`.")
    return (line + (None,) if line else None), None


def with_note(out, note):
    """out, with the note added to what the user is told."""
    if not note:
        return out
    out = dict(out or {})
    out["systemMessage"] = f"{out['systemMessage']} {note}" if out.get("systemMessage") else note
    return out


def tables():
    """Role entries in lookup order: the owner's overrides, then the shipped table."""
    return [team_roles.table_roles(team_roles.personal_table()),
            team_roles.table_roles(team_roles.shipped_table())]


_USERS = {}


def users_at(cwd):
    """A function giving the names of the user's and the project's own agents for cwd (team_roles.user_agents): a
    bare agent name among them is that agent, never a plugin agent of the same name (R0.1 S6 #2). Read once per
    run, and only when asked; the project's folder is found by repo_root, inside the run's deadline."""
    def users():
        k = str(cwd)
        if k not in _USERS:
            _USERS[k] = team_roles.user_agents(repo_root(k))
        return _USERS[k]
    return users


def reviews_folder(binding):
    """(folder, None) for the binding's `reviews`, or (None, reason) when it is not a safe path."""
    v = binding.get("reviews", DEFAULT_REVIEWS)
    if not isinstance(v, str) or not v.strip():
        return None, f"reviews {v!r} is not a path"
    if os.path.isabs(v) or ".." in Path(v).parts:
        return None, f"reviews {v!r} is outside the repository"
    return v.rstrip("/") + "/", None


def runs_mode(binding):
    """(mode, None): where the binding keeps its run folders (locator.runs_mode: "records", runs/ in the records
    worktree, or "tree", its `reviews` in the code tree); (None, why) for a bad `runs` field."""
    try:
        return locator.runs_mode(binding), None
    except locator.BadRuns as e:
        return None, str(e)


_RECORDS = {}


def records_of(path, named=False):
    """The records worktree of the project of path (a path inside a records worktree: that project's), resolved,
    or None; asked once per run, inside the run's deadline, in the paths the binding lookup asks (lookup_paths:
    for the cwd, then the launch project). In records mode the run folders are runs/ in it."""
    k = (str(path), named)
    if k not in _RECORDS:
        rec = None
        for at in lookup_paths(str(path), named):
            rec = team_roles.locate("records", at, log, timeout=min(team_roles.RESOLVE_TIMEOUT, left()),
                                    env=NAMED_ENV, owner=True)[0]
            if rec:
                break
        _RECORDS[k] = Path(rec).resolve() if rec else None
    return _RECORDS[k]


def runs_text(binding, where):
    """The run folders' place as a brief names it: the binding's `reviews` (default axis/runs/), or, in records
    mode, the full path of runs/ in the records worktree of the project of where."""
    if binding and runs_mode(binding)[0] == "records":
        rec = records_of(where)
        return f"{shown(rec)}/{locator.RUNS}" if rec else locator.RUNS
    return reviews_folder(binding or {})[0] or DEFAULT_REVIEWS


def dispatch_runs_text(binding, cwd):
    """runs_text, for the text a dispatch writes into the agent's prompt: a bad `runs` or `reviews` field refuses
    the dispatch, naming the field and the binding file, as place() does (R0.3 B7), where runs_text would put
    axis/runs/ in its place without a word."""
    mode, bad = runs_mode(binding) if binding else (None, None)
    why = bad or (reviews_folder(binding)[1] if binding and mode == "tree" else None)
    if why:
        raise Unreadable(f"the binding {binding_file(cwd) or 'of the repository ' + str(cwd)} has a bad field "
                         f"`{'runs' if bad else 'reviews'}` ({why}): fix that field")
    return runs_text(binding, cwd)


def code_root(cwd, rec):
    """The code checkout a records-mode record's code paths are read in: the cwd's repository when it is a
    checkout of the project whose records worktree is rec, else that project's own folder."""
    top = repo_root(cwd)
    if top.resolve() != rec and records_of(top) == rec:
        return top
    root = team_roles.locate("root", str(rec), log, timeout=min(team_roles.RESOLVE_TIMEOUT, left()), env=NAMED_ENV,
                             owner=True)[0]
    return Path(root) if root else top


def is_above(model, ceiling):
    """True when model (None = inherit, which may be opus) is above the ceiling."""
    order = {"haiku": 0, "sonnet": 1, "opus": 2, "fable": 3}
    m = next((k for k in order if model and k in model), None)
    c = next((k for k in order if k in ceiling), None)
    if c is None:
        return False
    return m is None or order[m] > order[c]


def binding_file(cwd):
    """The binding file of the project at cwd, found as binding_at finds it (lookup_paths), for a message; None
    when the locator names none."""
    for at in lookup_paths(str(cwd), False):
        where = team_roles.locate("binding", at, log, timeout=min(team_roles.RESOLVE_TIMEOUT, left()), env=NAMED_ENV)[0]
        if where:
            return where
    return None


def read_already(cwd):
    """The cwd's binding when this run has read it already, else None: a dispatch whose decision never needed its
    binding never reads its `roles`."""
    return _BINDINGS.get((str(cwd), False), (None, None))[0]


def broken_tables(cwd, binding):
    """Why each role table a dispatch from cwd reads cannot be used (R0.1 S6 #3, #4): the personal table, and the
    `roles` of binding (the cwd's, when it was read)."""
    out = [w for w in [team_roles.table_problem(team_roles.personal_table())] if w]
    why = team_roles.binding_roles_problem(binding)
    if why:
        out.append(f"the binding {binding_file(cwd) or 'of the repository ' + str(cwd)} {why}")
    return out


def pre_tool_use(d):
    # The shipped table is the plugin's own: one that cannot be used is a bug in the plugin. The dispatch passes
    # unchanged, never read as naming no such role (R0.1 B-N2), and the user is told (R0.1 S6 #3).
    shipped = team_roles.table_problem(team_roles.shipped_table())
    if shipped:
        msg = (f"bakehouse agent-roles: {shipped}. That table is the plugin's own, so this is a bug in bakehouse: "
               "this dispatch passes unchanged, with no role rule applied. Reinstall or update the plugin.")
        log(f"ERROR {msg}")
        return {"systemMessage": msg}
    ti = dict(d.get("tool_input") or {})
    tabs = tables()
    st = ti.get("subagent_type") or "general-purpose"
    cwd = d.get("cwd") or os.getcwd()
    users = users_at(cwd)
    notes = []
    role_entry = None
    binding = None

    mark = False
    warn = None
    engineer = False
    before = ti.get("prompt")
    by_role = st.startswith("role:")
    # an agent's name from before R0.1 in bakehouse's own namespaces (role:, bakehouse:) is refused with its new
    # name (R0.3 B1); a bare old name, or another plugin's, may be someone's own agent: it is left as it was
    own_old = st.startswith("bakehouse:") and st[len("bakehouse:"):] in team_roles.RENAMED
    renamed = team_roles.renamed_hint(st[5:]) if by_role else team_roles.renamed_hint(st) if own_old else None
    if by_role or renamed:
        spec = st[5:] if by_role else st.split(":")[-1]
        binding = bound(cwd)
        broken = broken_tables(cwd, binding)
        if broken:
            # a role: dispatch passed unchanged reaches no agent; resolved without the broken table, it may be
            # another agent than the owner meant: it is refused, naming the file (R0.1 S6 #3)
            reason = (f"bakehouse agent-roles: role '{spec}' cannot be resolved, because a role table it reads cannot "
                      f"be used: {'; '.join(broken)}. Fix that file and dispatch again; nothing in the brief is the "
                      "cause.")
            log(f"DENY {reason}")
            refusal_event("dispatch:role-table", reason, before, cwd)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "permissionDecision": "deny",
                                           "permissionDecisionReason": reason}}
        lvls = team_roles.levels(binding)
        res = team_roles.resolve(spec, lvls, team_roles.installed_agents(cwd))
        for where, key, provs in res["skipped"]:
            log(f"SKIP role:{spec}: {where} key '{key}' names {provs}, not installed")
        if not res["agent"]:
            what = "no installed agent fills it" if team_roles.known_role(spec, lvls) else "no level names it"
            who = f"role '{spec}'" if by_role else f"agent '{st}'"
            then = f"{renamed}." if renamed else "Do the work in this session instead, behind the same gates."
            reason = f"bakehouse agent-roles: {who} is unresolved ({what}). {then}"
            log(f"DENY {reason}")
            refusal_event("dispatch:role-unresolved", reason, before, cwd)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "permissionDecision": "deny",
                                           "permissionDecisionReason": reason}}
        ti["subagent_type"] = st = res["agent"]
        src = "fallback, no level matched" if res["fallback"] else f"{res['where']} key '{res['key']}'"
        notes.append(f"role:{spec} -> {st} ({src})")
        role_entry = res["entry"]
        mark = spec in prediction_jobs()
        engineer = st in change_engineers(binding, cwd)
    elif st not in team_roles.BUILT_IN and prediction_jobs():
        b = bound(cwd)
        if b and any(team_roles.is_agent(st, a, users) for a in change_engineers(b, cwd)):
            engineer = True
            if work_order(ti.get("prompt"), b):
                mark, binding = True, b
            else:
                warn = (f"bakehouse agent-roles: {st} was dispatched by its plain name with no `Work order <id>:` "
                        "line, so the prediction stop check cannot tell whether this is a change job. A change "
                        "engineer is dispatched as role:change:engineer; `predict-gate.sh close` is the hard gate.")
                log(f"WARN {warn}")

    # The record folder of an agent the stop checks is decided here, once, from the cwd the PM dispatched
    # from, and logged for its stop; a line that cannot be decided is refused before any work runs.
    decided = None
    if mark or (team_roles.role_of(st, tabs, users)[1] or {}).get("run_record"):
        bcwd = binding if binding is not None else bound(cwd)
        records = bool(bcwd) and runs_mode(bcwd)[0] == "records"
        dec = dispatch_decision(before, cwd, mark or by_role or bool(bcwd), records)
        if dec and dec[0] == "refuse":
            under = (f"the records runs/ folder, {runs_text(bcwd, cwd)}" if records
                     else "a bound repository's reviews folder")
            reason = (f"bakehouse agent-roles: the stop check reads this agent's record folder from its brief's "
                      f"`Record folder:` line, decided now, at dispatch: {dec[1]}. Write one line, the full path "
                      f"of a folder under {under} (`Record folder: ~/<path>/`), and dispatch again.")
            log(f"DENY {reason}")
            refusal_event("dispatch:record-folder", reason, before, cwd)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "permissionDecision": "deny",
                                           "permissionDecisionReason": reason}}
        decided = dec[1] if dec else None

    # Without a `role:` dispatch, the SHIPPED table touches a plain agent name only in a repository
    # that has opted in with a binding: anywhere else a same-named agent may be the user's own
    # (review 2026-10-11, H2). The owner's own table (the personal one) is their explicit choice and
    # applies everywhere.
    if binding is None and (team_roles.role_of(st, tabs, users)[1]
                            or team_roles.role_of(d.get("agent_type"), tabs, users)[1]):
        binding = bound(cwd)
        if not binding:
            tabs = tabs[:1]
    rname, r = team_roles.role_of(st, tabs, users)
    if not r and role_entry:
        # an agent no table lists takes only the dispatched role's model
        r = {"model": role_entry.get("model")}
    if r:
        want = r.get("model")
        if isinstance(want, str) and want and ti.get("model") != want and APPROVED not in (ti.get("description") or ""):
            notes.append(f"model {ti.get('model')} -> {want}")
            ti["model"] = want
        extra = r.get("prompt_append")
        if isinstance(extra, str) and extra and MARKER not in (ti.get("prompt") or ""):
            if "{reviews}" in extra:
                if binding is None:
                    binding = bound(cwd)
                extra = extra.replace("{reviews}", dispatch_runs_text(binding, cwd))
            ti["prompt"] = (ti.get("prompt") or "") + f"\n\n{MARKER}\n{extra}"
            notes.append("prompt_append added")

    if mark and PREDICTION_MARKER not in (ti.get("prompt") or ""):
        reviews = dispatch_runs_text(binding, cwd)
        where = f"your record folder {decided[3]}/" if decided else f"the record folder your brief names under {reviews}"
        # the marker is the prompt's own final line: the stop check reads it there and nowhere else
        ti["prompt"] = (ti.get("prompt") or "") + (
            f"\n\nBefore you stop, {where} must hold the prediction as moves.prediction.json (a fix round: "
            "moves.prediction.<round>.json, for example moves.prediction.fix1.json), in the format "
            "`predict-gate.sh check` reads, naming at least one move (a round that moves nothing declares "
            "\"noMoves\": true, and is accepted before its round is measured, and beside its measured pair "
            "when that pair moved 0 keys). "
            "A prose prediction may sit beside it. "
            f"The stop hook sends you back once if it is missing, empty or does not parse.\n{PREDICTION_MARKER}")
        notes.append(f"prediction-record rule added ({decided[3] if decided else 'no Record folder line'})")

    if engineer or decided:
        record_dispatch(st, before, ti.get("prompt"), mark if engineer else None,
                        decided and dict(zip(("kind", "root", "folder", "line", "path"), map(str, decided))))

    parent = d.get("agent_type")
    if parent:
        pname, pr = team_roles.role_of(parent, tabs, users)
        cap = (pr or {}).get("nested_model")
        if isinstance(cap, str) and cap and is_above(ti.get("model"), cap):
            notes.append(f"nested spawn by {parent}: model {ti.get('model')} -> {cap}")
            ti["model"] = cap

    if not by_role:
        # a broken table was skipped, and the rest applied: the user is told, naming the file (R0.1 S6 #3)
        skipped = broken_tables(cwd, read_already(cwd))
        if skipped:
            msg = (f"bakehouse agent-roles: {'; '.join(skipped)}. This dispatch skipped that table and used the "
                   "other role tables, so the shipped table's rules still apply; fix that file.")
            log(f"WARN {msg}")
            warn = f"{warn} {msg}" if warn else msg

    if not notes:
        return {"systemMessage": warn} if warn else None
    log(f"REWRITE {st} ({ti.get('description', '')}): " + "; ".join(notes))
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                  "permissionDecision": "allow",
                                  "updatedInput": ti}}
    if warn:
        out["systemMessage"] = warn
    return out


def team_dispatch(d):
    """True when the dispatch is the team's: a role: dispatch, an agent a role table lists (dispatched, or
    dispatching), or a brief with a `Work order <id>:` line or a line labelled as a record folder."""
    ti = d.get("tool_input") or {}
    st, prompt, tabs = ti.get("subagent_type") or "general-purpose", ti.get("prompt") or "", tables()
    users = users_at(d.get("cwd") or os.getcwd())
    return (st.startswith("role:") or any(team_roles.role_of(a, tabs, users)[0] for a in (st, d.get("agent_type")))
            or bool(WORK_ORDER.search(prompt) or RECORD_LABEL.search(prompt)))


def on_pre_tool_use(d):
    """pre_tool_use, refused when a binding its decision needs cannot be read: the reason names the cause
    and what to fix, never the brief. A project that needs setup refuses only a team dispatch; any other
    dispatch passes unchanged (R0.1 S15-2: a clone with a binding and no records pointer)."""
    try:
        return pre_tool_use(d)
    except Unreadable as e:
        if isinstance(e.args[0], team_roles.SetupProblem) and not team_dispatch(d):
            log(f"PASS a dispatch that is not the team's, unchanged: {e}")
            return None
        reason = (f"bakehouse agent-roles: this dispatch cannot be decided, because the hook cannot read its own "
                  f"input: {e}. Nothing in the brief is the cause; fix that and dispatch again.")
        log(f"DENY {reason}")
        prompt = (d.get("tool_input") or {}).get("prompt") or ""
        # the event is kept in the log, never sent through the working folder's ledger, which is found through
        # the input that failed
        refusal_event("dispatch:binding-unreadable", reason, prompt, d.get("cwd") or os.getcwd(), keep=True)
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}


def repo_root(cwd):
    wait = min(2, left())
    if wait <= 0:  # past the run's deadline: read as git failing
        return Path(cwd)
    try:
        out = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=wait)
        if out.returncode == 0:
            return Path(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return Path(cwd)


def run_started(d):
    try:
        st = os.stat(d["agent_transcript_path"])
        return getattr(st, "st_birthtime", st.st_mtime)
    except (KeyError, OSError):
        return time.time() - 3600


def fresh_folders(runs, started):
    """Record folders under runs with a file written since the agent started."""
    if not runs.is_dir():
        return []
    return sorted({p.parent for p in runs.glob("*/*") if p.is_file() and p.stat().st_mtime >= started - 5})


def first_prompt(path):
    """The dispatch prompt: the first record of the agent's transcript."""
    try:
        with open(path, encoding="utf-8") as f:
            rec = json.loads(f.readline())
        c = rec["message"]["content"]
    except (OSError, ValueError, TypeError, KeyError):
        return ""
    if isinstance(c, list):
        c = " ".join(b.get("text", "") for b in c if isinstance(b, dict))
    return c if isinstance(c, str) else ""


def unsealed_moves(folders, where, named, wo=None):
    """The top-level moves files of the record folders whose bytes no seal in their project's ops ledger names:
    the project of `where`, the path the hook's binding lookup used (the cwd, or the record line's path:
    named), found by the binding's own rule (lookup_paths). Returns [(file, why the gate refuses its seal now, or
    None)]. With the work order wo, each file is judged by predict-gate's seal_refusal, the seal's own rules: a
    file whose refusal no action can clear (a round's prediction or baseline after its measured seal) is logged
    as "cannot be sealed" and not listed; any other refusal is listed with the gate's text. Fail-open, like the
    rest of this hook: an error reads as sealed, and says so."""
    try:
        gate = predict_gate()
        led, why = None, None
        for at in lookup_paths(where, named) if where else []:
            led, why = team_roles.locate("ops_ledger", at, log, env=NAMED_ENV, owner=named)
            if led or why:
                break
        if not led:
            log(f"SEAL check skipped: no project for {where}" + (f" ({why})" if why else ""))
            return []
        ol = gate.ledger_module()
        led = Path(led)
        events = list(map(ol.parse, ol.lines_of(led.read_bytes() if led.is_file() else b"")))
        sealed = {e.get("sha256") for e in events if isinstance(e, dict) and e.get("kind") in ("sealed", "revised")}
        out = []
        for p in [p for f in folders for p in (sorted(f.iterdir()) if f.is_dir() else [])
                  if p.is_file() and gate.SEAL_FILE.match(p.name) and ol.line_sha(p.read_bytes()) not in sealed]:
            why = None
            if wo:
                try:
                    kind, rnd = gate.file_round(p.name)
                    why, final = gate.seal_refusal(p, f"{p.parent.name}/{p.name}", wo, rnd, kind,
                                                   ol.line_sha(p.read_bytes()), events) or (None, False)
                except gate.BadInput as e:
                    why, final = str(e), False
                if final:
                    log(f"SEAL {p.parent.name}/{p.name} cannot be sealed, so the stop does not ask for it: {why}")
                    continue
            out.append((p, why))
        return out
    except Exception as e:  # noqa: BLE001 - fail-open: the stop is never blocked by this check's own error
        log(f"SEAL check skipped: {e!r}")
        return []


def prediction_stop(d):
    """The change engineer may stop only when its own record folder holds a prediction file that
    predict-gate's own checker accepts and that names at least one move, or that declares "noMoves": true:
    accepted while its round has no measured file yet, and beside its measured pair when that pair moved 0 keys
    (predict-gate's no_move_refusal)."""
    prompt = first_prompt(d.get("agent_transcript_path"))
    lines = [x.strip() for x in prompt.splitlines() if x.strip()]
    marked = bool(lines) and lines[-1] == PREDICTION_MARKER
    agent = d.get("agent_type")
    cwd = d.get("cwd") or os.getcwd()
    users = users_at(cwd)
    binding = binding_at(cwd)[0]
    line, note = stop_line(prompt, cwd, binding)
    kind = line[0] if line else None
    if kind == "repo" and line[4]:
        # decided at dispatch: the bound repo the line names, not the stop's cwd
        binding = binding_at(line[4], named=True)[0]
    if not binding:
        if marked:
            log(f"PREDICTION check skipped for {agent}: no binding for {cwd}")
        # a line the fallback cannot place, in a change job's brief, is reported
        if note and any(team_roles.is_agent(agent, a, users) for a in change_engineers(binding, cwd)) \
                and dispatched(prompt) is not False and (
                marked or (work_order(prompt, binding) and agent not in team_roles.BUILT_IN)):
            return with_note(None, note)
        return None
    if not any(team_roles.is_agent(agent, a, users) for a in change_engineers(binding, cwd)):
        return None
    log(f"PROMPT-SOURCE {agent}: transcript line 1 {'ends' if marked else 'does not end'} with "
        f"{PREDICTION_MARKER} (a marked dispatch shows here whether line 1 holds the rewritten prompt)")
    wo = work_order(prompt, binding)
    job = dispatched(prompt)
    if job is False or (job is None and not marked and (not wo or agent in team_roles.BUILT_IN)):
        if wo:
            log(f"PREDICTION check not run for {agent}: " + ("dispatched as a job that is not a change job"
                if job is False else "a built-in agent, unmarked"))
        return None
    mode, why = runs_mode(binding)
    if mode == "tree":
        reviews, why = reviews_folder(binding)
    else:
        reviews = None if why else runs_text(binding, cwd)
    if not reviews:
        log(f"PREDICTION check skipped for {agent}: binding {why}")
        return with_note(None, note)
    repo = repo_root(cwd)
    gate = predict_gate()
    if kind == "repo":
        folders, named = [line[1] / line[2]], line[2]
    elif wo and mode == "records":
        rec = records_of(repo)
        folders = gate.record_folders(rec, binding, wo, locator.RUNS)[1] if rec else []
        named = f"the {reviews} folder of work order {wo}"
    elif wo:
        folders, named = gate.record_folders(repo, binding, wo)[1], f"the {reviews} folder of work order {wo}"
    else:
        folders, named = [], None
    good, bad = [], []
    for f in folders:
        for p in sorted(f.iterdir()) if f.is_dir() else []:
            if p.is_file() and gate.PREDICTION_RE.match(p.name):
                try:
                    pred = gate.check_prediction(gate.load(p), p)
                    rnd = gate.PREDICTION_RE.match(p.name).group(1)
                    if pred["noMoves"] and not (f / f"moves.measured{rnd}.json").is_file():
                        # the prediction is written before the round is measured, so its measured file does
                        # not exist yet: accepted here; close is the hard gate, and needs the pair (R0.1 S6 #1)
                        good.append(p)
                        continue
                    moved = None if pred["moves"] else gate.pair_moved(
                        p.name, pred, lambda n, f=f: (f / n).is_file(), lambda n, f=f: (f / n).read_text(encoding="utf-8"))
                    why = gate.no_move_refusal(len(pred["moves"]), pred["noMoves"], moved)
                    if why:
                        bad.append(f"{p.name} {why}")
                    else:
                        good.append(p)
                except gate.BadInput as e:
                    bad.append(str(e))
    if good and not bad:
        decided = kind == "repo" and bool(line[4])
        unsealed = unsealed_moves(folders, line[4] if decided else cwd, decided, wo)
        if not unsealed:
            log(f"PREDICTION ok for {agent}: {', '.join(p.parent.name + '/' + p.name for p in good)}")
            return with_note(None, note)
        what = (f"moves file(s) with no seal in the ops ledger: "
                f"{', '.join(p.parent.name + '/' + p.name for p, _ in unsealed)}")
        # the "seal each one" list names only the files the gate would seal now (R0.3 D8)
        sealable = [f"{p.parent.name}/{p.name}" for p, why in unsealed if not why]
        if sealable:
            what += (f". Seal each one as you write it (predict-gate.sh seal {wo or '<work order>'} <round> <file>): "
                     + ", ".join(sealable))
        refused = [f"{p.parent.name}/{p.name}: {why}" for p, why in unsealed if why]
        if refused:
            what += ". The gate refuses these seals as the files are now: " + "; ".join(refused)
        if not d.get("stop_hook_active"):
            log(f"SEAL missing for {agent} — sent back: {what}")
            out = with_note({"decision": "block", "reason": f"bakehouse agent-roles: this change job has {what}, "
                                                            "then stop."}, note)
            refusal_event("stop:seal", out["reason"], prompt, folders[0] if folders else cwd)
            return out
        log(f"SEAL still missing for {agent} — let it stop, told the user")
        return with_note({"systemMessage": f"bakehouse agent-roles: {agent} stopped with {what}. The work order "
                                           "cannot close until they are sealed."}, note)
    if bad:
        what = "; ".join(bad)
    elif named is None:
        what = "your brief names no record folder (no 'Record folder:' line and no 'Work order <id>:' line)"
    else:
        what = f"{named} holds no moves.prediction.json"
    if not d.get("stop_hook_active"):
        log(f"PREDICTION missing or bad for {agent} — sent back: {what}")
        out = with_note({"decision": "block",
                         "reason": f"bakehouse agent-roles: this change job needs a machine prediction before you "
                                   f"stop: {what}. Write moves.prediction.json (a fix round: "
                                   f"moves.prediction.<round>.json) in the record folder your brief names, in the "
                                   f"format `predict-gate.sh check` reads, then stop."}, note)
        refusal_event("stop:prediction", out["reason"], prompt, folders[0] if folders else cwd)
        return out
    log(f"PREDICTION still missing or bad for {agent} — let it stop, told the user")
    return with_note({"systemMessage": f"bakehouse agent-roles: {agent} stopped without a machine prediction the "
                                       f"stop check accepts: {what}. It was let stop this time; "
                                       "`predict-gate.sh close` is the hard gate, and checks the round's prediction "
                                       "again."}, note)


def records_check():
    import importlib.util
    spec = importlib.util.spec_from_file_location("records_check", Path(__file__).resolve().with_name("records-check.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DiskReader:
    """records-check's Reader, on the working tree: the stop check runs before the record is committed."""

    def __init__(self, root):
        self.root = root

    def text(self, path):
        return Path(path).read_text(encoding="utf-8", errors="replace")

    def exists(self, path):
        return (self.root / path).exists()

    def file_text(self, path):
        p = self.root / path
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None


def record_problems(rname, root, folder, started, binding, code=None):
    """[what is wrong] in the record folders this run wrote, against the role's job contract; [] when
    the role is not a job in the record contracts. code: the checkout the record's code paths (tests, gate files)
    are read in, when it is not root (records mode). Fail open: an error is logged and passes."""
    try:
        rc = records_check()
        contracts = rc.load_contracts()
        jobs = rc.merged_jobs(binding, contracts)
        job = jobs.get(rname)
        if not job or not any(r.get("active") is not False for r in job["require"]):
            return []
        runs = root / folder
        made, wrote = {}, {}
        for q in runs.rglob("*"):
            if q.is_file() and q.stat().st_mtime >= started - 5:
                wrote.setdefault(q.parent, set()).add(q.name)
                if any(fnmatch.fnmatchcase(q.name, g) for g in job["marks"]):
                    made.setdefault(q.parent, set()).add(q.name)
        others = rc.claimed(jobs, rname)
        # a findings file under a name no round declares is read by nothing: refuse it while it can be renamed
        out = [f"{f.relative_to(root)}: {n} is not a declared findings name; write it as {exp} and its rows as "
               f"{mut} (record-contracts.json, rounds)"
               for f in sorted(wrote) for n, exp, mut in rc.misnamed([q.name for q in f.iterdir() if q.is_file()],
                                                                     job, others) if n in wrote[f]]
        if not made:
            return out
        code = code or root
        gates = rc.gate_ids(binding, lambda x: (code / x).read_text(encoding="utf-8", errors="replace")
                            if (code / x).is_file() else None)
        read = DiskReader(code)
        roots = rc.test_roots(binding, contracts)
        for f in sorted(made):
            files = {q.name: q for q in f.iterdir() if q.is_file()}
            out += [f"{f.relative_to(root)}: {what}"
                    for _, _, what in rc.check_folder(files, job, read, gates, roots=roots, open_ok=True)]
            # a findings file this run wrote owes rows this run wrote, not an earlier round's file
            out += [f"{f.relative_to(root)}: {n} was not written during this run (owed by {m})"
                    for r in job["require"] if r.get("per") and r.get("active") is not False
                    for m in sorted(made[f]) if fnmatch.fnmatchcase(m, r["per"])
                    for n in [rc.owed_name(r, m)] if n in files and files[n].stat().st_mtime < started - 5]
            # a review writes its contract first: beside its findings file, or in a folder above it in the record
            above = [f] + [p for p in f.parents if p == runs or runs in p.parents]
            if rname == "reviewer" and not any(q.is_file() for d in above for q in d.glob("*contract*.md")):
                out.append(f"{f.relative_to(root)}: no contract.md here or in a folder above it, inside {folder}")
        return out
    except Exception as e:  # fail open, as the hook does
        log(f"ERROR record check for {rname}: {e!r}")
        return []


def marked(rname, folder, started, binding):
    """([folder] when a file written since the agent started, at any depth under it, matches one of the
    role's job marks (record-contracts.json, with the binding's jobs), else []; and those marks. A role
    that is not a job there has no marks (None): any new file counts. Fail open: an error is logged
    and any new file counts."""
    try:
        rc = records_check()
        job = rc.merged_jobs(binding, rc.load_contracts()).get(rname)
        marks = job["marks"] if job else None
    except Exception as e:  # noqa: BLE001 - fail open, as the hook does
        log(f"ERROR record marks for {rname}: {e!r}")
        marks = None
    hit = folder.is_dir() and any(
        q.is_file() and q.stat().st_mtime >= started - 5
        and (marks is None or any(fnmatch.fnmatchcase(q.name, g) for g in marks)) for q in folder.rglob("*"))
    return ([folder] if hit else []), marks


def subagent_stop(d):
    out = prediction_stop(d)
    if out:
        return out
    tabs = tables()
    users = users_at(d.get("cwd") or os.getcwd())
    rname, r = team_roles.role_of(d.get("agent_type"), tabs, users)
    if not r or not r.get("run_record"):
        return None
    personal = bool(team_roles.role_of(d.get("agent_type"), tabs[:1], users)[1])
    started = run_started(d)
    cwd = d.get("cwd") or os.getcwd()
    binding = binding_at(cwd)[0]
    # The line's folder is the one decided at dispatch; a repo record decided there is checked in that
    # folder alone, with the binding of the repo it names, whatever the stop's cwd.
    prompt = first_prompt(d.get("agent_transcript_path"))
    line, note = stop_line(prompt, cwd, binding)
    kind = line[0] if line else None
    rroot, rbind = ((line[1], binding_at(line[4], named=True)[0]) if kind == "repo" and line[4]
                    else (repo_root(cwd), binding))
    # checks: (root, folder, binding); fresh None = no run record demanded; want: the marks a decided folder lacks
    checks, named, fresh, want = [], None, None, None
    # The shipped table demands a record only in a repository that has opted in with a binding and
    # that the owner owns (or whose binding names a reviews folder). A rule in the owner's own table
    # is their explicit choice and applies everywhere (review 2026-10-11, H1).
    mode = None
    if personal or (rbind and (rbind.get("owned") is True or "reviews" in rbind)):
        mode, why = runs_mode(rbind) if rbind else ("tree", None)
        folder, why = reviews_folder(rbind) if mode == "tree" else (None if why else locator.RUNS, why)
        if mode == "records" and folder and not (kind == "repo" and line[4]):
            rroot = records_of(rroot)  # the run folders are runs/ in the records worktree
            folder, why = (folder, None) if rroot else (None, "has no records worktree the locator finds")
        if not folder:
            log(f"RUN-RECORD check skipped for {d.get('agent_type')}: binding {why}")
        elif kind == "repo" and line[4]:
            # decided at dispatch: that folder alone, never another work order's under the same reviews
            named, rf = line[3], rroot / line[2]
            checks.append((rroot, line[2], rbind))
            # written when a new file at any depth matches the job's marks
            fresh, want = marked(rname, rf, started, rbind)
        else:
            checks.append((rroot, folder, rbind))
            if kind is None or kind == "repo":
                fresh = fresh_folders(checks[-1][0] / folder, started)
    if not checks:
        return with_note(None, note)
    code = code_root(cwd, checks[0][0].resolve()) if mode == "records" else None
    bad = [x for root, f, b in checks for x in record_problems(rname, root, f, started, b, code)]
    fix = (f" Role '{rname}' must also leave its job's artefacts in each record folder it wrote: "
           + "; ".join(bad) + ". A reviewer writes mutations.json beside findings.md (a re-check's "
           "findings-fix1.md: its own mutations-fix1.json, written in this run): one row per mutation "
           "performed, {id, mutation, site, gate, verdict, disposition}; a survivor's disposition is "
           "`open: <finding id>` until someone decides, `test-added <test file or path::name>`, or "
           "`accepted: <reason>` stating its reach as a count.") if bad else ""
    if fresh is None and not bad:
        return with_note(None, note)
    if fresh and not bad:
        log(f"RUN-RECORD ok for {d.get('agent_type')}: {fresh[0].name}")
        return with_note(None, note)
    # where the record is: the folder decided at dispatch, else the working directory
    where = (line[1] / line[2]) if kind == "repo" and line[4] else cwd
    if fresh or fresh is None:
        if not d.get("stop_hook_active"):
            log(f"RECORD-ARTEFACTS missing or bad for {d.get('agent_type')} — sent back: {'; '.join(bad)}")
            refusal_event("stop:record-artefacts", "bakehouse agent-roles:" + fix, prompt, where)
            return with_note({"decision": "block", "reason": "bakehouse agent-roles:" + fix}, note)
        log(f"RECORD-ARTEFACTS still missing or bad for {d.get('agent_type')} — let it stop, told the user")
        return with_note({"systemMessage": f"bakehouse agent-roles: {d.get('agent_type')} stopped with its record "
                                           f"incomplete: {'; '.join(bad)}."}, note)
    if not d.get("stop_hook_active"):
        log(f"RUN-RECORD missing for {d.get('agent_type')} — sent back")
        refusal_event("stop:run-record", f"bakehouse agent-roles: role '{rname}' requires a run record"
                      + (f" in {named}/" if named else "") + "." + fix, prompt, where)
        return with_note({"decision": "block",
                          "reason": f"bakehouse agent-roles: role '{rname}' requires a run record. Before stopping, "
                                    + (f"write contract.md and findings.md in the record folder your brief names, "
                                       f"{named}/, or in a subfolder of it, as your dispatch prompt describes"
                                       + (f": no file written in this run there matches {' or '.join(want)}." if want
                                          else ".") if named else
                                       (f"write {checks[-1][1]}<YYYY-MM-DD>-<subject>-<protocol>/ at the repository "
                                        "root" if mode != "records" else f"write {shown(checks[-1][0])}/{checks[-1][1]}"
                                        "<YYYY-MM-DD>-<subject>-<protocol>/ in the records worktree")
                                       + " with contract.md and findings.md, as your dispatch prompt describes.")
                                    + fix}, note)
    log(f"RUN-RECORD still missing for {d.get('agent_type')} — let it stop, told the user")
    return with_note({"systemMessage": f"bakehouse agent-roles: {d.get('agent_type')} stopped without writing a record "
                                       f"{'in ' + named if named else 'under ' + checks[-1][1]}."
                                       + (f" Its record is also incomplete: {'; '.join(bad)}." if bad else "")}, note)


def check_new(cwd):
    have = team_roles.installed_agents(cwd)
    entries = [(rn, r) for t in tables() for rn, r in t.items() if isinstance(r, dict)]
    known = {p for _, r in entries for p in team_roles.providers(r)}
    try:
        seen = set(SEEN.read_text().split())
        first = False
    except OSError:
        seen, first = set(), True
    # A provider that has gone missing while its plugin is still installed is almost always a rename
    # upstream. Said every session until the table is fixed. A plugin that is not installed at all is
    # the normal case for someone without it, so that stays silent.
    # A missing shipped agent is said only in a repository with a binding: someone who does not use
    # the team may have removed the agents on purpose.
    plugins = {n.split(":")[0] for n in have if ":" in n}
    bound = bool(team_roles.load_binding(cwd, log))
    gone = [(rn, p) for rn, r in entries for p in team_roles.providers(r)
            if p not in have and (p.split(":")[0] in plugins if ":" in p else bound)]
    for rn, p in gone:
        print(f"[bakehouse] Agent role '{rn}': provider '{p}' is not installed"
              + (f", but the {p.split(':')[0]} plugin is — it was probably renamed or removed. "
                 if ":" in p else " — reinstall the plugin. ")
              + "Tell the owner, and offer to update the role table that names it "
                f"(runtime/agent-roles.json or {team_roles.personal_table()}).")
    new = sorted(have - seen - known - team_roles.BUILT_IN)
    if new and not first:
        print("[bakehouse] New agent(s) installed since the last check: " + ", ".join(new) + ". "
              "If one does the same job as a bakehouse role (runtime/agent-roles.json), tell the owner and "
              "offer to add it to that role's providers. It is shown only once.")
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text("\n".join(sorted(seen | have)) + "\n")


def main():
    if team_roles is None:
        return
    if "--check-new" in sys.argv:
        try:
            # the project, never the process cwd first: after a compaction it is wherever the shell last was
            check_new(str(locator.hook_folder(os.environ, sys.stdin)))
        except Exception as e:  # never break session start
            log(f"ERROR check-new: {e!r}")
        return
    try:
        d = json.load(sys.stdin)
        ev = d.get("hook_event_name")
        out = on_pre_tool_use(d) if ev == "PreToolUse" else subagent_stop(d) if ev == "SubagentStop" else None
    except Exception as e:  # fail open
        log(f"ERROR: {e!r}")
        out = None
    if out:
        print(json.dumps(out))


if __name__ == "__main__":
    main()
