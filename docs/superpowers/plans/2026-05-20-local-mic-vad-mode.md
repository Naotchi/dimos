# ローカルマイク VAD / hold モード切り替え Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `unitree-go2-agentic-local-tts` のローカルマイク入力を、現状の F9 押下中のみ録音する hold モードに加え、キー操作不要の VAD モード（silero-vad で自動的に発話を区切る）へプロファイルから切り替えられるようにする。

**Architecture:** silero `VADIterator` をラップした新規 fork 固有クラス `VadStreamSegmenter`（`feed(AudioEvent) -> AudioEvent | None` のみ公開）を作り、`LocalMicrophoneJa` が `mic_mode` 設定に応じて hold（既存 `mic_gate` 駆動）か vad（segmenter 駆動）を選ぶ。`PttKeyboard` は無変更でバンドル常駐、vad モードでは `mic_gate` を購読しないだけ。

**Tech Stack:** Python, pydantic `ModuleConfig`, numpy, silero-vad（pip 同梱モデル）, pytest, reactivex。

設計根拠: `docs/superpowers/specs/2026-05-20-local-mic-vad-mode-design.md`

---

## File Structure

- **Create** `dimos/agents/vad_segmenter_ja.py` — `VadStreamSegmenter`。1 フレーム=1 VAD 窓前提の発話組み立て（プリロール・最小発話長・最大長打ち切り）を内包。silero に依存するのは `from_config` のみ。
- **Modify** `dimos/agents/local_microphone_ja.py` — `LocalMicrophoneJaConfig` に振る舞いフィールド追加、`LocalMicrophoneJa` の `start`/`_on_audio`/`stop` に vad モード分岐を追加。
- **Modify** `pyproject.toml` — Audio extra に `silero-vad` を 1 行追加。
- **Create** `tests/agents/test_vad_segmenter_ja.py` — segmenter の単体テスト（VADIterator はモック）。
- **Create** `tests/agents/test_local_microphone_ja.py` — モード分岐の単体テスト（segmenter はモック）。

実行は worktree 方針に従い **`python -m pytest`**（bare `pytest` 不可）。

---

## Task 1: Config に mic_mode と VAD パラメータを追加

**Files:**
- Modify: `dimos/agents/local_microphone_ja.py`（`LocalMicrophoneJaConfig`）
- Test: `tests/agents/test_local_microphone_ja.py`

mic_mode と VAD パラメータは**実行場所非依存の振る舞い設定**なので env seed を付けず、純粋な `Field(default=...)` にする（既存の `device_index` 等はマシン依存なので env seed 維持）。

- [ ] **Step 1: 失敗するテストを書く**

`tests/agents/test_local_microphone_ja.py` を新規作成:

```python
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
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/agents/test_local_microphone_ja.py -v`
Expected: FAIL（`mic_mode` 属性が無く `ValidationError` か `AttributeError`）

- [ ] **Step 3: Config にフィールドを追加**

`dimos/agents/local_microphone_ja.py` の `LocalMicrophoneJaConfig` 末尾（`max_utterance_seconds` の後）に追記。ファイル先頭の `from typing import Any` を `from typing import Any, Literal` に変更:

```python
    # --- VAD / hold モード切り替え（実行場所非依存 → env seed なし、profile 専管）---
    mic_mode: Literal["hold", "vad"] = Field(default="hold")
    vad_threshold: float = Field(default=0.5)
    vad_min_silence_ms: int = Field(default=700)
    vad_speech_pad_ms: int = Field(default=300)
    vad_min_speech_ms: int = Field(default=200)
```

- [ ] **Step 4: テストを実行して合格を確認**

Run: `python -m pytest tests/agents/test_local_microphone_ja.py -v`
Expected: PASS（3 件）

- [ ] **Step 5: コミット**

```bash
git add dimos/agents/local_microphone_ja.py tests/agents/test_local_microphone_ja.py
git commit -m "feat(local-mic): add mic_mode + VAD params to config"
```

---

## Task 2: VadStreamSegmenter の骨格（1フレーム=1窓の feed + 検証）

**Files:**
- Create: `dimos/agents/vad_segmenter_ja.py`
- Test: `tests/agents/test_vad_segmenter_ja.py`

vad モードはマイクを silero の窓幅（16kHz=512）で開くので、`feed` は 1 フレーム =
1 窓を前提にする。本タスクでは VADIterator が常に `None`（無音）を返す前提で、
フレームがそのまま 1 回 food されること、窓幅以外のフレームを `ValueError` で弾く
こと、`chunk_samples_for` のマッピングだけを検証する。

