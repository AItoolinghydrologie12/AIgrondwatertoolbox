"""Step 2 of the Grondwatertoolbox page: drawing watercourses, drains, wells, GHB, CHD, walls
(damwanden) and zones on the grid, and the tables to edit them."""

from __future__ import annotations

import math
from typing import Any

import labels as lbl
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from draw_actions import GROUPS, NEW, apply_selection
from grid_ui import add_background, centre_key, drawing_figure, line_snap, to_cells, wc_icon
from location_ui import background, background_caption, background_inputs, model_bbox
from tables_ui import Col, object_table
from toolbox_state import (
    DEFAULT_RADIUS,
    KEY,
    MAP_CONFIG,
    Grid,
    bump,
    can_undo,
    example,
    rerun_fragment,
    reset,
    ss,
    undo,
)  # fmt: skip
from water_import_ui import area_outlines, import_inputs, import_notice

from gwproducten.products import peilopzet_grid as p3

WC_MODES = {
    "lijn": "lijn klikken", "cellen": "cellen selecteren", "wissen": "cellen weghalen",
    "aanwijzen": "aanwijzen en aanpassen",
}  # fmt: skip
PICK_FIELDS = ["kind", "stage_ref", "stage_new", "bottom", "width", "entry_resistance"]
WALL_MODES = {"lijn": "lijn langs celranden", "rondom": "rondom selectie"}
WALL_RESISTANCE = 100.0  # [d] starting value when a wall is made leaky
GROUP_NAMES = {"put": "Putten", "ghb": "GHB", "chd": "CHD"}  # default names of new groups
WC = lbl.WATERCOURSE


def _num(container, field: lbl.Field, key: str, value: float, step: float, **kw) -> float:
    """Number input with the label, help and display format of ``field``."""
    fmt = "%.2f" if field.fmt in (None, lbl.LEVEL) else field.fmt
    return container.number_input(
        field.label, value=value, step=step, key=key, help=field.help, format=fmt, **kw
    )


def props_form(prefix: str, cell: float, surface: float) -> dict[str, Any]:
    """Properties of a new watercourse; levels default relative to the surface."""
    props: dict[str, Any] = {
        "name": st.text_input(
            "Naam", f"Watergang {len(ss[f'{KEY}_wcs']) + 1}", key=f"{prefix}_name"
        ),
        "kind": st.selectbox(
            WC["kind"].label, p3.KINDS, format_func=lbl.KIND.get, key=f"{prefix}_kind",
            help=WC["kind"].help,
        ),
    }  # fmt: skip
    c1, c2 = st.columns(2)
    props["stage_ref"] = _num(c1, WC["stage_ref"], f"{prefix}_sr", surface - 1.2, 0.05)
    props["stage_new"] = _num(c2, WC["stage_new"], f"{prefix}_sn", surface - 1.2, 0.05)
    props["bottom"] = _num(c1, WC["bottom"], f"{prefix}_b", surface - 1.6, 0.1)
    props["width"] = _num(c2, WC["width"], f"{prefix}_w", 1.0, 0.5, min_value=0.1)
    props["entry_resistance"] = _num(
        c1, WC["entry_resistance"], f"{prefix}_c", 1.0, 0.5, min_value=0.0
    )
    props["length"] = _num(c2, WC["length"], f"{prefix}_l", cell, 5.0, min_value=0.1)
    props["conductance"] = _num(st, WC["conductance"], f"{prefix}_cond", 0.0, 10.0, min_value=0.0)
    return props


