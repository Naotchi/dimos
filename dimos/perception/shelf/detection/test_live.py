import numpy as np

from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.detection.type.detection2d.bbox import Detection2DBBox
from dimos.perception.detection.type.detection2d.imageDetections2D import ImageDetections2D
from dimos.perception.shelf.detection import live


def test_detections_to_rerun_boxes():
    image = Image.from_opencv(np.zeros((480, 640, 3), dtype=np.uint8))
    det = Detection2DBBox(
        bbox=(10.0, 20.0, 50.0, 80.0),
        track_id=-1,
        class_id=0,
        confidence=0.9,
        name="class_0",
        ts=image.ts,
        image=image,
    )
    detections = ImageDetections2D(image=image, detections=[det])
    mins, sizes, labels = live.detections_to_rerun_boxes(detections)
    assert mins == [[10.0, 20.0]]
    assert sizes == [[40.0, 60.0]]
    assert labels == ["class_0 0.90"]


def test_detections_to_rerun_boxes_empty():
    image = Image.from_opencv(np.zeros((480, 640, 3), dtype=np.uint8))
    mins, sizes, labels = live.detections_to_rerun_boxes(ImageDetections2D(image=image, detections=[]))
    assert mins == [] and sizes == [] and labels == []
