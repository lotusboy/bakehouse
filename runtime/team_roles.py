"""Role resolution for the project team: the one implementation of the rules in docs/PROJECT-TEAM.md, "Roles".

Used by team-preflight.py (check 5) and the agent-roles hook. Standard library only.

A dispatch names `<job>:<role>` or `<role>`. Resolution checks three levels in order: the repo
binding's `roles`, the owner's personal `agent-roles.json` in the machine folder (locator.machine_dir()), the
shipped `agent-roles.json` beside this file.
At each level the key `<job>:<role>` is tried first, then `<role>`. A level matches when it names an
agent installed for the working directory; a level that names only agents not installed is skipped,
and the skip is reported. The first match wins. When nothing matches, a review role resolves to the
built-in `general-purpose`; any other role is left to the session.
"""
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import locator

HERE = Path(__file__).resolve().parent
LOCATOR = HERE / "locator.py"

BUILT_IN = {"general-purpose", "Explore", "Plan", "claude-code-guide", "statusline-setup", "claude", "fork"}
REVIEW_ROLES = {"reviewer", "code-review"}
REVIEW_FALLBACK = "general-purpose"
RESOLVE_TIMEOUT = 2  # seconds; the hook's whole budget is 10


def frontmatter_name(path):
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().split("\n")
    except (OSError, ValueError):
        return None
    if not lines or lines[0].strip() != "---":
        return path.stem
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return path.stem


def enabled_plugins(cwd):
    enabled = {}
    for p in (Path.home() / ".claude" / "settings.json", Path(cwd) / ".claude" / "settings.json",
              Path(cwd) / ".claude" / "settings.local.json"):
        try:
            enabled.update(json.load(open(p)).get("enabledPlugins", {}))
        except (OSError, ValueError):
            pass
    return [k for k, v in enabled.items() if v]


def installed_agents(cwd):
    """Every agent name this session could dispatch: built-ins, user, project, enabled plugins."""
    names = set(BUILT_IN)
    for d in (Path.home() / ".claude" / "agents", Path(cwd) / ".claude" / "agents"):
        for f in d.glob("*.md"):
            n = frontmatter_name(f)
            if n:
                names.add(n)
    try:
        installs = json.load(open(Path.home() / ".claude" / "plugins" / "installed_plugins.json"))["plugins"]
    except (OSError, ValueError, KeyError):
        installs = {}
    for key in enabled_plugins(cwd):
        entries = installs.get(key, [])
        # a project-scope install for this folder wins over the user-scope one
        pick = next((e for e in entries if e.get("scope") == "project"
                     and str(cwd).startswith(e.get("projectPath", "\0"))), None)
        pick = pick or next((e for e in entries if e.get("scope") == "user"), None)
        if not pick:
            continue
        plugin = key.split("@")[0]
        for f in Path(pick["installPath"], "agents").glob("*.md"):
            n = frontmatter_name(f)
            if n:
                names.add(f"{plugin}:{n}")
    return names


def table_roles(path):
    try:
        roles = json.loads(Path(path).read_text()).get("roles")
    except (OSError, ValueError, AttributeError):
        return {}
    return roles if isinstance(roles, dict) else {}


def providers(entry):
    """A binding entry is an agent name, a list of names, or the table form {"providers": [...]}
    (which may also set a model); a table entry is {"providers": [...]}.
    Anything else names no agent (a string `providers` is not read character by character)."""
    if isinstance(entry, dict):
        entry = entry.get("providers")
    elif isinstance(entry, str):
        return [entry]
    return [p for p in entry if isinstance(p, str)] if isinstance(entry, list) else []


