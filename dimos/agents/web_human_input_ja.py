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

"""Japanese WebInput variant: Whisper(ja) + bench instrumentation.

Differs from upstream WebInput in two ways:
1. WhisperNode is configured with language='ja'.
2. STT timing is emitted via dimos.agents.bench_ja.log_bench_event so the
   bench event schema stays consistent across the *_ja.py files.

Also exposes self._audio_subject so bench replay scripts can publish
fixture audio into the pipeline without going through the WebUI.
"""

from __future__ import annotations

import time
from threading import Thread
from typing import TYPE_CHECKING, Any

import reactivex as rx
import reactivex.operators as ops

from dimos.agents.bench_ja import log_bench_event
from dimos.agents.web_human_input import WebInput
from dimos.core.core import rpc
from dimos.core.stream import Out
from dimos.core.transport import pLCMTransport
from dimos.stream.audio.base import AudioEvent
from dimos.stream.audio.node_normalizer import AudioNormalizer
from dimos.stream.audio.stt.node_whisper import WhisperNode
from dimos.utils.logging_config import setup_logger
from dimos.web.robot_web_interface import RobotWebInterface

if TYPE_CHECKING:
    pass

logger = setup_logger()


def _make_stt_timer() -> tuple[Any, Any]:
    """Return (audio_tap, text_tap) operators that emit stt_done bench events.

    The audio tap pushes (t0, audio_seconds) onto a FIFO when an AudioEvent
    enters Whisper; the text tap pops it when the transcription emerges and
    logs the round-trip via bench_ja.log_bench_event. Whisper is synchronous
    (one audio in -> one text out), so a plain list FIFO is sufficient.
    """
    pending: list[dict[str, Any]] = []

    def on_audio(event: "AudioEvent") -> None:
        sr = float(getattr(event, "sample_rate", 0) or 16000)
        n = int(getattr(event.data, "shape", [0])[0] or 0)
        pending.append(
            {
                "t0": time.perf_counter(),
                "audio_seconds": round(n / sr, 4) if sr else None,
            }
        )

    def on_text(text: str) -> None:
        info = pending.pop(0) if pending else {"t0": time.perf_counter(), "audio_seconds": None}
        elapsed = time.perf_counter() - info["t0"]
        log_bench_event(
            "stt_done",
            duration_s=round(elapsed, 4),
            audio_seconds=info.get("audio_seconds"),
            text_len=len(text),
        )

    return ops.do_action(on_audio), ops.do_action(on_text)


class JapaneseWebInput(WebInput):
    """WebInput that runs Whisper in Japanese and emits bench-schema events.

    Implementation mirrors upstream WebInput.start() so we don't depend on the
    parent's internals; we just hold our own references (notably to
    self._audio_subject) so bench replay scripts can drive the pipeline.
    """

    _audio_subject: rx.subject.Subject  # exposed for bench replay; treat as internal
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

        normalizer = AudioNormalizer()
        stt_node = WhisperNode(modelopts={"language": "ja", "fp16": False})

        normalizer.consume_audio(self._audio_subject.pipe(ops.share()))
        audio_tap, text_tap = _make_stt_timer()
        stt_node.consume_audio(normalizer.emit_audio().pipe(audio_tap))

        unsub = self._web_interface.query_stream.subscribe(self._human_transport.publish)
        self.register_disposable(unsub)

        unsub = stt_node.emit_text().pipe(text_tap).subscribe(self._human_transport.publish)
        self.register_disposable(unsub)

        self._thread = Thread(target=self._web_interface.run, daemon=True)
        self._thread.start()

        logger.info("JapaneseWebInput started at http://localhost:5555")