- [ ] **Step 1: 失敗するテストを書く**

`tests/agents/test_vad_segmenter_ja.py` を新規作成:

```python
"""VadStreamSegmenter の単体テスト（silero VADIterator はモック）。"""
from __future__ import annotations

import time

import numpy as np

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
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/agents/test_vad_segmenter_ja.py -v`
Expected: FAIL（`ModuleNotFoundError: dimos.agents.vad_segmenter_ja`）

- [ ] **Step 3: VadStreamSegmenter の骨格を実装**

`dimos/agents/vad_segmenter_ja.py` を新規作成:

```python
#!/usr/bin/env python3
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Streaming VAD utterance segmenter for the local Japanese mic path.

Wraps silero ``VADIterator``. :class:`LocalMicrophoneJa` opens the mic at the
silero window width (512 samples @ 16 kHz / 256 @ 8 kHz) in vad mode, so each
mic frame is exactly one VAD window — no re-chunking. Frames are fed one at a
time and assembled into a single utterance ``AudioEvent`` on the falling edge
of speech. Used by :class:`LocalMicrophoneJa` in vad mode.

The silero dependency is isolated in :meth:`from_config`; the core
:meth:`feed` logic takes an injected iterator so it is unit-testable with a
fake (no model, no torch).
"""
from __future__ import annotations

from collections import deque
from typing import Any

import numpy as np

from dimos.stream.audio.base import AudioEvent
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_CHUNK_FOR_RATE = {16000: 512, 8000: 256}


class VadStreamSegmenter:
    """Assemble utterances from a streaming mic using an injected VADIterator."""

    def __init__(
        self,
        vad_iterator: Any,
        *,
        sample_rate: int = 16000,
        speech_pad_ms: int = 300,
        min_speech_ms: int = 200,
        max_utterance_seconds: float = 60.0,
    ) -> None:
        if sample_rate not in _CHUNK_FOR_RATE:
            raise ValueError(
                f"VAD requires 16000 or 8000 Hz, got {sample_rate}"
            )
        self._iter = vad_iterator
        self._sr = sample_rate
        self.chunk = _CHUNK_FOR_RATE[sample_rate]
        self._preroll_samples = int(speech_pad_ms * sample_rate / 1000)
        self._min_speech_samples = int(min_speech_ms * sample_rate / 1000)
        self._max_samples = int(max_utterance_seconds * sample_rate)

        self._preroll: deque[np.ndarray] = deque()
        self._preroll_total = 0
        self._recording = False
        self._utt: list[np.ndarray] = []
        self._utt_samples = 0

    @staticmethod
    def chunk_samples_for(sample_rate: int) -> int:
        """silero の窓サイズ。LocalMicrophoneJa がマイクの block_size に使う。"""
        try:
            return _CHUNK_FOR_RATE[sample_rate]
        except KeyError:
            raise ValueError(f"VAD requires 16000 or 8000 Hz, got {sample_rate}") from None

    def feed(self, event: AudioEvent) -> AudioEvent | None:
        """Consume one mic frame (must be exactly one VAD window)."""
        data = np.asarray(event.data, dtype=np.int16).reshape(-1)
        if data.size != self.chunk:
            raise ValueError(
                f"VAD expects {self.chunk}-sample frames, got {data.size}; "
                f"open the mic with block_size={self.chunk} in vad mode"
            )
        return self._process_chunk(data, event)

    def _process_chunk(self, chunk_i16: np.ndarray, event: AudioEvent) -> AudioEvent | None:
        # silero wants float32 in [-1, 1]; keep int16 for the utterance buffer.
        vad_chunk = chunk_i16.astype(np.float32) / 32768.0
        res = self._iter(vad_chunk)
        # placeholder: full state machine added in Task 3
        return None
```

- [ ] **Step 4: テストを実行して合格を確認**

Run: `python -m pytest tests/agents/test_vad_segmenter_ja.py -v`
Expected: PASS（3 件）

- [ ] **Step 5: コミット**

```bash
git add dimos/agents/vad_segmenter_ja.py tests/agents/test_vad_segmenter_ja.py
git commit -m "feat(vad): VadStreamSegmenter feed skeleton (one frame = one window)"
```

---

## Task 3: VadStreamSegmenter の発話組み立て（start/end/プリロール/最小長/最大長）

**Files:**
- Modify: `dimos/agents/vad_segmenter_ja.py`（`_process_chunk` を状態機械化、ヘルパ追加）
- Test: `tests/agents/test_vad_segmenter_ja.py`（追記）

