"""Shell execution."""

import asyncio
import os
import re
import shlex

from agent_server import processes
from agent_server.config import MAX_TOOL_RESULT_CHARS
from agent_server.tools.base import ToolContext, ToolResult, truncate

DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 600_000

# Commands that only observe state. Used to keep the approval prompt from firing
# on every `ls`, and surfaced in the UI as "read-only".
READ_ONLY_PREFIXES = {
    "ls", "cat", "head", "tail", "pwd", "whoami", "date", "echo", "which", "type",
    "file", "stat", "wc", "du", "df", "tree", "find", "grep", "rg", "fd", "env",
    "printenv", "uname", "hostname", "id", "ps", "top", "uptime", "history",
}
GIT_READ_ONLY = {"status", "log", "diff", "show", "branch", "remote", "blame", "describe", "rev-parse"}

# Paths whose recursive deletion destroys the machine rather than the project.
# A `rm -rf build/` is fine; `rm -rf /` is not.
PROTECTED_RM_TARGETS = {
    "/", "/*", "/.", "/..", "~", "~/", "$HOME", "${HOME}", "$HOME/",
    "/home", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64",
    "/boot", "/var", "/opt", "/root", "/srv", "/mnt", "/proc", "/sys", "/dev",
}
# The real home directory, spelled out. `~` and `$HOME` were covered but the
# expanded path was not, and a model that has just run `pwd` writes the
# expanded path.
PROTECTED_RM_TARGETS.add(os.path.expanduser("~"))
_BLOCK_DEV_RE = re.compile(r"/dev/(sd[a-z]+|hd[a-z]+|nvme\d+n\d+|vd[a-z]+|xvd[a-z]+|mmcblk\d+|disk|mapper)")

# The same guard for the computer most people using this actually have.
#
# Everything above is POSIX-shaped: `rm -rf /`, `/dev/sda`, a fork bomb written
# in shell. None of it can even be typed on Windows, where the shell is cmd and
# the commands are `del`, `rd` and `format` -- so on Windows this guard was a
# guard against nothing at all, protecting the one group of users who cannot
# possibly notice what went wrong or put it back.
#
# Written with the same restraint as the POSIX half: it fires on the handful of
# things that take the machine with them, and never on `rd /s /q build` or
# `del /q *.log`, which are ordinary and correct.
_WINDOWS_DRIVE_ROOT = re.compile(r"^[a-z]:[\\/]?\*?$")
_WINDOWS_PROTECTED = {
    r"%userprofile%", r"%homepath%", r"%systemroot%", r"%windir%",
    r"%programfiles%", r"%programdata%", r"%appdata%", r"%localappdata%",
    r"$env:userprofile", r"$env:systemroot", r"$env:windir", r"$home",
    r"c:\windows", r"c:\users", r"c:\program files", r"c:\programdata",
    r"c:/windows", r"c:/users",
}


def _windows_target_is_protected(token: str) -> bool:
    """Whether a path is one whose recursive deletion ends the computer.

    The trailing `\\*` matters and is easy to miss: `del /s /q %USERPROFILE%`
    is an error, and `del /s /q %USERPROFILE%\\*` is somebody's photographs.
    Every wildcard and separator on the end comes off before comparing.
    """
    bare = token.strip("\"'").lower()
    if _WINDOWS_DRIVE_ROOT.match(bare):
        return True
    stripped = bare.rstrip("*").rstrip("\\/") or bare
    return stripped in _WINDOWS_PROTECTED


def _windows_danger(command: str) -> str | None:
    """The Windows half of `danger_reason`. None when the command is fine."""
    lowered = command.lower()

    # Formatting a drive, or repartitioning one. There is no version of either
    # that belongs in a project folder.
    if re.search(r"\bformat\s+[a-z]:", lowered):
        return "format of a drive"
    if re.search(r"\b(diskpart|bcdedit)\b.*\b(clean|delete|format)\b", lowered):
        return "repartitioning the disk"

    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        tokens = command.split()
    lowered_tokens = [t.lower() for t in tokens]
    if not lowered_tokens:
        return None

    verbs = {os.path.basename(t.strip('"\'')) for t in lowered_tokens}
    recursive = any(t in ("/s", "-recurse", "-r") for t in lowered_tokens)
    # `del` needs /s to be recursive; `rd` and `rmdir` need it to remove a tree
    # at all; Remove-Item needs -Recurse. Without one of those, a protected
    # path is at worst an empty-directory error.
    if not recursive:
        return None
    if not (verbs & {"del", "erase", "rd", "rmdir", "remove-item", "ri", "del.exe"}):
        return None

    for token in tokens:
        if token.startswith(("/", "-")) and len(token) <= 12:
            continue  # a flag, not a path
        if _windows_target_is_protected(token):
            return f"recursive delete of {token}"
    return None


