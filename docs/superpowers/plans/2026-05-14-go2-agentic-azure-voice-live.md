# unitree-go2-agentic Azure Voice Live バリアント Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Azure Voice Live API（WS、STT+LLM+TTS 一体）を使った新 blueprint `unitree-go2-agentic-voice-live` を追加する。既存 `unitree-go2-agentic` は変更しない。

**Architecture:** 新 Module `AzureVoiceLiveAgent` が `SounddeviceAudioSource`（PC マイク）→ `AzureVoiceLiveNode`（Azure WS クライアント）→ `SounddeviceAudioOutput`（PC スピーカー）を配線し、Voice Live の function call を `McpAdapter` 経由で既存 MCP サーバへブリッジする。

**Tech Stack:** Python 3.12, asyncio, `websockets`, `reactivex`, `sounddevice`, 既存 `dimos.core.module.Module` / `dimos.agents.mcp.mcp_adapter.McpAdapter`。

**Spec:** `docs/superpowers/specs/2026-05-14-go2-agentic-azure-voice-live-design.md`

---

## File Structure

新規:
- `dimos/agents/voice_live/__init__.py` — `AzureVoiceLiveAgent` を re-export
- `dimos/agents/voice_live/japanese_prompt.py` — 日本語 system prompt 定数
- `dimos/agents/voice_live/voice_live_node.py` — `AzureVoiceLiveNode`（asyncio + websockets, 純粋クラス）
- `dimos/agents/voice_live/voice_live_agent.py` — `AzureVoiceLiveAgent`（`Module` 派生、env 検証 + MCP ブリッジ）
- `dimos/agents/voice_live/test_voice_live_node.py` — Node 単体テスト（WS モック）
- `dimos/agents/voice_live/test_voice_live_agent.py` — Agent 単体テスト（MCP モック）
- `dimos/agents/voice_live/conftest.py` — pytest fixtures（必要に応じて）
- `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py` — blueprint

変更:
- `pyproject.toml` — `agents` グループに `websockets>=13` 追加
- `dimos/robot/all_blueprints.py` — 新 blueprint 登録
- `README.md` — Azure Voice Live バリアントの起動方法を追記

---

## Task 1: 依存パッケージ追加

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: `pyproject.toml` の `agents` グループ末尾に `websockets` を追加**

`agents = [` ブロック内、`"faster-whisper>=1.0.0",` の次の行に追加:

```toml
    "websockets>=13",
```

- [ ] **Step 2: 依存解決と lockfile 更新**

Run: `uv sync --extra unitree`
Expected: 成功、`websockets` が解決される。

- [ ] **Step 3: import 確認**

Run: `uv run python -c "import websockets; print(websockets.__version__)"`
Expected: バージョン文字列（13.x 以上）

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps(agents): add websockets for Azure Voice Live"
```

---

## Task 2: 日本語 system prompt 定数

**Files:**
- Create: `dimos/agents/voice_live/__init__.py`
- Create: `dimos/agents/voice_live/japanese_prompt.py`

- [ ] **Step 1: パッケージ `__init__.py` を作成**

```python
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
```

このコメントだけ書き、本体エクスポートは Task 11 以降で追記する。

- [ ] **Step 2: `japanese_prompt.py` を作成**

```python
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

JAPANESE_SYSTEM_PROMPT = """あなたは Unitree Go2 四足歩行ロボットを操作するエージェントです。
ユーザーの日本語の指示を理解し、利用可能なツール（移動、追従、状態確認など）を使って実行してください。

ガイドライン:
- 返答は日本語で、簡潔に話すこと
- 動作実行の前に、何をするか短く伝えること
- 動作完了後は結果を報告すること
- 失敗時はその理由を簡潔に説明すること
- ツールを呼ぶときは安全を最優先にし、不明確な指示は質問して確認すること
"""
```

- [ ] **Step 3: import 確認**

Run: `uv run python -c "from dimos.agents.voice_live.japanese_prompt import JAPANESE_SYSTEM_PROMPT; print(len(JAPANESE_SYSTEM_PROMPT))"`
Expected: 整数（200程度）

- [ ] **Step 4: Commit**

```bash
git add dimos/agents/voice_live/__init__.py dimos/agents/voice_live/japanese_prompt.py
git commit -m "feat(voice_live): add Japanese system prompt"
```

---

## Task 3: `AzureVoiceLiveNode` スケルトン（コンストラクタと型）

**Files:**
- Create: `dimos/agents/voice_live/voice_live_node.py`
- Create: `dimos/agents/voice_live/test_voice_live_node.py`

- [ ] **Step 1: 失敗するテストを書く**

`dimos/agents/voice_live/test_voice_live_node.py`:

```python
from dimos.agents.voice_live.voice_live_node import AzureVoiceLiveNode


