# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

import os


def _resolve_endpoint() -> tuple[str, str, str]:
    """Resolve (base_url, model, api_key) from env.

    Prefers SHELF_VLM_* (shelf-specific override) and falls back to the agent's
    DIMOS_LLM_* so the shelf grounding shares the same local Qwen by default.
    """
    base_url = os.getenv("SHELF_VLM_BASE_URL") or os.getenv("DIMOS_LLM_BASE_URL")
    model = os.getenv("SHELF_VLM_MODEL") or os.getenv("DIMOS_LLM_MODEL")
    api_key = (
        os.getenv("SHELF_VLM_API_KEY")
        or os.getenv("DIMOS_LLM_API_KEY")
        or "lm-studio"  # LM Studio ignores the key; OpenAI SDK requires a non-empty string
    )
    if not base_url:
        raise ValueError("SHELF_VLM_BASE_URL or DIMOS_LLM_BASE_URL must be set")
    if not model:
        raise ValueError("SHELF_VLM_MODEL or DIMOS_LLM_MODEL must be set")
    return base_url, model, api_key
