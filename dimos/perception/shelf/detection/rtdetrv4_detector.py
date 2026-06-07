from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.detection.detectors.base import Detector
from dimos.perception.detection.type.detection2d.bbox import Detection2DBBox
from dimos.perception.detection.type.detection2d.imageDetections2D import ImageDetections2D
from dimos.perception.shelf.detection.weights import resolve_config, resolve_weights
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


def _to_numpy(x: Any) -> np.ndarray:
    if hasattr(x, "detach"):
        x = x.detach().cpu().numpy()
    return np.asarray(x)


_COCO_LABEL_NAMES: dict[int, str] | None = None


def _coco_label_names() -> dict[int, str]:
    """Map RT-DETRv4's contiguous class label (0-79) -> human-readable COCO name.

    The model emits 0-79 contiguous labels (the configs use
    ``remap_mscoco_category=False``), so we compose the engine's
    ``mscoco_label2category`` (label -> 1-90 category id) with
    ``mscoco_category2name`` (category id -> name). Cached after first build.
    """
    global _COCO_LABEL_NAMES
    if _COCO_LABEL_NAMES is None:
        from engine.data.dataset import mscoco_category2name, mscoco_label2category

        _COCO_LABEL_NAMES = {
            label: mscoco_category2name[cat] for label, cat in mscoco_label2category.items()
        }
    return _COCO_LABEL_NAMES


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

    names = _coco_label_names()
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
            name=names.get(class_id, f"class_{class_id}"),
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


def _move_precomputed_tensors(model: Any, device: str) -> None:
    """Move RT-DETRv4's precomputed pos_embed tensors to ``device``.

    The HybridEncoder precomputes sinusoidal position embeddings when
    ``eval_spatial_size`` is set, but stores them as plain attributes
    (`setattr(self, f"pos_embed{idx}", ...)`) instead of registered buffers
    (the ``register_buffer`` line is commented out upstream). Plain tensor
    attributes are NOT moved by ``nn.Module.to()``, so on CUDA they stay on
    CPU and trigger a device mismatch in the encoder. Move them explicitly.
    """
    import torch

    for module in model.modules():
        for name, value in list(vars(module).items()):
            if name.startswith("pos_embed") and isinstance(value, torch.Tensor):
                setattr(module, name, value.to(device))


class RTDetrv4Detector(Detector):
    """RT-DETRv4 dense detector exposed via dimos' Detector interface."""

    def __init__(
        self,
        model_size: str = "l",
        weights: str | os.PathLike[str] | None = None,
        config: str | os.PathLike[str] | None = None,
        device: str | None = None,
        conf: float = 0.4,
        download: bool = True,
    ) -> None:
        import torch

        self.conf = conf
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        if self.device == "cpu":
            logger.warning("RT-DETRv4 running on CPU; inference will be slow.")

        config_path = Path(config) if config else resolve_config(model_size)
        weights_path = resolve_weights(model_size, weights, download=download)
        self.model = self._build_model(config_path, weights_path, self.device)

    @staticmethod
    def _build_model(config_path: Path, weights_path: Path, device: str) -> Any:
        import torch
        import torch.nn as nn
        from engine.core import YAMLConfig

        cfg = YAMLConfig(str(config_path), resume=str(weights_path))
        if "HGNetv2" in cfg.yaml_cfg:
            cfg.yaml_cfg["HGNetv2"]["pretrained"] = False

        checkpoint = torch.load(str(weights_path), map_location="cpu")
        state = checkpoint["ema"]["module"] if "ema" in checkpoint else checkpoint["model"]
        cfg.model.load_state_dict(state)

        deploy_model = cfg.model.deploy()
        postprocessor = cfg.postprocessor.deploy()

        class _Model(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.model = deploy_model
                self.postprocessor = postprocessor

            def forward(self, images: Any, orig_target_sizes: Any) -> Any:
                return self.postprocessor(self.model(images), orig_target_sizes)

        model = _Model().to(device).eval()
        _move_precomputed_tensors(model, device)
        return model

    def process_image(self, image: Image) -> ImageDetections2D:
        import torch

        im_data, orig_size = _preprocess(image, self.device)
        with torch.no_grad():
            labels, boxes, scores = self.model(im_data, orig_size)
        return build_image_detections(image, labels[0], boxes[0], scores[0], self.conf)
