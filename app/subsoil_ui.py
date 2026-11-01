"""Step 1 of the Grondwatertoolbox page: layers, zones, surface, drainage, boundary and
seepage."""

from __future__ import annotations

from typing import Any

import streamlit as st
from draw_actions import new_zone
from toolbox_state import DEFAULTS, KEY, bump, ss
from ui_common import number, stack_inputs, zone_stack_inputs

from gwproducten.core.layers import LayerStack
from gwproducten.products import peilopzet_grid as p3

BOUNDARY_LABEL = {
    "vast": "vaste stijghoogte",
    "doorlopend": "doorlopend pakket",
    "gesloten": "gesloten",
    "per_laag": "per laag",
}
BOUNDARY_HELP = (
    "Vaste stijghoogte: de buitenste ring houdt het randpeil (CHD). Doorlopend pakket: het "
    "pakket loopt buiten het model door; de rand watert af naar het randpeil met de weerstand "
    "van een half oneindig lekkend pakket (spreidingslengte λ). Gesloten: geen stroming."
)


def zone_inputs(base: LayerStack) -> dict[int, tuple[LayerStack, float]]:
    """Zones in step 1: name, surface and layer values per zone (structure of the base).
    Cells are assigned in step 2. Returns (stack, surface) per zone id."""
    st.subheader("Zones")
    st.caption(
        "Een zone heeft dezelfde opbouw als de basis, maar eigen diktes, k, c en maaiveld. De "
        "diktes tellen vanaf het maaiveld van de zone. Cellen wijs je toe in stap 2."
    )
    zones = ss[f"{KEY}_zones"]
    out: dict[int, tuple[LayerStack, float]] = {}
    for z in list(zones):
        k = f"{KEY}_z{z['id']}"
        with st.expander(f"Zone {z['name']}", expanded=False):
            c1, c2 = st.columns(2)
            z["name"] = c1.text_input("Naam", z["name"], key=f"{k}_name").strip() or z["name"]
            with c2:
                surface = number(p3.Zone, "surface", DEFAULTS.surface, f"{k}_mv", 0.1)
            n_cells = len(z["points"])
            st.caption(f"{n_cells} cellen toegewezen" if n_cells else "Nog geen cellen.")
            zstack = zone_stack_inputs(base, k) if not base.errors() else base
            out[z["id"]] = (zstack, surface)
            if st.button("Zone verwijderen", key=f"{k}_del", icon=":material/delete:"):
                ss[f"{KEY}_zones"] = [w for w in zones if w["id"] != z["id"]]
                bump()
                st.rerun()
    if st.button(
        "Zone toevoegen", key=f"{KEY}_zone_add", icon=":material/add:",
        disabled=len(zones) >= p3.MAX_ZONES,
        help=f"Maximaal {p3.MAX_ZONES} zones.",
    ):  # fmt: skip
        new_zone(ss, KEY)
        st.rerun()
    return out


def _surface_inputs(values: dict[str, Any]) -> None:
    st.subheader("Maaiveld, neerslag en drainage")
    with st.container(border=True):
        c1, c2 = st.columns(2)
        with c1:
            values["surface"] = number(p3.Params, "surface", DEFAULTS.surface, f"{KEY}_mv", 0.1)
        values["recharge"] = (
            c2.number_input(
                "Neerslagoverschot [mm/d]", -10.0, 20.0, DEFAULTS.recharge * 1000, 0.1,
                format="%g", key=f"{KEY}_rch",
                help="Jaargemiddeld Nederland ~0,8 mm/d (≈ 290 mm/jaar); natte winter ~2 mm/d.",
            )
            / 1000.0
        )  # fmt: skip
        c1, c2, c3 = st.columns(3)
        with c1:
            values["mv_resistance"] = number(
                p3.Params, "mv_resistance", DEFAULTS.mv_resistance, f"{KEY}_wmv", 1.0
            )
        with c2:
            values["drain_depth"] = number(
                p3.Params, "drain_depth", DEFAULTS.drain_depth, f"{KEY}_dd", 0.1
            )
        with c3:
            values["drain_resistance"] = number(
                p3.Params, "drain_resistance", DEFAULTS.drain_resistance, f"{KEY}_dw", 10.0
            )
        st.caption(
            "Maaivelddrainage werkt overal zodra het grondwater boven maaiveld komt. "
            "Buisdrainage teken je per cel in stap 2; diepte en weerstand gelden voor alle "
            "gedraineerde cellen."
        )


