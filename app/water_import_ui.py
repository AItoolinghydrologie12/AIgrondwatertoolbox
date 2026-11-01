"""Import watercourses (step 2): TOP10NL water plus, within WDODelta, the level areas
(peilgebieden) and the legger. Choose which kinds, fetch, preview per watercourse, then add.
Levels come from the chosen level of each area; bottoms from the legger (mean of upstream
and downstream, kept per line) or level - water depth. Connected pieces form one
watercourse."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st
from grid_ui import centre_key
from location_ui import located, model_bbox
from toolbox_state import KEY, Grid, bump, ss

from gwproducten.geo import import_water, water, waterschap
from gwproducten.geo.pdok import Bbox, ServiceError

TODO = " (peil invullen)"
BOARD = waterschap.WDODELTA
DEPTH_INPUTS = {  # import_water.DEPTHS key -> (widget key suffix, label)
    "legger 1": ("l1", "Legger primair"),
    "legger 2": ("l2", "Legger secundair"),
    "0,5 - 3 meter": ("t1", "TOP10NL 0,5–3 m"),
    "3 - 6 meter": ("t2", "TOP10NL 3–6 m"),
    "breed": ("t3", "Breder / onbekend"),
}
DEFAULT_KINDS = ["legger 1"]  # Job: only primary by default, keeps the table readable


@st.cache_data(ttl=24 * 3600, max_entries=20, show_spinner="Watergangen ophalen bij PDOK…")
def _fetch_top10(bbox: Bbox) -> list[water.Water]:
    return water.fetch_waterdelen(bbox)


@st.cache_data(ttl=24 * 3600, max_entries=20, show_spinner="Peilen en legger ophalen…")
def _fetch_board(bbox: Bbox) -> tuple[list[waterschap.LevelArea], list[waterschap.Reach]]:
    return waterschap.level_areas(bbox, BOARD), waterschap.reaches(bbox, BOARD)


@st.cache_data(max_entries=20, show_spinner=False)
def _combine(
    top10: list[water.Water],
    reaches: list[waterschap.Reach],
    areas: list[waterschap.LevelArea],
    origin: tuple[float, float],
    grid: tuple[float, int, int],
    choice: str,
    depths: dict[str, float],
    default_stage: float,
    include: tuple[str, ...],
) -> import_water.Result:
    return import_water.combine(
        top10, reaches, areas, origin, grid, choice=choice, depths=depths,
        default_stage=default_stage, include=include,
    )  # fmt: skip


def group_name(g: import_water.Group, taken: set[str]) -> str:
    """Unique name: source and level area; with the width when that is not unique, then a
    number. '(peil invullen)' stays at the end for watercourses without an area."""
    base = g.source.replace(" meter", " m")
    area = f" · gebied {g.area} ({g.stage:.2f})" if g.area is not None else ""
    end = "" if g.area is not None else TODO
    for name in (
        f"{base}{area}{end}",
        f"{base}{area} · b {g.width:g} m{end}",
        *(f"{base}{area} · b {g.width:g} m #{k}{end}" for k in range(2, 999)),
    ):
        if name not in taken:
            taken.add(name)
            return name
    raise ValueError("geen unieke naam")


def watercourse(g: import_water.Group, cell: float, taken: set[str]) -> dict[str, Any]:
    """Watercourse dict of the drawing state: lines as drawn lines (length per cell follows
    the line) with their bottom offset, area water as cells with their length."""
    wc: dict[str, Any] = {
        "name": group_name(g, taken), "kind": "riv", "stage_ref": round(g.stage, 3),
        "stage_new": round(g.stage, 3), "bottom": round(g.bottom, 3), "width": g.width,
        "length": cell, "entry_resistance": 1.0, "conductance": 0.0,
        "lines": [list(ln) for ln in g.lines], "points": set(), "manual": {},
        "manual_cell": cell,
    }  # fmt: skip
    if any(g.line_offsets):
        wc["line_offsets"] = g.line_offsets
    for (r, c), length in g.cells.items():
        key = centre_key(r, c, cell)
        wc["points"].add(key)
        wc["manual"][key] = length
    return wc


def _preview(groups: list[import_water.Group]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Watergang": g.source, "Peilgebied": g.area or "–",
                "Peil [m NAP]": round(g.stage, 2), "Bodem [m NAP]": round(g.bottom, 2),
                "Bodem uit": (
                    "legger, aangepast" if g.bottom_adjusted
                    else "legger" if g.bottom_from_legger
                    else f"deels legger ({g.legger_share:.0%})" if g.legger_share > 0.0
                    else "aanname"
                ),
                "Breedte [m]": g.width, "Lengte [m]": round(g.length),
            }
            for g in groups
        ]
    )  # fmt: skip


def _options() -> tuple[bool, str, dict[str, float], tuple[str, ...]]:
    use_board = st.toggle(
        f"Peilen en legger van het waterschap ({BOARD.name})", value=True, key=f"{KEY}_ws_on",
        help="Peil uit de peilgebiedenkaart; primaire en secundaire watergangen met breedte en "
        "bodem uit de legger. Alleen binnen WDODelta; daarbuiten vul je het peil zelf in.",
    )  # fmt: skip
    options = [k for k in import_water.KINDS if use_board or not k.startswith("legger")]
    kinds = st.multiselect(
        "Welke watergangen", options,
        default=DEFAULT_KINDS if use_board else options, format_func=import_water.KINDS.get,
        key=f"{KEY}_kinds_{int(use_board)}",
        help="Minder soorten = een overzichtelijker tabel en kaart. TOP10NL is alles wat niet "
        "in de legger staat; ook watergangen uit de legger die je niet kiest, halen TOP10NL "
        "langs dezelfde lijn weg.",
    )  # fmt: skip
    c1, c2 = st.columns([1, 2])
    choice = c1.selectbox(
        "Peil", waterschap.LEVELS, index=1, format_func=waterschap.LEVEL_LABEL.get,
        key=f"{KEY}_ws_level", disabled=not use_board,
    )  # fmt: skip
    c2.caption(
        "Peil na opzet = dit peil; een opzet stel je daarna in de tabel in. Ontbreekt het "
        "gekozen peil, dan neemt de app winter- (bij min) of zomerpeil (bij max)."
    )
    depths = {}
    with st.expander("Waterdiepte waar de legger geen bodem geeft (aanname)", expanded=False):
        cols = st.columns(len(DEPTH_INPUTS))
        for col, (name, (suffix, label)) in zip(cols, DEPTH_INPUTS.items(), strict=True):
            depths[name] = col.number_input(
                f"{label} [m]", 0.05, 10.0, import_water.DEPTHS[name], 0.05, format="%g",
                key=f"{KEY}_wd_{suffix}",
            )  # fmt: skip
        st.caption("Bodem = peil − waterdiepte.")
    return use_board, choice, depths, tuple(kinds)


def import_inputs(grid: Grid, values: dict[str, Any]) -> None:
    with st.expander("Watergangen ophalen (TOP10NL, peilen en legger)", expanded=False):
        if not located():
            st.caption("Zet bij stap 1 een locatie aan om watergangen op te halen.")
            return
        origin = (values["origin_x"], values["origin_y"])
        bbox = model_bbox(*origin, grid.ncol * grid.cell, grid.nrow * grid.cell)
        st.caption(
            "Haalt de watergangen in het modelgebied op: primaire en secundaire uit de legger "
            "van het waterschap, de overige uit TOP10NL (hartlijnen 0,5–3 en 3–6 m, vlakken "
            "van bredere wateren; duikers vallen weg). Elk stuk krijgt het peil van zijn "
            "peilgebied; stukken die aan elkaar vastzitten worden één watergang, met de bodem "
            "uit de legger per stuk. Kleine greppels ontbreken in TOP10NL."
        )
        use_board, choice, depths, kinds = _options()
        if st.button("Ophalen", key=f"{KEY}_top10_get", icon=":material/water:"):
            fetched: dict[str, Any] = {"bbox": bbox, "top10": [], "areas": [], "reaches": []}
            try:
                fetched["top10"] = _fetch_top10(bbox)
            except ServiceError as err:
                st.warning(str(err))
            if use_board:
                try:
                    fetched["areas"], fetched["reaches"] = _fetch_board(bbox)
                except ServiceError as err:
                    st.warning(f"{err} Alleen TOP10NL; peil zelf invullen.")
                if not fetched["areas"]:
                    st.info(
                        f"Geen peilgebieden van {BOARD.name} in dit gebied: het peil vul je "
                        "zelf in (standaard maaiveld − 1,2 m)."
                    )
            ss[f"{KEY}_top10"] = fetched
        fetched = ss.get(f"{KEY}_top10")
        if not fetched or fetched["bbox"] != bbox:
            return
        areas = fetched["areas"] if use_board else []
        reaches = fetched["reaches"] if use_board else []
        if not fetched["top10"] and not reaches:
            st.info("Geen watergangen gevonden in dit gebied.")
            return
        result = _combine(
            fetched["top10"], reaches, areas, origin, (grid.cell, grid.nrow, grid.ncol),
            choice, depths, values["surface"] - 1.2, kinds,
        )  # fmt: skip
        if not result.groups:
            st.info(
                "Geen watergangen van de gekozen soort in dit gebied"
                + ("" if reaches else " (geen legger hier: kies TOP10NL)")
                + "."
            )
            return
        legger = sum(g.bottom_from_legger for g in result.groups)
        st.dataframe(_preview(result.groups), hide_index=True)
        st.caption(
            f"{len(result.groups)} watergangen; bodem geheel uit de legger bij {legger}, "
            "aangenomen (peil − waterdiepte) waar de legger geen bodem geeft. Een watergang "
            "met bodems uit de legger houdt die per stuk; in de tabel staat het gemiddelde, en "
            "pas je dat aan, dan schuift de hele watergang mee."
        )
        for note in result.notes:
            st.info(note)
        if st.button("Toevoegen als watergangen", type="primary", key=f"{KEY}_top10_add"):
            taken = {wc["name"] for wc in ss[f"{KEY}_wcs"]}
            ss[f"{KEY}_wcs"].extend(watercourse(g, grid.cell, taken) for g in result.groups)
            ss[f"{KEY}_areas"] = (bbox, areas)
            ss.pop(f"{KEY}_top10")
            bump()
            st.rerun()
        sources = [water.SOURCE] + ([BOARD.source] if use_board else [])
        st.caption("Bron: " + "; ".join(sources) + ".")


def area_outlines(grid: Grid, values: dict[str, Any]) -> list[list[tuple[float, float]]]:
    """Borders of the fetched level areas in model metres (for the drawing map)."""
    origin = (values.get("origin_x", 0.0), values.get("origin_y", 0.0))
    bbox = model_bbox(*origin, grid.ncol * grid.cell, grid.nrow * grid.cell)
    stored = ss.get(f"{KEY}_areas")
    fetched = ss.get(f"{KEY}_top10")
    if fetched and fetched["bbox"] == bbox:
        areas = fetched["areas"]
    elif stored and stored[0] == bbox:
        areas = stored[1]
    else:
        return []
    return [water.to_local(ring, *origin) for a in areas for ring in a.rings]


def import_notice() -> None:
    """Reminder after an import: levels outside level areas are defaults."""
    names = [wc["name"] for wc in ss[f"{KEY}_wcs"] if wc["name"].endswith(TODO)]
    if names:
        st.warning(
            "Het peil van deze geïmporteerde watergangen is een standaardwaarde (maaiveld − 1,2 "
            "m; bodem = peil − waterdiepte). Vul peil en bodem in de tabel hieronder in en haal "
            f"'(peil invullen)' uit de naam als ze kloppen: {', '.join(names)}.",
            icon=":material/edit:",
        )