def group_form(action: str, labels: dict[str, str], surface: float) -> dict[str, Any]:
    """Properties of a new well group (action "put"), GHB group ("ghb") or fixed-head group
    ("chd")."""
    fields = lbl.GROUPS[GROUPS[action]]
    n = len(ss[f"{KEY}_{GROUPS[action]}"]) + 1
    k = f"{KEY}_{action}"
    props: dict[str, Any] = {
        "name": st.text_input("Naam", f"{GROUP_NAMES[action]} {n}", key=f"{k}_name"),
        "layer": st.selectbox(
            "Laag", list(labels), format_func=labels.get, key=f"{k}_layer",
            index=len(labels) - 1 if action == "put" and labels else 0,
            help=fields["layer"].help,
        ),
    }  # fmt: skip
    c1, c2 = st.columns(2)
    if action == "put":
        props["q_ref"] = _num(c1, fields["q_ref"], f"{k}_qr", 0.0, 10.0)
        props["q_new"] = _num(c2, fields["q_new"], f"{k}_qn", 100.0, 10.0)
        props["radius"] = _num(
            st, fields["radius"], f"{k}_rw", DEFAULT_RADIUS, 0.05, min_value=0.01,
            max_value=2.0,
        )  # fmt: skip
    else:
        props["head_ref"] = _num(c1, fields["head_ref"], f"{k}_hr", surface - 1.2, 0.05)
        props["head_new"] = _num(c2, fields["head_new"], f"{k}_hn", surface - 1.2, 0.05)
        if action == "ghb":
            props["resistance"] = _num(st, fields["resistance"], f"{k}_c", 1.0, 0.5, min_value=0.01)
        else:
            st.caption(
                "Vaste stijghoogte (CHD): de cellen houden deze stijghoogte; het debiet volgt "
                "uit de berekening en staat in de waterbalans. Andere objecten in deze cellen "
                "(zelfde laag) werken niet."
            )
    return props


def wall_form(labels: dict[str, str]) -> dict[str, Any]:
    """Properties of a new wall: name, deepest layer, impermeable or c_w, reference."""
    k = f"{KEY}_nwall"
    props: dict[str, Any] = {
        "name": st.text_input("Naam", f"Damwand {len(ss[f'{KEY}_walls']) + 1}", key=f"{k}_name"),
        "layer": st.selectbox(
            lbl.WALL["layer"].label, list(labels), format_func=labels.get, key=f"{k}_layer",
            index=min(1, len(labels) - 1), help=lbl.WALL["layer"].help,
        ),
    }  # fmt: skip
    c1, c2 = st.columns(2)
    props["impermeable"] = c1.checkbox("Ondoorlatend", value=True, key=f"{k}_imp")
    props["resistance"] = c2.number_input(
        lbl.WALL["resistance"].label, value=WALL_RESISTANCE, min_value=p3.WALL_RESISTANCE[0],
        max_value=p3.WALL_RESISTANCE[1], format="%g", key=f"{k}_c",
        disabled=props["impermeable"], help=lbl.WALL["resistance"].help,
    )  # fmt: skip
    props["reference"] = st.checkbox(
        "Ook in de referentie (bestaande wand)", value=False, key=f"{k}_ref",
        help="Uit: de wand hoort bij de ingreep en staat alleen in de opzet.",
    )  # fmt: skip
    return props


def _col(field: str, f: lbl.Field, kind: str = "number", **kw) -> Col:
    return Col(field, f.label, kind, fmt=f.fmt, help=f.help, **kw)


def wall_table(labels: dict[str, str], grid: Grid) -> None:
    """Editable table of the walls."""
    walls = ss.get(f"{KEY}_walls", [])
    if not walls:
        return
    faces = [len(v["faces"]) for v in grid.walls()]
    W = lbl.WALL
    st.markdown("**Damwanden** (direct te bewerken)")
    object_table(
        walls,
        [
            _col("name", W["name"], "text"),
            _col("layer", W["layer"], "layer"),
            _col("impermeable", W["impermeable"], "bool", default=True),
            _col("resistance", W["resistance"], default=WALL_RESISTANCE),
            _col("reference", W["reference"], "bool", default=False),
            Col("faces", "Celranden", readonly=True, value=lambda k, w: faces[k]),
            Col("length", "Lengte [m]", readonly=True, fmt="%.0f",
                value=lambda k, w: faces[k] * grid.cell),
        ],
        f"{KEY}_walls_table_{ss[f'{KEY}_ver']}", state_key=f"{KEY}_walls", labels=labels,
    )  # fmt: skip
    st.caption(
        "Een damwand ligt op celranden en loopt vanaf de bovenkant tot en met de gekozen laag. "
        "Scheidende lagen hebben in het model geen horizontale stroming. Zie Theorie."
    )


def group_tables(labels: dict[str, str]) -> None:
    """Editable tables of the well, GHB and CHD groups."""
    for kind, title in lbl.GROUP_TITLES.items():
        items = ss[f"{KEY}_{kind}"]
        if not items:
            continue
        st.markdown(f"**{title}** (direct te bewerken)")
        cols = [
            _col(f, field, "text" if f == "name" else "layer" if f == "layer" else "number",
                 default=DEFAULT_RADIUS if f == "radius" else None)
            for f, field in lbl.GROUPS[kind].items()
        ]  # fmt: skip
        cols.append(Col("points", "Cellen", readonly=True, value=lambda k, g: len(g["points"])))
        object_table(
            items, cols, f"{KEY}_{kind}_table_{ss[f'{KEY}_ver']}", state_key=f"{KEY}_{kind}",
            labels=labels,
        )  # fmt: skip


