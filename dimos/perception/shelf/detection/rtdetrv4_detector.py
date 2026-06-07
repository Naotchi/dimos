from __future__ import annotations

from typing import Any

import numpy as np

from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.detection.type.detection2d.bbox import Detection2DBBox
from dimos.perception.detection.type.detection2d.imageDetections2D import ImageDetections2D
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


def _to_numpy(x: Any) -> np.ndarray:
    if hasattr(x, "detach"):
        x = x.detach().cpu().numpy()
    return np.asarray(x)


def build_image_detections(
    image: Image,
    labels: Any,
    boxes: Any,
    scores: Any,
    conf: float,
) -> ImageDetections2D:
    """Convert RT-DETRv4 single-image outputs into dimos ImageDetections2D.

    Args:
        image: source dimos Image
        labels: (N,) class ids
        boxes: (N, 4) xyxy in original image coordinates
        scores: (N,) confidences
        conf: minimum confidence to keep
    """
    labels_np = _to_numpy(labels).reshape(-1)
    boxes_np = _to_numpy(boxes).reshape(-1, 4)
    scores_np = _to_numpy(scores).reshape(-1)

    detections: list[Detection2DBBox] = []
    for i in range(scores_np.shape[0]):
        score = float(scores_np[i])
        if score < conf:
            continue
        b = boxes_np[i]
        class_id = int(labels_np[i])
        det = Detection2DBBox(
            bbox=(float(b[0]), float(b[1]), float(b[2]), float(b[3])),
            track_id=-1,
            class_id=class_id,
            confidence=score,
            name=f"class_{class_id}",
            ts=image.ts,
            image=image,
        )
        if det.is_valid():
            detections.append(det)
    return ImageDetections2D(image=image, detections=detections)


def _preprocess(image: Image, device: str) -> tuple[Any, Any]:
    """PIL RGB -> Resize(640) -> ToTensor -> (im_data[1,3,640,640], orig_size[[w,h]])."""
    import cv2
    from PIL import Image as PILImage
    import torch
    import torchvision.transforms as T

    rgb = cv2.cvtColor(image.to_opencv(), cv2.COLOR_BGR2RGB)
    pil = PILImage.fromarray(rgb)
    transforms = T.Compose([T.Resize((640, 640)), T.ToTensor()])
    im_data = transforms(pil).unsqueeze(0).to(device)
    orig_size = torch.tensor([[image.width, image.height]]).to(device)
    return im_data, orig_size
