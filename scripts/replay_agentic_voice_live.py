#!/usr/bin/env python
"""Replay wav fixtures through the unitree_go2_agentic_voice_live blueprint.

Boots the blueprint in-process, then uses the existing /upload_audio HTTP
endpoint (served by RobotWebInterface on port 5555, same endpoint the WebUI
uses) to inject fixture wavs. The endpoint runs inside the JapaneseWebInput
worker subprocess, decodes via ffmpeg, and pushes the AudioEvent into the
in-subprocess audio_subject. JapaneseWebInput.audio_out is then autoconnected
to AzureVoiceLiveAgent.web_audio_in for cross-process delivery.

After publishing each wav we emit a `user_audio_end` bench event so the
analyzer can compute end-to-end latencies relative to that timestamp.

Inter-turn gating uses AzureVoiceLiveAgent.agent_idle (Out[bool]); unlike the
agentic-local-tts cascade flow there is no `turn_done` event, and a single user utterance
can produce multiple Voice Live `response.done` events (e.g. when a tool call
is involved). We therefore consider a turn complete only once agent_idle has
stayed True continuously for --idle-settle-ms.

Usage:
    python scripts/replay_agentic_voice_live.py \
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
from dimos.agents.realtime import AzureVoiceLiveAgent
from dimos.core.coordination.module_coordinator import ModuleCoordinator
from dimos.core.global_config import global_config
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_voice_live import (
    unitree_go2_agentic_voice_live,
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
    p.add_argument(
        "--simulation",
        action="store_true",
        help='Run blueprint against MuJoCo (sets global_config.simulation="mujoco").',
    )
    p.add_argument("--turn-timeout", type=float, default=30.0)
    p.add_argument("--initial-idle-timeout", type=float, default=60.0)
    p.add_argument(
        "--idle-settle-ms",
        type=float,
        default=500.0,
        help="agent_idle must stay True this many ms before a turn is considered complete.",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Override log run dir (default: logs/{ts}-bench-agentic-voice-live).",
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
        path = Path("logs") / f"{ts}-bench-agentic-voice-live"
    path.mkdir(parents=True, exist_ok=True)
    set_run_log_dir(path)
    return path


def boot_blueprint() -> tuple[ModuleCoordinator, Any]:
    """Build the blueprint; return coordinator and the AzureVoiceLiveAgent proxy."""
    coordinator = ModuleCoordinator.build(
        unitree_go2_agentic_voice_live, blueprint_args={}
    )
    agent = coordinator.get_instance(AzureVoiceLiveAgent)
    return coordinator, agent


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


class IdleSettleWaiter:
    """Settles after agent_idle has been observed True for settle_ms.

    Two modes:
    - Initial (before first clear()): the first observed True is enough.
      Used to wait for the Voice Live session-ready bootstrap.
    - Per-turn (after clear()): a False transition is required before True
      can settle. Prevents settling on a stale True between turns when the
      wav has been posted but Voice Live VAD has not yet flipped the
      agent into a response.
    """

    def __init__(self, agent_idle_stream: Any, settle_ms: float) -> None:
        self._evt = threading.Event()
        self._settle_s = settle_ms / 1000.0
        self._timer: threading.Timer | None = None
        self._seen_false = False
        self._false_required = False
        self._sub = agent_idle_stream.subscribe(self._on_idle)

    def _on_idle(self, is_idle: bool) -> None:
        if not is_idle:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._seen_false = True
            return
        if self._false_required and not self._seen_false:
            return
        if self._timer is not None:
            return
        self._timer = threading.Timer(self._settle_s, self._evt.set)
        self._timer.daemon = True
        self._timer.start()

    def clear(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._seen_false = False
        self._false_required = True
        self._evt.clear()

    def wait(self, timeout: float) -> bool:
        return self._evt.wait(timeout)

    def dispose(self) -> None:
        try:
            if callable(self._sub):
                self._sub()
            else:
                self._sub.dispose()
        except Exception:
            pass
        if self._timer is not None:
            self._timer.cancel()


def main() -> int:
    args = parse_args()
    if args.simulation:
        global_config.update(simulation="mujoco")
    out_dir = configure_log_dir(args.out)
    print(
        f"[replay] logging to {out_dir} (connection={global_config.unitree_connection_type})",
        flush=True,
    )

    fx_path = Path(args.fixtures)
    manifest = yaml.safe_load(fx_path.read_text())
    fixtures = manifest["fixtures"]

    coordinator, agent = boot_blueprint()

    waiter = IdleSettleWaiter(agent.agent_idle, settle_ms=args.idle_settle_ms)

    try:
        print(f"[replay] waiting for web interface on port {WEB_PORT}...", flush=True)
        if not wait_for_web_interface(timeout=args.initial_idle_timeout):
            print("[replay] web interface never came up", file=sys.stderr)
            return 2

        # NOTE: agent_idle is only published after the first turn completes,
        # so we cannot wait for an "initial idle" here — we just send the first
        # wav once the web interface is up, then wait for idle between wavs.

        schedule = list(fixture_iter(fixtures, args.runs, args.warmup, args.shuffle))
        print(f"[replay] {len(schedule)} runs scheduled", flush=True)

        for i, fx in enumerate(schedule):
            if i > 0:
                if not waiter.wait(timeout=args.turn_timeout):
                    print(
                        f"[replay] WARN: idle wait timed out before fx {fx['id']}",
                        file=sys.stderr,
                    )
            waiter.clear()

            wav_path = fx_path.parent / fx["wav"]
            audio_seconds = wav_seconds(wav_path)

            # t=0: about to hand the wav to /upload_audio. We log BEFORE post_wav
            # because the upload handler runs synchronously, so post_wav blocks
            # until the audio has been consumed; logging after would invert the
            # timeline.
            reset()
            new_turn()
            agent.reset_bench_turn()
            log_bench_event(
                "user_audio_end",
                audio_seconds=audio_seconds,
                fixture_id=fx["id"],
                run_idx=fx["run_idx"],
                warmup=fx["warmup"],
            )

            ok = post_wav(wav_path)
            if not ok:
                log_bench_event(
                    "upload_failed",
                    fixture_id=fx["id"],
                    run_idx=fx["run_idx"],
                )
                continue

            if not waiter.wait(timeout=args.turn_timeout):
                print(f"[replay] WARN: turn {fx['id']} timed out", file=sys.stderr)
                log_bench_event(
                    "turn_timeout",
                    fixture_id=fx["id"],
                    run_idx=fx["run_idx"],
                )

        print("[replay] done", flush=True)
        return 0
    finally:
        waiter.dispose()
        coordinator.stop()


if __name__ == "__main__":
    sys.exit(main())
