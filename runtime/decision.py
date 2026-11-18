#!/usr/bin/env python3
"""The decision ledger: every question put to the owner, the agent's recommendation and confidence,
the owner's choice, and what happened later. Scores agreement and calibration per category, and says
when a category has earned autonomy. Pure computation; no AI. Called through runtime/decision.sh.

Ledger: <records>/decisions/ledger.jsonl, append-only; the source of truth for scoring. Three event
kinds share one decision id:
  ask      the question, options, recommendation, confidence, evidence, blind, autonomous
  answer   the owner's choice and whether it agreed with the recommendation
An ask may name its approving option (--approves): the one choice that approves an exception to
`predict-gate.sh close`. Agreeing with the recommendation is not approval.
  outcome  later evidence that the decision held, was reversed, or was refined
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
# A blind-readers run writes its readers' reports as reader-<x>.md and its comparison as agreement.md
# (skills/blind-readers), side by side in its run folder. Only there are those names an agent's record.
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

def resolve_project(repo):
    """The project of --repo, else of the current folder (runtime/locator.py)."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"} if repo else os.environ
    try:
        project = locator.find(repo, env)
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


def read_events(project):
    p = project.decisions_ledger
    if not p.exists():
        return []
    rows = []
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


def _append(project, event):
    p = project.decisions_ledger
    p.parent.mkdir(exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(json.dumps(event, ensure_ascii=False), file=sys.stderr)
    render(project)


def heading_question(line):
    """The question of a QUESTIONS.md heading, as an older ask's questionsRef keeps it."""
    q = re.sub(r"^## Q\s*[—-]\s*", "", line)
    return re.split(r"\s+\(raised\b|\s+\[(OPEN|CLOSED|DECIDED)", q)[0].strip()


def fold(events):
    """id -> {ask, answers, outcomes}. Later answers do not erase earlier ones; the latest one counts."""
    out = {}
    for e in events:
        if e.get("kind") == "ask":
            out[e["id"]] = {"ask": e, "answers": [], "outcomes": []}
        elif e.get("id") in out:
            out[e["id"]]["answers" if e["kind"] == "answer" else "outcomes"].append(e)
    return out


# ---------- record ----------

def cmd_ask(a):
    project = resolve_project(a.repo)
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
    if not loose:
        check_grounding(a, config(project))
    cats = config(project)["categories"]
    if a.category not in cats:
        print(f"decision: note: category '{a.category}' is outside the default vocabulary ({', '.join(cats)})",
              file=sys.stderr)
    ts = stamp()
    did = a.id or "d" + ts[:10].replace("-", "") + "-" + hashlib.sha1(
        f"{ts}{project.root}{a.question}{random.random()}".encode()).hexdigest()[:6]
    if did in fold(read_events(project)):
        fail(f"id {did} already exists in {name_of(project)}", "decision:id-exists")
    ev = {"kind": "ask", "id": did, "ts": ts, "stream": name_of(project), "project": a.project or None,
          "category": a.category, "question": a.question, "options": a.option,
          "recommendation": a.recommendation or None, "confidence": conf,
          "evidence": {"kind": a.evidence_kind, "pointer": a.evidence or None},
          "blind": a.blind, "autonomous": a.autonomous, "questionsRef": None,
          "read": [{"path": r, "what": a.read_what} for r in a.read] or None,
          "task": a.task or None, "fixRound": a.fix_round, "rechecked": a.rechecked or None,
          "upstream": a.upstream or None, "steppedBack": a.stepped_back or None,
          "recheck": bool(a.recheck)}
    if a.approves is not None:
        ev["approves"] = a.approves
    if loose:
        ev["backfilled"] = True
    append(project, ev)
    print(did)


def meaningful(value, words=3):
    """A justification flag holds a statement, not a placeholder: at least `words` words with letters."""
    return len(re.findall(r"[^\W\d_]{2,}", value or "")) >= words


def blind_readers_record(path):
    """True for a reader-<x>.md or an agreement.md in a folder that holds both: a blind-readers run folder."""
    if not (READER_REPORT.fullmatch(path.name) or path.name.lower() == "agreement.md"):
        return False
    try:
        here = [p.name for p in path.parent.iterdir() if p.is_file()]
    except OSError:
        return False
    return any(READER_REPORT.fullmatch(n) for n in here) and any(n.lower() == "agreement.md" for n in here)


def check_grounding(a, cfg):
    """Refuse a question that is not grounded in a source the asker opened (owner, 2026-10-14).
    A category in the config's groundingExempt (a spend, process or scope call, which no source file
    settles) needs no --read; every other check still applies."""
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
        if not meaningful(a.read_what, 1):
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
    if a.task and a.fix_round is None and not a.recheck and re.search(r"\bfix round\b", text):
        fail(f"the question proposes a fix on {a.task}: give --fix-round N (rounds asked so far: "
             f"{sorted(asked) or 'none'})", "decision:fix-round")
    if re.search(r"\bfix round\b", text) and a.fix_round is None and not a.recheck:
        fail("the question is about a fix round: give --task and --fix-round N", "decision:fix-round")
    if a.fix_round is not None:
        if not a.task:
            fail("--fix-round needs --task (the work order id)", "decision:fix-round")
        if a.fix_round < 1:
            fail("--fix-round counts from 1", "decision:fix-round-number")
        # a fix is what the asker declares with --fix-round, not a word in the question (item 13)
        if not meaningful(a.upstream):
            fail("the question proposes a fix: give --upstream, what feeds the failing step and what you saw"
                 " when you looked at it (Seesaw: check what it was given before fixing it)", "decision:upstream")
        # the round is counted from the ledger, not taken on trust: an earlier number cannot be reused
        if asked and a.fix_round <= max(asked):
            fail(f"{a.task} already has fix round(s) {sorted(asked)}; this one is round {max(asked) + 1}",
                 "decision:fix-round-number")
        if a.fix_round >= 2 and not meaningful(a.rechecked):
            fail(f"fix round {a.fix_round} of {a.task}: give --rechecked, what you re-checked at the source"
                 " before proposing another round (a second round means the design may be wrong)", "decision:rechecked")
        if (a.fix_round >= 3 or len(asked) >= 2) and not meaningful(a.stepped_back):
            fail(f"{a.task} is at fix round {a.fix_round} (rounds asked so far: {sorted(asked) or 'none'}):"
                 " give --stepped-back, the step-back you did first — what you re-checked at the source across"
                 " the whole problem, and why another round beats re-designing (owner, 2026-10-15)",
                 "decision:stepped-back")
    if FIX_WORDS.search(text) and a.fix_round is None and not a.recheck:
        print("decision: note: the question mentions a fix; if it proposes one, give --task, --fix-round"
              " and --upstream", file=sys.stderr)


def norm(s):
    return " ".join((s or "").lower().split())


def cmd_answer(a):
    project = resolve_project(a.repo)
    d = fold(read_events(project)).get(a.id)
    if not d:
        fail(f"no decision {a.id} in {name_of(project)}", "decision:unknown-id")
    ask = d["ask"]
    if ask["autonomous"]:
        fail(f"{a.id} was decided autonomously; record what later showed with `outcome`, not `answer`",
             "decision:autonomous-answer")
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


def cmd_outcome(a):
    project = resolve_project(a.repo)
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
    """The owner's agreement, with a later reversal counting against it. None when not comparable."""
    if d["ask"]["autonomous"] or not d["answers"]:
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
    answered = [d for d in asked if d["answers"]]
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
            out.append(f"- **Owner's choice ({an['ts'][:16].replace('T', ' ')}):** {an['choice']}. "
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
                     "(decided by agent)" if q["autonomous"] else (ans["choice"] if ans else "(no answer yet)"),
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
    s.add_argument("--read-what", help="the rows, cells or lines you looked at in it")
    s.add_argument("--task", help="the work order id the question serves")
    s.add_argument("--approves", help="the option that approves a close exception (predict-gate.sh close); one of --option")
    s.add_argument("--fix-round", type=int, help="which fix round of --task this question opens")
    s.add_argument("--rechecked", help="for fix round 2+: what you re-checked at the source first")
    s.add_argument("--stepped-back", help="fix round 3+ of a task: the step-back done first")
    s.add_argument("--recheck", action="store_true",
                   help="this ask reviews a fix round of --task (a re-check); it opens no round")
    s.add_argument("--upstream", help="for a fix: what feeds the failing step, and what you saw when you looked")
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser("answer", help="record the owner's reply")
    repo_arg(s)
    s.add_argument("id")
    s.add_argument("--choice", required=True, help="verbatim")
    s.add_argument("--agreed", choices=["yes", "no"])
    s.add_argument("--notes")
    s.set_defaults(fn=cmd_answer)

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
    global _TASK
    _TASK = getattr(a, "task", None)
    a.fn(a)


if __name__ == "__main__":
    main()
