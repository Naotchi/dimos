# Shelf-Row VLM Crop → Dense Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pipeline pre-stage that uses a local Qwen-VL endpoint to detect each horizontal shelf row, crops each row, and runs RT-DETRv4 dense detection per crop — exposed as a drop-in `Detector` wired into `Detection3DModule` via a new fork-local blueprint.

**Architecture:** A fork-local `LocalQwenVlModel` subclasses the upstream `QwenVlModel`, overriding only the OpenAI client (to hit `localhost:1234`) and `query_detections` (to parse the local model's `bbox_2d` / 0–1000-normalized grounding format). A fork-local `ShelfRowDetector(Detector)` calls it once (cached), crops each row, runs the existing `RTDetrv4Detector` per crop, and remaps boxes to full-image coordinates. A new blueprint passes `detector=ShelfRowDetector` to `Detection3DModule.blueprint(...)`; everything else (3D projection, LCM, rerun) is reused unchanged.

**Tech Stack:** Python 3.12, `.venv` (already sourced — use `python`/`pytest` directly, never `python3`), pytest, `openai` SDK, dimos `Detector`/`VlModel`/`ImageDetections2D` types, RT-DETRv4 (`rt-detrv4` package).

---

## Reference: confirmed interfaces (read before starting)

- `Detector` (upstream, `dimos/perception/detection/detectors/base.py`): single abstractmethod `process_image(self, image: Image) -> ImageDetections2D`.
- `RTDetrv4Detector` (fork, `dimos/perception/shelf/detection/rtdetrv4_detector.py:121`): `RTDetrv4Detector(model_size="l", conf=0.4, ...)`, implements `process_image(image) -> ImageDetections2D`.
- `QwenVlModel` (upstream, `dimos/models/vl/qwen.py:33`): config `QwenVlModelConfig(model_name, api_key)`; `_client` is a `@cached_property` returning `OpenAI(base_url="<alibaba cloud>", api_key=...)`; `query(image, query) -> str`.
- `VlModel.query_detections` (upstream, `dimos/models/vl/base.py:266`): default prompt asks for `[label,x1,y1,x2,y2]` **pixels** — the local Qwen ignores this and returns `bbox_2d` 0–1000, so we override it.
- `Configurable.__init__(**kwargs)` (upstream, `dimos/protocol/service/spec.py:28`) builds `self.config = config_type(**kwargs)` — so `QwenVlModel(model_name="x", api_key="y")` works.
- `Detection2DBBox` (upstream, `dimos/perception/detection/type/detection2d/bbox.py:74`): constructed as `Detection2DBBox(bbox=(x1,y1,x2,y2), track_id=int, class_id=int, confidence=float, name=str, ts=float, image=Image)`; has `.bbox`, `.name`, `.is_valid()` (bounds-checks against `image.shape`).
- `ImageDetections2D(image, detections=None)` (upstream): `.detections` is a plain `list`; iterable; `len()`.
- `Image` (upstream, `dimos/msgs/sensor_msgs/Image.py`): `.shape -> (h, w, ...)`, `.crop(x, y, width, height) -> Image` (clamps internally), `Image.from_numpy(np_image, format=ImageFormat.BGR, ts=...)`, `.ts`.
- `extract_json(response: str) -> dict | list` (upstream, `dimos/utils/llm_utils.py`): robustly extracts JSON arrays/objects from messy text.
- Blueprint registry: upstream `dimos/robot/all_blueprints.py` is a `name -> "module:attr"` dict; fork already adds entries here (line 100). Adding one line is the allowed minimal diff.
- `detector` injection: `Detection2DModule` (upstream `module2D.py:42,68`) has `Config.detector: Callable[..., Detector] = Yolo2DDetector` and constructs it as `self.config.detector()` (no args). Passing the class `ShelfRowDetector` works.

**Fork policy (CLAUDE.md):** all new files are fork-local and free to edit. The only upstream-file edit allowed is the **single registry line** in `all_blueprints.py`. Do NOT edit `base.py`, `qwen.py`, `module2D.py`, `module3D.py` — subclass/inject instead.

---

## File Structure

- Create `dimos/perception/shelf/regions/__init__.py` — package marker.
- Create `dimos/perception/shelf/regions/local_qwen_vl.py` — `LocalQwenVlModel` (endpoint override + 0–1000 `bbox_2d` parsing).
- Create `dimos/perception/shelf/regions/test_local_qwen_vl.py` — unit tests (mock `query`).
- Create `dimos/perception/shelf/regions/shelf_row_detector.py` — `ShelfRowDetector(Detector)` (crop + remap + cache + fallback).
- Create `dimos/perception/shelf/regions/test_shelf_row_detector.py` — unit tests (fake vlm + fake dense detector).
- Create `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_local_tts_shelf.py` — new blueprint.
- Create `dimos/robot/unitree/go2/blueprints/agentic/test_unitree_go2_agentic_local_tts_shelf.py` — blueprint import/build test.
- Modify `dimos/robot/all_blueprints.py` — add ONE registry line.

---

## Task 1: `regions` package + `LocalQwenVlModel` endpoint resolution

**Files:**
- Create: `dimos/perception/shelf/regions/__init__.py`
- Create: `dimos/perception/shelf/regions/local_qwen_vl.py`
- Test: `dimos/perception/shelf/regions/test_local_qwen_vl.py`

- [ ] **Step 1: Create the package marker**

Create `dimos/perception/shelf/regions/__init__.py` (empty file):

```python
```

- [ ] **Step 2: Write the failing test for endpoint resolution**

Create `dimos/perception/shelf/regions/test_local_qwen_vl.py`:

```python
# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

import pytest

from dimos.perception.shelf.regions.local_qwen_vl import _resolve_endpoint


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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest dimos/perception/shelf/regions/test_local_qwen_vl.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError: cannot import name '_resolve_endpoint'`.

- [ ] **Step 4: Write minimal implementation**

Create `dimos/perception/shelf/regions/local_qwen_vl.py`:

```python
# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

import os


def _resolve_endpoint() -> tuple[str, str, str]:
    """Resolve (base_url, model, api_key) from env.

    Prefers SHELF_VLM_* (shelf-specific override) and falls back to the agent's
    DIMOS_LLM_* so the shelf grounding shares the same local Qwen by default.
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest dimos/perception/shelf/regions/test_local_qwen_vl.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add dimos/perception/shelf/regions/__init__.py dimos/perception/shelf/regions/local_qwen_vl.py dimos/perception/shelf/regions/test_local_qwen_vl.py
git commit -m "feat(shelf): LocalQwenVlModel endpoint resolution (SHELF_VLM_* -> DIMOS_LLM_*)"
```

---

## Task 2: `LocalQwenVlModel` class — local client + `bbox_2d` 0–1000 parsing

**Files:**
- Modify: `dimos/perception/shelf/regions/local_qwen_vl.py`
- Test: `dimos/perception/shelf/regions/test_local_qwen_vl.py`

- [ ] **Step 1: Write the failing test**

Append to `dimos/perception/shelf/regions/test_local_qwen_vl.py`:

```python
from unittest.mock import MagicMock

import numpy as np

from dimos.msgs.sensor_msgs.Image import Image, ImageFormat
from dimos.perception.detection.type.detection2d.imageDetections2D import ImageDetections2D
from dimos.perception.shelf.regions.local_qwen_vl import LocalQwenVlModel

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest dimos/perception/shelf/regions/test_local_qwen_vl.py -k "bbox_2d or garbage or base_url" -v`
Expected: FAIL with `ImportError: cannot import name 'LocalQwenVlModel'`.

- [ ] **Step 3: Write the implementation**

Append to `dimos/perception/shelf/regions/local_qwen_vl.py`:

```python
from functools import cached_property
from typing import Any

from openai import OpenAI

from dimos.models.vl.qwen import QwenVlModel
from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.detection.type.detection2d.bbox import Detection2DBBox
from dimos.perception.detection.type.detection2d.imageDetections2D import ImageDetections2D
from dimos.utils.llm_utils import extract_json

_ROW_PROMPT = (
    "Detect every horizontal shelf row (each shelf layer / tier) in this image. "
    "Return one bounding box per shelf row as a JSON array, no other text:\n"
    '[{"bbox_2d": [x1, y1, x2, y2], "label": "shelf row"}]\n'
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
        self, image: Image, query: str = "shelf row", **kwargs: Any
    ) -> ImageDetections2D:
        h, w = image.shape[:2]
        result: ImageDetections2D = ImageDetections2D(image)

        raw = self.query(image, _ROW_PROMPT)
        try:
            items = extract_json(raw)
        except Exception:
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
                nx1, ny1, nx2, ny2 = (float(v) for v in box)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest dimos/perception/shelf/regions/test_local_qwen_vl.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add dimos/perception/shelf/regions/local_qwen_vl.py dimos/perception/shelf/regions/test_local_qwen_vl.py
git commit -m "feat(shelf): LocalQwenVlModel local client + 0-1000 bbox_2d grounding parse"
```

---

## Task 3: `ShelfRowDetector` — crop each row, run dense detector, remap to full image

**Files:**
- Create: `dimos/perception/shelf/regions/shelf_row_detector.py`
- Test: `dimos/perception/shelf/regions/test_shelf_row_detector.py`

- [ ] **Step 1: Write the failing test**

Create `dimos/perception/shelf/regions/test_shelf_row_detector.py`:

```python
# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

import numpy as np

from dimos.msgs.sensor_msgs.Image import Image, ImageFormat
from dimos.perception.detection.type.detection2d.bbox import Detection2DBBox
from dimos.perception.detection.type.detection2d.imageDetections2D import ImageDetections2D
from dimos.perception.shelf.regions.shelf_row_detector import ShelfRowDetector


def _img(h=240, w=320):
    return Image.from_numpy(np.zeros((h, w, 3), dtype=np.uint8), format=ImageFormat.BGR)


class _FakeVlm:
    """Returns fixed shelf-row boxes (already in full-image pixel coords)."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def query_detections(self, image, query="shelf row"):
        self.calls += 1
        dets = ImageDetections2D(image)
        for i, (x1, y1, x2, y2) in enumerate(self.rows):
            dets.detections.append(
                Detection2DBBox(
                    bbox=(float(x1), float(y1), float(x2), float(y2)),
                    track_id=i,
                    class_id=-1,
                    confidence=1.0,
                    name="shelf row",
                    ts=image.ts,
                    image=image,
                )
            )
        return dets


class _FakeDense:
    """Returns a single fixed detection at (5,5,15,15) in *crop* coordinates."""

    def __init__(self):
        self.crops_seen = 0

    def process_image(self, crop):
        self.crops_seen += 1
        dets = ImageDetections2D(crop)
        dets.detections.append(
            Detection2DBBox(
                bbox=(5.0, 5.0, 15.0, 15.0),
                track_id=0,
                class_id=-1,
                confidence=0.9,
                name="bottle",
                ts=crop.ts,
                image=crop,
            )
        )
        return dets


def test_remaps_crop_detections_to_full_image_coords():
    img = _img(h=240, w=320)
    # Two rows: top half and bottom half.
    vlm = _FakeVlm(rows=[(0, 0, 320, 120), (0, 120, 320, 240)])
    dense = _FakeDense()
    detector = ShelfRowDetector(vlm=vlm, dense_detector=dense)

    out = detector.process_image(img)

    assert dense.crops_seen == 2
    assert len(out) == 2
    # Row 0 crop origin (0,0): bbox unchanged.
    assert out.detections[0].bbox == (5.0, 5.0, 15.0, 15.0)
    # Row 1 crop origin (0,120): bbox shifted down by 120.
    assert out.detections[1].bbox == (5.0, 125.0, 15.0, 135.0)
    assert all(d.is_valid() for d in out)


def test_caches_grounding_across_frames():
    img = _img()
    vlm = _FakeVlm(rows=[(0, 0, 320, 240)])
    detector = ShelfRowDetector(vlm=vlm, dense_detector=_FakeDense())

    detector.process_image(img)
    detector.process_image(img)

    assert vlm.calls == 1  # grounding cached, VLM not re-queried


def test_reset_rows_forces_regrounding():
    img = _img()
    vlm = _FakeVlm(rows=[(0, 0, 320, 240)])
    detector = ShelfRowDetector(vlm=vlm, dense_detector=_FakeDense())

    detector.process_image(img)
    detector.reset_rows()
    detector.process_image(img)

    assert vlm.calls == 2


def test_falls_back_to_whole_image_when_no_rows():
    img = _img(h=240, w=320)
    vlm = _FakeVlm(rows=[])  # VLM found no rows
    dense = _FakeDense()
    detector = ShelfRowDetector(vlm=vlm, dense_detector=dense)

    out = detector.process_image(img)

    assert dense.crops_seen == 1  # whole image processed as one row
    assert len(out) == 1
    assert out.detections[0].bbox == (5.0, 5.0, 15.0, 15.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest dimos/perception/shelf/regions/test_shelf_row_detector.py -v`
Expected: FAIL with `ImportError: cannot import name 'ShelfRowDetector'`.

- [ ] **Step 3: Write the implementation**

Create `dimos/perception/shelf/regions/shelf_row_detector.py`:

```python
# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

from __future__ import annotations

from typing import Any

from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.detection.detectors.base import Detector
from dimos.perception.detection.type.detection2d.bbox import Detection2DBBox
from dimos.perception.detection.type.detection2d.imageDetections2D import ImageDetections2D

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
            ix1 = max(0, min(int(x1), w - 1))
            iy1 = max(0, min(int(y1), h - 1))
            ix2 = max(ix1 + 1, min(int(x2), w))
            iy2 = max(iy1 + 1, min(int(y2), h))
            rows.append((ix1, iy1, ix2, iy2))
        if not rows:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest dimos/perception/shelf/regions/test_shelf_row_detector.py -v`
Expected: PASS (4 passed).

Note: `test_remaps_...` asserts the merged name carries the row marker indirectly via `is_valid()`; the `(row{row_id})` suffix is the spec's label-based row identification. If you want to assert it explicitly, add `assert out.detections[1].name == "bottle (row1)"`.

- [ ] **Step 5: Commit**

```bash
git add dimos/perception/shelf/regions/shelf_row_detector.py dimos/perception/shelf/regions/test_shelf_row_detector.py
git commit -m "feat(shelf): ShelfRowDetector crop+remap+cache composite Detector"
```

---

## Task 4: New blueprint `unitree-go2-agentic-local-tts-shelf`

**Files:**
- Create: `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_local_tts_shelf.py`
- Modify: `dimos/robot/all_blueprints.py` (one line)
- Test: `dimos/robot/unitree/go2/blueprints/agentic/test_unitree_go2_agentic_local_tts_shelf.py`

- [ ] **Step 1: Write the failing test**

Create `dimos/robot/unitree/go2/blueprints/agentic/test_unitree_go2_agentic_local_tts_shelf.py`:

```python
# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.


def test_shelf_blueprint_importable_and_built():
    from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts_shelf import (
        unitree_go2_agentic_local_tts_shelf,
    )

    assert unitree_go2_agentic_local_tts_shelf is not None


def test_shelf_blueprint_registered():
    from dimos.robot.all_blueprints import all_blueprints  # registry dict

    assert "unitree-go2-agentic-local-tts-shelf" in all_blueprints
```

Note: the registry dict is the module-level `all_blueprints` in `dimos/robot/all_blueprints.py:18` (the dict containing the `"unitree-go2-agentic-local-tts-detection": ...` line).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest dimos/robot/unitree/go2/blueprints/agentic/test_unitree_go2_agentic_local_tts_shelf.py -v`
Expected: FAIL with `ModuleNotFoundError` (blueprint module not created yet).

- [ ] **Step 3: Create the blueprint**

Create `dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_local_tts_shelf.py`:

```python
#!/usr/bin/env python3
# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""``unitree-go2-agentic-local-tts`` + shelf-row crop -> dense detection (fork-local).

Variant of :mod:`unitree_go2_agentic_local_tts_detection`. The only difference
is the 2D detector: instead of the default, ``Detection3DModule`` is given
:class:`ShelfRowDetector`, which uses the local Qwen-VL to find shelf rows,
crops each, and runs RT-DETRv4 per crop. 3D projection / LCM / rerun wiring is
identical to the detection blueprint and reused unchanged.
"""

from dimos.core.coordination.blueprints import autoconnect
from dimos.core.transport import LCMTransport
from dimos.experimental.security_demo.security_module import SecurityModule
from dimos.msgs.sensor_msgs.Image import Image
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2
from dimos.msgs.vision_msgs.Detection2DArray import Detection2DArray
from dimos.perception.detection.module3D import Detection3DModule
from dimos.perception.shelf.regions.shelf_row_detector import ShelfRowDetector
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts import (
    unitree_go2_agentic_local_tts,
)
from dimos.robot.unitree.go2.connection import GO2Connection

unitree_go2_agentic_local_tts_shelf = (
    autoconnect(
        unitree_go2_agentic_local_tts,
        Detection3DModule.blueprint(
            camera_info=GO2Connection.camera_info_static,
            detector=ShelfRowDetector,
        ),
    )
    .remappings(
        [
            (Detection3DModule, "pointcloud", "global_map"),
        ]
    )
    .transports(
        {
            ("detections", Detection3DModule): LCMTransport(
                "/detector3d/detections", Detection2DArray
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

__all__ = ["unitree_go2_agentic_local_tts_shelf"]
```

- [ ] **Step 4: Register the blueprint (single upstream-file line)**

In `dimos/robot/all_blueprints.py`, immediately after the line:

```python
    "unitree-go2-agentic-local-tts-detection": "dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts_detection:unitree_go2_agentic_local_tts_detection",
```

add:

```python
    "unitree-go2-agentic-local-tts-shelf": "dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts_shelf:unitree_go2_agentic_local_tts_shelf",
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest dimos/robot/unitree/go2/blueprints/agentic/test_unitree_go2_agentic_local_tts_shelf.py -v`
Expected: PASS (2 passed).

If `test_shelf_blueprint_importable_and_built` triggers heavy construction or needs env, keep only the import + registry assertions (importing the module is sufficient to prove it composes, since the blueprint object is built at import time).

- [ ] **Step 6: Commit**

```bash
git add dimos/robot/unitree/go2/blueprints/agentic/unitree_go2_agentic_local_tts_shelf.py dimos/robot/unitree/go2/blueprints/agentic/test_unitree_go2_agentic_local_tts_shelf.py dimos/robot/all_blueprints.py
git commit -m "feat(shelf): unitree-go2-agentic-local-tts-shelf blueprint (ShelfRowDetector)"
```

---

## Task 5: Full-suite regression + coordinate-convention live check

**Files:** none (verification only)

- [ ] **Step 1: Run the new package's tests together**

Run: `pytest dimos/perception/shelf/regions/ dimos/robot/unitree/go2/blueprints/agentic/test_unitree_go2_agentic_local_tts_shelf.py -v`
Expected: all PASS.

- [ ] **Step 2: Confirm no upstream detector tests broke**

Run: `pytest dimos/perception/shelf/ -q`
Expected: PASS (existing RT-DETRv4 detector tests unaffected).

- [ ] **Step 3 (live, optional — requires the LM Studio endpoint): verify coordinate convention end-to-end**

This guards against the §3.3 risk that the live model's coordinate convention drifts from the assumed 0–1000 normalization. With `SHELF_VLM_BASE_URL`/`SHELF_VLM_MODEL` (or `DIMOS_LLM_*`) set to the running endpoint, and a real shelf photo at `<SHELF_PHOTO>` (a `.jpg`/`.png`), run:

```bash
python - <<'PY'
from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.shelf.regions.local_qwen_vl import LocalQwenVlModel

img = Image.from_file("<SHELF_PHOTO>")  # e.g. a JPEG photographed in front of a shelf
m = LocalQwenVlModel()
dets = m.query_detections(img, "each horizontal shelf row")
print("image (h,w):", img.shape[:2])
print("rows:", len(dets), [d.bbox for d in dets])
PY
```

Expected: row boxes whose pixel coordinates fall within the image bounds and visually correspond to shelf rows. If boxes are ~1000× too large/small or clustered near the origin, the model is NOT using 0–1000 normalization — adjust the scaling in `LocalQwenVlModel.query_detections` accordingly and re-run Task 2's unit tests.

- [ ] **Step 4: Commit (if any scaling fix was needed in Step 3)**

```bash
git add dimos/perception/shelf/regions/local_qwen_vl.py
git commit -m "fix(shelf): correct VLM coordinate scaling per live endpoint check"
```

---

## Out of scope (do NOT implement here)

- SKU identification (③ embedding + k-NN), out-of-stock (④), planogram (⑤).
- RT-DETRv4 product/price-tag fine-tune.
- "Shelf-unit → geometric row split" hybrid fallback (only the whole-image fallback is in scope).
- A standalone CLI (`scripts/shelf_rows.py`) — deferred; the live check in Task 5 covers manual verification.
