# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Fork-local LLM endpoint switching via environment variables.

The fork-side blueprints (``*_ja`` / ``*_local_tts``) drive ``create_agent``
through an **OpenAI-compatible** ChatCompletion endpoint. Switching between
Azure OpenAI (v1 API), OpenAI cloud, and a local vLLM/Ollama server is then
just a matter of pointing ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` somewhere
else and giving the right ``model`` (or Azure deployment name).

We expose three env vars so callers never have to know which one the
underlying client reads:

- ``DIMOS_LLM_MODEL``    — model name (or Azure deployment name)
- ``DIMOS_LLM_BASE_URL`` — OpenAI-compatible base URL, ending in ``/v1``
- ``DIMOS_LLM_API_KEY``  — bearer token / API key

``mirror_llm_endpoint_env()`` sets ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY``
from the ``DIMOS_LLM_*`` counterparts so ``langchain``'s ``init_chat_model``
picks them up. The model name is owned by the module config, not resolved here.

Examples::

    # Azure OpenAI v1 (OpenAI-compatible) endpoint
    DIMOS_LLM_MODEL=gpt-4o-deploy
    DIMOS_LLM_BASE_URL=https://<resource>.openai.azure.com/openai/v1
    DIMOS_LLM_API_KEY=<azure-key>

    # Local vLLM on DGX Spark
    DIMOS_LLM_MODEL=Qwen/Qwen3-30B-A3B
    DIMOS_LLM_BASE_URL=http://dgx-spark:8000/v1
    DIMOS_LLM_API_KEY=dummy

    # OpenAI cloud
    DIMOS_LLM_MODEL=gpt-4o-mini
    DIMOS_LLM_BASE_URL=https://api.openai.com/v1
    DIMOS_LLM_API_KEY=sk-...
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o"


def mirror_llm_endpoint_env() -> None:
    """Mirror ``DIMOS_LLM_BASE_URL`` / ``DIMOS_LLM_API_KEY`` into ``OPENAI_*``.

    Category B/C endpoint wiring (deploy-dependent). Existing ``OPENAI_*``
    values are only overwritten when the ``DIMOS_*`` counterpart is set.

    The model string is intentionally NOT resolved here — it is a category-A
    value owned by the module config (``TimedMcpClientConfig.model``), seeded
    from ``DIMOS_LLM_MODEL`` and overridable by the profile ``config.json``.
    """
    base_url = os.environ.get("DIMOS_LLM_BASE_URL")
    api_key = os.environ.get("DIMOS_LLM_API_KEY")

    if base_url:
        os.environ["OPENAI_BASE_URL"] = base_url
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key

    effective_base = os.environ.get("OPENAI_BASE_URL", "<openai default>")
    logger.info("[LLM] endpoint base_url=%s", effective_base)
