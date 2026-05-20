#!/usr/bin/env python
"""Config-driven LLM/STT/TTS bench runner.

Boots ``unitree_go2_agentic_local_tts`` with module configs resolved from a
profile (``configs/profiles/<name>/config.json``), injects fixture wavs via
``LocalMicrophoneJa.inject_utterance``, and writes bench events to
``logs/{ts}-{config.name}/main.jsonl``.

The bench YAML references a profile name; ``apply_profile`` loads the profile
``.env`` before the blueprint is imported so that ``resolve_llm_model()`` (which
runs at blueprint import time) sees the correct ``DIMOS_LLM_*`` values.

For headless MuJoCo runs, invoke under ``xvfb-run`` on Linux:

    xvfb-run -a python scripts/bench_llm.py --config scripts/bench_configs/<name>.yaml

Usage:
    python scripts/bench_llm.py --config scripts/bench_configs/<name>.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import threading
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from dimos.agents.bench_ja import log_bench_event, new_turn, reset
from dimos.agents.local_microphone_ja import LocalMicrophoneJa
from dimos.agents.mcp.mcp_client_ja import TimedMcpClient
from dimos.agents.skills.speak_skill_ja import AssistantSpeechNodeJa
from dimos.core.coordination.module_coordinator import ModuleCoordinator
from dimos.utils.logging_config import set_run_log_dir

# NOTE: dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts
# is intentionally NOT imported here. It calls resolve_llm_model() at module load,
# which reads DIMOS_LLM_* env vars. Import is deferred to main() AFTER apply_profile().


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True, help="Path to bench config YAML")
    return p.parse_args()


def load_config(path: Path) -> dict[str, Any]:
    cfg = yaml.safe_load(path.read_text())
    for required in ("name", "profile"):
        if required not in cfg:
            raise ValueError(f"config {path} missing required {required!r} field")
    return cfg


def config_hash(cfg: dict[str, Any]) -> str:
    norm = json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(norm).hexdigest()[:8]


def redacted_endpoint(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Capture the resolved LLM endpoint for the run record, minus secrets.

    ``resolve_llm_model`` (fired at blueprint import after ``apply_profile``)
    mirrors the profile's DIMOS_LLM_* into OPENAI_*. We record base_url + model
    so the run is self-describing; the api_key is intentionally never logged.
    """
    return {
        "base_url": os.environ.get("OPENAI_BASE_URL"),
        "model": (kwargs.get("timedmcpclient") or {}).get("model"),
    }


def wav_seconds(path: Path) -> float:
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
            yield {**fx, "run_idx": run_idx, "warmup": run_idx < warmup}


def setup_run_dir(cfg: dict[str, Any], cfg_path: Path, config_path: Path, kwargs: dict[str, Any]) -> Path:
    # Task 5 finalizes the run-record contents (config_path and kwargs are reserved for it).
    ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    out_dir = Path("logs") / f"{ts}-{cfg['name']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cfg_path, out_dir / "config.yaml")
    set_run_log_dir(out_dir)
    return out_dir


def warn_if_no_display_for_sim(cfg: dict[str, Any], kwargs: dict[str, Any]) -> None:
    sim_on = bool((kwargs.get("g") or {}).get("simulation"))
    if not sim_on or not cfg.get("headless"):
        return
    if os.environ.get("DISPLAY"):
        return
    print(
        "[bench] WARN: simulation on + headless but no DISPLAY. "
        "MuJoCo viewer.launch_passive will fail. Invoke via 'xvfb-run -a'.",
        file=sys.stderr,
    )


