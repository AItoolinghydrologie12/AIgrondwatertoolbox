"""Grondwatertoolbox — peilopzet en grondwaterstroming op een rooster (numeriek)."""

from __future__ import annotations

import dataclasses
import hashlib
import math
import time
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from draw_actions import NEW, apply_selection, new_zone
from grid_ui import (
    absolute_lines,
    centre_key,
    drawing_figure,
    result_figure,
    to_cells,
    wc_geometry,
)
from profile_ui import crossings, profile_figure, profile_table
from project_io import apply_project, parse_project, to_json
from ui_common import (
    TABS,
    csv_button,
    dict_table,
    number,
    page_header,
    png_buttons,
    show_param_error,
    split_theory,
    stack_inputs,
    zone_stack_inputs,
)

from gwproducten.core.layers import LayerStack
from gwproducten.core.params import ParamError, meta
from gwproducten.products import peilopzet_grid as p3

KEY = "p3"
DEFAULTS = p3.Params()
WC_FIELDS = [
    "stage_ref", "stage_new", "bottom", "width", "length", "entry_resistance", "conductance",
]  # fmt: skip
WC_COLUMNS = {
    "name": "Naam",
    "kind": "Type",
    "stage_ref": "Peil ref [m NAP]",
    "stage_new": "Peil opzet [m NAP]",
    "bottom": "Bodem [m NAP]",
    "width": "Breedte [m]",
    "length": "Lengte/cel lasso [m]",
    "entry_resistance": "Intreeweerstand [d]",
    "conductance": "C volle cel [m²/d] (0 = auto)",
}
STEPS = ["1 · Ondergrond en maaiveld", "2 · Watergangen tekenen", "3 · Berekenen en resultaat"]
OBJECTS = {
    "watergang": "Watergang",
    "drain": "Buisdrainage",
    "put": "Put",
    "ghb": "GHB",
    "zone": "Zone",
}
MAP_CONFIG = {"scrollZoom": True, "displaylogo": False}
WC_MODES = {"lijn": "lijn klikken", "cellen": "cellen selecteren", "wissen": "cellen weghalen"}
BOUNDARY_LABEL = {
    "vast": "vaste stijghoogte",
    "gesloten": "gesloten",
    "per_laag": "per laag",
}

page_header(p3.META)
ss = st.session_state


def _example() -> list[dict[str, Any]]:
    """Default example: north-south lines at fractions of the model width, so they stay at
    1/3 and 2/3 when the model or cell size changes."""
    out = []
    for frac, wc in p3.DEFAULT_WATERCOURSE_SPECS:
        d = {f: getattr(wc, f) for f in ["name", "kind", *WC_FIELDS]}
        d["lines"], d["rel_lines"] = [], [[(frac, 0.0), (frac, 1.0)]]
        d["points"], d["manual"], d["manual_cell"] = set(), {}, DEFAULTS.cell
        out.append(d)
    return out


def _reset(wcs: list[dict[str, Any]]) -> None:
    ss[f"{KEY}_wcs"], ss[f"{KEY}_drain"], ss[f"{KEY}_line"] = wcs, set(), []
    ss[f"{KEY}_wells"], ss[f"{KEY}_ghb"], ss[f"{KEY}_zones"] = [], [], []
    ss[f"{KEY}_ver"] = ss.get(f"{KEY}_ver", 0) + 1


if f"{KEY}_wcs" not in ss:
    _reset(_example())
ss.setdefault(f"{KEY}_zones", [])
_pending = ss.pop(f"{KEY}_pending_project", None)
if _pending is not None:  # loaded in the previous run, applied before the widgets exist
    apply_project(ss, KEY, _pending)
    ss.pop(f"{KEY}_result", None)
    ss[f"{KEY}_ver"] = ss.get(f"{KEY}_ver", 0) + 1


def _bump() -> None:
    ss[f"{KEY}_ver"] += 1  # new widget keys clear the map selection and tables


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
                _bump()
                st.rerun()
    if st.button(
        "Zone toevoegen", key=f"{KEY}_zone_add", icon=":material/add:",
        disabled=len(zones) >= p3.MAX_ZONES,
        help=f"Maximaal {p3.MAX_ZONES} zones.",
    ):  # fmt: skip
        new_zone(ss, KEY)
        st.rerun()
    return out


def size_input(name: str, key: str, size_max: float) -> float:
    """Model width/height whose maximum follows the cell size; an old value above the new
    maximum is clipped instead of raising a widget error."""
    m = meta(p3.Params, name)
    ss.setdefault(key, float(getattr(DEFAULTS, name)))
    ss[key] = min(float(ss[key]), size_max)
    return st.number_input(
        f"{m['label']} [{m['unit']}]", float(m["min"]), size_max, step=25.0, format="%g",
        key=key,
    )  # fmt: skip


