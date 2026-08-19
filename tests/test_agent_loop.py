"""Doom-loop detection and the write deny-list.

There are no permission prompts and no human in the loop mid-turn, so a model
that gets stuck repeating one tool call will repeat it until the context fills
or the user gives up. `_doom_round` is what stops that, and it has to refuse a
genuinely stuck call without tripping on a tool that is legitimately called
several times with different arguments.
"""

from pathlib import Path

from agent_server.agent import DOOM_ABORT_ROUNDS, DOOM_ROUNDS, _doom_history, _doom_round
from agent_server.permissions import is_denied


def _call(name: str, args: str = "{}", call_id: str = "c"):
    return {"id": call_id, "function": {"name": name, "arguments": args}}


def _fresh(session_id: str):
    _doom_history.pop(session_id, None)
    from agent_server.agent import _doom_recorded

    _doom_recorded.pop(session_id, None)


def test_identical_call_is_refused_after_the_threshold():
    _fresh("s1")
    calls = [_call("read", '{"filePath": "a.py"}')]
    for turn in range(DOOM_ROUNDS - 1):
        refuse, fatal = _doom_round("s1", calls, f"assistant-{turn}")
        assert refuse == set() and not fatal

    refuse, fatal = _doom_round("s1", calls, f"assistant-{DOOM_ROUNDS - 1}")
    assert refuse and not fatal


def test_varying_arguments_never_trip_the_guard():
    """Reading twenty different files is progress, not a loop."""
    _fresh("s2")
    for turn in range(DOOM_ABORT_ROUNDS + 2):
        refuse, fatal = _doom_round(
            "s2", [_call("read", f'{{"filePath": "f{turn}.py"}}')], f"a{turn}"
        )
        assert refuse == set() and not fatal


def test_the_run_is_aborted_once_it_is_hopeless():
    _fresh("s3")
    calls = [_call("bash", '{"command": "ls"}')]
    fatal = False
    for turn in range(DOOM_ABORT_ROUNDS):
        _, fatal = _doom_round("s3", calls, f"a{turn}")
    assert fatal


def test_the_same_assistant_turn_is_only_counted_once():
    """`_drain_pending` re-reads the message list on every pass, so one stuck
    turn must not be able to advance the counter by itself."""
    _fresh("s4")
    calls = [_call("read", '{"filePath": "a.py"}')]
    for _ in range(DOOM_ABORT_ROUNDS + 5):
        refuse, fatal = _doom_round("s4", calls, "the-same-turn")
        assert not fatal
    assert refuse == set()


def test_argument_order_does_not_disguise_a_repeat():
    """Keys are serialised sorted, so the same call written two ways is one
    call — otherwise a model could loop forever by reordering its JSON."""
    _fresh("s5")
    a = [_call("edit", '{"filePath": "x.py", "startLine": 1}')]
    b = [_call("edit", '{"startLine": 1, "filePath": "x.py"}')]
    for turn, calls in enumerate([a, b, a]):
        refuse, _ = _doom_round("s5", calls, f"a{turn}")
    assert refuse


def test_denied_paths_are_never_writable():
    for path in ("/proc/self/mem", "/sys/kernel/x", "/etc/shadow", "/boot/vmlinuz"):
        assert is_denied(Path(path)) is True


def test_ordinary_paths_are_writable():
    for path in ("/home/user/project/main.py", "/tmp/scratch.txt", "/etc/hosts"):
        assert is_denied(Path(path)) is False
