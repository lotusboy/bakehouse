#!/usr/bin/env python3
"""The decision ledger: every question put to the owner, the agent's recommendation and confidence,
the owner's choice, and what happened later. Scores agreement and calibration per category, and says
when a category has earned autonomy. Pure computation; no AI. Called through runtime/decision.sh.

Ledger: <records>/decisions/ledger.jsonl, append-only; the source of truth for scoring. Three event
kinds share one decision id:
  ask      the question, options, recommendation, confidence, evidence, blind, autonomous
  answer   the owner's choice and whether it agreed with the recommendation. An assumed answer
           (`answer --assumed`, unattended mode) is the recommendation, recorded by the PM with
           "assumed": true and its estTokens; it is left out of the scores and approves no gate
           until the owner answers.
An ask may name its approving option (--approves): the one choice that approves an exception to
`predict-gate.sh close`. Agreeing with the recommendation is not approval.
  outcome  later evidence that the decision held, was reversed, or was refined
One more event kind carries no decision id: `unattended` (action start, with why and wave; or stop),
the switch for unattended mode. The project turns the mode on in its config ("unattended").
Config: runtime/decision-config.json, overridden per project by .team/decision-config.json.
Human view: <records>/DECISIONS.md, regenerated from the ledger after every write (never edited by
hand). The project is found by runtime/locator.py.
"""
import argparse
import datetime
import hashlib
import json
import os
import random
import re
import sys
from pathlib import Path

try:
    import fcntl
except ImportError:  # no fcntl on native Windows: single-writer behaviour, as before
    fcntl = None

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import locator  # noqa: E402

EVIDENCE_KINDS = ("measured", "blind-readers-agreed", "source-read", "inference")
VERDICTS = ("held", "reversed", "refined")
# A question rests on a source the asker opened, not on an agent's report about it (owner, 2026-10-14).
REPORT_NAMES = re.compile(r"(^|[-_.])(report|findings|result|prediction|disposition|contract|pass\d|handback)"
                          r"([-_.]|$)", re.I)
# A blind-readers run writes its readers' reports as reader-<x>.md and its comparison as agreement.md, and
# ratify.sh compare writes agreement.json beside them (skills/blind-readers), side by side in its run folder.
# Only there are those names an agent's record.
READER_REPORT = re.compile(r"reader-.+\.md", re.I)
# Only a text record can be an agent's report: a contract.pdf or a report.csv is source material.
REPORT_SUFFIXES = (".md", ".txt", ".json", ".jsonl")
# Words that mark an inference. A question using one must label it: "guess:" (owner, 2026-10-14).
GUESS_WORDS = ("probably", "likely", "should", "infer", "inferred", "inferring", "heuristic",
               "guess", "presumably", "assume", "assumed", "seems", "appears to", "works it out")
# "Should we ship it?" is how a yes/no question is asked, not an inference: a sentence-opening
# "should" is not counted. Neither is "pattern", which names things more often than it guesses.
LEADING_SHOULD = re.compile(r"(^|[.?!;]\s+)should\b")
FIX_WORDS = re.compile(r"\b(fix|fixes|fixing|workaround)\b")
CHANGE_WORDS = re.compile(r"\b(change|changes|changed|changing)\b")
AREAS_LISTED = 10  # a new area lists at most this many areas already recorded
GUESS_LABEL = re.compile(r"\bguess\s*:", re.I)
# A word inside quotation marks is named, not used: "refuses the word 'should'" asserts nothing.
QUOTED = re.compile(r"(?<!\w)'[^']*'(?!\w)|\"[^\"]*\"|\u2018[^\u2019]*\u2019|\u201c[^\u201d]*\u201d")
BANDS = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0001))


_PROJECT = None  # the project resolve_project found, for the refusal event
_TASK = None


def fail(msg, gate=None):
    """Refuse: exit 1 with the message. A refusal with a gate id first writes one gate-refused event to
    the project's ops ledger (docs/PROJECT-TEAM.md, "Gate refusals"); a failed write changes nothing."""
    if gate and _PROJECT:
        try:
            import gate_event
            gate_event.refused(gate, "decision.sh refused", f"decision: {msg}", task=_TASK, path=_PROJECT.root)
        except Exception:  # noqa: BLE001, S110 - the event reports a refusal; it never changes one
            pass
    sys.exit(f"decision: {msg}")


def now():
    if os.environ.get("DECISION_NOW"):
        return datetime.datetime.fromisoformat(os.environ["DECISION_NOW"])
    return datetime.datetime.now().astimezone()


def stamp():
    return os.environ.get("DECISION_TS") or now().isoformat(timespec="seconds")


def backfill():
    return os.environ.get("DECISION_BACKFILL") == "1"


# ---------- project, config, ledger ----------

def resolve_project(repo, write=False):
    """The project of --repo, else of the current folder (runtime/locator.py). write: the command appends to the
    ledger, so its records must name a file to append to (locator.writer_file: in records format 2, a writer id)."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"} if repo else os.environ
    try:
        project = locator.find(repo, env)
        if write and project is not None:
            project.write_file("decisions_ledger")
    except locator.SetupNeeded as e:
        fail(str(e))
    if project is None:
        fail(f"{repo or os.getcwd()} has no team (no .team/binding.json); pass --repo")
    global _PROJECT
    _PROJECT = project
    return project


def name_of(project):
    """The project's short name, for a row, a heading or a message (its repository folder's name)."""
    return project.root.name


def decisions(project):
    """(decision id -> {ask, answers, outcomes}, the ledger file) for the project; read by predict-gate."""
    return fold(read_events(project)), project.decisions_ledger


def merge(base, over):
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def config(project):
    cfg = json.loads((HERE / "decision-config.json").read_text())
    p = project.decision_config if project else None
    if p and p.exists():
        cfg = merge(cfg, json.loads(p.read_text()))
    return cfg


def categories(project):
    """The project's categories: its own list in .team/decision-config.json replaces the default one. A list of
    another shape is refused: null would stop the tool, and a text would be matched one character at a time."""
    cats = config(project).get("categories")
    if not isinstance(cats, list) or not cats or not all(isinstance(c, str) for c in cats):
        fail(f'"categories" in {project.decision_config} is not a non-empty list of text: fix it there',
             "decision:categories-config")
    return cats


def read_events(project):
    """Every event, from the ledger's files in read order (locator.ledger_files): the ledger itself, then in
    records format 2 each writer's file."""
    rows = []
    for p in project.read_files("decisions_ledger"):
        if not p.exists():
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    print(f"decision: {p} line {n} is not JSON; skipped", file=sys.stderr)
    return rows