def props_form(prefix: str, cell: float, surface: float) -> dict[str, Any]:
    """Properties of a new watercourse; levels default relative to the surface."""
    props: dict[str, Any] = {
        "name": st.text_input(
            "Naam", f"Watergang {len(ss[f'{KEY}_wcs']) + 1}", key=f"{prefix}_name"
        ),
        "kind": st.selectbox("Type", p3.KINDS, format_func=p3.KIND_LABEL.get, key=f"{prefix}_kind"),
    }
    c1, c2 = st.columns(2)
    props["stage_ref"] = c1.number_input(
        "Peil ref [m NAP]", value=surface - 1.2, step=0.05, key=f"{prefix}_sr"
    )
    props["stage_new"] = c2.number_input(
        "Peil opzet [m NAP]", value=surface - 1.2, step=0.05, key=f"{prefix}_sn"
    )
    props["bottom"] = c1.number_input(
        "Bodem [m NAP]", value=surface - 1.6, step=0.1, key=f"{prefix}_b"
    )
    props["width"] = c2.number_input(
        "Breedte [m]", value=1.0, min_value=0.1, step=0.5, key=f"{prefix}_w"
    )
    props["entry_resistance"] = c1.number_input(
        "Intreeweerstand [d]", value=1.0, min_value=0.0, step=0.5, key=f"{prefix}_c"
    )
    props["length"] = c2.number_input(
        "Lengte per cel bij lasso [m]", value=cell, min_value=0.1, step=5.0, key=f"{prefix}_l",
        help="Alleen voor cellen die je met rechthoek/lasso kiest; bij een lijn volgt de "
        "lengte per cel uit de getekende lijn.",
    )  # fmt: skip
    props["conductance"] = st.number_input(
        "C per volle cel [m²/d] — 0 = automatisch", value=0.0, min_value=0.0, step=10.0,
        key=f"{prefix}_cond",
        help="Laat op 0: de app berekent C uit breedte, waterdiepte, intreeweerstand, de "
        "geraakte lagen en de celgrootte-correctie. Vul alleen in als je een conductance uit "
        "een ander model wilt overnemen; bij gedeeltelijke cellen schaalt die met de lengte.",
    )  # fmt: skip
    return props


GROUP_COLUMNS = {
    "wells": {
        "name": "Naam", "layer": "Laag", "q_ref": "Q per put ref [m³/d]",
        "q_new": "Q per put opzet [m³/d]",
    },
    "ghb": {
        "name": "Naam", "layer": "Laag", "head_ref": "h ref [m NAP]",
        "head_new": "h opzet [m NAP]", "resistance": "Weerstand c [d]",
    },
}  # fmt: skip


def group_form(action: str, labels: dict[str, str]) -> dict[str, Any]:
    """Properties of a new well group (action "put") or GHB group (action "ghb")."""
    kind = "wells" if action == "put" else "ghb"
    n = len(ss[f"{KEY}_{kind}"]) + 1
    k = f"{KEY}_{action}"
    props: dict[str, Any] = {
        "name": st.text_input(
            "Naam", f"Putten {n}" if action == "put" else f"GHB {n}", key=f"{k}_name"
        ),
        "layer": st.selectbox(
            "Laag", list(labels), format_func=labels.get, key=f"{k}_layer",
            index=len(labels) - 1 if action == "put" and labels else 0,
        ),
    }  # fmt: skip
    c1, c2 = st.columns(2)
    if action == "put":
        props["q_ref"] = c1.number_input(
            "Q per put ref [m³/d]", value=0.0, step=10.0, key=f"{k}_qr",
            help="Positief = onttrekking, negatief = infiltratie.",
        )  # fmt: skip
        props["q_new"] = c2.number_input(
            "Q per put opzet [m³/d]", value=100.0, step=10.0, key=f"{k}_qn"
        )
    else:
        props["head_ref"] = c1.number_input(
            "h ref [m NAP]", value=values["surface"] - 1.2, step=0.05, key=f"{k}_hr"
        )
        props["head_new"] = c2.number_input(
            "h opzet [m NAP]", value=values["surface"] - 1.2, step=0.05, key=f"{k}_hn"
        )
        props["resistance"] = st.number_input(
            "Weerstand c [d]", value=1.0, min_value=0.01, step=0.5, key=f"{k}_c",
            help="Conductance per cel = celoppervlak / c.",
        )  # fmt: skip
    return props


def group_tables(labels: dict[str, str]) -> None:
    """Editable tables of the well and GHB groups."""
    by_label = {v: k for k, v in labels.items()}
    for kind, title in (("wells", "Putgroepen"), ("ghb", "GHB-groepen")):
        items = ss[f"{KEY}_{kind}"]
        if not items:
            continue
        cols = GROUP_COLUMNS[kind]
        st.markdown(f"**{title}** (direct te bewerken)")
        table = pd.DataFrame(
            [
                {
                    **{cols[f]: labels.get(g[f], g[f]) if f == "layer" else g[f] for f in cols},
                    "Cellen": len(g["points"]),
                    "Verwijderen": False,
                }
                for g in items
            ]
        )
        edited = st.data_editor(
            table, hide_index=True, key=f"{KEY}_{kind}_table_{ss[f'{KEY}_ver']}",
            width="stretch",
            column_config={
                "Laag": st.column_config.SelectboxColumn(options=list(by_label)),
                "Cellen": st.column_config.NumberColumn(disabled=True),
            },
        )  # fmt: skip
        for g, (_, row) in zip(items, edited.iterrows(), strict=True):
            for f, colname in cols.items():
                if f == "name":
                    g[f] = row[colname]
                elif f == "layer":
                    g[f] = by_label.get(row[colname], row[colname])
                else:
                    g[f] = float(row[colname])
        if edited["Verwijderen"].any():
            ss[f"{KEY}_{kind}"] = [
                g for g, rm in zip(items, edited["Verwijderen"], strict=True) if not rm
            ]
            _bump()
            st.rerun()


@st.cache_data(show_spinner="Rekenen…", max_entries=20)  # bounded: shared memory in the cloud
def run(params: p3.Params) -> p3.Result:
    return p3.compute(params)


