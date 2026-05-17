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

"""Japanese common agentic skill set: mirrors _common_agentic with JA TTS/STT.

Human input is a local mic gated by a PTT hotkey (default F9) rather than
the WebUI mic, so the WebUI is no longer part of this bundle. See
LocalMicrophoneJa (PTT-driven utterance recorder) and WhisperHumanInputJa
(STT → /human_input bridge) — autoconnect wires them via the shared
``mic_gate`` and ``mic_utterance`` port names.
"""

from dimos.agents.local_microphone_ja import LocalMicrophoneJa
from dimos.agents.realtime.ptt_keyboard import PttKeyboard
from dimos.agents.skills.navigation import NavigationSkillContainer
from dimos.agents.skills.person_follow import PersonFollowSkillContainer
from dimos.agents.skills.speak_skill_ja import JapaneseSpeakSkill
from dimos.agents.whisper_human_input_ja import WhisperHumanInputJa
from dimos.core.coordination.blueprints import autoconnect
from dimos.robot.unitree.go2.connection import GO2Connection
from dimos.robot.unitree.unitree_skill_container import UnitreeSkillContainer

_common_agentic_ja = autoconnect(
    NavigationSkillContainer.blueprint(),
    PersonFollowSkillContainer.blueprint(camera_info=GO2Connection.camera_info_static),
    UnitreeSkillContainer.blueprint(),
    LocalMicrophoneJa.blueprint(),
    WhisperHumanInputJa.blueprint(),
    PttKeyboard.blueprint(),
    JapaneseSpeakSkill.blueprint(),
)

__all__ = ["_common_agentic_ja"]
