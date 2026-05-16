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

"""WebInput variant that exposes raw audio without running STT.

Used by the Azure Voice Live blueprint: the model accepts PCM directly,
so a separate Whisper pass would duplicate the user message (audio +
transcribed text both reach the model and the second arrival triggers
a second response). This class drops the Whisper pipeline and only
publishes the raw audio stream and the typed-text /human_input.
"""

from __future__ import annotations

from threading import Thread

import reactivex as rx

from dimos.agents.web_human_input import WebInput
from dimos.core.core import rpc
from dimos.core.stream import Out
from dimos.core.transport import pLCMTransport
from dimos.stream.audio.base import AudioEvent
from dimos.utils.logging_config import setup_logger
from dimos.web.robot_web_interface import RobotWebInterface

logger = setup_logger()


class WebInputAudioOnly(WebInput):
    """WebInput exposing audio_out (no STT). Typed text still flows via
    /human_input through the existing query_stream subscription."""

    _audio_subject: rx.subject.Subject
    _web_interface: RobotWebInterface
    _human_transport: pLCMTransport
    _thread: Thread

    audio_out: Out[AudioEvent]

    @rpc
    def start(self) -> None:
        from dimos.core.module import Module

        Module.start(self)

        self._human_transport = pLCMTransport("/human_input")
        self._audio_subject = rx.subject.Subject()

        audio_out_sub = self._audio_subject.subscribe(
            on_next=self.audio_out.publish
        )
        self.register_disposable(audio_out_sub)

        self._web_interface = RobotWebInterface(
            port=5555,
            text_streams={"agent_responses": rx.subject.Subject()},
            audio_subject=self._audio_subject,
        )

        unsub = self._web_interface.query_stream.subscribe(
            self._human_transport.publish
        )
        self.register_disposable(unsub)

        self._thread = Thread(target=self._web_interface.run, daemon=True)
        self._thread.start()

        logger.info("WebInputAudioOnly started at http://localhost:5555")
