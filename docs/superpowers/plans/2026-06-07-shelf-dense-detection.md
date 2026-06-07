# Shelf Dense Detection (Stage ②) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** RT-DETRv4 を dimos から独立して走らせ、棚画像 → 高密度 bbox（クラス非依存）を出す単独モジュール（CLI + 可視化 + テスト）を作る。

**Architecture:** RT-DETRv4 は `Naotchi/rt-detrv4`（upstream `RT-DETRs/RT-DETRv4` の fork + 最小 `pyproject.toml`）として `uv add git+...` で導入。dimos 側は fork 固有の新規パッケージ `dimos/perception/shelf/detection/` に、dimos の `Detector` 抽象に準拠した薄い wrapper `RTDetrv4Detector` と CLI を置く。推論変換ロジックは純関数に切り出し、重み無しで TDD する。

**Tech Stack:** Python 3.11, PyTorch, torchvision, PIL, OpenCV, RT-DETRv4 (`engine` package), dimos types (`Image`, `Detection2DBBox`, `ImageDetections2D`), pytest, uv.

設計 spec: `docs/superpowers/specs/2026-06-07-shelf-dense-detection-design.md`

---

## 前提メモ（実装者向け）

- `.venv` は source 済み。`python` / `pytest` をそのまま使う（`python3` は使わない）。
- RT-DETRv4 推論 API（`tools/inference/torch_inf.py` 由来の確定事実）:
  - `cfg = YAMLConfig(config_path, resume=ckpt_path)` でビルド。
  - ckpt の state は `checkpoint["ema"]["module"]`（無ければ `checkpoint["model"]`）。
  - `cfg.model.deploy()` と `cfg.postprocessor.deploy()` を `nn.Module` でラップし、
    `forward(images, orig_target_sizes) -> (labels, boxes, scores)`。
  - **postprocessor は orig_target_sizes を使い boxes を元画像座標(xyxy)で返す**ので手動スケール不要。
  - 前処理は PIL RGB → `T.Resize((640,640))` → `T.ToTensor()` → `unsqueeze(0)`。`orig_size = [[w, h]]`。
- dimos 型 API（確定事実）:
  - `Image.from_file(path)` / `Image.from_opencv(np_bgr)` / `image.to_opencv()`(BGR) / `image.width` / `image.height` / `image.ts` / `image.shape`。
  - `Detection2DBBox` は `@dataclass`、フィールド: `bbox: tuple[float,float,float,float]`(xyxy), `track_id:int`, `class_id:int`, `confidence:float`, `name:str`, `ts:float`, `image:Image`。`is_valid()` は「x2>x1 かつ y2>y1 かつ全座標が画像内」。
  - `ImageDetections2D(image=..., detections=[...])`、`.annotated_image()`(bbox描画済 Image を返す)、各 det に `.to_repr_dict()`。
- import の遅延: `engine` / `torch` / `torchvision` / `gdown` は **モジュール先頭で import しない**（重み/ fork 未導入でも純関数テストが回るよう関数内で import）。

---

## Task 1: RT-DETRv4 を fork して uv 依存に追加

**Files:**
- 変更なし（dimos リポ外の準備 + `pyproject.toml` / `uv.lock` への依存追記）

- [ ] **Step 1: upstream を Naotchi org に fork**

Run:
```bash
gh repo fork RT-DETRs/RT-DETRv4 --org Naotchi --fork-name rt-detrv4 --clone=true -- /tmp/rt-detrv4
```
Expected: `/tmp/rt-detrv4` に Naotchi/rt-detrv4 が clone される。
（org 指定で権限が無い場合は手動 fork → `gh repo clone Naotchi/rt-detrv4 /tmp/rt-detrv4`）

- [ ] **Step 2: fork に最小 pyproject.toml を追加**

