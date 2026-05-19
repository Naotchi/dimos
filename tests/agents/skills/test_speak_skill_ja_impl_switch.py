# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Verify AssistantSpeechNodeJa selects the right TTS node from config."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dimos.agents.skills.speak_skill_ja import (
    AssistantSpeechNodeJa,
    AssistantSpeechNodeJaConfig,
)
from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode
from dimos.stream.audio.tts.node_openai import OpenAITTSNode, Voice


def _build_node(impl: str, **extra) -> AssistantSpeechNodeJa:
    """Instantiate the node without starting it (no audio device required)."""
    return AssistantSpeechNodeJa(impl=impl, **extra)


def test_default_impl_is_sbv2():
    cfg = AssistantSpeechNodeJaConfig()
    assert cfg.impl == "sbv2"


def test_impl_open_jtalk_returns_open_jtalk_node():
    node = _build_node(impl="open_jtalk")
    tts = node._make_tts_node()
    assert isinstance(tts, OpenJTalkTTSNode)


def test_impl_openai_returns_openai_node(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    node = _build_node(impl="openai", openai_voice="echo", openai_model="tts-1")
    tts = node._make_tts_node()
    assert isinstance(tts, OpenAITTSNode)
    assert tts.voice == Voice.ECHO
    assert tts.model == "tts-1"


def test_impl_sbv2_routes_to_sbv2_module(monkeypatch):
    """Dispatch picks the sbv2 module without actually loading the model.

    sbv2 instantiation pulls a HuggingFace model on first use; replace the
    constructor with a sentinel so the test verifies routing only.
    """
    sentinel = object()
    import dimos.stream.audio.tts.node_style_bert_vits2 as sbv2_mod

    monkeypatch.setattr(sbv2_mod, "StyleBertVits2TTSNode", lambda: sentinel)
    node = _build_node(impl="sbv2")
    assert node._make_tts_node() is sentinel


def test_impl_voicevox_routes_to_voicevox_module(monkeypatch):
    """Dispatch picks the voicevox module without contacting the engine."""
    sentinel = object()
    import dimos.stream.audio.tts.node_voicevox as vv_mod

    monkeypatch.setattr(vv_mod, "VoicevoxTTSNode", lambda **kw: sentinel)
    node = _build_node(impl="voicevox")
    assert node._make_tts_node() is sentinel


def test_unknown_impl_raises():
    with pytest.raises(ValidationError):
        AssistantSpeechNodeJaConfig(impl="bogus")