def _has_flag(tokens: list[str], flag: str, long: str = "") -> bool:
    """True when a short flag (possibly in a `-rf` cluster) or its `--long`
    spelling is present. Long options never match short flags: `--force` is not
    `-f`, so `rm --force /` must not be mistaken for `rm -rf`. """
    for t in tokens:
        if long and t == long:
            return True
        if not t.startswith("-") or t.startswith("--"):
            continue
        if flag in t[1:].lower():
            return True
    return False


def danger_reason(command: str) -> str | None:
    """Why `command` must not run, or None when it is allowed.

    A guard against the commands that take the machine down with them, not just
    the project. Deliberately conservative: it only fires on the obvious
    catastrophes and never on an ordinary `rm -rf build/` or `git clean`.
    """
    s = command.strip()

    # Classic fork bomb.
    if re.search(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;", s):
        return "fork bomb"

    # rm with recursive+force flags targeting a protected path. Match by token
    # basename so a path-qualified `/bin/rm` is caught as well as a bare `rm`.
    #
    # Tokenised the way a shell does, because `str.split` keeps the quotes: it
    # sees `rm -rf "/"` as the token `"/"`, which is not the string `/`, and
    # waved the command through. Models quote paths as a matter of habit.
    try:
        tokens = shlex.split(s)
    except ValueError:
        tokens = s.split()
    if (
        any(os.path.basename(t) == "rm" for t in tokens)
        and _has_flag(tokens, "r", "--recursive")
        and _has_flag(tokens, "f", "--force")
    ):
        for tok in tokens:
            if tok.startswith("-"):
                continue
            target = tok.rstrip("/") or "/"
            if target in PROTECTED_RM_TARGETS:
                return f"rm -rf of {tok}"

    # Writing directly to a raw block device.
    if _BLOCK_DEV_RE.search(s) and re.search(r"\b(dd|mkfs\S*|fdisk|parted|sfdisk)\b", s):
        return "raw disk write"
    if re.search(r"[>]\s*" + _BLOCK_DEV_RE.pattern, s):
        return "raw disk write"

    # Checked on every computer, not only Windows. The guard costs nothing here
    # and a project *is* sometimes built on one machine for another -- but the
    # real reason is that a rule which only runs where it was written is a rule
    # that gets forgotten the moment somebody develops on Linux, which is the
    # exact situation this was written in.
    return _windows_danger(s)


def is_read_only(command: str) -> bool:
    """Conservative check: every segment of the pipeline must be observational."""
    stripped = command.strip()
    if not stripped:
        return True
    # Anything that can redirect into a file or chain unknown commands is unsafe.
    # A newline is a command separator exactly like `;`, and `&` both backgrounds
    # and separates. Leaving them out meant a subagent -- which is allowed only
    # observational commands -- could send "ls\nrm -rf build" and have it
    # judged read-only on the strength of the first line. `<(` and `>(` run a
    # command too, whatever the surrounding one does with the result.
    if any(tok in stripped for tok in (
        ">", ">>", "&", "|&", ";", "`", "$(", "<(", ">(", "\n", "\r", "sudo"
    )):
        return False
    for segment in stripped.split("|"):
        try:
            parts = shlex.split(segment)
        except ValueError:
            return False
        if not parts:
            return False
        cmd = os.path.basename(parts[0])
        if cmd == "git":
            if not _git_read_only(parts):
                return False
            continue
        if cmd == "find" and any(a in ("-delete", "-exec", "-execdir", "-ok", "-okdir") for a in parts[1:]):
            return False
        if cmd not in READ_ONLY_PREFIXES:
            return False
    return True


def _git_read_only(parts: list[str]) -> bool:
    """`git <sub>` is observational only when the subcommand and its flags are.

    `git branch` lists, but `git branch -D` deletes; `git remote` lists, but
    `git remote add` mutates config. The bare subcommand whitelist alone was
    therefore wrong.
    """
    if len(parts) < 2 or parts[1] not in GIT_READ_ONLY:
        return False
    flags = parts[2:]
    sub = parts[1]
    destructive = (
        (sub == "branch" and any(a in ("-d", "-D", "--delete", "-m", "-M") for a in flags))
        or (sub == "remote" and any(a in ("add", "remove", "rm", "set-url", "set-head") for a in flags))
    )
    return not destructive


async def run_bash(
    ctx: ToolContext,
    *,
    command: str,
    timeout: int | None = None,
    workdir: str | None = None,
    env: dict[str, str] | None = None,
    **_,
) -> ToolResult:
    if not command or not command.strip():
        return ToolResult.error("empty command", "bash")

    reason = danger_reason(command)
    if reason:
        return ToolResult.error(
            f"refusing to run destructive command ({reason}). "
            "The guard only blocks machine-destroying commands; be explicit if "
            "you meant a scoped deletion.",
            "bash",
        )

    # There is nowhere to prompt for a password: this app has no permission UI
    # and the user may be listening rather than looking. `-S` makes sudo read
    # the password from stdin, which is then closed immediately, so it fails in
    # a second with a clear message instead of hanging until the timeout.
    has_sudo = "sudo" in command.split()
    if has_sudo:
        command = re.sub(r"\bsudo\b", "sudo -S", command, count=1)

    timeout_ms = min(timeout or DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS)
    timeout_sec = timeout_ms / 1000
    cwd = str(ctx.resolve(workdir)) if workdir else ctx.project_dir
    if not os.path.isdir(cwd):
        cwd = ctx.project_dir
    title = command.strip().splitlines()[0][:90]

    proc = None
    detached = False
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if has_sudo else asyncio.subprocess.DEVNULL,
            cwd=cwd,
            # In a group of its own, so `_kill` can reach whatever it starts.
            # Windows needs a different flag for the same thing, which is why
            # this is not `start_new_session=True` written here.
            **processes.spawn_kwargs(),
            env={**os.environ, "TERM": "dumb", "NO_COLOR": "1", "PAGER": "cat", **(env or {})},
        )
        if has_sudo and proc.stdin is not None:
            proc.stdin.close()
        stdout, stderr, detached = await asyncio.wait_for(
            _collect(proc), timeout=timeout_sec
        )
    except TimeoutError:
        _kill(proc)
        return ToolResult.error(f"command timed out after {timeout_sec:g}s: {command}", title)
    except asyncio.CancelledError:
        _kill(proc)
        raise
    except Exception as e:
        return ToolResult.error(f"failed to execute: {e}", title)

    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()
    code = proc.returncode

    parts = []
    if out:
        parts.append(out)
    if err:
        parts.append(f"[stderr]\n{err}")
    if code != 0:
        parts.append(f"[exit code {code}]")
    if detached:
        # The shell exited but something it spawned still holds the output pipe,
        # i.e. a real background process. Say so, otherwise the model sees a
        # suspiciously empty result and retries a server it already started.
        parts.append(
            "[note] the shell exited and left a background process running. "
            "Nothing in this app can stop it -- not the Stop button, not closing "
            "the project, not deleting it -- and its output is no longer being "
            "captured. If it is a server the user needs, stop it and start it "
            "with `preview` instead, which owns what it starts. Two servers go "
            "in one `preview` command with `&` between them."
        )
    if has_sudo and code != 0:
        # Otherwise the model reads "sudo: no password was provided" as a bug in
        # its own command and retries it verbatim until the doom-loop guard fires.
        parts.append(
            "[note] sudo cannot be used here: there is no way to ask the user for "
            "their password. Find a way that does not need administrator rights "
            "(for example install into the user's own folder), or tell the user "
            "the exact command to run themselves."
        )
    body = "\n".join(parts) or "(no output)"

    return ToolResult(
        output=truncate(body, MAX_TOOL_RESULT_CHARS, spill=True),
        is_error=code != 0,
        title=f"{title} (exit {code})",
    )


