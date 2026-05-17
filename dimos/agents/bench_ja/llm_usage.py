"""Pure helpers for extracting token-usage data from LangChain AIMessages."""

from __future__ import annotations

from typing import Any, Iterable


def extract_usage(msgs: Iterable[Any]) -> dict[str, Any]:
    """Sum input_tokens / output_tokens across messages that expose usage_metadata.

    Returns a dict with three keys:
      - input_tokens (int | None)
      - output_tokens (int | None)
      - available (bool): False when no message in the iterable carried usage_metadata

    "Available=False" lets bench_ja log the keys with None values, which the
    analyzer can then distinguish from "0 tokens were used".
    """
    in_total = 0
    out_total = 0
    seen = False
    for m in msgs:
        meta = getattr(m, "usage_metadata", None)
        if not meta:
            continue
        seen = True
        in_total += int(meta.get("input_tokens", 0) or 0)
        out_total += int(meta.get("output_tokens", 0) or 0)
    if not seen:
        return {"input_tokens": None, "output_tokens": None, "available": False}
    return {"input_tokens": in_total, "output_tokens": out_total, "available": True}
