# Copyright 2026 Dimensional Inc.
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

from __future__ import annotations

import math
from typing import Any

from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.detection.detectors.base import Detector
from dimos.perception.detection.type.detection2d.bbox import Detection2DBBox
from dimos.perception.detection.type.detection2d.imageDetections2D import ImageDetections2D
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_ROW_QUERY = "each horizontal shelf row"


class ShelfRowDetector(Detector):
    """Composite ``Detector``: VLM shelf-row grounding -> per-row crop -> dense.

    1. (low frequency, cached) ask the local Qwen-VL for shelf-row boxes;
    2. crop each row;
    3. run the dense detector (RT-DETRv4) on each crop;
    4. remap crop-local boxes back to full-image coordinates.

    The VLM call is slow (seconds), so grounding is cached after the first
    frame. Call :meth:`reset_rows` to force re-grounding (e.g. when the robot
    moves to a new shelf). ``vlm`` and ``dense_detector`` are injectable for
    testing; defaults are constructed lazily so importing this module is cheap.
    """

    def __init__(
        self,
        vlm: Any | None = None,
        dense_detector: Detector | None = None,
        reground_each_frame: bool = False,
    ) -> None:
        self._vlm = vlm
        self._dense = dense_detector
        self._reground = reground_each_frame
        self._rows: list[tuple[int, int, int, int]] | None = None

    @property
    def vlm(self) -> Any:
        if self._vlm is None:
            from dimos.perception.shelf.regions.local_qwen_vl import LocalQwenVlModel

            self._vlm = LocalQwenVlModel()
        return self._vlm

    @property
    def dense(self) -> Detector:
        if self._dense is None:
            from dimos.perception.shelf.detection.rtdetrv4_detector import RTDetrv4Detector

            self._dense = RTDetrv4Detector()
        return self._dense

    def reset_rows(self) -> None:
        """Drop the cached shelf-row layout so the next frame re-grounds."""
        self._rows = None

    def _ground_rows(self, image: Image) -> list[tuple[int, int, int, int]]:
        h, w = image.shape[:2]
        dets = self.vlm.query_detections(image, _ROW_QUERY)
        rows: list[tuple[int, int, int, int]] = []
        for det in dets:
            x1, y1, x2, y2 = det.bbox
            if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
                continue
            ix1 = max(0, min(int(x1), w - 1))
            iy1 = max(0, min(int(y1), h - 1))
            ix2 = max(ix1 + 1, min(int(x2), w))
            iy2 = max(iy1 + 1, min(int(y2), h))
            rows.append((ix1, iy1, ix2, iy2))
        if not rows:
            logger.warning(
                "Shelf-row grounding returned no usable rows; "
                "falling back to the whole image as a single row."
            )
            rows = [(0, 0, w, h)]  # fallback: whole image as one row
        return rows

    def process_image(self, image: Image) -> ImageDetections2D:
        if self._rows is None or self._reground:
            self._rows = self._ground_rows(image)

        merged: ImageDetections2D = ImageDetections2D(image)
        for row_id, (x1, y1, x2, y2) in enumerate(self._rows):
            crop = image.crop(x1, y1, x2 - x1, y2 - y1)
            for det in self.dense.process_image(crop):
                bx1, by1, bx2, by2 = det.bbox
                # image is the full frame; bbox is remapped from crop-local to
                # full-image coords so downstream 3D projection uses full intrinsics.
                merged.detections.append(
                    Detection2DBBox(
                        bbox=(bx1 + x1, by1 + y1, bx2 + x1, by2 + y1),
                        track_id=det.track_id,
                        class_id=det.class_id,
                        confidence=det.confidence,
                        name=f"{det.name} (row{row_id})",
                        ts=image.ts,
                        image=image,
                    )
                )
        return merged
