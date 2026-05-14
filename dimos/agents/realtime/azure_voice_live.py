# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Azure Voice Live realtime agent — replaces McpClient as the brain.

Streams microphone PCM → Azure Voice Live WebSocket session, plays the
returned TTS PCM back through the speakers, bridges Voice Live function
calls to the project's MCP server, and exposes the same Module ports as
McpClient so blueprints can drop it in place.
"""
from __future__ import annotations

import asyncio
import base64
import os
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from dimos.stream.audio.node_microphone import SounddeviceAudioSource

from azure.ai.voicelive.aio import connect as voicelive_connect
from azure.ai.voicelive.models import (
    AudioEchoCancellation,
    AudioNoiseReduction,
    AzureStandardVoice,
    InputAudioFormat,
    Modality,
    OutputAudioFormat,
    RequestSession,
    ServerEventType,
    ServerVad,
)
from azure.core.credentials import AzureKeyCredential

import numpy as np
import sounddevice as sd  # type: ignore[import-untyped]

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import Field

from dimos.agents.mcp.mcp_adapter import McpAdapter
from dimos.agents.realtime.prompts.japanese import JAPANESE_SYSTEM_PROMPT
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_ENV_PREFIX = "DIMOS_AZURE_VOICE_LIVE_"


@dataclass
class _PlaybackPacket:
    seq: int
    data: bytes | None  # None = end-of-stream sentinel


class _VoicePlayback:
    """Callback-driven sounddevice output with a cancellable queue.

    The sd.OutputStream callback pops bytes from ``_queue`` as the kernel
    requests them.  ``skip_pending()`` advances ``_base`` so packets with
    a lower seq number are dropped when popped.
    """

    _BYTES_PER_SAMPLE = 2  # int16 mono
    _CHUNK_SAMPLES = 1200  # 50ms at 24kHz

    def __init__(self, sample_rate: int, device_index: int | None) -> None:
        self._sample_rate = sample_rate
        self._device_index = device_index
        self._queue: queue.Queue[_PlaybackPacket] = queue.Queue()
        self._base = 0
        self._next_seq = 0
        self._remaining = b""
        self._stream: sd.OutputStream | None = None

    def start(self) -> None:
        if self._stream is not None:
            return
        self._stream = sd.OutputStream(
            device=self._device_index,
            samplerate=self._sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self._CHUNK_SAMPLES,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is None:
            return
        # Drop any pending audio, then send the end-of-stream sentinel.
        self.skip_pending()
        self._queue.put(_PlaybackPacket(seq=self._next_seq, data=None))
        self._next_seq += 1
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None

    def enqueue(self, pcm: bytes) -> None:
        if not pcm:
            return
        self._queue.put(_PlaybackPacket(seq=self._next_seq, data=pcm))
        self._next_seq += 1

    def skip_pending(self) -> None:
        """Drop everything currently buffered (called on barge-in)."""
        self._base = self._next_seq
        self._remaining = b""

    def _callback(self, outdata: np.ndarray, frames: int, _t: Any, _s: Any) -> None:
        needed = frames * self._BYTES_PER_SAMPLE
        out = self._remaining[:needed]
        self._remaining = self._remaining[needed:]

        while len(out) < needed:
            try:
                pkt = self._queue.get_nowait()
            except queue.Empty:
                out += b"\x00" * (needed - len(out))
                break
            if pkt.data is None:
                out += b"\x00" * (needed - len(out))
                break
            if pkt.seq < self._base:
                # Dropped by skip_pending().
                self._remaining = b""
                continue
            take = needed - len(out)
            out += pkt.data[:take]
            self._remaining = pkt.data[take:]

        outdata[:] = np.frombuffer(out, dtype=np.int16).reshape(-1, 1)


def _build_voice_config(voice: str) -> Any:
    """Return an SDK voice config (AzureStandardVoice or raw string).

    Azure neural voices contain a locale prefix like ``ja-JP-*`` or
    ``en-US-*``; OpenAI voices (alloy, echo, ...) are plain strings.
    """
    if "-" in voice:
        return AzureStandardVoice(name=voice)
    return voice


def _mcp_to_voice_function(mcp_tool: dict[str, Any]) -> dict[str, Any]:
    """Convert an MCP tool descriptor to the Voice Live function-tool dict.

    The SDK accepts either dataclass instances or plain dicts in the
    ``tools`` list.  We send dicts to avoid SDK type drift.
    """
    return {
        "type": "function",
        "name": mcp_tool["name"],
        "description": mcp_tool.get("description", ""),
        "parameters": mcp_tool.get(
            "inputSchema", {"type": "object", "properties": {}}
        ),
    }


def _extract_tool_text(result: dict[str, Any]) -> str:
    """Pull text content out of an MCP tools/call result.

    Image / binary content items are replaced with a ``[image omitted]``
    suffix so the LLM at least knows something was returned.
    """
    content = result.get("content", [])
    text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
    text = "\n".join(p for p in text_parts if p)
    has_non_text = any(c.get("type") != "text" for c in content)
    if has_non_text:
        text = (text + "\n[image omitted]").strip()
    return text


class AzureVoiceLiveConfig(ModuleConfig):
    endpoint: str = Field(
        default_factory=lambda: os.environ.get(f"{_ENV_PREFIX}ENDPOINT", "")
    )
    api_key: str = Field(
        default_factory=lambda: os.environ.get(f"{_ENV_PREFIX}API_KEY", "")
    )
    model: str = Field(
        default_factory=lambda: os.environ.get(f"{_ENV_PREFIX}MODEL", "gpt-realtime")
    )
    voice: str = Field(
        default_factory=lambda: os.environ.get(
            f"{_ENV_PREFIX}VOICE", "ja-JP-NanamiNeural"
        )
    )
    system_prompt: str = Field(
        default_factory=lambda: os.environ.get(
            f"{_ENV_PREFIX}SYSTEM_PROMPT", JAPANESE_SYSTEM_PROMPT
        )
    )
    mcp_server_url: str = Field(
        default_factory=lambda: os.environ.get(
            f"{_ENV_PREFIX}MCP_URL", "http://localhost:9990/mcp"
        )
    )
    mic_device_index: int | None = Field(
        default_factory=lambda: (
            int(v) if (v := os.environ.get(f"{_ENV_PREFIX}MIC_DEVICE")) else None
        )
    )
    speaker_device_index: int | None = Field(
        default_factory=lambda: (
            int(v) if (v := os.environ.get(f"{_ENV_PREFIX}SPEAKER_DEVICE")) else None
        )
    )
    sample_rate: int = 24000


class AzureVoiceLiveAgent(Module):
    """Azure Voice Live realtime conversational agent."""

    config: AzureVoiceLiveConfig
    agent: Out[BaseMessage]
    human_input: In[str]
    agent_idle: Out[bool]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._tool_pool: ThreadPoolExecutor | None = None
        self._mcp: McpAdapter | None = None
        self._tool_registry: dict[str, dict[str, Any]] = {}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._conn: Any = None  # VoiceLiveConnection at runtime
        self._playback: _VoicePlayback | None = None
        self._mic_active = threading.Event()
        self._response_active = False
        self._response_text_buf: list[str] = []
        self._mic: SounddeviceAudioSource | None = None
        self._mic_subscription: Any = None

    @rpc
    def start(self) -> None:
        self._stop_event.clear()
        super().start()
        cfg = self.config
        missing = [
            n for n in ("endpoint", "api_key") if not getattr(cfg, n)
        ]
        if missing:
            raise ValueError(
                "Missing required env vars: "
                + ", ".join(f"{_ENV_PREFIX}{n.upper()}" for n in missing)
            )
        self._tool_pool = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="VoiceLiveTool"
        )
        self._mcp = McpAdapter(url=cfg.mcp_server_url)
        self._mic = SounddeviceAudioSource(
            device_index=cfg.mic_device_index,
            sample_rate=cfg.sample_rate,
        )
        self._mic_subscription = self._mic.emit_audio().subscribe(
            on_next=self._on_mic_audio
        )

    def _start_ws_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_ws_thread,
            name="AzureVoiceLiveAgent-ws",
            daemon=True,
        )
        self._thread.start()

    def _run_ws_thread(self) -> None:
        try:
            asyncio.run(self._async_run())
        except Exception:
            logger.exception("Voice Live WS thread crashed")

    async def _async_run(self) -> None:
        cfg = self.config
        credential = AzureKeyCredential(cfg.api_key)
        async with voicelive_connect(
            endpoint=cfg.endpoint,
            credential=credential,
            model=cfg.model,
        ) as conn:
            self._conn = conn
            self._loop = asyncio.get_running_loop()
            await self._send_session_update()
            await self._event_loop()
        self._conn = None
        self._loop = None

    async def _send_session_update(self) -> None:
        cfg = self.config
        tools = [_mcp_to_voice_function(t) for t in self._tool_registry.values()]
        session = RequestSession(
            modalities=[Modality.TEXT, Modality.AUDIO],
            instructions=cfg.system_prompt,
            voice=_build_voice_config(cfg.voice),
            input_audio_format=InputAudioFormat.PCM16,
            output_audio_format=OutputAudioFormat.PCM16,
            turn_detection=ServerVad(
                threshold=0.5,
                prefix_padding_ms=300,
                silence_duration_ms=500,
            ),
            input_audio_echo_cancellation=AudioEchoCancellation(),
            input_audio_noise_reduction=AudioNoiseReduction(
                type="azure_deep_noise_suppression"
            ),
            tools=tools,
        )
        await self._conn.session.update(session=session)

    async def _event_loop(self) -> None:
        async for event in self._conn:
            if self._stop_event.is_set():
                break
            try:
                await self._handle_event(event)
            except Exception:
                logger.exception("Voice Live event handler error")

    def _on_mic_audio(self, event: Any) -> None:
        if not self._mic_active.is_set():
            return
        if self._loop is None or self._conn is None:
            return
        pcm = event.to_int16().data.tobytes()
        b64 = base64.b64encode(pcm).decode("ascii")
        asyncio.run_coroutine_threadsafe(
            self._conn.input_audio_buffer.append(audio=b64), self._loop
        )

    async def _handle_event(self, event: Any) -> None:
        et = event.type
        if et == ServerEventType.SESSION_UPDATED:
            logger.info("Voice Live session ready: %s", event.session.id)
            self._mic_active.set()
        elif et == ServerEventType.ERROR:
            logger.error("Voice Live error: %s", event.error.message)
        else:
            logger.debug("Voice Live unhandled event: %s", et)

    @rpc
    def on_system_modules(self, _modules: list[Any]) -> None:
        assert self._mcp is not None
        if not self._mcp.wait_for_ready(timeout=60.0):
            raise TimeoutError(
                f"MCP server not ready at {self.config.mcp_server_url}"
            )
        mcp_tools = self._mcp.list_tools()
        self._tool_registry = {t["name"]: t for t in mcp_tools}
        logger.info(
            "Voice Live discovered %d MCP tools: %s",
            len(mcp_tools),
            [t["name"] for t in mcp_tools],
        )
        self._start_ws_thread()

    @rpc
    def stop(self) -> None:
        if self._mic_subscription is not None:
            try:
                self._mic_subscription.dispose()
            except Exception:
                pass
            self._mic_subscription = None
        if self._mic is not None:
            try:
                self._mic.stop()
            except Exception:
                pass
            self._mic = None
        self._mic_active.clear()
        self._stop_event.set()
        if self._loop is not None and self._conn is not None:
            asyncio.run_coroutine_threadsafe(self._conn.close(), self._loop)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        if self._tool_pool is not None:
            self._tool_pool.shutdown(wait=True, cancel_futures=True)
            self._tool_pool = None
        super().stop()