# ---- zijbalk: rekenen, model en numeriek ----------------------------------------------
clicked = st.sidebar.button(
    "Bereken", type="primary", icon=":material/play_arrow:", key=f"{KEY}_calc_side",
    width="stretch",
)  # fmt: skip
status_side = st.sidebar.empty()
values: dict[str, Any] = {}
with st.sidebar.expander("Model en rand", expanded=True):
    values["cell"] = number(p3.Params, "cell", DEFAULTS.cell, f"{KEY}_cell", 5.0)
    size_max = p3.MAX_CELLS_PER_SIDE * values["cell"]
    for name, k in (("size_x", "sx"), ("size_y", "sy")):
        values[name] = size_input(name, f"{KEY}_{k}", size_max)
    st.caption(
        f"Maximaal {p3.MAX_CELLS_PER_SIDE} × {p3.MAX_CELLS_PER_SIDE} cellen: bij "
        f"{values['cell']:g} m is het model hooguit {size_max:g} m breed en hoog."
    )
    st.caption("De modelrand en kwel stel je in bij stap 1.")
project_box = st.sidebar.expander("Project opslaan of laden", expanded=False)
with st.sidebar.expander("Beoordeling en numeriek", expanded=False):
    values["threshold"] = number(p3.Params, "threshold", DEFAULTS.threshold, f"{KEY}_thr", 0.01)
    values["grid_correction"] = st.checkbox(
        "Celgrootte-correctie (De Lange-principe)", value=True, key=f"{KEY}_gc"
    )
    values["convertible"] = st.checkbox(
        "Transmissiviteit volgt de natte dikte (zoals MODFLOW 6 'convertible')",
        value=True,
        key=f"{KEY}_conv",
        help="Uit: T = kh·D blijft vast (lineair model). Aan: T = kh × natte dikte in het "
        "freatisch pakket en in pakketten waar de stijghoogte onder de bovenkant zakt.",
    )

step1, step2, step3 = st.tabs(STEPS, key=f"{KEY}_step")

# ---- stap 1: ondergrond, maaiveld en drainage ------------------------------------------
with step1:
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
        st.subheader("Modelrand en kwel")
        with st.container(border=True):
            nodes = stack.nodes if not stack.errors() else []
            values["boundary"] = st.radio(
                "Modelrand (buitenste ring cellen)", p3.BOUNDARIES, horizontal=True,
                key=f"{KEY}_bnd", format_func=BOUNDARY_LABEL.get,
            )  # fmt: skip
            if values["boundary"] == "vast":
                values["boundary_head"] = number(
                    p3.Params, "boundary_head", DEFAULTS.boundary_head, f"{KEY}_bh", 0.1
                )
            elif values["boundary"] == "per_laag":
                per_layer: list[float | None] = []
                for node in nodes:
                    c1, c2 = st.columns([1, 1], vertical_alignment="bottom")
                    fixed_here = c1.toggle(
                        f"{node.label}: vaste stijghoogte", value=True,
                        key=f"{KEY}_bfix_{node.key}",
                    )  # fmt: skip
                    head = c2.number_input(
                        f"Stijghoogte rand {node.label} [m NAP]", -50.0, 50.0,
                        DEFAULTS.boundary_head, 0.1, format="%g", key=f"{KEY}_bh_{node.key}",
                        disabled=not fixed_here,
                    )  # fmt: skip
                    per_layer.append(head if fixed_here else None)
                values["boundary_layers"] = tuple(per_layer)
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

cell = float(values["cell"])
nrow = int(round(values["size_y"] / cell))
ncol = int(round(values["size_x"] / cell))


def geometry() -> list[dict[str, Any]]:
    out = []
    for wc in ss[f"{KEY}_wcs"]:
        cells, lengths = wc_geometry(wc, cell, nrow, ncol)
        lines = absolute_lines(wc, ncol * cell, nrow * cell)
        out.append({**wc, "cells": cells, "lengths": lengths, "lines": lines})
    return out


def groups(name: str) -> list[dict[str, Any]]:
    """Well or GHB groups with their cells on the current grid."""
    return [{**g, "cells": to_cells(g["points"], cell, nrow, ncol)} for g in ss[f"{KEY}_{name}"]]


def zone_groups() -> list[dict[str, Any]]:
    """Zones with their cells on the current grid (zones without cells are left out)."""
    out = []
    for z in ss[f"{KEY}_zones"]:
        cells = to_cells(z["points"], cell, nrow, ncol)
        if cells and z["id"] in zone_values:
            out.append({**z, "cells": cells})
    return out


def build_params(wcs_view: list[dict[str, Any]], drain_cells: tuple) -> p3.Params:
    return p3.Params(
        stack=stack,
        drain_cells=drain_cells,
        zones=tuple(
            p3.Zone(z["name"], z["cells"], *zone_values[z["id"]]) for z in zone_groups()
        ),
        wells=tuple(
            p3.WellGroup(g["name"], g["cells"], g["layer"], g["q_ref"], g["q_new"])
            for g in groups("wells")
        ),
        ghb=tuple(
            p3.GhbGroup(
                g["name"], g["cells"], g["layer"], g["head_ref"], g["head_new"], g["resistance"]
            )
            for g in groups("ghb")
        ),
        watercourses=tuple(
            p3.Watercourse(
                cells=wc["cells"], lengths=wc["lengths"],
                **{f: wc[f] for f in ["name", "kind", *WC_FIELDS]},
            )
            for wc in wcs_view
        ),
        **values,
    )  # fmt: skip


