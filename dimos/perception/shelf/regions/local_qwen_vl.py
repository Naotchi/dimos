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

import json
import os
from functools import cached_property
from typing import Any

from openai import OpenAI

from dimos.models.vl.qwen import QwenVlModel
from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.detection.type.detection2d.bbox import Detection2DBBox
from dimos.perception.detection.type.detection2d.imageDetections2D import ImageDetections2D
from dimos.utils.llm_utils import extract_json


def _resolve_endpoint() -> tuple[str, str, str]:
    """Resolve (base_url, model, api_key) from env.

    Prefers SHELF_VLM_* (shelf-specific override) and falls back to the
    project-wide DIMOS_LLM_* variables so the shelf grounding shares the same
    local Qwen as the agent by default.
    """
    base_url = os.getenv("SHELF_VLM_BASE_URL") or os.getenv("DIMOS_LLM_BASE_URL")
    model = os.getenv("SHELF_VLM_MODEL") or os.getenv("DIMOS_LLM_MODEL")
    api_key = (
        os.getenv("SHELF_VLM_API_KEY")
        or os.getenv("DIMOS_LLM_API_KEY")
        or "lm-studio"  # LM Studio ignores the key; OpenAI SDK requires a non-empty string
    )
    if not base_url:
        raise ValueError("SHELF_VLM_BASE_URL or DIMOS_LLM_BASE_URL must be set")
    if not model:
        raise ValueError("SHELF_VLM_MODEL or DIMOS_LLM_MODEL must be set")
    return base_url, model, api_key


_GROUNDING_PROMPT_TEMPLATE = (
    "Detect every {target} in this image. "
    "Return one bounding box per match as a JSON array, no other text:\n"
    '[{{"bbox_2d": [x1, y1, x2, y2], "label": "..."}}]\n'
    "Coordinates must be normalized to the 0-1000 range. If there are none, return []."
)


class LocalQwenVlModel(QwenVlModel):
    """``QwenVlModel`` pointed at a local OpenAI-compatible endpoint (LM Studio).

    Two overrides vs the upstream cloud model:

    * ``_client`` targets the local endpoint (``SHELF_VLM_*`` / ``DIMOS_LLM_*``).
    * ``query_detections`` parses the local Qwen's native ``bbox_2d`` / 0-1000
      normalized grounding format (the upstream base prompt asks for pixel
      ``[label, x1, y1, x2, y2]`` lists, which this model ignores).

    Everything else (``query``, ``query_json``, image prep) is reused from base.
    """

    def __init__(self, **kwargs: Any) -> None:
        base_url, model, api_key = _resolve_endpoint()
        self._base_url = base_url
        self._api_key = api_key
        kwargs.setdefault("model_name", model)
        kwargs.setdefault("api_key", api_key)
        super().__init__(**kwargs)

    @cached_property
    def _client(self) -> OpenAI:
        return OpenAI(base_url=self._base_url, api_key=self._api_key)

    def query_detections(  # type: ignore[override]
        self, image: Image, query: str = "each horizontal shelf row", **kwargs: Any
    ) -> ImageDetections2D:
        h, w = image.shape[:2]
        result: ImageDetections2D = ImageDetections2D(image)

        prompt = _GROUNDING_PROMPT_TEMPLATE.format(target=query)
        raw = self.query(image, prompt)
        try:
            items = extract_json(raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            return result
        if not isinstance(items, list):
            return result

        for track_id, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            box = item.get("bbox_2d")
            label = str(item.get("label", "shelf row"))
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            try:
                nx1, ny1, nx2, ny2 = [float(v) for v in box]
            except (TypeError, ValueError):
                continue
            bbox = (
                nx1 / 1000.0 * w,
                ny1 / 1000.0 * h,
                nx2 / 1000.0 * w,
                ny2 / 1000.0 * h,
            )
            det = Detection2DBBox(
                bbox=bbox,
                track_id=track_id,
                class_id=-1,
                confidence=1.0,
                name=label,
                ts=image.ts,
                image=image,
            )
            if det.is_valid():
                result.detections.append(det)
        return result
