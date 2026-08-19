"""Dictation: model selection and the guards around transcription.

There is one backend on every platform, deliberately. whisper.cpp was faster
but needed a compiled binary plus an ffmpeg system package, so the app behaved
differently depending on whose machine it ran on. faster-whisper is a plain pip
install everywhere, and the same model serves both press-to-talk and live
dictation so only one copy is ever in memory.

Nothing here loads a model or transcribes audio; these are the rules only.
"""

import pytest

from agent_server import config, stt


@pytest.fixture(autouse=True)
def restore_size():
    """Model size is process-global, so put it back after each test."""
    before = config.whisper_size()
    yield
    config.set_whisper_size(before)


def test_the_default_is_the_balanced_model():
    config.set_whisper_size("tiny.en")
    config._whisper_size = ""
    assert config.whisper_size() == config.DEFAULT_WHISPER_MODEL


def test_every_offered_model_is_described_for_a_non_technical_reader():
    for choice in config.WHISPER_MODEL_CHOICES:
        assert choice["id"] and choice["name"] and choice["note"] and choice["size"]
        # The picker is for someone who does not know what "base.en" means, so
        # the label has to be words rather than a model id.
        assert choice["name"] != choice["id"]


def test_choices_and_ids_agree():
    assert {m["id"] for m in config.WHISPER_MODEL_CHOICES} == config.WHISPER_MODEL_IDS
    assert config.DEFAULT_WHISPER_MODEL in config.WHISPER_MODEL_IDS


def test_selecting_a_model_reports_whether_it_changed():
    config.set_whisper_size("base.en")
    assert config.set_whisper_size("small.en") is True
    assert config.whisper_size() == "small.en"
    # Selecting the one already in use must not report a change; the caller
    # uses that to decide whether to throw away the loaded model.
    assert config.set_whisper_size("small.en") is False


def test_an_unknown_model_is_refused_rather_than_stored():
    config.set_whisper_size("base.en")
    assert config.set_whisper_size("enormous.en") is False
    assert config.whisper_size() == "base.en"
    assert config.set_whisper_size("") is False
    assert config.whisper_size() == "base.en"


def test_a_corrupt_stored_value_falls_back_to_the_default():
    """The size comes from the database, which a person can edit by hand."""
    config._whisper_size = "not-a-model"
    assert config.whisper_size() == config.DEFAULT_WHISPER_MODEL


def test_live_dictation_needs_nothing_beyond_dictation_itself():
    """Live and press-to-talk share one model, so if one works both do. They
    used to differ: live needed a separate whisper-server binary, so it was
    silently missing on most machines."""
    assert config.whisper_streaming_available() == config.stt_available()


async def test_empty_audio_is_rejected_before_the_model_is_touched():
    with pytest.raises(stt.STTError):
        await stt.transcribe(b"")


async def test_oversized_audio_is_rejected():
    with pytest.raises(stt.STTError):
        await stt.transcribe(b"x" * (stt.MAX_AUDIO_BYTES + 1))


async def test_a_missing_install_gives_a_one_command_fix(monkeypatch):
    monkeypatch.setattr(config, "stt_available", lambda: False)
    monkeypatch.setattr(stt, "stt_available", lambda: False)
    await stt.reload_model()
    with pytest.raises(stt.STTError) as excinfo:
        await stt.transcribe(b"some audio")
    # Whoever reads this is being read it by the assistant, so it has to carry
    # the fix rather than a diagnosis.
    assert "pip install faster-whisper" in str(excinfo.value)


def test_availability_names_the_model_in_words():
    config.set_whisper_size("small.en")
    report = stt.availability()
    assert report["model"] == "small.en"
    assert report["model_name"] == "Most accurate"


def test_transcript_cleaning_drops_non_speech_markers():
    assert stt._clean("[BLANK_AUDIO]") == ""
    assert stt._clean("(silence)") == ""
    assert stt._clean("Thanks for watching!") == ""
    assert stt._clean("  Build me a  website.  ") == "Build me a website."
    assert stt._clean("[music] Build me a website.") == "Build me a website."
