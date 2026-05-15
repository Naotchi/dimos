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

"""Japanese SpeakSkill variant: pyopenjtalk TTS + first_audio_out bench event."""

from __future__ import annotations

import threading
from typing import Any

import reactivex.operators as ops

from dimos.agents.bench_ja import log_bench_event
from dimos.agents.skills.speak_skill import SpeakSkill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.stream.audio.node_output import SounddeviceAudioOutput
from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode


class JapaneseSpeakSkill(SpeakSkill):
    """SpeakSkill that synthesizes Japanese via pyopenjtalk and emits first_audio_out.

    - Overrides start() to bypass SpeakSkill.start (which would init OpenAITTSNode)
      and wire OpenJTalkTTSNode -> SounddeviceAudioOutput at 48 kHz.
    - Taps the audio stream with do_action so the first chunk emitted after each
      speak() call fires a 'first_audio_out' bench event.
    """

    _first_chunk_pending: bool
    _first_chunk_lock: threading.Lock

    @rpc
    def start(self) -> None:
        # Skip SpeakSkill.start (which constructs OpenAITTSNode); call grandparent.
        Module.start(self)

        self._first_chunk_pending = False
        self._first_chunk_lock = threading.Lock()

        self._tts_node = OpenJTalkTTSNode()  # type: ignore[assignment]
        self._audio_output = SounddeviceAudioOutput(sample_rate=48000)

        tapped = self._tts_node.emit_audio().pipe(ops.do_action(self._on_audio_chunk))
        self._audio_output.consume_audio(tapped)

    def _on_audio_chunk(self, _chunk: Any) -> None:
        """Fire first_audio_out exactly once per speak() invocation."""
        with self._first_chunk_lock:
            if not self._first_chunk_pending:
                return
            self._first_chunk_pending = False
        log_bench_event("first_audio_out", tool="speak")

    def speak(self, text: str, blocking: bool = True) -> str:
        """Arm the first-chunk flag, then delegate to upstream speak()."""
        with self._first_chunk_lock:
            self._first_chunk_pending = True
        return super().speak(text, blocking=blocking)
