"""Verify AssistantSpeechNodeJa selects the right TTS node from config."""

from __future__ import annotations

import pytest

from dimos.agents.skills.speak_skill_ja import (
    AssistantSpeechNodeJa,
    AssistantSpeechNodeJaConfig,
)
from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode
from dimos.stream.audio.tts.node_openai import OpenAITTSNode


def _build_node(impl: str, **extra) -> AssistantSpeechNodeJa:
    """Instantiate the node without starting it (no audio device required)."""
    # Module __init__ takes flat config-field kwargs, not a pre-built config
    # object; build the config here to validate fields, then spread.
    cfg = AssistantSpeechNodeJaConfig(impl=impl, **extra)
    node = AssistantSpeechNodeJa(**cfg.model_dump(exclude={"g"}))
    return node


def test_default_impl_is_open_jtalk():
    node = _build_node(impl="open_jtalk")
    tts = node._make_tts_node()
    assert isinstance(tts, OpenJTalkTTSNode)


def test_impl_openai_returns_openai_node(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    node = _build_node(impl="openai", openai_voice="echo", openai_model="tts-1")
    tts = node._make_tts_node()
    assert isinstance(tts, OpenAITTSNode)


def test_unknown_impl_raises():
    node = _build_node(impl="bogus")
    with pytest.raises(ValueError, match="bogus"):
        node._make_tts_node()