if f"{KEY}_wells" not in ss:
    ss[f"{KEY}_wells"], ss[f"{KEY}_ghb"] = [], []
wcs_view = geometry()
drain_cells = to_cells(ss[f"{KEY}_drain"], cell, nrow, ncol)
node_labels = {n.key: n.label for n in stack.nodes} if not stack.errors() else {}

try:
    schem_params = build_params(wcs_view, drain_cells)
    if schematic_of != "basis":  # cross-section with the layers and surface of that zone
        zid = next(z["id"] for z in ss[f"{KEY}_zones"] if z["name"] == schematic_of)
        zstack, zsurface = zone_values[zid]
        schem_params = dataclasses.replace(schem_params, stack=zstack, surface=zsurface, zones=())
    schematic_box.plotly_chart(p3.schematic(schem_params), width="stretch")
except (ParamError, ValueError) as err:
    schematic_box.info(f"Lagenopbouw niet te tekenen: {err}")


# ---- stap 2: tekenen --------------------------------------------------------------
with step2:
    st.caption(
        "Kies rechts **wat** je tekent en **hoe**. Een watergang teken je als lijn (klik punten; "
        "de lengte per cel volgt uit de lijn) of met rechthoek/lasso als volle cellen. Andere "
        "objecten: selecteer cellen met rechthoek of lasso (werkbalk rechtsboven) en klik "
        "*Toepassen*. Lichtere kleur = minder watergang in de cel."
    )
    col_map, col_ctl = st.columns([3, 2])
    with col_ctl:
        obj = st.radio(
            "Wat teken je?", list(OBJECTS), format_func=OBJECTS.get, horizontal=True,
            key=f"{KEY}_obj",
        )  # fmt: skip
        if obj == "watergang":
            how = st.radio(
                "Hoe?", list(WC_MODES), format_func=WC_MODES.get, horizontal=True,
                key=f"{KEY}_wc_how",
            )  # fmt: skip
        else:
            how = st.radio(
                "Hoe?", ["toevoegen", "verwijderen"], horizontal=True, key=f"{KEY}_how",
                format_func={"toevoegen": "cellen toevoegen", "verwijderen": "cellen weghalen"}.get,
            )  # fmt: skip
    line_mode = obj == "watergang" and how == "lijn"
    snap = max(cell / 5.0, max(values["size_x"], values["size_y"]) / 200.0) if line_mode else None

    with col_map:
        fig_draw = drawing_figure(
            nrow, ncol, cell, wcs_view, drain_cells, current_line=ss[f"{KEY}_line"], snap=snap,
            wells=groups("wells"), ghbs=groups("ghb"), zones=zone_groups(),
        )  # fmt: skip
        # A stable key keeps zoom and pan between reruns; each selection is used only once
        # (``fresh``) instead of clearing it with a new key.
        draw_key = f"{KEY}_draw_{'lijn' if line_mode else 'cel'}"
        event = st.plotly_chart(
            fig_draw, key=draw_key, on_select="rerun", config=MAP_CONFIG, width="stretch",
            selection_mode=("points",) if line_mode else ("box", "lasso"),
        )  # fmt: skip
        target_curve = len(fig_draw.data) - 1
        picked = [
            (float(p["x"]), float(p["y"]))
            for p in (event.selection.get("points", []) if event else [])
            if p.get("curve_number") == target_curve
        ]
        selection_id = (draw_key, tuple(sorted(picked)))
        fresh = bool(picked) and selection_id != ss.get(f"{KEY}_used_sel")

    if line_mode and fresh:
        ss[f"{KEY}_used_sel"] = selection_id
        pt = picked[-1]
        if not ss[f"{KEY}_line"] or ss[f"{KEY}_line"][-1] != pt:
            ss[f"{KEY}_line"].append(pt)
        _bump()
        st.rerun()

    with col_ctl:
        wcs = ss[f"{KEY}_wcs"]
        zones = ss[f"{KEY}_zones"]
        selected = set(picked) if fresh and not line_mode else set()
        target = None
        props: dict[str, Any] = {}
        if obj == "watergang" and how != "wissen":
            target = st.selectbox(
                "Watergang", [NEW, *[wc["name"] for wc in wcs]], key=f"{KEY}_wc_target",
                format_func=lambda n: "+ nieuwe watergang" if n == NEW else n,
            )  # fmt: skip
            if target == NEW:
                props = props_form(f"{KEY}_np", cell, values["surface"])
        elif obj in ("put", "ghb") and how == "toevoegen":
            props = group_form(obj, node_labels)
        elif obj == "zone" and how == "toevoegen":
            target = st.selectbox(
                "Zone", [NEW, *[z["id"] for z in zones]], key=f"{KEY}_zone_target",
                format_func={NEW: "+ nieuwe zone", **{z["id"]: z["name"] for z in zones}}.get,
            )  # fmt: skip
            st.caption("Lagen en maaiveld van een zone stel je in bij stap 1.")

        if line_mode:
            line = ss[f"{KEY}_line"]
            length = sum(math.dist(a, b) for a, b in zip(line[:-1], line[1:], strict=True))
            st.markdown(f"**Lijn: {len(line)} punten, {length:.0f} m**")
            b1, b2 = st.columns(2)
            if b1.button("Laatste punt weg", disabled=not line, key=f"{KEY}_undo"):
                line.pop()
                _bump()
                st.rerun()
            if b2.button("Lijn annuleren", disabled=not line, key=f"{KEY}_cancel"):
                ss[f"{KEY}_line"] = []
                _bump()
                st.rerun()
            if st.button(
                "Lijn afronden", type="primary", disabled=len(line) < 2, key=f"{KEY}_finish"
            ):
                if target == NEW:
                    wcs.append({**props, "lines": [list(line)], "points": set(), "manual": {},
                                "manual_cell": cell})  # fmt: skip
                else:
                    next(wc for wc in wcs if wc["name"] == target)["lines"].append(list(line))
                ss[f"{KEY}_line"] = []
                _bump()
                st.rerun()
        else:
            st.markdown(f"**Selectie: {len(selected)} cellen**")
            if st.button("Toepassen", type="primary", disabled=not selected, key=f"{KEY}_apply"):
                apply_selection(ss, KEY, (cell, nrow, ncol), obj, how, target, props, selected)
                ss[f"{KEY}_used_sel"] = selection_id
                _bump()
                st.rerun()
        st.divider()
        b1, b2 = st.columns(2)
        if b1.button("Voorbeeld laden", key=f"{KEY}_ex"):
            _reset(_example())
            st.rerun()
        if b2.button("Alles wissen", key=f"{KEY}_clear"):
            _reset([])
            st.rerun()

    # ---- tabellen: putgroepen en GHB-groepen --------------------------------------------
    group_tables(node_labels)

    # ---- tabellen: eigenschappen en lengte per cel ----------------------------------------
    wcs_view = geometry()
    if ss[f"{KEY}_wcs"]:
        st.markdown("**Eigenschappen watergangen** (direct te bewerken)")
        table = pd.DataFrame(
            [
                {
                    **{WC_COLUMNS[f]: wc[f] for f in WC_COLUMNS},
                    "Cellen": len(v["cells"]),
                    "Totale lengte [m]": round(sum(v["lengths"]), 1),
                    "Verwijderen": False,
                }
                for wc, v in zip(ss[f"{KEY}_wcs"], wcs_view, strict=True)
            ]
        )
        edited = st.data_editor(
            table, hide_index=True, key=f"{KEY}_table_{ss[f'{KEY}_ver']}", width="stretch",
            column_config={
                "Type": st.column_config.SelectboxColumn(options=list(p3.KINDS)),
                "Cellen": st.column_config.NumberColumn(disabled=True),
                "Totale lengte [m]": st.column_config.NumberColumn(disabled=True),
            },
        )  # fmt: skip
        for wc, (_, row) in zip(ss[f"{KEY}_wcs"], edited.iterrows(), strict=True):
            for f, colname in WC_COLUMNS.items():
                wc[f] = row[colname] if f in ("name", "kind") else float(row[colname])
        if edited["Verwijderen"].any():
            ss[f"{KEY}_wcs"] = [
                wc for wc, rm in zip(ss[f"{KEY}_wcs"], edited["Verwijderen"], strict=True) if not rm
            ]
            _bump()
            st.rerun()

        with st.expander("Lengte per cel aanpassen", expanded=False):
            names = [wc["name"] for wc in ss[f"{KEY}_wcs"]]
            pick = st.selectbox("Watergang", names, key=f"{KEY}_celwc")
            i = names.index(pick)
            wc, view = ss[f"{KEY}_wcs"][i], wcs_view[i]
            if wc.get("manual") and wc.get("manual_cell") != cell:
                st.info("Handmatige lengtes horen bij een andere celgrootte en worden genegeerd.")
            cells_df = pd.DataFrame(
                {
                    "Rij": [r for r, _ in view["cells"]],
                    "Kolom": [c for _, c in view["cells"]],
                    "x [m]": [(c + 0.5) * cell for _, c in view["cells"]],
                    "y [m]": [(r + 0.5) * cell for r, _ in view["cells"]],
                    "Lengte [m]": np.round(view["lengths"], 2),
                }
            )
            st.caption(
                "Pas de lengte aan, of verwijder een rij om de cel uit deze watergang te halen."
            )
            edited_cells = st.data_editor(
                cells_df, hide_index=True, num_rows="dynamic", width="stretch",
                key=f"{KEY}_cells_{pick}_{ss[f'{KEY}_ver']}",
                column_config={c: st.column_config.NumberColumn(disabled=True)
                               for c in ("Rij", "Kolom", "x [m]", "y [m]")},
            )  # fmt: skip
            kept = {
                (int(r["Rij"]), int(r["Kolom"])): float(r["Lengte [m]"])
                for _, r in edited_cells.dropna(subset=["Rij", "Kolom"]).iterrows()
            }
            new_manual = {}
            for (r, c), length in zip(view["cells"], view["lengths"], strict=True):
                target_len = kept.get((r, c), 0.0)
                if abs(target_len - round(length, 2)) > 1e-6:  # compare with the shown value
                    new_manual[centre_key(r, c, cell)] = target_len
            if new_manual:
                if wc.get("manual_cell") != cell:
                    wc["manual"], wc["manual_cell"] = {}, cell
                wc["manual"].update(new_manual)
                _bump()
                st.rerun()