class locked:
    """Hold the project's one lock: two writers must not interleave a read-modify-write of
    DECISIONS.md (review 2026-10-11, M3). The lock is a lock file beside the ledger,
    `.ledger.jsonl.lock`, which .gitignore names: the ledger itself is tracked in git, and a checkout,
    merge or reset replaces it while a writer holds a lock on the old file. Re-entrant in one process."""
    depth = 0

    def __init__(self, project):
        ledger = project.decisions_ledger
        self.path = ledger.with_name(f".{ledger.name}.lock")

    def __enter__(self):
        locked.depth += 1
        if locked.depth > 1 or fcntl is None:
            self.f = None
            return self
        self.path.parent.mkdir(exist_ok=True)
        self.f = self.path.open("a")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        locked.depth -= 1
        if self.f:
            self.f.close()


def write_whole(path, text):
    """Replace a file in one step, so a reader never sees half of it."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def append(project, event):
    with locked(project):
        _append(project, event)
    if locator.records_format(project.records) != locator.FORMAT:
        return  # format 1 commits nothing, and needs no more of the runtime than locator
    # records format 2: this writer's file, committed by path and synced (records_sync.commit_own)
    import records_sync
    what = event.get("id") or event.get("action") or ""
    records_sync.commit_own(project.records, project.write_file("decisions_ledger"),
                            f"decisions: {event.get('kind')} {what} (decision.sh)",
                            json.dumps(event, ensure_ascii=False) + "\n")


def _append(project, event):
    p = project.write_file("decisions_ledger")  # the ledger, or this writer's own file; resolve_project(write=True) checked it
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(json.dumps(event, ensure_ascii=False), file=sys.stderr)
    render(project)


def heading_question(line):
    """The question of a QUESTIONS.md heading, as an older ask's questionsRef keeps it."""
    q = re.sub(r"^## Q\s*[—-]\s*", "", line)
    return re.split(r"\s+\(raised\b|\s+\[(OPEN|CLOSED|DECIDED)", q)[0].strip()


def fold(events):
    """id -> {ask, answers, outcomes}. Later answers do not erase earlier ones; the latest one counts. Two passes,
    asks first: in records format 2 an answer in one writer's file can come, in read order, before its ask in
    another's."""
    out = {}
    for e in events:
        if e.get("kind") == "ask":
            out[e["id"]] = {"ask": e, "answers": [], "outcomes": []}
    for e in events:
        if e.get("kind") != "ask" and e.get("id") in out:
            out[e["id"]]["answers" if e["kind"] == "answer" else "outcomes"].append(e)
    return out


# ---------- record ----------

def cmd_ask(a):
    project = resolve_project(a.repo, write=True)
    loose = backfill()
    if not a.category.strip():
        fail("--category is empty", "decision:category")
    if not (a.question or "").strip():
        fail("--question is empty", "decision:question")
    if a.blind and a.autonomous:
        fail("a decision is either asked blind or decided autonomously, not both", "decision:blind-autonomous")
    if len(a.option) < 2 and not loose:
        fail("give at least two --option values (yes/no counts as two)", "decision:options")
    if not a.recommendation and not loose:
        fail("--recommendation is required", "decision:recommendation")
    conf = a.confidence
    if conf is None and not loose:
        fail("--confidence (0-1) is required", "decision:confidence")
    if conf is not None and not 0 <= conf <= 1:
        fail("--confidence must be between 0 and 1", "decision:confidence")
    if a.evidence_kind is None and not loose:
        fail(f"--evidence-kind is required: {' | '.join(EVIDENCE_KINDS)}", "decision:evidence-kind")
    if a.evidence_kind is not None and a.evidence_kind not in EVIDENCE_KINDS:
        fail(f"--evidence-kind must be one of {' | '.join(EVIDENCE_KINDS)}", "decision:evidence-kind")
    if not a.evidence and not loose:
        fail("--evidence (a one-line pointer) is required", "decision:evidence")
    if a.recommendation and a.option and a.recommendation not in a.option and not loose:
        fail("--recommendation must be one of the --option values", "decision:recommendation")
    if a.approves is not None and a.approves not in a.option:
        fail("--approves must be one of the --option values", "decision:approves")
    triggers = check_grounding(a, config(project)) if not loose else []
    cats = categories(project)
    if norm(a.category) not in {norm(c) for c in cats}:
        print(f"decision: note: category '{a.category}' is outside the project's categories ({', '.join(cats)}):"
              " an assumed answer is refused for it", file=sys.stderr)
    ts = stamp()
    did = a.id or "d" + ts[:10].replace("-", "") + "-" + hashlib.sha1(
        f"{ts}{project.root}{a.question}{random.random()}".encode()).hexdigest()[:6]
    earlier = fold(read_events(project))
    if did in earlier:
        fail(f"id {did} already exists in {name_of(project)}", "decision:id-exists")
    ev = {"kind": "ask", "id": did, "ts": ts, "stream": name_of(project), "project": a.project or None,
          "category": a.category, "question": a.question, "options": a.option,
          "recommendation": a.recommendation or None, "confidence": conf,
          "evidence": {"kind": a.evidence_kind, "pointer": a.evidence or None},
          "blind": a.blind, "autonomous": a.autonomous, "questionsRef": None,
          "read": [{"path": r, "what": w} for r, w in zip(a.read, read_whats(a))] or None,
          "task": a.task or None, "fixRound": a.fix_round, "rechecked": a.rechecked or None,
          "upstream": a.upstream or None, "steppedBack": a.stepped_back or None,
          "recheck": bool(a.recheck), "change": changes(a), "layer": a.layer, "owners": a.owners,
          "area": a.area.strip() if a.area else None, "architectOption": a.architect_option, "triggers": triggers}
    if a.approves is not None:
        ev["approves"] = a.approves
    if loose:
        ev["backfilled"] = True
    append(project, ev)
    outcome_line(a, earlier)
    print(did)


def outcome_line(a, earlier):
    """At fix round 2 or later, one line naming the decision this round follows, when its outcome is not recorded
    (0.14.2): the moment an ending shows is the moment to ask for it. Not a trigger; refuses and records nothing.
    earlier: the ledger folded, as it was before this ask."""
    if not a.task or a.recheck or (a.fix_round or 0) < 2:
        return
    asks = [e for e in by_time([d["ask"] for d in earlier.values()])
            if str(e.get("task") or "").lower() == a.task.lower()]
    rounds = [e for e in asks if isinstance(e.get("fixRound"), int) and 0 < e["fixRound"] < a.fix_round]
    if not asks:
        return
    last = (rounds or asks)[-1]
    if earlier[last["id"]]["outcomes"]:
        return
    was = f" (fix round {last['fixRound']})" if rounds else ""
    print(f"decision: outcome: fix round {a.fix_round} of {a.task} follows {last['id']}{was}, whose outcome is not"
          f" recorded. When you know how it turned out: decision.sh outcome {last['id']} --verdict VERDICT"
          f" --by \"what showed it\" (VERDICT: {', '.join(VERDICTS[:-1])} or {VERDICTS[-1]})", file=sys.stderr)


