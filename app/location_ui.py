"""Model location in RD coordinates (optional): address search with the PDOK Locatieserver, a
map to click the centre of the model on, and the centre in RD. The location is used for map
data and the MODFLOW 6 export; the calculation itself stays in local metres."""

from __future__ import annotations

import base64
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st
from grid_ui import to_cells
from regis_ui import regis_inputs
from toolbox_state import KEY, ss

from gwproducten.core.params import meta
from gwproducten.geo import ahn, pdok, rd
from gwproducten.products import peilopzet_grid as p3

DEFAULT_CENTRE = (155000.0, 463000.0)  # Amersfoort, origin of the RD system
RD_X, RD_Y = (0.0, 300000.0), (300000.0, 625000.0)  # range of the centre inputs [m]
LOCMAP_ZOOM = 14  # start zoom of the location map (about 2 km across)
FRAME_COLOR = "#d62828"


@st.cache_data(ttl=24 * 3600, max_entries=100, show_spinner="Zoeken bij PDOK…")
def _search(query: str) -> list[pdok.Place]:
    return pdok.search_places(query)


def located() -> bool:
    return bool(ss.get(f"{KEY}_geo", False))


def location_inputs(values: dict[str, Any]) -> None:
    """Toggle, address search and RD centre; fills ``origin_x``/``origin_y`` (lower-left
    corner, 0 without a location)."""
    with st.expander("Locatie (optioneel)", expanded=located()):
        on = st.toggle(
            "Model op een echte locatie (RD-coördinaten)", key=f"{KEY}_geo",
            help="Nodig voor kaartgegevens (luchtfoto, watergangen, maaiveld, ondergrond). "
            "De berekening zelf verandert niet; het MODFLOW 6-model krijgt de RD-hoek mee.",
        )  # fmt: skip
        if not on:
            values["origin_x"] = values["origin_y"] = 0.0
            st.caption("Zonder locatie teken je in lokale meters (linksonder = 0, 0).")
            return
        c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
        query = c1.text_input("Zoek adres, postcode of plaats", key=f"{KEY}_loc_q")
        if c2.button("Zoeken", key=f"{KEY}_loc_search", icon=":material/search:", width="stretch"):
            try:
                ss[f"{KEY}_loc_hits"] = _search(query)
            except pdok.ServiceError as err:
                st.warning(str(err))
            else:
                if not ss[f"{KEY}_loc_hits"]:
                    st.info("Niets gevonden; probeer een andere schrijfwijze of vul RD in.")
        hits: list[pdok.Place] = ss.get(f"{KEY}_loc_hits", [])
        if hits:
            c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
            pick = c1.selectbox(
                "Gevonden", range(len(hits)), key=f"{KEY}_loc_pick",
                format_func=lambda i: f"{hits[i].name} ({hits[i].kind})",
            )  # fmt: skip
            if c2.button("Model hier centreren", key=f"{KEY}_loc_use", width="stretch"):
                ss[f"{KEY}_cx"], ss[f"{KEY}_cy"] = round(hits[pick].x), round(hits[pick].y)
        clicked = ss.pop(f"{KEY}_loc_next", None)  # a click on the map in the previous run
        if clicked is not None:  # within the range of the inputs below
            ss[f"{KEY}_cx"] = min(max(clicked[0], RD_X[0]), RD_X[1])
            ss[f"{KEY}_cy"] = min(max(clicked[1], RD_Y[0]), RD_Y[1])
        ss.setdefault(f"{KEY}_cx", DEFAULT_CENTRE[0])
        ss.setdefault(f"{KEY}_cy", DEFAULT_CENTRE[1])
        c1, c2 = st.columns(2)
        cx = c1.number_input(
            "RD x middelpunt model [m]", *RD_X, step=10.0, format="%.0f", key=f"{KEY}_cx"
        )
        cy = c2.number_input(
            "RD y middelpunt model [m]", *RD_Y, step=10.0, format="%.0f",
            key=f"{KEY}_cy",
        )  # fmt: skip
        x0 = max(cx - values["size_x"] / 2.0, 0.0)
        y0 = max(cy - values["size_y"] / 2.0, 0.0)
        values["origin_x"], values["origin_y"] = x0, y0
        st.caption(
            f"Modelgebied RD: linksonder ({x0:.0f}; {y0:.0f}), rechtsboven "
            f"({x0 + values['size_x']:.0f}; {y0 + values['size_y']:.0f}) m. Adreszoeker: "
            "PDOK Locatieserver."
        )
        location_map(values, cx, cy)
        st.divider()
        ahn_inputs(values)
        st.divider()
        regis_inputs(values)


