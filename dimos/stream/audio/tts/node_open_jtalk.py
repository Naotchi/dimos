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

import threading
import time

import numpy as np
import pyopenjtalk  # type: ignore[import-not-found]
from reactivex import Observable, Subject

from dimos.stream.audio.base import AbstractAudioEmitter, AudioEvent
from dimos.stream.audio.text.base import AbstractTextConsumer, AbstractTextEmitter
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

SAMPLE_RATE = 48000


class OpenJTalkTTSNode(AbstractTextConsumer, AbstractAudioEmitter, AbstractTextEmitter):
    """Japanese TTS node backed by pyopenjtalk.

    Consumes text, synthesizes Japanese speech via the bundled Mei HTS voice,
    emits AudioEvent objects on emit_audio(), and re-emits the spoken text on
    emit_text(). Mirrors OpenAITTSNode's background-thread + queue pattern.
    """

    def __init__(self) -> None:
        self.audio_subject = Subject()  # type: ignore[var-annotated]
        self.text_subject = Subject()  # type: ignore[var-annotated]
        self.subscription = None
        self.processing_thread: threading.Thread | None = None
        self.is_running = True
        self.text_queue: list[str] = []
        self.queue_lock = threading.Lock()

    def emit_audio(self) -> Observable:  # type: ignore[type-arg]
        return self.audio_subject

    def emit_text(self) -> Observable:  # type: ignore[type-arg]
        return self.text_subject

    def consume_text(self, text_observable: Observable) -> "AbstractTextConsumer":  # type: ignore[type-arg]
        logger.info("Starting OpenJTalkTTSNode")
        self.processing_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.processing_thread.start()
        self.subscription = text_observable.subscribe(  # type: ignore[assignment]
            on_next=self._queue_text,
            on_error=lambda e: logger.error(f"Error in OpenJTalkTTSNode: {e}"),
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
            waveform, _ = pyopenjtalk.tts(text)
            self.text_subject.on_next(text)
            # pyopenjtalk returns float64 in int16 amplitude range;
            # cast to int16 so SounddeviceAudioOutput's to_float32 normalizes correctly.
            audio_event = AudioEvent(
                data=waveform.astype(np.int16),
                sample_rate=SAMPLE_RATE,
                timestamp=time.time(),
                channels=1,
            )
            self.audio_subject.on_next(audio_event)
        except Exception as e:
            logger.error(f"Error synthesizing speech: {e}")

    def dispose(self) -> None:
        logger.info("Disposing OpenJTalkTTSNode")
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
