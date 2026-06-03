#!/usr/bin/env python
"""Aggregate end-to-end latency from a unitree-go2-agentic-local-tts bench run.

Usage:
    python scripts/bench_agentic_local_tts.py [logs/<run-dir>] [--json FILE]

Without a run-dir argument, picks the latest logs/*agentic-local-tts*/.

Reads main.jsonl and prints per-turn end-to-end latencies and stage breakdown:
  - agent_first_call_s  (user_audio_end -> first_tool_call)
  - speak_tts_s         (first speak_invoke -> first_audio_out)
  - stt_s / llm_total_s / tools_total_s / turn_total_s
  - mcp_tool:*      (per-tool durations from upstream "MCP tool done" events,
                     bucketed into turns by timestamp range)

Aggregates over all non-warmup turns in a single pool.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any



def _percentile(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    i = max(0, min(len(xs) - 1, int(round((len(xs) - 1) * q))))
    return xs[i]


def _parse_duration(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.match(r"^([0-9.]+)\s*s?$", v.strip())
        if m:
            return float(m.group(1))
    return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def build_turns(jsonl_path: Path) -> dict[str, dict[str, Any]]:
    """Group bench events by their currently-open turn.

    Turns are strictly sequential (the replay script gates each new turn on
    idle_event set by turn_done), so the analyzer can walk main.jsonl in emit
    order and attribute every event after `user_audio_end` to that turn until
    `turn_done` / `turn_timeout` closes it. No wall-clock matching needed.
    """
    rows = _read_jsonl(jsonl_path)
    turns: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "llm_steps": [],
            "tools_steps": [],
            "mcp_tools": [],
            "speak_invokes": [],
            "llm_first_tokens": [],
        }
    )

    current: str | None = None
    for row in rows:
        kind = row.get("event_kind")
        if kind == "user_audio_end":
            current = row.get("turn_id")
            if current is None:
                continue
            turns[current]["user_audio_end"] = row
            continue

        if current is None:
            # Event arrived before any turn was opened (shouldn't happen in
            # well-formed logs, but be defensive).
            continue

        if kind == "stt_done":
            turns[current].setdefault("stt_done", row)
        elif kind in ("llm_step", "model_step"):
            turns[current]["llm_steps"].append(row)
        elif kind == "tools_step" or (
            isinstance(kind, str)
            and kind.endswith("_step")
            and kind not in ("llm_step", "model_step")
        ):
            turns[current]["tools_steps"].append(row)
        elif kind == "first_tool_call":
            turns[current].setdefault("first_tool_call", row)
        elif kind == "speak_invoke":
            turns[current]["speak_invokes"].append(row)
        elif kind == "llm_first_token":
            turns[current]["llm_first_tokens"].append(row)
        elif kind == "first_audio_out":
            turns[current].setdefault("first_audio_out", row)
        elif kind == "turn_done":
            # Do NOT close the turn here. ``turn_done`` is emitted when the
            # agent loop publishes the final AIMessage, but ``first_audio_out``
            # and ``tts_idle`` fire later (TTS chain is async, cross-process).
            # The next ``user_audio_end`` re-opens with a new turn_id, which
            # is the real boundary in this cascade flow.
            turns[current]["turn_done"] = row
        elif kind == "turn_timeout":
            turns[current]["turn_timeout"] = row
            current = None
        elif row.get("event") == "MCP tool done":
            duration = _parse_duration(row.get("duration"))
            turns[current]["mcp_tools"].append(
                {"tool": row.get("tool", "?"), "duration": duration}
            )

    return dict(turns)


def compute_per_turn_metrics(turns: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Convert grouped events into per-turn numeric metrics.

    Two headline indicators are computed:

    - ``agent_first_call_s``: ``first_tool_call.t - user_audio_end.t``.
      Measures STT + LLM latency. Defined for every turn that has a
      ``first_tool_call`` event.
    - ``speak_tts_s``: ``first_audio_out.t - first speak_invoke.t``.
      Measures TTS synth + first-chunk latency. Defined only for turns
      where ``speak`` was invoked.

    Splitting these two intervals avoids the contamination that ``e2e_response_s``
    suffered when motion preceded speak in a later LLM round.
    """
    metrics: dict[str, dict[str, Any]] = {}
    for turn_id, data in turns.items():
        ue = data.get("user_audio_end")
        if not ue:
            continue
        t0 = ue.get("t")

        ftc = data.get("first_tool_call")
        fao = data.get("first_audio_out")
        stt = data.get("stt_done")
        td = data.get("turn_done")
        speak_invokes = data.get("speak_invokes", [])

        def _delta(row: dict[str, Any] | None, t_base: float | None) -> float | None:
            if row is None or t_base is None:
                return None
            t = row.get("t")
            return (t - t_base) if t is not None else None

        speak_tts_s: float | None = None
        if speak_invokes and fao is not None:
            speak_tts_s = _delta(fao, speak_invokes[0].get("t"))

        llm_steps = data.get("llm_steps", [])
        first_tokens = data.get("llm_first_tokens", [])
        stt_s = _parse_duration(stt.get("duration_s")) if stt else None

        ttft_s: float | None = None
        if first_tokens and t0 is not None and stt_s is not None:
            ttft_s = first_tokens[0].get("t", 0.0) - t0 - stt_s

        llm_step_0_s = (
            _parse_duration(llm_steps[0].get("duration_s")) if llm_steps else None
        )
        llm_step_last_s = (
            _parse_duration(llm_steps[-1].get("duration_s")) if llm_steps else None
        )

        prompt_tokens_vals = [s.get("input_tokens") for s in llm_steps]
        completion_tokens_vals = [s.get("output_tokens") for s in llm_steps]
        prompt_tokens = (
            sum(v for v in prompt_tokens_vals if v is not None)
            if any(v is not None for v in prompt_tokens_vals) else None
        )
        completion_tokens = (
            sum(v for v in completion_tokens_vals if v is not None)
            if any(v is not None for v in completion_tokens_vals) else None
        )

        llm_durations = [
            _parse_duration(s.get("duration_s")) or 0.0 for s in llm_steps
        ]
        tools_durations = [
            _parse_duration(s.get("duration_s")) or 0.0 for s in data.get("tools_steps", [])
        ]
        metrics[turn_id] = {
            "fixture_id": ue.get("fixture_id"),
            "run_idx": ue.get("run_idx"),
            "warmup": bool(ue.get("warmup")),
            "audio_seconds": ue.get("audio_seconds"),
            "agent_first_call_s": _delta(ftc, t0),
            "speak_tts_s": speak_tts_s,
            "e2e_first_audio_s": _delta(fao, t0),
            "first_tool_call_s": _delta(ftc, t0),
            "stt_s": stt_s,
            "ttft_s": ttft_s,
            "llm_step_0_s": llm_step_0_s,
            "llm_step_last_s": llm_step_last_s,
            "llm_total_s": sum(llm_durations) if llm_durations else None,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "tools_total_s": sum(tools_durations) if tools_durations else None,
            "turn_total_s": _parse_duration(td.get("duration_s")) if td else None,
            "n_mcp_tools": len(data.get("mcp_tools", [])),
            "mcp_tools": data.get("mcp_tools", []),
            "timeout": "turn_timeout" in data,
        }
    return metrics