- [ ] **Step 1: 失敗するテストを追記**

`tests/agents/test_vad_segmenter_ja.py` に追記（`_chunk_event` は Task 2 で定義済み）:

```python
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
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/agents/test_vad_segmenter_ja.py -v`
Expected: FAIL（`_process_chunk` が常に None を返すため emit 系が全滅）

- [ ] **Step 3: 状態機械を実装**

`dimos/agents/vad_segmenter_ja.py` の `_process_chunk` を置き換え、ヘルパを追加:

```python
    def _process_chunk(self, chunk_i16: np.ndarray, event: AudioEvent) -> AudioEvent | None:
        # silero wants float32 in [-1, 1]; keep int16 for the utterance buffer.
        vad_chunk = chunk_i16.astype(np.float32) / 32768.0
        res = self._iter(vad_chunk)

        if res is not None and "start" in res and not self._recording:
            self._recording = True
            self._utt = list(self._preroll)
            self._utt_samples = self._preroll_total
            self._preroll.clear()
            self._preroll_total = 0
            self._append(chunk_i16)
            return None

        if res is not None and "end" in res and self._recording:
            self._append(chunk_i16)
            return self._finalize(event, forced=False)

        if self._recording:
            self._append(chunk_i16)
            if self._utt_samples >= self._max_samples:
                return self._finalize(event, forced=True)
            return None

        # idle: keep a rolling preroll of recent audio.
        self._preroll.append(chunk_i16)
        self._preroll_total += chunk_i16.size
        while self._preroll_total > self._preroll_samples and self._preroll:
            self._preroll_total -= self._preroll.popleft().size
        return None

    def _append(self, chunk_i16: np.ndarray) -> None:
        self._utt.append(chunk_i16)
        self._utt_samples += chunk_i16.size

    def _finalize(self, event: AudioEvent, *, forced: bool) -> AudioEvent | None:
        self._iter.reset_states()
        utt, samples = self._utt, self._utt_samples
        self._recording = False
        self._utt = []
        self._utt_samples = 0
        if not forced and samples < self._min_speech_samples:
            logger.info("VAD: dropping short utterance (%d samples)", samples)
            return None
        if not utt:
            return None
        data = np.concatenate(utt)
        return AudioEvent(
            data=data,
            sample_rate=self._sr,
            timestamp=event.timestamp,
            channels=event.channels,
        )
```

注: `start` チャンク自身も発話に含める設計（上記 `_append` 呼び出し）。`test_start_then_end_emits_utterance` の期待値 4 チャンクと整合（start+2+end）。

- [ ] **Step 4: テストを実行して合格を確認**

Run: `python -m pytest tests/agents/test_vad_segmenter_ja.py -v`
Expected: PASS（Task 2 の 3 件 + 本タスク 4 件 = 7 件）

- [ ] **Step 5: コミット**

```bash
git add dimos/agents/vad_segmenter_ja.py tests/agents/test_vad_segmenter_ja.py
git commit -m "feat(vad): utterance assembly with preroll/min-speech/max-length"
```

---

## Task 4: from_config（silero ロード）と未インストール時の明示エラー

**Files:**
- Modify: `dimos/agents/vad_segmenter_ja.py`（`from_config` classmethod 追加）
- Test: `tests/agents/test_vad_segmenter_ja.py`（追記）

- [ ] **Step 1: 失敗するテストを追記**

```python
import builtins

import pytest


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
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/agents/test_vad_segmenter_ja.py -k from_config -v`
Expected: FAIL（`from_config` 未定義 → `AttributeError`）

- [ ] **Step 3: from_config を実装**

`VadStreamSegmenter` に classmethod を追加（`from_config` 内で import するので silero 未導入でも import 時には壊れない）:

```python
    @classmethod
    def from_config(cls, cfg: Any) -> "VadStreamSegmenter":
        """Build a segmenter backed by a real silero VADIterator.

        Isolated here so the silero/torch dependency is only required when
        vad mode is actually selected.
        """
        try:
            from silero_vad import VADIterator, load_silero_vad
        except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
            raise RuntimeError(
                "mic_mode='vad' requires the silero-vad package. "
                "Install fork extras with: uv sync --extra all"
            ) from exc

        model = load_silero_vad()
        vad_iterator = VADIterator(
            model,
            threshold=cfg.vad_threshold,
            sampling_rate=cfg.sample_rate,
            min_silence_duration_ms=cfg.vad_min_silence_ms,
            speech_pad_ms=cfg.vad_speech_pad_ms,
        )
        return cls(
            vad_iterator,
            sample_rate=cfg.sample_rate,
            speech_pad_ms=cfg.vad_speech_pad_ms,
            min_speech_ms=cfg.vad_min_speech_ms,
            max_utterance_seconds=cfg.max_utterance_seconds,
        )
```

