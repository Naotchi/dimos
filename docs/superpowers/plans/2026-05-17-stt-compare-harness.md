# STT 比較ハーネス Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PTT で 1 発話を録音し、同一 buffer を local faster-whisper と Azure Voice Live (transcription-only) に並列で投げて、2 つの transcript を即座に並べて表示する CLI スクリプトを 1 本作る。

**Architecture:** `scripts/bench_stt_compare.py` 単一ファイル。`MicCapture` (sounddevice + pynput PTT) → `asyncio.gather(LocalStt, VoiceLiveStt)` → stdout に diff ハイライト付きで表示。`dimos/` 配下のソースは触らない（CLAUDE.md 「新規ファイル追加」方針）。

**Tech Stack:** Python 3.12, asyncio, sounddevice (録音), pynput (key listener), faster-whisper, azure-ai-voicelive (transcription session), scipy (resample), difflib (diff)

**Spec:** [2026-05-17-stt-compare-harness-design.md](../specs/2026-05-17-stt-compare-harness-design.md)

**Testing note:** マイクと Azure 課金が必須のため自動テストはスコープ外（spec で明示済み）。各タスクは「手動実行で何を確認するか」を必ず明示する。コミットは各タスク末尾で 1 つずつ刻む。

---

## Task 1: スクリプト骨格 + key listener

**Files:**
- Create: `scripts/bench_stt_compare.py`

- [ ] **Step 1: 骨格ファイルを作成**

```python
#!/usr/bin/env python3
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""STT accuracy compare harness.

Record one PTT utterance, send the same buffer to both local
faster-whisper and Azure Voice Live (transcription-only), and print
both transcripts side by side. Design:
docs/superpowers/specs/2026-05-17-stt-compare-harness-design.md
"""

from __future__ import annotations

import asyncio
import sys

from pynput import keyboard


SAMPLE_RATE = 24_000  # match dimos/agents/realtime/azure_voice_live.py


class PttController:
    """Track SPACE down/up edges and 'q' for quit, exposing asyncio events."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self.space_down = asyncio.Event()
        self.space_up = asyncio.Event()
        self.quit = asyncio.Event()
        self._space_held = False
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()

    def _set(self, event: asyncio.Event) -> None:
        self._loop.call_soon_threadsafe(event.set)

    def _on_press(self, key: object) -> None:
        if key == keyboard.Key.space and not self._space_held:
            self._space_held = True
            self.space_up.clear()
            self._set(self.space_down)
        elif getattr(key, "char", None) == "q":
            self._set(self.quit)

    def _on_release(self, key: object) -> None:
        if key == keyboard.Key.space and self._space_held:
            self._space_held = False
            self.space_down.clear()
            self._set(self.space_up)


async def amain() -> int:
    loop = asyncio.get_running_loop()
    ptt = PttController(loop)
    ptt.start()
    print("[SPACE] 録音 / [q] 終了", flush=True)
    turn = 0
    try:
        while not ptt.quit.is_set():
            done, _ = await asyncio.wait(
                [asyncio.create_task(ptt.space_down.wait()),
                 asyncio.create_task(ptt.quit.wait())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if ptt.quit.is_set():
                break
            turn += 1
            print(f"\n─── turn {turn} ───────────────────────────────", flush=True)
            print("(recording... release SPACE to stop)", flush=True)
            await ptt.space_up.wait()
            print("(stub: would now transcribe)", flush=True)
    finally:
        ptt.stop()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
```

- [ ] **Step 2: 手動 smoke test**

Run: `python scripts/bench_stt_compare.py`
Expected: 起動メッセージが出る。SPACE を押す → "recording..." 表示。離す → "(stub: would now transcribe)" 表示。`q` → 即終了。

- [ ] **Step 3: Commit**

```bash
git add scripts/bench_stt_compare.py
git commit -m "feat(bench): add STT compare harness skeleton with PTT controller"
```

---

