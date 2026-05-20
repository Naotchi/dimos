"""VadStreamSegmenter の単体テスト（silero VADIterator はモック）。"""
from __future__ import annotations

import time

import numpy as np

from dimos.agents.vad_segmenter_ja import VadStreamSegmenter
from dimos.stream.audio.base import AudioEvent


class FakeVadIterator:
    """__call__ ごとに script の先頭を返す偽 VADIterator。"""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls = 0
        self.reset_calls = 0

    def __call__(self, chunk, return_seconds=False):
        self.calls += 1
        return self.script.pop(0) if self.script else None

    def reset_states(self):
        self.reset_calls += 1


def _event(n_samples: int) -> AudioEvent:
    return AudioEvent(
        data=np.zeros(n_samples, dtype=np.int16),
        sample_rate=16000,
        timestamp=time.time(),
        channels=1,
    )


def _chunk_event(value: int = 1000) -> AudioEvent:
    """512 サンプル(=16kHz の silero 1 窓)ちょうどのフレーム。"""
    return AudioEvent(
        data=np.full(512, value, dtype=np.int16),
        sample_rate=16000,
        timestamp=time.time(),
        channels=1,
    )


def test_feed_forwards_one_chunk_to_iterator():
    it = FakeVadIterator()  # 常に None
    seg = VadStreamSegmenter(it, sample_rate=16000)
    assert seg.chunk == 512
    assert seg.feed(_chunk_event()) is None
    assert it.calls == 1


def test_feed_rejects_wrong_frame_size():
    import pytest

    it = FakeVadIterator()
    seg = VadStreamSegmenter(it, sample_rate=16000)
    # vad モードはマイクを 512 で開く前提。512 以外が来たら設定ミスとして弾く。
    with pytest.raises(ValueError, match="block_size=512"):
        seg.feed(_event(1024))


def test_chunk_samples_for():
    assert VadStreamSegmenter.chunk_samples_for(16000) == 512
    assert VadStreamSegmenter.chunk_samples_for(8000) == 256
