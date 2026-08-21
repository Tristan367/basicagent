"""What the assistant did, folded into something a person can read.

The failure this defends against is the one the app had: the assistant works for
a minute, files get written, a website gets opened and checked, and the
conversation shows ten replies in a row with no hint that anything happened at
all.
"""

import json

from agent_server import activity


def tool(name, **extra):
    return {"role": "tool", "tool_name": name, **extra}


def working():
    """An assistant turn that only asks for tools: no words, never drawn."""
    return {"role": "assistant", "content": "", "tool_calls": "[]"}


def strips(items):
    return [i for i in items if i.get("kind") == "activity"]


def test_a_run_of_tools_becomes_one_line():
    out = activity.group([
        {"role": "user", "content": "go"},
        working(), tool("read"), tool("read"), tool("read"),
        {"role": "assistant", "content": "Done."},
    ])
    assert len(strips(out)) == 1
    assert strips(out)[0]["sentence"] == "I read 3 files."


def test_a_turn_that_reaches_for_tools_repeatedly_is_still_one_line():
    """An assistant turn with only tool calls in it has no words and is never
    rendered, so it must not break the run either -- four rounds of tools used
    to produce four strips stacked on top of each other."""
    out = activity.group([
        {"role": "user", "content": "go"},
        working(), tool("read"), tool("glob"),
        working(), tool("write"), tool("edit"),
        working(), tool("bash"),
        {"role": "assistant", "content": "Done."},
    ])
    assert len(strips(out)) == 1
    said = strips(out)[0]["sentence"]
    assert "wrote 2 files" in said and "ran a command" in said and "read 2 files" in said


def test_two_stretches_of_work_stay_apart():
    """A message somebody actually said is the boundary. Merging across one
    would claim the assistant did in one go what it did in two."""
    out = activity.group([
        {"role": "user", "content": "first"},
        working(), tool("read"),
        {"role": "assistant", "content": "Here you go."},
        {"role": "user", "content": "second"},
        working(), tool("write"),
        {"role": "assistant", "content": "Done."},
    ])
    assert [s["sentence"] for s in strips(out)] == ["I read a file.", "I wrote a file."]


def test_nothing_is_added_when_no_tools_ran():
    out = activity.group([
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hello!"},
    ])
    assert strips(out) == []
    assert len(out) == 2


def test_a_button_the_user_needs_survives_the_fold():
    """`open_project` puts a real button in the conversation. Folding it away
    would leave the user with nothing to press."""
    out = activity.group([
        {"role": "user", "content": "new project"},
        working(), tool("create_project", open_session="abc123"),
        {"role": "assistant", "content": "Made it."},
    ])
    assert any(m.get("open_session") == "abc123" for m in out)


def test_a_compaction_summary_is_not_swallowed():
    out = activity.group([
        {"kind": "summary", "summary_text": "earlier things"},
        working(), tool("read"),
        {"role": "assistant", "content": "ok"},
    ])
    assert any(m.get("kind") == "summary" for m in out)


def test_an_unknown_tool_still_counts_as_something():
    """A tool added tomorrow must not vanish from the account of the work."""
    out = activity.group([working(), tool("some_new_tool"),
                          {"role": "assistant", "content": "ok"}])
    assert strips(out)[0]["sentence"]


def test_the_sentence_reads_as_english():
    assert activity.sentence({"look": 1}) == "I read a file."
    assert activity.sentence({"look": 2, "write": 1}) == "I wrote a file and read 2 files."
    assert activity.sentence({}) == ""


def test_chips_are_ordered_the_same_way_every_time():
    """So the eye learns where to look. Insertion order would move them about
    depending on which tool happened to run first."""
    a = [c["family"] for c in activity.chips({"see": 1, "write": 1, "look": 1})]
    b = [c["family"] for c in activity.chips({"look": 1, "see": 1, "write": 1})]
    assert a == b


def test_colour_is_never_the_only_signal():
    """Every chip carries words as well as a hue, so somebody who cannot
    separate these colours loses nothing but the decoration."""
    for c in activity.chips({f: 2 for f in activity.FAMILIES}):
        assert c["text"].strip(), c


def test_every_tool_the_app_registers_has_a_family():
    """A tool with no family falls back to "ran a command", which is a lie for
    anything that did not run one."""
    from agent_server.tools.registry import TOOLS

    missing = sorted(set(TOOLS) - set(activity.TOOL_FAMILY))
    assert not missing, f"no family for: {missing}"


def test_the_browser_gets_the_same_table_the_server_uses():
    """The browser counts events as they arrive and the server folds them back
    from the database. Two copies of this table would drift the first time a
    tool was added, and the live view would stop matching the reloaded one."""
    from pathlib import Path

    page = Path("web_ui/templates/chat.html").read_text()
    assert 'id="activity-families"' in page
    assert "activity_families" in page

    app_js = Path("web_ui/static/js/app.js").read_text()
    assert "getElementById('activity-families')" in app_js
    # And no second hard-coded copy of the glyphs.
    assert "'wrote a file'" not in app_js and '"wrote a file"' not in app_js

    # It has to survive being embedded in a page.
    assert "</script>" not in json.dumps(activity.FAMILIES)


# ── the same order live as when it is loaded back ───────────────────────────
#
# The two used to disagree completely. Reloading a conversation interleaved it
# correctly -- said this, did that, said the next thing -- but while the turn
# was actually running there was one bubble for everything the assistant said
# and one activity strip pinned above it, so all the chips piled up at the top
# and all the words ran together into a wall underneath. You could not tell
# which sentence caused which work.


def _app_js() -> str:
    from pathlib import Path

    return Path("web_ui/static/js/app.js").read_text()


def test_the_live_strip_is_appended_rather_than_put_above_the_reply():
    js = _app_js()
    block = js[js.index("function activityStrip()"):]
    block = block[:block.index("function noteToolDone")]
    assert "messages.appendChild(el)" in block
    assert "insertBefore" not in block, "the strip goes above the words again"


def test_words_after_a_tool_start_a_new_message():
    """Which is how they are stored, and so how they read back. One bubble for
    the whole turn is what made it a wall of text."""
    js = _app_js()
    handler = js[js.index("function handleEvent(ev)"):]
    handler = handler[:handler.index("let assistantEl")]
    assert "closeSegment();" in handler, "nothing ends the current message"
    content = handler[handler.index("case 'content':"):handler.index("case 'tool_start':")]
    assert "if (!assistantEl)" in content and "bubble('assistant')" in content


def test_what_the_assistant_is_doing_is_not_in_the_composer():
    """It was a line of small grey text next to Send -- so the answer to "is
    this broken?" was somewhere you would only look if you already suspected it
    was not."""
    js = _app_js()
    handler = js[js.index("function handleEvent(ev)"):]
    handler = handler[:handler.index("let assistantEl")]
    assert "setStatus(" not in handler, "turn status is back in the composer"
    assert handler.count("setWorking(") >= 5


def test_the_working_row_stays_the_last_thing_in_the_conversation():
    js = _app_js()
    assert "function keepWorkingLast()" in js
    # Everything added during a turn has to put it back, or it ends up buried.
    for after in ("messages.appendChild(el);", "appendAction(ev.open_session"):
        idx = js.index(after)
        assert "keepWorkingLast()" in js[idx:idx + 320], f"nothing restores it after {after}"


def test_the_spinner_is_slow_enough_to_read():
    js = _app_js()
    line = next(ln for ln in js.splitlines() if "SPIN_MS" in ln and "=" in ln)
    assert int(line.split("=")[1].strip().rstrip(";")) >= 200
