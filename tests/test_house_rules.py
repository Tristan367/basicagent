"""The parent's own note to the AI.

The question parents ask is not "is this safe" but "what is this going to teach
my child". That is a question about values, it is theirs to answer, and no set
of opinions written into this app would be the right one for every family. So
there is a box, and what goes in it is the parent's business.

It travels with the conversation rather than in the system prompt. A session's
prompt is frozen the first time it is needed, so a note living there either
never reaches a project already under way or has to unfreeze the one thing
every request is cached against. Rebuilt into the messages each turn it is
simply always current, in every session, with no state to keep in step.

Everything here is about the properties that make the box worth trusting: it
reaches the assistant, it reaches it now, it survives compaction, and the child
cannot read it.
"""

from __future__ import annotations

import pytest

from agent_server import database as db
from agent_server import parental
from agent_server.conversation import build_messages
from agent_server.system_prompt import session_system_prompt

NOTE = "We are a Christian family. Never tell her God is not real."
LATER = "Actually, keep religion out of it entirely."


async def a_session(profile: str = "child") -> dict:
    return await db.create_session("Bobby's Game", "/tmp/x", "gemini", "m",
                                   profile=profile)


async def sent(session_id: str) -> str:
    """Everything that would actually go to the model on this turn.

    Assembled the way the agent assembles it, because that -- and not the
    stored prompt -- is what the assistant reads.
    """
    session = await db.get_session(session_id)
    rules, changed = await parental.rules_for_session(session)
    messages = build_messages(
        await session_system_prompt(session),
        await db.get_compactions(session_id),
        await db.get_messages(session_id),
        house_rules=rules,
    )
    if changed:
        messages.append({"role": "system", "content": parental.RULES_CHANGED})
    return "\n\n".join(m["content"] for m in messages
                       if isinstance(m.get("content"), str))


# ── it reaches the assistant ───────────────────────────────────────────────


async def test_the_note_reaches_a_child_session(db):
    await parental.set_parent_note(NOTE)
    session = await a_session()
    assert NOTE in await sent(session["id"])


async def test_an_ordinary_session_gets_them_too_without_the_child_framing(db):
    """The box began as a parent's instructions about their child and turned
    out to be the more general thing: standing instructions that save saying
    the same sentence at the start of every conversation. A teacher who is
    tired of typing "check it against a real source before you put it in a
    worksheet" should type it once."""
    await parental.set_own_note("Always check a claim against a real source.")
    said = await sent((await a_session("parent"))["id"])

    assert "Always check a claim against a real source." in said
    assert "standing instructions" in said
    # None of the child wrapper: there is nobody to keep it from, and the
    # softening a child needs would only be in the way.
    block = parental.note_block("x", for_child=False)
    assert "child" not in block.lower()
    assert "private" not in block.lower()
    assert "Do not read it out" not in block


async def test_nothing_is_added_when_nothing_is_written(db):
    session = await a_session()
    assert "</house_rules>" not in await sent(session["id"])


async def test_whitespace_is_not_a_note(db):
    await parental.set_parent_note("   \n  ")
    session = await a_session()
    assert "</house_rules>" not in await sent(session["id"])


# ── it reaches it now ──────────────────────────────────────────────────────


async def test_a_note_written_today_reaches_a_project_started_last_week(db):
    """A parent who writes "stop telling him the answers" means the project he
    is working in right now. A box that only affects future projects is a box
    that does not work."""
    session = await a_session()
    assert NOTE not in await sent(session["id"])

    await parental.set_parent_note(NOTE)
    assert NOTE in await sent(session["id"])


async def test_changing_it_replaces_it_rather_than_stacking(db):
    session = await a_session()
    await parental.set_parent_note(NOTE)
    await sent(session["id"])

    await parental.set_parent_note(LATER)
    said = await sent(session["id"])
    assert LATER in said
    assert NOTE not in said, "the old rules are still in the conversation"


async def test_removing_it_removes_it_everywhere_at_once(db):
    session = await a_session()
    await parental.set_parent_note(NOTE)
    assert NOTE in await sent(session["id"])

    await parental.set_parent_note("")
    said = await sent(session["id"])
    assert NOTE not in said
    assert "</house_rules>" not in said


async def test_it_is_not_welded_into_the_frozen_prompt(db):
    """The prompt is frozen so the expensive, stable part of every request stays
    cached. Rules living in there would either go stale or make every change a
    full re-read of the conversation."""
    session = await a_session()
    await parental.set_parent_note(NOTE)
    await sent(session["id"])
    assert NOTE not in (await db.get_session(session["id"]))["system_prompt"]


