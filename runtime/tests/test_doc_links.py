"""Every relative link in the pages a new user reads first resolves: the file exists, and a #anchor names a
heading in it (GitHub's anchor form). Standard library only; reads the repository's own files.
python3 -m unittest runtime/tests/test_doc_links.py
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGES = ("README.md", "docs/GETTING-STARTED.md", "examples/notes-app/README.md")
LINK = re.compile(r"\]\(([^)\s]+)\)")


def anchor(heading):
    """GitHub's anchor for a heading: lower case, punctuation other than - and _ dropped, spaces to -."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def anchors(page):
    text = re.sub(r"```.*?```", "", page.read_text(), flags=re.S)
    return {anchor(h) for h in re.findall(r"^#{1,6} +(.+)$", text, re.M)}


class DocLinks(unittest.TestCase):
    def test_the_anchor_form(self):
        self.assertEqual(anchor("9. Run the first change"), "9-run-the-first-change")
        self.assertEqual(anchor("README, \"Install\""), "readme-install")

    def test_every_relative_link_resolves(self):
        for name in PAGES:
            page = ROOT / name
            links = LINK.findall(page.read_text())
            self.assertTrue(links, name)
            for link in links:
                if "://" in link or link.startswith("mailto:"):
                    continue
                with self.subTest(page=name, link=link):
                    path, _, frag = link.partition("#")
                    target = (page.parent / path).resolve() if path else page
                    self.assertTrue(target.exists(), f"{name}: {link}: no such file")
                    if frag:
                        self.assertIn(frag, anchors(target), f"{name}: {link}: no such heading")


if __name__ == "__main__":
    unittest.main()