- [ ] **Step 4: テストを実行**

Run: `python -m pytest tests/agents/test_vad_segmenter_ja.py -k from_config -v`
Expected: missing-silero テストは PASS。real テストは silero 未導入なら SKIP（Task 6 後に PASS）。

- [ ] **Step 5: コミット**

```bash
git add dimos/agents/vad_segmenter_ja.py tests/agents/test_vad_segmenter_ja.py
git commit -m "feat(vad): VadStreamSegmenter.from_config with actionable silero error"
```

---

## Task 5: LocalMicrophoneJa にモード分岐を組み込む

**Files:**
- Modify: `dimos/agents/local_microphone_ja.py`（`__init__` / `start` / `_on_audio` / `stop`）
- Test: `tests/agents/test_local_microphone_ja.py`（追記）

`start()` は実機マイク（`SounddeviceAudioSource`）を開くため単体テストしにくい。よって `_on_audio` の分岐をテスト対象にし、segmenter を直接差し込んで検証する。

- [ ] **Step 1: 失敗するテストを追記**

`tests/agents/test_local_microphone_ja.py` に追記:

```python
import time

import numpy as np

from dimos.agents.local_microphone_ja import LocalMicrophoneJa
from dimos.stream.audio.base import AudioEvent


class _RecordingOut:
    def __init__(self):
        self.published = []

    def publish(self, value):
        self.published.append(value)


def _make_mic(mic_mode: str) -> LocalMicrophoneJa:
    # Module 経由の生成を避け、_on_audio 分岐に必要な属性だけ用意する。
    mic = LocalMicrophoneJa.__new__(LocalMicrophoneJa)
    import threading

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
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/agents/test_local_microphone_ja.py -k on_audio -v`
Expected: FAIL（`_segmenter` 属性が `__init__` に無い／`_on_audio` に vad 分岐が無い）

- [ ] **Step 3: LocalMicrophoneJa を変更**

3-a. import に `VadStreamSegmenter` を追加（`local_microphone_ja.py` の import 群へ）:

```python
from dimos.agents.vad_segmenter_ja import VadStreamSegmenter
```

3-b. `__init__` の末尾（`self._recording_started_at = 0.0` の後）に追加:

```python
        self._segmenter: VadStreamSegmenter | None = None
```

3-c. `start()` のマイク構築〜購読部分を分岐に変更。vad モードはマイクを silero
の窓幅で開くので、先に segmenter を構築して `block_size` を決める。既存:

```python
        self._mic = SounddeviceAudioSource(
            device_index=cfg.device_index,
            sample_rate=cfg.sample_rate,
            block_size=cfg.block_size,
        )
        self._mic_unsub = self._mic.emit_audio().subscribe(on_next=self._on_audio)
        self._gate_unsub = self.mic_gate.subscribe(self._on_gate)
        logger.info(
            "LocalMicrophoneJa started (device=%s, sr=%d Hz, block=%d)",
            cfg.device_index,
            cfg.sample_rate,
            cfg.block_size,
        )
```

を次に置き換え:

```python
        if cfg.mic_mode == "vad":
            # silero ロード後、その窓幅でマイクを開く（1 フレーム = 1 VAD 窓）。
            self._segmenter = VadStreamSegmenter.from_config(cfg)
            block_size = self._segmenter.chunk
        else:
            block_size = cfg.block_size
        self._mic = SounddeviceAudioSource(
            device_index=cfg.device_index,
            sample_rate=cfg.sample_rate,
            block_size=block_size,
        )
        self._mic_unsub = self._mic.emit_audio().subscribe(on_next=self._on_audio)
        if cfg.mic_mode != "vad":
            self._gate_unsub = self.mic_gate.subscribe(self._on_gate)
        logger.info(
            "LocalMicrophoneJa started in %s mode (device=%s, sr=%d Hz, block=%d)",
            cfg.mic_mode,
            cfg.device_index,
            cfg.sample_rate,
            block_size,
        )
```

3-d. `_on_audio` の先頭に vad 分岐を追加。既存の `def _on_audio(self, event: AudioEvent) -> None:` 本体の冒頭へ:

