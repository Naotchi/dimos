# Copyright 2025-2026 Dimensional Inc.
"""SpeakSkill env-driven TTS selection tests."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from dimos.agents.skills.speak_skill import SpeakSkill
from dimos.stream.audio.tts.node_openai import OpenAITTSNode
from dimos.stream.audio.tts.node_pytts import PyTTSNode


def _make_skill() -> SpeakSkill:
    return SpeakSkill()


@mock.patch.dict(os.environ, {"DIMOS_TTS": "pyttsx3"}, clear=False)
@mock.patch("dimos.agents.skills.speak_skill.PyTTSNode")
def test_start_pyttsx3_uses_pytts_node(pytts_cls: mock.MagicMock) -> None:
    skill = _make_skill()
    pytts_cls.return_value = mock.MagicMock(spec=PyTTSNode)
    try:
        skill.start()
        pytts_cls.assert_called_once()
        assert skill._audio_output is None
    finally:
        skill.stop()


@mock.patch.dict(os.environ, {"DIMOS_TTS": "openai"}, clear=False)
@mock.patch("dimos.agents.skills.speak_skill.SounddeviceAudioOutput")
@mock.patch("dimos.agents.skills.speak_skill.OpenAITTSNode")
def test_start_openai_uses_openai_node(openai_cls, sd_cls) -> None:
    skill = _make_skill()
    openai_cls.return_value = mock.MagicMock(spec=OpenAITTSNode)
    sd_cls.return_value = mock.MagicMock()
    try:
        skill.start()
        openai_cls.assert_called_once()
        sd_cls.assert_called_once_with(sample_rate=24000)
    finally:
        skill.stop()


@mock.patch.dict(os.environ, {"DIMOS_TTS": "bogus"}, clear=False)
def test_start_invalid_env_raises() -> None:
    skill = _make_skill()
    try:
        with pytest.raises(ValueError, match="DIMOS_TTS"):
            skill.start()
    finally:
        skill.stop()


@mock.patch.dict(os.environ, {}, clear=True)
@mock.patch("dimos.agents.skills.speak_skill.PyTTSNode")
def test_start_default_is_pyttsx3(pytts_cls) -> None:
    skill = _make_skill()
    pytts_cls.return_value = mock.MagicMock(spec=PyTTSNode)
    try:
        skill.start()
        pytts_cls.assert_called_once()
    finally:
        skill.stop()


@mock.patch.dict(os.environ, {"DIMOS_TTS": "pyttsx3"}, clear=False)
@mock.patch("dimos.agents.skills.speak_skill.PyTTSNode")
def test_start_pyttsx3_forwards_voice_lang(pytts_cls: mock.MagicMock) -> None:
    pytts_cls.return_value = mock.MagicMock(spec=PyTTSNode)
    skill = SpeakSkill(voice_lang="ja")
    try:
        skill.start()
        pytts_cls.assert_called_once_with(voice_lang="ja")
    finally:
        skill.stop()


@mock.patch.dict(os.environ, {"DIMOS_TTS": "pyttsx3"}, clear=False)
@mock.patch("dimos.agents.skills.speak_skill.PyTTSNode")
def test_start_pyttsx3_default_voice_lang_is_none(pytts_cls: mock.MagicMock) -> None:
    pytts_cls.return_value = mock.MagicMock(spec=PyTTSNode)
    skill = SpeakSkill()
    try:
        skill.start()
        pytts_cls.assert_called_once_with(voice_lang=None)
    finally:
        skill.stop()


@mock.patch.dict(os.environ, {"DIMOS_TTS": "open_jtalk"}, clear=False)
@mock.patch("dimos.agents.skills.speak_skill.SounddeviceAudioOutput")
def test_start_open_jtalk_uses_open_jtalk_node(sd_cls: mock.MagicMock) -> None:
    sd_cls.return_value = mock.MagicMock()
    with mock.patch(
        "dimos.stream.audio.tts.node_open_jtalk.OpenJTalkTTSNode"
    ) as node_cls:
        node_cls.return_value = mock.MagicMock()
        skill = _make_skill()
        try:
            skill.start()
            node_cls.assert_called_once_with()
            sd_cls.assert_called_once_with(sample_rate=48000)
        finally:
            skill.stop()


@mock.patch.dict(os.environ, {"DIMOS_TTS": "bogus"}, clear=False)
def test_start_invalid_env_message_lists_open_jtalk() -> None:
    skill = _make_skill()
    try:
        with pytest.raises(ValueError, match="open_jtalk"):
            skill.start()
    finally:
        skill.stop()
