# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Verify Sbv2ParamsConfig honors `explicit > env seed > default`."""

from __future__ import annotations

import pytest

from dimos.agents.skills.speak_skill_ja import (
    AssistantSpeechNodeJaConfig,
    Sbv2ParamsConfig,
)


_SBV2_ENV_VARS = (
    "DIMOS_SBV2_SPEAKER_ID",
    "DIMOS_SBV2_STYLE",
    "DIMOS_SBV2_STYLE_WEIGHT",
    "DIMOS_SBV2_SDP_RATIO",
    "DIMOS_SBV2_NOISE",
    "DIMOS_SBV2_NOISE_W",
    "DIMOS_SBV2_LENGTH",
    "DIMOS_SBV2_PITCH_SCALE",
    "DIMOS_SBV2_INTONATION_SCALE",
)


def _clear_sbv2_env(monkeypatch):
    for k in _SBV2_ENV_VARS:
        monkeypatch.delenv(k, raising=False)


def test_defaults(monkeypatch):
    _clear_sbv2_env(monkeypatch)
    cfg = Sbv2ParamsConfig()
    assert cfg.speaker_id == 0
    assert cfg.style == "Neutral"
    assert cfg.style_weight == pytest.approx(1.0)
    assert cfg.sdp_ratio == pytest.approx(0.15)
    assert cfg.noise == pytest.approx(0.4)
    assert cfg.noise_w == pytest.approx(0.6)
    assert cfg.length == pytest.approx(1.1)
    assert cfg.pitch_scale == pytest.approx(1.08)
    assert cfg.intonation_scale == pytest.approx(0.85)


def test_env_seed(monkeypatch):
    monkeypatch.setenv("DIMOS_SBV2_SPEAKER_ID", "3")
    monkeypatch.setenv("DIMOS_SBV2_STYLE", "Angry")
    monkeypatch.setenv("DIMOS_SBV2_LENGTH", "1.5")
    cfg = Sbv2ParamsConfig()
    assert cfg.speaker_id == 3
    assert cfg.style == "Angry"
    assert cfg.length == pytest.approx(1.5)


def test_explicit_beats_env(monkeypatch):
    monkeypatch.setenv("DIMOS_SBV2_SPEAKER_ID", "3")
    cfg = Sbv2ParamsConfig(speaker_id=7)
    assert cfg.speaker_id == 7


def test_nested_in_assistant_speech_config(monkeypatch):
    _clear_sbv2_env(monkeypatch)
    cfg = AssistantSpeechNodeJaConfig(
        impl="sbv2",
        sbv2={"speaker_id": 2, "style": "Happy"},
    )
    assert cfg.sbv2.speaker_id == 2
    assert cfg.sbv2.style == "Happy"
    assert cfg.sbv2.length == pytest.approx(1.1)  # untouched default
