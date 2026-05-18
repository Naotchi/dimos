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

"""Speak assistant messages directly via local Japanese TTS.

Subscribes to ``McpClient.agent: Out[BaseMessage]`` (autoconnect wires by
``(name, type)``) and feeds the text content of each ``AIMessage`` into a
TTS node selected by ``impl`` (default ``open_jtalk``). Output goes to
``SounddeviceAudioOutput``.
"""

from __future__ import annotations

import threading
from typing import Any

import reactivex.operators as ops
from langchain_core.messages import AIMessage
from langchain_core.messages.base import BaseMessage
from reactivex import Subject
from reactivex.disposable import Disposable

from dimos.agents.bench_ja import log_bench_event
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In
from dimos.stream.audio.node_output import SounddeviceAudioOutput
from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode
from dimos.stream.audio.tts.node_openai import OpenAITTSNode, Voice
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class AssistantSpeechNodeJaConfig(ModuleConfig):
    """Config selecting the underlying TTS implementation."""

    impl: str = "open_jtalk"  # one of: open_jtalk, openai
    openai_voice: str = "echo"  # used when impl == "openai"
    openai_model: str = "tts-1"  # used when impl == "openai"


class AssistantSpeechNodeJa(Module):
    """Speak assistant message text via a configurable Japanese TTS node."""

    agent: In[BaseMessage]
    config: AssistantSpeechNodeJaConfig

    def _make_tts_node(self):
        impl = self.config.impl
        if impl == "open_jtalk":
            return OpenJTalkTTSNode()
        if impl == "openai":
            return OpenAITTSNode(
                voice=Voice(self.config.openai_voice),
                model=self.config.openai_model,
            )
        raise ValueError(f"Unknown AssistantSpeechNodeJa impl: {impl!r}")

    @rpc
    def start(self) -> None:
        super().start()

        self._first_chunk_pending = False
        self._first_chunk_lock = threading.Lock()

        self._tts_node = self._make_tts_node()
        self._audio_output = SounddeviceAudioOutput(sample_rate=48000)

        self._text_subject = Subject()
        self._tts_node.consume_text(self._text_subject)

        tapped = self._tts_node.emit_audio().pipe(ops.do_action(self._on_audio_chunk))
        self._audio_output.consume_audio(tapped)

        self.register_disposable(
            Disposable(self.agent.subscribe(self._on_agent_message))
        )

    @rpc
    def stop(self) -> None:
        if self._text_subject is not None:
            self._text_subject.on_completed()
            self._text_subject = None
        if self._tts_node is not None:
            self._tts_node.dispose()
            self._tts_node = None
        if self._audio_output is not None:
            self._audio_output.stop()
            self._audio_output = None
        super().stop()

    def _on_agent_message(self, msg: BaseMessage) -> None:
        if not isinstance(msg, AIMessage):
            return
        content = msg.content
        if not isinstance(content, str):
            return
        if content.strip() == "":
            return
        if self._text_subject is None:
            logger.warning(
                "AssistantSpeechNodeJa received agent message after stop(); dropping."
            )
            return

        log_bench_event("speak_invoke")
        with self._first_chunk_lock:
            self._first_chunk_pending = True
        self._text_subject.on_next(content)

    def _on_audio_chunk(self, _chunk: Any) -> None:
        with self._first_chunk_lock:
            if not self._first_chunk_pending:
                return
            self._first_chunk_pending = False
        log_bench_event("first_audio_out", tool="speak")


__all__ = ["AssistantSpeechNodeJa", "AssistantSpeechNodeJaConfig"]