def _boundary_inputs(values: dict[str, Any], stack: LayerStack) -> None:
    st.subheader("Modelrand en kwel")
    with st.container(border=True):
        nodes = stack.nodes if not stack.errors() else []
        values["boundary"] = st.radio(
            "Modelrand (buitenste ring cellen)", p3.BOUNDARIES, horizontal=True,
            key=f"{KEY}_bnd", format_func=BOUNDARY_LABEL.get, help=BOUNDARY_HELP,
        )  # fmt: skip
        if values["boundary"] in ("vast", "doorlopend"):
            values["boundary_head"] = number(
                p3.Params, "boundary_head", DEFAULTS.boundary_head, f"{KEY}_bh", 0.1
            )
        elif values["boundary"] == "per_laag":
            per_layer: list[float | None] = []
            kinds: list[str] = []
            for node in nodes:
                c1, c2 = st.columns([1, 1], vertical_alignment="bottom")
                kind_key = f"{KEY}_bkind_{node.key}"
                if kind_key not in ss and ss.get(f"{KEY}_bfix_{node.key}") is False:
                    ss[kind_key] = "gesloten"  # project saved with the old on/off switch
                kind = c1.selectbox(
                    f"Rand {node.label}", p3.RING_KINDS, key=kind_key,
                    format_func=BOUNDARY_LABEL.get,
                )  # fmt: skip
                head = c2.number_input(
                    f"Stijghoogte rand {node.label} [m NAP]", -50.0, 50.0,
                    DEFAULTS.boundary_head, 0.1, format="%g", key=f"{KEY}_bh_{node.key}",
                    disabled=kind == "gesloten",
                )  # fmt: skip
                per_layer.append(None if kind == "gesloten" else head)
                kinds.append(kind)
            values["boundary_layers"] = tuple(per_layer)
            values["boundary_kinds"] = tuple(kinds)
        values["seepage"] = st.toggle(
            "Kwel/wegzijging via de onderrand", value=False, key=f"{KEY}_kwel",
            help="Diep pakket onder de onderste rekenlaag met een vaste stijghoogte φ, "
            "via een weerstand c. φ is gelijk in referentie en opzet.",
        )  # fmt: skip
        if values["seepage"]:
            c1, c2 = st.columns(2)
            with c1:
                values["seepage_head"] = number(
                    p3.Params, "seepage_head", DEFAULTS.seepage_head, f"{KEY}_phi", 0.1
                )
            with c2:
                values["seepage_resistance"] = number(
                    p3.Params, "seepage_resistance", DEFAULTS.seepage_resistance,
                    f"{KEY}_ck", 100.0,
                )  # fmt: skip


def subsoil_step(
    values: dict[str, Any],
) -> tuple[LayerStack, dict[int, tuple[LayerStack, float]], str, Any]:
    """Step 1; fills ``values``. Returns the base stack, the zone values, which schematic
    to draw ("basis" or a zone name) and the empty box for that schematic."""
    col_in, col_fig = st.columns([2, 3], gap="large")
    with col_in:
        st.subheader("Lagenopbouw")
        stack, _ = stack_inputs(
            DEFAULTS, KEY, max_aquifers=p3.MAX_AQUIFERS, with_w=False, container=st.container()
        )
        zone_values = zone_inputs(stack)
    with col_fig:
        zone_names = [z["name"] for z in ss[f"{KEY}_zones"]]
        schematic_of = (
            st.selectbox(
                "Doorsnede van", ["basis", *zone_names], key=f"{KEY}_schem_of",
                format_func=lambda n: "basisopbouw" if n == "basis" else f"zone {n}",
            )
            if zone_names
            else "basis"
        )  # fmt: skip
        schematic_box = st.empty()
        _surface_inputs(values)
        _boundary_inputs(values, stack)
    return stack, zone_values, schematic_of, schematic_box
