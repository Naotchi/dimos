"""LocalMicrophoneJa の config / モード分岐テスト。"""
from __future__ import annotations

import time
import threading

import numpy as np

from dimos.agents.local_microphone_ja import LocalMicrophoneJa, LocalMicrophoneJaConfig
from dimos.stream.audio.base import AudioEvent


def test_config_defaults_hold_mode():
    cfg = LocalMicrophoneJaConfig()
    assert cfg.mic_mode == "hold"
    assert cfg.vad_threshold == 0.5
    assert cfg.vad_min_silence_ms == 700
    assert cfg.vad_speech_pad_ms == 300
    assert cfg.vad_min_speech_ms == 200


def test_config_vad_params_have_no_env_seed(monkeypatch):
    # mic_mode は実行場所非依存 → env では上書きされない（profile config 専管）。
    monkeypatch.setenv("DIMOS_MIC_MODE", "vad")
    monkeypatch.setenv("DIMOS_VAD_THRESHOLD", "0.9")
    cfg = LocalMicrophoneJaConfig()
    assert cfg.mic_mode == "hold"
    assert cfg.vad_threshold == 0.5


def test_config_vad_mode_via_explicit_value():
    cfg = LocalMicrophoneJaConfig(mic_mode="vad", vad_threshold=0.7)
    assert cfg.mic_mode == "vad"
    assert cfg.vad_threshold == 0.7


# ---------------------------------------------------------------------------
# _on_audio モード分岐テスト
# ---------------------------------------------------------------------------

class _RecordingOut:
    def __init__(self):
        self.published = []

    def publish(self, value):
        self.published.append(value)


def _make_mic(mic_mode: str) -> LocalMicrophoneJa:
    # Module 経由の生成を避け、_on_audio 分岐に必要な属性だけ用意する。
    mic = LocalMicrophoneJa.__new__(LocalMicrophoneJa)
    mic._lock = threading.Lock()
    mic._buffer = []
    mic._recording = False
    mic._recording_started_at = 0.0
    mic._segmenter = None
    mic.config = LocalMicrophoneJaConfig(mic_mode=mic_mode)
    mic.mic_utterance = _RecordingOut()
    return mic


def _event(value: int = 5) -> AudioEvent:
    return AudioEvent(
        data=np.full(512, value, dtype=np.int16),
        sample_rate=16000,
        timestamp=time.time(),
        channels=1,
    )


class _FakeSegmenter:
    """3 回目の feed で発話を返す偽 segmenter。"""

    def __init__(self):
        self.calls = 0

    def feed(self, event):
        self.calls += 1
        if self.calls == 3:
            return AudioEvent(
                data=np.ones(2048, dtype=np.int16),
                sample_rate=16000,
                timestamp=event.timestamp,
                channels=1,
            )
        return None


def test_on_audio_vad_mode_publishes_segmenter_output():
    mic = _make_mic("vad")
    mic._segmenter = _FakeSegmenter()
    mic._on_audio(_event())
    mic._on_audio(_event())
    assert mic.mic_utterance.published == []   # まだ発話確定せず
    mic._on_audio(_event())                    # 3 回目で確定
    assert len(mic.mic_utterance.published) == 1
    assert mic.mic_utterance.published[0].data.shape[0] == 2048


def test_on_audio_hold_mode_ignores_segmenter_path():
    mic = _make_mic("hold")  # _segmenter は None のまま
    mic._recording = False
    mic._on_audio(_event())                    # hold 非録音中は何も publish しない
    assert mic.mic_utterance.published == []