def test_node_constructor_stores_config():
    node = AzureVoiceLiveNode(
        endpoint="wss://example.azure.com/voice-live",
        api_key="test-key",
        model="gpt-4o-realtime",
        voice="ja-JP-NanamiNeural",
        instructions="日本語で話して",
        tools=[],
        on_tool_call=lambda call_id, name, args_json: None,
    )
    assert node.endpoint == "wss://example.azure.com/voice-live"
    assert node.api_key == "test-key"
    assert node.model == "gpt-4o-realtime"
    assert node.voice == "ja-JP-NanamiNeural"
    assert node.instructions == "日本語で話して"
    assert node.tools == []
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_node.py -v`
Expected: FAIL（モジュール未定義）

- [ ] **Step 3: 最小実装**

`dimos/agents/voice_live/voice_live_node.py`:

```python
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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
    ) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.instructions = instructions
        self.tools = tools
        self.on_tool_call = on_tool_call
        self.sample_rate = sample_rate

        self._audio_out_subject: Subject[AudioEvent] = Subject()
        self._audio_in_subject: Subject[AudioEvent] | None = None

    def consume_audio(self, audio_observable: Observable) -> "AzureVoiceLiveNode":
        self._audio_in_subject = audio_observable  # type: ignore[assignment]
        return self

    def emit_audio(self) -> Observable:
        return self._audio_out_subject
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_node.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dimos/agents/voice_live/voice_live_node.py dimos/agents/voice_live/test_voice_live_node.py
git commit -m "feat(voice_live): add AzureVoiceLiveNode skeleton"
```

---

## Task 4: WS 接続と `session.update` 送信

**Files:**
- Modify: `dimos/agents/voice_live/voice_live_node.py`
- Modify: `dimos/agents/voice_live/test_voice_live_node.py`

- [ ] **Step 1: WS モックを使う失敗テストを追加**

`dimos/agents/voice_live/test_voice_live_node.py` の末尾に追加:

```python
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_start_connects_and_sends_session_update():
    sent_messages: list[str] = []

    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock(side_effect=lambda msg: sent_messages.append(msg))
    mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError)  # exit recv loop immediately
    mock_ws.close = AsyncMock()

    @asyncio.coroutine
    def _fake_connect(*args, **kwargs):
        return mock_ws

    node = AzureVoiceLiveNode(
        endpoint="wss://example.azure.com/voice-live",
        api_key="test-key",
        model="gpt-4o-realtime",
        voice="ja-JP-NanamiNeural",
        instructions="日本語で話して",
        tools=[{"type": "function", "name": "move", "description": "move", "parameters": {}}],
        on_tool_call=lambda *a: None,
    )

    with patch("dimos.agents.voice_live.voice_live_node.websockets.connect", new=AsyncMock(return_value=mock_ws)):
        await node._run_once()  # one connection attempt

    # session.update should have been sent
    session_msgs = [json.loads(m) for m in sent_messages if json.loads(m).get("type") == "session.update"]
    assert len(session_msgs) == 1
    session = session_msgs[0]["session"]
    assert session["model"] == "gpt-4o-realtime"
    assert session["voice"] == "ja-JP-NanamiNeural"
    assert session["instructions"] == "日本語で話して"
    assert session["tools"] == [{"type": "function", "name": "move", "description": "move", "parameters": {}}]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_node.py::test_start_connects_and_sends_session_update -v`
Expected: FAIL（`_run_once` 未実装）

- [ ] **Step 3: 実装**

`dimos/agents/voice_live/voice_live_node.py` の `class AzureVoiceLiveNode` 末尾に追加:

```python
    async def _run_once(self) -> None:
        """Connect once and run the recv loop until disconnect or stop.

        Internal helper; the public start() will call this with reconnect logic.
        """
        import websockets

        headers = {"api-key": self.api_key}
        async with websockets.connect(self.endpoint, additional_headers=headers) as ws:
            self._ws = ws
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
        """Handle a single WS message. Filled in by later tasks."""
        return None
```

ファイル先頭の import 群に追加:

```python
import asyncio
import json
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_node.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "feat(voice_live): connect WS and send session.update"
```

---

## Task 5: 音声送信 — `consume_audio` → `input_audio_buffer.append`

**Files:**
- Modify: `dimos/agents/voice_live/voice_live_node.py`
- Modify: `dimos/agents/voice_live/test_voice_live_node.py`

- [ ] **Step 1: 失敗テストを追加**

```python
import base64

import numpy as np
import reactivex as rx
from dimos.stream.audio.base import AudioEvent


