#!/usr/bin/env python3
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Streaming VAD utterance segmenter for the local Japanese mic path.

Wraps silero ``VADIterator``. :class:`LocalMicrophoneJa` opens the mic at the
silero window width (512 samples @ 16 kHz / 256 @ 8 kHz) in vad mode, so each
mic frame is exactly one VAD window — no re-chunking. Frames are fed one at a
time and assembled into a single utterance ``AudioEvent`` on the falling edge
of speech. Used by :class:`LocalMicrophoneJa` in vad mode.

The silero dependency is isolated in :meth:`from_config`; the core
:meth:`feed` logic takes an injected iterator so it is unit-testable with a
fake (no model, no torch).
"""
from __future__ import annotations

from collections import deque
from typing import Any

import numpy as np

from dimos.stream.audio.base import AudioEvent
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_CHUNK_FOR_RATE = {16000: 512, 8000: 256}


class VadStreamSegmenter:
    """Assemble utterances from a streaming mic using an injected VADIterator."""

    def __init__(
        self,
        vad_iterator: Any,
        *,
        sample_rate: int = 16000,
        speech_pad_ms: int = 300,
        min_speech_ms: int = 200,
        max_utterance_seconds: float = 60.0,
    ) -> None:
        if sample_rate not in _CHUNK_FOR_RATE:
            raise ValueError(
                f"VAD requires 16000 or 8000 Hz, got {sample_rate}"
            )
        self._iter = vad_iterator
        self._sr = sample_rate
        self.chunk = _CHUNK_FOR_RATE[sample_rate]
        self._preroll_samples = int(speech_pad_ms * sample_rate / 1000)
        self._min_speech_samples = int(min_speech_ms * sample_rate / 1000)
        self._max_samples = int(max_utterance_seconds * sample_rate)

        self._preroll: deque[np.ndarray] = deque()
        self._preroll_total = 0
        self._recording = False
        self._utt: list[np.ndarray] = []
        self._utt_samples = 0

    @classmethod
    def from_config(cls, cfg: Any) -> "VadStreamSegmenter":
        """Build a segmenter backed by a real silero VADIterator.

        Isolated here so the silero/torch dependency is only required when
        vad mode is actually selected.
        """
        try:
            from silero_vad import VADIterator, load_silero_vad
        except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
            raise RuntimeError(
                "mic_mode='vad' requires the silero-vad package. "
                "Install fork extras with: uv sync --extra all"
            ) from exc

        model = load_silero_vad()
        vad_iterator = VADIterator(
            model,
            threshold=cfg.vad_threshold,
            sampling_rate=cfg.sample_rate,
            min_silence_duration_ms=cfg.vad_min_silence_ms,
            speech_pad_ms=cfg.vad_speech_pad_ms,
        )
        return cls(
            vad_iterator,
            sample_rate=cfg.sample_rate,
            speech_pad_ms=cfg.vad_speech_pad_ms,
            min_speech_ms=cfg.vad_min_speech_ms,
            max_utterance_seconds=cfg.max_utterance_seconds,
        )

    @staticmethod
    def chunk_samples_for(sample_rate: int) -> int:
        """silero の窓サイズ。LocalMicrophoneJa がマイクの block_size に使う。"""
        try:
            return _CHUNK_FOR_RATE[sample_rate]
        except KeyError:
            raise ValueError(f"VAD requires 16000 or 8000 Hz, got {sample_rate}") from None

    def feed(self, event: AudioEvent) -> AudioEvent | None:
        """Consume one mic frame (must be exactly one VAD window)."""
        data = np.asarray(event.data, dtype=np.int16).reshape(-1)
        if data.size != self.chunk:
            raise ValueError(
                f"VAD expects {self.chunk}-sample frames, got {data.size}; "
                f"open the mic with block_size={self.chunk} in vad mode"
            )
        return self._process_chunk(data, event)

    def _process_chunk(self, chunk_i16: np.ndarray, event: AudioEvent) -> AudioEvent | None:
        # silero wants float32 in [-1, 1]; keep int16 for the utterance buffer.
        vad_chunk = chunk_i16.astype(np.float32) / 32768.0
        res = self._iter(vad_chunk)

        if res is not None and "start" in res and not self._recording:
            self._recording = True
            self._utt = list(self._preroll)
            self._utt_samples = self._preroll_total
            self._preroll.clear()
            self._preroll_total = 0
            self._append(chunk_i16)
            return None

        if res is not None and "end" in res and self._recording:
            self._append(chunk_i16)
            return self._finalize(event, forced=False)

        if self._recording:
            self._append(chunk_i16)
            if self._utt_samples >= self._max_samples:
                return self._finalize(event, forced=True)
            return None

        # idle: keep a rolling preroll of recent audio.
        self._preroll.append(chunk_i16)
        self._preroll_total += chunk_i16.size
        while self._preroll_total > self._preroll_samples and self._preroll:
            self._preroll_total -= self._preroll.popleft().size
        return None

    def _append(self, chunk_i16: np.ndarray) -> None:
        self._utt.append(chunk_i16)
        self._utt_samples += chunk_i16.size

    def _finalize(self, event: AudioEvent, *, forced: bool) -> AudioEvent | None:
        self._iter.reset_states()
        utt, samples = self._utt, self._utt_samples
        self._recording = False
        self._utt = []
        self._utt_samples = 0
        if not forced and samples < self._min_speech_samples:
            logger.info("VAD: dropping short utterance (%d samples)", samples)
            return None
        if not utt:
            return None
        data = np.concatenate(utt)
        return AudioEvent(
            data=data,
            sample_rate=self._sr,
            timestamp=event.timestamp,
            channels=event.channels,
        )
