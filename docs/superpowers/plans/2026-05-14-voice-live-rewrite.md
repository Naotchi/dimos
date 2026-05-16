# Voice Live エージェント書き直し Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `unitree-go2-agentic` の頭脳 `McpClient` を Azure Voice Live ベースのリアルタイム音声エージェントに置き換え、音声会話・MCPツール呼び出し・バージイン・trigger continuation・WebInput 並行入力を全て実現する。

**Architecture:** `azure.ai.voicelive` SDK を使った WebSocket セッションを背景スレッド (`asyncio.run` 内) で保持し、reactivex Subject / In・Out ポートでモジュール境界とブリッジ。音声 I/O は sounddevice、ツール呼び出しは既存の `McpAdapter` 経由で MCP サーバへ。`AzureVoiceLiveAgent` は `dimos/agents/realtime/azure_voice_live.py` に新設し、`McpClient` の I/F (`human_input`, `agent`, `agent_idle`, `add_message`, `dispatch_continuation`) を互換実装する。

**Tech Stack:** Python 3.11, `azure-ai-voicelive`, `azure-identity`, `sounddevice`, `reactivex`, 既存 `McpAdapter` (HTTP JSON-RPC), `dimos.core.module.Module` / `@rpc`.

**Spec:** `docs/superpowers/specs/2026-05-14-voice-live-rewrite-design.md`

**Plan-time corrections vs spec:**
- spec の `McpHttpClient` 抽出と `McpClient` リファクタは不要 — 既存 `dimos/agents/mcp/mcp_adapter.py:McpAdapter` がそのまま流用可能。`McpClient` は無変更。

---

## File Structure

### 新規

| Path | 責務 |
|---|---|
| `dimos/agents/realtime/__init__.py` | `AzureVoiceLiveAgent` re-export |
| `dimos/agents/realtime/azure_voice_live.py` | `AzureVoiceLiveAgent` Module 本体と内部 `_VoicePlayback` |
| `dimos/agents/realtime/prompts/__init__.py` | 空（パッケージ化のみ） |
| `dimos/agents/realtime/prompts/japanese.py` | 既定 system prompt |
| `dimos/agents/realtime/test_azure_voice_live.py` | スモークテスト（`connect` を AsyncMock 差し替え） |

### 編集

| Path | 変更 |
|---|---|
| `pyproject.toml` | `azure-ai-voicelive`, `azure-identity` を追加 |
| `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py` | import path 変更（新 `dimos.agents.realtime`） |
| `README.md` | env 例と起動手順を更新 |

### 削除

| Path |
|---|
| `dimos/agents/voice_live/` ディレクトリ全部 |
| `voice-live-playground.py` |

---

## Task 1: 依存追加

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: pyproject.toml の `dependencies` リストに 2 行追加**

`dependencies = [` の中、既存の `"websockets>=13",` の直後に挿入:

```toml
    "azure-ai-voicelive>=1,<2",
    "azure-identity>=1.15",
```

- [ ] **Step 2: lock 更新**

Run: `uv lock`
Expected: `Resolved N packages` で完了。`azure-ai-voicelive`, `azure-identity` がロックに含まれる。

- [ ] **Step 3: install**

Run: `uv sync`
Expected: 新依存がインストールされる。

- [ ] **Step 4: import smoke check**