@pytest.mark.asyncio
async def test_audio_in_chunks_forwarded_as_input_audio_buffer_append():
    sent_messages: list[str] = []
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock(side_effect=lambda msg: sent_messages.append(msg))

    node = AzureVoiceLiveNode(
        endpoint="wss://example", api_key="k", model="m", voice="v",
        instructions="", tools=[], on_tool_call=lambda *a: None,
    )
    node._ws = mock_ws
    node._loop = asyncio.get_event_loop()

    audio = AudioEvent(
        data=np.array([0, 100, -100], dtype=np.int16),
        sample_rate=24000,
        timestamp=0.0,
    )
    # consume_audio should subscribe to source and forward chunks
    subject: rx.subject.Subject[AudioEvent] = rx.subject.Subject()
    node.consume_audio(subject)
    node._activate_audio_input()  # bridge subject to WS sender

    subject.on_next(audio)
    await asyncio.sleep(0.05)  # let async task run

    appends = [json.loads(m) for m in sent_messages if json.loads(m).get("type") == "input_audio_buffer.append"]
    assert len(appends) == 1
    decoded = base64.b64decode(appends[0]["audio"])
    assert len(decoded) == 6  # 3 int16 samples = 6 bytes
```

- [ ] **Step 2: 失敗確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_node.py::test_audio_in_chunks_forwarded_as_input_audio_buffer_append -v`
Expected: FAIL（`_activate_audio_input` 未実装）

- [ ] **Step 3: 実装**

`voice_live_node.py` の import に追加:

```python
import base64
```

`AzureVoiceLiveNode` 内に追加:

```python
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
```

`__init__` の末尾に追加:

```python
        self._ws = None
        self._loop: asyncio.AbstractEventLoop | None = None
```

`_run_once` の `async with` ブロック先頭（`self._ws = ws` の後）に追加:

```python
            self._loop = asyncio.get_running_loop()
            self._activate_audio_input()
```

- [ ] **Step 4: テスト通過確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_node.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "feat(voice_live): forward mic audio as input_audio_buffer.append"
```

---

## Task 6: 音声受信 — `response.audio.delta` → `emit_audio()`

**Files:**
- Modify: `dimos/agents/voice_live/voice_live_node.py`
- Modify: `dimos/agents/voice_live/test_voice_live_node.py`

- [ ] **Step 1: 失敗テストを追加**

```python
@pytest.mark.asyncio
async def test_response_audio_delta_emits_audio_event():
    received: list[AudioEvent] = []
    node = AzureVoiceLiveNode(
        endpoint="wss://example", api_key="k", model="m", voice="v",
        instructions="", tools=[], on_tool_call=lambda *a: None,
    )
    node.emit_audio().subscribe(on_next=received.append)

    pcm = np.array([0, 256, -256], dtype=np.int16).tobytes()
    raw = json.dumps({
        "type": "response.audio.delta",
        "delta": base64.b64encode(pcm).decode("ascii"),
    })
    await node._handle_message(raw)

    assert len(received) == 1
    assert received[0].sample_rate == 24000
    assert received[0].data.dtype == np.int16
    assert received[0].data.tolist() == [0, 256, -256]
```

- [ ] **Step 2: 失敗確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_node.py::test_response_audio_delta_emits_audio_event -v`
Expected: FAIL

- [ ] **Step 3: 実装**

`voice_live_node.py` の import に `import numpy as np` を追加（既存なら飛ばす）、`_handle_message` を更新:

```python
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
```

- [ ] **Step 4: テスト通過確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_node.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "feat(voice_live): emit TTS audio from response.audio.delta"
```

---

## Task 7: function call ハンドリング

**Files:**
- Modify: `dimos/agents/voice_live/voice_live_node.py`
- Modify: `dimos/agents/voice_live/test_voice_live_node.py`

- [ ] **Step 1: 失敗テストを追加**

```python
@pytest.mark.asyncio
async def test_function_call_arguments_done_invokes_handler():
    calls: list[tuple[str, str, str]] = []

    def handler(call_id: str, name: str, args_json: str) -> None:
        calls.append((call_id, name, args_json))

    node = AzureVoiceLiveNode(
        endpoint="wss://example", api_key="k", model="m", voice="v",
        instructions="", tools=[], on_tool_call=handler,
    )

    raw = json.dumps({
        "type": "response.function_call_arguments.done",
        "call_id": "call_123",
        "name": "relative_move",
        "arguments": '{"x": 1.0, "y": 0}',
    })
    await node._handle_message(raw)

    assert calls == [("call_123", "relative_move", '{"x": 1.0, "y": 0}')]
```

- [ ] **Step 2: 失敗確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_node.py::test_function_call_arguments_done_invokes_handler -v`
Expected: FAIL

- [ ] **Step 3: 実装** — `_handle_message` に分岐を追加

