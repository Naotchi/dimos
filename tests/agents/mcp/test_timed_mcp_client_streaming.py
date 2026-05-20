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

"""TimedMcpClient declares an agent_text Out[str] port for streaming TTS."""

from __future__ import annotations

from typing import get_args, get_origin, get_type_hints

from dimos.agents.mcp.mcp_client_ja import TimedMcpClient
from dimos.core.stream import Out


def test_timed_mcp_client_declares_agent_text_out_str():
    hints = get_type_hints(TimedMcpClient)
    assert "agent_text" in hints, "TimedMcpClient must declare agent_text port"
    ann = hints["agent_text"]
    assert get_origin(ann) is Out
    assert get_args(ann)[0] is str
