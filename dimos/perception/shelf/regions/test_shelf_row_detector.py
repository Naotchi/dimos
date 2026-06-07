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

    def query_detections(self, image, query="each horizontal shelf row"):
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
    vlm = _FakeVlm(rows=[(0, 0, 320, 120), (0, 120, 320, 240)])
    dense = _FakeDense()
    detector = ShelfRowDetector(vlm=vlm, dense_detector=dense)

    out = detector.process_image(img)

    assert dense.crops_seen == 2
    assert len(out) == 2
    assert out.detections[0].bbox == (5.0, 5.0, 15.0, 15.0)
    assert out.detections[1].bbox == (5.0, 125.0, 15.0, 135.0)
    assert out.detections[1].name == "bottle (row1)"
    assert all(d.is_valid() for d in out)


def test_caches_grounding_across_frames():
    img = _img()
    vlm = _FakeVlm(rows=[(0, 0, 320, 240)])
    detector = ShelfRowDetector(vlm=vlm, dense_detector=_FakeDense())
    detector.process_image(img)
    detector.process_image(img)
    assert vlm.calls == 1


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
    vlm = _FakeVlm(rows=[])
    dense = _FakeDense()
    detector = ShelfRowDetector(vlm=vlm, dense_detector=dense)
    out = detector.process_image(img)
    assert dense.crops_seen == 1
    assert len(out) == 1
    assert out.detections[0].bbox == (5.0, 5.0, 15.0, 15.0)


def test_reground_each_frame_requeries_every_call():
    img = _img()
    vlm = _FakeVlm(rows=[(0, 0, 320, 240)])
    detector = ShelfRowDetector(
        vlm=vlm, dense_detector=_FakeDense(), reground_each_frame=True
    )
    detector.process_image(img)
    detector.process_image(img)
    assert vlm.calls == 2


def test_multiple_detections_per_crop_are_all_remapped():
    img = _img(h=240, w=320)

    class _MultiDense:
        def process_image(self, crop):
            dets = ImageDetections2D(crop)
            for bx in (5.0, 50.0):
                dets.detections.append(
                    Detection2DBBox(
                        bbox=(bx, 5.0, bx + 10.0, 15.0),
                        track_id=0,
                        class_id=-1,
                        confidence=0.9,
                        name="bottle",
                        ts=crop.ts,
                        image=crop,
                    )
                )
            return dets

    vlm = _FakeVlm(rows=[(0, 0, 320, 120), (0, 120, 320, 240)])
    out = ShelfRowDetector(vlm=vlm, dense_detector=_MultiDense()).process_image(img)
    # 2 rows x 2 detections each = 4
    assert len(out) == 4
    # row1's detections shifted down by 120
    row1_boxes = {d.bbox for d in out if d.name.endswith("(row1)")}
    assert (5.0, 125.0, 15.0, 135.0) in row1_boxes
    assert (50.0, 125.0, 60.0, 135.0) in row1_boxes


def test_out_of_bounds_vlm_box_is_clamped_not_dropped():
    img = _img(h=240, w=320)
    vlm = _FakeVlm(rows=[(-10, -10, 400, 300)])  # extends past image edges
    dense = _FakeDense()
    out = ShelfRowDetector(vlm=vlm, dense_detector=dense).process_image(img)
    assert dense.crops_seen == 1  # clamped to a valid crop, not dropped
    assert len(out) == 1
    assert out.detections[0].bbox == (5.0, 5.0, 15.0, 15.0)
