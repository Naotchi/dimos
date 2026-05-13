# Copyright 2025-2026 Dimensional Inc.
"""OpenJTalkTTSNode unit tests."""

from __future__ import annotations

import sys
import time
import types
from unittest import mock

import numpy as np
import pytest
from reactivex import Subject


@pytest.fixture(autouse=True)
def _cleanup_pyopenjtalk_modules():
    sys.modules.pop("pyopenjtalk", None)
    sys.modules.pop("dimos.stream.audio.tts.node_open_jtalk", None)
    yield
    sys.modules.pop("pyopenjtalk", None)
    sys.modules.pop("dimos.stream.audio.tts.node_open_jtalk", None)


def _install_fake_pyopenjtalk() -> mock.MagicMock:
    """Install a fake pyopenjtalk module before import."""
    fake = types.ModuleType("pyopenjtalk")
    waveform = np.zeros(4800, dtype=np.float64)
    tts_mock = mock.MagicMock(return_value=(waveform, 48000))
    fake.tts = tts_mock  # type: ignore[attr-defined]
    sys.modules["pyopenjtalk"] = fake
    return tts_mock


def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached within timeout")


def test_consume_text_emits_audio_event() -> None:
    tts_mock = _install_fake_pyopenjtalk()
    from dimos.stream.audio.base import AudioEvent
    from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode

    node = OpenJTalkTTSNode()
    text_subject: Subject = Subject()
    received: list[AudioEvent] = []
    node.emit_audio().subscribe(on_next=received.append)
    node.consume_text(text_subject)

    text_subject.on_next("こんにちは")

    _wait_for(lambda: len(received) == 1)
    event = received[0]
    assert event.sample_rate == 48000
    assert event.channels == 1
    assert isinstance(event.data, np.ndarray)
    tts_mock.assert_called_once_with("こんにちは")

    node.dispose()


def test_emit_text_passes_through_input() -> None:
    _install_fake_pyopenjtalk()
    from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode

    node = OpenJTalkTTSNode()
    text_subject: Subject = Subject()
    spoken: list[str] = []
    node.emit_text().subscribe(on_next=spoken.append)
    node.consume_text(text_subject)

    text_subject.on_next("テスト")

    _wait_for(lambda: spoken == ["テスト"])
    node.dispose()


def test_dispose_stops_worker_thread() -> None:
    _install_fake_pyopenjtalk()
    from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode

    node = OpenJTalkTTSNode()
    text_subject: Subject = Subject()
    node.consume_text(text_subject)
    assert node.processing_thread is not None
    assert node.processing_thread.is_alive()

    node.dispose()

    node.processing_thread.join(timeout=2.0)
    assert not node.processing_thread.is_alive()


def test_synthesis_error_is_logged_and_does_not_kill_worker() -> None:
    fake = types.ModuleType("pyopenjtalk")
    fake.tts = mock.MagicMock(side_effect=RuntimeError("boom"))  # type: ignore[attr-defined]
    sys.modules["pyopenjtalk"] = fake

    from dimos.stream.audio.tts.node_open_jtalk import OpenJTalkTTSNode

    node = OpenJTalkTTSNode()
    text_subject: Subject = Subject()
    node.consume_text(text_subject)
    text_subject.on_next("error case")

    _wait_for(lambda: fake.tts.call_count >= 1)  # type: ignore[attr-defined]
    assert node.processing_thread is not None
    assert node.processing_thread.is_alive()
    node.dispose()
