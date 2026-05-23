#!/usr/bin/env python3
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

"""Cascade variant of unitree-go2-agentic with **local TTS**.

Differences from sibling blueprints:

- ``unitree-go2-agentic`` (original, English):
    STT=local Whisper, LLM=cloud, **TTS=cloud**.
- ``unitree-go2-agentic-local-tts`` (this one, Japanese):
    STT=local ja-tuned Whisper, LLM=swappable via ``DIMOS_LLM_MODEL`` /
    ``DIMOS_LLM_BASE_URL`` / ``DIMOS_LLM_API_KEY`` (any OpenAI-compatible
    endpoint: Azure v1, OpenAI cloud, local vLLM/Ollama on DGX Spark, ...;
    default ``gpt-4o`` on OpenAI), **TTS=local Style-Bert-VITS2**, ja
    system prompt.
- ``unitree-go2-agentic-voice-live`` (Japanese):
    Azure Voice Live realtime end-to-end (STT+LLM+TTS cloud).

The defining axis vs the other two is **local TTS** — Japanese (via
Style-Bert-VITS2, a neural VITS-based engine) is implied by the locality
choice. Upstream files are not modified; all fork-specific wiring is in
``*_ja`` helpers.
"""

from dimos.agents.llm_env_ja import mirror_llm_endpoint_env
from dimos.agents.mcp.mcp_client_ja import TimedMcpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.system_prompt_ja import SYSTEM_PROMPT_JA
from dimos.core.coordination.blueprints import autoconnect
from dimos.experimental.security_demo.security_module import SecurityModule
from dimos.robot.unitree.go2.blueprints.agentic._common_agentic_ja import _common_agentic_ja
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_spatial_bounded import (
    unitree_go2_spatial_bounded,
)

# LLM endpoint wiring: DIMOS_LLM_BASE_URL / DIMOS_LLM_API_KEY → OPENAI_*.
# Called at import (main process, after apply_profile, before worker fork).
# The model string is owned by the profile config.json (TimedMcpClientConfig),
# not baked here. See dimos/agents/llm_env_ja.py.
mirror_llm_endpoint_env()

# SecurityModule は spatial_bounded に含まれるが SpeakSkillSpec satisfier を
# 必要とする。local-tts では LLM 応答テキストを直接 TTS に流す方針なので、
# SpeakSkill 系を一切置かない。よってここでは SecurityModule ごと無効化する。
unitree_go2_agentic_local_tts = autoconnect(
    unitree_go2_spatial_bounded,
    McpServer.blueprint(),
    TimedMcpClient.blueprint(system_prompt=SYSTEM_PROMPT_JA),
    _common_agentic_ja,
).disabled_modules(SecurityModule)

__all__ = ["unitree_go2_agentic_local_tts"]
