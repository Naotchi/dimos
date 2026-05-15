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

"""Japanese WebInput variant: same pipeline as WebInput but Whisper transcribes ja."""

from threading import Thread
from typing import TYPE_CHECKING

import reactivex as rx
import reactivex.operators as ops

from dimos.agents.web_human_input import WebInput
from dimos.core.core import rpc
from dimos.core.transport import pLCMTransport
from dimos.stream.audio.node_normalizer import AudioNormalizer
from dimos.utils.logging_config import setup_logger
from dimos.web.robot_web_interface import RobotWebInterface

if TYPE_CHECKING:
    from dimos.stream.audio.base import AudioEvent

logger = setup_logger()


class JapaneseWebInput(WebInput):
    """WebInput that initializes WhisperNode with language='ja'.

    WebInput's start() instantiates WhisperNode() with no args, defaulting to
    English. We re-implement start() here only to pass modelopts={"language":
    "ja", "fp16": False} — keeping upstream WebInput untouched.
    """

    @rpc
    def start(self) -> None:
        from dimos.core.module import Module

        Module.start(self)

        self._human_transport = pLCMTransport("/human_input")

        audio_subject: rx.subject.Subject[AudioEvent] = rx.subject.Subject()

        self._web_interface = RobotWebInterface(
            port=5555,
            text_streams={"agent_responses": rx.subject.Subject()},
            audio_subject=audio_subject,
        )

        normalizer = AudioNormalizer()

        from dimos.stream.audio.stt.node_whisper import WhisperNode

        stt_node = WhisperNode(modelopts={"language": "ja", "fp16": False})

        normalizer.consume_audio(audio_subject.pipe(ops.share()))
        stt_node.consume_audio(normalizer.emit_audio())

        unsub = self._web_interface.query_stream.subscribe(self._human_transport.publish)
        self.register_disposable(unsub)

        unsub = stt_node.emit_text().subscribe(self._human_transport.publish)
        self.register_disposable(unsub)

        self._thread = Thread(target=self._web_interface.run, daemon=True)
        self._thread.start()

        logger.info("JapaneseWebInput started at http://localhost:5555")
