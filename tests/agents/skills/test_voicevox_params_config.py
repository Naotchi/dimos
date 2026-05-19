# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Verify VoicevoxParamsConfig honors `explicit > env seed > default`."""

from __future__ import annotations

import pytest

from dimos.agents.skills.speak_skill_ja import (
    AssistantSpeechNodeJaConfig,
    VoicevoxParamsConfig,
)


def test_default_speaker_id_is_74():
    cfg = VoicevoxParamsConfig()
    assert cfg.speaker_id == 74


def test_default_factory_reads_env_seed(monkeypatch):
    monkeypatch.setenv("DIMOS_VOICEVOX_SPEAKER_ID", "99")
    monkeypatch.setenv("DIMOS_VOICEVOX_SPEED_SCALE", "1.5")
    cfg = VoicevoxParamsConfig()
    assert cfg.speaker_id == 99
    assert cfg.speed_scale == pytest.approx(1.5)


def test_explicit_beats_env(monkeypatch):
    monkeypatch.setenv("DIMOS_VOICEVOX_SPEAKER_ID", "99")
    cfg = VoicevoxParamsConfig(speaker_id=42)
    assert cfg.speaker_id == 42


def test_all_params_defaults():
    cfg = VoicevoxParamsConfig()
    assert cfg.speed_scale == 1.0
    assert cfg.pitch_scale == 0.0
    assert cfg.intonation_scale == 1.0
    assert cfg.volume_scale == 1.0


def test_nested_in_assistant_speech_config():
    cfg = AssistantSpeechNodeJaConfig(
        impl="voicevox",
        voicevox={"speaker_id": 5, "speed_scale": 1.3},
    )
    assert cfg.voicevox.speaker_id == 5
    assert cfg.voicevox.speed_scale == pytest.approx(1.3)
    assert cfg.voicevox.pitch_scale == 0.0  # untouched default


def test_node_does_not_read_env(monkeypatch):
    """VoicevoxTTSNode no longer reads DIMOS_VOICEVOX_* env at __init__ time.

    Network probe is mocked so we don't need a running engine.
    """
    import dimos.stream.audio.tts.node_voicevox as vv_mod

    class _FakeResp:
        text = "0.0.0-test"
        def raise_for_status(self): pass

    monkeypatch.setattr(vv_mod.requests, "get", lambda *a, **k: _FakeResp())
    monkeypatch.setenv("DIMOS_VOICEVOX_SPEAKER_ID", "99")
    monkeypatch.setenv("DIMOS_VOICEVOX_SPEED_SCALE", "9.9")

    node = vv_mod.VoicevoxTTSNode(speaker_id=42, speed_scale=1.0)
    assert node._speaker_id == 42  # explicit kwarg, env ignored
    assert node._speed_scale == pytest.approx(1.0)
