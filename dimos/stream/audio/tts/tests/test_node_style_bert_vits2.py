# Copyright 2025-2026 Dimensional Inc.
"""StyleBertVits2TTSNode unit tests."""

from __future__ import annotations

import sys
import time
import types
from unittest import mock

import numpy as np
import pytest
from reactivex import Subject

_NODE_MOD = "dimos.stream.audio.tts.node_style_bert_vits2"
_FAKE_MODULES = (
    "torch",
    "huggingface_hub",
    "style_bert_vits2",
    "style_bert_vits2.constants",
    "style_bert_vits2.nlp",
    "style_bert_vits2.tts_model",
)


@pytest.fixture(autouse=True)
def _cleanup_sbv2_modules():
    for m in _FAKE_MODULES + (_NODE_MOD,):
        sys.modules.pop(m, None)
    yield
    for m in _FAKE_MODULES + (_NODE_MOD,):
        sys.modules.pop(m, None)


def _install_fake_sbv2(infer_return=None, infer_side_effect=None) -> mock.MagicMock:
    """Install fake torch / huggingface_hub / style_bert_vits2 modules.

    Returns the ``TTSModel`` instance's ``infer`` mock so tests can assert calls.
    """
    # torch
    torch_mod = types.ModuleType("torch")

    def _cuda_available() -> bool:
        return False

    torch_mod.cuda = types.SimpleNamespace(is_available=_cuda_available)  # type: ignore[attr-defined]
    sys.modules["torch"] = torch_mod

    # huggingface_hub
    hf_mod = types.ModuleType("huggingface_hub")
    hf_mod.hf_hub_download = mock.MagicMock(side_effect=lambda repo, path: f"/tmp/{path}")  # type: ignore[attr-defined]
    sys.modules["huggingface_hub"] = hf_mod

    # style_bert_vits2 package + submodules
    pkg = types.ModuleType("style_bert_vits2")
    sys.modules["style_bert_vits2"] = pkg

    constants = types.ModuleType("style_bert_vits2.constants")

    class _Languages:
        JP = "JP"

    constants.Languages = _Languages  # type: ignore[attr-defined]
    sys.modules["style_bert_vits2.constants"] = constants

    nlp = types.ModuleType("style_bert_vits2.nlp")
    bert_models = types.SimpleNamespace(
        load_model=mock.MagicMock(),
        load_tokenizer=mock.MagicMock(),
    )
    nlp.bert_models = bert_models  # type: ignore[attr-defined]
    sys.modules["style_bert_vits2.nlp"] = nlp

    tts_model_mod = types.ModuleType("style_bert_vits2.tts_model")

    waveform = np.full(4800, 12345, dtype=np.int16)
    if infer_return is None:
        infer_return = (44100, waveform)
    infer_mock = mock.MagicMock(
        return_value=infer_return,
        side_effect=infer_side_effect,
    )

    class _TTSModel:
        def __init__(self, *_, **__) -> None:
            self.hyper_parameters = types.SimpleNamespace(
                data=types.SimpleNamespace(sampling_rate=44100)
            )
            self.infer = infer_mock

    tts_model_mod.TTSModel = _TTSModel  # type: ignore[attr-defined]
    sys.modules["style_bert_vits2.tts_model"] = tts_model_mod

    return infer_mock


def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached within timeout")


def test_consume_text_emits_audio_event() -> None:
    infer_mock = _install_fake_sbv2()
    from dimos.stream.audio.base import AudioEvent
    from dimos.stream.audio.tts.node_style_bert_vits2 import StyleBertVits2TTSNode

    node = StyleBertVits2TTSNode()
    assert node.sample_rate == 44100
    text_subject: Subject = Subject()
    received: list[AudioEvent] = []
    node.emit_audio().subscribe(on_next=received.append)
    node.consume_text(text_subject)

    text_subject.on_next("こんにちは")

    _wait_for(lambda: len(received) == 1)
    event = received[0]
    assert event.sample_rate == 44100
    assert event.channels == 1
    assert isinstance(event.data, np.ndarray)
    assert event.data.dtype == np.int16
    assert event.data[0] == 12345
    infer_mock.assert_called_once()
    assert infer_mock.call_args.kwargs["text"] == "こんにちは"

    node.dispose()


def test_emit_text_passes_through_input() -> None:
    _install_fake_sbv2()
    from dimos.stream.audio.tts.node_style_bert_vits2 import StyleBertVits2TTSNode

    node = StyleBertVits2TTSNode()
    text_subject: Subject = Subject()
    spoken: list[str] = []
    node.emit_text().subscribe(on_next=spoken.append)
    node.consume_text(text_subject)

    text_subject.on_next("テスト")

    _wait_for(lambda: spoken == ["テスト"])
    node.dispose()


def test_dispose_stops_worker_thread() -> None:
    _install_fake_sbv2()
    from dimos.stream.audio.tts.node_style_bert_vits2 import StyleBertVits2TTSNode

    node = StyleBertVits2TTSNode()
    text_subject: Subject = Subject()
    node.consume_text(text_subject)
    assert node.processing_thread is not None
    assert node.processing_thread.is_alive()

    node.dispose()

    node.processing_thread.join(timeout=2.0)
    assert not node.processing_thread.is_alive()


def test_synthesis_error_is_logged_and_does_not_kill_worker() -> None:
    infer_mock = _install_fake_sbv2(infer_side_effect=RuntimeError("boom"))
    from dimos.stream.audio.tts.node_style_bert_vits2 import StyleBertVits2TTSNode

    node = StyleBertVits2TTSNode()
    text_subject: Subject = Subject()
    node.consume_text(text_subject)
    text_subject.on_next("error case")

    _wait_for(lambda: infer_mock.call_count >= 1)
    assert node.processing_thread is not None
    assert node.processing_thread.is_alive()
    node.dispose()
