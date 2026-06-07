from __future__ import annotations

import argparse
import time
from typing import Any

import cv2

from dimos.msgs.sensor_msgs.Image import Image
from dimos.perception.shelf.detection.rtdetrv4_detector import RTDetrv4Detector
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


def detections_to_rerun_boxes(detections: Any) -> tuple[list, list, list]:
    """Convert ImageDetections2D into rerun Boxes2D inputs (mins, sizes, labels)."""
    mins, sizes, labels = [], [], []
    for d in detections:
        x1, y1, x2, y2 = d.bbox
        mins.append([float(x1), float(y1)])
        sizes.append([float(x2 - x1), float(y2 - y1)])
        labels.append(f"{d.name} {d.confidence:.2f}")
    return mins, sizes, labels


def run_live(args: argparse.Namespace) -> None:
    """Capture from a webcam, run RT-DETRv4 per frame, stream to rerun + optional mp4."""
    rr = None
    if args.rrd or args.serve:
        import rerun as rr  # noqa: PLC0415

        rr.init("shelf_dense_detection")
        if args.serve:
            # rerun 0.32: serve data over gRPC, then serve a web viewer that connects to it.
            server_uri = rr.serve_grpc()
            rr.serve_web_viewer(web_port=args.web_port, open_browser=False, connect_to=server_uri)
            logger.info(
                f"rerun web viewer on http://<this-host>:{args.web_port} "
                f"(data stream: {server_uri}) — open it in a browser"
            )
        elif args.rrd:
            rr.save(args.rrd)

    detector = RTDetrv4Detector(
        model_size=args.model_size, device=args.device, conf=args.conf
    )

    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open camera index {args.camera}")

    writer = None
    frame_idx = 0
    t_prev = time.time()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                logger.warning("frame grab failed; stopping")
                break

            image = Image.from_opencv(frame)
            detections = detector.process_image(image)

            now = time.time()
            fps = 1.0 / max(now - t_prev, 1e-6)
            t_prev = now

            if rr is not None:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                rr.set_time("frame", sequence=frame_idx)
                rr.log("camera", rr.Image(rgb))
                mins, sizes, labels = detections_to_rerun_boxes(detections)
                rr.log("camera/detections", rr.Boxes2D(mins=mins, sizes=sizes, labels=labels))

            if args.out:
                annotated = detections.annotated_image().to_opencv()
                if writer is None:
                    h, w = annotated.shape[:2]
                    writer = cv2.VideoWriter(
                        args.out, cv2.VideoWriter_fourcc(*"mp4v"), 15.0, (w, h)
                    )
                writer.write(annotated)

            if frame_idx % 15 == 0:
                logger.info(f"frame {frame_idx}: {len(detections)} det, {fps:.1f} FPS")

            frame_idx += 1
            if args.max_frames and frame_idx >= args.max_frames:
                break
    except KeyboardInterrupt:
        logger.info("interrupted; shutting down")
    finally:
        cap.release()
        if writer is not None:
            writer.release()
            logger.info(f"wrote annotated video to {args.out}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Live RT-DETRv4 dense detection from a webcam")
    p.add_argument("--camera", type=int, default=0, help="cv2 VideoCapture index")
    p.add_argument("--model-size", dest="model_size", default="l", choices=["s", "m", "l", "x"])
    p.add_argument("--device", default=None, help="cuda / cpu (default: auto)")
    p.add_argument("--conf", type=float, default=0.4)
    p.add_argument("--rrd", default=None, help="save a rerun .rrd recording to this path")
    p.add_argument("--serve", action="store_true", help="serve a live rerun web viewer")
    p.add_argument("--web-port", dest="web_port", type=int, default=9090, help="rerun web viewer port")
    p.add_argument("--out", default=None, help="write an annotated .mp4 to this path")
    p.add_argument("--max-frames", dest="max_frames", type=int, default=0, help="0 = run until Ctrl-C")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if not (args.rrd or args.serve or args.out):
        args.rrd = "shelf_live.rrd"  # default: at least produce a viewable recording
    run_live(args)


if __name__ == "__main__":
    main()