# How long to keep reading after the shell itself has exited. Only matters when
# a background grandchild inherited the pipe; a normal command's pipes are
# already at EOF by then, so this costs nothing in the common case.
BACKGROUND_DRAIN_SEC = 0.25


async def _collect(proc) -> tuple[bytes, bytes, bool]:
    """Read stdout/stderr, but stop waiting once the shell itself has exited.

    `communicate()` waits for the pipes to reach EOF, not for the process to
    exit. `python3 -m http.server &` exits the shell immediately while the
    server inherits the pipe and holds it open for as long as it runs, so
    communicate() blocks for the full timeout and the process group then gets
    killed -- taking the server with it. Waiting on the shell instead means a
    backgrounded command returns immediately, as the user expects.
    """
    out: list[bytes] = []
    err: list[bytes] = []

    async def drain(stream, sink):
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                return
            sink.append(chunk)

    readers = [
        asyncio.create_task(drain(proc.stdout, out)),
        asyncio.create_task(drain(proc.stderr, err)),
    ]
    try:
        # NB: neither communicate() nor wait() can be used here. Both only
        # resolve once every pipe has disconnected (see _try_finish in
        # asyncio/base_subprocess.py), which is precisely what a background
        # grandchild prevents. `returncode` is set as soon as the child exits,
        # independently of the pipes, so poll that instead.
        while proc.returncode is None:
            await asyncio.sleep(0.02)
        # Give whatever is already buffered a moment to arrive.
        _, pending = await asyncio.wait(readers, timeout=BACKGROUND_DRAIN_SEC)
        detached = bool(pending)
    finally:
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)

    return b"".join(out), b"".join(err), detached


def _kill(proc):
    """Stop a command that ran over its time, and everything it started.

    This used to reach for `os.killpg` and `signal.SIGKILL` directly, neither
    of which exists on Windows -- and the `except (ProcessLookupError,
    PermissionError, OSError)` around it does not catch an AttributeError. So
    on Windows the app's answer to "that command took too long" was a crash in
    the cleanup path.
    """
    processes.kill_tree(proc)