## Task 2: MicCapture — PTT 中の PCM を bytearray に蓄積

**Files:**
- Modify: `scripts/bench_stt_compare.py`

- [ ] **Step 1: MicCapture クラスを追加**

`PttController` クラスの直後に追加:

```python
import threading

import numpy as np
import sounddevice as sd  # type: ignore[import-untyped]


class MicCapture:
    """Buffer 16-bit mono PCM into a bytearray between PTT down/up.

    Lifetime: open one InputStream up front (avoiding device open/close
    latency every turn), gate writes via `_recording` flag.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        self._sample_rate = sample_rate
        self._buf = bytearray()
        self._recording = False
        self._lock = threading.Lock()
        self._stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocksize=int(sample_rate * 0.02),  # 20ms
            callback=self._callback,
        )

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        self._stream.stop()
        self._stream.close()

    def begin(self) -> None:
        with self._lock:
            self._buf = bytearray()
            self._recording = True

    def end(self) -> bytes:
        with self._lock:
            self._recording = False
            return bytes(self._buf)

    def _callback(
        self, indata: np.ndarray, frames: int, _t: object, _s: object
    ) -> None:
        with self._lock:
            if self._recording:
                self._buf.extend(indata.tobytes())
```

- [ ] **Step 2: amain でマイクを実際に使う**

`amain` を次のように差し替える:

```python
async def amain() -> int:
    loop = asyncio.get_running_loop()
    ptt = PttController(loop)
    ptt.start()
    mic = MicCapture()
    mic.start()
    print("[SPACE] 録音 / [q] 終了", flush=True)
    turn = 0
    try:
        while not ptt.quit.is_set():
            await asyncio.wait(
                [asyncio.create_task(ptt.space_down.wait()),
                 asyncio.create_task(ptt.quit.wait())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if ptt.quit.is_set():
                break
            turn += 1
            print(f"\n─── turn {turn} ───────────────────────────────", flush=True)
            print("(recording... release SPACE to stop)", flush=True)
            mic.begin()
            await ptt.space_up.wait()
            pcm = mic.end()
            seconds = len(pcm) / 2 / SAMPLE_RATE
            print(f"(captured {len(pcm)} bytes = {seconds:.2f}s)", flush=True)
    finally:
        mic.stop()
        ptt.stop()
    return 0
```

- [ ] **Step 3: 手動 smoke test**

Run: `python scripts/bench_stt_compare.py`
Expected: SPACE 押下→ "recording..." → 1〜2 秒待って離す → `(captured XXXXXX bytes = 1.50s)` のように録音長が出る。短すぎる時は数十ミリ秒、長押しすれば長くなることで callback が動いていることを確認。

- [ ] **Step 4: Commit**

```bash
git add scripts/bench_stt_compare.py
git commit -m "feat(bench): capture PTT-recorded PCM into bytearray"
```

---

## Task 3: LocalStt — faster-whisper ラッパー + warmup

**Files:**
- Modify: `scripts/bench_stt_compare.py`

**Note:** dimos の既存ノード (`dimos/stream/audio/stt/node_whisper.py`) は openai-whisper を優先、無ければ faster-whisper にフォールバックする実装。本ハーネスでは **faster-whisper を直接** 使う（reactivex 経由は重い／ワーカー隔離が要らない）。モデル名は環境変数で上書き可能にする。

- [ ] **Step 1: LocalStt クラスを追加**

`MicCapture` の直後に追加:

