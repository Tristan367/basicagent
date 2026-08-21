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
    # And no second hard-coded copy of the wording. Comments stripped first:
    # they quote it to explain where it comes from, which is not a copy of it.
    import re

    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", app_js, flags=re.S)
    assert "'wrote a file'" not in code and '"wrote a file"' not in code

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
    assert "messages.appendChild(wrap)" in block
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
    """Everything added during a turn has to put it back, or it ends up buried
    somewhere up the conversation saying the assistant is still working."""
    js = _app_js()
    assert "function keepWorkingLast()" in js
    # Every append into the conversation has to be followed by it, inside the
    # same function. Measured against the next `\n  }` -- the close of a
    # top-level function in this file, which is indented two spaces.
    for where in ("function bubble(role)", "function activityStrip()",
                  "function appendAction("):
        start = js.index(where)
        body = js[start:js.index("\n  }", start)]
        assert "messages.appendChild(" in body, f"{where} no longer appends"
        assert "keepWorkingLast()" in body, f"nothing restores it in {where}"


def test_the_spinner_is_slow_enough_to_read():
    js = _app_js()
    line = next(ln for ln in js.splitlines() if "SPIN_MS" in ln and "=" in ln)
    assert int(line.split("=")[1].strip().rstrip(";")) >= 200


# ── what it actually did, when you ask ──────────────────────────────────────
#
# The chips say "read 4 files". Opening the group says which four, and shows the
# diff for anything that changed. Shut to begin with, always: somebody who does
# not want to know what a tool is never has to find out, and somebody who does
# is one press away.


def test_a_file_tool_is_named_by_its_file_not_its_whole_path():
    """The title is the entire path, elided from the left to fit -- fine as a
    tooltip, useless as a list of what happened."""
    from agent_server.activity import short_label

    assert short_label(
        "read", "…e/tristan/Projects/custom-code-agent-test-1/chaos/script.js (436 lines)"
    ) == "script.js (436 lines)"
    assert short_label("write", "/home/me/thing/hello.txt (1 lines)") == "hello.txt (1 lines)"


def test_everything_else_keeps_the_title_it_gave_itself():
    """`bash` gives the command, `grep` the pattern, `browser` the steps. Those
    are already the right answer and cutting them at a slash would ruin them."""
    from agent_server.activity import short_label

    for tool, title in (
        ("bash", 'git commit -m "Add the site"'),
        ("grep", "'prefers-reduced-motion|@media' (13 matches)"),
        ("browser", "goto, click, expect"),
        ("webfetch", "https://example.com (164 chars)"),
    ):
        assert short_label(tool, title) == title


def test_a_group_carries_what_each_call_was():
    from agent_server.activity import group

    out = group([
        {"role": "user", "content": "go"},
        {"role": "tool", "tool_name": "read", "tool_title": "/a/b/index.html (9 lines)"},
        {"role": "tool", "tool_name": "edit", "tool_title": "/a/b/style.css",
         "diff": "@@ -1 +1 @@\n-a\n+b\n", "duration_ms": 12},
        {"role": "assistant", "content": "done"},
    ])
    calls = out[1]["calls"]
    assert [c["label"] for c in calls] == ["index.html (9 lines)", "style.css"]
    assert calls[1]["diff"].startswith("@@")
    assert calls[1]["ms"] == 12
    assert calls[0]["failed"] is False


def test_a_call_that_failed_says_so():
    from agent_server.activity import group

    out = group([
        {"role": "user", "content": "go"},
        {"role": "tool", "tool_name": "bash", "tool_title": "npm test", "is_error": 1},
        {"role": "assistant", "content": "hmm"},
    ])
    assert out[1]["calls"][0]["failed"] is True


def test_a_huge_diff_is_clipped_rather_than_carried_whole():
    """Every diff in a conversation held in the page at once is a lot of page.
    This is a summary of the work; the record of it is the project's own git
    history."""
    from agent_server.activity import MAX_DIFF_CHARS, detail

    d = detail({"tool_name": "write", "tool_title": "/a/big.txt", "diff": "+x\n" * 40_000})
    assert len(d["diff"]) == MAX_DIFF_CHARS
    assert d["clipped"] is True


def test_the_calls_are_not_called_items():
    """Jinja resolves `m.items` on a dict to dict.items -- the method, not the
    key -- so naming it that rendered a bound method into the page and blew up
    on the way to JSON. Caught in the browser, not by a test, which is why
    there is one now."""
    from agent_server.activity import group

    out = group([
        {"role": "user", "content": "go"},
        {"role": "tool", "tool_name": "read", "tool_title": "/a/b.txt"},
        {"role": "assistant", "content": "done"},
    ])
    assert "items" not in out[1]
    assert "calls" in out[1]


