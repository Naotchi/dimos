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
        missing = [name for name in _REQUIRED_ENVS if not getattr(self.config, _env_to_field(name))]
        if missing:
            raise ValueError(f"Missing required env vars: {', '.join(missing)}")
