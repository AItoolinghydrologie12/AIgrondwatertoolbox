"""Shared state of the Grondwatertoolbox page: session keys, the default example and the
drawn objects on the current grid."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

import streamlit as st
from grid_ui import absolute_lines, to_cells, wall_faces, wc_geometry_cached
from streamlit.errors import StreamlitAPIException

from gwproducten.core.layers import LayerStack
from gwproducten.products import peilopzet_grid as p3

KEY = "p3"
DEFAULTS = p3.Params()
DEFAULT_RADIUS = p3.WellGroup().radius  # also for well groups from older projects
WC_FIELDS = [
    "stage_ref", "stage_new", "bottom", "width", "length", "entry_resistance", "conductance",
]  # fmt: skip
MAP_CONFIG = {"scrollZoom": True, "displaylogo": False}
DRAWN = ("wcs", "drain", "wells", "ghb", "chd", "walls", "zones")  # what "Ongedaan maken" restores
MAX_UNDO = 10

ss = st.session_state


def example() -> list[dict[str, Any]]:
    """Default example: north-south lines at fractions of the model width, so they stay at
    1/3 and 2/3 when the model or cell size changes."""
    out = []
    for frac, wc in p3.DEFAULT_WATERCOURSE_SPECS:
        d = {f: getattr(wc, f) for f in ["name", "kind", *WC_FIELDS]}
        d["lines"], d["rel_lines"] = [], [[(frac, 0.0), (frac, 1.0)]]
        d["points"], d["manual"], d["manual_cell"] = set(), {}, DEFAULTS.cell
        out.append(d)
    return out


def reset(wcs: list[dict[str, Any]]) -> None:
    if f"{KEY}_checkpoint" in ss:  # not at the first start
        _push_undo()
    ss[f"{KEY}_wcs"], ss[f"{KEY}_drain"], ss[f"{KEY}_line"] = wcs, set(), []
    ss[f"{KEY}_wells"], ss[f"{KEY}_ghb"], ss[f"{KEY}_chd"] = [], [], []
    ss[f"{KEY}_zones"] = []
    ss[f"{KEY}_walls"] = []
    ss[f"{KEY}_ver"] = ss.get(f"{KEY}_ver", 0) + 1


def bump() -> None:
    """After a change of the drawing: it can be undone; new widget keys clear the map
    selection and the tables."""
    _push_undo()
    ss[f"{KEY}_ver"] += 1


def checkpoint() -> None:
    """At the start of every page run: a copy of the drawing, the undo point of a change made
    in this run (the drawing panel reruns on its own only for the line in progress)."""
    ss[f"{KEY}_checkpoint"] = copy.deepcopy(
        {k: ss[f"{KEY}_{k}"] for k in DRAWN if f"{KEY}_{k}" in ss}
    )


def _push_undo() -> None:
    stack = ss.setdefault(f"{KEY}_undo_stack", [])
    point = ss.get(f"{KEY}_checkpoint")
    if point is not None and (not stack or stack[-1] is not point):  # once per run
        stack.append(point)
        del stack[:-MAX_UNDO]


def can_undo() -> bool:
    return bool(ss.get(f"{KEY}_undo_stack"))


def undo() -> None:
    """Restore the drawing from before the last change."""
    stack = ss.get(f"{KEY}_undo_stack", [])
    if not stack:
        return
    for k, value in copy.deepcopy(stack.pop()).items():
        ss[f"{KEY}_{k}"] = value
    ss[f"{KEY}_line"] = []
    ss[f"{KEY}_ver"] += 1


def clear_undo() -> None:
    """After a change of the layers (split) or loading a project the old drawings no longer
    fit: forget them."""
    ss[f"{KEY}_undo_stack"] = []


def rerun_fragment() -> None:
    """Rerun only the current fragment (drawing panel, result panel); Streamlit refuses that
    during a full-page run (e.g. in tests), and then the whole page reruns."""
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:
        st.rerun()


@dataclass(frozen=True)
class Grid:
    """The current grid; maps the drawn objects (in metres) to its cells."""

    cell: float
    nrow: int
    ncol: int

    def watercourses(self) -> list[dict[str, Any]]:
        out = []
        for wc in ss[f"{KEY}_wcs"]:
            cells, lengths, offsets = wc_geometry_cached(wc, self.cell, self.nrow, self.ncol)
            lines = absolute_lines(wc, self.ncol * self.cell, self.nrow * self.cell)
            out.append(
                {
                    **wc,
                    "cells": cells,
                    "lengths": lengths,
                    "bottom_offsets": offsets,
                    "lines": lines,
                }
            )
        return out

    def groups(self, name: str) -> list[dict[str, Any]]:
        """Well, GHB or CHD groups with their cells on the current grid."""
        return [
            {**g, "cells": to_cells(g["points"], self.cell, self.nrow, self.ncol)}
            for g in ss[f"{KEY}_{name}"]
        ]

    def zones(self, zone_values: dict[int, Any]) -> list[dict[str, Any]]:
        """Zones with their cells on the current grid (zones without cells or values are
        left out)."""
        out = []
        for z in ss[f"{KEY}_zones"]:
            cells = to_cells(z["points"], self.cell, self.nrow, self.ncol)
            if cells and z["id"] in zone_values:
                out.append({**z, "cells": cells})
        return out

    def walls(self) -> list[dict[str, Any]]:
        """Walls with their faces on the current grid."""
        return [
            {**w, "faces": wall_faces(w, self.cell, self.nrow, self.ncol)}
            for w in ss.get(f"{KEY}_walls", [])
        ]

    def drain_cells(self) -> tuple:
        return to_cells(ss[f"{KEY}_drain"], self.cell, self.nrow, self.ncol)


def build_params(
    grid: Grid,
    stack: LayerStack,
    zone_values: dict[int, tuple[LayerStack, float]],
    values: dict[str, Any],
    wcs_view: list[dict[str, Any]],
) -> p3.Params:
    return p3.Params(
        stack=stack,
        drain_cells=grid.drain_cells(),
        zones=tuple(
            p3.Zone(z["name"], z["cells"], *zone_values[z["id"]]) for z in grid.zones(zone_values)
        ),
        wells=tuple(
            p3.WellGroup(
                g["name"], g["cells"], g["layer"], g["q_ref"], g["q_new"],
                g.get("radius", DEFAULT_RADIUS),
            )  # fmt: skip
            for g in grid.groups("wells")
        ),
        ghb=tuple(
            p3.GhbGroup(
                g["name"], g["cells"], g["layer"], g["head_ref"], g["head_new"], g["resistance"]
            )
            for g in grid.groups("ghb")
        ),
        chd=tuple(
            p3.ChdGroup(g["name"], g["cells"], g["layer"], g["head_ref"], g["head_new"])
            for g in grid.groups("chd")
        ),
        walls=tuple(
            p3.Wall(
                w["name"], w["faces"], w["layer"],
                None if w.get("impermeable", True) else float(w["resistance"]),
                bool(w.get("reference", False)),
            )  # fmt: skip
            for w in grid.walls()
            if w["faces"]
        ),
        watercourses=tuple(
            p3.Watercourse(
                cells=wc["cells"], lengths=wc["lengths"], bottom_offsets=wc.get("bottom_offsets"),
                **{f: wc[f] for f in ["name", "kind", *WC_FIELDS]},
            )
            for wc in wcs_view
        ),
        **values,
    )  # fmt: skip
