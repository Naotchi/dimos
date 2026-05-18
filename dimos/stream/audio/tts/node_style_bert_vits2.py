#!/usr/bin/env python3
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Neural Japanese TTS node backed by Style-Bert-VITS2.

Replacement for the older HMM-based ``OpenJTalkTTSNode``. Mirrors its
interface (``AbstractTextConsumer`` + ``AbstractAudioEmitter`` +
``AbstractTextEmitter``) so call sites only need an import swap.

Model files are resolved in this order:

1. If ``DIMOS_SBV2_MODEL_PATH``, ``DIMOS_SBV2_CONFIG_PATH`` and
   ``DIMOS_SBV2_STYLE_PATH`` are all set, use those local paths.
2. Otherwise download the default JVNV-F1 voice from HuggingFace
   (``litagin/style_bert_vits2_jvnv``) via ``huggingface_hub``.

The BERT model (default ``ku-nlp/deberta-v2-large-japanese-char-wwm``) is
also downloaded on first use; override with ``DIMOS_SBV2_BERT_MODEL``.

The node exposes the model's native sample rate as ``self.sample_rate``
so the downstream audio sink (``SounddeviceAudioOutput``) can be opened
at a matching rate without resampling.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

import numpy as np
from reactivex import Observable, Subject

from dimos.stream.audio.base import AbstractAudioEmitter, AudioEvent
from dimos.stream.audio.text.base import AbstractTextConsumer, AbstractTextEmitter
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

# pyopenjtalk-plus の run_frontend が "!" / "！" を `pron="！"` で返すが、
# Style-Bert-VITS2 の text_to_sep_kata は "？" しか特例処理しておらず、
# 全角 ！ が後段の __kata_to_phoneme_list に流れて
# ValueError("Input must be katakana only: ！") で落ちる。
# 入力側で感嘆符を除去して回避する（読み上げ上の損失はほぼなし）。
_EXCLAMATION_RE = re.compile(r"[!！]+")


def _sanitize_for_sbv2(text: str) -> str:
    return _EXCLAMATION_RE.sub("", text)


_DEFAULT_BERT_MODEL = "ku-nlp/deberta-v2-large-japanese-char-wwm"
_DEFAULT_HF_REPO = "litagin/style_bert_vits2_jvnv"
_DEFAULT_MODEL_SUBDIR = "jvnv-F2-jp"
_DEFAULT_MODEL_FILE = "jvnv-F2_e166_s20000.safetensors"


def _resolve_model_files() -> tuple[Path, Path, Path]:
    env_m = os.environ.get("DIMOS_SBV2_MODEL_PATH")
    env_c = os.environ.get("DIMOS_SBV2_CONFIG_PATH")
    env_s = os.environ.get("DIMOS_SBV2_STYLE_PATH")
    if env_m and env_c and env_s:
        return Path(env_m), Path(env_c), Path(env_s)

    from huggingface_hub import hf_hub_download

    repo = os.environ.get("DIMOS_SBV2_HF_REPO", _DEFAULT_HF_REPO)
    subdir = os.environ.get("DIMOS_SBV2_MODEL_SUBDIR", _DEFAULT_MODEL_SUBDIR)
    model_file = os.environ.get("DIMOS_SBV2_MODEL_FILE", _DEFAULT_MODEL_FILE)
    m = Path(hf_hub_download(repo, f"{subdir}/{model_file}"))
    c = Path(hf_hub_download(repo, f"{subdir}/config.json"))
    s = Path(hf_hub_download(repo, f"{subdir}/style_vectors.npy"))
    return m, c, s


