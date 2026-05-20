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

"""Verify AssistantSpeechNodeJa streaming toggle + input-port selection."""

from __future__ import annotations

from dimos.agents.skills.speak_skill_ja import (
    AssistantSpeechNodeJa,
    AssistantSpeechNodeJaConfig,
)


def test_streaming_default_true(monkeypatch):
    monkeypatch.delenv("DIMOS_TTS_STREAMING", raising=False)
    assert AssistantSpeechNodeJaConfig().streaming is True


def test_streaming_env_seed_false(monkeypatch):
    monkeypatch.setenv("DIMOS_TTS_STREAMING", "0")
    assert AssistantSpeechNodeJaConfig().streaming is False


def test_streaming_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv("DIMOS_TTS_STREAMING", "0")
    assert AssistantSpeechNodeJaConfig(streaming=True).streaming is True


def test_select_input_streaming_uses_agent_text():
    node = AssistantSpeechNodeJa(impl="open_jtalk", streaming=True)
    stream, cb = node._select_input()
    assert stream is node.agent_text
    assert cb == node._on_agent_text


def test_select_input_non_streaming_uses_agent():
    node = AssistantSpeechNodeJa(impl="open_jtalk", streaming=False)
    stream, cb = node._select_input()
    assert stream is node.agent
    assert cb == node._on_agent_message
