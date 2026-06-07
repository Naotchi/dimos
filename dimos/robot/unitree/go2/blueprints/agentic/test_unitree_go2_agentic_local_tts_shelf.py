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


def test_shelf_blueprint_importable_and_built():
    from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts_shelf import (
        unitree_go2_agentic_local_tts_shelf,
    )

    assert unitree_go2_agentic_local_tts_shelf is not None


def test_shelf_blueprint_registered():
    from dimos.robot.all_blueprints import all_blueprints

    assert "unitree-go2-agentic-local-tts-shelf" in all_blueprints