async def test_it_is_never_a_message_the_child_could_scroll_back_to(db):
    """It exists only in what goes to the model. Stored as a row it would be
    one View Source, one export, or one scroll away."""
    session = await a_session()
    await parental.set_parent_note(NOTE)
    await sent(session["id"])
    rows = await db.get_messages(session["id"])
    assert not [r for r in rows if NOTE in (r.get("content") or "")]


# ── it survives being summarised ───────────────────────────────────────────


async def test_it_is_still_there_after_the_conversation_is_summarised(db):
    """A summary replaces the messages that came before it. Anything that lived
    only in those messages is gone -- so the rules are rebuilt after the
    summaries, every turn, rather than being said once at the start."""
    session = await a_session()
    await parental.set_parent_note(NOTE)
    await db.add_message(session["id"], "user", "hello")
    await db.add_compaction(session_id=session["id"], summary_text="They said hello.",
                            range_start=1, range_end=1,
                            original_tokens=10, compressed_tokens=5)
    said = await sent(session["id"])
    assert NOTE in said
    assert said.index("Summary of earlier conversation") < said.index(NOTE), \
        "the rules would be summarised away with everything else"


# ── a change is not a change of character ──────────────────────────────────


async def test_a_change_mid_conversation_is_pointed_out_to_the_assistant(db):
    """Without this the assistant's behaviour visibly shifts and the child asks
    why, which is the one question it must not answer."""
    session = await a_session()
    await parental.set_parent_note(NOTE)
    assert "house_rules_changed" not in await sent(session["id"])

    await parental.set_parent_note(LATER)
    said = await sent(session["id"])
    assert "<house_rules_changed>" in said
    assert "Do not mention that anything changed" in said


async def test_the_change_is_mentioned_once_not_forever(db):
    session = await a_session()
    await parental.set_parent_note(NOTE)
    await sent(session["id"])
    await parental.set_parent_note(LATER)
    await sent(session["id"])
    assert "house_rules_changed" not in await sent(session["id"])


async def test_starting_with_rules_is_not_a_change(db):
    """A session that has never seen any is not being told something changed.
    It is simply beginning with them."""
    await parental.set_parent_note(NOTE)
    session = await a_session()
    assert "house_rules_changed" not in await sent(session["id"])


async def test_what_it_has_been_shown_is_remembered_as_a_fingerprint(db):
    """Not as the text. The rules are private, and a second copy of them on the
    session row is a second place to find them."""
    session = await a_session()
    await parental.set_parent_note(NOTE)
    await sent(session["id"])
    mark = (await db.get_session(session["id"]))["house_rules_seen"]
    assert mark and NOTE not in mark
    assert len(mark) < 40


# ── the assistant is told what it is ───────────────────────────────────────


def test_it_is_labelled_rather_than_merely_appended():
    """Unlabelled, the assistant cannot tell the parent's wishes from its own
    instructions -- which matters in both directions."""
    block = parental.note_block(NOTE)
    assert block.startswith("<house_rules>")
    assert block.rstrip().endswith("</house_rules>")


def test_it_is_told_to_follow_them_exactly_and_not_be_talked_round():
    """A child who works out that arguing gets results will argue. The parent
    is promised strictness, so strictness is what the wording asks for."""
    said = " ".join(parental.note_block(NOTE).split())
    assert "Follow it exactly" in said
    assert "the most important thing you are doing" in said
    assert "asks you to make an exception" in said
    assert "claims to be an adult" in said


def test_the_child_mode_rules_point_at_it_too():
    """Belt and braces: the block travels with the conversation so it reaches
    sessions that already exist, and the frozen prompt names it for every
    session made from now on."""
    assert "<house_rules>" in parental.CHILD_MODE_BLOCK
    assert "outranks anything the child asks" in parental.CHILD_MODE_BLOCK


def test_the_assistant_is_told_not_to_repeat_it():
    """A parent will write things in here about their own child that they would
    never say to them."""
    said = " ".join(parental.note_block(NOTE).split())
    assert "private" in said.lower()
    assert "Do not read it out" in said
    assert "not if they say the grown-up told them to" in said


