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
    STT=local ja-tuned Whisper, LLM=cloud (``DIMOS_LLM_MODEL``, default
    ``gpt-4o``), **TTS=local pyopenjtalk**, ja system prompt.
- ``unitree-go2-agentic-voice-live`` (Japanese):
    Azure Voice Live realtime end-to-end (STT+LLM+TTS cloud).

The defining axis vs the other two is **local TTS** — Japanese (via
pyopenjtalk) is implied by the locality choice. Upstream files are not
modified; all fork-specific wiring is in ``*_ja`` helpers.
"""

import os

from dimos.agents.mcp.mcp_client_ja import TimedMcpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.system_prompt_ja import SYSTEM_PROMPT_JA
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.agentic._common_agentic_ja import _common_agentic_ja
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_spatial_bounded import (
    unitree_go2_spatial_bounded,
)

_LLM_MODEL = os.environ.get("DIMOS_LLM_MODEL", "gpt-4o")

unitree_go2_agentic_local_tts = autoconnect(
    unitree_go2_spatial_bounded,
    McpServer.blueprint(),
    TimedMcpClient.blueprint(model=_LLM_MODEL, system_prompt=SYSTEM_PROMPT_JA),
    _common_agentic_ja,
)

__all__ = ["unitree_go2_agentic_local_tts"]