```python
    def _on_audio(self, event: AudioEvent) -> None:
        if self._segmenter is not None:
            utterance = self._segmenter.feed(event)
            if utterance is not None and utterance.data.shape[0] > 0:
                logger.info(
                    "VAD: emitting utterance (%d samples)", utterance.data.shape[0]
                )
                self.mic_utterance.publish(utterance)
            return
        # --- hold モード（既存ロジック）---
        with self._lock:
            ...
```

（`with self._lock:` 以降の既存行はそのまま残す。）

3-e. `stop()` の `with self._lock:` ブロックで segmenter も落とす。既存の `self._buffer.clear()` の隣に追加:

```python
            self._buffer.clear()
            self._recording = False
        self._segmenter = None
```

- [ ] **Step 4: テストを実行して合格を確認**

Run: `python -m pytest tests/agents/test_local_microphone_ja.py -v`
Expected: PASS（Task 1 の 3 件 + 本タスク 2 件 = 5 件）

- [ ] **Step 5: コミット**

```bash
git add dimos/agents/local_microphone_ja.py tests/agents/test_local_microphone_ja.py
git commit -m "feat(local-mic): wire vad mode into LocalMicrophoneJa"
```

---

## Task 6: silero-vad 依存の追加と解決

**Files:**
- Modify: `pyproject.toml`（Audio extra）

- [ ] **Step 1: 依存を追加**

`pyproject.toml` の Audio ブロック、`"faster-whisper>=1.0.0",` の直後に 1 行追加:

```toml
    "faster-whisper>=1.0.0",
    "silero-vad>=5.1",
```

- [ ] **Step 2: 依存を解決**

Run: `uv sync --extra all`
Expected: エラーなく完了し silero-vad が入る（`--all-extras` は使わない）。

- [ ] **Step 3: silero ロードを実地確認**

Run: `python -c "from silero_vad import load_silero_vad, VADIterator; load_silero_vad(); print('ok')"`
Expected: `ok`（モデルは pip 同梱、ネットワーク不要）

- [ ] **Step 4: real segmenter テストが PASS することを確認**

Run: `python -m pytest tests/agents/test_vad_segmenter_ja.py -v`
Expected: 全 PASS（Task 4 の `test_from_config_builds_real_segmenter` が SKIP → PASS に）

- [ ] **Step 5: コミット**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add silero-vad dependency for local-mic vad mode"
```

---

## Task 7: 全体回帰と vad モード有効化手順の記録

**Files:**
- Modify: `dimos/agents/local_microphone_ja.py` のモジュール docstring（有効化方法を追記）

- [ ] **Step 1: 既存スイートの回帰確認**

Run: `python -m pytest tests/agents/ -v`
Expected: 既存 PTT テスト（`tests/agents/realtime/test_ptt_keyboard.py`）含め全 PASS。hold モードの挙動は不変。

- [ ] **Step 2: 有効化手順を docstring に追記**

`local_microphone_ja.py` のモジュール docstring 末尾に追記:

```python
"""
...
vad モード有効化: プロファイルの config.json に
``"localmicrophoneja": {"mic_mode": "vad"}`` を書く（env では設定しない）。
hold（既定）は従来通り PttKeyboard の F9 押下中のみ録音。
"""
```

- [ ] **Step 3: コミット**

```bash
git add dimos/agents/local_microphone_ja.py
git commit -m "docs(local-mic): document vad mode activation via profile"
```

---

## Self-Review メモ

- **Spec coverage**: mic_mode 切替(Task1,5) / VADIterator ラップ(Task2-4) / プリロール・最小長・最大長(Task3) / silero 依存隔離(Task4,6) / tts_idle 不採用（実装しない＝対応不要）/ PttKeyboard 無変更（触らない）/ env seed 無し(Task1) を網羅。
- **型整合**: `VadStreamSegmenter.feed(AudioEvent) -> AudioEvent | None`、`from_config(cfg)`、`LocalMicrophoneJa._segmenter` の名称は Task 2/4/5 で一貫。
- **オプション B 採用**: vad モードはマイクを silero 窓幅（512@16k）で開くため
  segmenter 側の再チャンク／キャリーバッファは不要。`feed` は 1 フレーム = 1 窓を
  前提に、窓幅以外を `ValueError` で弾く（block_size 設定ミスを早期検出）。`block_size`
  は PortAudio のソフトウェア設定でハードウェア非依存（経緯: upstream PR #151 の
  ファイル新規作成時からの汎用デフォルト 1024）。