```python
        elif mtype == "response.function_call_arguments.done":
            try:
                self.on_tool_call(msg["call_id"], msg["name"], msg["arguments"])
            except Exception:
                logger.exception("on_tool_call handler raised")
```

- [ ] **Step 4: テスト通過確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_node.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "feat(voice_live): dispatch function call to handler"
```

---

## Task 8: `send_function_output` で結果返信

**Files:**
- Modify: `dimos/agents/voice_live/voice_live_node.py`
- Modify: `dimos/agents/voice_live/test_voice_live_node.py`

- [ ] **Step 1: 失敗テストを追加**

```python
@pytest.mark.asyncio
async def test_send_function_output_emits_correct_messages():
    sent: list[str] = []
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock(side_effect=lambda m: sent.append(m))

    node = AzureVoiceLiveNode(
        endpoint="wss://example", api_key="k", model="m", voice="v",
        instructions="", tools=[], on_tool_call=lambda *a: None,
    )
    node._ws = mock_ws
    node._loop = asyncio.get_event_loop()

    node.send_function_output("call_42", "Moved 1m forward")
    await asyncio.sleep(0.05)

    types = [json.loads(s)["type"] for s in sent]
    assert types == ["conversation.item.create", "response.create"]
    item = json.loads(sent[0])["item"]
    assert item["type"] == "function_call_output"
    assert item["call_id"] == "call_42"
    assert item["output"] == "Moved 1m forward"
```

- [ ] **Step 2: 失敗確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_node.py::test_send_function_output_emits_correct_messages -v`
Expected: FAIL

- [ ] **Step 3: 実装** — `AzureVoiceLiveNode` に追加:

```python
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
```

- [ ] **Step 4: テスト通過確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_node.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "feat(voice_live): send function_call_output and continuation prompt"
```

---

## Task 9: `start()` / `stop()` と再接続ロジック

**Files:**
- Modify: `dimos/agents/voice_live/voice_live_node.py`
- Modify: `dimos/agents/voice_live/test_voice_live_node.py`

- [ ] **Step 1: 失敗テストを追加**

```python
@pytest.mark.asyncio
async def test_start_retries_on_connect_failure_then_gives_up():
    attempts: list[int] = []

    async def fake_connect(*args, **kwargs):
        attempts.append(len(attempts) + 1)
        raise ConnectionError("nope")

    node = AzureVoiceLiveNode(
        endpoint="wss://example", api_key="k", model="m", voice="v",
        instructions="", tools=[], on_tool_call=lambda *a: None,
        max_retries=3, backoff_base=0.0,
    )
    with patch("dimos.agents.voice_live.voice_live_node.websockets.connect", side_effect=fake_connect):
        with pytest.raises(ConnectionError):
            await node._run()

    assert len(attempts) == 3
```

- [ ] **Step 2: 失敗確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_node.py::test_start_retries_on_connect_failure_then_gives_up -v`
Expected: FAIL

- [ ] **Step 3: 実装**

`__init__` のシグネチャに `max_retries: int = 3, backoff_base: float = 1.0` を追加し、フィールドへ格納:

```python
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._stop_event = asyncio.Event() if False else None  # set in start()
        self._task: asyncio.Task[None] | None = None
        self._thread: threading.Thread | None = None
```

`AzureVoiceLiveNode` 本体に追加:

```python
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
```

ファイル先頭の import に `import threading` を追加。

- [ ] **Step 4: テスト通過確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_node.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "feat(voice_live): start/stop with backoff reconnect"
```

---

## Task 10: `AzureVoiceLiveAgent` config + env バリデーション

**Files:**
- Create: `dimos/agents/voice_live/voice_live_agent.py`
- Create: `dimos/agents/voice_live/test_voice_live_agent.py`

- [ ] **Step 1: 失敗テストを書く**

`dimos/agents/voice_live/test_voice_live_agent.py`:

```python
import os
from unittest.mock import patch

import pytest

from dimos.agents.voice_live.voice_live_agent import AzureVoiceLiveAgent, AzureVoiceLiveConfig


def test_config_reads_env(monkeypatch):
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_ENDPOINT", "wss://e")
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_API_KEY", "k")
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_MODEL", "m")
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_VOICE", "ja-JP-NanamiNeural")

    cfg = AzureVoiceLiveConfig()
    assert cfg.endpoint == "wss://e"
    assert cfg.api_key == "k"
    assert cfg.model == "m"
    assert cfg.voice == "ja-JP-NanamiNeural"
    assert cfg.mcp_server_url == "http://localhost:9990/mcp"


