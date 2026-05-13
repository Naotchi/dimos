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

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from reactivex import Observable, Subject

from dimos.stream.audio.base import AbstractAudioConsumer, AbstractAudioEmitter, AudioEvent
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

ToolCallHandler = Callable[[str, str, str], None]  # (call_id, name, args_json) -> None


class AzureVoiceLiveNode(AbstractAudioConsumer, AbstractAudioEmitter):
    """WebSocket client for Azure Voice Live API.

    Streams microphone PCM up and receives TTS PCM + function calls down.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str,
        voice: str,
        instructions: str,
        tools: list[dict[str, Any]],
        on_tool_call: ToolCallHandler,
        sample_rate: int = 24000,
    ) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.instructions = instructions
        self.tools = tools
        self.on_tool_call = on_tool_call
        self.sample_rate = sample_rate

        self._audio_out_subject: Subject[AudioEvent] = Subject()
        self._audio_in_subject: Subject[AudioEvent] | None = None

    def consume_audio(self, audio_observable: Observable) -> "AzureVoiceLiveNode":
        self._audio_in_subject = audio_observable  # type: ignore[assignment]
        return self

    def emit_audio(self) -> Observable:
        return self._audio_out_subject
