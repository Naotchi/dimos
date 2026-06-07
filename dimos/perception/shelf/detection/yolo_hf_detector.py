from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.detection.detectors.base import Detector
from dimos.perception.detection.type.detection2d.imageDetections2D import ImageDetections2D
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


def resolve_yolo_weights(weights: str | os.PathLike[str], hf_filename: str = "best.pt") -> str:
    """Resolve YOLO weights to a local path.

    Accepts a local ``.pt`` path, or a HuggingFace repo id (``"owner/name"``) from
    which ``hf_filename`` is downloaded and cached.
    """
    p = Path(weights)
    if p.exists():
        return str(p)
    name = str(weights)
    if "/" in name and not name.endswith(".pt"):
        from huggingface_hub import hf_hub_download

        return hf_hub_download(repo_id=name, filename=hf_filename)
    raise FileNotFoundError(
        f"YOLO weights not found: {weights!r}. Pass a local .pt path or a HuggingFace "
        "repo id like 'foduucom/product-detection-in-shelf-yolov8'."
    )


def _cuda_nms_available() -> bool:
    """Whether torchvision's NMS op has a working CUDA kernel here.

    Some builds (e.g. this Spark/torch combo) ship torchvision without a CUDA
    NMS kernel, so ultralytics YOLO fails on GPU with NotImplementedError. We
    probe once so YoloDetector can fall back to CPU.
    """
    try:
        import torch
        import torchvision

        if not torch.cuda.is_available():
            return False
        boxes = torch.zeros((1, 4), device="cuda")
        scores = torch.zeros((1,), device="cuda")
        torchvision.ops.nms(boxes, scores, 0.5)
        return True
    except Exception:
        return False


class YoloDetector(Detector):
    """ultralytics YOLO detector for arbitrary weights (local .pt or HuggingFace repo).

    Conforms to dimos' ``Detector`` interface, so it is a drop-in alongside
    RTDetrv4Detector / the built-in Yolo2DDetector. Use it to run community
    shelf-product detectors (e.g. foduucom/product-detection-in-shelf-yolov8)
    without training.
    """

    def __init__(
        self,
        weights: str | os.PathLike[str],
        device: str | None = None,
        conf: float = 0.4,
        hf_filename: str = "best.pt",
    ) -> None:
        import torch
        from ultralytics import YOLO  # type: ignore[attr-defined]

        self.conf = conf
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        if self.device != "cpu" and not _cuda_nms_available():
            logger.warning(
                "torchvision CUDA NMS is unavailable in this environment; "
                "running YOLO on CPU instead."
            )
            self.device = "cpu"
        self.model = YOLO(resolve_yolo_weights(weights, hf_filename))

    def process_image(self, image: Image) -> ImageDetections2D:
        results = self.model.predict(
            source=image.to_opencv(),
            device=self.device,
            conf=self.conf,
            verbose=False,
        )
        return ImageDetections2D.from_ultralytics_result(image, results)