```python
import os
import time

from faster_whisper import WhisperModel  # type: ignore[import-untyped]
from scipy.signal import resample_poly


_WHISPER_SAMPLE_RATE = 16_000
_DEFAULT_WHISPER_MODEL = os.environ.get("DIMOS_WHISPER_MODEL", "large-v3")


def _pcm24k_to_float16k(pcm: bytes) -> np.ndarray:
    """24kHz int16 mono bytes -> 16kHz float32 mono numpy array."""
    audio_24k = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    audio_16k = resample_poly(audio_24k, _WHISPER_SAMPLE_RATE, SAMPLE_RATE)
    return audio_16k.astype(np.float32)


class LocalStt:
    """faster-whisper transcription wrapper, ja-tuned by default."""

    def __init__(self, model_name: str = _DEFAULT_WHISPER_MODEL) -> None:
        self._model = WhisperModel(model_name, device="auto", compute_type="int8")
        self._opts = {"language": "ja", "vad_filter": False}

    def warmup(self) -> None:
        silence = np.zeros(_WHISPER_SAMPLE_RATE, dtype=np.float32)
        segs, _ = self._model.transcribe(silence, **self._opts)
        for _ in segs:
            pass

    async def transcribe(self, pcm: bytes) -> tuple[str, float]:
        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        text = await loop.run_in_executor(None, self._transcribe_sync, pcm)
        return text, time.perf_counter() - t0

    def _transcribe_sync(self, pcm: bytes) -> str:
        audio = _pcm24k_to_float16k(pcm)
        segments, _info = self._model.transcribe(audio, **self._opts)
        return " ".join(seg.text.strip() for seg in segments)
```

- [ ] **Step 2: amain で LocalStt を実呼び出し**

`amain` の `pcm = mic.end()` の直後を次に変える:

```python
            pcm = mic.end()
            seconds = len(pcm) / 2 / SAMPLE_RATE
            print(f"(captured {seconds:.2f}s)", flush=True)
            local_text, local_ms = await local.transcribe(pcm)
            print(f"Local Whisper : {local_text}  ({local_ms:.2f}s)", flush=True)
```

そして `mic.start()` の前に warmup を入れる:

```python
    mic = MicCapture()
    mic.start()
    print("(loading whisper model...)", flush=True)
    local = LocalStt()
    local.warmup()
    print("[SPACE] 録音 / [q] 終了", flush=True)
```

- [ ] **Step 3: 手動 smoke test**

Run: `python scripts/bench_stt_compare.py`
Expected: モデルロード後にプロンプト表示。SPACE 押下中に何か日本語を喋って離す → `Local Whisper : <認識テキスト> (X.XXs)` が出る。空発話なら空文字 or 空白。

- [ ] **Step 4: Commit**

```bash
git add scripts/bench_stt_compare.py
git commit -m "feat(bench): wire local faster-whisper STT path"
```

---

## Task 4: VoiceLiveStt — transcription-only session

**Files:**
- Modify: `scripts/bench_stt_compare.py`

**Note:** Azure Voice Live で「transcription だけ」を引き出すには、session の `input_audio_transcription` を有効化し、`response.create` を**呼ばない**ことで TTS/LLM 応答生成を抑制する（modalities を空にする API がないため、これが現実的な方法）。VAD は OFF にして `input_audio_buffer.commit` を明示送信し、ターン境界を制御する。受信イベントは別の async task で監視。

- [ ] **Step 1: VoiceLiveStt クラスを追加**

`LocalStt` の直後に追加:

```python
import base64

from azure.ai.voicelive.aio import connect as voicelive_connect
from azure.ai.voicelive.models import (
    AudioInputTranscriptionOptions,
    InputAudioFormat,
    RequestSession,
    ServerEventType,
)
from azure.core.credentials import AzureKeyCredential


_VL_ENDPOINT = os.environ.get("DIMOS_AZURE_VOICE_LIVE_ENDPOINT", "")
_VL_API_KEY = os.environ.get("DIMOS_AZURE_VOICE_LIVE_API_KEY", "")
_VL_MODEL = os.environ.get("DIMOS_AZURE_VOICE_LIVE_MODEL", "gpt-realtime")
_VL_STT_MODEL = os.environ.get("DIMOS_VL_STT_MODEL", "azure-speech")


class VoiceLiveStt:
    """Azure Voice Live transcription-only client.

    Opens one persistent session at startup, reuses it for every turn.
    Sends PCM via input_audio_buffer.append, commits, then awaits the
    next conversation.item.input_audio_transcription.completed event.
    """

    def __init__(self) -> None:
        if not _VL_ENDPOINT or not _VL_API_KEY:
            raise RuntimeError(
                "DIMOS_AZURE_VOICE_LIVE_ENDPOINT / _API_KEY が未設定。"
                " default.env を読み込んで再実行してください。"
            )
        self._conn_cm: object | None = None
        self._conn: object | None = None
        self._reader: asyncio.Task[None] | None = None
        self._transcript_q: asyncio.Queue[tuple[str, bool]] = asyncio.Queue()
        # tuple = (text, ok)

    async def open(self) -> None:
        self._conn_cm = voicelive_connect(
            endpoint=_VL_ENDPOINT,
            credential=AzureKeyCredential(_VL_API_KEY),
            model=_VL_MODEL,
        )
        self._conn = await self._conn_cm.__aenter__()
        await self._conn.session.update(
            session=RequestSession(
                instructions="",
                input_audio_format=InputAudioFormat.PCM16,
                turn_detection=None,  # commit を明示送信する
                input_audio_transcription=AudioInputTranscriptionOptions(
                    model=_VL_STT_MODEL, language="ja"
                ),
            )
        )
        self._reader = asyncio.create_task(self._read_events())

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
        if self._conn_cm is not None:
            await self._conn_cm.__aexit__(None, None, None)

    async def _read_events(self) -> None:
        assert self._conn is not None
        try:
            async for event in self._conn:
                etype = getattr(event, "type", None)
                if etype == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED:
                    transcript = getattr(event, "transcript", "") or ""
                    await self._transcript_q.put((transcript, True))
                elif etype == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_FAILED:
                    err = getattr(event, "error", None)
                    await self._transcript_q.put((f"[VL failed: {err}]", False))
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            await self._transcript_q.put((f"[VL reader crashed: {exc}]", False))

    async def transcribe(self, pcm: bytes) -> tuple[str, float]:
        assert self._conn is not None
        # Drain any stale event before sending.
        while not self._transcript_q.empty():
            self._transcript_q.get_nowait()
        b64 = base64.b64encode(pcm).decode("ascii")
        t0 = time.perf_counter()
        await self._conn.input_audio_buffer.append(audio=b64)
        await self._conn.input_audio_buffer.commit()
        text, _ok = await self._transcript_q.get()
        return text, time.perf_counter() - t0
```

- [ ] **Step 2: amain で VoiceLiveStt を組み込み、両者を並列実行**

`amain` の `local` セットアップ周辺を次に差し替える:

```python
    print("(loading whisper model...)", flush=True)
    local = LocalStt()
    local.warmup()
    print("(opening Voice Live session...)", flush=True)
    vl = VoiceLiveStt()
    await vl.open()
    print("[SPACE] 録音 / [q] 終了", flush=True)
```

そして transcribe 部を:

```python
            pcm = mic.end()
            seconds = len(pcm) / 2 / SAMPLE_RATE
            print(f"(captured {seconds:.2f}s)", flush=True)
            (local_text, local_ms), (vl_text, vl_ms) = await asyncio.gather(
                local.transcribe(pcm),
                vl.transcribe(pcm),
            )
            print(f"Local Whisper : {local_text}  ({local_ms:.2f}s)", flush=True)
            print(f"Voice Live    : {vl_text}  ({vl_ms:.2f}s)", flush=True)
```

`finally` ブロックに VL クローズを追加:

```python
    finally:
        await vl.close()
        mic.stop()
        ptt.stop()
```

- [ ] **Step 3: 手動 smoke test**

事前に `default.env` を source（または `DIMOS_AZURE_VOICE_LIVE_ENDPOINT` / `_API_KEY` を export）して:

Run: `python scripts/bench_stt_compare.py`
Expected: モデルロード → VL session open → プロンプト。発話→離す → `Local Whisper` と `Voice Live` の 2 行が並んで出る。両方とも日本語で発話内容を反映していること。

- [ ] **Step 4: Commit**

```bash
git add scripts/bench_stt_compare.py
git commit -m "feat(bench): add Azure Voice Live transcription-only path"
```

---

## Task 5: 差分ハイライト表示

**Files:**
- Modify: `scripts/bench_stt_compare.py`

- [ ] **Step 1: 差分整形関数を追加**

`VoiceLiveStt` クラスの直後 (amain の前) に追加:

```python
import difflib


_ANSI_RED = "\x1b[31m"
_ANSI_GREEN = "\x1b[32m"
_ANSI_RESET = "\x1b[0m"


def _highlight_diff(local_text: str, vl_text: str) -> tuple[str, str]:
    """Return (local_decorated, vl_decorated) with diff chars colored.

    Characters present only in local are red, only in VL are green,
    common characters left plain. Uses ndiff for character-level diff.
    """
    matcher = difflib.SequenceMatcher(a=local_text, b=vl_text)
    local_out: list[str] = []
    vl_out: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        l_chunk = local_text[i1:i2]
        v_chunk = vl_text[j1:j2]
        if tag == "equal":
            local_out.append(l_chunk)
            vl_out.append(v_chunk)
        else:
            if l_chunk:
                local_out.append(f"{_ANSI_RED}{l_chunk}{_ANSI_RESET}")
            if v_chunk:
                vl_out.append(f"{_ANSI_GREEN}{v_chunk}{_ANSI_RESET}")
    return "".join(local_out), "".join(vl_out)
```

- [ ] **Step 2: 表示部を差し替え**

amain の transcribe 後を:

```python
            (local_text, local_ms), (vl_text, vl_ms) = await asyncio.gather(
                local.transcribe(pcm),
                vl.transcribe(pcm),
            )
            if local_text == vl_text:
                print(f"match         : {local_text}", flush=True)
                print(f"              : local {local_ms:.2f}s / vl {vl_ms:.2f}s", flush=True)
            else:
                local_hl, vl_hl = _highlight_diff(local_text, vl_text)
                print(f"Local Whisper : {local_hl}  ({local_ms:.2f}s)", flush=True)
                print(f"Voice Live    : {vl_hl}  ({vl_ms:.2f}s)", flush=True)
```

- [ ] **Step 3: 手動 smoke test**

Run: `python scripts/bench_stt_compare.py`
Expected: 同じ転写が両方から返ったとき `match` 行のみ。異なるときは 2 行表示で差分箇所が color 着色される（ターミナルが ANSI 対応であること）。

- [ ] **Step 4: Commit**

```bash
git add scripts/bench_stt_compare.py
git commit -m "feat(bench): highlight diff between local and voice-live transcripts"
```

---

## Task 6: エラー処理の堅牢化

**Files:**
- Modify: `scripts/bench_stt_compare.py`

- [ ] **Step 1: transcribe を個別に try で包む**

amain の transcribe 部を:

```python
            local_task = asyncio.create_task(local.transcribe(pcm))
            vl_task = asyncio.create_task(vl.transcribe(pcm))
            await asyncio.gather(local_task, vl_task, return_exceptions=True)

            def _unwrap(task: asyncio.Task[tuple[str, float]], label: str) -> tuple[str, float]:
                exc = task.exception()
                if exc is not None:
                    return (f"[{label} error: {exc!r}]", 0.0)
                return task.result()

            local_text, local_ms = _unwrap(local_task, "local")
            vl_text, vl_ms = _unwrap(vl_task, "vl")
            if local_text == vl_text:
                print(f"match         : {local_text}", flush=True)
                print(f"              : local {local_ms:.2f}s / vl {vl_ms:.2f}s", flush=True)
            else:
                local_hl, vl_hl = _highlight_diff(local_text, vl_text)
                print(f"Local Whisper : {local_hl}  ({local_ms:.2f}s)", flush=True)
                print(f"Voice Live    : {vl_hl}  ({vl_ms:.2f}s)", flush=True)
```