Create: `/tmp/rt-detrv4/pyproject.toml`
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "rt-detrv4"
version = "0.0.0+naotchi"
description = "RT-DETRv4 packaged for dimos (engine + configs)"
requires-python = ">=3.10"
dependencies = [
    "torch",
    "torchvision",
    "faster-coco-eval>=1.6.5",
    "PyYAML",
    "tensorboard",
    "scipy",
    "calflops",
    "transformers",
]

[tool.hatch.build.targets.wheel]
packages = ["engine"]

[tool.hatch.build.targets.wheel.force-include]
"configs" = "configs"
```
理由: wheel に `engine` パッケージと `configs/` を同梱し、install 後 `site-packages/engine` と `site-packages/configs` が兄弟になる（`resolve_config` がこの配置を前提に config パスを解決する）。

- [ ] **Step 3: fork に commit & push**

Run:
```bash
cd /tmp/rt-detrv4 && git checkout -b add-pyproject && git add pyproject.toml && \
git commit -m "build: add hatchling pyproject (ship engine + configs)" && \
git push -u origin add-pyproject && git rev-parse HEAD
```
Expected: コミット SHA が表示される。push 後、main へ取り込む（PR マージ or `git push origin add-pyproject:main`）。以降の `<sha>` はこの main の SHA (= 86b20b0a68d73a93b8ee23372cb2f6c12f0dd341)。

- [ ] **Step 4: dimos に依存追加**

Run (in `/home/naoki/dimos`):
```bash
uv add "rt-detrv4 @ git+https://github.com/Naotchi/rt-detrv4@86b20b0a68d73a93b8ee23372cb2f6c12f0dd341" gdown
```
Expected: `pyproject.toml` / `uv.lock` 更新。torch のバージョン競合が出たら dimos 既存 pin に合わせて解決（fork の `torch` は無印なので通常追従する）。

- [ ] **Step 5: import 検証**

Run:
```bash
python -c "import engine; from engine.core import YAMLConfig; from pathlib import Path; print(Path(engine.__file__).resolve().parent.parent / 'configs' / 'rtv4' / 'rtv4_hgnetv2_l_coco.yml')"
```
Expected: 表示された config パスが実在する（`ls` で確認）。

- [ ] **Step 6: Commit**

```bash
cd /home/naoki/dimos && git add pyproject.toml uv.lock && \
git commit -m "build(shelf): add RT-DETRv4 (Naotchi fork) + gdown deps"
```

---

## Task 2: shelf パッケージの scaffold

**Files:**
- Create: `dimos/perception/shelf/__init__.py`
- Create: `dimos/perception/shelf/detection/__init__.py`

- [ ] **Step 1: パッケージ初期化ファイルを作成**

Create `dimos/perception/shelf/__init__.py`:
```python
"""Fork-local shelf-monitoring pipeline (see docs/misc/棚監視AI 2026年5月最新版.md)."""
```
Create `dimos/perception/shelf/detection/__init__.py`:
```python
"""Stage ② Dense Detection: RT-DETRv4-based dense detector for shelf images."""
```

- [ ] **Step 2: import 検証**

Run: `python -c "import dimos.perception.shelf.detection"`
Expected: エラー無し。

- [ ] **Step 3: Commit**

```bash
git add dimos/perception/shelf/__init__.py dimos/perception/shelf/detection/__init__.py
git commit -m "feat(shelf): scaffold shelf.detection package"
```

---

## Task 3: weights.py（config / 重みの解決とダウンロード）

**Files:**
- Create: `dimos/perception/shelf/detection/weights.py`
- Test: `dimos/perception/shelf/detection/test_weights.py`

- [ ] **Step 1: 失敗するテストを書く**

Create `dimos/perception/shelf/detection/test_weights.py`:
```python
import pytest

from dimos.perception.shelf.detection import weights


def test_resolve_weights_returns_explicit_existing_path(tmp_path):
    p = tmp_path / "model.pth"
    p.write_bytes(b"x")
    assert weights.resolve_weights("l", weights=p) == p


