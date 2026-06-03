#!/usr/bin/env python
"""Replay-bench runner for a DimOS blueprint.

Mirrors the real ``dimos run <blueprint> --profile <name>`` invocation: the
blueprint is a positional arg (resolved by name like the CLI, so any registered
variant incl. the fork-local ``*-detection`` is benchable), ``--profile`` selects
``configs/profiles/<name>.json``, and ``--bench`` points at a slim YAML carrying
the bench-only knobs (fixtures / runs / warmup / shuffle / turn_timeout /
simulation). Injects fixture wavs via ``LocalMicrophoneJa.inject_utterance`` and
writes bench events to ``logs/{ts}-{blueprint}-{profile}/main.jsonl``.

The bench calls ``load_dotenv()`` to load the root ``.env``, then
``apply_profile`` reads ``timedmcpclient.endpoint`` from the profile JSON and
copies the matching ``DIMOS_LLM_<ENDPOINT>_{BASE_URL,API_KEY}`` values into the
generic ``DIMOS_LLM_{BASE_URL,API_KEY}``, which the blueprint's import-time
``mirror_llm_endpoint_env()`` mirrors into ``OPENAI_*``.

For headless MuJoCo runs, invoke under ``xvfb-run`` on Linux:

    xvfb-run -a python scripts/bench_llm.py <blueprint> --profile <name> --bench <yaml>

Usage:
    python scripts/bench_llm.py unitree-go2-agentic-local-tts-detection \\
        --profile qwen-vl --bench scripts/bench_configs/agentic_ja.yaml
"""

from __future__ import annotations

import argparse
import copy
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

# NOTE: blueprint modules are intentionally NOT imported here. They call
# mirror_llm_endpoint_env() at module load, which reads DIMOS_LLM_* env. Both
# the named blueprint and the get_all_blueprints registry (which imports every
# blueprint) are pulled in inside main() AFTER apply_profile() has set the env.


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Replay-bench a DimOS blueprint, mirroring `dimos run <blueprint> --profile <name>`.",
    )
    p.add_argument(
        "blueprint",
        nargs="+",
        help="Blueprint name(s), joined with '-' (e.g. unitree-go2-agentic-local-tts-detection)",
    )
    p.add_argument(
        "--profile",
        required=True,
        help="Named profile under configs/profiles/<name>.json (same as `dimos run --profile`)",
    )
    p.add_argument(
        "--bench",
        required=True,
        type=Path,
        help="Path to bench YAML (fixtures / runs / warmup / shuffle / turn_timeout / simulation)",
    )
    return p.parse_args()


def load_bench(path: Path) -> dict[str, Any]:
    cfg = yaml.safe_load(path.read_text())
    if "fixtures" not in cfg:
        raise ValueError(f"bench config {path} missing required 'fixtures' field")
    return cfg


# global_config.simulation is a string enum, mirroring `dimos run --simulation`
# (SIMULATORS in dimos/robot/cli/dimos.py). "" = real robot. Kept local to avoid
# importing the heavy CLI module at bench import time.
SIMULATORS = ("mujoco", "dimsim")


def normalize_simulation(value: Any) -> str:
    """Map the bench YAML ``simulation:`` field to ``global_config.simulation``.

    Accepts the str enum (``"mujoco"`` | ``"dimsim"``), a legacy bool
    (``True`` -> ``"mujoco"`` for backwards compat), or falsy (-> ``""``,
    i.e. real robot). The CLI normalizes a bare ``--simulation`` to ``mujoco``
    the same way.
    """
    if value is True:
        return SIMULATORS[0]
    if not value:
        return ""
    s = str(value)
    if s not in SIMULATORS:
        raise ValueError(
            f"bench 'simulation' must be one of {SIMULATORS} or a bool, got {value!r}"
        )
    return s


def config_hash(cfg: dict[str, Any]) -> str:
    norm = json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(norm).hexdigest()[:8]


def redacted_endpoint(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Capture the resolved LLM endpoint for the run record, minus secrets.

    ``mirror_llm_endpoint_env`` (fired at blueprint import after ``apply_profile``)
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


def setup_run_dir(
    label: str,
    bench_path: Path,
    config_path: Path,
    kwargs: dict[str, Any],
) -> Path:
    ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    out_dir = Path("logs") / f"{ts}-{label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Self-describing run record: the bench YAML, the referenced profile
    # JSON, and the resolved blueprint_args actually passed to build().
    # No secrets are logged — endpoint creds live in the root .env, never in
    # the profile; the endpoint is captured redacted via
    # run_meta.resolved_endpoint instead.
    shutil.copy2(bench_path, out_dir / "bench.yaml")
    shutil.copy2(config_path, out_dir / "profile_config.json")
    (out_dir / "resolved_config.json").write_text(
        json.dumps(kwargs, indent=2, ensure_ascii=False, sort_keys=True)
    )
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
    from dotenv import load_dotenv

    from dimos.agents.profile_ja import apply_profile

    args = parse_args()
    bench_path = Path(args.bench)
    cfg = load_bench(bench_path)
    blueprint_name = "-".join(args.blueprint)
    label = cfg.get("label") or f"{blueprint_name}-{args.profile}"

    # Load the root .env (DIMOS_LLM_{LOCAL,CLOUD}_* endpoint creds) BEFORE
    # apply_profile, so the profile's endpoint selection can resolve them. The
    # CLI loads root .env at dimos.py import; the bench reaches apply_profile
    # first, so it must load it explicitly. apply_profile then sets
    # DIMOS_LLM_{BASE_URL,API_KEY}, which the blueprint's import-time
    # mirror_llm_endpoint_env() mirrors into OPENAI_*.
    load_dotenv()
    config_path = apply_profile(args.profile)
    from dimos.core.coordination.blueprints import autoconnect
    from dimos.robot.cli.dimos import load_config_args
    from dimos.robot.get_all_blueprints import get_by_name_or_exit

    # Resolve the blueprint by name the same way `dimos run` does, so any
    # registered variant (incl. the fork-local *-detection) is benchable.
    blueprint = autoconnect(*map(get_by_name_or_exit, args.blueprint))

    kwargs = load_config_args(blueprint.config(), [], config_path)
    # Run-mode is an invocation parameter, not a profile concern (Spec §2).
    # The bench YAML carries `simulation:` so the run stays reproducible.
    # global_config.simulation is a str enum ("mujoco"|"dimsim"|""), not a bool.
    kwargs.setdefault("g", {})["simulation"] = normalize_simulation(cfg.get("simulation"))

    os.environ.setdefault("MUJOCO_GL", "egl")
    warn_if_no_display_for_sim(cfg, kwargs)

    out_dir = setup_run_dir(label, bench_path, config_path, kwargs)
    print(f"[bench] {label} → {out_dir}", flush=True)

    # build() pops "g" from kwargs in place, so snapshot for the record first.
    resolved_snapshot = copy.deepcopy(kwargs)
    log_bench_event(
        "run_meta",
        config_name=label,
        blueprint=blueprint_name,
        profile=args.profile,
        resolved_config=resolved_snapshot,
        resolved_endpoint=redacted_endpoint(resolved_snapshot),
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