def main() -> int:
    import copy

    from dimos.agents.profile_ja import apply_profile, resolve_profile

    args = parse_args()
    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)

    # Load the profile .env BEFORE importing the blueprint: the blueprint module
    # calls resolve_llm_model() at import time, which reads DIMOS_LLM_* and
    # mirrors them into OPENAI_*. Importing earlier would miss the profile env.
    apply_profile(cfg["profile"])
    from dimos.robot.cli.dimos import load_config_args
    from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts import (
        unitree_go2_agentic_local_tts as blueprint,
    )

    _, config_path = resolve_profile(cfg["profile"])
    if config_path is None:
        raise ValueError(f"profile {cfg['profile']!r} has no config.json")
    kwargs = load_config_args(blueprint.config(), [], config_path)

    os.environ.setdefault("MUJOCO_GL", "egl")
    warn_if_no_display_for_sim(cfg, kwargs)

    out_dir = setup_run_dir(cfg, cfg_path, config_path, kwargs)
    print(f"[bench] {cfg['name']} ({cfg['profile']}) → {out_dir}", flush=True)

    # build() pops "g" from kwargs in place, so snapshot for the record first.
    resolved_snapshot = copy.deepcopy(kwargs)
    log_bench_event(
        "run_meta",
        config_name=cfg["name"],
        profile=cfg["profile"],
        resolved_config=resolved_snapshot,
        resolved_endpoint=redacted_endpoint(kwargs),
        config_hash=config_hash(resolved_snapshot),
        started_at=datetime.now().isoformat(),
    )

    coordinator = ModuleCoordinator.build(blueprint, kwargs)
    mcp_client = coordinator.get_instance(TimedMcpClient)
    mic = coordinator.get_instance(LocalMicrophoneJa)
    speech = coordinator.get_instance(AssistantSpeechNodeJa)

    idle_event = threading.Event()
    tts_idle_event = threading.Event()
    tts_idle_event.set()  # idle until first speak_invoke
    tts_was_busy = threading.Event()  # latched True once a speak_invoke fires

    def on_idle(is_idle: bool) -> None:
        if is_idle:
            idle_event.set()
        else:
            idle_event.clear()

    def on_tts_idle(is_idle: bool) -> None:
        if is_idle:
            tts_idle_event.set()
        else:
            tts_idle_event.clear()
            tts_was_busy.set()

    mcp_client.agent_idle.subscribe(on_idle)
    speech.tts_idle.subscribe(on_tts_idle)

    fx_path = Path(cfg["fixtures"])
    manifest = yaml.safe_load(fx_path.read_text())
    fixtures = manifest["fixtures"]

    schedule = list(
        fixture_iter(
            fixtures,
            runs=int(cfg.get("runs", 3)),
            warmup=int(cfg.get("warmup", 1)),
            shuffle=bool(cfg.get("shuffle", False)),
        )
    )
    turn_timeout = float(cfg.get("turn_timeout", 30.0))
    # TTS playback is unbounded by the LLM/agent timeout — a long response can
    # take >30s of audio to drain. Use a separate, much larger cap so that the
    # drain-gate doesn't false-positive and let the next fixture race ahead.
    tts_drain_timeout = float(cfg.get("tts_drain_timeout", 300.0))
    print(f"[bench] {len(schedule)} runs scheduled", flush=True)

    for i, fx in enumerate(schedule):
        if i > 0:
            if not idle_event.wait(timeout=turn_timeout):
                print(
                    f"[bench] WARN: idle wait timed out before fx {fx['id']}",
                    file=sys.stderr,
                )
            # Only block on TTS drain if this turn actually spoke. Tool-only
            # turns never publish ``tts_idle=False``, so ``tts_was_busy``
            # stays clear and we skip the wait (otherwise we'd hang on the
            # stale-True fallthrough or the false-positive timeout).
            if tts_was_busy.is_set():
                if not tts_idle_event.wait(timeout=tts_drain_timeout):
                    print(
                        f"[bench] WARN: tts_idle wait timed out before fx {fx['id']}",
                        file=sys.stderr,
                    )
                tts_was_busy.clear()
            idle_event.clear()

        wav_path = fx_path.parent / fx["wav"]
        audio_seconds = wav_seconds(wav_path)

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
        except Exception as e:  # noqa: BLE001
            print(f"[bench] inject failed for {fx['id']}: {e}", file=sys.stderr)
            log_bench_event(
                "inject_failed",
                fixture_id=fx["id"],
                run_idx=fx["run_idx"],
                error=str(e),
            )
            continue

        if not idle_event.wait(timeout=turn_timeout):
            print(f"[bench] WARN: turn {fx['id']} timed out", file=sys.stderr)
            log_bench_event(
                "turn_timeout", fixture_id=fx["id"], run_idx=fx["run_idx"]
            )

    print("[bench] done", flush=True)
    coordinator.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