def read_whats(a):
    """What was looked at in each --read file: as many texts as files, each with its own; one text, with every file;
    any other count, all of them with every file."""
    if len(a.read_what) == len(a.read):
        return a.read_what
    return ["; ".join(a.read_what) if a.read_what else None] * len(a.read)


def meaningful(value, words=3):
    """A justification flag holds a statement, not a placeholder: at least `words` words with letters."""
    return len(re.findall(r"[^\W\d_]{2,}", value or "")) >= words


def blind_readers_record(path):
    """True for a reader-<x>.md, an agreement.md or an agreement.json in a folder that holds a reader-<x>.md and
    an agreement.md: a blind-readers run folder."""
    if not (READER_REPORT.fullmatch(path.name) or path.name.lower() in ("agreement.md", "agreement.json")):
        return False
    try:
        here = [p.name for p in path.parent.iterdir() if p.is_file()]
    except OSError:
        return False
    return any(READER_REPORT.fullmatch(n) for n in here) and any(n.lower() == "agreement.md" for n in here)


def trigger(name, why):
    """The one line a trigger prints whenever its rule applies, pass or refuse (0.11.0)."""
    print(f"decision: {name} trigger: {why}", file=sys.stderr)
    return name


def changes(a):
    """The question proposes a code change on --task: declared with --change, or by --fix-round (a fix is one)."""
    return bool(a.change or a.fix_round is not None)


def check_grounding(a, cfg):
    """Refuse a question that is not grounded in a source the asker opened (owner, 2026-10-14).
    A category in the config's groundingExempt (a spend, process or scope call, which no source file
    settles) needs no --read; every other check still applies. Returns the triggers that fired."""
    fired = []
    if a.category not in cfg.get("groundingExempt", []):
        if not a.read:
            fail("--read <file> is required: a source file you opened yourself (raw document, product data,"
                 f" code), not an agent's report. Questions in {', '.join(cfg.get('groundingExempt', [])) or 'no category'}"
                 " need none.", "decision:read-required")
        for r in a.read:
            path = Path(r).expanduser()
            if not path.is_file():
                fail(f"--read {r}: no such file" if not path.exists() else f"--read {r}: not a file",
                     "decision:read-file")
            report = path.suffix.lower() in REPORT_SUFFIXES and (REPORT_NAMES.search(path.stem)
                                                                 or {"review", "reviews", "runs"} & set(path.parts))
            if report or blind_readers_record(path):
                fail(f"--read {r}: that is an agent's record, not the source; open what it is about",
                     "decision:read-report")
        if not a.read_what or not all(meaningful(w, 1) for w in a.read_what):
            fail("--read-what is required: the rows, cells or lines you looked at in the --read file(s)",
                 "decision:read-what")
    text = a.question.lower()
    # each sentence is judged on its own: a 'guess:' label clears only the sentence it is in
    for sentence in re.split(r"(?<=[.?!;])\s+", text):
        if GUESS_LABEL.search(sentence):
            continue
        said = LEADING_SHOULD.sub(" ", QUOTED.sub(" ", sentence).strip())
        hits = [w for w in GUESS_WORDS if re.search(rf"(?<![\w.]){re.escape(w)}(?![\w.])", said)]
        if hits:
            fail(f"the question uses {', '.join(repr(h) for h in hits)}: an inference. Check it at the"
                 " source, or label that sentence with 'guess:'", "decision:inference")
    events = read_events(resolve_project(a.repo)) if a.task else []
    asked = {e.get("fixRound") for e in events if e.get("kind") == "ask"
             and (e.get("task") or "").lower() == (a.task or "").lower() and e.get("fixRound")}
    if a.recheck:
        if not a.task or a.fix_round is not None:
            fail("--recheck needs --task and no --fix-round: it reviews a fix round, it does not open one",
                 "decision:recheck")
        if not asked:
            fail(f"--recheck: {a.task} has no fix round in the ledger to review", "decision:recheck")
    if a.fix_round is not None:
        if not a.task:
            fail("--fix-round needs --task (the work order id)", "decision:fix-round")
        if a.fix_round < 1:
            fail("--fix-round counts from 1", "decision:fix-round-number")
        # every trigger that applies prints before the first refusal, so one try shows them all
        fired.append(trigger("upstream", f"fix round {a.fix_round} of {a.task}: check what feeds the failing step"
                                         " first (--upstream)"))
        if a.fix_round >= 2:
            fired.append(trigger("re-check", f"fix round {a.fix_round} of {a.task}: a second round means the design"
                                             " may be wrong; re-check at the source first (--rechecked)"))
        if a.fix_round >= 3 or len(asked) >= 2:
            fired.append(trigger("step-back", f"{a.task} is at fix round {a.fix_round} (rounds asked so far:"
                                              f" {sorted(asked) or 'none'}): step back across the whole problem"
                                              " first (--stepped-back)"))
    plan = altitude(a, events)
    if a.fix_round is not None:
        # a fix is what the asker declares with --fix-round, not a word in the question (item 13)
        if not meaningful(a.upstream):
            fail("the question proposes a fix: give --upstream, what feeds the failing step and what you saw"
                 " when you looked at it (Seesaw: check what it was given before fixing it)", "decision:upstream")
        # the round is counted from the ledger, not taken on trust: an earlier number cannot be reused
        if asked and a.fix_round <= max(asked):
            fail(f"{a.task} already has fix round(s) {sorted(asked)}; this one is round {max(asked) + 1}."
                 " A question inside a round needs no --fix-round.", "decision:fix-round-number")
        if a.fix_round >= 2 and not meaningful(a.rechecked):
            fail(f"fix round {a.fix_round} of {a.task}: give --rechecked, what you re-checked at the source"
                 " before proposing another round (a second round means the design may be wrong)", "decision:rechecked")
        if (a.fix_round >= 3 or len(asked) >= 2) and not meaningful(a.stepped_back):
            fail(f"{a.task} is at fix round {a.fix_round} (rounds asked so far: {sorted(asked) or 'none'}):"
                 " give --stepped-back, the step-back you did first — what you re-checked at the source across"
                 " the whole problem, and why another round beats re-designing (owner, 2026-10-15)",
                 "decision:stepped-back")
    fired += check_change(a, events, plan)
    if re.search(r"\bfix round\b", text) and a.fix_round is None and not a.recheck and asked:
        # a question inside a round is recorded there; only the ask that opens a round numbers it (0.17.2)
        print("decision: note: the question says 'fix round': it is recorded inside the work order's current round;"
              " --fix-round N is given only on the ask that opens round N", file=sys.stderr)
    elif re.search(r"\bfix round\b", text) and a.fix_round is None and not a.recheck:
        # no round is open on its work order (or no --task): the question may be opening round 1 (0.17.2 review F1)
        print("decision: note: the question says 'fix round' and no fix round is open on its work order: if it opens"
              " one, give --task, --fix-round 1 and --upstream", file=sys.stderr)
    elif FIX_WORDS.search(text) and not changes(a) and not a.recheck:
        print("decision: note: the question mentions a fix; if it proposes one, give --task, --fix-round"
              " and --upstream", file=sys.stderr)
    elif CHANGE_WORDS.search(text) and a.task and not changes(a) and not a.recheck:
        print(f"decision: note: the question mentions a change on {a.task}; if it proposes one, give --change,"
              " --layer, --owners and --area", file=sys.stderr)
    return fired


