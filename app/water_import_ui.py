"""Import watercourses from TOP10NL (step 2): fetch, preview per width class, add as one
watercourse per class. Levels and bottoms are not in TOP10NL; they get the defaults of a
new watercourse and must be filled in."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st
from grid_ui import centre_key
from location_ui import located, model_bbox
from toolbox_state import KEY, Grid, bump, ss

from gwproducten.core.grids import polyline_cell_lengths
from gwproducten.geo import water
from gwproducten.geo.pdok import ServiceError

TODO = " (peil invullen)"


@st.cache_data(ttl=24 * 3600, max_entries=20, show_spinner="Watergangen ophalen bij PDOK…")
def _fetch(bbox: tuple[float, float, float, float]) -> list[water.Water]:
    return water.fetch_waterdelen(bbox)


def watercourses_from_top10nl(
    items: list[water.Water], grid: Grid, origin: tuple[float, float], surface: float
) -> list[dict[str, Any]]:
    """One watercourse dict per width class: lines as drawn lines (length per cell follows
    the line), areas as cells with length = area in the cell / width."""
    out = []
    for label in sorted({w.width_class for w in items}, key=water.class_width):
        group = [w for w in items if w.width_class == label]
        width = water.class_width(label)
        wc: dict[str, Any] = {
            "name": f"TOP10NL {label.replace(' meter', ' m')}{TODO}", "kind": "riv",
            "stage_ref": surface - 1.2, "stage_new": surface - 1.2, "bottom": surface - 1.6,
            "width": width, "length": grid.cell, "entry_resistance": 1.0, "conductance": 0.0,
            "lines": [], "points": set(), "manual": {}, "manual_cell": grid.cell,
        }  # fmt: skip
        for w in group:
            if not w.area:
                wc["lines"].append(water.to_local(w.parts[0], *origin))
                continue
            rings = [water.to_local(r, *origin) for r in w.parts]
            for (r, c), length in water.polygon_cell_lengths(
                rings, width, grid.cell, grid.nrow, grid.ncol
            ).items():
                key = centre_key(r, c, grid.cell)
                wc["points"].add(key)
                wc["manual"][key] = wc["manual"].get(key, 0.0) + length
        out.append(wc)
    return out


def _preview(items: list[water.Water], grid: Grid, origin: tuple[float, float]) -> pd.DataFrame:
    rows = []
    for label in sorted({w.width_class for w in items}, key=water.class_width):
        group = [w for w in items if w.width_class == label]
        length = 0.0
        for w in group:
            if w.area:
                rings = [water.to_local(r, *origin) for r in w.parts]
                cells = water.polygon_cell_lengths(
                    rings, water.class_width(label), grid.cell, grid.nrow, grid.ncol
                )
            else:
                local = water.to_local(w.parts[0], *origin)
                cells = polyline_cell_lengths(local, grid.cell, grid.nrow, grid.ncol)
            length += sum(cells.values())
        rows.append(
            {
                "Breedteklasse": label,
                "Soort": "vlak" if group[0].area else "lijn",
                "Aantal": len(group),
                "Lengte in model [m]": round(length),
                "Breedte [m]": water.class_width(label),
            }
        )
    return pd.DataFrame(rows)


def import_inputs(grid: Grid, values: dict[str, Any]) -> None:
    with st.expander("Watergangen ophalen uit TOP10NL (Kadaster)", expanded=False):
        if not located():
            st.caption("Zet bij stap 1 een locatie aan om watergangen op te halen.")
            return
        origin = (values["origin_x"], values["origin_y"])
        bbox = model_bbox(*origin, grid.ncol * grid.cell, grid.nrow * grid.cell)
        st.caption(
            "Haalt de watergangen in het modelgebied op: hartlijnen van sloten (0,5–3 m en "
            "3–6 m) en vlakken van bredere wateren; duikers vallen weg. Peil en bodem staan "
            "niet in TOP10NL: die vul je daarna zelf in. Kleine greppels ontbreken."
        )
        if st.button("Ophalen", key=f"{KEY}_top10_get", icon=":material/water:"):
            try:
                ss[f"{KEY}_top10"] = (bbox, _fetch(bbox))
            except ServiceError as err:
                st.warning(str(err))
        fetched = ss.get(f"{KEY}_top10")
        if not fetched or fetched[0] != bbox:
            return
        items = fetched[1]
        if not items:
            st.info("Geen watergangen gevonden in dit gebied.")
            return
        st.dataframe(_preview(items, grid, origin), hide_index=True)
        if st.button("Toevoegen als watergangen", type="primary", key=f"{KEY}_top10_add"):
            new = watercourses_from_top10nl(items, grid, origin, values["surface"])
            ss[f"{KEY}_wcs"].extend(new)
            ss.pop(f"{KEY}_top10")
            bump()
            st.rerun()
        st.caption(f"Bron: {water.SOURCE}.")


def import_notice() -> None:
    """Reminder after an import: levels and bottoms are defaults."""
    names = [wc["name"] for wc in ss[f"{KEY}_wcs"] if wc["name"].endswith(TODO)]
    if names:
        st.warning(
            "Peil en bodem van de geïmporteerde watergangen zijn standaardwaarden (maaiveld "
            "− 1,2 m en − 1,6 m). Vul ze in de tabel hieronder in en haal '(peil invullen)' "
            f"uit de naam als ze kloppen: {', '.join(names)}.",
            icon=":material/edit:",
        )
