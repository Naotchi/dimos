#!/usr/bin/env python
"""Replay wav fixtures through the unitree_go2_agentic_ja blueprint.

Boots the blueprint in-process, then uses the existing /upload_audio HTTP
endpoint (served by RobotWebInterface on port 5555, same endpoint the WebUI
uses) to inject fixture wavs. The endpoint runs inside the JapaneseWebInput
worker subprocess, decodes via ffmpeg, and pushes the AudioEvent into the
in-subprocess audio_subject -> Whisper -> agent pipeline.

After publishing each wav we emit a `user_audio_end` bench event so the
analyzer can compute end-to-end latencies relative to that timestamp.

Usage:
    python scripts/replay_agentic_ja.py \
        --fixtures tests/bench_fixtures/agentic_ja/fixtures.yaml \
        --runs 3 --warmup 1
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml

from dimos.agents.bench_ja import log_bench_event, new_turn, reset
from dimos.agents.mcp.mcp_client_ja import TimedMcpClient
from dimos.core.coordination.module_coordinator import ModuleCoordinator
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_ja import (
    unitree_go2_agentic_ja,
)
from dimos.utils.logging_config import set_run_log_dir

WEB_PORT = 5555
UPLOAD_URL = f"http://localhost:{WEB_PORT}/upload_audio"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--fixtures", default="tests/bench_fixtures/agentic_ja/fixtures.yaml")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--warmup", type=int, default=1)
    p.add_argument("--shuffle", action="store_true")
    p.add_argument("--turn-timeout", type=float, default=30.0)
    p.add_argument("--initial-idle-timeout", type=float, default=60.0)
    p.add_argument(
        "--out",
        default=None,
        help="Override log run dir (default: logs/{ts}-bench-agentic-ja).",
    )
    return p.parse_args()


def wav_seconds(path: Path) -> float:
    """Read the duration (s) of a 16-bit PCM WAV without loading samples."""
    with wave.open(str(path), "rb") as w:
        return round(w.getnframes() / w.getframerate(), 4)


def fixture_iter(fixtures: list[dict[str, Any]], runs: int, warmup: int, shuffle: bool):
    import random

    order = list(range(len(fixtures)))
    for run_idx in range(runs):
        if shuffle:
            random.shuffle(order)
        for j in order:
            fx = fixtures[j]
            yield {
                **fx,
                "run_idx": run_idx,
                "warmup": run_idx < warmup,
            }


def configure_log_dir(out_override: str | None) -> Path:
    if out_override:
        path = Path(out_override)
    else:
        ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        path = Path("logs") / f"{ts}-bench-agentic-ja"
    path.mkdir(parents=True, exist_ok=True)
    set_run_log_dir(path)
    return path


def boot_blueprint() -> tuple[ModuleCoordinator, TimedMcpClient]:
    """Build the blueprint; return coordinator and the (proxy) mcp_client."""
    coordinator = ModuleCoordinator.build(unitree_go2_agentic_ja, blueprint_args={})
    # JapaneseWebInput runs in a worker subprocess; we reach it via /upload_audio.
    # We still need TimedMcpClient.agent_idle (cross-process Out works via dimos
    # streams; the subscribe below pulls events back to this process).
    mcp_client = coordinator.get_instance(TimedMcpClient)
    return coordinator, mcp_client


def wait_for_web_interface(timeout: float = 30.0) -> bool:
    """Poll /upload_audio until it responds (404/405 = up, ConnectionError = down)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"http://localhost:{WEB_PORT}/text_streams", timeout=1.0)
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.5)
    return False


def post_wav(wav_path: Path) -> bool:
    """POST wav to /upload_audio. Returns True on 2xx, False otherwise."""
    with wav_path.open("rb") as f:
        files = {"file": (wav_path.name, f, "audio/wav")}
        r = requests.post(UPLOAD_URL, files=files, timeout=30.0)
    if r.status_code // 100 != 2:
        print(f"[replay] upload failed for {wav_path}: {r.status_code} {r.text}", file=sys.stderr)
        return False
    return True


def main() -> int:
    args = parse_args()
    out_dir = configure_log_dir(args.out)
    print(f"[replay] logging to {out_dir}", flush=True)

    fx_path = Path(args.fixtures)
    manifest = yaml.safe_load(fx_path.read_text())
    fixtures = manifest["fixtures"]

    coordinator, mcp_client = boot_blueprint()

    idle_event = threading.Event()

    def on_idle(is_idle: bool) -> None:
        if is_idle:
            idle_event.set()
        else:
            idle_event.clear()

    mcp_client.agent_idle.subscribe(on_idle)

    print(f"[replay] waiting for web interface on port {WEB_PORT}...", flush=True)
    if not wait_for_web_interface(timeout=args.initial_idle_timeout):
        print("[replay] web interface never came up", file=sys.stderr)
        coordinator.stop()
        return 2

    print("[replay] waiting for initial agent_idle...", flush=True)
    if not idle_event.wait(timeout=args.initial_idle_timeout):
        print("[replay] timed out waiting for initial agent_idle", file=sys.stderr)
        coordinator.stop()
        return 2

    schedule = list(fixture_iter(fixtures, args.runs, args.warmup, args.shuffle))
    print(f"[replay] {len(schedule)} runs scheduled", flush=True)

    for fx in schedule:
        idle_event.clear()
        if not idle_event.wait(timeout=args.turn_timeout):
            print(f"[replay] WARN: idle wait timed out before fx {fx['id']}", file=sys.stderr)

        wav_path = fx_path.parent / fx["wav"]
        audio_seconds = wav_seconds(wav_path)

        idle_event.clear()
        ok = post_wav(wav_path)
        if not ok:
            log_bench_event(
                "upload_failed",
                fixture_id=fx["id"],
                run_idx=fx["run_idx"],
            )
            continue

        # t=0: upload request has completed; the wav is now flowing into the
        # in-subprocess audio pipeline.
        reset()
        new_turn()
        log_bench_event(
            "user_audio_end",
            audio_seconds=audio_seconds,
            fixture_id=fx["id"],
            category=fx["category"],
            run_idx=fx["run_idx"],
            warmup=fx["warmup"],
        )

        if not idle_event.wait(timeout=args.turn_timeout):
            print(f"[replay] WARN: turn {fx['id']} timed out", file=sys.stderr)
            log_bench_event(
                "turn_timeout",
                fixture_id=fx["id"],
                run_idx=fx["run_idx"],
            )

    print("[replay] done", flush=True)
    coordinator.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