- [ ] **Step 2: VL 起動失敗を fail-fast に**

amain の `await vl.open()` を try で包み、失敗時は明確に終了:

```python
    try:
        await vl.open()
    except Exception as exc:
        print(f"FATAL: Voice Live session open failed: {exc!r}", file=sys.stderr)
        mic.stop()
        ptt.stop()
        return 1
```

- [ ] **Step 3: 手動 smoke test (negative path)**

1. 環境変数を一時的に外す: `DIMOS_AZURE_VOICE_LIVE_API_KEY= python scripts/bench_stt_compare.py`
   Expected: `RuntimeError` メッセージで即終了 (return code != 0)。
2. 通常起動して再度普通の発話 → 正常表示。

- [ ] **Step 4: Commit**

```bash
git add scripts/bench_stt_compare.py
git commit -m "feat(bench): isolate per-stt errors so one path failing does not crash the loop"
```

---

## Task 7: README 追記

**Files:**
- Modify: `scripts/bench_stt_compare.py` (top-of-file docstring) — ファイル先頭 docstring に **使い方** を書く。プロジェクト全体の `README.md` には触らない（fork-local ツールのため）。

- [ ] **Step 1: docstring を拡充**

ファイル冒頭の docstring を次のものに置換:

```python
"""STT accuracy compare harness.

Manual-only tool to A/B-compare local faster-whisper vs Azure Voice Live
STT on the same microphone capture, used to decide whether the voice_live
blueprint should be retained for STT accuracy.

Usage:
    # default.env を source 済みで .venv が active な状態で
    python scripts/bench_stt_compare.py

Controls:
    SPACE (hold) : record while held
    SPACE (release) : stop recording, transcribe via both engines
    q : quit

Environment:
    DIMOS_AZURE_VOICE_LIVE_ENDPOINT  (required)
    DIMOS_AZURE_VOICE_LIVE_API_KEY   (required)
    DIMOS_AZURE_VOICE_LIVE_MODEL     (optional, default 'gpt-realtime')
    DIMOS_WHISPER_MODEL              (optional, default 'large-v3')
    DIMOS_VL_STT_MODEL               (optional, default 'azure-speech')

Design: docs/superpowers/specs/2026-05-17-stt-compare-harness-design.md
"""
```

- [ ] **Step 2: Commit**

```bash
git add scripts/bench_stt_compare.py
git commit -m "docs(bench): document stt-compare harness usage in module docstring"
```

---

## Self-Review

- **Spec coverage**: Goal / MicCapture / LocalStt / VoiceLiveStt / 表示 / エラー処理 / 環境変数 / 配置先 すべて該当タスクあり。非 Goals (CER 自動計算 / 永続化 / 自動テスト) は plan からも除外済。
- **Placeholder scan**: 全ステップに実コード or 実コマンドあり。"TBD" / "similar to" 等なし。
- **Type consistency**: `MicCapture.begin/end()`, `LocalStt.transcribe(pcm) -> (str, float)`, `VoiceLiveStt.transcribe(pcm) -> (str, float)`, `_highlight_diff(local_text, vl_text) -> (str, str)`, `SAMPLE_RATE = 24_000` がタスク跨ぎで一貫している。
- **既知の実装時 verification**:
  - `ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_*` の正確な enum 名。SDK の `azure/ai/voicelive/models/_models.py` で確認済（イベント discriminator 文字列の存在を grep 確認）。enum 属性名が異なれば実装中に修正。
  - `turn_detection=None` を渡せるかは Task 4 実装中に SDK 仕様で再確認。受け付けなければ `ServerVad(threshold=0)` 相当を使う。
