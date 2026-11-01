"""Drawing actions of the plan-view grid page, on a plain state mapping (testable without
Streamlit). Objects keep their cells as cell centres in metres, so they survive a change
of the cell size."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from grid_ui import centre_key, outline_lines, to_cells, wc_geometry

NEW = "__nieuw__"


def new_zone(state: MutableMapping[str, Any], key: str) -> int:
    """Add an empty zone (base values until changed in step 1); returns its id."""
    zones = state[f"{key}_zones"]
    new_id = max((z["id"] for z in zones), default=0) + 1
    taken = {z["name"] for z in zones}
    name = next(f"Zone {n}" for n in range(1, 99) if f"Zone {n}" not in taken)
    zones.append({"id": new_id, "name": name, "points": set()})
    return new_id


def apply_selection(
    state: MutableMapping[str, Any],
    key: str,
    grid: tuple[float, int, int],
    obj: str,
    how: str,
    target: Any,
    props: dict[str, Any],
    selected: set[tuple[float, float]],
) -> None:
    """Apply a box/lasso selection to the chosen object.

    ``obj``: watergang, drain, put, ghb, damwand or zone; ``how``: cellen/wissen
    (watergang), rondom (damwand around the selection) or toevoegen/verwijderen; ``target``:
    watercourse or wall name, or zone id, or :data:`NEW`;
    ``grid``: (cell, nrow, ncol)."""
    cell, nrow, ncol = grid
    wcs = state[f"{key}_wcs"]
    if obj == "watergang" and how == "cellen":
        if target == NEW:
            wcs.append({**props, "lines": [], "points": set(selected), "manual": {},
                        "manual_cell": cell})  # fmt: skip
        else:
            next(wc for wc in wcs if wc["name"] == target)["points"] |= selected
    elif obj == "watergang":  # remove cells from all watercourses (also drawn lines)
        keys = {centre_key(r, c, cell) for r, c in to_cells(selected, cell, nrow, ncol)}
        for wc in wcs:
            wc["points"] -= selected
            if wc.get("manual_cell") != cell:
                wc["manual"], wc["manual_cell"] = {}, cell
            wc["manual"].update(dict.fromkeys(keys, 0.0))
        state[f"{key}_wcs"] = [w for w in wcs if wc_geometry(w, cell, nrow, ncol)[0]]
    elif obj == "drain":
        if how == "toevoegen":
            state[f"{key}_drain"] |= selected
        else:
            state[f"{key}_drain"] -= selected
    elif obj in ("put", "ghb"):
        name = "wells" if obj == "put" else "ghb"
        if how == "toevoegen":
            state[f"{key}_{name}"].append({**props, "points": set(selected)})
        else:
            for g in state[f"{key}_{name}"]:
                g["points"] -= selected
            state[f"{key}_{name}"] = [g for g in state[f"{key}_{name}"] if g["points"]]
    elif obj == "damwand":
        lines = outline_lines(selected, cell, nrow, ncol)
        if target == NEW:
            state[f"{key}_walls"].append({**props, "lines": lines})
        else:
            next(w for w in state[f"{key}_walls"] if w["name"] == target)["lines"] += lines
    elif obj == "zone":
        if how == "toevoegen" and target == NEW:
            target = new_zone(state, key)
        for z in state[f"{key}_zones"]:  # a cell belongs to one zone at most
            z["points"] = z["points"] - selected
            if how == "toevoegen" and z["id"] == target:
                z["points"] = z["points"] | selected