def test_a_group_opens_shut_and_the_list_is_built_by_the_browser():
    """A long conversation carries a great many of these. Building every diff
    up front costs a page nobody has asked to see."""
    import re
    from pathlib import Path

    tpl = Path("web_ui/templates/chat_messages.html").read_text()
    block = tpl[tpl.index("m.kind == 'activity'"):tpl.index("m.role == 'tool'")]
    tag = re.search(r'<details class="did"([^>]*)>', block)
    assert tag, "the group is not a disclosure any more"
    assert "open" not in tag.group(1), "the group starts open"
    assert "data-items=" in block and "did-list" not in block, \
        "the list is rendered here rather than built on demand"


def test_thinking_is_kept_but_shut():
    """It was thrown away entirely, which is the wrong call for anybody trying
    to work out why the assistant did what it did."""
    from pathlib import Path

    tpl = Path("web_ui/templates/chat_messages.html").read_text()
    block = tpl[tpl.index("m.role == 'assistant'"):]
    assert "reasoning_content" in block
    assert '<details class="thinking">' in block
    assert "<details open" not in block


def test_a_group_says_how_many_of_its_calls_failed():
    """The chips count work attempted, so a turn where everything failed still
    shows what was tried rather than reading as though nothing happened. But
    "wrote a file", when the write was refused, is a lie on its own."""
    out = activity.group([
        {"role": "user", "content": "go"},
        tool("bash", tool_title="mkdir x", is_error=1),
        tool("bash", tool_title="ls", is_error=1),
        tool("bash", tool_title="pwd"),
        {"role": "assistant", "content": "hmm"},
    ])
    strip = strips(out)[0]
    assert strip["failures"] == 2
    assert strip["sentence"] == "I ran 3 commands. 2 of those failed."


def test_one_failure_reads_as_one():
    assert activity.sentence({"look": 2}, 1) == "I read 2 files. One of those failed."
    assert activity.sentence({"look": 2}, 0) == "I read 2 files."


def test_a_group_where_nothing_failed_says_nothing_about_it():
    out = activity.group([
        {"role": "user", "content": "go"}, tool("read"),
        {"role": "assistant", "content": "ok"},
    ])
    assert strips(out)[0]["failures"] == 0
    assert "failed" not in strips(out)[0]["sentence"]


# ── the marks down the side ─────────────────────────────────────────────────


def _css() -> str:
    import re
    from pathlib import Path

    return re.sub(r"/\*.*?\*/", "", Path("web_ui/static/css/style.css").read_text(), flags=re.S)


def _left_pad(css: str, rule: str) -> float:
    """The left padding a rule sets, from `padding:` shorthand or longhand."""
    import re

    block = css[css.index(rule):]
    block = block[:block.index("}")]
    long = re.search(r"padding-left:\s*([\d.]+)px", block)
    if long:
        return float(long.group(1))
    short = re.search(r"padding:\s*([^;]+);", block)
    assert short, f"{rule} sets no padding"
    parts = short.group(1).split()
    # 1 value: all sides. 2: block, inline. 3: top, inline, bottom. 4: t r b l.
    left = parts[{1: 0, 2: 1, 3: 1, 4: 3}[len(parts)]]
    return float(left.removesuffix("px"))


def test_thinking_lines_up_with_the_work_beside_it():
    """They are one column of marks running down the side of the conversation.
    Thinking sat further right than the pills, which read as a different kind of
    thing indented under them rather than the same kind of thing beside them."""
    import re

    css = _css()
    chip = css[css.index(".chip {"):]
    chip = chip[:chip.index("}")]
    border = float(re.search(r"border:\s*([\d.]+)px", chip).group(1))

    pill_mark = (_left_pad(css, ".did-summary {")
                 + _left_pad(css, ".activity {")
                 + border
                 + _left_pad(css, ".chip {"))
    assert _left_pad(css, ".thinking > summary {") == pill_mark, (
        f"the tick starts at {_left_pad(css, '.thinking > summary {')}px "
        f"and the tool glyphs at {pill_mark}px"
    )


def test_a_finished_thought_is_a_tick_not_a_stopped_spinner():
    """The star it used was the first frame of the live spinner, so a finished
    thought looked like an animation that had died -- which is what a thing that
    has crashed looks like."""
    css = _css()
    block = css[css.index(".thinking > summary::before"):]
    block = block[:block.index("}")]
    assert '"✓"' in block
    assert "✲" not in block


def test_the_tool_marks_are_bigger_than_their_labels():
    """They are the part you read at a glance. A small mark beside small words
    is two small things rather than a shape and its caption."""
    import re

    css = _css()
    block = css[css.index(".chip-glyph {"):]
    block = block[:block.index("}")]
    size = float(re.search(r"font-size:\s*([\d.]+)em", block).group(1))
    assert size >= 1.2, f"the glyph is {size}em"


def test_there_is_no_caret():
    """The pill is plainly a thing you press. A triangle flipping up and down
    beside it was one more thing to look at for nothing."""
    from pathlib import Path

    assert ".did-caret" not in _css()
    assert "did-caret" not in Path("web_ui/static/js/app.js").read_text()
    assert "did-caret" not in Path("web_ui/templates/chat_messages.html").read_text()
