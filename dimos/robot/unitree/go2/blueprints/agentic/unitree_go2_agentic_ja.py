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

"""Japanese variant of unitree-go2-agentic.

Uses the bundled pyopenjtalk Japanese TTS, ja-tuned Whisper STT, the Japanese
system prompt, and an env-driven LLM model selector. Upstream files are not
modified.
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

unitree_go2_agentic_ja = autoconnect(
    unitree_go2_spatial_bounded,
    McpServer.blueprint(),
    TimedMcpClient.blueprint(model=_LLM_MODEL, system_prompt=SYSTEM_PROMPT_JA),
    _common_agentic_ja,
)

__all__ = ["unitree_go2_agentic_ja"]
