# Copyright 2025-2026 Dimensional Inc.
"""PyTTSNode voice_lang selection tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock


def _voice(id_: str, name: str, languages: list[bytes]) -> SimpleNamespace:
    return SimpleNamespace(id=id_, name=name, languages=languages)


@mock.patch("dimos.stream.audio.tts.node_pytts.pyttsx3.init")
def test_voice_lang_selects_matching_voice_by_languages(init_mock: mock.MagicMock) -> None:
    engine = mock.MagicMock()
    engine.getProperty.return_value = [
        _voice("en-id", "english", [b"en-US"]),
        _voice("ja-id", "japanese", [b"ja-JP"]),
    ]
    init_mock.return_value = engine

    from dimos.stream.audio.tts.node_pytts import PyTTSNode

    PyTTSNode(voice_lang="ja")

    set_calls = [c for c in engine.setProperty.call_args_list if c.args[0] == "voice"]
    assert set_calls, "voice should be set when a matching voice is found"
    assert set_calls[-1].args[1] == "ja-id"


@mock.patch("dimos.stream.audio.tts.node_pytts.pyttsx3.init")
def test_voice_lang_falls_back_when_no_match(init_mock: mock.MagicMock) -> None:
    engine = mock.MagicMock()
    engine.getProperty.return_value = [
        _voice("en-id", "english", [b"en-US"]),
    ]
    init_mock.return_value = engine

    from dimos.stream.audio.tts.node_pytts import PyTTSNode

    PyTTSNode(voice_lang="ja")

    voice_set_calls = [c for c in engine.setProperty.call_args_list if c.args[0] == "voice"]
    assert voice_set_calls == [], "voice should not be set when no matching voice exists"


@mock.patch("dimos.stream.audio.tts.node_pytts.pyttsx3.init")
def test_voice_lang_none_does_not_touch_voice(init_mock: mock.MagicMock) -> None:
    engine = mock.MagicMock()
    init_mock.return_value = engine

    from dimos.stream.audio.tts.node_pytts import PyTTSNode

    PyTTSNode()

    voice_set_calls = [c for c in engine.setProperty.call_args_list if c.args[0] == "voice"]
    assert voice_set_calls == [], "voice should not be set when voice_lang is None"


@mock.patch("dimos.stream.audio.tts.node_pytts.pyttsx3.init")
def test_voice_lang_matches_via_id_or_name(init_mock: mock.MagicMock) -> None:
    """languages 属性が空でも id/name の言語コードでマッチさせる (mac/win 等で languages 空のケース)."""
    engine = mock.MagicMock()
    engine.getProperty.return_value = [
        _voice("com.apple.voice.compact.en-US.Samantha", "Samantha", []),
        _voice("com.apple.voice.compact.ja-JP.Kyoko", "Kyoko", []),
    ]
    init_mock.return_value = engine

    from dimos.stream.audio.tts.node_pytts import PyTTSNode

    PyTTSNode(voice_lang="ja")

    set_calls = [c for c in engine.setProperty.call_args_list if c.args[0] == "voice"]
    assert set_calls, "voice should be set via id match"
    assert "ja" in set_calls[-1].args[1]
