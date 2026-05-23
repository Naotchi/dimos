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

"""Resolve and apply named profiles at ``configs/profiles/<name>.json``.

A profile is a single committed JSON file holding module parameters
(category A blueprint_args) including ``timedmcpclient.endpoint``
(``"local"`` | ``"cloud"``). Endpoint credentials are NOT in the profile;
they live in the root ``.env`` as ``DIMOS_LLM_{LOCAL,CLOUD}_{BASE_URL,API_KEY}``.

``apply_profile`` reads the selected endpoint and copies the matching pair
into the generic ``DIMOS_LLM_BASE_URL`` / ``DIMOS_LLM_API_KEY`` that
``mirror_llm_endpoint_env()`` mirrors into ``OPENAI_*`` at blueprint import.
The caller is responsible for having loaded the root ``.env`` first (the CLI
does this at ``dimos.py`` import; the bench calls ``load_dotenv()`` itself).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PROFILES_ROOT = Path("configs/profiles")


def resolve_profile(name: str) -> Path:
    """Resolve a profile name to ``configs/profiles/<name>.json``.

    Raises ValueError on unsafe names, FileNotFoundError if absent.
    """
    if not name or "/" in name or ".." in name or name.startswith("."):
        raise ValueError(f"Invalid profile name: {name!r}")

    path = (PROFILES_ROOT / f"{name}.json").resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Profile {name!r} not found: {path} does not exist")
    return path


def apply_profile(name: str) -> Path:
    """Apply a profile: select the LLM endpoint env, return its config path.

    Reads ``timedmcpclient.endpoint`` (default ``"local"``) and copies the
    matching ``DIMOS_LLM_<ENDPOINT>_{BASE_URL,API_KEY}`` pair into the generic
    ``DIMOS_LLM_{BASE_URL,API_KEY}``. The profile carries no secrets.
    """
    config_path = resolve_profile(name)
    cfg = json.loads(config_path.read_text())
    endpoint = cfg.get("timedmcpclient", {}).get("endpoint", "local")
    _select_endpoint_env(endpoint)
    return config_path


def _select_endpoint_env(endpoint: str) -> None:
    """Copy ``DIMOS_LLM_<ENDPOINT>_{BASE_URL,API_KEY}`` → ``DIMOS_LLM_{...}``.

    Only copies a value when the source var is set, so an unfilled root ``.env``
    leaves the generic vars untouched (mirror then uses the OpenAI default).
    """
    prefix = f"DIMOS_LLM_{endpoint.upper()}_"
    for suffix in ("BASE_URL", "API_KEY"):
        val = os.environ.get(prefix + suffix)
        if val is not None:
            os.environ[f"DIMOS_LLM_{suffix}"] = val
