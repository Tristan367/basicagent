"""The few paths no tool may ever write to.

This app has no permission prompts. The user should never have to answer "may I
run this?" — the agent just does the work. What replaces the prompt is a small
number of hard guards that refuse and explain, so the model can tell the user in
plain words what it avoided and why:

* this deny-list, checked by the write tools and by the subagent guard, and
* the destructive-command guard in `tools/bash.py` (`rm -rf /` and friends).

Deliberately short. A long list would start refusing ordinary work, and the
thing it is actually protecting against is a confused model wrecking the
machine, not a hostile one.
"""

from pathlib import Path

# Never writable, under any circumstances.
DENIED_PREFIXES = (
    "/proc", "/sys", "/dev", "/boot", "/etc/shadow", "/etc/sudoers",
)


def is_denied(path: Path) -> bool:
    try:
        text = str(path.resolve())
    except OSError:
        text = str(path)
    return text.startswith(DENIED_PREFIXES)