# ---- stap 3: rekenen (alleen na "Bereken") en resultaat --------------------------------
wcs_view = geometry()
try:
    current = build_params(wcs_view, drain_cells)
    input_error = None
except ParamError as err:
    current, input_error = None, err

with step3:
    c_btn, c_msg = st.columns([1, 4], vertical_alignment="center")
    clicked |= c_btn.button(
        "Bereken", type="primary", icon=":material/play_arrow:", key=f"{KEY}_calc",
        width="stretch",
    )  # fmt: skip

if clicked and input_error is None:
    t0 = time.perf_counter()
    try:
        ss[f"{KEY}_result"] = {
            "params": current,
            "res": run(current),
            "wcs": wcs_view,
            "seconds": time.perf_counter() - t0,
        }
    except ParamError as err:
        input_error = err

done = ss.get(f"{KEY}_result")
if done is None:
    status = ("info", "Nog niet gerekend. Teken de watergangen en klik op **Bereken**.")
elif current != done["params"]:
    status = (
        "warning",
        "De invoer is gewijzigd: het resultaat hoort bij de vorige berekening. "
        "Klik op **Bereken** om opnieuw te rekenen.",
    )
else:
    status = ("success", f"Berekend in {done['seconds']:.1f} s.")
getattr(c_msg, status[0])(status[1])
getattr(status_side, status[0])(status[1])
if input_error is not None:
    with step3:
        show_param_error(input_error)
    status_side.error("De invoer is niet geldig; zie stap 3.")