class StyleBertVits2TTSNode(AbstractTextConsumer, AbstractAudioEmitter, AbstractTextEmitter):
    """Japanese neural TTS via Style-Bert-VITS2."""

    def __init__(
        self,
        device: str | None = None,
        speaker_id: int = 0,
        style: str = "Neutral",
        style_weight: float = 1.0,
        sdp_ratio: float = 0.15,
        noise: float = 0.4,
        noise_w: float = 0.6,
        length: float = 1.1,
        pitch_scale: float = 1.08,
        intonation_scale: float = 0.85,
    ) -> None:
        import torch
        from style_bert_vits2.constants import Languages
        from style_bert_vits2.nlp import bert_models
        from style_bert_vits2.tts_model import TTSModel

        self.audio_subject: Subject = Subject()  # type: ignore[type-arg]
        self.text_subject: Subject = Subject()  # type: ignore[type-arg]
        self.subscription = None
        self.processing_thread: threading.Thread | None = None
        self.is_running = True
        self.text_queue: list[str] = []
        self.queue_lock = threading.Lock()
        def _envf(name: str, default: float) -> float:
            v = os.environ.get(name)
            return float(v) if v is not None else default

        def _envi(name: str, default: int) -> int:
            v = os.environ.get(name)
            return int(v) if v is not None else default

        self._speaker_id = _envi("DIMOS_SBV2_SPEAKER_ID", speaker_id)
        self._style = os.environ.get("DIMOS_SBV2_STYLE", style)
        self._style_weight = _envf("DIMOS_SBV2_STYLE_WEIGHT", style_weight)
        self._sdp_ratio = _envf("DIMOS_SBV2_SDP_RATIO", sdp_ratio)
        self._noise = _envf("DIMOS_SBV2_NOISE", noise)
        self._noise_w = _envf("DIMOS_SBV2_NOISE_W", noise_w)
        self._length = _envf("DIMOS_SBV2_LENGTH", length)
        self._pitch_scale = _envf("DIMOS_SBV2_PITCH_SCALE", pitch_scale)
        self._intonation_scale = _envf("DIMOS_SBV2_INTONATION_SCALE", intonation_scale)

        bert_name = os.environ.get("DIMOS_SBV2_BERT_MODEL", _DEFAULT_BERT_MODEL)
        logger.info(f"Loading SBV2 BERT model: {bert_name}")
        bert_models.load_model(Languages.JP, bert_name)
        bert_models.load_tokenizer(Languages.JP, bert_name)

        model_path, config_path, style_path = _resolve_model_files()
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info(f"Loading SBV2 model {model_path.name} on device={device}")
        self._model = TTSModel(
            model_path=str(model_path),
            config_path=str(config_path),
            style_vec_path=str(style_path),
            device=device,
        )
        # Expose native sample rate so the audio sink can be opened to match.
        self.sample_rate = int(self._model.hyper_parameters.data.sampling_rate)

    def emit_audio(self) -> Observable:  # type: ignore[type-arg]
        return self.audio_subject

    def emit_text(self) -> Observable:  # type: ignore[type-arg]
        return self.text_subject

    def consume_text(self, text_observable: Observable) -> "AbstractTextConsumer":  # type: ignore[type-arg]
        logger.info("Starting StyleBertVits2TTSNode")
        self.processing_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.processing_thread.start()
        self.subscription = text_observable.subscribe(  # type: ignore[assignment]
            on_next=self._queue_text,
            on_error=lambda e: logger.error(f"Error in StyleBertVits2TTSNode: {e}"),
        )
        return self

    def _queue_text(self, text: str) -> None:
        if not text.strip():
            return
        with self.queue_lock:
            self.text_queue.append(text)

    def _process_queue(self) -> None:
        while self.is_running:
            text_to_process: str | None = None
            with self.queue_lock:
                if self.text_queue:
                    text_to_process = self.text_queue.pop(0)
            if text_to_process is not None:
                self._synthesize_speech(text_to_process)
            else:
                time.sleep(0.05)

    def _synthesize_speech(self, text: str) -> None:
        try:
            sr, waveform = self._model.infer(
                text=_sanitize_for_sbv2(text),
                speaker_id=self._speaker_id,
                style=self._style,
                style_weight=self._style_weight,
                sdp_ratio=self._sdp_ratio,
                noise=self._noise,
                noise_w=self._noise_w,
                length=self._length,
                pitch_scale=self._pitch_scale,
                intonation_scale=self._intonation_scale,
            )
            self.text_subject.on_next(text)
            audio_event = AudioEvent(
                data=np.asarray(waveform, dtype=np.int16),
                sample_rate=int(sr),
                timestamp=time.time(),
                channels=1,
            )
            self.audio_subject.on_next(audio_event)
        except Exception as e:
            logger.error(f"Error synthesizing speech: {e}")

    def dispose(self) -> None:
        logger.info("Disposing StyleBertVits2TTSNode")
        self.is_running = False
        with self.queue_lock:
            self.text_queue.clear()
        if self.processing_thread and self.processing_thread.is_alive():
            self.processing_thread.join(timeout=2.0)
        if self.subscription:
            self.subscription.dispose()
            self.subscription = None
        self.audio_subject.on_completed()
        self.text_subject.on_completed()


__all__ = ["StyleBertVits2TTSNode"]
