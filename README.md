# bakehouse

A project team for Claude Code. Work in progress: not ready for use.

Python: the tools need Python 3.10 or later. The plugin's hooks run the first `python3` on the hook's
PATH and still load under Python 3.9 (macOS's `/usr/bin/python3`), so the dispatch gate and the session
start keep working there.
