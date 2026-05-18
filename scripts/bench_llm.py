#!/usr/bin/env python
"""Config-driven LLM/STT/TTS bench runner.

Boots ``unitree_go2_agentic_local_tts`` with module configs injected from a
YAML file, injects fixture wavs via ``LocalMicrophoneJa.inject_utterance``,
and writes bench events to ``logs/{ts}-{config.name}/main.jsonl``. A copy
of the config plus a sha256 hash are recorded so each run is
self-describing.

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
from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts import (
    unitree_go2_agentic_local_tts,
)
from dimos.utils.logging_config import set_run_log_dir


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True, help="Path to bench config YAML")
    return p.parse_args()


def load_config(path: Path) -> dict[str, Any]:
    cfg = yaml.safe_load(path.read_text())
    if "name" not in cfg:
        raise ValueError(f"config {path} missing required 'name' field")
    return cfg


def config_hash(cfg: dict[str, Any]) -> str:
    norm = json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(norm).hexdigest()[:8]


def build_blueprint_args(cfg: dict[str, Any]) -> dict[str, Any]:
    """Translate YAML config into ModuleCoordinator.build blueprint_args."""
    args: dict[str, Any] = {}

    sim = cfg.get("simulation", {})
    args["g"] = {"simulation": bool(sim.get("enabled", False))}

    # NOTE: ModuleCoordinator dispatches blueprint_args by ``module_class.name``
    # which is ``cls.__name__.lower()`` (see dimos/core/module.py). Keys MUST be
    # lowercase or the override is silently ignored.

    stt = cfg.get("stt", {})
    if stt:
        args["whisperhumaninputja"] = {
            "model": stt.get("model", "base"),
            "fp16": bool(stt.get("fp16", False)),
        }

    llm = cfg.get("llm", {})
    llm_args: dict[str, Any] = {}
    if "model" in llm:
        llm_args["model"] = llm["model"]
    sp = llm.get("system_prompt", "ja_default")
    if sp != "ja_default":
        raise NotImplementedError(
            f"system_prompt={sp!r} not implemented; only 'ja_default' supported."
        )
    if llm_args:
        args["timedmcpclient"] = llm_args
    # ``base_url`` / ``api_key`` are not McpClientConfig fields; they flow
    # through env (resolve_llm_model mirrors them into OPENAI_BASE_URL /
    # OPENAI_API_KEY which langchain's create_agent picks up). See
    # apply_llm_env() — called in main() before coordinator.build.

    tts = cfg.get("tts", {})
    if tts:
        tts_args = {"impl": tts.get("impl", "open_jtalk")}
        if "openai_voice" in tts:
            tts_args["openai_voice"] = tts["openai_voice"]
        if "openai_model" in tts:
            tts_args["openai_model"] = tts["openai_model"]
        args["assistantspeechnodeja"] = tts_args

    return args


def apply_llm_env(cfg: dict[str, Any]) -> None:
    """Mirror ``llm.base_url`` / ``llm.api_key`` from YAML into OPENAI_* env.

    langchain's ``create_agent`` reads ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY``
    at agent instantiation time. The blueprint module-load-time call to
    ``resolve_llm_model()`` only fires for ``DIMOS_LLM_*`` env vars, so the
    YAML-driven path sets ``OPENAI_*`` directly here, late enough that the
    coordinator deploy will see it.
    """
    llm = cfg.get("llm", {}) or {}
    if base_url := llm.get("base_url"):
        os.environ["OPENAI_BASE_URL"] = base_url
    api_key = llm.get("api_key")
    if api_key is None and llm.get("base_url"):
        # Local OpenAI-compatible servers (vLLM / LM Studio / Ollama) accept
        # any non-empty key; surface a sentinel so the OpenAI client doesn't
        # error on missing ``OPENAI_API_KEY``.
        api_key = "dummy"
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key


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


def setup_run_dir(cfg: dict[str, Any], cfg_path: Path) -> Path:
    ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    out_dir = Path("logs") / f"{ts}-{cfg['name']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cfg_path, out_dir / "config.yaml")
    set_run_log_dir(out_dir)
    return out_dir


def warn_if_no_display_for_sim(cfg: dict[str, Any]) -> None:
    sim = cfg.get("simulation", {})
    if not sim.get("enabled"):
        return
    if not sim.get("headless"):
        return
    if os.environ.get("DISPLAY"):
        return
    print(
        "[bench] WARN: simulation.headless=true but no DISPLAY is set. "
        "MuJoCo viewer.launch_passive will fail. Invoke via 'xvfb-run -a'.",
        file=sys.stderr,
    )


def main() -> int:
    args = parse_args()
    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)

    # Validate config (build args) before touching env or filesystem so that a
    # bad config exits cleanly without leaving a half-formed run dir behind.
    bp_args = build_blueprint_args(cfg)
    apply_llm_env(cfg)

    os.environ.setdefault("MUJOCO_GL", "egl")
    warn_if_no_display_for_sim(cfg)

    out_dir = setup_run_dir(cfg, cfg_path)

    print(f"[bench] {cfg['name']} → {out_dir}", flush=True)

    log_bench_event(
        "run_meta",
        config_name=cfg["name"],
        config_hash=config_hash(cfg),
        config=cfg,
        started_at=datetime.now().isoformat(),
    )

    coordinator = ModuleCoordinator.build(
        unitree_go2_agentic_local_tts,
        blueprint_args=bp_args,
    )
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