def check_change(a, events, plan):
    """A declared change (--change, or --fix-round) names its layer, owners and area; the altitude trigger fires on
    a layer out or two or more owners, and a cluster on the third of one kind on one area (0.11.0). plan: what
    altitude() found and printed. Returns the triggers that fired."""
    if not changes(a):
        if a.layer is not None or a.owners is not None or a.area is not None or a.architect_option is not None:
            fail("--layer, --owners, --area and --architect-option go with --change (or --fix-round): the question"
                 " proposes a code change on --task", "decision:change")
        return []
    if not a.task:
        fail("--change needs --task (the work order id the change is on)", "decision:change")
    missing = [f for f, v in (("--layer in|out (the change's layer against the work order's owning layer)", a.layer),
                              ("--owners N (how many places own the rule it touches)", a.owners),
                              ("--area <name> (the module or data shape whose rule it touches)",
                               (a.area or "").strip() or None)) if v is None]
    if missing:
        fail(f"the question proposes a change on {a.task}: give {'; '.join(missing)}", "decision:change")
    if a.owners < 1:
        fail("--owners counts from 1: the places that own the rule the change touches", "decision:change")
    if a.architect_option is not None and a.architect_option not in a.option:
        fail(f"--architect-option {a.architect_option!r} is not one of the --option values"
             f" ({', '.join(repr(o) for o in a.option)}): it names the option that is the architect check",
             "decision:change")
    if plan is None:
        return []
    area, why, clusters = plan
    if a.autonomous and a.recommendation != a.architect_option:
        fail(f"the altitude trigger fired on {a.task}: an --autonomous decision takes the architect check, so"
             " --recommendation is the --architect-option (or ask the owner)", "decision:altitude")
    if clusters:
        if a.architect_option is None:
            fail(f"a cluster of altitude triggers on {area}: give --architect-option, the --option that is one"
                 f" architect check of the area {area}, and hold the items it covers until it reports; a recorded"
                 " architect run for one task does not count for a cluster", "decision:altitude-cluster")
        return ["altitude", "altitude-cluster"]
    if a.architect_option is not None:
        return ["altitude"]
    opened, run = architect_run(a.task, events)
    if run:
        return ["altitude"]
    fail(f"the altitude trigger fired on {a.task} ({why}): give --architect-option, the --option that is an"
         " architect check of the owning layer, or record an architect run for"
         f" {a.task} after its work order opened (spend.sh record --kind architect --task {a.task})"
         + ("" if opened else f"; {a.task} has no sealed event in the ops ledger, so no recorded run counts yet"),
         "decision:altitude")


def by_time(events):
    """The ask events, oldest first: by their time (when()), then by read order."""
    lowest = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    return [e for _, _, e in sorted(((when(e.get("ts")) or lowest, n, e) for n, e in enumerate(events)
                                     if e.get("kind") == "ask"), key=lambda x: x[:2])]


def cleared(e, answers):
    """An ask that recorded altitude-cluster and whose latest answer chose its declared architect option (the
    area check): the cluster's count starts again after it."""
    if "altitude-cluster" not in (e.get("triggers") or []) or not answers or not e.get("architectOption"):
        return False
    return norm(answers[-1].get("choice")) == norm(e["architectOption"])


def altitude(a, events):
    """The altitude trigger: prints its line(s) and returns (area, why, cluster lines), or None when it does not
    fire or the change flags are not all given (check_change refuses those). Prints before any refusal."""
    if not (changes(a) and a.task and a.layer and isinstance(a.owners, int) and a.owners >= 1
            and (a.area or "").strip()):
        return None
    kinds = [k for k, on in (("layer", a.layer == "out"), ("owners", a.owners >= 2)) if on]
    if not kinds:
        return None
    area = a.area.strip()
    asks = by_time(events)
    same = [e for e in asks if norm(e.get("area") if isinstance(e.get("area"), str) else "") == norm(area)]
    folded = fold(events)
    after = same
    for i in range(len(same) - 1, -1, -1):  # the count starts again after the latest cleared cluster
        if cleared(same[i], folded.get(same[i].get("id"), {}).get("answers")):
            after = same[i + 1:]
            break
    clusters, single = [], []
    for k in kinds:
        earlier = [e for e in after if fires(e, k)]
        if len(earlier) >= 2:
            tasks = ", ".join(dict.fromkeys([str(e.get("task") or "-") for e in earlier] + [a.task]))
            clusters.append(f"cluster: {CLUSTER_KINDS[k]} on {area}, {len(earlier) + 1} times ({tasks})")
        else:
            single.append(k)
    for line in clusters:
        trigger("altitude", line)
    why = None
    if single:
        why = (f"both (layer out, {a.owners} places own the rule)" if len(single) == 2
               else "layer out" if single == ["layer"] else f"{a.owners} places own the rule")
        known = {}  # one name per area (case and spaces ignored), most recent first
        for e in reversed(asks):
            if isinstance(e.get("area"), str) and e["area"].strip():
                known.setdefault(norm(e["area"]), e["area"].strip())
        known = list(known.values())
        if not same and known:
            trigger("altitude", f"{why}; area '{area}' is new (areas already recorded, most recent first:"
                                f" {', '.join(known[:AREAS_LISTED])})")
        else:
            trigger("altitude", why)
    return area, why, clusters


