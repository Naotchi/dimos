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

"""Neural Japanese TTS node backed by the VOICEVOX engine HTTP API.

Talks to a VOICEVOX engine over HTTP (default ``http://127.0.0.1:50021``).

Synthesis params (speaker_id / *_scale) are passed in by the caller.
The Config seed for those values lives in
``AssistantSpeechNodeJaConfig.voicevox`` (see ``speak_skill_ja.py``).

Env vars read directly here (category B: deployment-dependent):

- ``DIMOS_VOICEVOX_URL``              base URL (default ``http://127.0.0.1:50021``)
- ``DIMOS_VOICEVOX_PROBE_ATTEMPTS``   probe retry count (default ``10``)
- ``DIMOS_VOICEVOX_PROBE_TIMEOUT``    per-probe timeout seconds (default ``10``)
"""

from __future__ import annotations

import io
import os
import threading
import time
import wave

import numpy as np
import requests
from reactivex import Observable, Subject

from dimos.stream.audio.base import AbstractAudioEmitter, AudioEvent
from dimos.stream.audio.text.base import AbstractTextConsumer, AbstractTextEmitter
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_DEFAULT_URL = "http://127.0.0.1:50021"
_DEFAULT_SPEAKER_ID = 74  # 東北イタコ ノーマル


class VoicevoxTTSNode(AbstractTextConsumer, AbstractAudioEmitter, AbstractTextEmitter):
    """Japanese neural TTS via the VOICEVOX HTTP engine."""

    def __init__(
        self,
        base_url: str | None = None,
        speaker_id: int = _DEFAULT_SPEAKER_ID,
        speed_scale: float = 1.0,
        pitch_scale: float = 0.0,
        intonation_scale: float = 1.0,
        volume_scale: float = 1.0,
        request_timeout: float = 30.0,
    ) -> None:
        self.audio_subject: Subject = Subject()  # type: ignore[type-arg]
        self.text_subject: Subject = Subject()  # type: ignore[type-arg]
        self.subscription = None
        self.processing_thread: threading.Thread | None = None
        self.is_running = True
        self.text_queue: list[str] = []
        self.queue_lock = threading.Lock()

        # base_url stays env-aware (category B: deployment-dependent endpoint).
        self._base = (
            base_url or os.environ.get("DIMOS_VOICEVOX_URL", _DEFAULT_URL)
        ).rstrip("/")
        self._speaker_id = speaker_id
        self._speed_scale = speed_scale
        self._pitch_scale = pitch_scale
        self._intonation_scale = intonation_scale
        self._volume_scale = volume_scale
        self._timeout = request_timeout

        # Probe so we fail fast at start() rather than on first utterance.
        # First request can be slow while the engine warms up its models, so
        # retry a few times before giving up.
        probe_attempts = int(os.environ.get("DIMOS_VOICEVOX_PROBE_ATTEMPTS", "10"))
        probe_timeout = float(os.environ.get("DIMOS_VOICEVOX_PROBE_TIMEOUT", "10"))
        last_err: Exception | None = None
        for i in range(probe_attempts):
            try:
                r = requests.get(f"{self._base}/version", timeout=probe_timeout)
                r.raise_for_status()
                logger.info(
                    "VOICEVOX engine %s @ %s speaker_id=%d",
                    r.text.strip(),
                    self._base,
                    self._speaker_id,
                )
                last_err = None
                break
            except Exception as e:
                last_err = e
                logger.info(
                    "VOICEVOX probe attempt %d/%d failed: %s", i + 1, probe_attempts, e
                )
                time.sleep(2.0)
        if last_err is not None:
            raise RuntimeError(
                f"Cannot reach VOICEVOX engine at {self._base} after "
                f"{probe_attempts} attempts: {last_err}. "
                "Start the engine (e.g. `voicevox_engine` or "
                "`docker run --rm -p 50021:50021 voicevox/voicevox_engine:cpu-ubuntu22.04-latest`) "
                "or set DIMOS_VOICEVOX_URL."
            ) from last_err

        # VOICEVOX default output is 24 kHz mono 16-bit. We read the actual
        # rate from each WAV header anyway, but expose 24000 up-front so the
        # downstream audio sink can be opened immediately.
        self.sample_rate = 24000

    def emit_audio(self) -> Observable:  # type: ignore[type-arg]
        return self.audio_subject

    def emit_text(self) -> Observable:  # type: ignore[type-arg]
        return self.text_subject

    def consume_text(self, text_observable: Observable) -> "AbstractTextConsumer":  # type: ignore[type-arg]
        logger.info("Starting VoicevoxTTSNode")
        self.processing_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.processing_thread.start()
        self.subscription = text_observable.subscribe(  # type: ignore[assignment]
            on_next=self._queue_text,
            on_error=lambda e: logger.error(f"Error in VoicevoxTTSNode: {e}"),
        )
        return self

    def _queue_text(self, text: str) -> None:
        if not text.strip():
            return
        with self.queue_lock:
            self.text_queue.append(text)

    def _process_queue(self) -> None:
        while self.is_running:
            text_to_process: str | None = None
            with self.queue_lock:
                if self.text_queue:
                    text_to_process = self.text_queue.pop(0)
            if text_to_process is not None:
                self._synthesize_speech(text_to_process)
            else:
                time.sleep(0.05)

    def _synthesize_speech(self, text: str) -> None:
        try:
            q = requests.post(
                f"{self._base}/audio_query",
                params={"text": text, "speaker": self._speaker_id},
                timeout=self._timeout,
            )
            q.raise_for_status()
            query = q.json()
            query["speedScale"] = self._speed_scale
            query["pitchScale"] = self._pitch_scale
            query["intonationScale"] = self._intonation_scale
            query["volumeScale"] = self._volume_scale

            s = requests.post(
                f"{self._base}/synthesis",
                params={"speaker": self._speaker_id},
                json=query,
                timeout=self._timeout,
            )
            s.raise_for_status()
            wav_bytes = s.content

            with wave.open(io.BytesIO(wav_bytes)) as wf:
                sr = wf.getframerate()
                channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                pcm = wf.readframes(wf.getnframes())

            if sampwidth != 2:
                logger.error(
                    "VOICEVOX returned unexpected sample width: %d bytes", sampwidth
                )
                return
            waveform = np.frombuffer(pcm, dtype=np.int16)

            self.text_subject.on_next(text)
            self.audio_subject.on_next(
                AudioEvent(
                    data=waveform,
                    sample_rate=int(sr),
                    timestamp=time.time(),
                    channels=channels,
                )
            )
        except Exception as e:
            logger.error(f"Error synthesizing speech via VOICEVOX: {e}")

    def dispose(self) -> None:
        logger.info("Disposing VoicevoxTTSNode")
        self.is_running = False
        with self.queue_lock:
            self.text_queue.clear()
        if self.processing_thread and self.processing_thread.is_alive():
            self.processing_thread.join(timeout=2.0)
        if self.subscription:
            self.subscription.dispose()
            self.subscription = None
        self.audio_subject.on_completed()
        self.text_subject.on_completed()


__all__ = ["VoicevoxTTSNode"]
