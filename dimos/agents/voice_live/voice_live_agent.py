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

import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic import Field

from dimos.agents.mcp.mcp_adapter import McpAdapter
from dimos.agents.voice_live.japanese_prompt import JAPANESE_SYSTEM_PROMPT
from dimos.agents.voice_live.voice_live_node import AzureVoiceLiveNode
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.stream.audio.node_microphone import SounddeviceAudioSource
from dimos.stream.audio.node_output import SounddeviceAudioOutput
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_REQUIRED_ENVS = [
    "DIMOS_AZURE_VOICE_LIVE_ENDPOINT",
    "DIMOS_AZURE_VOICE_LIVE_API_KEY",
    "DIMOS_AZURE_VOICE_LIVE_MODEL",
    "DIMOS_AZURE_VOICE_LIVE_VOICE",
]


def _env_to_field(env_name: str) -> str:
    """DIMOS_AZURE_VOICE_LIVE_ENDPOINT -> endpoint"""
    return env_name.removeprefix("DIMOS_AZURE_VOICE_LIVE_").lower()


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

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._executor: ThreadPoolExecutor | None = None
        self._mcp: Any = None
        self._node: Any = None
        self._mic: Any = None
        self._speaker: Any = None

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

    @rpc
    def start(self) -> None:
        super().start()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="VoiceLiveTool")
        cfg = self.config
        missing = [name for name in _REQUIRED_ENVS if not getattr(cfg, _env_to_field(name))]
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

    @rpc
    def stop(self) -> None:
        if self._node is not None:
            self._node.stop()
        if self._speaker is not None:
            self._speaker.stop()
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
        super().stop()

    def _handle_tool_call(self, call_id: str, name: str, args_json: str) -> None:
        if self._executor is None:
            return

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