def test_resolve_weights_missing_explicit_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        weights.resolve_weights("l", weights=tmp_path / "nope.pth")


def test_resolve_weights_unknown_size_raises():
    with pytest.raises(ValueError):
        weights.resolve_weights("zzz")


def test_resolve_weights_not_downloaded_no_download_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("DIMOS_RTDETRV4_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        weights.resolve_weights("l", download=False)


def test_resolve_config_unknown_size_raises():
    with pytest.raises(ValueError):
        weights.resolve_config("zzz")
```

- [ ] **Step 2: テストが落ちることを確認**

Run: `pytest dimos/perception/shelf/detection/test_weights.py -v`
Expected: FAIL（`module weights not found` 等）。

- [ ] **Step 3: 最小実装**

Create `dimos/perception/shelf/detection/weights.py`:
```python
from __future__ import annotations

import os
from pathlib import Path

# RT-DETRv4 公式 checkpoint の Google Drive file id（COCO 学習済み）
_GDRIVE_IDS = {
    "s": "1jDAVxblqRPEWed7Hxm6GwcEl7zn72U6z",
    "m": "1O-YpP4X-quuOXbi96y2TKkztbjroP5mX",
    "l": "1shO9EzZvXZyKedE2urLsN4dwEv8Jqa_8",
    "x": "19gnkMTgFveJsrOvSmEPQXCTG6v9oQHN3",
}


def weights_dir() -> Path:
    base = os.environ.get("DIMOS_RTDETRV4_DIR")
    d = Path(base) if base else Path.home() / ".cache" / "dimos" / "rtdetrv4"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_config(size: str) -> Path:
    size = size.lower()
    if size not in _GDRIVE_IDS:
        raise ValueError(f"Unknown model size {size!r}; expected one of {sorted(_GDRIVE_IDS)}")
    import engine  # provided by Naotchi/rt-detrv4

    config = (
        Path(engine.__file__).resolve().parent.parent
        / "configs"
        / "rtv4"
        / f"rtv4_hgnetv2_{size}_coco.yml"
    )
    if not config.is_file():
        raise FileNotFoundError(
            f"RT-DETRv4 config not found at {config}. "
            "Is Naotchi/rt-detrv4 installed with configs shipped?"
        )
    return config


def resolve_weights(
    size: str,
    weights: str | os.PathLike[str] | None = None,
    download: bool = True,
) -> Path:
    size = size.lower()
    if weights is not None:
        p = Path(weights)
        if not p.is_file():
            raise FileNotFoundError(f"Weights not found: {p}")
        return p
    if size not in _GDRIVE_IDS:
        raise ValueError(f"Unknown model size {size!r}; expected one of {sorted(_GDRIVE_IDS)}")
    dest = weights_dir() / f"rtv4_hgnetv2_{size}_coco.pth"
    if dest.is_file():
        return dest
    if not download:
        raise FileNotFoundError(
            f"Weights not found at {dest}. Download Google Drive id "
            f"{_GDRIVE_IDS[size]} to that path, or pass weights=."
        )
    try:
        import gdown
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("gdown is required to download weights: `uv add gdown`") from e
    gdown.download(id=_GDRIVE_IDS[size], output=str(dest), quiet=False)
    if not dest.is_file():  # pragma: no cover
        raise RuntimeError(f"Download failed; expected file at {dest}")
    return dest
```

- [ ] **Step 4: テストが通ることを確認**

Run: `pytest dimos/perception/shelf/detection/test_weights.py -v`
Expected: PASS（5 件）。

- [ ] **Step 5: Commit**

```bash
git add dimos/perception/shelf/detection/weights.py dimos/perception/shelf/detection/test_weights.py
git commit -m "feat(shelf): weights/config resolution + gdown download"
```

---

## Task 4: 検出変換の純関数（重み不要で TDD）

**Files:**
- Create: `dimos/perception/shelf/detection/rtdetrv4_detector.py`（純関数部のみ）
- Test: `dimos/perception/shelf/detection/test_rtdetrv4_detector.py`

- [ ] **Step 1: 失敗するテストを書く**

Create `dimos/perception/shelf/detection/test_rtdetrv4_detector.py`:
```python
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
```

- [ ] **Step 2: テストが落ちることを確認**

Run: `pytest dimos/perception/shelf/detection/test_rtdetrv4_detector.py -v`
Expected: FAIL（`rtdetrv4_detector` not found）。

- [ ] **Step 3: 最小実装（純関数のみ）**

Create `dimos/perception/shelf/detection/rtdetrv4_detector.py`:
```python
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
```

- [ ] **Step 4: テストが通ることを確認**

Run: `pytest dimos/perception/shelf/detection/test_rtdetrv4_detector.py -v`
Expected: PASS（3 件）。

- [ ] **Step 5: Commit**

```bash
git add dimos/perception/shelf/detection/rtdetrv4_detector.py dimos/perception/shelf/detection/test_rtdetrv4_detector.py
git commit -m "feat(shelf): RT-DETRv4 output -> ImageDetections2D conversion"
```

---

## Task 5: 前処理関数 `_preprocess`

**Files:**
- Modify: `dimos/perception/shelf/detection/rtdetrv4_detector.py`
- Test: `dimos/perception/shelf/detection/test_rtdetrv4_detector.py`（追記）

- [ ] **Step 1: 失敗するテストを追記**

Append to `dimos/perception/shelf/detection/test_rtdetrv4_detector.py`:
```python
def test_preprocess_shapes():
    image = _img(w=800, h=600)
    im_data, orig_size = rd._preprocess(image, "cpu")
    assert tuple(im_data.shape) == (1, 3, 640, 640)
    assert orig_size.tolist() == [[800, 600]]
```

- [ ] **Step 2: テストが落ちることを確認**

Run: `pytest dimos/perception/shelf/detection/test_rtdetrv4_detector.py::test_preprocess_shapes -v`
Expected: FAIL（`_preprocess` not defined）。

- [ ] **Step 3: 最小実装を追記**

Append to `dimos/perception/shelf/detection/rtdetrv4_detector.py`:
```python
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
```

- [ ] **Step 4: テストが通ることを確認**

Run: `pytest dimos/perception/shelf/detection/test_rtdetrv4_detector.py -v`
Expected: PASS（4 件）。

- [ ] **Step 5: Commit**

```bash
git add dimos/perception/shelf/detection/rtdetrv4_detector.py dimos/perception/shelf/detection/test_rtdetrv4_detector.py
git commit -m "feat(shelf): add RT-DETRv4 preprocess (640 resize + orig_size)"
```

---

## Task 6: `RTDetrv4Detector` クラス（モデル構築 + 推論）

**Files:**
- Modify: `dimos/perception/shelf/detection/rtdetrv4_detector.py`
- Test: `dimos/perception/shelf/detection/test_rtdetrv4_detector.py`（opt-in smoke 追記）

- [ ] **Step 1: クラスを実装**

Append to `dimos/perception/shelf/detection/rtdetrv4_detector.py`:
```python
import os
from pathlib import Path

from dimos.perception.detection.detectors.base import Detector
from dimos.perception.shelf.detection.weights import resolve_config, resolve_weights


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

        return _Model().to(device).eval()

    def process_image(self, image: Image) -> ImageDetections2D:
        import torch

        im_data, orig_size = _preprocess(image, self.device)
        with torch.no_grad():
            labels, boxes, scores = self.model(im_data, orig_size)
        return build_image_detections(image, labels[0], boxes[0], scores[0], self.conf)
```

- [ ] **Step 2: opt-in smoke テストを追記**

Append to `dimos/perception/shelf/detection/test_rtdetrv4_detector.py`:
```python
import pytest


@pytest.mark.skipif(
    __import__("importlib.util", fromlist=["util"]).util.find_spec("engine") is None,
    reason="RT-DETRv4 (Naotchi/rt-detrv4) not installed",
)
def test_real_inference_smoke():
    from dimos.perception.shelf.detection import weights as w

    try:
        wp = w.resolve_weights("l", download=False)
    except FileNotFoundError:
        pytest.skip("RT-DETRv4 weights not downloaded")

    det = rd.RTDetrv4Detector(model_size="l", weights=wp, device="cpu", conf=0.4)
    out = det.process_image(_img(w=640, h=480))
    assert isinstance(out, ImageDetections2D)
    assert all(d.is_valid() for d in out)
```

- [ ] **Step 3: テスト実行（重み無し環境では smoke は skip）**

Run: `pytest dimos/perception/shelf/detection/test_rtdetrv4_detector.py -v`
Expected: 純関数テスト PASS、`test_real_inference_smoke` は engine/重みが無ければ SKIP。

- [ ] **Step 4: Commit**

```bash
git add dimos/perception/shelf/detection/rtdetrv4_detector.py dimos/perception/shelf/detection/test_rtdetrv4_detector.py
git commit -m "feat(shelf): RTDetrv4Detector (build + process_image) + smoke test"
```

---

## Task 7: CLI（`cli.py` + 薄い `scripts/shelf_detect.py`）

**Files:**
- Create: `dimos/perception/shelf/detection/cli.py`
- Create: `scripts/shelf_detect.py`
- Test: `dimos/perception/shelf/detection/test_cli.py`

- [ ] **Step 1: 失敗するテストを書く（fake detector を monkeypatch）**

Create `dimos/perception/shelf/detection/test_cli.py`:
```python
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
```

- [ ] **Step 2: テストが落ちることを確認**

Run: `pytest dimos/perception/shelf/detection/test_cli.py -v`
Expected: FAIL（`cli` not found）。

- [ ] **Step 3: cli.py を実装**

Create `dimos/perception/shelf/detection/cli.py`:
```python
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.shelf.detection.rtdetrv4_detector import RTDetrv4Detector


def run(args: argparse.Namespace) -> dict[str, int]:
    detector = RTDetrv4Detector(
        model_size=args.model_size,
        weights=args.weights,
        device=args.device,
        conf=args.conf,
    )
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, int] = {}
    for path in args.images:
        image = Image.from_file(path)
        detections = detector.process_image(image)
        stem = Path(path).stem
        cv2.imwrite(str(out_dir / f"{stem}_annotated.jpg"), detections.annotated_image().to_opencv())
        records = [d.to_repr_dict() for d in detections]
        (out_dir / f"{stem}.json").write_text(json.dumps(records, indent=2, ensure_ascii=False))
        summary[stem] = len(records)
    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="RT-DETRv4 dense detection on shelf images")
    p.add_argument("images", nargs="+", help="input image file(s)")
    p.add_argument("--model-size", dest="model_size", default="l", choices=["s", "m", "l", "x"])
    p.add_argument("--weights", default=None, help="path to .pth (default: auto-download)")
    p.add_argument("--device", default=None, help="cuda / cpu (default: auto)")
    p.add_argument("--conf", type=float, default=0.4)
    p.add_argument("--out", default="shelf_detect_out")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    summary = run(args)
    for stem, n in summary.items():
        print(f"{stem}: {n} detections")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: テストが通ることを確認**