def test_missing_required_env_raises(monkeypatch):
    monkeypatch.delenv("DIMOS_AZURE_VOICE_LIVE_ENDPOINT", raising=False)
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_API_KEY", "k")
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_MODEL", "m")
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_VOICE", "v")

    agent = AzureVoiceLiveAgent()
    with pytest.raises(ValueError, match="DIMOS_AZURE_VOICE_LIVE_ENDPOINT"):
        agent.start()
```

- [ ] **Step 2: 失敗確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_agent.py -v`
Expected: FAIL（モジュール未定義）

- [ ] **Step 3: 実装** — `dimos/agents/voice_live/voice_live_agent.py`:

```python
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

import os
from typing import Any

from pydantic import Field

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_REQUIRED_ENVS = [
    "DIMOS_AZURE_VOICE_LIVE_ENDPOINT",
    "DIMOS_AZURE_VOICE_LIVE_API_KEY",
    "DIMOS_AZURE_VOICE_LIVE_MODEL",
    "DIMOS_AZURE_VOICE_LIVE_VOICE",
]


class AzureVoiceLiveConfig(ModuleConfig):
    endpoint: str = Field(default_factory=lambda: os.environ.get("DIMOS_AZURE_VOICE_LIVE_ENDPOINT", ""))
    api_key: str = Field(default_factory=lambda: os.environ.get("DIMOS_AZURE_VOICE_LIVE_API_KEY", ""))
    model: str = Field(default_factory=lambda: os.environ.get("DIMOS_AZURE_VOICE_LIVE_MODEL", ""))
    voice: str = Field(default_factory=lambda: os.environ.get("DIMOS_AZURE_VOICE_LIVE_VOICE", ""))
    mcp_server_url: str = Field(
        default_factory=lambda: os.environ.get(
            "DIMOS_AZURE_VOICE_LIVE_MCP_URL", "http://localhost:9990/mcp"
        )
    )
    mic_device_index: int | None = Field(
        default_factory=lambda: int(v) if (v := os.environ.get("DIMOS_AZURE_VOICE_LIVE_MIC_DEVICE")) else None
    )
    speaker_device_index: int | None = Field(
        default_factory=lambda: int(v) if (v := os.environ.get("DIMOS_AZURE_VOICE_LIVE_SPEAKER_DEVICE")) else None
    )


class AzureVoiceLiveAgent(Module):
    config: AzureVoiceLiveConfig

    @rpc
    def start(self) -> None:
        super().start()
        missing = [
            name for name in _REQUIRED_ENVS
            if not getattr(self.config, name.removeprefix("DIMOS_AZURE_VOICE_LIVE_").lower())
        ]
        if missing:
            raise ValueError(f"Missing required env vars: {', '.join(missing)}")
```

- [ ] **Step 4: テスト通過確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dimos/agents/voice_live/voice_live_agent.py dimos/agents/voice_live/test_voice_live_agent.py
git commit -m "feat(voice_live): add AzureVoiceLiveAgent with env validation"
```

---

## Task 11: MCP tool 取得と Voice Live 形式変換

**Files:**
- Modify: `dimos/agents/voice_live/voice_live_agent.py`
- Modify: `dimos/agents/voice_live/test_voice_live_agent.py`

- [ ] **Step 1: 失敗テストを追加**

```python
from unittest.mock import MagicMock


def test_convert_mcp_tools_to_voice_live_format():
    agent = AzureVoiceLiveAgent()
    mcp_tools = [
        {
            "name": "relative_move",
            "description": "Move robot by relative delta",
            "inputSchema": {"type": "object", "properties": {"x": {"type": "number"}}},
        },
        {
            "name": "speak",  # description 欠落のケース
            "inputSchema": {"type": "object"},
        },
    ]
    result = agent._convert_tools(mcp_tools)
    assert result == [
        {
            "type": "function",
            "name": "relative_move",
            "description": "Move robot by relative delta",
            "parameters": {"type": "object", "properties": {"x": {"type": "number"}}},
        },
        {
            "type": "function",
            "name": "speak",
            "description": "",
            "parameters": {"type": "object"},
        },
    ]
```

- [ ] **Step 2: 失敗確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_agent.py::test_convert_mcp_tools_to_voice_live_format -v`
Expected: FAIL

- [ ] **Step 3: 実装** — `AzureVoiceLiveAgent` に追加:

```python
    @staticmethod
    def _convert_tools(mcp_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["inputSchema"],
            }
            for t in mcp_tools
        ]
```

- [ ] **Step 4: テスト通過**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "feat(voice_live): convert MCP tools to Voice Live function format"
```

---

## Task 12: `start()` で MCP + Node + Audio I/O を配線

**Files:**
- Modify: `dimos/agents/voice_live/voice_live_agent.py`
- Modify: `dimos/agents/voice_live/test_voice_live_agent.py`

- [ ] **Step 1: 失敗テストを追加**

```python
from unittest.mock import MagicMock, patch


