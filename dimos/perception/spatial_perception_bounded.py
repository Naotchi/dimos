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

"""SpatialMemory variant with a bounded image store.

Upstream VisualMemory.images is an unbounded dict, so long sessions grow
memory until the host freezes. This variant evicts the oldest entries from
both VisualMemory and the ChromaDB collection once the count exceeds
``visual_memory_maxlen``.
"""

from __future__ import annotations

from typing import Any

from dimos.perception.spatial_perception import SpatialConfig, SpatialMemory
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class SpatialConfigBounded(SpatialConfig):
    visual_memory_maxlen: int = 500


class SpatialMemoryBounded(SpatialMemory):
    """SpatialMemory that evicts the oldest entries past ``visual_memory_maxlen``."""

    config: SpatialConfigBounded

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._visual_memory_maxlen = self.config.visual_memory_maxlen

    def _process_frame(self) -> None:
        super()._process_frame()
        self._evict_overflow()

    def _evict_overflow(self) -> None:
        vm = getattr(self, "_visual_memory", None)
        images = getattr(vm, "images", None) if vm is not None else None
        if not isinstance(images, dict):
            return

        excess = len(images) - self._visual_memory_maxlen
        if excess <= 0:
            return

        # Python 3.7+ dicts preserve insertion order — oldest first.
        victims = [next(iter(images)) for _ in range(excess)]
        for vid in victims:
            images.pop(vid, None)

        try:
            self.vector_db.image_collection.delete(ids=victims)
        except Exception as e:
            logger.warning("ChromaDB delete failed during eviction: %s", e)

        logger.info(
            "Evicted %d oldest entries (cap=%d)", excess, self._visual_memory_maxlen
        )


__all__ = ["SpatialConfigBounded", "SpatialMemoryBounded"]
