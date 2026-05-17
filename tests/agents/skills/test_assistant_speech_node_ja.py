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

"""Unit tests for AssistantSpeechNodeJa."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


@pytest.fixture
def node(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Construct AssistantSpeechNodeJa with TTS / audio output mocked."""
    fake_tts = MagicMock(name="OpenJTalkTTSNode_instance")
    fake_tts.emit_audio.return_value = MagicMock(name="audio_observable")
    fake_audio = MagicMock(name="SounddeviceAudioOutput_instance")

    monkeypatch.setattr(
        "dimos.agents.skills.speak_skill_ja.OpenJTalkTTSNode",
        lambda *a, **kw: fake_tts,
    )
    monkeypatch.setattr(
        "dimos.agents.skills.speak_skill_ja.SounddeviceAudioOutput",
        lambda *a, **kw: fake_audio,
    )

    from dimos.agents.skills.speak_skill_ja import AssistantSpeechNodeJa

    n = AssistantSpeechNodeJa()
    # subscribe is exercised by integration; here we stub it out to keep the
    # unit test focused on _on_agent_message / _on_audio_chunk logic.
    n.agent.subscribe = MagicMock(return_value=lambda: None)
    n.start()
    n._test_fake_tts = fake_tts  # type: ignore[attr-defined]
    n._test_fake_audio = fake_audio  # type: ignore[attr-defined]
    return n


def test_ai_message_with_text_is_enqueued(node: Any) -> None:
    sent: list[str] = []
    node._text_subject.subscribe(on_next=sent.append)

    node._on_agent_message(AIMessage(content="こんにちは"))

    assert sent == ["こんにちは"]


def test_ai_message_empty_content_with_tool_calls_is_dropped(node: Any) -> None:
    sent: list[str] = []
    node._text_subject.subscribe(on_next=sent.append)

    node._on_agent_message(
        AIMessage(
            content="",
            tool_calls=[{"name": "navigate", "args": {}, "id": "x"}],
        )
    )

    assert sent == []


def test_ai_message_whitespace_only_content_is_dropped(node: Any) -> None:
    sent: list[str] = []
    node._text_subject.subscribe(on_next=sent.append)

    node._on_agent_message(AIMessage(content="   \n  "))

    assert sent == []


def test_human_message_is_dropped(node: Any) -> None:
    sent: list[str] = []
    node._text_subject.subscribe(on_next=sent.append)

    node._on_agent_message(HumanMessage(content="ユーザの発話"))

    assert sent == []


def test_tool_message_is_dropped(node: Any) -> None:
    sent: list[str] = []
    node._text_subject.subscribe(on_next=sent.append)

    node._on_agent_message(ToolMessage(content="tool result", tool_call_id="x"))

    assert sent == []


def test_ai_message_list_content_is_dropped(node: Any) -> None:
    sent: list[str] = []
    node._text_subject.subscribe(on_next=sent.append)

    node._on_agent_message(
        AIMessage(
            content=[
                {"type": "text", "text": "見て"},
                {"type": "image_url", "image_url": {"url": "data:..."}},
            ]
        )
    )

    assert sent == []


def test_speak_invoke_bench_event_emitted_once_per_message(node: Any) -> None:
    with patch("dimos.agents.skills.speak_skill_ja.log_bench_event") as logbench:
        node._on_agent_message(AIMessage(content="こんにちは"))
        node._on_agent_message(AIMessage(content="さようなら"))

    speak_invokes = [c for c in logbench.call_args_list if c.args == ("speak_invoke",)]
    assert len(speak_invokes) == 2


def test_first_audio_out_emitted_once_per_message(node: Any) -> None:
    with patch("dimos.agents.skills.speak_skill_ja.log_bench_event") as logbench:
        node._on_agent_message(AIMessage(content="こんにちは"))
        node._on_audio_chunk(object())
        node._on_audio_chunk(object())

        node._on_agent_message(AIMessage(content="さようなら"))
        node._on_audio_chunk(object())

    first_audio_calls = [
        c for c in logbench.call_args_list
        if c.args == ("first_audio_out",) and c.kwargs.get("tool") == "speak"
    ]
    assert len(first_audio_calls) == 2