CLUSTER_KINDS = {"layer": "layer out", "owners": "two or more places own the rule"}


def fires(e, kind):
    """An earlier ask fired this kind of altitude trigger: its recorded layer is out, or its owners 2 or more."""
    if kind == "layer":
        return e.get("layer") == "out"
    owners = e.get("owners")
    return isinstance(owners, int) and not isinstance(owners, bool) and owners >= 2


def when(ts):
    try:
        t = datetime.datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return t if t.tzinfo else t.astimezone()


def architect_run(task, events):
    """(when the work order opened, an architect run recorded for it since). The work order opened at its first
    sealed event in the ops ledger (every writer's file); the run is the spend ledger's (spend.sh record --kind
    architect --task), its latest row per agent id. One run lets one altitude ask through: it counts only when
    recorded after the task's latest earlier ask that recorded the altitude trigger."""
    import ops_ledger
    import spend
    seals = []
    for p in _PROJECT.read_files("ops_ledger"):
        if p.is_file():
            for line in ops_ledger.lines_of(p.read_bytes()):
                ev = ops_ledger.parse(line)
                if ev and ev.get("kind") == "sealed" and str(ev.get("task") or "").lower() == task.lower():
                    seals.append(when(ev.get("ts")))
    seals = [t for t in seals if t is not None]
    if not seals:
        return None, None
    opened = min(seals)
    since = [opened] + [when(e.get("ts")) for e in by_time(events) if "altitude" in (e.get("triggers") or [])
                        and str(e.get("task") or "").lower() == task.lower() and when(e.get("ts"))]
    used = max(since[1:]) if len(since) > 1 else None
    runs = [r for r in spend.agents(spend.read_events(_PROJECT)) if r.get("kind") == "architect"
            and str(r.get("task") or "").lower() == task.lower() and when(r.get("ts"))
            and when(r.get("ts")) >= opened and (used is None or when(r.get("ts")) > used)]
    return opened, (runs[0] if runs else None)


def est_arg(v):
    s = v.replace(",", "").replace("_", "")
    if not s.isdigit():
        raise argparse.ArgumentTypeError(f"'{v}' is not a whole number of tokens, 0 or more")
    return int(s)


def norm(s):
    return " ".join((s or "").lower().split())


def cmd_answer(a):
    project = resolve_project(a.repo, write=True)
    d = fold(read_events(project)).get(a.id)
    if not d:
        fail(f"no decision {a.id} in {name_of(project)}", "decision:unknown-id")
    ask = d["ask"]
    if ask["autonomous"]:
        fail(f"{a.id} was decided autonomously; record what later showed with `outcome`, not `answer`",
             "decision:autonomous-answer")
    if a.assumed or a.est_tokens is not None:
        answer_assumed(project, a, d)
        return
    if not a.choice.strip():
        fail("--choice is empty", "decision:choice")
    agreed = {"yes": True, "no": False, None: None}[a.agreed]
    if agreed is None and ask.get("recommendation") and norm(a.choice) == norm(ask["recommendation"]):
        agreed = True
    if agreed is None and not ask["blind"] and not backfill():
        fail("the choice does not match the recommendation word for word; say --agreed yes or --agreed no",
             "decision:agreed")
    ev = {"kind": "answer", "id": a.id, "ts": stamp(), "choice": a.choice, "agreed": agreed,
          "notes": a.notes or None}
    if backfill():
        ev["backfilled"] = True
    append(project, ev)


class SettingsError(ValueError):
    """The project's "unattended" block has a shape the tools cannot use (review F9)."""


def unattended_settings(project):
    """The "unattended" settings, merged over the defaults and checked once: an object; "bands" a list of
    {"maxTokens": <whole number>, "above": <number>}; "neverCategories" a list of text. Raises SettingsError."""
    cfg = config(project).get("unattended")

    def num(v, whole=False):
        return not isinstance(v, bool) and isinstance(v, int if whole else (int, float))

    if not isinstance(cfg, dict):
        why = "is not an object"
    elif not isinstance(cfg.get("bands"), list) or not all(
            isinstance(b, dict) and num(b.get("maxTokens"), whole=True) and num(b.get("above")) for b in cfg["bands"]):
        why = 'has "bands" that is not a list of {"maxTokens": <whole number>, "above": <number>}'
    elif not isinstance(cfg.get("neverCategories"), list) or not all(isinstance(c, str)
                                                                      for c in cfg["neverCategories"]):
        why = 'has "neverCategories" that is not a list of text'
    else:
        return cfg
    raise SettingsError(f'the "unattended" block in {project.decision_config} {why}: fix it there')


def unattended(project, events=None):
    """Unattended mode: on when the project turns it on (the JSON value true) and its latest switch event is a
    start."""
    enabled = unattended_settings(project).get("enabled") is True
    events = read_events(project) if events is None else events
    last = next((e for e in reversed(events) if e.get("kind") == "unattended"), None)
    on = enabled and bool(last) and last.get("action") == "start"
    return {"on": on, "since": last.get("ts") if on else None, "why": last.get("why") if on else None,
            "wave": last.get("wave") if on else None}


def cmd_unattended(a):
    """Start is refused unless the project turns the mode on, and while it is on. Stop is always taken: it is
    the safe direction."""
    project = resolve_project(a.repo, write=True)
    ev = {"kind": "unattended", "action": a.action, "ts": stamp(), "stream": name_of(project)}
    if a.action == "start":
        if unattended_settings(project).get("enabled") is not True:
            fail('unattended mode is off for this project; the project turns it on with "unattended": {"enabled": true}'
                 f" in {project.decision_config}", "decision:unattended-disabled")
        if not a.why.strip():
            fail('--why is empty: say why, for example "owner said unattended" or "no answer to <id> for 30 minutes"',
                 "decision:unattended-why")
        now_on = unattended(project)
        if now_on["on"]:
            fail(f"unattended mode is already on, since {now_on['since']} ({now_on['why']}); stop it before a new"
                 " start", "decision:unattended-on")
        ev.update(why=a.why, wave=a.wave or None)
    append(project, ev)