Run: `pytest dimos/perception/shelf/detection/test_cli.py -v`
Expected: PASS（1 件）。

- [ ] **Step 5: 薄い scripts/shelf_detect.py を作成**

Create `scripts/shelf_detect.py`:
```python
#!/usr/bin/env python
"""CLI entrypoint for RT-DETRv4 shelf dense detection (see dimos.perception.shelf.detection.cli)."""
from dimos.perception.shelf.detection.cli import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 6: CLI が起動することを確認**

Run: `python scripts/shelf_detect.py --help`
Expected: usage が表示される（引数解析のみ、モデル構築前なので weights 不要）。

- [ ] **Step 7: Commit**

```bash
git add dimos/perception/shelf/detection/cli.py dimos/perception/shelf/detection/test_cli.py scripts/shelf_detect.py
git commit -m "feat(shelf): shelf_detect CLI (annotated image + JSON output)"
```

---

## Task 8: README とフルパス検証

**Files:**
- Create: `dimos/perception/shelf/README.md`

- [ ] **Step 1: README を作成**

Create `dimos/perception/shelf/README.md`:
```markdown
# shelf — 棚監視パイプライン（fork-local）

`docs/misc/棚監視AI 2026年5月最新版.md` の推奨パイプラインを段階実装する fork 固有パッケージ。

