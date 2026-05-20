"""LocalMicrophoneJa の config / モード分岐テスト。"""
from __future__ import annotations

from dimos.agents.local_microphone_ja import LocalMicrophoneJaConfig


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
