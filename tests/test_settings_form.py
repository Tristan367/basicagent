"""Checkbox handling on the settings form.

A browser omits an unticked checkbox from the submission entirely, so "off" and
"not on this form" arrive looking identical. Treating both as "leave it alone"
meant every checkbox on the settings page could be switched on and never off
again -- including read-aloud, which is the one a user is most likely to want
to undo in a hurry.
"""

from starlette.datastructures import FormData

from agent_server.routes.settings import _checkbox


def _form(*pairs):
    return FormData(list(pairs))


def test_hidden_twin_alone_means_unticked():
    assert _checkbox(_form(("tts_auto", "off")), "tts_auto") is False


def test_hidden_twin_plus_checkbox_means_ticked():
    form = _form(("tts_auto", "off"), ("tts_auto", "on"))
    assert _checkbox(form, "tts_auto") is True


def test_order_of_the_pair_does_not_matter():
    """The hidden field's position in the DOM must not decide the answer."""
    assert _checkbox(_form(("x", "on"), ("x", "off")), "x") is True
    assert _checkbox(_form(("x", "off"), ("x", "on")), "x") is True


def test_absent_field_is_none_not_false():
    """A form that never carried this setting must leave it untouched, rather
    than silently switching it off."""
    assert _checkbox(_form(("something_else", "on")), "tts_auto") is None
    assert _checkbox(_form(), "tts_auto") is None


def test_a_bare_checkbox_still_reads_as_ticked():
    """Belt and braces: a form without the hidden twin should not read a
    ticked box as unticked."""
    assert _checkbox(_form(("tts_auto", "on")), "tts_auto") is True