def watercourse_tables(grid: Grid) -> None:
    """Editable properties of the watercourses and their length per cell."""
    cell = grid.cell
    wcs_view = grid.watercourses()
    if not ss[f"{KEY}_wcs"]:
        return
    st.markdown("**Eigenschappen watergangen** (direct te bewerken)")
    cols = [
        Col("icon", "Lijn", readonly=True, image=True, value=lambda k, wc: wc_icon(k),
            help="Kleur en lijnsoort op de tekenkaart"),
        *[
            _col(f, field, "text" if f == "name" else "select" if f == "kind" else "number",
                 choices=lbl.KIND if f == "kind" else None)
            for f, field in WC.items()
        ],
        Col("cells", "Cellen", readonly=True, value=lambda k, wc: len(wcs_view[k]["cells"])),
        Col("total", "Totale lengte [m]", readonly=True, fmt="%.0f",
            value=lambda k, wc: round(sum(wcs_view[k]["lengths"]), 1)),
    ]  # fmt: skip
    object_table(ss[f"{KEY}_wcs"], cols, f"{KEY}_table_{ss[f'{KEY}_ver']}", state_key=f"{KEY}_wcs")

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


def pick_panel(wcs_view: list[dict[str, Any]]) -> None:
    """Edit or remove the watercourse picked on the map (it is highlighted there)."""
    names = [wc["name"] for wc in ss[f"{KEY}_wcs"]]
    hits = [n for n in ss.get(f"{KEY}_wc_hits", []) if n in names]
    if not hits:
        st.caption(
            "Klik op een cel van een watergang (of trek een rechthoek): die licht rood op en "
            "je past hem hier aan. De naam zie je ook als je over de kaart beweegt."
        )
        return
    if ss.get(f"{KEY}_wc_pick") not in hits:
        ss[f"{KEY}_wc_pick"] = hits[0]
    name = st.selectbox(
        f"Aangewezen watergang ({len(hits)} in de selectie)" if len(hits) > 1
        else "Aangewezen watergang",
        hits, key=f"{KEY}_wc_pick",
    )  # fmt: skip
    wc = ss[f"{KEY}_wcs"][names.index(name)]
    view = wcs_view[names.index(name)]
    st.caption(
        f"{len(view['cells'])} cellen, {sum(view['lengths']):.0f} m"
        + (
            "; bodem per stuk uit de legger (hier het gemiddelde; aanpassen schuift alles mee)"
            if view.get("bottom_offsets")
            else ""
        )
    )
    ver = ss[f"{KEY}_ver"]
    c1, c2 = st.columns(2)
    new: dict[str, Any] = {
        "kind": c1.selectbox(
            WC["kind"].label, p3.KINDS, index=list(p3.KINDS).index(wc["kind"]),
            format_func=lbl.KIND.get, key=f"{KEY}_pk_kind_{ver}", help=WC["kind"].help,
        ),
    }  # fmt: skip
    for k, f in enumerate(PICK_FIELDS[1:]):
        col = (c2, c1)[k % 2]
        new[f] = col.number_input(
            WC[f].label, value=float(wc[f]), step=0.05 if f != "entry_resistance" else 0.5,
            format="%.2f", key=f"{KEY}_pk_{f}_{ver}", help=WC[f].help,
        )  # fmt: skip
    b1, b2 = st.columns(2)
    if b1.button("Opslaan", type="primary", key=f"{KEY}_pk_save"):
        wc.update(new)
        bump()
        st.rerun()
    if b2.button("Verwijderen", key=f"{KEY}_pk_del", icon=":material/delete:"):
        ss[f"{KEY}_wcs"] = [w for w in ss[f"{KEY}_wcs"] if w["name"] != name]
        ss[f"{KEY}_wc_hits"] = [n for n in hits if n != name]
        ss.pop(f"{KEY}_wc_pick", None)
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
        "de lengte per cel volgt uit de lijn) of met rechthoek/lasso als volle cellen. Een "
        "damwand klik je als lijn langs de celranden, of zet je rondom een selectie (bouwput). "
        "Andere objecten: selecteer cellen met rechthoek of lasso (werkbalk rechtsboven) en "
        "klik *Toepassen*. Elke watergang heeft een eigen kleur op de lijn; de grijze cellen "
        "eronder zijn de rekencellen: hoe donkerder, hoe meer lengte van de watergang in de "
        "cel (een lichte cel raakt de lijn maar net en telt naar verhouding weinig mee)."
    )
    draw_panel(grid, values, node_labels, zone_values)
    import_inputs(grid, values)
    import_notice()
    group_tables(node_labels)
    wall_table(node_labels, grid)
    watercourse_tables(grid)
    return ss[f"{KEY}_fig_draw"]


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
        show_cells = st.checkbox(
            "Rekencellen van de watergangen tonen", value=False, key=f"{KEY}_show_cells",
            help="Grijs onder de getekende lijnen: de cellen waarmee het model rekent; hoe "
            "donkerder, hoe meer lengte van de watergang in de cel.",
        )  # fmt: skip
    with col_ctl:
        obj = st.radio(
            "Wat teken je?", list(lbl.OBJECTS), format_func=lbl.OBJECTS.get, horizontal=True,
            key=f"{KEY}_obj",
        )  # fmt: skip
        if obj == "watergang":
            how = st.radio(
                "Hoe?", list(WC_MODES), format_func=WC_MODES.get, horizontal=True,
                key=f"{KEY}_wc_how",
            )  # fmt: skip
        elif obj == "damwand":
            how = st.radio(
                "Hoe?", list(WALL_MODES), format_func=WALL_MODES.get, horizontal=True,
                key=f"{KEY}_wall_how",
            )  # fmt: skip
        else:
            how = st.radio(
                "Hoe?", ["toevoegen", "verwijderen"], horizontal=True, key=f"{KEY}_how",
                format_func={"toevoegen": "cellen toevoegen", "verwijderen": "cellen weghalen"}.get,
            )  # fmt: skip
    line_mode = obj in ("watergang", "damwand") and how == "lijn"
    point_mode = obj == "watergang" and how == "aanwijzen"
    click_mode = obj == "put" and how == "toevoegen"  # one click places a well
    snap = None
    if line_mode:  # a wall follows the grid lines: click the cell corners
        snap = cell if obj == "damwand" else line_snap(cell, values["size_x"], values["size_y"])
    wcs_view = grid.watercourses()
    hits = ss.get(f"{KEY}_wc_hits", []) if point_mode else []
    pick = ss.get(f"{KEY}_wc_pick") if ss.get(f"{KEY}_wc_pick") in hits else next(iter(hits), None)

    with col_map:
        fig_draw = drawing_figure(
            nrow, ncol, cell, wcs_view, grid.drain_cells(),
            current_line=ss[f"{KEY}_line"], snap=snap, wells=grid.groups("wells"),
            ghbs=grid.groups("ghb"), chds=grid.groups("chd"), zones=grid.zones(zone_values),
            outlines=area_outlines(grid, values), walls=grid.walls(), highlight=pick,
            show_cells=show_cells,
        )  # fmt: skip
        width, height = ncol * cell, nrow * cell
        bbox = model_bbox(values["origin_x"], values["origin_y"], width, height)
        add_background(fig_draw, background(bbox), width, height)
        ss[f"{KEY}_fig_draw"] = fig_draw
        # A stable key keeps zoom and pan between reruns; each selection is used only once
        # (``fresh``) instead of clearing it with a new key.
        mode = "lijn" if line_mode else "wijs" if point_mode else "klik" if click_mode else "cel"
        draw_key = f"{KEY}_draw_{mode}"
        event = st.plotly_chart(
            fig_draw, key=draw_key, on_select="rerun", config=MAP_CONFIG, width="stretch",
            selection_mode=(
                ("points",) if line_mode
                else ("points", "box", "lasso") if point_mode or click_mode
                else ("box", "lasso")
            ),
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
        rerun_fragment()  # the line in progress is not part of the model yet
    if point_mode and fresh:
        ss[f"{KEY}_used_sel"] = selection_id
        cells = set(to_cells(set(picked), cell, nrow, ncol))
        hits = [v["name"] for v in wcs_view if cells & set(v["cells"])]
        ss[f"{KEY}_wc_hits"] = hits
        ss[f"{KEY}_wc_pick"] = hits[0] if hits else None
        rerun_fragment()  # show the highlight

    with col_ctl:
        wcs = ss[f"{KEY}_wcs"]
        zones = ss[f"{KEY}_zones"]
        selected = set(picked) if fresh and not line_mode else set()
        target = None
        props: dict[str, Any] = {}
        if point_mode:
            pick_panel(wcs_view)
        elif obj == "watergang" and how != "wissen":
            target = st.selectbox(
                "Watergang", [NEW, *[wc["name"] for wc in wcs]], key=f"{KEY}_wc_target",
                format_func=lambda n: "+ nieuwe watergang" if n == NEW else n,
            )  # fmt: skip
            if target == NEW:
                props = props_form(f"{KEY}_np", cell, values["surface"])
        elif obj == "damwand":
            target = st.selectbox(
                "Damwand", [NEW, *[w["name"] for w in ss[f"{KEY}_walls"]]],
                key=f"{KEY}_wall_target",
                format_func=lambda n: "+ nieuwe damwand" if n == NEW else n,
            )  # fmt: skip
            if target == NEW:
                props = wall_form(node_labels)
        elif click_mode:
            names = [g["name"] for g in ss[f"{KEY}_wells"]]
            pending = ss.pop(f"{KEY}_put_target_next", None)  # the group made by the last click
            if pending in names:
                ss[f"{KEY}_put_target"] = pending
            target = st.selectbox(
                "Putgroep", [NEW, *names], key=f"{KEY}_put_target",
                format_func=lambda n: "+ nieuwe putgroep" if n == NEW else n,
                help="Een klik op de kaart zet een put in deze groep; de debieten gelden per put.",
            )  # fmt: skip
            if target == NEW:
                props = group_form(obj, node_labels, values["surface"])
        elif obj in GROUPS and how == "toevoegen":
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
                rerun_fragment()
            if b2.button("Lijn annuleren", disabled=not line, key=f"{KEY}_cancel"):
                ss[f"{KEY}_line"] = []
                rerun_fragment()
            if st.button(
                "Lijn afronden", type="primary", disabled=len(line) < 2, key=f"{KEY}_finish"
            ):
                if obj == "damwand":
                    walls = ss[f"{KEY}_walls"]
                    if target == NEW:
                        walls.append({**props, "lines": [list(line)]})
                    else:
                        next(w for w in walls if w["name"] == target)["lines"].append(list(line))
                elif target == NEW:
                    wcs.append({**props, "lines": [list(line)], "points": set(), "manual": {},
                                "manual_cell": cell})  # fmt: skip
                else:
                    next(wc for wc in wcs if wc["name"] == target)["lines"].append(list(line))
                ss[f"{KEY}_line"] = []
                bump()
                st.rerun()
        elif click_mode:
            st.caption(
                "Klik op een cel om er een put te zetten; meerdere tegelijk met rechthoek of "
                "lasso (werkbalk rechtsboven)."
            )
            if selected:  # applied at once, no "Toepassen"
                apply_selection(ss, KEY, (cell, nrow, ncol), obj, how, target, props, selected)
                ss[f"{KEY}_used_sel"] = selection_id
                ss[f"{KEY}_put_target_next"] = props["name"] if target == NEW else target
                bump()
                st.rerun()
        elif not point_mode:
            st.markdown(f"**Selectie: {len(selected)} cellen**")
            if st.button("Toepassen", type="primary", disabled=not selected, key=f"{KEY}_apply"):
                apply_selection(ss, KEY, (cell, nrow, ncol), obj, how, target, props, selected)
                ss[f"{KEY}_used_sel"] = selection_id
                bump()
                st.rerun()
        st.divider()
        b0, b1, b2 = st.columns(3)
        if b0.button(
            "Ongedaan maken", key=f"{KEY}_undo_draw", icon=":material/undo:",
            disabled=not can_undo(),
            help="Zet de tekening terug van vóór de laatste wijziging (tekenen, importeren, "
            "verwijderen; tot 10 stappen). Na pakket splitsen of een project laden begint het "
            "opnieuw.",
        ):  # fmt: skip
            undo()
            st.rerun()
        if b1.button("Voorbeeld laden", key=f"{KEY}_ex"):
            reset(example())
            st.rerun()
        if b2.button("Alles wissen", key=f"{KEY}_clear"):
            reset([])
            st.rerun()
