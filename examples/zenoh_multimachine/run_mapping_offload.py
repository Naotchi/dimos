#!/usr/bin/env python3
# Fork-local PoC runner: "cloud side" of the mapping-offload demo.
# See docs/zenoh-multimachine-feasibility.md.

"""Runs stock dimos mapping modules (VoxelGridMapper -> CostMapper) in their
own dimos instance, consuming the lidar stream published by a separately
launched robot instance (e.g. `dimos --transport zenoh --replay --viewer none
run unitree-go2-basic`) over zenoh.

Probes the boundary streams and reports:
  - lidar        (PointCloud2, robot -> here)  : input actually crossing zenoh
  - global_map   (PointCloud2, VoxelGridMapper): mapper output
  - global_costmap (OccupancyGrid, CostMapper) : final offloaded product

Exits 0 when at least --min-costmaps costmaps were produced within --duration.
"""

import os

os.environ.setdefault("DIMOS_TRANSPORT", "zenoh")

import argparse  # noqa: E402
import json  # noqa: E402
import socket  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

from dimos.core.coordination.blueprints import autoconnect  # noqa: E402
from dimos.core.coordination.module_coordinator import ModuleCoordinator  # noqa: E402
from dimos.core.global_config import global_config  # noqa: E402
from dimos.core.transport import ZenohTransport  # noqa: E402
from dimos.mapping.costmapper import CostMapper  # noqa: E402
from dimos.mapping.voxels.module import VoxelGridMapper  # noqa: E402
from dimos.msgs.nav_msgs.OccupancyGrid import OccupancyGrid  # noqa: E402
from dimos.msgs.sensor_msgs.PointCloud2 import PointCloud2  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=90.0, help="probe window [s]")
    parser.add_argument("--min-costmaps", type=int, default=3)
    parser.add_argument("--device", default="CPU:0", help="VoxelGridMapper device")
    parser.add_argument("--emit-every", type=int, default=5)
    parser.add_argument(
        "--json", default="/tmp/zenoh_offload_result.json", help="result JSON path"
    )
    args = parser.parse_args()

    print(f"transport={global_config.transport} scouting={global_config.zenoh_scouting}")

    blueprint = autoconnect(
        VoxelGridMapper.blueprint(emit_every=args.emit_every, device=args.device),
        CostMapper.blueprint(),
    ).global_config(n_workers=2)
    coordinator = ModuleCoordinator.build(blueprint)
    # NOTE: no start_rpc_service() here — only one dimos instance per zenoh
    # bus may serve the Coordinator RPC (the robot instance already does).

    counts = {"lidar": 0, "global_map": 0, "global_costmap": 0}

    def counter(name):
        def _cb(_msg):
            counts[name] += 1

        return _cb

    probes = [
        ZenohTransport("dimos/lidar", PointCloud2),
        ZenohTransport("dimos/global_map", PointCloud2),
        ZenohTransport("dimos/global_costmap", OccupancyGrid),
    ]
    unsubs = [t.subscribe(counter(n)) for t, n in zip(probes, counts)]

    deadline = time.time() + args.duration
    ok = False
    try:
        while time.time() < deadline:
            time.sleep(5)
            print(f"counts={counts}", flush=True)
            if counts["global_costmap"] >= args.min_costmaps:
                ok = True
                break

        result = {
            "host": socket.gethostname(),
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "transport": global_config.transport,
            "zenoh_scouting": global_config.zenoh_scouting,
            "counts": counts,
            "ok": ok,
        }
        with open(args.json, "w") as f:
            json.dump(result, f, indent=2)
        print(f"result written to {args.json}")
    finally:
        for unsub in unsubs:
            unsub()
        coordinator.stop()

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