def answer_assumed(project, a, d):
    """An assumed yes while the owner is away: the recommendation, recorded as the PM's and never as the owner's.
    The confidence must be above the line of the band the step's estimated tokens fall in."""
    ask = d["ask"]
    cfg = unattended_settings(project)
    if not unattended(project)["on"]:
        fail("unattended mode is not on, so nothing is assumed: `decision.sh unattended start --why ...`",
             "decision:assumed-off")
    if not a.assumed or a.est_tokens is None:
        fail("an assumed answer gives both --assumed and --est-tokens <n>, the step's estimated tokens"
             " (0: a step with no AI)", "decision:assumed-estimate")
    if a.agreed is not None:
        fail("--agreed is not given with --assumed: an assumed answer is the recommendation, and only the owner"
             " agrees or not", "decision:assumed-agreed")
    if any(not an.get("assumed") for an in d["answers"]):
        fail(f"{a.id} already has the owner's answer; an assumed one never replaces it",
             "decision:assumed-answered")
    rec, conf = ask.get("recommendation"), ask.get("confidence")
    if not rec or conf is None:
        fail(f"{a.id} has no recommendation or no confidence, so there is nothing to assume",
             "decision:assumed-recommendation")
    if a.choice is not None and norm(a.choice) != norm(rec):
        fail(f"an assumed answer is the recommendation itself ({rec!r}), not {a.choice!r}", "decision:assumed-choice")
    if ask.get("approves") is not None:
        fail(f"{a.id} names --approves: an approval of a gate exception is always the owner's",
             "decision:assumed-approves")
    if "altitude" in (ask.get("triggers") or []) and rec != ask.get("architectOption"):
        fail(f"{a.id} fired the altitude trigger and its recommendation is not its architect option: an assumed"
             " answer takes only the architect check, so this waits for the owner", "decision:altitude")
    if ask.get("blind"):
        fail(f"{a.id} was asked blind: a blind ask measures the owner's own answer, so it waits for the owner",
             "decision:assumed-blind")
    if norm(ask.get("category")) in {norm(c) for c in cfg["neverCategories"]}:
        fail(f"category {ask.get('category')} is on the project's never-list: it waits for the owner",
             "decision:assumed-never")
    cats = categories(project)
    if norm(ask.get("category")) not in {norm(c) for c in cats}:
        fail(f"category {ask.get('category')} is not in the project's categories ({', '.join(cats)}): an"
             " assumed answer needs one of them, so this waits for the owner. A project lists its own categories"
             " in .team/decision-config.json, and that list replaces the default one", "decision:assumed-category")
    bands = sorted(cfg["bands"], key=lambda b: b["maxTokens"])
    band = next((b for b in bands if a.est_tokens <= b["maxTokens"]), None)
    if band is None:
        fail(f"the estimate {a.est_tokens:,} tokens is above the top band"
             f" ({bands[-1]['maxTokens'] if bands else 0:,}): never assumed; it waits for the owner",
             "decision:assumed-cost")
    if not conf > band["above"]:
        fail(f"confidence {conf} is not above {band['above']} for a step of up to {band['maxTokens']:,} tokens:"
             " dig further, or park it for the owner", "decision:assumed-confidence")
    append(project, {"kind": "answer", "id": a.id, "ts": stamp(), "choice": rec, "agreed": True,
                     "notes": a.notes or None, "assumed": True, "estTokens": a.est_tokens})


def cmd_outcome(a):
    project = resolve_project(a.repo, write=True)
    if a.id not in fold(read_events(project)):
        fail(f"no decision {a.id} in {name_of(project)}", "decision:unknown-id")
    if not a.by.strip():
        fail("--by (what showed it) is empty", "decision:by")
    ev = {"kind": "outcome", "id": a.id, "ts": stamp(), "verdict": a.verdict, "by": a.by,
          "pointer": a.pointer or None}
    if backfill():
        ev["backfilled"] = True
    append(project, ev)


# ---------- score ----------

def reversed_(d):
    return bool(d["outcomes"]) and d["outcomes"][-1]["verdict"] == "reversed"


def effective(d):
    """The owner's agreement, with a later reversal counting against it. None when not comparable, and while
    the latest answer is an assumed one (not the owner's)."""
    if d["ask"]["autonomous"] or not d["answers"] or d["answers"][-1].get("assumed"):
        return None
    agreed = d["answers"][-1]["agreed"]
    if agreed is None:
        return None
    return False if reversed_(d) else agreed


def age_days(ts, at):
    t = datetime.datetime.fromisoformat(ts)
    if t.tzinfo is None:
        t = t.astimezone()
    return max(0.0, (at - t).total_seconds() / 86400)


def stats(decisions, cfg, at):
    asked = [d for d in decisions if not d["ask"]["autonomous"]]
    answered = [d for d in asked if d["answers"] and not d["answers"][-1].get("assumed")]
    scored = [d for d in answered if effective(d) is not None]
    ys = [1.0 if effective(d) else 0.0 for d in scored]
    hl = cfg["halfLifeDays"]
    ws = [0.5 ** (age_days(d["ask"]["ts"], at) / hl) for d in scored]
    conf = [(d["ask"]["confidence"], y) for d, y in zip(scored, ys) if d["ask"].get("confidence") is not None]
    blind = [1.0 if effective(d) else 0.0 for d in scored if d["ask"]["blind"]]
    bands = []
    for lo, hi in BANDS:
        inb = [(c, y) for c, y in conf if lo <= c < hi]
        bands.append({"band": f"{lo:.1f}-{min(hi, 1.0):.1f}", "n": len(inb),
                      "meanConfidence": round(sum(c for c, _ in inb) / len(inb), 3) if inb else None,
                      "agreement": round(sum(y for _, y in inb) / len(inb), 3) if inb else None})
    window = sorted(decisions, key=lambda d: d["ask"]["ts"])[-cfg["autonomy"]["reversalWindow"]:]
    s = {"decisions": len(decisions), "asked": len(asked), "autonomous": len(decisions) - len(asked),
         "answered": len(answered), "scored": len(scored),
         "agreement": round(sum(ys) / len(ys), 3) if ys else None,
         "agreementRecency": round(sum(w * y for w, y in zip(ws, ys)) / sum(ws), 3) if ys else None,
         "brier": round(sum((c - y) ** 2 for c, y in conf) / len(conf), 3) if conf else None,
         "withConfidence": len(conf),
         "blindN": len(blind), "blindAgreement": round(sum(blind) / len(blind), 3) if blind else None,
         "reversed": sum(1 for d in decisions if reversed_(d)),
         "reversedInWindow": sum(1 for d in window if reversed_(d)),
         "reliability": bands}
    s["autonomy"], s["autonomyReasons"] = verdict(s, cfg["autonomy"])
    return s


def verdict(s, t):
    why = []
    if s["answered"] < t["minAnswered"]:
        why.append(f"answered {s['answered']} < {t['minAnswered']}")
    if s["agreementRecency"] is None or s["agreementRecency"] < t["minRecencyAgreement"]:
        why.append(f"recency agreement {s['agreementRecency']} < {t['minRecencyAgreement']}")
    if s["brier"] is None:
        why.append("no confidence recorded, so calibration is unknown")
    elif s["brier"] > t["maxBrier"]:
        why.append(f"Brier {s['brier']} > {t['maxBrier']}")
    if s["reversedInWindow"] > t["maxReversalsInWindow"]:
        why.append(f"{s['reversedInWindow']} reversed in the last {t['reversalWindow']}")
    return ("autonomous" if not why else "ask"), why