## Stage ② Dense Detection (RT-DETRv4)

棚画像 → 高密度 bbox（クラス非依存, COCO 学習済み重み）。

### 依存
RT-DETRv4 は `Naotchi/rt-detrv4`（fork + pyproject）として導入済み:
`uv add "rt-detrv4 @ git+https://github.com/Naotchi/rt-detrv4@86b20b0a68d73a93b8ee23372cb2f6c12f0dd341" gdown`

### 使い方
```bash
python scripts/shelf_detect.py path/to/shelf.jpg --model-size l --out out/
# out/shelf_annotated.jpg と out/shelf.json が生成される
# 初回は重みを Google Drive から自動ダウンロード（~/.cache/dimos/rtdetrv4）
```

### Python から
```python
from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.shelf.detection.rtdetrv4_detector import RTDetrv4Detector

detector = RTDetrv4Detector(model_size="l")
detections = detector.process_image(Image.from_file("shelf.jpg"))
```

`RTDetrv4Detector` は dimos の `Detector` 抽象に準拠するため、後段で
`Detection2DModule(Config.detector=RTDetrv4Detector)` として stream 層に差し込める。

### 範囲外（後続）
product/price-tag への fine-tune、③ SKU 埋め込み以降、dimos Module 層への統合。
```

- [ ] **Step 2: 全テスト実行**

Run: `pytest dimos/perception/shelf/ -v`
Expected: 全 PASS（実重み smoke のみ環境次第で SKIP）。

- [ ] **Step 3: （重みがある環境のみ）実画像でフルパス確認**

Run: `python scripts/shelf_detect.py <棚画像> --device cpu --out /tmp/shelf_out && ls /tmp/shelf_out`
Expected: `*_annotated.jpg` と `*.json` が生成される。重み未取得なら初回 DL が走る。

- [ ] **Step 4: Commit**

```bash
git add dimos/perception/shelf/README.md
git commit -m "docs(shelf): README for Stage ② dense detection"
```

---

## Self-Review（記入済み）

- **Spec coverage:**
  - §2 モデル/依存(A2) → Task 1。
  - §3 ファイル構成 → Task 2/3/4/6/7（spec の `scripts/shelf_detect.py` に加え、テスト容易性のため `cli.py` を追加。spec の意図と整合）。
  - §4 `RTDetrv4Detector(Detector)` / weights.py → Task 3/5/6。
  - §5 データフロー → Task 5(preprocess)+6(process_image)+7(CLI 出力)。
  - §6 エラー処理（重み未配置 actionable / CPU フォールバック / 検出ゼロ正常系）→ Task 3, 6, 4。
  - §7 テスト方針（純関数 TDD / opt-in smoke / CLI smoke）→ Task 4/5/7 + 6。
- **Placeholder scan:** `<sha>` は Task 1 で確定する fork の実 SHA を指す意図的プレースホルダ（実装者が記入）。それ以外の TBD/TODO 無し。
- **Type consistency:** `build_image_detections` / `_to_numpy` / `_preprocess` / `RTDetrv4Detector.process_image` / `cli.run` のシグネチャは全 Task で一貫。`Detection2DBBox` のフィールド名は実コードと一致確認済み。
