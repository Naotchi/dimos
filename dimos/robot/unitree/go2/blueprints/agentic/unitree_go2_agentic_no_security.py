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

"""unitree-go2-agentic variant without SecurityModule.

SecurityModule pulls in EdgeTAM which requires a CUDA GPU, so the default
agentic blueprint fails to deploy on non-CUDA hosts. This variant swaps in
the fork-local SecurityModule-free spatial blueprint; agent tools other than
`start_security_patrol` / `stop_security_patrol` remain available.
"""

from dimos.agents.mcp.mcp_client import McpClient
from dimos.agents.mcp.mcp_server import McpServer
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.agentic._common_agentic import _common_agentic
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_spatial_no_security import (
    unitree_go2_spatial_no_security,
)

unitree_go2_agentic_no_security = autoconnect(
    unitree_go2_spatial_no_security,
    McpServer.blueprint(),
    McpClient.blueprint(),
    _common_agentic,
)

__all__ = ["unitree_go2_agentic_no_security"]
