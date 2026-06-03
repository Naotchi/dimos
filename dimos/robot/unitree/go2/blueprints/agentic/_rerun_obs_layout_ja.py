#!/usr/bin/env python3
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

"""Fork-local Rerun layout override for OBS recordings (diagonal 2x2).

Why this exists
---------------
The active Go2 layout is baked in the **upstream** file
``go2/blueprints/basic/unitree_go2_basic.py`` (``_go2_rerun_blueprint`` ->
``rerun_config["blueprint"]`` on ``RerunBridgeModule``). We do not want to fork
that file's logic, so instead we swap *only* the ``blueprint`` field on the
existing ``RerunBridgeModule`` atom, preserving all the other upstream
rerun_config (``visual_override``, ``static``, ``max_hz``, ``pubsubs``).

The toggle lands on ``global_config.rerun_obs_layout`` (alongside the other
``rerun_*`` display flags ``rerun_open`` / ``rerun_web``); this module just
consumes that boolean. It is owned by the **profile**, which declares
``"g": {"rerun_obs_layout": true}``; fork ``apply_profile`` propagates that to
``global_config`` before this module is imported (the swap is import-time).

Layout (diagonal 2x2), designed for OBS picture-in-picture overlays:

    +----------+----------+
    | Camera   | (filler) |   <- top-right covered by OBS terminal wipe
    |  (2D)    |          |
    +----------+----------+
    | (filler) | Point    |   <- bottom-left covered by OBS webcam wipe
    |          | cloud 3D |
    +----------+----------+

The off-diagonal corners are cheap blank 2D views (nothing logged at their
origin, solid-black background) so they cost no GPU like a duplicated 3D view
would, and read as clean dark panels under the OBS overlays.
"""

from dataclasses import replace
from typing import Any

from dimos.core.coordination.blueprints import Blueprint
from dimos.core.global_config import global_config
from dimos.visualization.rerun.bridge import RerunBridgeModule

__all__ = [
    "obs_diagonal_blueprint",
    "with_obs_layout_if_enabled",
    "with_rerun_blueprint",
]

# Filler tiles live at this (never-logged) entity path so they render empty.
_FILLER_ORIGIN = "obs/overlay_placeholder"


def obs_diagonal_blueprint() -> Any:
    """Diagonal 2x2: Camera top-left, point cloud (3D) bottom-right.

    Imports rerun lazily (mirrors ``_go2_rerun_blueprint`` upstream) so the
    module stays importable in worker processes that never touch the viewer.
    """
    import rerun as rr
    import rerun.blueprint as rrb

    black = rrb.Background(kind="SolidColor", color=[0, 0, 0])

    camera = rrb.Spatial2DView(origin="world/color_image", name="Camera")

    point_cloud = rrb.Spatial3DView(
        origin="world",
        name="Point cloud",
        background=black,
        line_grid=rrb.LineGrid3D(
            plane=rr.components.Plane3D.XY.with_distance(0.5),
        ),
        overrides={
            # Raw lidar is noisy / redundant with the mapped cloud; hide it
            # (parity with the upstream go2 layout).
            "world/lidar": rrb.EntityBehavior(visible=False),
        },
    )

    def _filler(name: str) -> Any:
        # Blank dark tile; covered by an OBS overlay during recording.
        return rrb.Spatial2DView(origin=_FILLER_ORIGIN, name=name, background=black)

    return rrb.Blueprint(
        rrb.Vertical(
            rrb.Horizontal(camera, _filler("OBS · terminal"), column_shares=[1, 1]),
            rrb.Horizontal(_filler("OBS · webcam"), point_cloud, column_shares=[1, 1]),
            row_shares=[1, 1],
        ),
        # Maximize the diagonal: hide the surrounding Rerun panels.
        #
        # IMPORTANT: the bridge wraps every blueprint in ``_with_graph_tab``
        # (upstream ``rerun/bridge.py``), which rebuilds the Blueprint from
        # ``root_container`` and forwards ONLY ``auto_layout`` / ``auto_views`` /
        # ``collapse_panels`` — any explicit ``*Panel(state=...)`` we pass here
        # is silently dropped. ``collapse_panels=True`` is the one panel control
        # that survives that round-trip; per Rerun it fully hides the left
        # (blueprint) and right (selection) panels and leaves a simplified
        # bottom time panel.
        collapse_panels=True,
    )


def with_rerun_blueprint(stack: Blueprint, factory: Any) -> Blueprint:
    """Return a copy of ``stack`` with the RerunBridgeModule's blueprint swapped.

    Only the ``blueprint`` kwarg of the existing ``RerunBridgeModule`` atom is
    replaced; every other upstream rerun_config field is kept intact.
    """
    new_atoms = []
    found = False
    for atom in stack.blueprints:
        if atom.module is RerunBridgeModule:
            atom = replace(atom, kwargs={**atom.kwargs, "blueprint": factory})
            found = True
        new_atoms.append(atom)
    if not found:
        raise RuntimeError(
            "with_rerun_blueprint: no RerunBridgeModule in stack "
            "(is the viewer backend 'rerun'?)"
        )
    return replace(stack, blueprints=tuple(new_atoms))


def with_obs_layout_if_enabled(stack: Blueprint) -> Blueprint:
    """Apply the diagonal OBS layout iff ``global_config.rerun_obs_layout`` is set.

    The toggle is owned by config (env ``RERUN_OBS_LAYOUT``); this module just
    consumes the typed boolean — config owns the decision.
    """
    if global_config.rerun_obs_layout:
        return with_rerun_blueprint(stack, obs_diagonal_blueprint)
    return stack
