import numpy as np

from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.detection.type.detection2d.imageDetections2D import ImageDetections2D
from dimos.perception.shelf.detection import rtdetrv4_detector as rd


def _img(w=640, h=480):
    return Image.from_opencv(np.zeros((h, w, 3), dtype=np.uint8))


def test_build_filters_by_conf_and_builds_detections():
    image = _img()
    labels = np.array([0, 1, 2])
    boxes = np.array([[10, 10, 50, 50], [60, 60, 120, 120], [0, 0, 5, 5]], dtype=float)
    scores = np.array([0.9, 0.3, 0.95])
    out = rd.build_image_detections(image, labels, boxes, scores, conf=0.5)
    assert isinstance(out, ImageDetections2D)
    # score 0.3 は除外 → 2 件
    assert len(out) == 2
    first = out[0]
    assert first.bbox == (10.0, 10.0, 50.0, 50.0)
    assert first.confidence == 0.9
    assert first.class_id == 0
    assert first.name == "class_0"
    assert first.track_id == -1


def test_build_drops_out_of_bounds_boxes():
    image = _img(w=640, h=480)
    labels = np.array([0])
    boxes = np.array([[600, 400, 9999, 9999]], dtype=float)  # 画像外
    scores = np.array([0.99])
    out = rd.build_image_detections(image, labels, boxes, scores, conf=0.5)
    assert len(out) == 0


def test_to_numpy_accepts_list():
    assert rd._to_numpy([1, 2, 3]).tolist() == [1, 2, 3]


def test_preprocess_shapes():
    image = _img(w=800, h=600)
    im_data, orig_size = rd._preprocess(image, "cpu")
    assert tuple(im_data.shape) == (1, 3, 640, 640)
    assert orig_size.tolist() == [[800, 600]]
