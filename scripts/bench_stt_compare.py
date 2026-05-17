#!/usr/bin/env python3
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

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
    DIMOS_WHISPER_MODEL              (optional, default 'base' — matches
                                      unitree-go2-agentic-local-tts blueprint)
    DIMOS_VL_STT_MODEL               (optional, default 'azure-speech')

Design: docs/superpowers/specs/2026-05-17-stt-compare-harness-design.md
"""

from __future__ import annotations

import asyncio
import base64
import difflib
import os
import sys
import threading
import time

import numpy as np
import sounddevice as sd  # type: ignore[import-untyped]
from azure.ai.voicelive.aio import connect as voicelive_connect
from azure.ai.voicelive.models import (
    AudioInputTranscriptionOptions,
    InputAudioFormat,
    RequestSession,
    ServerEventType,
)
from azure.core.credentials import AzureKeyCredential
from faster_whisper import WhisperModel  # type: ignore[import-untyped]
from pynput import keyboard
from scipy.signal import resample_poly


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


_WHISPER_SAMPLE_RATE = 16_000
_DEFAULT_WHISPER_MODEL = os.environ.get("DIMOS_WHISPER_MODEL", "base")


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


_VL_ENDPOINT = os.environ.get("DIMOS_AZURE_VOICE_LIVE_ENDPOINT", "")
_VL_API_KEY = os.environ.get("DIMOS_AZURE_VOICE_LIVE_API_KEY", "")
_VL_MODEL = os.environ.get("DIMOS_AZURE_VOICE_LIVE_MODEL", "gpt-realtime")
_VL_STT_MODEL = os.environ.get("DIMOS_VL_STT_MODEL", "azure-speech")
_VL_TIMEOUT_S = 30.0


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
        try:
            text, _ok = await asyncio.wait_for(
                self._transcript_q.get(), timeout=_VL_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            return f"[VL timeout after {_VL_TIMEOUT_S:.0f}s]", time.perf_counter() - t0
        return text, time.perf_counter() - t0


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


async def amain() -> int:
    loop = asyncio.get_running_loop()
    ptt = PttController(loop)
    ptt.start()
    mic = MicCapture()
    mic.start()
    print("(loading whisper model...)", flush=True)
    local = LocalStt()
    local.warmup()
    print("(opening Voice Live session...)", flush=True)
    vl = VoiceLiveStt()
    try:
        await vl.open()
    except Exception as exc:
        print(f"FATAL: Voice Live session open failed: {exc!r}", file=sys.stderr)
        mic.stop()
        ptt.stop()
        return 1
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
            print(f"(captured {seconds:.2f}s)", flush=True)
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
    finally:
        await vl.close()
        mic.stop()
        ptt.stop()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
