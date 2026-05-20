"""VadStreamSegmenter の単体テスト（silero VADIterator はモック）。"""
from __future__ import annotations

import builtins
import time

import numpy as np
import pytest

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


def test_feed_converts_float32_mic_input_to_pcm16():
    # SounddeviceAudioSource emits float32 [-1, 1] by default. A naive int16
    # cast would zero those samples and feed silence to the VAD; verify the
    # frame is quantized to PCM16 so silero sees the real signal and the
    # emitted utterance carries non-zero int16 audio.
    captured = []

    class CapturingIter:
        def __init__(self):
            self.script = [{"start": 0}, {"end": 0}]

        def __call__(self, chunk, return_seconds=False):
            captured.append(chunk)
            return self.script.pop(0) if self.script else None

        def reset_states(self):
            pass

    seg = _seg(CapturingIter())

    def _f32_event(value: float) -> AudioEvent:
        return AudioEvent(
            data=np.full(512, value, dtype=np.float32),
            sample_rate=16000,
            timestamp=time.time(),
            channels=1,
        )

    assert seg.feed(_f32_event(0.5)) is None     # start
    utt = seg.feed(_f32_event(0.5))              # end → emit

    # silero received normalized float32 ~0.5 (not floored to 0).
    assert captured[0].dtype == np.float32
    assert abs(float(captured[0][0]) - 0.5) < 0.01
    # utterance is int16 PCM with the signal preserved.
    assert utt is not None
    assert utt.data.dtype == np.int16
    assert int(utt.data[0]) != 0


def test_from_config_raises_clear_error_when_silero_missing(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "silero_vad":
            raise ImportError("No module named 'silero_vad'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    class Cfg:
        vad_threshold = 0.5
        vad_min_silence_ms = 700
        vad_speech_pad_ms = 300
        vad_min_speech_ms = 200
        sample_rate = 16000
        max_utterance_seconds = 60.0

    with pytest.raises(RuntimeError, match="uv sync --extra all"):
        VadStreamSegmenter.from_config(Cfg())


def test_from_config_builds_real_segmenter():
    pytest.importorskip("silero_vad")

    class Cfg:
        vad_threshold = 0.5
        vad_min_silence_ms = 700
        vad_speech_pad_ms = 300
        vad_min_speech_ms = 200
        sample_rate = 16000
        max_utterance_seconds = 60.0

    seg = VadStreamSegmenter.from_config(Cfg())
    assert isinstance(seg, VadStreamSegmenter)