def score(project, cfg, category=None, since=None):
    decisions = list(fold(read_events(project)).values())
    if since:
        decisions = [d for d in decisions if d["ask"]["ts"][:10] >= since]
    at = now()
    cats = sorted({d["ask"]["category"] for d in decisions})
    if category:
        cats = [category]
    return {"stream": name_of(project),
            "categories": {c: stats([d for d in decisions if d["ask"]["category"] == c], cfg, at) for c in cats},
            "overall": stats(decisions, cfg, at)}


def fmt(v):
    return "-" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))


def score_text(sc, md):
    head = ["category", "asked", "auto", "answered", "agree", "agree (recent)", "Brier", "blind agree",
            "reversed", "verdict"]
    rows = []
    for name, s in list(sc["categories"].items()) + [("OVERALL", sc["overall"])]:
        rows.append([name, s["asked"], s["autonomous"], s["answered"], fmt(s["agreement"]),
                     fmt(s["agreementRecency"]), fmt(s["brier"]),
                     f"{fmt(s['blindAgreement'])} (n {s['blindN']})", s["reversed"],
                     s["autonomy"] if name != "OVERALL" else "-"])
    out = [f"Decision scores: project {sc['stream']}", ""]
    out += table(head, rows, md)
    out += ["", "Why each category still asks:"]
    for name, s in sc["categories"].items():
        if s["autonomyReasons"]:
            out.append(f"- {name}: " + "; ".join(s["autonomyReasons"]))
    o = sc["overall"]
    out += ["", f"Calibration (all categories, {o['withConfidence']} decisions with a stated confidence):", ""]
    out += table(["confidence band", "n", "mean confidence", "owner agreed"],
                 [[b["band"], b["n"], fmt(b["meanConfidence"]), fmt(b["agreement"])] for b in o["reliability"]], md)
    return out


def cell(v):
    return str(v).replace("|", "\\|").replace("\n", "<br>")


def table(head, rows, md):
    if md:
        return (["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
                + ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows])
    w = [max(len(str(x)) for x in col) for col in zip(head, *rows)]
    return ["  ".join(str(x).ljust(n) for x, n in zip(r, w)).rstrip() for r in [head, *rows]]


def cmd_score(a):
    project = resolve_project(a.repo)
    sc = score(project, config(project), a.category, a.since)
    if a.json:
        print(json.dumps(sc, indent=1))
    else:
        print("\n".join(score_text(sc, False)) + "\n")


# ---------- sample ----------

def cmd_should_ask(a):
    project = resolve_project(a.repo)
    cfg = config(project)
    s = score(project, cfg, a.category)["categories"][a.category]
    if s["autonomy"] != "autonomous":
        print("ask")
        print(f"{a.category} is not autonomous: " + "; ".join(s["autonomyReasons"]), file=sys.stderr)
        return
    p = cfg["blindSampleRate"]
    draw = random.Random(a.seed).random()
    print("ask-blind" if draw < p else "decide-and-log")
    print(f"{a.category} is autonomous; blind sample rate {p}, draw {draw:.3f}", file=sys.stderr)


# ---------- review and DECISIONS.md ----------

def flags_of(q):
    return [f for f, on in (("blind", q["blind"]), ("autonomous", q["autonomous"]),
                            ("backfilled", q.get("backfilled"))) if on]


def render(project):
    """Rewrite <records>/DECISIONS.md from the ledger: scores on top, then every decision, newest first."""
    sc = score(project, config(project))
    out = [f"# DECISIONS — {name_of(project)}", "",
           "> Generated by `runtime/decision.sh` from `decisions/ledger.jsonl` after every recorded ask, answer "
           "or outcome. Do not edit by hand: a change here is overwritten. The ledger is the record; this "
           f"file is the readable view. Scores as of {now().isoformat(timespec='minutes')}.", "",
           "## Scores", ""]
    out += score_text(sc, True)[2:]
    out += ["", "## Every decision, newest first", ""]
    for d in sorted(fold(read_events(project)).values(), key=lambda d: d["ask"]["ts"], reverse=True):
        q, ev = d["ask"], d["ask"].get("evidence") or {}
        out += [f"### `{q['id']}` — {q['question']}", "",
                f"- **When:** {q['ts'][:16].replace('T', ' ')}. **Category:** {q['category']}."
                + (f" **Project:** {q['project']}." if q.get("project") else "")
                + (f" **Flags:** {', '.join(flags_of(q))}." if flags_of(q) else "")]
        if q.get("options"):
            out.append(f"- **Options:** {' / '.join(q['options'])}.")
        out.append(f"- **Recommended:** {q.get('recommendation') or 'not recorded'}"
                   f" (confidence {fmt(q.get('confidence'))}).")
        if ev.get("kind") or ev.get("pointer"):
            out.append(f"- **Evidence:** {ev.get('kind') or 'kind not recorded'}: {ev.get('pointer') or '-'}.")
        if q.get("questionsRef"):
            out.append(f"- **QUESTIONS.md entry:** {heading_question(q['questionsRef'])}")
        if q["autonomous"]:
            out.append("- **Decided by the agent without asking.**")
        for an in d["answers"]:
            agreed = {True: "yes", False: "no", None: "not yet compared"}[an["agreed"]]
            who = "Assumed by the PM, to confirm" if an.get("assumed") else "Owner's choice"
            out.append(f"- **{who} ({an['ts'][:16].replace('T', ' ')}):** {an['choice']}. "
                       f"**Agreed with the recommendation:** {agreed}."
                       + (f" Notes: {an['notes']}" if an.get("notes") else ""))
        if not q["autonomous"] and not d["answers"]:
            out.append("- **Owner's choice:** no answer yet.")
        for o in d["outcomes"]:
            out.append(f"- **Later ({o['ts'][:10]}):** {o['verdict']}, shown by {o['by']}"
                       + (f" ({o['pointer']})" if o.get("pointer") else "") + ".")
        out.append("")
    write_whole(project.decisions_view, "\n".join(out))


def cmd_render(a):
    project = resolve_project(a.repo)
    render(project)
    print(project.decisions_view)


