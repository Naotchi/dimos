#!/usr/bin/env python
"""Aggregate end-to-end latency from a unitree-go2-agentic-ja bench run.

Usage:
    python scripts/bench_agentic_ja.py [logs/<run-dir>] [--json FILE]

Without a run-dir argument, picks the latest logs/*-bench-agentic-ja/, then
falls back to logs/*-unitree-go2-agentic-ja/ for backward compatibility.

Reads main.jsonl and prints per-turn end-to-end latencies and stage breakdown:
  - e2e_response_s  (user_audio_end -> first_audio_out)
  - e2e_motion_s    (user_audio_end -> first_motion_tool)
  - stt_s / llm_total_s / tools_total_s / turn_total_s
  - mcp_tool:*      (per-tool durations from upstream "MCP tool done" events,
                     bucketed into turns by timestamp range)

Aggregates overall and per category (speak_only / motion_only / both).
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
    """Group bench events by turn_id; bucket mcp_tool:* by timestamp range."""
    rows = _read_jsonl(jsonl_path)
    turns: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"llm_steps": [], "tools_steps": [], "mcp_tools": []}
    )

    # First pass: events with turn_id.
    for row in rows:
        turn_id = row.get("turn_id")
        kind = row.get("event_kind")
        if not turn_id or not kind:
            continue
        if kind == "user_audio_end":
            turns[turn_id]["user_audio_end"] = row
        elif kind == "stt_done":
            turns[turn_id]["stt_done"] = row
        elif kind == "llm_step":
            turns[turn_id]["llm_steps"].append(row)
        elif kind == "tools_step" or (kind.endswith("_step") and kind != "llm_step"):
            turns[turn_id]["tools_steps"].append(row)
        elif kind == "first_motion_tool":
            turns[turn_id]["first_motion_tool"] = row
        elif kind == "first_audio_out":
            turns[turn_id]["first_audio_out"] = row
        elif kind == "turn_done":
            turns[turn_id]["turn_done"] = row
        elif kind == "turn_timeout":
            turns[turn_id]["turn_timeout"] = row

    # Second pass: bucket mcp_tool:* by timestamp range [user_audio_end.t, turn_done.t].
    ranges = []
    for turn_id, data in turns.items():
        if "user_audio_end" in data and "turn_done" in data:
            ranges.append((data["user_audio_end"]["t"], data["turn_done"]["t"], turn_id))
    ranges.sort()

    for row in rows:
        if row.get("event") != "MCP tool done":
            continue
        t = row.get("t")
        if t is None:
            continue
        for t0, t1, turn_id in ranges:
            if t0 <= t <= t1:
                duration = _parse_duration(row.get("duration"))
                turns[turn_id]["mcp_tools"].append(
                    {"tool": row.get("tool", "?"), "duration": duration, "t": t}
                )
                break

    return dict(turns)


def compute_per_turn_metrics(turns: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Convert grouped events into per-turn numeric metrics."""
    metrics: dict[str, dict[str, Any]] = {}
    for turn_id, data in turns.items():
        ue = data.get("user_audio_end")
        if not ue:
            continue
        t0 = ue["t"]

        fao = data.get("first_audio_out")
        fmt = data.get("first_motion_tool")
        stt = data.get("stt_done")
        td = data.get("turn_done")

        llm_durations = [
            _parse_duration(s.get("duration_s")) or 0.0 for s in data.get("llm_steps", [])
        ]
        tools_durations = [
            _parse_duration(s.get("duration_s")) or 0.0 for s in data.get("tools_steps", [])
        ]
        metrics[turn_id] = {
            "fixture_id": ue.get("fixture_id"),
            "category": ue.get("category"),
            "run_idx": ue.get("run_idx"),
            "warmup": bool(ue.get("warmup")),
            "audio_seconds": ue.get("audio_seconds"),
            "e2e_response_s": (fao["t"] - t0) if fao else None,
            "e2e_motion_s": (fmt["t"] - t0) if fmt else None,
            "stt_s": _parse_duration(stt.get("duration_s")) if stt else None,
            "llm_total_s": sum(llm_durations) if llm_durations else None,
            "tools_total_s": sum(tools_durations) if tools_durations else None,
            "turn_total_s": _parse_duration(td.get("duration_s")) if td else None,
            "n_mcp_tools": len(data.get("mcp_tools", [])),
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


_METRIC_KEYS = (
    "e2e_response_s",
    "e2e_motion_s",
    "stt_s",
    "llm_total_s",
    "tools_total_s",
    "turn_total_s",
)


def aggregate(metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Aggregate non-warmup turns: overall + per category + mcp_tool:* tallies."""
    live = [m for m in metrics.values() if not m["warmup"]]

    overall = {k: _summarize([m.get(k) for m in live]) for k in _METRIC_KEYS}

    by_cat: dict[str, dict[str, Any]] = defaultdict(dict)
    for cat in {m.get("category") for m in live if m.get("category")}:
        cat_metrics = [m for m in live if m.get("category") == cat]
        by_cat[cat] = {k: _summarize([m.get(k) for m in cat_metrics]) for k in _METRIC_KEYS}

    return {"overall": overall, "by_category": dict(by_cat)}


def _pick_run(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    candidates = sorted(Path("logs").glob("*-bench-agentic-ja"))
    if not candidates:
        candidates = sorted(Path("logs").glob("*-unitree-go2-agentic-ja"))
    if not candidates:
        sys.exit("no logs/*-bench-agentic-ja or logs/*-unitree-go2-agentic-ja runs found")
    return candidates[-1]


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
    args = parser.parse_args(argv[1:])

    run_dir = _pick_run(args.run_dir)
    jsonl = run_dir / "main.jsonl"
    if not jsonl.exists():
        sys.exit(f"missing {jsonl}")

    turns = build_turns(jsonl)
    metrics = compute_per_turn_metrics(turns)
    agg = aggregate(metrics)

    live = [m for m in metrics.values() if not m["warmup"]]
    print(f"run: {run_dir}")
    print(f"turns analyzed (non-warmup): {len(live)} / {len(metrics)} total")

    _print_table("overall", agg["overall"])
    for cat, rows in sorted(agg["by_category"].items()):
        _print_table(f"category: {cat}", rows)

    # Per-tool mcp_tool:* summary across all live turns.
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