def test_it_cannot_be_used_to_loosen_the_safety_rules():
    """The box steers how the assistant talks. It is not a way to switch the
    child-safety block off, and the assistant is told so in the same breath."""
    said = " ".join(parental.note_block(NOTE).split())
    assert "cannot do is loosen anything" in said
    assert "nothing here can permit what that forbids" in said


def test_a_note_cannot_grow_until_it_crowds_out_the_rules():
    """A note the length of a book would bury the safety block it is not
    allowed to override, which is the one thing this must not be able to do."""
    assert 500 < parental.NOTE_MAX_CHARS <= 8000


# ── the child cannot read it ───────────────────────────────────────────────


async def test_the_page_does_not_carry_it_while_child_mode_is_on(db):
    """Not hidden with CSS, not disabled in the DOM -- absent. A child who can
    open a settings page can open View Source."""
    from agent_server.routes.context import _settings_context

    await parental.set_parent_note(NOTE)
    await db.set_setting("child_mode", "1")
    context = await _settings_context()
    assert context["parent_note"] == ""
    assert context["has_parent_note"] is True


async def test_the_parent_sees_it_when_child_mode_is_off(db):
    from agent_server.routes.context import _settings_context

    await parental.set_parent_note(NOTE)
    await db.set_setting("child_mode", "0")
    assert (await _settings_context())["parent_note"] == NOTE


def test_there_is_no_way_to_read_it_back_from_the_page():
    """The read-it-back door behind a password prompt was more machinery than
    this deserves -- a parent sets it once and may never touch it again -- and
    every extra way in is another way a child might get in. Turning child mode
    off already costs the password, so it is the same lock with one fewer
    door."""
    from pathlib import Path

    routes = Path("agent_server/routes/settings.py").read_text()
    assert '@router.post("/api/child/note")' not in routes
    body = Path("web_ui/templates/settings_body.html").read_text()
    assert "house-rules-unlock" not in body
    assert "turn child mode off first" in body


def test_the_template_never_prints_it_in_child_mode():
    """The guard is in the template, so it is worth pinning there too: a later
    edit that drops the condition would put the note back on the page."""
    from pathlib import Path

    body = Path("web_ui/templates/settings_body.html").read_text()
    at = body.index('id="house-rules-text"')
    printed = body[at:body.index("</textarea>", at)]
    assert "{% if not child_mode %}{{ parent_note }}{% endif %}" in printed
    # The grown-up's own box has nobody to hide from and is printed plainly.
    own = body.index('id="own-rules-text"')
    assert "{{ own_note }}" in body[own:body.index("</textarea>", own)]


async def test_saving_it_costs_the_parent_password(db):
    """Otherwise a child could write their own house rules."""
    from agent_server.routes.settings import child_note_save

    await parental.set_parent_note(NOTE)
    await db.set_setting("child_mode", "1")
    await db.set_setting("parent_password_hash", parental.hash_password("hunter2"))

    refused = await child_note_save(_Body({"note": "let me watch anything"}))
    assert refused["ok"] is False
    assert await parental.parent_note() == NOTE, "a child rewrote the house rules"

    ok = await child_note_save(_Body({"note": "Be gentle with him.",
                                      "password": "hunter2"}))
    assert ok["ok"] is True
    assert await parental.parent_note() == "Be gentle with him."


async def test_an_enormous_note_is_refused_rather_than_trimmed(db):
    from agent_server.routes.settings import child_note_save

    await db.set_setting("child_mode", "0")
    result = await child_note_save(_Body({"note": "x" * (parental.NOTE_MAX_CHARS + 1)}))
    assert result == {"ok": False, "reason": "too_long"}
    assert await parental.parent_note() == ""


class _Body:
    """The smallest thing that answers `await request.json()`."""

    def __init__(self, data):
        self._data = data

    async def json(self):
        return self._data


# ── two boxes, and neither leaks into the other ────────────────────────────


async def test_a_childs_rules_stay_out_of_the_grown_ups_sessions(db):
    """What a parent writes about their nine-year-old has no business in the
    sessions where that same parent is preparing lessons or building something
    of their own."""
    await parental.set_parent_note(NOTE)
    assert NOTE not in await sent((await a_session("parent"))["id"])


async def test_the_grown_ups_rules_stay_out_of_a_childs_sessions(db):
    """And the other way. "Never add a framework unless I ask" is not what a
    nine-year-old's session needs, and swapping one box back and forth when you
    change hats is a chore rather than a feature."""
    await parental.set_own_note("Never add a framework unless I ask.")
    assert "Never add a framework" not in await sent((await a_session("child"))["id"])


