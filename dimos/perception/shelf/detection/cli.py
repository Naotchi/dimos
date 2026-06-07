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
