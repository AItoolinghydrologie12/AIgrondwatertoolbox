"""Model location in RD coordinates (optional): address search with the PDOK Locatieserver and
the centre of the model. The location is used for map data and the MODFLOW 6 export; the
calculation itself stays in local metres."""

from __future__ import annotations

from typing import Any

import streamlit as st
from toolbox_state import KEY, ss

from gwproducten.geo import pdok

DEFAULT_CENTRE = (155000.0, 463000.0)  # Amersfoort, origin of the RD system


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
        ss.setdefault(f"{KEY}_cx", DEFAULT_CENTRE[0])
        ss.setdefault(f"{KEY}_cy", DEFAULT_CENTRE[1])
        c1, c2 = st.columns(2)
        cx = c1.number_input(
            "RD x middelpunt model [m]", 0.0, 300000.0, step=10.0, format="%.0f", key=f"{KEY}_cx"
        )
        cy = c2.number_input(
            "RD y middelpunt model [m]", 300000.0, 625000.0, step=10.0, format="%.0f",
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