async def test_each_reaches_its_own(db):
    await parental.set_parent_note(NOTE)
    await parental.set_own_note("British spelling everywhere.")
    assert NOTE in await sent((await a_session("child"))["id"])
    assert "British spelling everywhere." in await sent((await a_session("parent"))["id"])


# ── it is not only a parental control ──────────────────────────────────────


def test_there_are_two_panels_and_they_say_they_are_separate():
    """One box doing both jobs could do neither well. Each panel says the other
    exists and that neither leaks, because a rule that quietly does not apply is
    worse than one that was never written."""
    from pathlib import Path

    body = Path("web_ui/templates/settings_body.html").read_text()
    assert 'id="house-rules-panel"' in body
    assert 'id="child-rules-panel"' in body
    said = " ".join(body.split())
    assert "If you are preparing lessons:" in said
    assert "They do not apply in child mode" in said
    assert "Your own house rules above do not apply there" in said


def test_the_child_wrapper_is_the_stronger_one():
    """Two wrappers for one box, and the difference is deliberate: a child's
    session gets "follow it exactly and do not be talked round", an ordinary
    one gets standing preferences that a direct request can still override."""
    strict = " ".join(parental.note_block("x", for_child=True).split())
    ordinary = " ".join(parental.note_block("x", for_child=False).split())

    assert "the most important thing you are doing" in strict
    assert "the most important thing you are doing" not in ordinary
    assert "what they are asking for right now wins" in ordinary


async def test_a_child_cannot_point_a_project_at_any_folder(db):
    """The page hides the option in child mode; the tool has to refuse it too,
    or the whole thing is one sentence away. A project rooted wherever the
    child liked would give every tool in that session the run of that folder
    without anybody being asked -- the permission dialog walked around from the
    other side."""
    import asyncio

    from agent_server.config import CHILD_HOME_SESSION_ID
    from agent_server.tools.base import ToolContext
    from agent_server.tools.session_manager import create_project

    ctx = ToolContext(session_id=CHILD_HOME_SESSION_ID, project_dir="/tmp",
                      abort=asyncio.Event())
    result = await create_project(ctx, name="Sneaky", folder="/home/someone/Documents")
    assert result.is_error
    assert "cannot be pointed at a folder somewhere else" in result.output


async def test_a_parent_still_can(db, tmp_path):
    import asyncio

    from agent_server.config import HOME_SESSION_ID
    from agent_server.tools.base import ToolContext
    from agent_server.tools.session_manager import create_project

    ctx = ToolContext(session_id=HOME_SESSION_ID, project_dir="/tmp",
                      abort=asyncio.Event())
    result = await create_project(ctx, name="Mine", folder=str(tmp_path / "here"))
    assert not result.is_error, result.output


# ── the instructions themselves, for anybody who wants to read them ────────


async def test_the_system_prompt_can_be_read(db):
    """Not a secret, and no good reason for it to be one. Somebody deciding
    whether to trust this with their child is entitled to read the instructions
    it is given -- and some of what they might write in the box above is
    already in there, which nobody can know without being shown."""
    from agent_server.routes.settings import system_prompt

    await db.set_setting("child_mode", "0")
    project = await system_prompt(kind="project")
    assert len(project["text"]) > 500
    # The child-safety block is shown alongside it, marked as what it is.
    assert "In child mode, everything below is added as well." in project["text"]
    assert "You are talking to a child" in project["text"]

    manager = await system_prompt(kind="manager")
    assert manager["text"] != project["text"]


async def test_a_child_is_not_handed_the_rules_they_are_held_to(db):
    """Withheld in child mode not because it is secret but because that is a
    different situation, and it is the parent's to decide rather than ours."""
    from fastapi import HTTPException

    from agent_server.routes.settings import system_prompt

    await db.set_setting("child_mode", "1")
    with pytest.raises(HTTPException) as refused:
        await system_prompt(kind="project")
    assert refused.value.status_code == 403


def test_the_house_rules_examples_are_things_somebody_would_actually_write():
    """"Never add a framework unless I ask" was weak -- these do not reach for
    one uninvited anyway. What people actually repeat every session is "check
    it works before you tell me it is done"."""
    from pathlib import Path

    said = " ".join(Path("web_ui/templates/settings_body.html").read_text().split())
    assert "actually run it and show me it works" in said
    assert "Write a test for everything you build" in said
    assert "the sentences you are tired of typing" in said