def location_map(values: dict[str, Any], cx: float, cy: float) -> None:
    """Tile map (PDOK grey, loaded by the browser) with the model area; a click moves the
    centre there. Panning and zooming happen in the browser without a rerun; only a click
    reruns the page. The map follows the centre, the model frame is updated in place."""
    import folium
    from streamlit_folium import st_folium

    x0, y0 = values["origin_x"], values["origin_y"]
    x1, y1 = x0 + values["size_x"], y0 + values["size_y"]
    centre = rd.rd_to_wgs84(cx, cy)
    base = folium.Map(
        location=rd.rd_to_wgs84(*DEFAULT_CENTRE), zoom_start=LOCMAP_ZOOM, tiles=None,
        control_scale=True, max_bounds=True, min_zoom=7,
    )  # fmt: skip
    folium.TileLayer(
        tiles=pdok.TILE_URL_WEB, attr=pdok.TILE_ATTRIBUTION, name="PDOK grijs",
        max_zoom=19, max_native_zoom=19,
    ).add_to(base)  # fmt: skip
    frame = folium.FeatureGroup(name="modelgebied")
    folium.Polygon(
        [rd.rd_to_wgs84(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))],
        color=FRAME_COLOR, weight=3, fill=True, fill_opacity=0.05, tooltip="modelgebied",
    ).add_to(frame)  # fmt: skip
    folium.CircleMarker(
        centre, radius=5, color=FRAME_COLOR, fill=True, fill_opacity=1.0,
        tooltip=f"middelpunt RD {cx:.0f}; {cy:.0f}",
    ).add_to(frame)  # fmt: skip
    event = st_folium(
        base, center=centre, feature_group_to_add=frame, key=f"{KEY}_locmap",
        returned_objects=["last_clicked"], height=420, use_container_width=True,
    )  # fmt: skip
    clicked = (event or {}).get("last_clicked")
    if clicked:
        x, y = rd.wgs84_to_rd(float(clicked["lat"]), float(clicked["lng"]))
        point = (round(x), round(y))
        if point != ss.get(f"{KEY}_locmap_used"):
            ss[f"{KEY}_locmap_used"] = point
            if point != (round(cx), round(cy)):
                ss[f"{KEY}_loc_next"] = point
                st.rerun()
    st.caption(
        "Klik op de kaart om het middelpunt van het model daar te leggen. Slepen verschuift de "
        "kaart, scrollen zoomt. Achtergrond: " + pdok.BACKGROUND_SOURCE["grijs"] + "."
    )


@st.cache_data(ttl=7 * 24 * 3600, max_entries=10, show_spinner="Maaiveld ophalen bij PDOK (AHN)…")
def _ahn(bbox: pdok.Bbox, nrow: int, ncol: int) -> np.ndarray:
    return ahn.surface_per_cell(bbox, nrow, ncol)


def _clip_surface(value: float) -> float:
    m = meta(p3.Params, "surface")
    return round(min(max(value, m["min"]), m["max"]), 2)