theory, assumptions = split_theory(p3.THEORY)
VIEWS = {
    "verhoging": "Verhoging (opzet − referentie)",
    "opzet": "Stijghoogte na opzet [m NAP]",
    "ref": "Stijghoogte referentie [m NAP]",
    "gws": "Grondwaterstand t.o.v. maaiveld na opzet [m]",
    "putten": "Verlaging door putten na opzet [m]",
}
PROFILE_MODES = {
    "horizontaal": "Horizontaal (x)",
    "verticaal": "Verticaal (y)",
    "lijn": "Eigen lijn",
}
BALANCE_OK = 1e-6


def show_kpis(params: p3.Params, res: p3.Result, seconds: float) -> None:
    rise0 = res.rise[0]
    area_ha = float((rise0 >= params.threshold).sum()) * params.cell**2 / 1e4
    raised = [wc.name for wc in params.watercourses if wc.stage_new != wc.stage_ref]
    q_ref = float(res.budget.loc[raised, "referentie [m³/d]"].sum()) if raised else 0.0
    q_new = float(res.budget.loc[raised, "opzet [m³/d]"].sum()) if raised else 0.0
    wet_extra = res.inundated["opzet"] - res.inundated["referentie"]
    balance = max(res.mass_balance.values())
    row1 = st.columns(3)
    row1[0].metric(
        "Max. verhoging freatisch", f"{np.nanmax(rise0):.2f} m", border=True,
        help="Grootste stijging van de grondwaterstand (opzet − referentie) in het model.",
    )  # fmt: skip
    row1[1].metric(
        f"Oppervlak verhoging ≥ {params.threshold * 100:.0f} cm", f"{area_ha:.1f} ha",
        border=True, help="Som van de cellen waar de freatische verhoging de grens haalt.",
    )  # fmt: skip
    row1[2].metric(
        "Infiltratie opgezette watergangen",
        f"{q_new:.0f} m³/d" if raised else "geen opzet",
        delta=f"{q_new - q_ref:+.0f} m³/d t.o.v. referentie" if raised else None,
        delta_color="off", border=True,
        help="Netto uitwisseling van de watergangen met een ander peil na opzet; "
        "positief = water het grondwater in.",
    )  # fmt: skip
    row2 = st.columns(3)
    row2[0].metric(
        "Extra cellen met water op maaiveld", f"{wet_extra:d}",
        delta=f"{res.inundated['opzet']} na opzet, {res.inundated['referentie']} referentie",
        delta_color="off", delta_arrow="off", border=True,
    )  # fmt: skip
    row2[1].metric(
        "Balansfout (max.)", f"{balance:.1e}",
        delta="in orde" if balance < BALANCE_OK else "controleer",
        delta_color="normal" if balance < BALANCE_OK else "inverse",
        delta_arrow="off", border=True,
    )  # fmt: skip
    cells = f"{params.nrow * params.ncol:,}".replace(",", ".")
    row2[2].metric(
        "Rekentijd", f"{seconds:.1f} s",
        delta=f"{cells} cellen × {len(res.node_labels)} lagen",
        delta_color="off", delta_arrow="off", border=True,
    )  # fmt: skip
    for w_msg in res.warnings:
        st.warning(w_msg)


def profile_points(params: p3.Params) -> list[tuple[float, float]]:
    """Profile line from the controls below the map (horizontal, vertical or clicked)."""
    mode = st.segmented_control(
        "Dwarsprofiel", list(PROFILE_MODES), format_func=PROFILE_MODES.get,
        default="horizontaal", required=True, key=f"{KEY}_pmode",
    )  # fmt: skip
    W, H = params.size_x, params.size_y
    if mode in ("horizontaal", "verticaal"):
        size = H if mode == "horizontaal" else W
        k = f"{KEY}_p{mode[0]}"
        if k in ss and not 0.0 <= ss[k] <= size:
            ss[k] = size / 2.0
        pos = st.slider(
            "y van het profiel [m]" if mode == "horizontaal" else "x van het profiel [m]",
            0.0, float(size), float(size / 2.0), params.cell / 2.0, key=k,
        )  # fmt: skip
        return [(0.0, pos), (W, pos)] if mode == "horizontaal" else [(pos, 0.0), (pos, H)]
    pts = ss.setdefault(f"{KEY}_prof", [])
    c1, c2, c3 = st.columns([2, 1, 1], vertical_alignment="center")
    c1.caption(f"Klik punten op de kaart hierboven ({len(pts)} punten). A = begin, B = eind.")
    if c2.button("Laatste punt weg", disabled=not pts, key=f"{KEY}_pundo"):
        pts.pop()
        ss[f"{KEY}_pver"] = ss.get(f"{KEY}_pver", 0) + 1
        st.rerun()
    if c3.button("Profiel wissen", disabled=not pts, key=f"{KEY}_pclear"):
        ss[f"{KEY}_prof"] = []
        ss[f"{KEY}_pver"] = ss.get(f"{KEY}_pver", 0) + 1
        st.rerun()
    return list(pts)


