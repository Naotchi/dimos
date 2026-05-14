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
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_ENV_PREFIX = "DIMOS_AZURE_VOICE_LIVE_"


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
