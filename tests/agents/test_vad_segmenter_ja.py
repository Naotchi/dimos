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


def _seg(it, **kw):
    # テストはプリロール無し・最小長 1 チャンク相当で組む。
    params = dict(sample_rate=16000, speech_pad_ms=0, min_speech_ms=0, max_utterance_seconds=60.0)
    params.update(kw)
    return VadStreamSegmenter(it, **params)


def test_start_then_end_emits_utterance():
    # start → None → None → end の 4 チャンク。
    it = FakeVadIterator([{"start": 0}, None, None, {"end": 100}])
    seg = _seg(it)
    assert seg.feed(_chunk_event()) is None      # start
    assert seg.feed(_chunk_event()) is None      # speaking
    assert seg.feed(_chunk_event()) is None      # speaking
    utt = seg.feed(_chunk_event())               # end → emit
    assert utt is not None
    # 開始チャンク + speaking2 + end チャンク = 4 チャンク分。
    assert utt.data.shape[0] == 512 * 4
    assert it.reset_calls == 1


def test_preroll_prepended_on_start():
    # プリロール 512 サンプル(=1チャンク)。idle 中の 1 チャンクが先頭に付く。
    it = FakeVadIterator([None, {"start": 0}, {"end": 0}])
    seg = _seg(it, speech_pad_ms=32)  # 32ms @16k = 512 サンプル = 1 チャンク
    assert seg.feed(_chunk_event(7)) is None     # idle、プリロールに退避
    assert seg.feed(_chunk_event(8)) is None     # start（プリロール=1ch を先頭に）
    utt = seg.feed(_chunk_event(9))              # end
    assert utt is not None
    # プリロール1ch + startチャンク + endチャンク = 3 チャンク。
    assert utt.data.shape[0] == 512 * 3
    assert utt.data[0] == 7  # 先頭はプリロール由来


def test_short_utterance_dropped():
    # start+end = 2 チャンク = 1024 サンプル。min_speech を 3 チャンク相当
    # (1536 サンプル) にすると、この発話は短すぎて破棄される。
    it = FakeVadIterator([{"start": 0}, {"end": 0}])
    seg = _seg(it, min_speech_ms=96)             # 96ms @16k = 1536 サンプル = 3 チャンク
    assert seg.feed(_chunk_event()) is None      # start (1ch)
    dropped = seg.feed(_chunk_event())           # end, 計 2ch=1024 < 1536 → 破棄
    assert dropped is None
    assert it.reset_calls == 1                   # 破棄でも end 時に reset される


def test_max_utterance_force_flush():
    it = FakeVadIterator([{"start": 0}, None, None, None])
    # 最大長 = 2 チャンク分 (1024 サンプル / 16000 = 0.064s)。
    seg = _seg(it, max_utterance_seconds=512 * 3 / 16000)
    assert seg.feed(_chunk_event()) is None      # start (1ch)
    assert seg.feed(_chunk_event()) is None      # 2ch
    forced = seg.feed(_chunk_event())            # 3ch → max 到達で強制 flush
    assert forced is not None
    assert it.reset_calls == 1