def _summarize(values: list[float | None]) -> dict[str, float]:
    """n / mean / p50 / p95 / max / min over a list, ignoring None and NaN."""
    finite = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    n = len(finite)
    if n == 0:
        return {
            "n": 0,
            "mean": float("nan"),
            "p50": float("nan"),
            "p95": float("nan"),
            "max": float("nan"),
            "min": float("nan"),
        }
    return {
        "n": n,
        "mean": statistics.fmean(finite),
        "p50": _percentile(finite, 0.5),
        "p95": _percentile(finite, 0.95),
        "max": max(finite),
        "min": min(finite),
    }


_AGENTIC_LOCAL_TTS_METRIC_KEYS = (
    "e2e_first_audio_s",
    "agent_first_call_s",
    "speak_tts_s",
    "stt_s",
    "ttft_s",
    "llm_step_0_s",
    "llm_step_last_s",
    "llm_total_s",
    "prompt_tokens",
    "completion_tokens",
    "tools_total_s",
    "turn_total_s",
)

_VOICE_LIVE_METRIC_KEYS = (
    "e2e_first_audio_s",
    "first_tool_call_s",
)


def detect_mode(run_dir: Path) -> str:
    """Infer 'agentic-local-tts' or 'voice-live' from the run-dir basename."""
    name = Path(run_dir).name
    if "voice-live" in name:
        return "voice-live"
    return "agentic-local-tts"


def _metric_keys_for_mode(mode: str) -> tuple[str, ...]:
    if mode == "voice-live":
        return _VOICE_LIVE_METRIC_KEYS
    return _AGENTIC_LOCAL_TTS_METRIC_KEYS


def aggregate(metrics: dict[str, dict[str, Any]], mode: str = "agentic-local-tts") -> dict[str, Any]:
    """Aggregate non-warmup turns into a single pool.

    Returns the count of live turns and a per-metric summary
    (n / mean / p50 / p95 / max / min) for every key relevant to ``mode``.
    """
    live = [m for m in metrics.values() if not m["warmup"]]
    keys = _metric_keys_for_mode(mode)
    return {
        "n_turns": len(live),
        "metrics": {k: _summarize([m.get(k) for m in live]) for k in keys},
    }


def read_run_meta(jsonl_path: Path) -> dict[str, Any]:
    """Find the first run_meta event in main.jsonl, return its payload (envelope stripped).

    Returns an empty dict if no run_meta event is present (older runs).
    """
    if not jsonl_path.exists():
        return {}
    for row in _read_jsonl(jsonl_path):
        if row.get("event_kind") == "run_meta":
            payload = {k: v for k, v in row.items()
                       if k not in ("event_kind", "turn_id", "t",
                                    "event", "level", "logger", "timestamp",
                                    "func_name", "lineno")}
            return payload
    return {}


