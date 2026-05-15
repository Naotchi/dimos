#!/usr/bin/env python3
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

"""Japanese SpeakSkill variant backed by pyopenjtalk."""

from dimos.agents.skills.speak_skill import SpeakSkill
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.stream.audio.node_output import SounddeviceAudioOutput
from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode


class JapaneseSpeakSkill(SpeakSkill):
    """SpeakSkill that synthesizes Japanese via the bundled pyopenjtalk Mei voice.

    Inherits speak()/stop() from SpeakSkill. Overrides start() to bypass the
    parent's OpenAI TTS init and wire OpenJTalkTTSNode → SounddeviceAudioOutput
    at 48 kHz.
    """

    @rpc
    def start(self) -> None:
        # Skip SpeakSkill.start (which constructs OpenAITTSNode); call grandparent.
        Module.start(self)
        self._tts_node = OpenJTalkTTSNode()  # type: ignore[assignment]
        self._audio_output = SounddeviceAudioOutput(sample_rate=48000)
        self._audio_output.consume_audio(self._tts_node.emit_audio())
