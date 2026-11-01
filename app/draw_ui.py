"""Step 2 of the Grondwatertoolbox page: drawing watercourses, drains, wells, GHB and zones
on the grid, and the tables to edit them."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from draw_actions import NEW, apply_selection
from grid_ui import add_background, centre_key, drawing_figure
from location_ui import background, background_caption, background_inputs, model_bbox
from streamlit.errors import StreamlitAPIException
from toolbox_state import DEFAULT_RADIUS, KEY, MAP_CONFIG, Grid, bump, example, reset, ss
from water_import_ui import area_outlines, import_inputs, import_notice

from gwproducten.products import peilopzet_grid as p3

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
OBJECTS = {
    "watergang": "Watergang",
    "drain": "Buisdrainage",
    "put": "Put",
    "ghb": "GHB",
    "zone": "Zone",
}
WC_MODES = {"lijn": "lijn klikken", "cellen": "cellen selecteren", "wissen": "cellen weghalen"}
GROUP_COLUMNS = {
    "wells": {
        "name": "Naam", "layer": "Laag", "q_ref": "Q per put ref [m³/d]",
        "q_new": "Q per put opzet [m³/d]", "radius": "Putstraal [m]",
    },
    "ghb": {
        "name": "Naam", "layer": "Laag", "head_ref": "h ref [m NAP]",
        "head_new": "h opzet [m NAP]", "resistance": "Weerstand c [d]",
    },
}  # fmt: skip


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


def group_form(action: str, labels: dict[str, str], surface: float) -> dict[str, Any]:
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
        props["radius"] = st.number_input(
            "Putstraal [m]", value=DEFAULT_RADIUS, min_value=0.01, max_value=2.0, step=0.05,
            key=f"{k}_rw",
            help="Alleen voor de stijghoogte in de put zelf (Peaceman); de kaart toont het "
            "celgemiddelde.",
        )  # fmt: skip
    else:
        props["head_ref"] = c1.number_input(
            "h ref [m NAP]", value=surface - 1.2, step=0.05, key=f"{k}_hr"
        )
        props["head_new"] = c2.number_input(
            "h opzet [m NAP]", value=surface - 1.2, step=0.05, key=f"{k}_hn"
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
                    **{
                        cols[f]: labels.get(g[f], g[f])
                        if f == "layer"
                        else g.get(f, DEFAULT_RADIUS)
                        for f in cols
                    },
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
            bump()
            st.rerun()


def watercourse_tables(grid: Grid) -> None:
    """Editable properties of the watercourses and their length per cell."""
    cell = grid.cell
    wcs_view = grid.watercourses()
    if not ss[f"{KEY}_wcs"]:
        return
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
        bump()
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
        st.caption("Pas de lengte aan, of verwijder een rij om de cel uit deze watergang te halen.")
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
            bump()
            st.rerun()


def draw_step(
    grid: Grid,
    values: dict[str, Any],
    node_labels: dict[str, str],
    zone_values: dict[int, Any],
) -> go.Figure:
    """Step 2; returns the drawing figure (for the PNG export)."""
    st.caption(
        "Kies rechts **wat** je tekent en **hoe**. Een watergang teken je als lijn (klik punten; "
        "de lengte per cel volgt uit de lijn) of met rechthoek/lasso als volle cellen. Andere "
        "objecten: selecteer cellen met rechthoek of lasso (werkbalk rechtsboven) en klik "
        "*Toepassen*. Lichtere kleur = minder watergang in de cel."
    )
    draw_panel(grid, values, node_labels, zone_values)
    import_inputs(grid, values)
    import_notice()
    group_tables(node_labels)
    watercourse_tables(grid)
    return ss[f"{KEY}_fig_draw"]


def _rerun_panel() -> None:
    """Rerun only the drawing panel; Streamlit refuses that during a full-page run (e.g.
    in tests), and then the whole page reruns."""
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:
        st.rerun()


@st.fragment
def draw_panel(
    grid: Grid,
    values: dict[str, Any],
    node_labels: dict[str, str],
    zone_values: dict[int, Any],
) -> None:
    """Map and drawing controls. A fragment: selecting cells, clicking line points and the
    form inputs only rerun this panel; changes to the model (Toepassen, Lijn afronden,
    Voorbeeld, Wissen) rerun the whole page."""
    cell, nrow, ncol = grid.cell, grid.nrow, grid.ncol
    col_map, col_ctl = st.columns([3, 2])
    with col_map:
        background_inputs()
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
            nrow, ncol, cell, grid.watercourses(), grid.drain_cells(),
            current_line=ss[f"{KEY}_line"], snap=snap, wells=grid.groups("wells"),
            ghbs=grid.groups("ghb"), zones=grid.zones(zone_values),
            outlines=area_outlines(grid, values),
        )  # fmt: skip
        width, height = ncol * cell, nrow * cell
        bbox = model_bbox(values["origin_x"], values["origin_y"], width, height)
        add_background(fig_draw, background(bbox), width, height)
        ss[f"{KEY}_fig_draw"] = fig_draw
        # A stable key keeps zoom and pan between reruns; each selection is used only once
        # (``fresh``) instead of clearing it with a new key.
        draw_key = f"{KEY}_draw_{'lijn' if line_mode else 'cel'}"
        event = st.plotly_chart(
            fig_draw, key=draw_key, on_select="rerun", config=MAP_CONFIG, width="stretch",
            selection_mode=("points",) if line_mode else ("box", "lasso"),
        )  # fmt: skip
        background_caption()
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
        _rerun_panel()  # the line in progress is not part of the model yet

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
            props = group_form(obj, node_labels, values["surface"])
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
                _rerun_panel()
            if b2.button("Lijn annuleren", disabled=not line, key=f"{KEY}_cancel"):
                ss[f"{KEY}_line"] = []
                _rerun_panel()
            if st.button(
                "Lijn afronden", type="primary", disabled=len(line) < 2, key=f"{KEY}_finish"
            ):
                if target == NEW:
                    wcs.append({**props, "lines": [list(line)], "points": set(), "manual": {},
                                "manual_cell": cell})  # fmt: skip
                else:
                    next(wc for wc in wcs if wc["name"] == target)["lines"].append(list(line))
                ss[f"{KEY}_line"] = []
                bump()
                st.rerun()
        else:
            st.markdown(f"**Selectie: {len(selected)} cellen**")
            if st.button("Toepassen", type="primary", disabled=not selected, key=f"{KEY}_apply"):
                apply_selection(ss, KEY, (cell, nrow, ncol), obj, how, target, props, selected)
                ss[f"{KEY}_used_sel"] = selection_id
                bump()
                st.rerun()
        st.divider()
        b1, b2 = st.columns(2)
        if b1.button("Voorbeeld laden", key=f"{KEY}_ex"):
            reset(example())
            st.rerun()
        if b2.button("Alles wissen", key=f"{KEY}_clear"):
            reset([])
            st.rerun()