def _pick_run(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    # Run dirs are named logs/{ts}-{blueprint}-{profile}; any blueprint whose
    # name contains "agentic-local-tts" (incl. the *-detection variant) matches.
    # Sort by mtime, not name: logs/ mixes bench timestamps (2026-06-03-...) with
    # `dimos run` ids (20260602-...), so lexical order != chronological order.
    candidates = [d for d in Path("logs").glob("*agentic-local-tts*") if d.is_dir()]
    if not candidates:
        sys.exit("no logs/*agentic-local-tts* runs found")
    return max(candidates, key=lambda d: d.stat().st_mtime)


def _print_table(title: str, rows: dict[str, dict[str, float]]) -> None:
    print(f"\n== {title} ==")
    print(f"{'metric':<18} {'n':>4} {'mean':>8} {'p50':>8} {'p95':>8} {'max':>8} {'min':>8}")
    for k, s in rows.items():
        low_n = " [low-n]" if 0 < s["n"] < 5 else ""
        print(
            f"{k:<18} {s['n']:>4} {s['mean']:>8.3f} {s['p50']:>8.3f} "
            f"{s['p95']:>8.3f} {s['max']:>8.3f} {s['min']:>8.3f}{low_n}"
        )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", nargs="?", default=None)
    parser.add_argument("--json", dest="json_out", default=None)
    parser.add_argument(
        "--config",
        choices=("auto", "agentic-local-tts", "voice-live"),
        default="auto",
        help="Analyzer mode. 'auto' infers from run-dir basename.",
    )
    args = parser.parse_args(argv[1:])

    run_dir = _pick_run(args.run_dir)
    jsonl = run_dir / "main.jsonl"
    if not jsonl.exists():
        sys.exit(f"missing {jsonl}")

    mode = args.config if args.config != "auto" else detect_mode(run_dir)

    meta = read_run_meta(jsonl)
    if meta:
        # Prefer new keys (config_name + embedded config dict). Fall back to
        # legacy flat keys (label / model / base_url) for older bench runs.
        cfg = meta.get("config") or {}
        llm_cfg = cfg.get("llm") or {}
        name = meta.get("config_name") or meta.get("label") or "?"
        model = llm_cfg.get("model") or meta.get("model") or "?"
        base_url = llm_cfg.get("base_url") or meta.get("base_url") or "?"
        print(f"config: {name}  model: {model}  base_url: {base_url}")

    turns = build_turns(jsonl)
    metrics = compute_per_turn_metrics(turns)
    agg = aggregate(metrics, mode=mode)

    live = [m for m in metrics.values() if not m["warmup"]]
    print(f"run: {run_dir}")
    print(f"mode: {mode}")
    print(f"turns analyzed (non-warmup): {len(live)} / {len(metrics)} total")

    # Headline indicators (mode-gated).
    print("\n== headline ==")
    efa = agg["metrics"]["e2e_first_audio_s"]
    print(
        f"e2e_first_audio_s    n={efa['n']}/{len(live)}  "
        f"p50={efa['p50']:.2f}s  p95={efa['p95']:.2f}s"
    )

    if mode == "agentic-local-tts":
        afc = agg["metrics"]["agent_first_call_s"]
        sts = agg["metrics"]["speak_tts_s"]
        print(
            f"agent_first_call_s   n={afc['n']}/{len(live)}  "
            f"p50={afc['p50']:.2f}s  p95={afc['p95']:.2f}s"
        )
        n_no_speak = len(live) - sts["n"]
        no_speak_note = f"  ({n_no_speak} turn(s) had no speak)" if n_no_speak else ""
        print(
            f"speak_tts_s          n={sts['n']}/{len(live)}  "
            f"p50={sts['p50']:.2f}s  p95={sts['p95']:.2f}s{no_speak_note}"
        )
    else:  # voice-live
        ftc = agg["metrics"]["first_tool_call_s"]
        n_no_tool = len(live) - ftc["n"]
        no_tool_note = (
            f"  ({n_no_tool} turn(s) had no tool call)" if n_no_tool else ""
        )
        print(
            f"first_tool_call_s    n={ftc['n']}/{len(live)}  "
            f"p50={ftc['p50']:.2f}s  p95={ftc['p95']:.2f}s{no_tool_note}"
        )

    _print_table(f"all turns (n={len(live)})", agg["metrics"])

    # Per-tool mcp_tool:* summary across all live turns (agentic-local-tts only).
    if mode == "agentic-local-tts":
        tool_buckets: dict[str, list[float]] = defaultdict(list)
        for m_id, m in metrics.items():
            if m["warmup"]:
                continue
            for e in turns[m_id].get("mcp_tools", []):
                if e["duration"] is not None:
                    tool_buckets[f"mcp_tool:{e['tool']}"].append(e["duration"])
        if tool_buckets:
            tool_rows = {k: _summarize(v) for k, v in sorted(tool_buckets.items())}
            _print_table("mcp_tool:*", tool_rows)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(
                {
                    "run_dir": str(run_dir),
                    "n_turns": len(metrics),
                    "n_live": len(live),
                    "per_turn": metrics,
                    "aggregate": agg,
                },
                indent=2,
                default=str,
            )
        )
        print(f"\nJSON written to {args.json_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
