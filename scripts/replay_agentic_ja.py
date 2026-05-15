#!/usr/bin/env python
"""Replay wav fixtures through the unitree_go2_agentic_ja blueprint.

Boots the blueprint in-process, looks up the JapaneseWebInput instance, and
publishes fixture wavs to its _audio_subject. Emits a bench event
(user_audio_end) immediately after the last chunk is published so the
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
from typing import Iterator

import numpy as np
import yaml

from dimos.agents.bench_ja import log_bench_event, new_turn, reset
from dimos.agents.mcp.mcp_client_ja import TimedMcpClient
from dimos.agents.web_human_input_ja import JapaneseWebInput
from dimos.core.coordination.module_coordinator import ModuleCoordinator
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_ja import (
    unitree_go2_agentic_ja,
)
from dimos.stream.audio.base import AudioEvent
from dimos.utils.logging_config import set_run_log_dir


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--fixtures", default="tests/bench_fixtures/agentic_ja/fixtures.yaml")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--warmup", type=int, default=1)
    p.add_argument("--shuffle", action="store_true")
    p.add_argument(
        "--realtime",
        action="store_true",
        help="Publish chunks with sleep matching playback (default: burst).",
    )
    p.add_argument("--chunk-ms", type=int, default=200)
    p.add_argument("--turn-timeout", type=float, default=30.0)
    p.add_argument(
        "--out",
        default=None,
        help="Override log run dir (default: logs/{ts}-bench-agentic-ja).",
    )
    return p.parse_args()


def load_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return audio, sr


def chunked(audio: np.ndarray, sr: int, chunk_ms: int) -> Iterator[np.ndarray]:
    step = max(1, int(sr * chunk_ms / 1000))
    for i in range(0, len(audio), step):
        yield audio[i : i + step]


def fixture_iter(fixtures: list[dict], runs: int, warmup: int, shuffle: bool):
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


# TODO: Step 6.1 probe revealed JapaneseWebInput runs in a worker subprocess,
# so direct .on_next() into web_input._audio_subject does not reach the
# pipeline. Implement WebSocket fallback by connecting to ws://localhost:5555
# (see dimos/web/robot_web_interface.py for the actual audio endpoint path)
# and sending PCM frames in the WebUI's format.
def boot_blueprint() -> tuple[ModuleCoordinator, JapaneseWebInput, TimedMcpClient]:
    """Build the blueprint and return (coordinator, web_input_proxy, mcp_client_proxy)."""
    coordinator = ModuleCoordinator.build(unitree_go2_agentic_ja)
    web_input = coordinator.get_instance(JapaneseWebInput)
    mcp_client = coordinator.get_instance(TimedMcpClient)
    return coordinator, web_input, mcp_client


def main() -> int:
    args = parse_args()
    out_dir = configure_log_dir(args.out)
    print(f"[replay] logging to {out_dir}", flush=True)

    fx_path = Path(args.fixtures)
    manifest = yaml.safe_load(fx_path.read_text())
    fixtures = manifest["fixtures"]

    coordinator, web_input, mcp_client = boot_blueprint()

    idle_event = threading.Event()

    def on_idle(is_idle: bool) -> None:
        if is_idle:
            idle_event.set()
        else:
            idle_event.clear()

    mcp_client.agent_idle.subscribe(on_idle)

    if not idle_event.wait(timeout=60.0):
        print("[replay] timed out waiting for initial agent_idle", file=sys.stderr)
        return 2

    fixtures_iter = list(fixture_iter(fixtures, args.runs, args.warmup, args.shuffle))
    print(f"[replay] {len(fixtures_iter)} runs scheduled", flush=True)

    for fx in fixtures_iter:
        idle_event.clear()
        if not idle_event.wait(timeout=args.turn_timeout):
            print(f"[replay] WARN: idle wait timed out before fx {fx['id']}", file=sys.stderr)

        wav_path = fx_path.parent / fx["wav"]
        audio, sr = load_wav(wav_path)
        audio_seconds = round(len(audio) / sr, 4)

        idle_event.clear()
        for chunk in chunked(audio, sr, args.chunk_ms):
            web_input._audio_subject.on_next(  # noqa: SLF001 — bench-only hook
                AudioEvent(data=chunk, sample_rate=sr)
            )
            if args.realtime:
                time.sleep(len(chunk) / sr)

        # t=0: last chunk has been published.
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
