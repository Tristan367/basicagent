"""What the user reads when the model cannot be reached.

This string is not a log line. It goes straight onto the screen, unedited, and
is read aloud to anybody using the app with a screen reader. For most of this
app's life it said things like:

    deepseek API error 401: Invalid API key provided

which names nothing the reader can do and assumes they know what an API is.
Every provider goes through this one function -- Gemini, DeepSeek, OpenRouter
and any custom endpoint -- so these are the words the whole app fails in.
"""

import httpx
import openai
import pytest

from agent_server.providers.openai_compat import _describe

JARGON = ("api error", "status code", "http", "401", "429", "500", "traceback")


def error(status: int, message: str = "") -> openai.APIStatusError:
    request = httpx.Request("POST", "https://example.com/chat/completions")
    body = {"error": {"message": message}} if message else {}
    response = httpx.Response(status, request=request, json=body)
    return openai.APIStatusError(message or "err", response=response, body=body)


def test_a_rejected_key_says_it_is_the_key(db=None):
    said = _describe(error(401, "Invalid API key provided"), "Gemini")
    assert "key" in said.lower()
    assert "Project Manager" in said, "it does not say who can fix it"
    assert "Invalid API key provided" not in said, "it repeated the raw message"


def test_being_rate_limited_separates_the_two_reasons():
    """Too fast and out of credit need opposite responses -- wait, or change
    something -- and one message for both leaves the reader stuck."""
    said = _describe(error(429), "DeepSeek").lower()
    assert "wait" in said or "minute" in said
    assert "credit" in said or "allowance" in said


def test_a_missing_model_says_the_model_is_gone():
    said = _describe(error(404), "OpenRouter").lower()
    assert "model" in said
    assert "renamed" in said or "retired" in said


def test_trouble_at_the_far_end_says_it_is_not_the_users_fault():
    """The reflex is to assume you broke it, and then to stop using the app.
    Worth a sentence."""
    said = _describe(error(503), "Gemini").lower()
    assert "their end" in said or "nothing is wrong with this app" in said
    assert "again" in said


def test_a_refused_request_keeps_the_reason():
    """The only case where the provider's own words earn their place: a 400
    usually says which parameter or which piece of content it objected to."""
    said = _describe(error(400, "context length exceeded"), "DeepSeek")
    assert "context length exceeded" in said


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 502, 503])
def test_no_status_codes_or_jargon_are_read_out(status):
    said = _describe(error(status), "Gemini").lower()
    for word in JARGON:
        assert word not in said, f"{status} still says {word!r}"


def test_it_still_says_something_for_a_status_it_has_never_seen():
    said = _describe(error(418, "I am a teapot"), "Gemini")
    assert said.strip()
    assert "Gemini" in said
