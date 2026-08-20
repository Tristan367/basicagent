"""Compaction, which is the part that breaks three hours into a session.

Every case here corresponds to a fault found in a sibling project by forcing
compaction on a scratch session and driving real turns through it. None of them
is findable by reading the code, and all of them were present here too.
"""


from agent_server import compaction
from agent_server.config import compact_threshold_for
from agent_server.system_prompt import COMPACT_PROMPT


def user(text, tokens=None):
    return {"id": 0, "role": "user", "content": text, "token_count": tokens}


def assistant(text, tokens=100, calls=None):
    return {"id": 0, "role": "assistant", "content": text, "token_count": tokens,
            "tool_calls": calls}


def tool_row(text, tokens=100):
    return {"id": 0, "role": "tool", "content": text, "token_count": tokens,
            "tool_name": "read"}


# ── what a message costs ───────────────────────────────────────────────────


def test_a_user_message_is_not_free():
    """`token_count` comes from provider usage and nothing reports a cost for
    what the user typed, so `row["token_count"] or 0` prices their turns at
    zero. That shrinks the measured context, makes the tail walk keep far more
    than its budget, and under-reports how full the session is."""
    typed = user("a" * 4000)
    assert typed["token_count"] is None
    assert compaction.message_tokens(typed) > 500


def test_a_measured_row_is_taken_at_its_word():
    assert compaction.message_tokens(assistant("hi", tokens=1234)) == 1234


def test_the_estimate_counts_tool_calls_not_just_prose():
    """An assistant turn can be almost entirely tool arguments, with no content
    at all -- worth thousands of tokens and estimated at nothing."""
    row = assistant("", tokens=None, calls=[{
        "id": "1", "type": "function",
        "function": {"name": "write", "arguments": '{"content": "' + "x" * 4000 + '"}'},
    }])
    assert compaction.message_tokens(row) > 500


# ── the kept tail ──────────────────────────────────────────────────────────


def test_the_kept_tail_scales_with_the_threshold():
    """A flat tail is wrong at both ends. Beneath a small threshold it swallows
    the conversation -- summarising one round, freeing nothing, and firing again
    next round while destroying history a message at a time."""
    small = compaction.keep_tail_budget(16_000)
    large = compaction.keep_tail_budget(871_808)
    assert small < 16_000, "the tail is as big as the threshold; compaction frees nothing"
    assert large > small * 10, "the tail did not grow with the window"


def test_the_tail_always_leaves_something_to_summarise():
    rows = [user("q"), assistant("a"), user("q2"), assistant("a2"),
            user("q3"), assistant("a3")]
    head, kept = compaction.split_for_compaction(rows, keep_tail_tokens=10)
    assert head, "nothing was summarised, so compaction frees nothing and will fire again"
    assert kept


def test_a_huge_tail_budget_still_summarises_nothing_rather_than_lying():
    rows = [user("q"), assistant("a")]
    head, kept = compaction.split_for_compaction(rows, keep_tail_tokens=10**9)
    assert head == [] and kept == rows


def test_the_kept_window_never_starts_with_an_orphaned_tool_result():
    """A kept window beginning with a tool result is a 400 on every subsequent
    request in the session."""
    rows = [
        user("go"),
        assistant("", calls=[{"id": "1", "type": "function",
                              "function": {"name": "read", "arguments": "{}"}}]),
        tool_row("contents"),
        assistant("done"),
    ]
    for budget in (1, 50, 150, 400, 10_000):
        _head, kept = compaction.split_for_compaction(rows, keep_tail_tokens=budget)
        if kept:
            assert kept[0]["role"] != "tool", f"orphan at budget {budget}"


# ── the threshold ──────────────────────────────────────────────────────────


def test_the_threshold_follows_the_model_not_a_flat_number():
    """The headroom above it has to hold one more round: the output ceiling plus
    that round's tool results. `max_output` and context both vary by more than
    tenfold, so a flat figure is wasteful for a small model and unsafe for a
    large one."""
    big = compact_threshold_for("deepseek-v4-flash")
    small = compact_threshold_for("openai/gpt-5-mini")
    assert big > small, "a million-token window compacts at the same point as a 400K one"