def cmd_review(a):
    project = resolve_project(a.repo)
    cfg = config(project)
    out = score_text(score(project, cfg, since=a.since), a.md)
    out += ["", f"Every decision, newest first: project {name_of(project)}", ""]
    ds = sorted(fold(read_events(project)).values(), key=lambda d: d["ask"]["ts"], reverse=True)
    if a.since:
        ds = [d for d in ds if d["ask"]["ts"][:10] >= a.since]
    rows = []
    for d in ds:
        q = d["ask"]
        flags = flags_of(q)
        ans = d["answers"][-1] if d["answers"] else None
        ev = q.get("evidence") or {}
        rows.append([q["ts"][:16].replace("T", " "), q["id"], q["category"], q.get("project") or "",
                     q["question"], " / ".join(q.get("options") or []), q.get("recommendation") or "",
                     fmt(q.get("confidence")),
                     f"{ev.get('kind') or ''}: {ev.get('pointer') or ''}".strip(": "),
                     "(decided by agent)" if q["autonomous"] else (
                         (ans["choice"] + (" (assumed, to confirm)" if ans.get("assumed") else "")) if ans
                         else "(no answer yet)"),
                     {True: "yes", False: "no", None: "-"}[ans["agreed"]] if ans else "-",
                     "; ".join(f"{o['verdict']}: {o['by']}" for o in d["outcomes"]),
                     ", ".join(flags), (ans or {}).get("notes") or "",
                     heading_question(q["questionsRef"]) if q.get("questionsRef") else ""])
    out += table(["when", "id", "category", "project", "question", "options", "recommended", "conf",
                  "evidence", "owner's choice", "agreed", "outcomes", "flags", "notes", "QUESTIONS.md entry"],
                 rows, a.md)
    print("\n".join(out) + "\n")


def main():
    p = argparse.ArgumentParser(prog="decision.sh", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def repo_arg(sp):
        sp.add_argument("--repo", help="the project's repository folder; default: the current folder")

    s = sub.add_parser("ask", help="record a question put to the owner (prints its id)")
    repo_arg(s)
    s.add_argument("--category", required=True)
    s.add_argument("--question", help="verbatim, as put to the owner")
    s.add_argument("--option", action="append", default=[], help="repeat once per option")
    s.add_argument("--recommendation")
    s.add_argument("--confidence", type=float)
    s.add_argument("--evidence-kind")
    s.add_argument("--evidence", help="one-line pointer to the evidence")
    s.add_argument("--project")
    s.add_argument("--blind", action="store_true", help="asked without showing the recommendation")
    s.add_argument("--autonomous", action="store_true", help="decided by the agent without asking")
    s.add_argument("--id")
    s.add_argument("--read", action="append", default=[], help="a source file you opened yourself; repeat")
    s.add_argument("--read-what", action="append", default=[],
                   help="the rows, cells or lines you looked at in it; repeat, one per --read in order, or one for all")
    s.add_argument("--task", help="the work order id the question serves")
    s.add_argument("--approves", help="the option that approves a close exception (predict-gate.sh close); one of --option")
    s.add_argument("--fix-round", type=int, help="which fix round of --task this question opens")
    s.add_argument("--rechecked", help="for fix round 2+: what you re-checked at the source first")
    s.add_argument("--stepped-back", help="fix round 3+ of a task: the step-back done first")
    s.add_argument("--recheck", action="store_true",
                   help="this ask reviews a fix round of --task (a re-check); it opens no round")
    s.add_argument("--upstream", help="for a fix: what feeds the failing step, and what you saw when you looked")
    s.add_argument("--change", action="store_true",
                   help="the question proposes a code change on --task (a fix or a design change); --fix-round implies it")
    s.add_argument("--layer", choices=("in", "out"), help="with --change: the change's layer against the work order's owning layer")
    s.add_argument("--owners", type=int, help="with --change: how many places own the rule the change touches (1 or more)")
    s.add_argument("--area", help="with --change: the module or data shape whose rule the change touches")
    s.add_argument("--architect-option", help="with --change: the --option that is the architect check (one of --option)")
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser("answer", help="record the owner's reply")
    repo_arg(s)
    s.add_argument("id")
    s.add_argument("--choice", help="verbatim; required unless --assumed")
    s.add_argument("--agreed", choices=["yes", "no"])
    s.add_argument("--notes")
    s.add_argument("--assumed", action="store_true",
                   help="unattended mode: record the recommendation as an assumed yes, for the owner to confirm")
    s.add_argument("--est-tokens", type=est_arg, help="with --assumed: the step's estimated tokens (0: no AI)")
    s.set_defaults(fn=cmd_answer)
    answer = s

    s = sub.add_parser("unattended", help="turn unattended mode on (start) or off (stop)")
    switch = s.add_subparsers(dest="action", required=True)
    s = switch.add_parser("start", help="start unattended mode")
    repo_arg(s)
    s.add_argument("--why", required=True, help='"owner said unattended", or "no answer to <id> for 30 minutes"')
    s.add_argument("--wave", help="the spend wave the unattended run spends from")
    s.set_defaults(fn=cmd_unattended)
    s = switch.add_parser("stop", help="stop unattended mode")
    repo_arg(s)
    s.set_defaults(fn=cmd_unattended)

    s = sub.add_parser("outcome", help="record what later showed about a decision")
    repo_arg(s)
    s.add_argument("id")
    s.add_argument("--verdict", required=True, choices=VERDICTS)
    s.add_argument("--by", required=True, help="what showed it")
    s.add_argument("--pointer")
    s.set_defaults(fn=cmd_outcome)

    s = sub.add_parser("score", help="agreement, calibration and autonomy per category")
    repo_arg(s)
    s.add_argument("--category")
    s.add_argument("--since", help="YYYY-MM-DD")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_score)

    s = sub.add_parser("should-ask", help="ask, ask-blind or decide-and-log for a category")
    repo_arg(s)
    s.add_argument("category")
    s.add_argument("--seed", type=int)
    s.set_defaults(fn=cmd_should_ask)

    s = sub.add_parser("review", help="the full record, newest first, with the scores on top")
    repo_arg(s)
    s.add_argument("--since", help="YYYY-MM-DD")
    s.add_argument("--md", action="store_true", help="markdown tables")
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("render", help="regenerate DECISIONS.md from the ledger")
    repo_arg(s)
    s.set_defaults(fn=cmd_render)

    a = p.parse_args()
    if a.fn is cmd_answer and a.choice is None and not a.assumed:
        answer.error("the following arguments are required: --choice")
    global _TASK
    _TASK = getattr(a, "task", None)
    try:
        a.fn(a)
    except SettingsError as e:
        fail(str(e), "decision:unattended-config")


if __name__ == "__main__":
    main()
