"""Subsoil proposal from REGIS II at the model centre (step 1, under 'Locatie'): fetch, show
the REGIS units next to the proposed layer stack, and only on 'Overnemen' fill the layer
inputs of step 1."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
import streamlit as st
from toolbox_state import KEY, ss

from gwproducten.core.layers import Layer
from gwproducten.core.params import meta
from gwproducten.geo import regis
from gwproducten.geo.pdok import ServiceError
from gwproducten.products import peilopzet_grid as p3

REGIS_DEFAULT_AQUIFERS = 4  # default of the proposal; up to MAX_AQUIFERS on request


@st.cache_data(
    ttl=7 * 24 * 3600, max_entries=50, show_spinner="Ondergrond ophalen bij TNO (REGIS)…"
)
def _fetch(x: float, y: float) -> tuple[float, list[regis.Unit]]:
    return regis.fetch_point(x, y)


def _clip(name: str, value: float) -> float:
    m = meta(Layer, name)
    return float(min(max(value, m["min"]), m["max"]))


def layer_name(i: int, group: regis.Group, top_mode: str) -> str:
    n_aq = i // 2 + 1 if top_mode == "deklaag" else (i + 1) // 2
    if group.aquifer:
        return "Freatisch pakket" if top_mode == "freatisch_zand" and i == 0 else f"Pakket {n_aq}"
    return "Deklaag" if top_mode == "deklaag" and i == 0 else "Scheidende laag"


def proposal_table(p: regis.Proposal) -> pd.DataFrame:
    rows = []
    for i, g in enumerate(p.layers):
        rows.append(
            {
                "Laag": layer_name(i, g, p.top_mode),
                "Dikte [m]": round(g.thickness, 1),
                "k [m/d]": round(g.kh, 2) if g.aquifer else None,
                "kD [m²/d]": round(g.kh * g.thickness) if g.aquifer else None,
                "kv/kh [-]": round(g.anisotropy, 3) if g.aquifer else None,
                "c [d]": None if g.aquifer or not math.isfinite(g.c) else round(g.c),
                "REGIS-eenheden": g.codes,
            }
        )
    return pd.DataFrame(rows).astype(object).fillna("")  # empty instead of "None"


def units_table(units: list[regis.Unit]) -> pd.DataFrame:
    def r(v: float, n: int) -> float | None:
        return round(v, n) if math.isfinite(v) else None

    return pd.DataFrame(
        [
            {
                "Eenheid": u.code, "Type": "zand" if u.is_aquifer else "klei/complex",
                "Boven [m NAP]": r(u.top, 2), "Onder [m NAP]": r(u.bottom, 2),
                "kh [m/d]": r(u.kh, 2), "kv [m/d]": r(u.kv, 4), "c [d]": r(u.c, 0),
            }
            for u in units
        ]
    ).astype(object).fillna("")  # fmt: skip


def apply_proposal(state: Any, p: regis.Proposal) -> None:
    """Write the proposal into the layer inputs of step 1 (widget keys of stack_inputs).
    Aquitards without a REGIS value keep the current resistance."""
    mode = p.top_mode
    state[f"{KEY}_mode_top"] = mode
    state[f"{KEY}_naq_{mode}"] = p.n_aquifers
    for i, g in enumerate(p.layers):
        k = f"{KEY}_{mode}_{i}"
        state[f"{k}_d"] = round(_clip("thickness", g.thickness), 2)
        if g.aquifer:
            state[f"{k}_k"] = round(_clip("k_h", g.kh), 3)
            state[f"{k}_a"] = round(_clip("anisotropy", g.anisotropy), 3)
        elif math.isfinite(g.c):
            state[f"{k}_c"] = round(_clip("c", g.c))


def regis_inputs(values: dict[str, Any]) -> None:
    st.markdown("**Ondergrond uit REGIS II**")
    x = values["origin_x"] + values["size_x"] / 2.0
    y = values["origin_y"] + values["size_y"] / 2.0
    c1, c2, c3 = st.columns([2, 2, 2], vertical_alignment="bottom")
    c_min = c1.number_input(
        "Kleilaag telt mee vanaf c [d]", 0.0, 1000.0, 5.0, 1.0, format="%g", key=f"{KEY}_rg_cmin",
        help="Een kleilaag met een kleinere weerstand telt mee in het pakket eromheen.",
    )  # fmt: skip
    n_max = c2.number_input(
        "Maximaal aantal pakketten", 1, p3.MAX_AQUIFERS, REGIS_DEFAULT_AQUIFERS,
        key=f"{KEY}_rg_nmax",
        help="Daaronder rekent de app met een dichte basis. Meer dan 5 rekenlagen maakt het "
        "grootste rooster kleiner (zie de zijbalk).",
    )  # fmt: skip
    if c3.button("Ondergrond ophalen", key=f"{KEY}_rg_get", icon=":material/layers:"):
        try:
            ss[f"{KEY}_regis"] = (x, y, _fetch(x, y))
        except ServiceError as err:
            st.warning(str(err))
    fetched = ss.get(f"{KEY}_regis")
    if not fetched or fetched[:2] != (x, y):
        st.caption(
            "Haalt de hydrogeologische eenheden van REGIS II op in het midden van het model "
            "(rooster van 100 m) en stelt een lagenopbouw voor. Je ziet eerst het voorstel."
        )
        return
    surface, units = fetched[2]
    p = regis.propose(surface, units, c_min=c_min, max_aquifers=int(n_max))
    if not p.layers:
        st.info("Geen bruikbare lagen gevonden op deze plek.")
        return
    st.caption(
        f"Voorstel op RD ({x:.0f}; {y:.0f}); maaiveld volgens REGIS {surface:.2f} m NAP. De "
        "diktes tellen in de app vanaf het maaiveld van stap 1."
    )
    st.dataframe(proposal_table(p), hide_index=True)
    for note in p.notes:
        st.info(note)
    with st.expander(f"REGIS-eenheden op deze plek ({len(units)})", expanded=False):
        st.dataframe(units_table(units), hide_index=True)
    if st.button("Overnemen in lagenopbouw", type="primary", key=f"{KEY}_rg_use"):
        apply_proposal(ss, p)
        st.toast("Lagenopbouw overgenomen uit REGIS. Controleer de waarden.")
    st.caption(f"Bron: {regis.SOURCE}. Een regionaal model: controleer met boringen ter plekke.")