def test_the_threshold_leaves_room_for_a_full_length_reply():
    from agent_server.config import model_info

    for model in ("deepseek-v4-flash", "openai/gpt-5-mini", "gemini-3.7-flash"):
        info = model_info(model)
        headroom = info["context"] - compact_threshold_for(model)
        assert headroom > info.get("max_output", 8192), \
            f"{model}: one maximum-length reply would overrun the window"


def test_a_small_window_does_not_compact_on_every_turn():
    """Reserving a fixed 120,000 tokens from a 131,072-token window left the
    threshold at the floor, which is compaction as a permanent state."""
    threshold = compact_threshold_for("a-model-nobody-has-heard-of")
    from agent_server.config import model_info

    assert threshold > model_info("a-model-nobody-has-heard-of")["context"] * 0.4


# ── the summariser prompt ──────────────────────────────────────────────────


def test_the_summariser_is_told_not_to_carry_on_the_conversation():
    """It is handed the live conversation so the cached prefix matches, which
    means without being told otherwise it answers as a turn: preamble, offers to
    continue, questions."""
    # Whitespace-normalised: the prompt is wrapped, so a phrase can straddle a
    # newline and a naive substring check misses it.
    lowered = " ".join(COMPACT_PROMPT.lower().split())
    assert "nothing else" in lowered
    assert "not a turn in the conversation" in lowered


def test_the_summariser_is_told_not_to_reach_for_a_tool():
    assert "do not use any tool" in " ".join(COMPACT_PROMPT.lower().split())


def test_the_summariser_is_told_to_be_shorter():
    """Asked only to "summarise", models expand: a summary longer than its
    source frees nothing and compaction fires again immediately."""
    assert "shorter than the conversation" in " ".join(COMPACT_PROMPT.lower().split())


def test_there_is_a_nudge_for_the_empty_answer():
    """An empty reply is not a transport error, so nothing retries it, and a
    small model handed a long transcript returns nothing surprisingly often."""
    from agent_server.system_prompt import RETRY_NUDGE

    assert RETRY_NUDGE.strip()
    assert "summary" in RETRY_NUDGE.lower()


# ── a failed compaction ────────────────────────────────────────────────────


def test_a_failed_compaction_does_not_end_the_turn():
    """Compaction is housekeeping; the user asked a question. Ending the turn
    there leaves their message unanswered and the next one piles in behind it."""
    from pathlib import Path

    source = Path("agent_server/agent.py").read_text()
    block = source[source.index('yield {"type": "compacting"}'):]
    block = block[:block.index("rows = await db.get_messages")]
    # Comments stripped: this block explains at length *why* it does not return,
    # and the explanation contains the word.
    code = "\n".join(
        line for line in block.splitlines() if not line.strip().startswith("#")
    )
    assert "return" not in code, "a failed compaction still ends the turn"


# ── not compacting, which is often the right answer ────────────────────────


def test_the_tail_budget_is_a_share_of_the_conversation_not_the_threshold():
    """They are not measured in the same thing. The threshold is compared
    against the provider's reported prompt size -- system prompt included --
    while the tail walk adds up stored per-row counts. Measured on one real
    session: 12,376 against 4,118, three times apart. A tail taken from the
    threshold was therefore larger than the whole conversation, so the walk kept
    everything, left a few hundred tokens of head, freed nothing, and fired
    again next turn."""
    conversation = 4_118
    assert compaction.keep_tail_budget(conversation) < conversation
    # ...and it grows with the conversation, not with anything else.
    assert compaction.keep_tail_budget(400_000) > compaction.keep_tail_budget(4_000)


def test_a_head_too_small_to_be_worth_summarising_is_left_alone():
    """Watched live at a 3,000-token threshold: six compactions in seven turns,
    one of them turning 19 tokens into a 93-token summary, with the context
    climbing the whole time. Refusing is the honest outcome."""
    assert not compaction.worth_compacting(19)
    assert not compaction.worth_compacting(500)
    assert compaction.worth_compacting(50_000)


async def test_nothing_is_announced_when_nothing_will_be_compacted(db):
    """Sitting just over the threshold with nothing worth summarising is the
    ordinary state right after a compaction. Announcing "Summarising..." there
    put that on screen every single turn, for nothing."""
    session = await db.create_session(name="s", project_dir="/tmp")
    await db.add_message(session["id"], "user", "hello")
    await db.add_message(session["id"], "assistant", "hi", token_count=5)

    assert not await compaction.would_compact(session["id"])
