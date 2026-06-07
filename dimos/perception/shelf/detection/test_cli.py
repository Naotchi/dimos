import json
from argparse import Namespace

import cv2
import numpy as np

from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.detection.type.detection2d.bbox import Detection2DBBox
from dimos.perception.detection.type.detection2d.imageDetections2D import ImageDetections2D
from dimos.perception.shelf.detection import cli


class _FakeDetector:
    def __init__(self, *args, **kwargs):
        pass

    def process_image(self, image):
        det = Detection2DBBox(
            bbox=(10.0, 10.0, 50.0, 50.0),
            track_id=-1,
            class_id=0,
            confidence=0.9,
            name="class_0",
            ts=image.ts,
            image=image,
        )
        return ImageDetections2D(image=image, detections=[det])


def test_cli_run_writes_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RTDetrv4Detector", _FakeDetector)
    img_path = tmp_path / "shelf.jpg"
    cv2.imwrite(str(img_path), np.zeros((480, 640, 3), dtype=np.uint8))
    out_dir = tmp_path / "out"

    args = Namespace(
        images=[str(img_path)],
        model_size="l",
        weights=None,
        device="cpu",
        conf=0.4,
        out=str(out_dir),
    )
    summary = cli.run(args)

    assert summary == {"shelf": 1}
    assert (out_dir / "shelf_annotated.jpg").is_file()
    records = json.loads((out_dir / "shelf.json").read_text())
    assert records[0]["name"] == "class_0"
