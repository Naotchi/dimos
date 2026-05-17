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

"""Whisper-based human-input bridge for the local-tts blueprint.

Consumes complete utterances on ``mic_utterance``, runs them through the
Japanese-tuned Whisper pipeline (Normalizer → WhisperNode(ja)), and
publishes the transcript on the ``/human_input`` pLCM transport that the
agent already listens to.

Audio segmentation is *not* this module's job — callers must deliver
already-segmented utterances (e.g. via :class:`LocalMicrophoneJa`'s PTT
recorder). Per-frame audio would trigger one Whisper invocation per 64 ms
block, which is not what we want.

Bench instrumentation mirrors :mod:`dimos.agents.web_human_input_ja` so the
``stt_done`` event schema stays consistent across the *_ja.py files.
"""
from __future__ import annotations

import time
from typing import Any

import reactivex as rx
import reactivex.operators as ops

from dimos.agents.bench_ja import log_bench_event
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.stream import In
from dimos.core.transport import pLCMTransport
from dimos.stream.audio.base import AudioEvent
from dimos.stream.audio.node_normalizer import AudioNormalizer
from dimos.stream.audio.stt.node_whisper import WhisperNode
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


def _make_stt_timer() -> tuple[Any, Any]:
    """Return (audio_tap, text_tap) operators that emit stt_done bench events.

    Duplicated from web_human_input_ja so this module has no upward
    dependency on the WebUI flavour.
    """
    pending: list[dict[str, Any]] = []

    def on_audio(event: AudioEvent) -> None:
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


class WhisperHumanInputJa(Module):
    """Bridge: AudioEvent utterances → Japanese Whisper → ``/human_input``."""

    mic_utterance: In[AudioEvent]

    _audio_subject: rx.subject.Subject  # type: ignore[type-arg]
    _human_transport: pLCMTransport[str] | None = None

    @rpc
    def start(self) -> None:
        super().start()

        self._human_transport = pLCMTransport("/human_input")
        self._audio_subject = rx.subject.Subject()

        normalizer = AudioNormalizer()
        stt_node = WhisperNode(modelopts={"language": "ja", "fp16": False})

        audio_tap, text_tap = _make_stt_timer()
        normalizer.consume_audio(self._audio_subject.pipe(ops.share()))
        stt_node.consume_audio(normalizer.emit_audio().pipe(audio_tap))

        unsub = stt_node.emit_text().pipe(text_tap).subscribe(self._human_transport.publish)
        self.register_disposable(unsub)

        # Bridge In[AudioEvent] → internal Subject so bench replay scripts
        # can also publish to self._audio_subject directly (parity with
        # JapaneseWebInput).
        gate_unsub = self.mic_utterance.subscribe(self._audio_subject.on_next)
        self.register_disposable(gate_unsub)

        logger.info("WhisperHumanInputJa started (Whisper ja, fp16=False)")

    @rpc
    def stop(self) -> None:
        if self._human_transport:
            self._human_transport.lcm.stop()
            self._human_transport = None
        super().stop()


__all__ = ["WhisperHumanInputJa"]
