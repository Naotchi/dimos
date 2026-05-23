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

"""``unitree-go2-agentic-local-tts`` + 3D object detection (fork-local).

Combines the Japanese local-TTS agentic stack
(:mod:`unitree_go2_agentic_local_tts`) with the ``Detection3DModule`` wiring
lifted verbatim from :mod:`unitree_go2_detection` — same ``global_map``
pointcloud remapping and the same LCM transports for detections / annotations
/ scene update / per-detection pointclouds + images.

``Detection3DModule`` is added on top of the prebuilt agentic blueprint, so the
shared ``unitree_go2`` base is deduplicated by ``autoconnect``. The agentic
blueprint disables ``SecurityModule`` (no SpeakSkill satisfier in the
local-TTS path); ``autoconnect`` does not carry that flag across composition,
so it is re-applied here.
"""

from dimos_lcm.foxglove_msgs.ImageAnnotations import (
    ImageAnnotations,
)
from dimos_lcm.foxglove_msgs.SceneUpdate import SceneUpdate

from dimos.core.coordination.blueprints import autoconnect
from dimos.core.transport import LCMTransport
from dimos.experimental.security_demo.security_module import SecurityModule
from dimos.msgs.sensor_msgs.Image import Image
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.msgs.vision_msgs.Detection2DArray import Detection2DArray
from dimos.perception.detection.module3D import Detection3DModule
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts import (
    unitree_go2_agentic_local_tts,
)
from dimos.robot.unitree.go2.connection import GO2Connection

unitree_go2_agentic_local_tts_detection = (
    autoconnect(
        unitree_go2_agentic_local_tts,
        Detection3DModule.blueprint(
            camera_info=GO2Connection.camera_info_static,
        ),
    )
    .remappings(
        [
            (Detection3DModule, "pointcloud", "global_map"),
        ]
    )
    .transports(
        {
            # Detection 3D module outputs
            ("detections", Detection3DModule): LCMTransport(
                "/detector3d/detections", Detection2DArray
            ),
            ("annotations", Detection3DModule): LCMTransport(
                "/detector3d/annotations", ImageAnnotations
            ),
            ("scene_update", Detection3DModule): LCMTransport(
                "/detector3d/scene_update", SceneUpdate
            ),
            ("detected_pointcloud_0", Detection3DModule): LCMTransport(
                "/detector3d/pointcloud/0", PointCloud2
            ),
            ("detected_pointcloud_1", Detection3DModule): LCMTransport(
                "/detector3d/pointcloud/1", PointCloud2
            ),
            ("detected_pointcloud_2", Detection3DModule): LCMTransport(
                "/detector3d/pointcloud/2", PointCloud2
            ),
            ("detected_image_0", Detection3DModule): LCMTransport("/detector3d/image/0", Image),
            ("detected_image_1", Detection3DModule): LCMTransport("/detector3d/image/1", Image),
            ("detected_image_2", Detection3DModule): LCMTransport("/detector3d/image/2", Image),
        }
    )
    .disabled_modules(SecurityModule)
)

__all__ = ["unitree_go2_agentic_local_tts_detection"]
