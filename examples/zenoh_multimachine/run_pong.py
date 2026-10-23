#!/usr/bin/env python3
# Fork-local PoC runner (receiver side). See docs/zenoh-multimachine-feasibility.md.

"""Receiver-side dimos instance: echoes pings, sinks images, prints stats.

Run this on machine A (or terminal A for the single-host test):

    python run_pong.py

Environment:
    DIMOS_TRANSPORT        forced to "zenoh" by this script unless already set
    DIMOS_ZENOH_SCOUTING   set "true" for cross-machine discovery on a LAN
    ROBOT_IP               alternative: dial a zenoh router at tcp/<ip>:7447
"""

import os

os.environ.setdefault("DIMOS_TRANSPORT", "zenoh")

import time  # noqa: E402

from poc_modules import ZenohImageSink, ZenohPong  # noqa: E402

from dimos.core.coordination.blueprints import autoconnect  # noqa: E402
from dimos.core.coordination.module_coordinator import ModuleCoordinator  # noqa: E402
from dimos.core.global_config import global_config  # noqa: E402


def main() -> None:
    print(f"transport={global_config.transport} scouting={global_config.zenoh_scouting}")
    blueprint = autoconnect(
        ZenohPong.blueprint(),
        ZenohImageSink.blueprint(),
    ).global_config(n_workers=2)
    coordinator = ModuleCoordinator.build(blueprint)
    coordinator.start_rpc_service()
    pong = coordinator.get_instance(ZenohPong)
    sink = coordinator.get_instance(ZenohImageSink)
    print("pong side up; waiting for pings (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(5)
            print(f"pong={pong.get_stats()} image_sink={sink.get_stats()}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        coordinator.stop()


if __name__ == "__main__":
    main()
