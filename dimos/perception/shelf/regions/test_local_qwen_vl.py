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

from unittest.mock import MagicMock

import numpy as np
import pytest

from dimos.msgs.sensor_msgs.Image import Image, ImageFormat
from dimos.perception.detection.type.detection2d.imageDetections2D import ImageDetections2D
from dimos.perception.shelf.regions.local_qwen_vl import LocalQwenVlModel, _resolve_endpoint

# Local Qwen returns 0-1000 normalized bbox_2d wrapped in a ```json block + chatter.
MOCK_LOCAL_QWEN_RESPONSE = """
Here are the shelf rows:
```json
[
  {"bbox_2d": [0, 0, 1000, 500], "label": "shelf row"},
  {"bbox_2d": [0, 500, 1000, 1000], "label": "shelf row"}
]
```
Let me know if you need more.
"""


def _make_model(monkeypatch):
    monkeypatch.setenv("SHELF_VLM_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("SHELF_VLM_MODEL", "qwen/qwen3.6-35b-a3b")
    monkeypatch.setenv("SHELF_VLM_API_KEY", "x")
    return LocalQwenVlModel()


def test_resolve_endpoint_prefers_shelf_vars(monkeypatch):
    monkeypatch.setenv("SHELF_VLM_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("SHELF_VLM_MODEL", "qwen/qwen3.6-35b-a3b")
    monkeypatch.setenv("SHELF_VLM_API_KEY", "shelf-key")
    monkeypatch.setenv("DIMOS_LLM_BASE_URL", "http://other:9999/v1")
    monkeypatch.setenv("DIMOS_LLM_MODEL", "other-model")
    base_url, model, api_key = _resolve_endpoint()
    assert base_url == "http://localhost:1234/v1"
    assert model == "qwen/qwen3.6-35b-a3b"
    assert api_key == "shelf-key"


def test_resolve_endpoint_falls_back_to_dimos_llm_vars(monkeypatch):
    monkeypatch.delenv("SHELF_VLM_BASE_URL", raising=False)
    monkeypatch.delenv("SHELF_VLM_MODEL", raising=False)
    monkeypatch.delenv("SHELF_VLM_API_KEY", raising=False)
    monkeypatch.setenv("DIMOS_LLM_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("DIMOS_LLM_MODEL", "qwen/qwen3.6-35b-a3b")
    monkeypatch.delenv("DIMOS_LLM_API_KEY", raising=False)
    base_url, model, api_key = _resolve_endpoint()
    assert base_url == "http://localhost:1234/v1"
    assert model == "qwen/qwen3.6-35b-a3b"
    assert api_key == "lm-studio"  # default placeholder for keyless local servers


def test_resolve_endpoint_raises_without_base_url(monkeypatch):
    for var in ("SHELF_VLM_BASE_URL", "DIMOS_LLM_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SHELF_VLM_MODEL", "m")
    with pytest.raises(ValueError):
        _resolve_endpoint()


def test_query_detections_parses_bbox_2d_and_scales_to_pixels(monkeypatch):
    model = _make_model(monkeypatch)
    model.query = MagicMock(return_value=MOCK_LOCAL_QWEN_RESPONSE)

    image = Image.from_numpy(np.zeros((240, 320, 3), dtype=np.uint8), format=ImageFormat.BGR)
    dets = model.query_detections(image, "each horizontal shelf row")

    assert isinstance(dets, ImageDetections2D)
    assert len(dets) == 2
    # 0-1000 -> pixels: W=320, H=240
    assert dets.detections[0].bbox == (0.0, 0.0, 320.0, 120.0)
    assert dets.detections[1].bbox == (0.0, 120.0, 320.0, 240.0)
    assert dets.detections[0].name == "shelf row"
    assert all(d.is_valid() for d in dets)


def test_query_detections_empty_on_garbage(monkeypatch):
    model = _make_model(monkeypatch)
    model.query = MagicMock(return_value="no json here, sorry")
    image = Image.from_numpy(np.zeros((240, 320, 3), dtype=np.uint8), format=ImageFormat.BGR)
    dets = model.query_detections(image, "rows")
    assert len(dets) == 0


def test_client_uses_local_base_url(monkeypatch):
    model = _make_model(monkeypatch)
    client = model._client
    assert str(client.base_url).rstrip("/") == "http://localhost:1234/v1"


def test_query_detections_skips_malformed_items(monkeypatch):
    model = _make_model(monkeypatch)
    model.query = MagicMock(
        return_value='[{}, {"bbox_2d": "bad"}, {"bbox_2d": [0, 0, 500, 500], "label": "row"}]'
    )
    image = Image.from_numpy(np.zeros((240, 320, 3), dtype=np.uint8), format=ImageFormat.BGR)
    dets = model.query_detections(image, "rows")
    assert len(dets) == 1
    assert dets.detections[0].name == "row"


def test_query_detections_drops_zero_area_box(monkeypatch):
    model = _make_model(monkeypatch)
    model.query = MagicMock(return_value='[{"bbox_2d": [500, 500, 500, 500], "label": "row"}]')
    image = Image.from_numpy(np.zeros((240, 320, 3), dtype=np.uint8), format=ImageFormat.BGR)
    dets = model.query_detections(image, "rows")
    assert len(dets) == 0