def ahn_inputs(values: dict[str, Any]) -> None:
    """Surface level from AHN: median per cell, then the median of the model and of every
    zone; 'Overnemen' fills the surface inputs of step 1."""
    cell = float(values["cell"])
    nrow, ncol = round(values["size_y"] / cell), round(values["size_x"] / cell)
    bbox = model_bbox(values["origin_x"], values["origin_y"], ncol * cell, nrow * cell)
    st.markdown("**Maaiveld uit AHN**")
    if st.button("Maaiveld ophalen", key=f"{KEY}_ahn_get", icon=":material/landscape:"):
        try:
            ss[f"{KEY}_ahn"] = (bbox, nrow, ncol, _ahn(bbox, nrow, ncol))
        except pdok.ServiceError as err:
            st.warning(str(err))
    fetched = ss.get(f"{KEY}_ahn")
    if not fetched or fetched[:3] != (bbox, nrow, ncol):
        st.caption(
            "Haalt het maaiveld (AHN, 0,5 m) op als mediaan per cel en vult de mediaan in "
            "voor het model en voor elke zone. Sloten en losse gebouwen tellen zo nauwelijks mee."
        )
        return
    surface = fetched[3]
    rows, targets = [], []
    whole = ahn.stats(surface)
    if whole is None:
        st.info("Geen AHN-gegevens in dit gebied (bijvoorbeeld open water).")
        return
    rows.append(("Model (basis)", whole))
    targets.append((f"{KEY}_mv", whole.median))
    for z in ss[f"{KEY}_zones"]:
        cells = to_cells(z["points"], cell, nrow, ncol)
        if not cells:
            continue
        zs = ahn.stats(np.array([surface[r, c] for r, c in cells]))
        if zs is not None:
            rows.append((f"Zone {z['name']}", zs))
            targets.append((f"{KEY}_z{z['id']}_mv", zs.median))
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Gebied": name, "Mediaan [m NAP]": round(s.median, 2),
                    "P10 [m NAP]": round(s.p10, 2), "P90 [m NAP]": round(s.p90, 2),
                    "Cellen met AHN [%]": round(100 * s.coverage),
                }
                for name, s in rows
            ]
        ),
        hide_index=True,
    )  # fmt: skip
    if whole.p90 - whole.p10 > 1.0:
        st.info(
            "Het maaiveld varieert meer dan 1 m in het model. Overweeg zones met een eigen "
            "maaiveld; de app rekent met één maaiveld per zone."
        )
    if st.button("Overnemen als maaiveld", key=f"{KEY}_ahn_use", type="primary"):
        for key, value in targets:
            ss[key] = _clip_surface(value)
        st.toast("Maaiveld overgenomen uit AHN.")
    st.caption(f"Bron: {ahn.SOURCE}.")


BACKGROUNDS = {"grijs": "PDOK grijs", "geen": "geen"}


@st.cache_data(ttl=7 * 24 * 3600, max_entries=20, show_spinner="Kaart ophalen bij PDOK…")
def _background_uri(kind: str, bbox: pdok.Bbox) -> str:
    data = pdok.background_image(kind, bbox)
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def model_bbox(origin_x: float, origin_y: float, width: float, height: float) -> pdok.Bbox:
    return (origin_x, origin_y, origin_x + width, origin_y + height)


def background(bbox: pdok.Bbox) -> tuple[str, float] | None:
    """The chosen background map for ``bbox`` as (data URI, opacity), or None (no location,
    'geen', or the service failed; a warning is shown then)."""
    kind = ss.get(f"{KEY}_bg", "grijs")
    if not located() or kind not in pdok.BACKGROUND_SOURCE:
        return None
    try:
        uri = _background_uri(kind, bbox)
    except pdok.ServiceError as err:
        st.warning(str(err))
        return None
    return uri, float(ss.get(f"{KEY}_bg_op", 0.6))


def background_inputs() -> None:
    """Choice of background map and its opacity (only with a location)."""
    if not located():
        st.caption("Achtergrondkaart: zet bij stap 1 een locatie aan.")
        return
    if ss.get(f"{KEY}_bg") not in BACKGROUNDS:
        ss[f"{KEY}_bg"] = "grijs"  # also a project saved with a former map (topografie, ...)
    c1, c2 = st.columns([3, 2])
    c1.segmented_control("Achtergrondkaart", list(BACKGROUNDS), format_func=BACKGROUNDS.get,
                         required=True, key=f"{KEY}_bg")  # fmt: skip
    c2.slider("Zichtbaarheid", 0.1, 1.0, 0.6, 0.1, key=f"{KEY}_bg_op")


def background_caption() -> None:
    kind = ss.get(f"{KEY}_bg", "grijs")
    if located() and kind in pdok.BACKGROUND_SOURCE:
        st.caption(f"Achtergrond: {pdok.BACKGROUND_SOURCE[kind]}.")
