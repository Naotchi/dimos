#!/usr/bin/env python
"""Replay wav fixtures through the unitree_go2_agentic_local_tts blueprint.

Boots the blueprint in-process, then injects fixture wavs directly into
LocalMicrophoneJa.inject_utterance, which publishes them to the mic_utterance
Out stream — bypassing PortAudio and the PTT gate — so no real microphone or
HTTP server is needed.

After publishing each wav we emit a `user_audio_end` bench event so the
analyzer can compute end-to-end latencies relative to that timestamp.

Usage:
    python scripts/replay_agentic_local_tts.py \
        --fixtures tests/bench_fixtures/agentic_ja/fixtures.yaml \
        --runs 3 --warmup 1
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from dimos.agents.bench_ja import log_bench_event, new_turn, reset
from dimos.agents.local_microphone_ja import LocalMicrophoneJa
from dimos.agents.mcp.mcp_client_ja import TimedMcpClient
from dimos.core.coordination.module_coordinator import ModuleCoordinator
from dimos.core.global_config import global_config
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts import (
    unitree_go2_agentic_local_tts,
)
from dimos.utils.logging_config import set_run_log_dir


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--fixtures", default="tests/bench_fixtures/agentic_ja/fixtures.yaml")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--warmup", type=int, default=1)
    p.add_argument("--shuffle", action="store_true")
    p.add_argument(
        "--simulation",
        action="store_true",
        help="Run blueprint against MuJoCo (sets global_config.simulation=True).",
    )
    p.add_argument("--turn-timeout", type=float, default=30.0)
    p.add_argument("--initial-idle-timeout", type=float, default=60.0)
    p.add_argument(
        "--label",
        default=None,
        help="Free-form label for this run (recorded in main.jsonl run_meta event). "
             "Defaults to DIMOS_LLM_MODEL.",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Override log run dir (default: logs/{ts}-bench-agentic-local-tts).",
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
        path = Path("logs") / f"{ts}-bench-agentic-local-tts"
    path.mkdir(parents=True, exist_ok=True)
    set_run_log_dir(path)
    return path


def boot_blueprint() -> tuple[ModuleCoordinator, TimedMcpClient]:
    """Build the blueprint; return coordinator and the (proxy) mcp_client."""
    coordinator = ModuleCoordinator.build(unitree_go2_agentic_local_tts, blueprint_args={})
    mcp_client = coordinator.get_instance(TimedMcpClient)
    return coordinator, mcp_client


def main() -> int:
    args = parse_args()
    if args.simulation:
        global_config.update(simulation=True)
    out_dir = configure_log_dir(args.out)
    print(
        f"[replay] logging to {out_dir} (connection={global_config.unitree_connection_type})",
        flush=True,
    )

    label = args.label or os.environ.get("DIMOS_LLM_MODEL") or "unlabeled"
    log_bench_event(
        "run_meta",
        label=label,
        model=os.environ.get("DIMOS_LLM_MODEL"),
        base_url=(
            os.environ.get("DIMOS_LLM_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
        ),
        api_key_source=(
            "DIMOS_LLM_API_KEY"
            if os.environ.get("DIMOS_LLM_API_KEY")
            else ("OPENAI_API_KEY" if os.environ.get("OPENAI_API_KEY") else None)
        ),
        started_at=datetime.now().isoformat(),
    )

    fx_path = Path(args.fixtures)
    manifest = yaml.safe_load(fx_path.read_text())
    fixtures = manifest["fixtures"]

    coordinator, mcp_client = boot_blueprint()
    mic = coordinator.get_instance(LocalMicrophoneJa)

    idle_event = threading.Event()

    def on_idle(is_idle: bool) -> None:
        if is_idle:
            idle_event.set()
        else:
            idle_event.clear()

    mcp_client.agent_idle.subscribe(on_idle)

    # NOTE: agent_idle is only published after the first turn completes
    # (see TimedMcpClient._process_message), so we cannot wait for an
    # "initial idle" here — we just inject the first wav once the modules
    # are up, then wait for idle between subsequent wavs.

    schedule = list(fixture_iter(fixtures, args.runs, args.warmup, args.shuffle))
    print(f"[replay] {len(schedule)} runs scheduled", flush=True)

    for i, fx in enumerate(schedule):
        if i > 0:
            if not idle_event.wait(timeout=args.turn_timeout):
                print(f"[replay] WARN: idle wait timed out before fx {fx['id']}", file=sys.stderr)
            idle_event.clear()

        wav_path = fx_path.parent / fx["wav"]
        audio_seconds = wav_seconds(wav_path)

        # t=0: about to hand the wav to the mic. Publishing into mic_utterance
        # runs Whisper STT in the same process synchronously, so logging
        # BEFORE inject keeps the timeline ordered.
        reset()
        new_turn()
        log_bench_event(
            "user_audio_end",
            audio_seconds=audio_seconds,
            fixture_id=fx["id"],
            run_idx=fx["run_idx"],
            warmup=fx["warmup"],
        )

        try:
            mic.inject_utterance(str(wav_path))
        except Exception as e:
            print(f"[replay] inject failed for {fx['id']}: {e}", file=sys.stderr)
            log_bench_event(
                "inject_failed",
                fixture_id=fx["id"],
                run_idx=fx["run_idx"],
                error=str(e),
            )
            continue

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
