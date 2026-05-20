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

"""Resolve and apply named profiles under ``configs/profiles/<name>/``.

A profile bundles ``.env`` (deploy-dependent secrets/endpoints, category B/C)
and ``config.json`` (module parameters = blueprint_args, category A). Both
``dimos run --profile`` and the bench runner share this loader so they boot
with the identical env + config resolution path.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

PROFILES_ROOT = Path("configs/profiles")


def resolve_profile(name: str) -> tuple[Path | None, Path | None]:
    """Resolve a profile name to (env_path, config_path).

    Either may be None if absent. Raises FileNotFoundError if neither
    exists, ValueError on unsafe names.
    """
    if not name or "/" in name or ".." in name or name.startswith("."):
        raise ValueError(f"Invalid profile name: {name!r}")

    pdir = (PROFILES_ROOT / name).resolve()
    env_path = pdir / ".env"
    config_path = pdir / "config.json"

    env_exists = env_path.is_file()
    config_exists = config_path.is_file()
    if not env_exists and not config_exists:
        raise FileNotFoundError(
            f"Profile {name!r} not found: neither {env_path} nor {config_path} exists"
        )

    return (env_path if env_exists else None, config_path if config_exists else None)


def apply_profile(name: str) -> Path | None:
    """Apply a profile: load its .env with override, return its config.json path.

    The .env (if present) is loaded into process env with override=True so
    the profile wins over any pre-existing shell variables. Returns the
    config.json Path if the profile has one, else None.
    """
    env_path, config_path = resolve_profile(name)
    if env_path is not None:
        load_dotenv(env_path, override=True)
    return config_path
