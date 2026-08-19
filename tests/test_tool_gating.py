"""Which tools each session is offered.

Two rules worth pinning down:

* `browser` is always offered. Nearly all of it -- navigating, filling forms,
  asserting, reading the console, and `snapshot`, which returns the page's
  accessibility tree as text -- needs no ability to see an image. Gating it on
  vision left a website-building assistant unable to open the website it had
  just built.
* Tools that genuinely cannot work without vision are hidden rather than
  offered and broken, so the model never burns a round trip to be told no.
"""

import pytest

from agent_server.tools.registry import (
    MANAGER_TOOLS,
    TOOLS,
    Tool,
    allowed_tool_names,
    tool_schemas,
    vision_available,
)


def _names(session, **kwargs):
    return {s["function"]["name"] for s in tool_schemas(allowed_tool_names(session), **kwargs)}


PROJECT = {"kind": "project"}
MANAGER = {"kind": "manager"}


def test_no_vision_tool_ships_with_the_app():
    """Describing an image needs a GPU or a paid account this app cannot
    assume. If one is ever bundled, the gating tests below change meaning."""
    assert vision_available() is False


def test_browser_is_always_offered():
    assert "browser" in _names(PROJECT)
    assert "browser" in _names(PROJECT, include_vision=False)
    assert "browser" in _names(PROJECT, include_vision=True)


def test_browser_does_not_claim_to_need_vision():
    assert TOOLS["browser"].needs_vision is False


def test_vision_gated_tools_are_hidden_without_vision():
    assert "capture" not in _names(PROJECT, include_vision=False)


def test_vision_gated_tools_appear_when_vision_exists():
    assert "capture" in _names(PROJECT, include_vision=True)


def test_gating_defaults_to_whether_vision_is_registered():
    """The default must follow the registry, not the provider's multimodality.

    It was previously passed `not provider.supports_vision()` -- the wrong
    question, then inverted, so a multimodal provider was the one that lost
    `browser`.
    """
    assert _names(PROJECT) == _names(PROJECT, include_vision=vision_available())


def test_a_registered_vision_tool_unhides_the_gated_ones():
    async def _noop(ctx, **kwargs):  # pragma: no cover - never called
        raise AssertionError("stub")

    TOOLS["vision"] = Tool(
        name="vision", description="stub", parameters={"type": "object"}, handler=_noop
    )
    try:
        assert vision_available() is True
        assert "capture" in _names(PROJECT)
    finally:
        del TOOLS["vision"]
    assert vision_available() is False


def test_project_sessions_cannot_manage_projects():
    """Only the home session creates and deletes projects; a project session
    reaching those would let it delete its siblings."""
    assert not (_names(PROJECT) & MANAGER_TOOLS)


def test_manager_gets_project_tools_but_not_the_editing_ones():
    names = _names(MANAGER)
    assert {"create_project", "list_projects", "open_project"} <= names
    # It answers questions and looks inside projects; it does not build them.
    assert "edit" not in names
    assert "write" not in names
    assert "read" in names


@pytest.mark.parametrize("tool_name", sorted(TOOLS))
def test_every_tool_has_a_usable_schema(tool_name):
    schema = TOOLS[tool_name].schema()
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == tool_name
    assert fn["description"].strip(), f"{tool_name} has no description"
    assert fn["parameters"]["type"] == "object"
    for prop, spec in fn["parameters"].get("properties", {}).items():
        assert "type" in spec, f"{tool_name}.{prop} has no type"
