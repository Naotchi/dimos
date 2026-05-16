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
from dimos.agents.skills.navigation import NavigationSkillContainer
from dimos.agents.skills.person_follow import PersonFollowSkillContainer
from dimos.agents.skills.speak_skill import SpeakSkill
from dimos.agents.web_human_input_ja import JapaneseWebInput
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.blueprints.smart.unitree_go2_spatial import unitree_go2_spatial
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer

# SpeakSkill は SecurityModule の侵入者検知アラート用に必要 (Voice Live の
# 会話 TTS とは別経路)。Voice Live は agent の発話を自前で TTS するため、
# SpeakSkill が agent Out を二重に喋ることはない。
unitree_go2_agentic_voice_live = autoconnect(
    unitree_go2_spatial,
    McpServer.blueprint(),
    AzureVoiceLiveAgent.blueprint(),
    JapaneseWebInput.blueprint(),
    SpeakSkill.blueprint(),
    NavigationSkillContainer.blueprint(),
    PersonFollowSkillContainer.blueprint(camera_info=GO2Connection.camera_info_static),
    UnitreeSkillContainer.blueprint(),
)

__all__ = ["unitree_go2_agentic_voice_live"]