def show_result(
    params: p3.Params, res: p3.Result, wcs_done: list[dict[str, Any]]
) -> tuple[go.Figure, go.Figure | None, pd.DataFrame | None]:
    labels = res.node_labels
    keys = list(labels)
    c5, c6 = st.columns(2)
    views = [v for v in VIEWS if v != "putten" or res.well_drawdown is not None]
    if ss.get(f"{KEY}_view") not in views:
        ss[f"{KEY}_view"] = views[0]
    view = c5.selectbox("Kaart", views, format_func=VIEWS.get, key=f"{KEY}_view")
    layer = c6.selectbox("Laag", keys, format_func=labels.get, key=f"{KEY}_layer")
    j = keys.index(layer)
    if view == "verhoging":
        z, label, thr, rev = res.rise[j], "verhoging [m]", params.threshold, False
    elif view == "opzet":
        z, label, thr, rev = res.heads_new[j], "h [m NAP]", None, False
    elif view == "ref":
        z, label, thr, rev = res.heads_ref[j], "h [m NAP]", None, False
    elif view == "putten":
        z, label, thr, rev = res.well_drawdown[j], "verlaging [m]", None, False
    else:
        z, label, thr, rev = res.surface - res.heads_new[0], "diepte gws [m-mv]", None, True
    map_box = st.container()
    pts = profile_points(params)
    clicking = ss.get(f"{KEY}_pmode") == "lijn"
    snap = max(params.cell / 2.0, max(params.size_x, params.size_y) / 100.0)
    outlined = wcs_done + [{"name": g.name, "cells": g.cells} for g in params.ghb]
    fig_res = result_figure(
        res.x, res.y, z, params.cell, outlined, label=label, threshold=thr, reverse=rev,
        profile=pts, click_snap=snap if clicking else None,
        wells=[{"name": g.name, "cells": g.cells} for g in params.wells],
    )  # fmt: skip
    with map_box:
        event = st.plotly_chart(
            fig_res, width="stretch", key=f"{KEY}_resmap_{clicking}", config=MAP_CONFIG,
            on_select="rerun" if clicking else "ignore", selection_mode=("points",),
        )  # fmt: skip
        if view == "verhoging":
            st.caption(
                f"Dikke rode lijn: {params.threshold * 100:.0f} cm-grens. "
                "Witte stippellijn: dwarsprofiel A–B."
            )
    if clicking and event:
        target = len(fig_res.data) - 1
        picked = [
            (float(p["x"]), float(p["y"]))
            for p in event.selection.get("points", [])
            if p.get("curve_number") == target
        ]
        selection_id = ("resmap", tuple(picked))
        if picked and selection_id != ss.get(f"{KEY}_used_psel"):
            ss[f"{KEY}_used_psel"] = selection_id
            if not pts or pts[-1] != picked[-1]:
                ss[f"{KEY}_prof"].append(picked[-1])
                st.rerun()

    fig_prof, prof = None, None
    if len(pts) >= 2:
        prof = profile_table(
            pts, res.x, res.y, params.cell, res.surface, res.heads_ref, res.heads_new, labels
        )
        crossed = crossings(
            prof["afstand [m]"].to_numpy(), prof["x [m]"].to_numpy(), prof["y [m]"].to_numpy(),
            params.cell,
            [
                {"name": wc.name, "cells": wc.cells, "stage_ref": wc.stage_ref,
                 "stage_new": wc.stage_new}
                for wc in params.watercourses
            ],
        )  # fmt: skip
        fig_prof = profile_figure(prof, labels, crossed, params.threshold)
        st.plotly_chart(fig_prof, width="stretch")
        st.caption(
            "Stippel = referentie, doorgetrokken = na opzet; rood vlak = verhoging van de "
            "bovenste laag (rechteras). Driehoekjes: peil van gekruiste watergangen "
            "(open = referentie, dicht = opzet)."
        )
    with st.expander("Waterbalans en conductances", expanded=False):
        st.markdown("**Waterbalans [m³/d]**")
        st.dataframe(res.budget.round(2))
        st.caption(
            "Positief = water het grondwater in. 'rand' = in/uitstroom over de vaste modelrand."
        )
        st.markdown("**Conductance per cel** (gemiddeld over de cellen van de watergang)")
        st.dataframe(res.conductances.round(3), hide_index=True)
    return fig_res, fig_prof, prof


