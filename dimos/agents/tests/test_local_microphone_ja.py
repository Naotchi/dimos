"""Tests for the LocalMicrophoneJa wav-injection helper."""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import numpy as np
import pytest

from dimos.agents.local_microphone_ja import _load_wav_as_audio_event


def _write_pcm16_wav(path: Path, samples: list[int], sample_rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # int16
        w.setframerate(sample_rate)
        w.writeframes(b"".join(struct.pack("<h", s) for s in samples))


def test_load_wav_returns_audio_event_with_int16_data(tmp_path: Path) -> None:
    path = tmp_path / "fx.wav"
    samples = [0, 16384, -16384, 0, 0]
    _write_pcm16_wav(path, samples, sample_rate=16000)

    ev = _load_wav_as_audio_event(str(path))

    assert ev.sample_rate == 16000
    assert ev.channels == 1
    assert ev.data.dtype == np.int16
    assert ev.data.shape == (5,)
    assert list(ev.data.tolist()) == samples


def test_load_wav_preserves_non_default_sample_rate(tmp_path: Path) -> None:
    path = tmp_path / "fx2.wav"
    _write_pcm16_wav(path, [1, 2, 3], sample_rate=8000)
    ev = _load_wav_as_audio_event(str(path))
    assert ev.sample_rate == 8000


def test_load_wav_rejects_stereo(tmp_path: Path) -> None:
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00\x00\x00")
    with pytest.raises(ValueError, match="mono"):
        _load_wav_as_audio_event(str(path))
