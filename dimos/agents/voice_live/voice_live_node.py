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

from __future__ import annotations

import asyncio
import base64
import json
import threading
from collections.abc import Callable
from typing import Any

import numpy as np
import websockets
from reactivex import Observable, Subject

from dimos.stream.audio.base import AbstractAudioConsumer, AbstractAudioEmitter, AudioEvent
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

ToolCallHandler = Callable[[str, str, str], None]  # (call_id, name, args_json) -> None


class AzureVoiceLiveNode(AbstractAudioConsumer, AbstractAudioEmitter):
    """WebSocket client for Azure Voice Live API.

    Streams microphone PCM up and receives TTS PCM + function calls down.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str,
        voice: str,
        instructions: str,
        tools: list[dict[str, Any]],
        on_tool_call: ToolCallHandler,
        sample_rate: int = 24000,
        max_retries: int = 3,
        backoff_base: float = 1.0,
    ) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.instructions = instructions
        self.tools = tools
        self.on_tool_call = on_tool_call
        self.sample_rate = sample_rate
        self.max_retries = max_retries
        self.backoff_base = backoff_base

        self._audio_out_subject: Subject[AudioEvent] = Subject()
        self._audio_in_subject: Subject[AudioEvent] | None = None
        self._ws = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def consume_audio(self, audio_observable: Observable) -> "AzureVoiceLiveNode":
        self._audio_in_subject = audio_observable  # type: ignore[assignment]
        return self

    def _activate_audio_input(self) -> None:
        """Subscribe to incoming audio observable and forward to WS."""
        if self._audio_in_subject is None:
            return

        def _on_audio(event: AudioEvent) -> None:
            pcm = event.to_int16().data.tobytes()
            payload = {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            }
            if self._ws is None or self._loop is None:
                return
            asyncio.run_coroutine_threadsafe(self._ws.send(json.dumps(payload)), self._loop)

        self._audio_in_subject.subscribe(on_next=_on_audio)  # type: ignore[union-attr]

    def emit_audio(self) -> Observable:
        return self._audio_out_subject

    async def _run_once(self) -> None:
        """Connect once and run the recv loop until disconnect or stop."""
        headers = {"api-key": self.api_key}
        async with websockets.connect(self.endpoint, additional_headers=headers) as ws:
            self._ws = ws
            self._loop = asyncio.get_running_loop()
            self._activate_audio_input()
            session_payload = {
                "type": "session.update",
                "session": {
                    "model": self.model,
                    "voice": self.voice,
                    "instructions": self.instructions,
                    "tools": self.tools,
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "input_audio_sample_rate_hz": self.sample_rate,
                    "output_audio_sample_rate_hz": self.sample_rate,
                },
            }
            await ws.send(json.dumps(session_payload))
            try:
                async for raw in ws:
                    await self._handle_message(raw)
            except asyncio.CancelledError:
                pass

    async def _handle_message(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        msg = json.loads(raw)
        mtype = msg.get("type")
        if mtype == "response.audio.delta":
            pcm_bytes = base64.b64decode(msg["delta"])
            data = np.frombuffer(pcm_bytes, dtype=np.int16)
            event = AudioEvent(
                data=data,
                sample_rate=self.sample_rate,
                timestamp=0.0,
            )
            self._audio_out_subject.on_next(event)
        elif mtype == "response.function_call_arguments.done":
            try:
                self.on_tool_call(msg["call_id"], msg["name"], msg["arguments"])
            except Exception:
                logger.exception("on_tool_call handler raised")

    def send_function_output(self, call_id: str, output: str) -> None:
        """Return a tool-call result to the LLM and prompt continuation."""
        if self._ws is None or self._loop is None:
            logger.warning("send_function_output called before WS ready; dropping")
            return

        item_msg = {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": output,
            },
        }
        response_msg = {"type": "response.create"}

        async def _send_both() -> None:
            await self._ws.send(json.dumps(item_msg))
            await self._ws.send(json.dumps(response_msg))

        asyncio.run_coroutine_threadsafe(_send_both(), self._loop)

    async def _run(self) -> None:
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                await self._run_once()
                return  # graceful end
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("voice-live WS attempt %d/%d failed: %s", attempt, self.max_retries, exc)
                await asyncio.sleep(self.backoff_base * (2 ** (attempt - 1)))
        assert last_exc is not None
        raise last_exc

    def start(self) -> None:
        """Start the WS client in a background thread with its own event loop."""
        def _run_thread() -> None:
            asyncio.run(self._run())

        self._thread = threading.Thread(target=_run_thread, name="AzureVoiceLiveNode", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the WS client."""
        if self._ws is not None and self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
