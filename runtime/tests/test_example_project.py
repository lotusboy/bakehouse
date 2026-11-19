"""Tests for examples/notes-app, the example project a new user tries bakehouse on. Its team files are the
ones the getting-started guide writes, word for word, so the guide and the example cannot drift apart; a
git copy of it passes preflight for its work order T1; every relative link in the example's README
resolves. Standard library only; the preflight test builds a scratch repository in a temporary folder.
python3 -m unittest runtime/tests/test_example_project.py
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
GUIDE = ROOT / "docs" / "GETTING-STARTED.md"

# each team file of the example, and the guide step whose code block writes it
FROM_THE_GUIDE = {
    ".team/gates.json": ("## 3. Write the gate list", "json"),
    "tools/measure.mjs": ("## 4. Write the measure step", "js"),
    ".team/binding.json": ("## 5. Write the binding", "json"),
    "docs/TRACKER.md": ("## 6. Write the tracker item", "markdown"),
}


def guide_block(heading, lang):
    m = re.search(re.escape(heading) + r".*?```" + lang + r"\n(.*?)```", GUIDE.read_text(), re.S)
    assert m, f"no ```{lang} block after {heading!r} in the guide"
    return m.group(1)


class ExampleProject(unittest.TestCase):
    def test_each_team_file_is_the_guides_code_block(self):
        for path, (heading, lang) in FROM_THE_GUIDE.items():
            with self.subTest(path=path):
                self.assertEqual((EXAMPLE / path).read_text(), guide_block(heading, lang))

    def test_a_git_copy_passes_preflight_for_t1(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            repo = t / "notes-app"
            shutil.copytree(EXAMPLE, repo)
            (t / "home").mkdir()
            env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
            env.update(HOME=str(t / "home"), BAKEHOUSE_MACHINE_DIR=str(t / "machine"))
            for args in (("init", "-q", "-b", "main"),
                         ("worktree", "add", "-q", "--orphan", "-b", "bakehouse-records",
                          str(t / "notes-app.bakehouse-records")),
                         ("config", "bakehouse.records", str(t / "notes-app.bakehouse-records"))):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)
            p = subprocess.run(["python3", str(ROOT / "runtime" / "team-preflight.py"), "change", "T1",
                                "--repo", str(repo)], capture_output=True, text=True, env=env)
            out = p.stdout + p.stderr
            self.assertEqual(p.returncode, 0, out)
            self.assertRegex(out.strip().splitlines()[-1], r": ok \(binding ")

    def test_every_relative_link_in_the_readme_resolves(self):
        readme = EXAMPLE / "README.md"
        links = re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", readme.read_text())
        self.assertTrue(links)
        for link in links:
            if "://" in link:
                continue
            with self.subTest(link=link):
                self.assertTrue((readme.parent / link).resolve().exists(), link)


if __name__ == "__main__":
    unittest.main()
