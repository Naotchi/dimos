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

from dimos.agents.mcp.mcp_server import McpServer
from dimos.agents.realtime import AzureVoiceLiveAgent
from dimos.agents.realtime.ptt_keyboard import PttKeyboard
from dimos.agents.skills.navigation import NavigationSkillContainer
from dimos.agents.skills.person_follow import PersonFollowSkillContainer
from dimos.core.coordination.blueprints import autoconnect
from dimos.experimental.security_demo.security_module import SecurityModule
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_spatial import unitree_go2_spatial
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer

# SecurityModule は親 spatial blueprint に含まれるが、Voice Live では会話 TTS
# が AzureVoiceLiveAgent 側で完結し、侵入者アラート用の SpeakSkill 経路を
# 持たない（SpeakSkill を出すと excluded_tools でも MCP 二重発話のリスクが
# 残る）。よってここで SecurityModule ごと無効化する。
#
# PttKeyboard は SPACE 押下中だけ AzureVoiceLiveAgent.mic_gate を True にする。
# WebUI 経由の音声入力は使わない（スピーカー出力のエコーで誤発火するため）。
unitree_go2_agentic_voice_live = autoconnect(
    unitree_go2_spatial,
    McpServer.blueprint(),
    AzureVoiceLiveAgent.blueprint(ptt_mode=True),
    PttKeyboard.blueprint(),
    NavigationSkillContainer.blueprint(),
    PersonFollowSkillContainer.blueprint(camera_info=GO2Connection.camera_info_static),
    UnitreeSkillContainer.blueprint(),
).disabled_modules(SecurityModule)

__all__ = ["unitree_go2_agentic_voice_live"]
