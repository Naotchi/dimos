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

"""PTT-gated local microphone utterance source.

Wraps :class:`SounddeviceAudioSource` as a blueprint-compatible Module that
buffers PCM frames while ``mic_gate`` is True and, on the falling edge,
emits the concatenated recording as a single ``AudioEvent`` on
``mic_utterance``. Designed to feed :class:`WhisperHumanInputJa`, which
expects complete utterances (not 64 ms PortAudio frames).

Pairs with :class:`PttKeyboard` via autoconnect on ``mic_gate``. If nothing
publishes to ``mic_gate``, no audio is emitted — intentional so an
unconfigured deployment fails loud rather than hot-miking the user.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Literal

import numpy as np
from pydantic import Field

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.stream.audio.base import AudioEvent
from dimos.stream.audio.node_microphone import SounddeviceAudioSource
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_ENV_PREFIX = "DIMOS_LOCAL_MIC_"


class LocalMicrophoneJaConfig(ModuleConfig):
    device_index: int | None = Field(
        default_factory=lambda: (
            int(v) if (v := os.environ.get(f"{_ENV_PREFIX}DEVICE")) else None
        )
    )
    # Whisper expects 16 kHz mono; keep that as the default so downstream
    # AudioNormalizer is a no-op when nothing overrides it.
    sample_rate: int = Field(
        default_factory=lambda: int(os.environ.get(f"{_ENV_PREFIX}SAMPLE_RATE", "16000"))
    )
    block_size: int = Field(
        default_factory=lambda: int(os.environ.get(f"{_ENV_PREFIX}BLOCK_SIZE", "1024"))
    )
    # Cap a single PTT-hold recording. Beyond this we force-close to avoid
    # unbounded RAM if the user wedges the key.
    max_utterance_seconds: float = Field(
        default_factory=lambda: float(os.environ.get(f"{_ENV_PREFIX}MAX_SECONDS", "60"))
    )

    # --- VAD / hold モード切り替え（実行場所非依存 → env seed なし、profile 専管）---
    mic_mode: Literal["hold", "vad"] = Field(default="hold")
    vad_threshold: float = Field(default=0.5)
    vad_min_silence_ms: int = Field(default=700)
    vad_speech_pad_ms: int = Field(default=300)
    vad_min_speech_ms: int = Field(default=200)


class LocalMicrophoneJa(Module):
    """PTT-driven microphone: buffers while gate open, emits utterance on close."""

    config: LocalMicrophoneJaConfig
    mic_gate: In[bool]
    mic_utterance: Out[AudioEvent]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._mic: SounddeviceAudioSource | None = None
        self._mic_unsub: Any = None
        self._gate_unsub: Any = None
        # PortAudio callback (audio thread) and mic_gate subscriber (transport
        # thread) both touch _buffer / _recording, so guard with a lock.
        self._lock = threading.Lock()
        self._buffer: list[AudioEvent] = []
        self._recording = False
        self._recording_started_at = 0.0

    @rpc
    def start(self) -> None:
        super().start()
        cfg = self.config
        self._mic = SounddeviceAudioSource(
            device_index=cfg.device_index,
            sample_rate=cfg.sample_rate,
            block_size=cfg.block_size,
        )
        self._mic_unsub = self._mic.emit_audio().subscribe(on_next=self._on_audio)
        self._gate_unsub = self.mic_gate.subscribe(self._on_gate)
        logger.info(
            "LocalMicrophoneJa started (device=%s, sr=%d Hz, block=%d)",
            cfg.device_index,
            cfg.sample_rate,
            cfg.block_size,
        )

    @rpc
    def stop(self) -> None:
        if self._gate_unsub is not None:
            self._gate_unsub()
            self._gate_unsub = None
        if self._mic_unsub is not None:
            self._mic_unsub.dispose()
            self._mic_unsub = None
        self._mic = None
        with self._lock:
            self._buffer.clear()
            self._recording = False
        super().stop()

    @rpc
    def inject_utterance(self, wav_path: str) -> None:
        """Bench-only entry point: publish a wav fixture as a single utterance.

        Bypasses the PortAudio capture path and the PTT gate so the bench
        replay driver can drive the agent without a real microphone or
        keyboard. Intended for use from scripts/replay_agentic_local_tts.py.
        """
        event = _load_wav_as_audio_event(wav_path)
        logger.info("inject_utterance: %s (%d samples @ %d Hz)",
                    wav_path, event.data.shape[0], event.sample_rate)
        self.mic_utterance.publish(event)

    def _on_gate(self, active: bool) -> None:
        if active:
            with self._lock:
                if self._recording:
                    return
                self._buffer = []
                self._recording = True
                self._recording_started_at = time.time()
            logger.info("PTT down: recording")
        else:
            self._flush()

    def _on_audio(self, event: AudioEvent) -> None:
        with self._lock:
            if not self._recording:
                return
            self._buffer.append(event)
            elapsed = time.time() - self._recording_started_at
            over_limit = elapsed > self.config.max_utterance_seconds
        if over_limit:
            logger.warning(
                "PTT hold exceeded %.1fs; auto-flushing utterance",
                self.config.max_utterance_seconds,
            )
            self._flush()

    def _flush(self) -> None:
        with self._lock:
            if not self._recording:
                return
            buf = self._buffer
            self._buffer = []
            self._recording = False
            duration = time.time() - self._recording_started_at
        if not buf:
            logger.info("PTT up: no audio captured (released too fast?)")
            return
        utterance = _combine(buf)
        if utterance is None:
            logger.warning("PTT up: failed to combine %d frames", len(buf))
            return
        logger.info(
            "PTT up: emitting utterance (%.2fs, %d samples)",
            duration,
            utterance.data.shape[0],
        )
        self.mic_utterance.publish(utterance)


def _load_wav_as_audio_event(path: str) -> AudioEvent:
    """Load a PCM16 mono WAV from disk and return an AudioEvent.

    Used by the bench replay driver to feed fixture wavs into the same
    mic_utterance stream a real PTT-held microphone would publish to.
    """
    import wave

    with wave.open(path, "rb") as w:
        if w.getnchannels() != 1:
            raise ValueError(f"expected mono wav, got {w.getnchannels()} channels: {path}")
        if w.getsampwidth() != 2:
            raise ValueError(f"expected 16-bit PCM wav: {path}")
        sample_rate = w.getframerate()
        n_frames = w.getnframes()
        raw = w.readframes(n_frames)

    data = np.frombuffer(raw, dtype=np.int16)
    return AudioEvent(
        data=data,
        sample_rate=sample_rate,
        timestamp=time.time(),
        channels=1,
    )


def _combine(events: list[AudioEvent]) -> AudioEvent | None:
    """Concatenate per-frame AudioEvents into one. Returns None if empty."""
    valid = [e for e in events if e is not None and getattr(e.data, "size", 0) > 0]
    if not valid:
        return None
    first = valid[0]
    data = np.concatenate([e.data for e in valid], axis=0)
    return AudioEvent(
        data=data,
        sample_rate=first.sample_rate,
        timestamp=first.timestamp,
        channels=first.channels,
    )


__all__ = ["LocalMicrophoneJa", "LocalMicrophoneJaConfig", "_load_wav_as_audio_event"]