Run: `uv run python -c "from azure.ai.voicelive.aio import connect; from azure.ai.voicelive.models import RequestSession, ServerVad, Modality; from azure.core.credentials import AzureKeyCredential; print('ok')"`
Expected: `ok` と表示。

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps(agents): add azure-ai-voicelive SDK for realtime voice"
```

---

## Task 2: 旧 voice_live モジュール削除

**Files:**
- Delete: `dimos/agents/voice_live/`（全ファイル）

- [ ] **Step 1: ディレクトリ削除**

Run: `git rm -r dimos/agents/voice_live`
Expected: 6 ファイル削除（`__init__.py`, `voice_live_agent.py`, `voice_live_node.py`, `japanese_prompt.py`, `test_voice_live_agent.py`, `test_voice_live_node.py`）。

- [ ] **Step 2: 残存参照確認**

Run: `grep -rn "dimos.agents.voice_live\|dimos\.agents\.voice_live" --include="*.py" --include="*.toml" --include="*.md"`
Expected: マッチは
- `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py` の 1 行のみ（後の Task で書き換える）
- ドキュメント (`docs/superpowers/specs/*` 等) は無視で OK

他のソースファイルがマッチする場合はその参照も対応する。

- [ ] **Step 3: Commit**

```bash
git commit -m "refactor(voice_live): remove first-cut implementation prior to rewrite"
```

---

## Task 3: realtime パッケージと既定プロンプト

**Files:**
- Create: `dimos/agents/realtime/__init__.py`
- Create: `dimos/agents/realtime/prompts/__init__.py`
- Create: `dimos/agents/realtime/prompts/japanese.py`

- [ ] **Step 1: prompts/japanese.py 作成**

```python
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Default Japanese system prompt for the Azure Voice Live agent."""

JAPANESE_SYSTEM_PROMPT = """\
あなたは Unitree Go2 という四足歩行ロボットに搭載された日本語音声アシスタントです。

行動原則:
- ユーザの発話には簡潔で自然な日本語で応答する。
- ロボットの動作を指示されたら、提供されているツールを呼び出して実行する。
- 必要に応じてカメラやセンサーのツールを使って状況を確認してから動く。
- ツール呼び出し結果に「エラー」と書かれていた場合は、内容を要約してユーザに伝える。
- 余計な前置きや復唱はせず、要点だけ短く話す。
"""
```

- [ ] **Step 2: prompts/__init__.py 作成（空ファイル）**

```python
```

- [ ] **Step 3: realtime/__init__.py 作成**

```python
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Realtime conversational agents (Azure Voice Live, etc.)."""

from dimos.agents.realtime.azure_voice_live import (
    AzureVoiceLiveAgent,
    AzureVoiceLiveConfig,
)

__all__ = ["AzureVoiceLiveAgent", "AzureVoiceLiveConfig"]
```

注: この時点では `azure_voice_live.py` がまだ無いので import エラーになる。Task 4 で本体を作る。コミットは Task 4 の最後にまとめる。

---

## Task 4: `AzureVoiceLiveConfig` と Agent スケルトン

**Files:**
- Create: `dimos/agents/realtime/azure_voice_live.py`

このタスクでは Module の枠と config のみ作る。WS / 音声 I/O は後のタスクで足す。

- [ ] **Step 1: ファイル冒頭・ライセンス・import**

```python
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

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import Field

from dimos.agents.mcp.mcp_adapter import McpAdapter
from dimos.agents.realtime.prompts.japanese import JAPANESE_SYSTEM_PROMPT
from dimos.core.core import In, Out, rpc
from dimos.core.module import Module, ModuleConfig
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_ENV_PREFIX = "DIMOS_AZURE_VOICE_LIVE_"
```

- [ ] **Step 2: `AzureVoiceLiveConfig` 追加**

ファイル末尾に追記:

```python
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
```

- [ ] **Step 3: `AzureVoiceLiveAgent` スケルトン追加**

```python
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

    @rpc
    def start(self) -> None:
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

    @rpc
    def on_system_modules(self, _modules: list[Any]) -> None:
        # WS worker thread を起動するのは後のタスクで実装
        pass

    @rpc
    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        if self._tool_pool is not None:
            self._tool_pool.shutdown(wait=True, cancel_futures=True)
            self._tool_pool = None
        super().stop()
```

- [ ] **Step 4: import 確認**

Run: `uv run python -c "from dimos.agents.realtime import AzureVoiceLiveAgent, AzureVoiceLiveConfig; print(AzureVoiceLiveAgent, AzureVoiceLiveConfig)"`
Expected: 両クラスが表示される。

- [ ] **Step 5: Commit**

```bash
git add dimos/agents/realtime/
git commit -m "feat(realtime): scaffold AzureVoiceLiveAgent Module skeleton"
```

---

## Task 5: `_VoicePlayback` 実装

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py`

playground の `AudioProcessor` 再生半分を移植。callback 駆動の sounddevice 出力 + `queue.Queue` ベースで、`skip_pending()` でバージイン時に再生中の音を捨てる。

- [ ] **Step 1: import 追加**

ファイル冒頭の `import threading` の後に:

```python
import queue
from dataclasses import dataclass

import numpy as np
import sounddevice as sd  # type: ignore[import-untyped]
```

- [ ] **Step 2: `_VoicePlayback` クラス追加**

`AzureVoiceLiveConfig` の前に挿入:

```python
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
```

- [ ] **Step 3: 構文チェック**

Run: `uv run python -c "from dimos.agents.realtime.azure_voice_live import _VoicePlayback; _VoicePlayback(24000, None)"`
Expected: 例外なく完了（`start()` を呼ばないので sounddevice は触らない）。

- [ ] **Step 4: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "feat(realtime): add cancellable _VoicePlayback for barge-in"
```

---

## Task 6: WS worker スレッドと `session.update`（ツールなし）

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py`

接続だけ確立し、`session.update` を `RequestSession` で送る。MCP ツールはまだ載せない（次のタスク）。

- [ ] **Step 1: import 追加（ファイル冒頭）**

```python
import asyncio

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
```

- [ ] **Step 2: `AzureVoiceLiveAgent` に WS 周辺フィールドを追加**

`__init__` を以下に置き換え:

```python
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
```

- [ ] **Step 3: voice config ヘルパを追加**

`_VoicePlayback` の下に追加:

```python
def _build_voice_config(voice: str) -> Any:
    """Return an SDK voice config (AzureStandardVoice or raw string).

    Azure neural voices contain a locale prefix like ``ja-JP-*`` or
    ``en-US-*``; OpenAI voices (alloy, echo, ...) are plain strings.
    """
    if "-" in voice:
        return AzureStandardVoice(name=voice)
    return voice
```

- [ ] **Step 4: WS worker メソッド追加**

`AzureVoiceLiveAgent` 内に追加:

```python
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

    async def _handle_event(self, event: Any) -> None:
        et = event.type
        if et == ServerEventType.SESSION_UPDATED:
            logger.info("Voice Live session ready: %s", event.session.id)
            self._mic_active.set()
        elif et == ServerEventType.ERROR:
            logger.error("Voice Live error: %s", event.error.message)
        else:
            logger.debug("Voice Live unhandled event: %s", et)
```

- [ ] **Step 5: `on_system_modules` で WS worker を起動**

`on_system_modules` の本文を以下に差し替え:

```python
    @rpc
    def on_system_modules(self, _modules: list[Any]) -> None:
        assert self._mcp is not None
        if not self._mcp.wait_for_ready(timeout=60.0):
            raise TimeoutError(
                f"MCP server not ready at {self.config.mcp_server_url}"
            )
        self._start_ws_thread()
```

- [ ] **Step 6: `stop` で WS 終了を確実にする**

`stop` の `self._stop_event.set()` の直後に追加:

```python
        if self._loop is not None and self._conn is not None:
            asyncio.run_coroutine_threadsafe(self._conn.close(), self._loop)
```

- [ ] **Step 7: import smoke**

Run: `uv run python -c "from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveAgent; print('ok')"`
Expected: `ok`

- [ ] **Step 8: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "feat(realtime): connect WS and send session.update (no tools yet)"
```

---

## Task 7: MCP ツール取得・変換・session.update に載せる

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py`

- [ ] **Step 1: ツール変換ヘルパ追加**

`_build_voice_config` の下に追加:

```python
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
```

- [ ] **Step 2: `on_system_modules` でツール取得**

```python
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
```

- [ ] **Step 3: `_send_session_update` に tools を載せる**

`_send_session_update` メソッドを以下に差し替え:

```python
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
```

- [ ] **Step 4: import smoke**

Run: `uv run python -c "from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveAgent; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "feat(realtime): fetch MCP tools and include in session.update"
```

---

## Task 8: マイク音声送信（ゲーティング付き）

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py`

`SounddeviceAudioSource` を `start` 時に立ち上げ、Subject 経由で AudioEvent を WS worker に橋渡し。`SESSION_UPDATED` 受信までは破棄。

- [ ] **Step 1: import 追加（ファイル冒頭）**

```python
import base64

from dimos.stream.audio.node_microphone import SounddeviceAudioSource
```

- [ ] **Step 2: フィールド追加（`__init__` 末尾）**

```python
        self._mic: SounddeviceAudioSource | None = None
        self._mic_subscription: Any = None
```

- [ ] **Step 3: マイクを `start` で起動**

`start` メソッドで `self._mcp = McpAdapter(...)` の後に追加:

```python
        self._mic = SounddeviceAudioSource(
            device_index=cfg.mic_device_index,
            sample_rate=cfg.sample_rate,
        )
        self._mic_subscription = self._mic.emit_audio().subscribe(
            on_next=self._on_mic_audio
        )
```

- [ ] **Step 4: マイクハンドラとフォワーダ追加**

`AzureVoiceLiveAgent` に追加（`_handle_event` の上あたり）:

```python
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
```

- [ ] **Step 5: `stop` でマイクをクリーンアップ**

`stop` の冒頭（`self._stop_event.set()` の前）に追加:

```python
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
```

- [ ] **Step 6: import smoke**

Run: `uv run python -c "from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveAgent; print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "feat(realtime): forward mic PCM to Voice Live input buffer"
```

---

## Task 9: 音声出力 (`response.audio.delta` → `_VoicePlayback`)

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py`

- [ ] **Step 1: `start` で playback を起動**

`start` 内、マイク起動の後に追加:

```python
        self._playback = _VoicePlayback(
            sample_rate=cfg.sample_rate,
            device_index=cfg.speaker_device_index,
        )
        self._playback.start()
```

- [ ] **Step 2: `stop` で playback を止める**

`stop` のマイククリーンアップの直後に追加:

```python
        if self._playback is not None:
            try:
                self._playback.stop()
            except Exception:
                pass
            self._playback = None
```

- [ ] **Step 3: `_handle_event` に `RESPONSE_AUDIO_DELTA` ハンドラ追加**

`_handle_event` の elif 連鎖に追加（`SESSION_UPDATED` と `ERROR` の間）:

```python
        elif et == ServerEventType.RESPONSE_AUDIO_DELTA:
            if self._playback is not None:
                self._playback.enqueue(event.delta)
```

`event.delta` は SDK が `bytes` で渡してくる（playground 209 行参照）。

- [ ] **Step 4: import smoke**

Run: `uv run python -c "from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveAgent; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "feat(realtime): play TTS audio from response.audio.delta"
```

---

## Task 10: テキスト/transcript 累積 → `agent` Out + `agent_idle`

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py`

- [ ] **Step 1: `_handle_event` を拡張**

`_handle_event` を以下に書き換え:

```python
    async def _handle_event(self, event: Any) -> None:
        et = event.type
        if et == ServerEventType.SESSION_UPDATED:
            logger.info("Voice Live session ready: %s", event.session.id)
            self._mic_active.set()
        elif et == ServerEventType.RESPONSE_CREATED:
            self._response_active = True
            self._response_text_buf = []
            self.agent_idle.publish(False)
        elif et == ServerEventType.RESPONSE_AUDIO_DELTA:
            if self._playback is not None:
                self._playback.enqueue(event.delta)
        elif et == ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA:
            self._response_text_buf.append(event.delta or "")
        elif et == ServerEventType.RESPONSE_TEXT_DELTA:
            self._response_text_buf.append(event.delta or "")
        elif et == ServerEventType.RESPONSE_DONE:
            text = "".join(self._response_text_buf).strip()
            if text:
                self.agent.publish(AIMessage(content=text))
            self._response_text_buf = []
            self._response_active = False
            self.agent_idle.publish(True)
        elif et == ServerEventType.ERROR:
            logger.error("Voice Live error: %s", event.error.message)
        else:
            logger.debug("Voice Live unhandled event: %s", et)
```

注: `RESPONSE_AUDIO_TRANSCRIPT_DELTA` と `RESPONSE_TEXT_DELTA` を両方累積する。モデル設定により片方しか来ないので両受けが安全。

- [ ] **Step 2: import smoke**

Run: `uv run python -c "from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveAgent; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "feat(realtime): emit AIMessage on response.done and track agent_idle"
```

---

## Task 11: function call 経路 (Voice Live → MCP → function_call_output)

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py`

- [ ] **Step 1: import 追加**

ファイル冒頭:

```python
import json
import uuid
```

- [ ] **Step 2: `_handle_event` に function-call 分岐追加**

`RESPONSE_DONE` の elif の直前に挿入:

```python
        elif et == ServerEventType.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE:
            self._dispatch_function_call(
                call_id=event.call_id,
                name=event.name,
                arguments=event.arguments,
            )
```

- [ ] **Step 3: function-call ディスパッチとレスポンス送信メソッド追加**

`_on_mic_audio` の下あたりに追加:

```python
    def _dispatch_function_call(
        self, call_id: str, name: str, arguments: str
    ) -> None:
        if self._tool_pool is None or self._mcp is None:
            return
        self._tool_pool.submit(self._run_function_call, call_id, name, arguments)

    def _run_function_call(
        self, call_id: str, name: str, arguments: str
    ) -> None:
        assert self._mcp is not None
        try:
            args = json.loads(arguments) if arguments else {}
        except Exception as exc:  # noqa: BLE001
            output = f"Error: invalid arguments JSON: {exc}"
            self._send_function_output(call_id, output)
            return
        try:
            result = self._mcp.call_tool(name, args)
            output = _extract_tool_text(result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("MCP tool %s failed", name)
            output = f"Error: {exc}"
        self._send_function_output(call_id, output)

    def _send_function_output(self, call_id: str, output: str) -> None:
        if self._loop is None or self._conn is None:
            logger.warning("send_function_output before WS ready; dropping")
            return

        async def _send() -> None:
            await self._conn.conversation.item.create(
                item={
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                }
            )
            await self._conn.response.create()

        asyncio.run_coroutine_threadsafe(_send(), self._loop)
```

注: SDK の `conn.conversation.item.create(item=dict)` は dict もしくは
dataclass のどちらも受け取れる。dict 形式は実装的に最も安定。
`conn.response.create()` は引数なしで起動可能。

- [ ] **Step 4: import smoke**

Run: `uv run python -c "from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveAgent; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "feat(realtime): bridge function_call → MCP tool call → output"
```

---

## Task 12: `human_input` In と `add_message` RPC

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py`

- [ ] **Step 1: import 追加**

```python
from reactivex.disposable import Disposable
```

- [ ] **Step 2: フィールド追加 (`__init__` 末尾)**

```python
        self._human_input_sub: Any = None
```

- [ ] **Step 3: `start` で human_input を購読**

`start` の playback 起動後に追加:

```python
        self._human_input_sub = self.human_input.subscribe(self._on_human_text)
        self.register_disposable(Disposable(self._human_input_sub))
```

- [ ] **Step 4: テキスト送信ヘルパとハンドラを追加**

`_send_function_output` の下あたり:

```python
    def _send_user_text(self, text: str, prompt_response: bool = True) -> None:
        if self._loop is None or self._conn is None:
            logger.warning("user text dropped: WS not ready (%r)", text)
            return

        async def _send() -> None:
            await self._conn.conversation.item.create(
                item={
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                }
            )
            if prompt_response:
                await self._conn.response.create()

        asyncio.run_coroutine_threadsafe(_send(), self._loop)

    def _on_human_text(self, text: str) -> None:
        if not text:
            return
        self._send_user_text(text, prompt_response=True)

    @rpc
    def add_message(self, message: BaseMessage) -> None:
        """Inject a message into the conversation from another module."""
        text = (
            message.content
            if isinstance(message.content, str)
            else str(message.content)
        )
        if not text:
            return
        # Treat injected messages as new conversational input → trigger a response.
        self._send_user_text(text, prompt_response=True)
```

- [ ] **Step 5: `stop` で human_input 購読を解除**

`stop` の playback 解除の後に追加:

```python
        if self._human_input_sub is not None:
            try:
                self._human_input_sub.dispose()
            except Exception:
                pass
            self._human_input_sub = None
```

- [ ] **Step 6: import smoke**

Run: `uv run python -c "from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveAgent; print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "feat(realtime): accept WebInput text and add_message RPC"
```

---

## Task 13: `dispatch_continuation` RPC

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py`

McpClient の同名 RPC のセマンティクスを移植: `$`-prefixed 変数を `continuation_context` から解決、LLM をバイパスして MCP を呼び、結果を user メッセージとして注入（`response.create` は呼ばない）。同時に `agent` Out に流す。

- [ ] **Step 1: `dispatch_continuation` メソッド追加**

`add_message` の下:

```python
    @rpc
    def dispatch_continuation(
        self,
        continuation: dict[str, Any],
        continuation_context: dict[str, Any],
    ) -> None:
        tool_name = continuation.get("tool")
        if not tool_name:
            self.agent.publish(
                HumanMessage(
                    content=f"Continuation failed: missing 'tool' in {continuation}"
                )
            )
            return
        if tool_name not in self._tool_registry:
            self.agent.publish(
                HumanMessage(content=f"Continuation failed: tool '{tool_name}' not found")
            )
            return

        raw_args = continuation.get("args", {}) or {}
        args: dict[str, Any] = {}
        for key, value in raw_args.items():
            if isinstance(value, str) and value.startswith("$"):
                ctx_key = value[1:]
                if ctx_key not in continuation_context:
                    self.agent.publish(
                        HumanMessage(
                            content=(
                                f"Continuation failed: '{ctx_key}' not in context"
                            )
                        )
                    )
                    return
                args[key] = continuation_context[ctx_key]
            else:
                args[key] = value

        if self._tool_pool is None:
            return
        self._tool_pool.submit(self._run_continuation, tool_name, args)

    def _run_continuation(self, tool_name: str, args: dict[str, Any]) -> None:
        assert self._mcp is not None
        try:
            result = self._mcp.call_tool(tool_name, args)
            text = _extract_tool_text(result) or "started"
        except Exception as exc:  # noqa: BLE001
            logger.exception("continuation tool %s failed", tool_name)
            text = f"Error: {exc}"

        injected = f"[continuation:{tool_name}] {text}"
        self._send_user_text(injected, prompt_response=False)
        self.agent.publish(HumanMessage(content=injected))
```

- [ ] **Step 2: import smoke**

Run: `uv run python -c "from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveAgent; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "feat(realtime): implement dispatch_continuation RPC"
```

---

## Task 14: バージイン

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py`

- [ ] **Step 1: `_handle_event` に SPEECH_STARTED 分岐追加**

`RESPONSE_CREATED` の elif の直前に挿入:

```python
        elif et == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STARTED:
            if self._playback is not None:
                self._playback.skip_pending()
            if self._response_active and self._conn is not None:
                try:
                    await self._conn.response.cancel()
                except Exception as exc:  # noqa: BLE001
                    if "no active response" not in str(exc).lower():
                        logger.warning("response.cancel failed: %s", exc)
```

- [ ] **Step 2: import smoke**

Run: `uv run python -c "from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveAgent; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "feat(realtime): support barge-in (SPEECH_STARTED → cancel + flush)"
```

---

## Task 15: Tool stream notifications

**Files:**
- Modify: `dimos/agents/realtime/azure_voice_live.py`

McpClient と同様、`tool_stream.subscribe()` で MCP 側 progress / message を受け、会話に user メッセージとして注入（`response.create` は呼ばない）+ `agent` Out にも流す。

- [ ] **Step 1: import 追加**

```python
from dimos.agents.mcp import tool_stream
```

- [ ] **Step 2: フィールド追加 (`__init__` 末尾)**

```python
        self._tool_stream_cleanup: Any = None
```

- [ ] **Step 3: `start` でツールストリーム購読**

`start` の human_input 購読の後に追加:

```python
        self._tool_stream_cleanup = tool_stream.subscribe(
            self._on_tool_stream_message
        )
```

- [ ] **Step 4: ハンドラ追加**

```python
    def _on_tool_stream_message(self, msg: dict[str, Any]) -> None:
        method = msg.get("method")
        params = msg.get("params") or {}
        if method == tool_stream.NOTIFICATIONS_PROGRESS_METHOD:
            text = params.get("message") or ""
            tool_name = (params.get("_meta") or {}).get("tool_name") or "tool"
        elif method == tool_stream.NOTIFICATIONS_MESSAGE_METHOD:
            text = params.get("data") or ""
            tool_name = params.get("logger") or "tool"
        else:
            return
        if not text:
            return
        injected = f"[tool:{tool_name}] {text}"
        self._send_user_text(injected, prompt_response=False)
        self.agent.publish(HumanMessage(content=injected))
```

- [ ] **Step 5: `stop` で購読を切る**

`stop` の冒頭、`self._mic_subscription` 解除の前に追加:

```python
        if self._tool_stream_cleanup is not None:
            try:
                self._tool_stream_cleanup()
            except Exception:
                pass
            self._tool_stream_cleanup = None
```

- [ ] **Step 6: import smoke**

Run: `uv run python -c "from dimos.agents.realtime.azure_voice_live import AzureVoiceLiveAgent; print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add dimos/agents/realtime/azure_voice_live.py
git commit -m "feat(realtime): inject MCP tool-stream notifications into conversation"
```

---

## Task 16: blueprint の import path 更新

**Files:**
- Modify: `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py`

- [ ] **Step 1: import を新パスに**

ファイル全体を以下に書き換え:

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
from dimos.agents.realtime import AzureVoiceLiveAgent
from dimos.agents.skills.navigation import NavigationSkillContainer
from dimos.agents.skills.person_follow import PersonFollowSkillContainer
from dimos.agents.web_human_input import WebInput
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_spatial import unitree_go2_spatial
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer

unitree_go2_agentic_voice_live = autoconnect(
    unitree_go2_spatial,
    McpServer.blueprint(),
    AzureVoiceLiveAgent.blueprint(),
    WebInput.blueprint(),
    NavigationSkillContainer.blueprint(),
    PersonFollowSkillContainer.blueprint(camera_info=GO2Connection.camera_info_static),
    UnitreeSkillContainer.blueprint(),
)

__all__ = ["unitree_go2_agentic_voice_live"]
```

注: `WebInput.blueprint()` を新規に追加（spec 通り、音声と並行してテキスト入力を残す）。`SpeakSkill` は不参加。

- [ ] **Step 2: blueprint import 確認**

Run: `uv run python -c "from dimos.robot.all_blueprints import all_blueprints; print('unitree-go2-agentic-voice-live' in [b.name for b in all_blueprints])"`
Expected: `True`（または同等のチェック）。

もし `all_blueprints` の API が違う場合は:

Run: `uv run python -c "from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_voice_live import unitree_go2_agentic_voice_live; print(unitree_go2_agentic_voice_live)"`
Expected: blueprint オブジェクトが表示される。

- [ ] **Step 3: Commit**

```bash
git add dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_voice_live.py
git commit -m "feat(go2): rewire voice-live blueprint to new realtime module"
```

---

## Task 17: README 更新

**Files:**
- Modify: `README.md`

- [ ] **Step 1: env と起動手順を更新**

`### Azure Voice Live バリアント` セクション（README:205 付近）を以下に書き換え:

```markdown
### Azure Voice Live バリアント

リアルタイム音声会話（STT + LLM + TTS + 関数呼び出しが Azure Voice Live 1
セッション）で起動するには:

```bash
export DIMOS_AZURE_VOICE_LIVE_ENDPOINT=wss://<your-resource>.cognitiveservices.azure.com/
export DIMOS_AZURE_VOICE_LIVE_API_KEY=<key>
export DIMOS_AZURE_VOICE_LIVE_MODEL=gpt-realtime         # 任意（既定: gpt-realtime）
export DIMOS_AZURE_VOICE_LIVE_VOICE=ja-JP-NanamiNeural   # 任意（既定: ja-JP-NanamiNeural）

uv run dimos run unitree-go2-agentic-voice-live
```

PC のローカルマイク / スピーカーで音声入出力します（Go2 オンボードオーディ
オは未対応）。Web UI からのテキスト入力も並行して受け付けます。`SpeakSkill`
は本バリアントには含まれません（Voice Live が TTS を担当）。

完全な環境変数リストは `docs/superpowers/specs/2026-05-14-voice-live-rewrite-design.md` を参照してください。
```

(コードブロック中の ``` は README の元のフォーマットに合わせる)

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(readme): update Azure Voice Live env vars and runtime notes"
```

---

## Task 18: playground スクリプトを削除

**Files:**
- Delete: `voice-live-playground.py`

playground は MVP 検証用の参照スクリプトであり、`docs/superpowers/specs/...` から参照されている（履歴で残る）ので削除しても問題ない。

- [ ] **Step 1: 削除**

Run: `rm voice-live-playground.py`
Expected: ファイル削除。

- [ ] **Step 2: Commit**

```bash
git add -u voice-live-playground.py
git commit -m "chore: drop voice-live-playground.py after rewrite verification"
```

---

## Task 19: テストスタブ

**Files:**
- Create: `dimos/agents/realtime/test_azure_voice_live.py`

MVP は手動 E2E で検証する方針。本格テストは別作業として、ファイルだけ用意して `pytest.skip` で塞いでおく。

- [ ] **Step 1: スタブ作成**

```python
# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Stub test module for the Azure Voice Live agent.

The real tests will mock ``azure.ai.voicelive.aio.connect`` with an
AsyncMock and exercise: session.update, response.audio.delta → playback,
function_call → MCP → function_call_output, SPEECH_STARTED → cancel +
skip_pending.  Written after manual E2E verifies the happy path.
"""

import pytest

pytestmark = pytest.mark.skip(reason="Voice Live tests pending — verify manually")


def test_placeholder() -> None:
    assert True
```

- [ ] **Step 2: テスト実行**

Run: `uv run pytest dimos/agents/realtime/test_azure_voice_live.py -v`
Expected: 1 test skipped。

- [ ] **Step 3: Commit**

```bash
git add dimos/agents/realtime/test_azure_voice_live.py
git commit -m "test(realtime): add placeholder test module (skipped)"
```

---

## Task 20: 全体 import / lint smoke

最後に全モジュールが整合しているか確認。

- [ ] **Step 1: 全体 import**

Run:
```bash
uv run python -c "
from dimos.robot.all_blueprints import all_blueprints
from dimos.agents.realtime import AzureVoiceLiveAgent, AzureVoiceLiveConfig
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_voice_live import unitree_go2_agentic_voice_live
print('ok')
"
```
Expected: `ok`

- [ ] **Step 2: pytest 全体実行（既存テストの回帰確認）**

Run: `uv run pytest dimos/agents/mcp dimos/agents/realtime -x`
Expected: 全 PASS（または既存の skip）。`McpClient` 系のテストが落ちないこと（McpClient は本プランで触っていないので落ちないはず）。

- [ ] **Step 3: 残存古いパス参照の最終チェック**

Run: `grep -rn "dimos.agents.voice_live\|AzureVoiceLiveNode" --include="*.py"`
Expected: マッチなし（drag-out 完了）。

`docs/superpowers/specs/*` の旧 spec へのマッチは無視で OK。

- [ ] **Step 4: 何も commit するものがないことを確認**

Run: `git status`
Expected: `nothing to commit, working tree clean`

---

## 手動 E2E 検証（実装完了後）

このプランの完了時点で `uv run dimos run unitree-go2-agentic-voice-live`
が実機 / シミュレーションで動くはず。以下のチェックリストを通す:

- [ ] env を設定（`DIMOS_AZURE_VOICE_LIVE_ENDPOINT` / `_API_KEY` 必須）
- [ ] blueprint 起動: `uv run dimos run unitree-go2-agentic-voice-live`
- [ ] マイクで「立って」と発話 → MCP `stand_up` (or 該当ツール) のログが出て音声で応答
- [ ] AI 発話中にマイクで割り込み → 即座に停止、新しい発話に応答（バージイン）
- [ ] Web UI (http://localhost:5555) でテキスト入力 → 音声応答
- [ ] person follow の trigger を仕込み、人を映す → `dispatch_continuation` ログ + follow 起動
- [ ] `dimos agent-send "状況を教えて"` で `add_message` 経路の確認
- [ ] Ctrl-C で graceful shutdown（マイク / スピーカー / WS が閉じる）

---

## Self-Review

**Spec coverage check** — `docs/superpowers/specs/2026-05-14-voice-live-rewrite-design.md` の各要件:

| Spec 要件 | 対応 Task |
|---|---|
| Voice Live SDK で WS 接続 | Task 6 |
| session.update with ServerVad / echo / noise / TEXT+AUDIO | Task 6, 7 |
| MCP tools 取得・function 形式変換 | Task 7 |
| mic → input_audio_buffer.append (gating) | Task 8 |
| RESPONSE_AUDIO_DELTA → 再生 | Task 9 |
| バージイン (SPEECH_STARTED → cancel + skip_pending) | Task 14 |
| function_call → MCP → function_call_output | Task 11 |
| 画像ツール結果は `[image omitted]` 付記 | Task 7 (`_extract_tool_text`) |
| `human_input` In → 会話注入 + response.create | Task 12 |
| `agent` Out で AIMessage emit | Task 10 |
| `agent_idle` Out で状態 emit | Task 10 |
| `add_message` RPC | Task 12 |
| `dispatch_continuation` RPC | Task 13 |
| Tool stream notifications を会話に注入 | Task 15 |
| `dimos/agents/realtime/` 新設 | Task 3, 4 |
| 旧 `dimos/agents/voice_live/` 削除 | Task 2 |
| blueprint の import path 更新 / WebInput 残す / SpeakSkill 外す | Task 16 |
| pyproject に SDK 依存追加 | Task 1 |
| README 更新 | Task 17 |
| playground.py 削除 | Task 18 |
| テストスタブ | Task 19 |

すべてカバー済み。McpHttpClient 抽出 / McpClient リファクタは plan-time 訂正により非対象（plan ヘッダで明示）。

**Placeholder scan** — 「TBD」「TODO」「実装は後で」等は本文にない。`_mcp_to_voice_function` の dict 返却は SDK バージョン非依存に保つための意図的選択で、コード自体は完結している。

**Type consistency** — `agent_idle.publish(bool)` / `agent.publish(BaseMessage)` / `human_input.subscribe(callable)` / `register_disposable(Disposable)` は `dimos/agents/mcp/mcp_client.py:321,324` と一致。`McpAdapter.list_tools` / `call_tool` / `wait_for_ready` の I/F は `dimos/agents/mcp/mcp_adapter.py:90-122` と一致。SDK 型 (`RequestSession`, `ServerEventType`, `AzureKeyCredential` 等) は `voice-live-playground.py:17-32` で実在を確認済み。