def test_start_wires_mcp_node_and_audio(monkeypatch):
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_ENDPOINT", "wss://e")
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_API_KEY", "k")
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_MODEL", "m")
    monkeypatch.setenv("DIMOS_AZURE_VOICE_LIVE_VOICE", "v")

    mock_mcp = MagicMock()
    mock_mcp.wait_for_ready.return_value = True
    mock_mcp.list_tools.return_value = [
        {"name": "x", "description": "d", "inputSchema": {"type": "object"}},
    ]

    mock_node = MagicMock()
    mock_mic = MagicMock()
    mock_speaker = MagicMock()

    with patch("dimos.agents.voice_live.voice_live_agent.McpAdapter", return_value=mock_mcp), \
         patch("dimos.agents.voice_live.voice_live_agent.AzureVoiceLiveNode", return_value=mock_node), \
         patch("dimos.agents.voice_live.voice_live_agent.SounddeviceAudioSource", return_value=mock_mic), \
         patch("dimos.agents.voice_live.voice_live_agent.SounddeviceAudioOutput", return_value=mock_speaker):
        agent = AzureVoiceLiveAgent()
        agent.start()

    mock_mcp.wait_for_ready.assert_called_once()
    mock_mcp.list_tools.assert_called_once()
    mock_node.consume_audio.assert_called_once()
    mock_speaker.consume_audio.assert_called_once_with(mock_node.emit_audio.return_value)
    mock_node.start.assert_called_once()
```

- [ ] **Step 2: 失敗確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_agent.py::test_start_wires_mcp_node_and_audio -v`
Expected: FAIL

- [ ] **Step 3: 実装** — `voice_live_agent.py` を更新:

ファイル先頭の import 群に追加:

```python
from dimos.agents.mcp.mcp_adapter import McpAdapter
from dimos.agents.voice_live.japanese_prompt import JAPANESE_SYSTEM_PROMPT
from dimos.agents.voice_live.voice_live_node import AzureVoiceLiveNode
from dimos.stream.audio.node_microphone import SounddeviceAudioSource
from dimos.stream.audio.node_output import SounddeviceAudioOutput
```

`AzureVoiceLiveAgent.start()` を以下に置き換え:

```python
    @rpc
    def start(self) -> None:
        super().start()
        cfg = self.config
        missing = [name for name in _REQUIRED_ENVS if not getattr(cfg, name.removeprefix("DIMOS_AZURE_VOICE_LIVE_").lower())]
        if missing:
            raise ValueError(f"Missing required env vars: {', '.join(missing)}")

        self._mcp = McpAdapter(url=cfg.mcp_server_url)
        if not self._mcp.wait_for_ready(timeout=30.0):
            raise TimeoutError(f"MCP server not ready at {cfg.mcp_server_url}")
        mcp_tools = self._mcp.list_tools()
        voice_live_tools = self._convert_tools(mcp_tools)

        self._mic = SounddeviceAudioSource(
            device_index=cfg.mic_device_index, sample_rate=24000
        )
        self._speaker = SounddeviceAudioOutput(sample_rate=24000)

        self._node = AzureVoiceLiveNode(
            endpoint=cfg.endpoint,
            api_key=cfg.api_key,
            model=cfg.model,
            voice=cfg.voice,
            instructions=JAPANESE_SYSTEM_PROMPT,
            tools=voice_live_tools,
            on_tool_call=self._handle_tool_call,
        )
        self._node.consume_audio(self._mic.emit_audio())
        self._speaker.consume_audio(self._node.emit_audio())
        self._node.start()

    def _handle_tool_call(self, call_id: str, name: str, args_json: str) -> None:
        """Filled in by Task 13."""
        return None
```

- [ ] **Step 4: テスト通過確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "feat(voice_live): wire MCP + Node + audio I/O in start()"
```

---

## Task 13: `_handle_tool_call` — 別スレッドで MCP 呼び出し

**Files:**
- Modify: `dimos/agents/voice_live/voice_live_agent.py`
- Modify: `dimos/agents/voice_live/test_voice_live_agent.py`

- [ ] **Step 1: 失敗テストを追加**

```python
import time


def test_handle_tool_call_forwards_to_mcp_and_returns_result():
    agent = AzureVoiceLiveAgent()
    agent._mcp = MagicMock()
    agent._mcp.call_tool_text.return_value = "moved successfully"
    agent._node = MagicMock()

    agent._handle_tool_call("call_1", "relative_move", '{"x": 1.0}')

    # _handle_tool_call dispatches to thread; wait for it
    for _ in range(50):
        if agent._node.send_function_output.called:
            break
        time.sleep(0.02)

    agent._mcp.call_tool_text.assert_called_once_with("relative_move", {"x": 1.0})
    agent._node.send_function_output.assert_called_once_with("call_1", "moved successfully")


