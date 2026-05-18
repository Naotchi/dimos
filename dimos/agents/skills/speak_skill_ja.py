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
``(name, type)``) and feeds the text content of each ``AIMessage`` straight
into ``StyleBertVits2TTSNode`` -> ``SounddeviceAudioOutput``. The TTS node
exposes its native sample rate so the audio sink can be opened at a
matching rate without resampling.
"""

from __future__ import annotations

import os
import threading
from typing import Any

import reactivex.operators as ops
from langchain_core.messages import AIMessage
from langchain_core.messages.base import BaseMessage
from reactivex import Subject
from reactivex.disposable import Disposable

from dimos.agents.bench_ja import log_bench_event
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In
from dimos.stream.audio.node_output import SounddeviceAudioOutput
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


def _build_tts_node():
    backend = os.environ.get("DIMOS_TTS_BACKEND", "sbv2").strip().lower()
    if backend == "voicevox":
        from dimos.stream.audio.tts.node_voicevox import VoicevoxTTSNode
        logger.info("[TTS] backend=voicevox")
        return VoicevoxTTSNode()
    if backend not in ("sbv2", "style_bert_vits2", ""):
        logger.warning("[TTS] unknown DIMOS_TTS_BACKEND=%r, falling back to sbv2", backend)
    from dimos.stream.audio.tts.node_style_bert_vits2 import StyleBertVits2TTSNode
    logger.info("[TTS] backend=sbv2")
    return StyleBertVits2TTSNode()


class AssistantSpeechNodeJa(Module):
    """Speak assistant message text via local Japanese TTS.

    Wired by autoconnect to ``McpClient.agent`` (Out[BaseMessage]) via the
    matching ``agent: In[BaseMessage]`` field name + type.
    """

    agent: In[BaseMessage]

    @rpc
    def start(self) -> None:
        super().start()

        self._first_chunk_pending = False
        self._first_chunk_lock = threading.Lock()

        self._tts_node = _build_tts_node()
        self._audio_output = SounddeviceAudioOutput(sample_rate=self._tts_node.sample_rate)

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
        """Fire ``first_audio_out`` exactly once per ``_on_agent_message`` call."""
        with self._first_chunk_lock:
            if not self._first_chunk_pending:
                return
            self._first_chunk_pending = False
        log_bench_event("first_audio_out", tool="speak")


__all__ = ["AssistantSpeechNodeJa"]