def show_validation(params: p3.Params, res: p3.Result) -> None:
    st.markdown(
        "**Controles bij de berekende invoer.** Automatische tests: "
        "`tests/test_peilopzet_grid.py` en `tests/test_polyline.py` (parabool tussen twee "
        "sloten, maaivelddrainage, RIV-begrenzing, drain, buisdrainage per cel, lengte per "
        "cel, lijn → cellen, peilopzet vs 1D-analytisch met en zonder celgrootte-correctie, "
        "gridverfijning, massabalans)."
    )
    st.dataframe(dict_table(res.mass_balance, "Massabalansfout [-]", "{:.1e}"), hide_index=True)
    st.dataframe(
        dict_table({f"Iteraties RIV/DRN {k}": v for k, v in res.iterations.items()}, fmt="{:.0f}"),
        hide_index=True,
    )
    st.markdown("**Benchmark: lange evenwijdige sloten tegen de analytische peilopzet**")
    st.caption(
        "Zelfde lagenopbouw als de berekening; opgezette sloot met buren op 100 m, regionale "
        "drainage w = 100 d. Infiltratie van de opgezette sloot per meter, numeriek vs analytisch."
    )
    if st.button("Benchmark draaien", key=f"{KEY}_bench"):
        rows = []
        for cs in (25.0, 12.5, 5.0):
            for corr in (True, False):
                qn, qa = p3.strip_benchmark(params.stack, cs, corr)
                rows.append(
                    {
                        "Celgrootte [m]": cs,
                        "Correctie": "ja" if corr else "nee",
                        "Numeriek [m²/d]": qn,
                        "Analytisch [m²/d]": qa,
                        "Afwijking [%]": 100 * (qn / qa - 1),
                    }
                )
        st.dataframe(pd.DataFrame(rows).round(4), hide_index=True)


def show_export(
    params: p3.Params,
    res: p3.Result,
    wcs_done: list[dict[str, Any]],
    figures: dict[str, go.Figure],
    prof: pd.DataFrame | None,
) -> None:
    labels = res.node_labels
    gx, gy = np.meshgrid(res.x, res.y)
    grid = pd.DataFrame({"x [m]": gx.ravel(), "y [m]": gy.ravel()})
    grid["zone"] = np.array(res.zone_names)[res.zone_map.ravel()]
    grid["maaiveld [m NAP]"] = res.surface.ravel()
    for j, k in enumerate(labels):
        grid[f"h ref {labels[k]} [m NAP]"] = res.heads_ref[j].ravel()
        grid[f"h opzet {labels[k]} [m NAP]"] = res.heads_new[j].ravel()
        grid[f"verhoging {labels[k]} [m]"] = res.rise[j].ravel()
    csv_button(grid, "peilopzet2d_rooster.csv", "Rooster (CSV)")
    csv_button(
        res.budget.reset_index(names="post"), "peilopzet2d_waterbalans.csv", "Waterbalans (CSV)"
    )
    cells = pd.DataFrame(
        [
            {"watergang": wc["name"], "rij": r, "kolom": c, "lengte [m]": length}
            for wc in wcs_done
            for (r, c), length in zip(wc["cells"], wc["lengths"], strict=True)
        ]
        + [{"watergang": "buisdrainage", "rij": r, "kolom": c} for r, c in params.drain_cells]
    )
    cells = pd.concat(
        [
            cells,
            pd.DataFrame(
                [
                    {"watergang": f"{g.name} ({kind})", "rij": r, "kolom": c}
                    for kind, grp in (("put", params.wells), ("GHB", params.ghb))
                    for g in grp
                    for r, c in g.cells
                ],
                columns=["watergang", "rij", "kolom"],
            ),
        ],
        ignore_index=True,
    )
    csv_button(cells, "peilopzet2d_cellen.csv", "Getekende cellen (CSV)")
    if prof is not None:
        csv_button(prof.round(4), "peilopzet2d_profiel.csv", "Dwarsprofiel (CSV)")
    png_buttons(figures, KEY)


NO_RESULT = "Nog geen resultaat: klik op **Bereken**."
with step3:
    if done is not None:
        show_kpis(done["params"], done["res"], done["seconds"])
    t_res, t_theory, t_ass, t_val, t_exp = st.tabs(TABS)
    with t_theory:
        st.markdown(theory)
    with t_ass:
        st.markdown(assumptions)
    if done is None:
        for tab in (t_res, t_val, t_exp):
            tab.info(NO_RESULT)
    else:
        with t_res:
            fig_res, fig_prof, prof = show_result(done["params"], done["res"], done["wcs"])
        with t_val:
            show_validation(done["params"], done["res"])
        with t_exp:
            figs = {"peilopzet2d_kaart": fig_res, "peilopzet2d_tekening": fig_draw}
            if fig_prof is not None:
                figs["peilopzet2d_profiel"] = fig_prof
            show_export(done["params"], done["res"], done["wcs"], figs, prof)


# ---- project opslaan/laden (aan het eind: alle invoer van deze run is bekend) -----------
with project_box:
    st.caption(
        "Sla alle invoer en tekeningen op als bestand; in de browser blijft niets bewaard "
        "na verversen of sluiten."
    )
    st.download_button(
        "Project opslaan (JSON)", to_json(ss, KEY), file_name="grondwatertoolbox_project.json",
        mime="application/json", icon=":material/download:", key=f"{KEY}_save",
        width="stretch",
    )  # fmt: skip
    upload = st.file_uploader("Project laden", type=["json"], key=f"{KEY}_load")
    if upload is not None:
        raw = upload.getvalue()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != ss.get(f"{KEY}_loaded_digest"):
            data, load_errors = parse_project(raw)
            if load_errors:
                st.error(" ".join(load_errors))
            else:
                ss[f"{KEY}_loaded_digest"] = digest
                ss[f"{KEY}_pending_project"] = data
                st.rerun()
        else:
            st.success(f"Geladen: {upload.name}")