def test_handle_tool_call_returns_error_text_on_exception():
    agent = AzureVoiceLiveAgent()
    agent._mcp = MagicMock()
    agent._mcp.call_tool_text.side_effect = RuntimeError("boom")
    agent._node = MagicMock()

    agent._handle_tool_call("call_2", "broken_tool", "{}")

    for _ in range(50):
        if agent._node.send_function_output.called:
            break
        time.sleep(0.02)

    args, _ = agent._node.send_function_output.call_args
    assert args[0] == "call_2"
    assert "boom" in args[1]
```

- [ ] **Step 2: 失敗確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_agent.py -v`
Expected: 上記2件が FAIL

- [ ] **Step 3: 実装**

ファイル先頭の import に追加:

```python
import json
from concurrent.futures import ThreadPoolExecutor
```

`AzureVoiceLiveAgent.__init__` を追加（既存に追記）:

```python
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._executor: ThreadPoolExecutor | None = None
        self._mcp: Any = None
        self._node: Any = None
        self._mic: Any = None
        self._speaker: Any = None
```

`start()` 内、`super().start()` の直後あたりに `self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="VoiceLiveTool")` を追加。

`_handle_tool_call` を以下で置き換え:

```python
    def _handle_tool_call(self, call_id: str, name: str, args_json: str) -> None:
        if self._executor is None:
            return  # not started yet

        def _run() -> None:
            try:
                args = json.loads(args_json) if args_json else {}
            except Exception as exc:  # noqa: BLE001
                self._node.send_function_output(call_id, f"Error: invalid arguments JSON: {exc}")
                return
            try:
                result = self._mcp.call_tool_text(name, args)
            except Exception as exc:  # noqa: BLE001
                logger.exception("MCP tool %s failed", name)
                self._node.send_function_output(call_id, f"Error: {exc}")
                return
            self._node.send_function_output(call_id, result)

        self._executor.submit(_run)
```

- [ ] **Step 4: テスト通過確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "feat(voice_live): bridge function calls to MCP in worker thread"
```

---

## Task 14: `stop()` ライフサイクル

**Files:**
- Modify: `dimos/agents/voice_live/voice_live_agent.py`
- Modify: `dimos/agents/voice_live/test_voice_live_agent.py`

- [ ] **Step 1: 失敗テストを追加**

```python
def test_stop_cleans_up_node_audio_executor():
    agent = AzureVoiceLiveAgent()
    agent._node = MagicMock()
    agent._speaker = MagicMock()
    agent._mic = MagicMock()
    agent._executor = MagicMock()

    agent.stop()

    agent._node.stop.assert_called_once()
    agent._speaker.stop.assert_called_once()
    agent._executor.shutdown.assert_called_once()
```

- [ ] **Step 2: 失敗確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_agent.py::test_stop_cleans_up_node_audio_executor -v`
Expected: FAIL

- [ ] **Step 3: 実装** — `AzureVoiceLiveAgent` に追加:

```python
    @rpc
    def stop(self) -> None:
        if self._node is not None:
            self._node.stop()
        if self._speaker is not None:
            self._speaker.stop()
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
        super().stop()
```

- [ ] **Step 4: テスト通過確認**

Run: `uv run pytest dimos/agents/voice_live/test_voice_live_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "feat(voice_live): clean up resources on stop"
```

---

## Task 15: package re-export

**Files:**
- Modify: `dimos/agents/voice_live/__init__.py`

- [ ] **Step 1: `__init__.py` を更新**

```python
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

from dimos.agents.voice_live.voice_live_agent import (
    AzureVoiceLiveAgent,
    AzureVoiceLiveConfig,
)

__all__ = ["AzureVoiceLiveAgent", "AzureVoiceLiveConfig"]
```

- [ ] **Step 2: 確認**

Run: `uv run python -c "from dimos.agents.voice_live import AzureVoiceLiveAgent; print(AzureVoiceLiveAgent)"`
Expected: クラスの repr が出力される

- [ ] **Step 3: Commit**

```bash
git add dimos/agents/voice_live/__init__.py
git commit -m "feat(voice_live): export public classes from package"
```

---

## Task 16: blueprint ファイル作成

**Files:**
- Create: `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py`

- [ ] **Step 1: blueprint ファイル作成**

```python
#!/usr/bin/env python3
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.skills.navigation import NavigationSkillContainer
from dimos.agents.skills.person_follow import PersonFollowSkillContainer
from dimos.agents.voice_live import AzureVoiceLiveAgent
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_spatial import unitree_go2_spatial
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer

unitree_go2_agentic_voice_live = autoconnect(
    unitree_go2_spatial,
    McpServer.blueprint(),
    AzureVoiceLiveAgent.blueprint(),
    NavigationSkillContainer.blueprint(),
    PersonFollowSkillContainer.blueprint(camera_info=GO2Connection.camera_info_static),
    UnitreeSkillContainer.blueprint(),
)

__all__ = ["unitree_go2_agentic_voice_live"]
```

