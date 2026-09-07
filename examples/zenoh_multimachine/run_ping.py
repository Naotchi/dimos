#!/usr/bin/env python3
# Fork-local PoC runner (sender side). See docs/zenoh-multimachine-feasibility.md.

"""Sender-side dimos instance: sends pings, measures RTT, optional image load.

Run this on machine B (or terminal B for the single-host test) while
run_pong.py is up on the other side:

    python run_ping.py --count 200 --rate 20 --image

Exits 0 when >=95% of pings were echoed back, 1 otherwise.
Writes a JSON result next to the --json path (default /tmp/zenoh_poc_result.json).
"""

import os

os.environ.setdefault("DIMOS_TRANSPORT", "zenoh")

import argparse  # noqa: E402
import json  # noqa: E402
import socket  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

from poc_modules import ZenohImageSource, ZenohPing  # noqa: E402

from dimos.core.coordination.blueprints import autoconnect  # noqa: E402
from dimos.core.coordination.module_coordinator import ModuleCoordinator  # noqa: E402
from dimos.core.global_config import global_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=200, help="number of pings")
    parser.add_argument("--rate", type=float, default=20.0, help="ping rate [Hz]")
    parser.add_argument("--image", action="store_true", help="also publish an image stream")
    parser.add_argument("--image-rate", type=float, default=10.0, help="image rate [Hz]")
    parser.add_argument("--image-count", type=int, default=100, help="number of images")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--json", default="/tmp/zenoh_poc_result.json", help="result JSON path")
    parser.add_argument(
        "--settle", type=float, default=3.0, help="seconds to wait for peer discovery"
    )
    args = parser.parse_args()

    print(f"transport={global_config.transport} scouting={global_config.zenoh_scouting}")

    blueprints = [ZenohPing.blueprint(rate_hz=args.rate, count=args.count)]
    if args.image:
        blueprints.append(
            ZenohImageSource.blueprint(
                rate_hz=args.image_rate,
                count=args.image_count,
                width=args.width,
                height=args.height,
            )
        )
    blueprint = autoconnect(*blueprints).global_config(n_workers=2)

    coordinator = ModuleCoordinator.build(blueprint)
    ping = coordinator.get_instance(ZenohPing)

    # The ping module starts publishing immediately; early pings sent before
    # the remote peer is discovered count as losses unless we allow for a
    # settle window in the deadline.
    duration = args.count / args.rate
    if args.image:
        duration = max(duration, args.image_count / args.image_rate)
    deadline = time.time() + args.settle + duration + 10.0

    source = coordinator.get_instance(ZenohImageSource) if args.image else None

    stats: dict = {}
    try:
        while time.time() < deadline:
            time.sleep(1.0)
            stats = ping.get_stats()
            print(f"ping={stats}", flush=True)
            pings_done = stats.get("received", 0) >= args.count
            images_done = (
                source is None or source.get_stats().get("sent", 0) >= args.image_count
            )
            if pings_done and images_done:
                time.sleep(1.0)  # let the last in-flight frames land on the sink
                break

        result = {
            "host": socket.gethostname(),
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "transport": global_config.transport,
            "zenoh_scouting": global_config.zenoh_scouting,
            "robot_ip": global_config.robot_ip,
            "args": vars(args),
            "ping": stats,
        }
        if source is not None:
            result["image_source"] = source.get_stats()
        with open(args.json, "w") as f:
            json.dump(result, f, indent=2)
        print(f"result written to {args.json}")
        print(json.dumps(result["ping"], indent=2))
    finally:
        coordinator.stop()

    ok = stats.get("received", 0) >= args.count * 0.95
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
