"""Permission policy: everything is auto-approved.

This app is for people who should never have to answer "may I run this?" — the
agent just does the work. The only hard lines are a handful of machine-critical
paths that no tool may ever write, and the destructive-command guard inside the
`bash` tool itself (`rm -rf /` and friends). Both refuse and explain rather than
ask, so the model can tell the user in plain words what it avoided.
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


async def check(
    name: str,
    args: dict,
    session_id: str,
    project_dir: str,
    shell_auto_approve: bool,
) -> dict | None:
    """Always allow. The `bash` tool and the write tools enforce the few hard
    guards themselves; the user is never interrupted with a permission prompt.
    """
    return None