- [ ] **Step 2: import 健全性**

Run: `uv run python -c "from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_voice_live import unitree_go2_agentic_voice_live; print(unitree_go2_agentic_voice_live)"`
Expected: blueprint オブジェクトが出力される

- [ ] **Step 3: Commit**

```bash
git add dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py
git commit -m "feat(go2): add voice-live blueprint variant"
```

---

## Task 17: 全 blueprint 登録に追加

**Files:**
- Modify: `dimos/robot/all_blueprints.py`

- [ ] **Step 1: レジストリに 1 行追加**

`dimos/robot/all_blueprints.py` の `"unitree-go2-agentic-ollama": ...` の直後に追加:

```python
    "unitree-go2-agentic-voice-live": "dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_voice_live:unitree_go2_agentic_voice_live",
```

注: このファイルは `"key": "module:variable"` 形式の lazy 参照 dict なので、import 文の追加は不要。

- [ ] **Step 2: CLI で見えるか確認**

Run: `uv run dimos list 2>&1 | grep voice-live`
Expected: `unitree-go2-agentic-voice-live` が表示される。

- [ ] **Step 3: Commit**

```bash
git add dimos/robot/all_blueprints.py
git commit -m "feat(go2): register voice-live blueprint in dimos CLI"
```

---

## Task 18: README に起動方法追記

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 既存 `DIMOS_TTS` / `DIMOS_LLM_MODEL` の記述箇所を見つける**

Run: `grep -n "DIMOS_TTS\|DIMOS_LLM_MODEL\|unitree-go2-agentic" README.md`
Expected: 該当行が表示される。

- [ ] **Step 2: 該当セクション末尾に Voice Live バリアントの節を追加**

```markdown
### Azure Voice Live バリアント

リアルタイム音声会話（STT + LLM + TTS が Azure Voice Live 1本）で起動するには：

```bash
export DIMOS_AZURE_VOICE_LIVE_ENDPOINT=wss://<your-resource>.cognitiveservices.azure.com/voice-live/realtime
export DIMOS_AZURE_VOICE_LIVE_API_KEY=<key>
export DIMOS_AZURE_VOICE_LIVE_MODEL=<deployment-name>           # 例: gpt-4o-realtime
export DIMOS_AZURE_VOICE_LIVE_VOICE=ja-JP-NanamiNeural

uv run dimos run unitree-go2-agentic-voice-live
```

DimOS が動作する PC のローカルマイク/スピーカーで音声入出力します（Go2 オンボードオーディオは未対応）。`SpeakSkill` および Web UI は本バリアントには含まれません。
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): document unitree-go2-agentic-voice-live variant"
```

---

## Task 19: 全テスト実行と整合性確認

**Files:**
- なし（検証のみ）

- [ ] **Step 1: voice_live パッケージのテストを全部 pass**

Run: `uv run pytest dimos/agents/voice_live/ -v`
Expected: 全 PASS

- [ ] **Step 2: 既存テストを壊していないか**

Run: `uv run pytest dimos/agents/mcp/ dimos/agents/test_*.py -v` (高速な単体テスト範囲)
Expected: 既存テストすべて PASS

- [ ] **Step 3: import 健全性**

Run: `uv run python -c "from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_voice_live import unitree_go2_agentic_voice_live; print('ok')"`
Expected: `ok`

- [ ] **Step 4: 既存 `unitree_go2_agentic` も壊れていない**

Run: `uv run python -c "from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic import unitree_go2_agentic; print('ok')"`
Expected: `ok`

このタスクはコード変更を伴わないため commit は不要。

---

## 手動 E2E（実装後・別作業）

CI 対象外。実機 + Azure サブスクリプションで実施するスモーク:

1. 必要 env をエクスポート
2. `uv run dimos run unitree-go2-agentic-voice-live` 起動
3. マイクで「こんにちは」→ 日本語応答が返る
4. 「1メートル前進して」→ Go2 が動き完了報告
5. 会話中の割り込み挙動（人が喋ったら応答が止まるか）
6. PC のネットワーク一時 off → 自動再接続

---

## 完了基準

- 全 Task の単体テストが PASS
- 既存テストが PASS（regression なし）
- `uv run dimos list` で `unitree-go2-agentic-voice-live` が表示
- README に起動手順記載
- spec の各セクション（アーキテクチャ、設定、エラー処理、テスト戦略、スコープ外）が対応する Task で実装されている
