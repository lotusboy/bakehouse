"""Regression: the example project's walkthrough, run end to end as a new user would run it.

It runs, in a scratch HOME, every bash block of examples/notes-app/README.md steps 1 and 3 (make the copy,
set up, preflight, status) and then of examples/notes-app/WALKTHROUGH.md, in order. The two README lines
that clone the repo and copy the example out are replaced by a copy of this checkout's example. Step 2
(installing the plugin) needs Claude Code, so this checkout's bin/ is put on the PATH instead, which is
what the plugin does.

Each block must succeed, except one whose shown output ends "REFUSED", which must fail. Each line of the
output the docs show after a block (other than "...") must appear in what the block printed, in order.
So a change to a tool's output, or to the example, that leaves the docs wrong fails here.

Needs Node.js 18 or later and npm, as the example does; skipped without them, except in CI.
python3 -m unittest runtime/tests/test_regression_walkthrough.py
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "notes-app"
README = EXAMPLE / "README.md"
WALKTHROUGH = EXAMPLE / "WALKTHROUGH.md"
# the README lines that fetch the example; the test copies this checkout's example instead
FETCH = ("git clone https://github.com/lotusboy/bakehouse.git", "cp -R bakehouse/examples/notes-app ~/notes-app")
HAVE_NODE = bool(shutil.which("node") and shutil.which("npm"))


def blocks(text):
    """Each ```bash block, in order, with the plain ``` block that shows its output, if one follows it
    before the next bash block."""
    out = []
    for lang, body in re.findall(r"^```(\w*)\n(.*?)^```$", text, re.S | re.M):
        if lang == "bash":
            out.append([body, None])
        elif lang == "" and out and out[-1][1] is None:
            out[-1][1] = body
    return out


def section(text, start, end):
    i, j = text.index(start), text.index(end)
    return text[i:j]


@unittest.skipUnless(HAVE_NODE or os.environ.get("CI"), "needs node and npm")
class Walkthrough(unittest.TestCase):
    def test_the_readme_and_walkthrough_run_as_written(self):
        readme = README.read_text()
        step1 = blocks(section(readme, "## 1. ", "## 2. "))
        step3 = blocks(section(readme, "## 3. ", "## 4. "))
        walk = blocks(WALKTHROUGH.read_text())
        self.assertEqual((len(step1), len(step3)), (1, 1), "README steps 1 and 3 each have one bash block")
        self.assertGreaterEqual(len(walk), 8)
        lines = step1[0][0].splitlines()
        for f in FETCH:
            self.assertIn(f, lines, "README step 1 no longer fetches the example this way; update this test")
        step1[0][0] = "\n".join(line for line in lines if line not in FETCH) + "\n"

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            home = t / "home"
            home.mkdir()
            shutil.copytree(EXAMPLE, home / "notes-app")
            (t / "gitconfig").write_text("[user]\n\tname = Walkthrough\n\temail = walkthrough@example.invalid\n")
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("BAKEHOUSE_", "DECISION_", "CLAUDE_", "GIT_"))}
            env.update(HOME=str(home), GIT_CONFIG_GLOBAL=str(t / "gitconfig"), GIT_CONFIG_NOSYSTEM="1",
                       BAKEHOUSE_MACHINE_DIR=str(t / "machine"), TMPDIR=str(t),
                       PATH=os.pathsep.join([str(ROOT / "bin"), env.get("PATH", "")]))
            for n, (script, shown) in enumerate(step1 + step3 + walk, 1):
                p = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], cwd=home / "notes-app",
                                   env=env, capture_output=True, text=True, timeout=600)
                printed = p.stdout + p.stderr
                where = f"block {n}:\n{script}\n--- printed (exit {p.returncode}):\n{printed}"
                refused = bool(shown) and shown.rstrip().endswith("REFUSED.")
                if refused:
                    self.assertNotEqual(p.returncode, 0, where)
                else:
                    self.assertEqual(p.returncode, 0, where)
                at = 0
                for want in (shown or "").splitlines():
                    if want.strip() in ("", "..."):
                        continue
                    found = printed.find(want, at)
                    self.assertGreaterEqual(found, 0, f"the docs show {want!r}, not printed in order; {where}")
                    at = found + len(want)


if __name__ == "__main__":
    unittest.main()