def locate(field, path, log=None, timeout=RESOLVE_TIMEOUT, env=os.environ):
    """(the locator's `field` for the project of the repo at path, or None when it has no team; problem or
    None). The locator runs as its own process, so a slow git is cut at timeout. problem says why the project
    cannot be known: the locator cannot be run, did not answer within timeout, or timeout left it no time at
    all, or the project needs setup (the locator's own message); the reason also goes to log. env: a lookup
    for a path a brief names passes one without CLAUDE_PROJECT_DIR, so that path's own repo answers."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        return None, None
    # git needs a folder that exists: a record folder named before it is made is in its nearest parent's repo
    while not p.is_dir() and p != p.parent:
        p = p.parent
    cmd = f"{LOCATOR} {field} {p}"
    if timeout <= 0:
        why = f"the project locator `{cmd}` was not run: no time was left before the deadline"
        if log:
            log(f"SKIP binding level: {why}")
        return None, why
    try:
        proc = subprocess.Popen([sys.executable, str(LOCATOR), field, str(p)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(env),
                                start_new_session=True)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as e:
            # kill the whole group: a child left holding the pipe would block the read
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            if log:
                log(f"SKIP binding level: cannot locate the project for {path} ({e!r})")
            return None, f"the project locator `{cmd}` did not answer within {timeout:.1f} s"
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        if log:
            log(f"SKIP binding level: cannot locate the project for {path} ({e!r})")
        return None, f"the project locator `{LOCATOR}` cannot be run ({e})"
    if proc.returncode == locator.NO_TEAM:
        return None, None
    if proc.returncode == locator.SETUP_NEEDED:
        return None, err.strip()
    if proc.returncode or not out.strip():
        why = f"the project locator `{cmd}` cannot be run (exit {proc.returncode}: {err.strip()[-300:]})"
        if log:
            log(f"SKIP binding level: {why}")
        return None, why
    return out.strip(), None


def load_binding(path, log=None):
    """The binding for the repo at path, or {} when there is none or it cannot be read (read_binding
    tells the two apart)."""
    return read_binding(path, log)[0]


def read_binding(path, log=None, timeout=RESOLVE_TIMEOUT, env=os.environ):
    """(binding, problem) for the repo at path. ({}, None): there is no binding (the locator finds no team
    there). ({}, problem): there may be one, and it cannot be read; problem names the cause and what to fix:
    the locator cannot be run or did not answer in time, the project needs setup, or binding.json does not
    parse, is not a JSON object, cannot be read or is a dangling symlink."""
    where, why = locate("binding", path, log, timeout, env)
    if why or not where:
        return {}, why
    bpath = Path(where)
    try:
        b = json.loads(bpath.read_text())
    except FileNotFoundError:
        if bpath.is_symlink():
            return {}, f"the binding {bpath} is a symlink to {os.readlink(bpath)}, which does not exist: fix the link"
        return {}, None
    except (OSError, ValueError) as e:
        return {}, f"the binding {bpath} cannot be read ({e}): fix that file (team-preflight.py names what is wrong)"
    if not isinstance(b, dict):
        return {}, f"the binding {bpath} is not a JSON object: fix that file"
    return b, None


def shipped_table():
    """The shipped table. Before the move-2 swap the new one waits beside the live one as
    agent-roles.next.json; preflight reads it so it agrees with the hook after the swap."""
    nxt = HERE / "agent-roles.next.json"
    return nxt if nxt.is_file() else HERE / "agent-roles.json"


def personal_table():
    return locator.machine_dir() / "agent-roles.json"


def levels(binding):
    """The three resolution levels, in order, as (label, table)."""
    roles = binding.get("roles") if isinstance(binding, dict) else None
    shipped = shipped_table()
    return [("binding roles", roles if isinstance(roles, dict) else {}),
            ("personal agent-roles.json", table_roles(personal_table())),
            (f"runtime/{shipped.name}", table_roles(shipped))]


def resolve(spec, lvls, have):
    """Resolve `<job>:<role>` or `<role>`.

    Returns {agent, where, key, skipped, fallback, entry}: agent is None when the session does it;
    skipped lists (where, key, providers) for each level passed over; fallback is None,
    "general-purpose" or "session". entry is the dispatched role's own table entry (first in
    level order, job-qualified key first), whatever level matched: a resolved agent that no table
    lists as a provider takes that entry's model, and nothing else from it."""
    job, _, role = spec.rpartition(":")
    keys = [f"{job}:{role}", role] if job else [role]
    res = {"agent": None, "where": None, "key": None, "skipped": [], "fallback": None,
           "entry": next((t[k] for _, t in lvls for k in keys if isinstance(t.get(k), dict)), None)}
    for where, table in lvls:
        for key in keys:
            if key not in table:
                continue
            provs = providers(table[key])
            pick = next((p for p in provs if p in have), None)
            if pick:
                res.update(agent=pick, where=where, key=key)
                return res
            if provs:
                res["skipped"].append((where, key, provs))
    if role in REVIEW_ROLES:
        res.update(agent=REVIEW_FALLBACK, fallback=REVIEW_FALLBACK)
    else:
        res["fallback"] = "session"
    return res


def known_role(spec, lvls):
    job, _, role = spec.rpartition(":")
    keys = [f"{job}:{role}", role] if job else [role]
    return any(k in table for _, table in lvls for k in keys)


def role_of(agent_type, tables):
    """(role name, entry) of the first table entry listing agent_type as a provider."""
    if not agent_type:
        return None, None
    for table in tables:
        for name, r in table.items():
            if not isinstance(r, dict):
                continue
            for p in providers(r):
                # a plugin agent may be reported with or without its plugin prefix
                if agent_type == p or p.split(":")[-1] == agent_type:
                    return name, r
    return None, None
