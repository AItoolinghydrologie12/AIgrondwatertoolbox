"""Step 3 of the Grondwatertoolbox page: key figures, result map, cross-section, theory,
validation and export."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from engine_ui import engine_badge, model_zip
from grid_ui import DRAWDOWN_COLOR, THRESHOLD_COLOR, WC_STYLES, add_background, result_figure
from location_ui import background, background_caption, model_bbox
from profile_ui import (
    DRAWDOWN_SHOWN,
    crossings,
    profile_figure,
    profile_table,
    wall_crossings,
)
from toolbox_state import KEY, MAP_CONFIG, rerun_fragment, ss
from ui_common import TABS, csv_button, dict_table, png_buttons, split_theory

from gwproducten.products import peilopzet_grid as p3
from gwproducten.products.peilopzet_grid.gis import to_gpkg

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


def limit_lines(threshold: float, view: str) -> list[tuple[float, str, str]]:
    """The limit lines of a map view: rise and drawdown on the 'verhoging' map (negative =
    drawdown), drawdown on the wells map (positive = drawdown)."""
    cm = f"{threshold * 100:.0f} cm"
    if view == "verhoging":
        return [
            (threshold, THRESHOLD_COLOR, f"{cm} verhoging"),
            (-threshold, DRAWDOWN_COLOR, f"{cm} verlaging"),
        ]
    if view == "putten":
        return [(threshold, DRAWDOWN_COLOR, f"{cm} verlaging")]
    return []


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
    drawdown = -float(np.nanmin(rise0))
    if drawdown >= DRAWDOWN_SHOWN:  # e.g. a dewatering: the same figures for the drawdown
        area_dd = float((rise0 <= -params.threshold).sum()) * params.cell**2 / 1e4
        row3 = st.columns(3)
        row3[0].metric(
            "Max. verlaging freatisch", f"{drawdown:.2f} m", border=True,
            help="Grootste daling van de grondwaterstand (referentie − opzet) in het model.",
        )  # fmt: skip
        row3[1].metric(
            f"Oppervlak verlaging ≥ {params.threshold * 100:.0f} cm", f"{area_dd:.1f} ha",
            border=True, help="Som van de cellen waar de freatische verlaging de grens haalt.",
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
        rerun_fragment()
    if c3.button("Profiel wissen", disabled=not pts, key=f"{KEY}_pclear"):
        ss[f"{KEY}_prof"] = []
        ss[f"{KEY}_pver"] = ss.get(f"{KEY}_pver", 0) + 1
        rerun_fragment()
    return list(pts)


BUDGET_COLORS = {"referentie": "#86b6ef", "opzet": "#256abf"}  # one blue, light -> dark
BUDGET_MIN = 1e-3  # m³/d; smaller posts (e.g. no surface runoff) are left out of the figure


def budget_figure(budget: pd.DataFrame) -> go.Figure:
    """Water balance per post as horizontal bars around zero, reference next to opzet.
    Positive = into the groundwater."""
    table = budget.drop(index=[i for i in budget.index if str(i).startswith("som")])
    table = table[(table.abs() > BUDGET_MIN).any(axis=1)].iloc[::-1]  # first post on top
    fig = go.Figure()
    for col, (name, colour) in zip(table.columns, BUDGET_COLORS.items(), strict=True):
        fig.add_trace(
            go.Bar(
                y=[str(i) for i in table.index], x=table[col], orientation="h", name=name,
                marker={"color": colour}, hovertemplate=f"%{{y}} ({name}): %{{x:.1f}} m³/d"
                "<extra></extra>",
            )
        )  # fmt: skip
    fig.add_vline(x=0.0, line={"color": "#898781", "width": 1})
    fig.update_layout(
        barmode="group", bargap=0.35, bargroupgap=0.08, barcornerradius=4,
        height=90 + 48 * len(table), margin={"t": 10, "b": 40, "l": 10, "r": 10},
        xaxis={"title": "← uit het grondwater · in het grondwater → [m³/d]",
               "zeroline": False, "gridcolor": "#e1e0d9"},
        legend={"orientation": "h", "y": 1.02, "yanchor": "bottom"},
        plot_bgcolor="rgba(0,0,0,0)",
    )  # fmt: skip
    return fig


def show_result(
    params: p3.Params, res: p3.Result, wcs_done: list[dict[str, Any]]
) -> tuple[go.Figure, go.Figure | None, pd.DataFrame | None]:
    labels = res.node_labels
    keys = list(labels)
    c5, c6, c7 = st.columns([3, 2, 2])
    views = [v for v in VIEWS if v != "putten" or res.well_drawdown is not None]
    if ss.get(f"{KEY}_view") not in views:
        ss[f"{KEY}_view"] = views[0]
    view = c5.selectbox("Kaart", views, format_func=VIEWS.get, key=f"{KEY}_view")
    layer = c6.selectbox("Laag", keys, format_func=labels.get, key=f"{KEY}_layer")
    wc_style = c7.selectbox(
        "Watergangen", list(WC_STYLES), format_func=WC_STYLES.get, key=f"{KEY}_wcstyle",
        help="Dunne lijnen houden de contouren leesbaar bij veel watergangen; 'cellen' toont "
        "per watergang de cellen in kleur (ook GHB en CHD); 'uit' laat ze weg.",
    )  # fmt: skip
    j = keys.index(layer)
    if view == "verhoging":
        z, label, rev = res.rise[j], "verhoging [m]", False
    elif view == "opzet":
        z, label, rev = res.heads_new[j], "h [m NAP]", False
    elif view == "ref":
        z, label, rev = res.heads_ref[j], "h [m NAP]", False
    elif view == "putten":
        z, label, rev = res.well_drawdown[j], "verlaging [m]", False
    else:
        z, label, rev = res.surface - res.heads_new[0], "diepte gws [m-mv]", True
    map_box = st.container()
    pts = profile_points(params)
    clicking = ss.get(f"{KEY}_pmode") == "lijn"
    snap = max(params.cell / 2.0, max(params.size_x, params.size_y) / 100.0)
    outlined = wcs_done + [{"name": g.name, "cells": g.cells} for g in (*params.ghb, *params.chd)]
    fig_res = result_figure(
        res.x, res.y, z, params.cell, outlined, label=label, reverse=rev,
        limits=limit_lines(params.threshold, view),
        profile=pts, click_snap=snap if clicking else None,
        wells=[{"name": g.name, "cells": g.cells} for g in params.wells],
        walls=[{"name": w.name, "faces": w.faces} for w in params.walls], wc_style=wc_style,
    )  # fmt: skip
    bg = background(model_bbox(params.origin_x, params.origin_y, params.size_x, params.size_y))
    if bg is not None:
        fig_res.update_traces(opacity=0.7, selector={"type": "contour"})  # map shows through
        add_background(fig_res, bg, params.size_x, params.size_y)
    with map_box:
        event = st.plotly_chart(
            fig_res, width="stretch", key=f"{KEY}_resmap_{clicking}", config=MAP_CONFIG,
            on_select="rerun" if clicking else "ignore", selection_mode=("points",),
        )  # fmt: skip
        background_caption()
        cm = f"{params.threshold * 100:.0f} cm"
        if view == "verhoging":
            st.caption(
                f"Dikke rode lijn: {cm} verhoging; dikke blauwe lijn: {cm} verlaging (negatieve "
                "verhoging, bijv. bij een bemaling). Witte stippellijn: dwarsprofiel A–B."
            )
        elif view == "putten":
            st.caption(f"Dikke blauwe lijn: {cm} verlaging. Witte stippellijn: dwarsprofiel A–B.")
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
                rerun_fragment()

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
        walls_crossed = wall_crossings(
            prof["afstand [m]"].to_numpy(), prof["x [m]"].to_numpy(), prof["y [m]"].to_numpy(),
            params.cell, list(params.walls),
        )  # fmt: skip
        fig_prof = profile_figure(prof, labels, crossed, params.threshold, walls_crossed)
        st.plotly_chart(fig_prof, width="stretch")
        st.caption(
            "Stippel = referentie, doorgetrokken = na opzet; rood vlak = verhoging van de "
            "bovenste laag (rechteras). Driehoekjes: peil van gekruiste watergangen "
            "(open = referentie, dicht = opzet). Zwarte lijn: damwand (de stijghoogte "
            "verspringt daar; de lijnen tussen celmiddens verbinden de twee kanten)."
        )
    with st.expander("Waterbalans en conductances", expanded=False):
        st.markdown("**Waterbalans [m³/d]**")
        st.plotly_chart(budget_figure(res.budget), width="stretch")
        st.dataframe(res.budget.round(2))
        st.caption(
            "Positief = water het grondwater in. 'rand' = in/uitstroom over de vaste modelrand."
        )
        st.markdown("**Conductance per cel** (gemiddeld over de cellen van de watergang)")
        st.dataframe(res.conductances.round(3), hide_index=True)
    if res.walls is not None:
        with st.expander("Damwanden: doorstroming", expanded=False):
            st.dataframe(res.walls.round(2), hide_index=True)
            st.caption(
                "Som van |q| over alle celranden van de wand, per doorsneden laag [m³/d]: hoeveel "
                "water er nog door de wand gaat (0 bij een ondoorlatende wand). Geen post in de "
                "waterbalans: het blijft binnen het model."
            )
    if res.wells is not None:
        with st.expander("Putten: verlaging in de put", expanded=view == "putten"):
            st.dataframe(res.wells.round(2), hide_index=True)
            st.caption(
                "De kaart toont het celgemiddelde. De stijghoogte in de put volgt uit de "
                "Peaceman-correctie −Q/(2πT)·ln(0,198·Δx/r_w), met de putstraal r_w en T van "
                "de cel; 'Droog': de cel valt droog, dan is de putstijghoogte niet bepaald."
            )
    return fig_res, fig_prof, prof


@st.fragment
def result_panel(
    params: p3.Params, res: p3.Result, wcs_done: list[dict[str, Any]], out: dict[str, Any]
) -> None:
    """The result map, profile and tables as a fragment: choosing another map, layer or
    profile reruns only this panel. ``out`` receives the figures and the profile table for
    the export (a plain dict, so a deferred download reads the latest profile)."""
    out["fig_res"], out["fig_prof"], out["prof"] = show_result(params, res, wcs_done)


def show_validation(params: p3.Params, res: p3.Result) -> None:
    st.markdown(
        "**Controles bij de berekende invoer.** Automatische tests: "
        "`tests/test_peilopzet_grid.py` en `tests/test_polyline.py` (parabool tussen twee "
        "sloten, maaivelddrainage, RIV-begrenzing, drain, buisdrainage per cel, lengte per "
        "cel, lijn → cellen, peilopzet vs 1D-analytisch met en zonder celgrootte-correctie, "
        "gridverfijning, massabalans) en `tests/test_wall.py` (damwand: strook tegen de "
        "serieweerstand, ingesloten bouwput, lekkende wand, gesplitst pakket, gridverfijning)."
    )
    st.markdown(
        "**Rekenkern MODFLOW 6** (USGS; Langevin e.a., 2017). Elke laag is een MF6-modellaag "
        "(scheidende lagen met K33 = D/c). Eerst de standaardformulering; bij droogvallende "
        "cellen Newton; levert een put meer dan de laag kan geven, dan Newton met "
        "AUTO_FLOW_REDUCE (de put levert minder, met melding). `tests/test_mf6_reference.py` "
        "vergelijkt tien testgevallen (putten, kwel, zones, alle randtypen, damwanden, "
        "meebewegende T) met vaste referentie-uitkomsten: stijghoogten binnen 10⁻⁵ m. "
        "Gebruikte formulering nu: " + res.engine_note + "."
    )
    st.dataframe(dict_table(res.mass_balance, "Massabalansfout [-]", "{:.1e}"), hide_index=True)
    st.dataframe(
        dict_table(
            {f"Iteraties MODFLOW 6 {k}": v for k, v in res.iterations.items()}, fmt="{:.0f}"
        ),  # fmt: skip
        hide_index=True,
    )
    st.markdown("**Benchmark: lange evenwijdige sloten tegen de analytische peilopzet**")
    st.caption(
        "Zelfde lagenopbouw als de berekening; opgezette sloot met buren op 100 m, regionale "
        "drainage w = 100 d. Infiltratie van de opgezette sloot per meter, numeriek vs analytisch."
    )
    if st.button("Benchmark draaien", key=f"{KEY}_bench"):
        rows = []
        for cs in (50.0, 25.0, 10.0):  # the allowed cell sizes (10-50 m)
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


def grid_table(res: p3.Result) -> pd.DataFrame:
    """Every cell with its zone, surface and per layer the heads and the rise."""
    labels = res.node_labels
    gx, gy = np.meshgrid(res.x, res.y)
    grid = pd.DataFrame({"x [m]": gx.ravel(), "y [m]": gy.ravel()})
    grid["zone"] = np.array(res.zone_names)[res.zone_map.ravel()]
    grid["maaiveld [m NAP]"] = res.surface.ravel()
    for j, k in enumerate(labels):
        grid[f"h ref {labels[k]} [m NAP]"] = res.heads_ref[j].ravel()
        grid[f"h opzet {labels[k]} [m NAP]"] = res.heads_new[j].ravel()
        grid[f"verhoging {labels[k]} [m]"] = res.rise[j].ravel()
    return grid


def show_export(
    params: p3.Params,
    res: p3.Result,
    wcs_done: list[dict[str, Any]],
    figures: dict[str, go.Figure],
    out: dict[str, Any],
) -> None:
    csv_button(lambda: grid_table(res), "peilopzet2d_rooster.csv", "Rooster (CSV)")
    csv_button(
        res.budget.reset_index(names="post"), "peilopzet2d_waterbalans.csv", "Waterbalans (CSV)"
    )
    if res.wells is not None:
        csv_button(res.wells, "peilopzet2d_putten.csv", "Putten (CSV)")
    if res.walls is not None:
        csv_button(res.walls, "peilopzet2d_damwanden.csv", "Damwanden: doorstroming (CSV)")
        faces = pd.DataFrame(
            [
                {"damwand": w.name, "rij": r, "kolom": c,
                 "rand": "oost van de cel" if axis == "x" else "noord van de cel"}
                for w in params.walls
                for r, c, axis in w.faces
            ]
        )  # fmt: skip
        csv_button(faces, "peilopzet2d_damwand_celranden.csv", "Damwanden: celranden (CSV)")
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
                    for kind, grp in (
                        ("put", params.wells),
                        ("GHB", params.ghb),
                        ("CHD", params.chd),
                    )
                    for g in grp
                    for r, c in g.cells
                ],
                columns=["watergang", "rij", "kolom"],
            ),
        ],
        ignore_index=True,
    )
    csv_button(cells, "peilopzet2d_cellen.csv", "Getekende cellen (CSV)")
    if out.get("prof") is not None:
        csv_button(lambda: out["prof"].round(4), "peilopzet2d_profiel.csv", "Dwarsprofiel (CSV)")
    png_buttons(figures, KEY)
    st.markdown("**GIS (QGIS)**")
    located = not (params.origin_x == 0.0 and params.origin_y == 0.0)
    wc_lines = {wc["name"]: wc.get("lines", []) for wc in wcs_done}
    st.download_button(
        "GIS-bestand (GeoPackage)", lambda: to_gpkg(params, res, wc_lines),
        file_name="grondwatertoolbox.gpkg", mime="application/geopackage+sqlite3",
        icon=":material/map:", key=f"{KEY}_gpkg", disabled=not located, on_click="ignore",
    )  # fmt: skip
    st.caption(
        "Eén bestand voor QGIS (slepen in het kaartvenster), in RD (EPSG:28992), met de lagen "
        f"contouren_verhoging (elke {params.threshold * 100:.0f} cm per rekenlaag; negatief = "
        "verlaging; grens = 1 voor de beoordelingsgrens), modelgebied, watergangen (lijnen en "
        "rekencellen), putten, ghb_chd en damwanden."
        if located
        else "Zet bij stap 1 een locatie aan: zonder RD-coördinaten komt het bestand op de "
        "verkeerde plek in QGIS."
    )
    st.markdown("**MODFLOW 6-model**")
    st.caption(
        "Dezelfde berekening als invoerbestanden voor MODFLOW 6 (referentie en opzet, met een "
        "LEESMIJ), om zelf te draaien of na te lopen. Bevat alleen modelinvoer."
    )
    if st.button("MODFLOW 6-model maken", key=f"{KEY}_mf6zip"):
        ss[f"{KEY}_mf6zip_for"] = params
    if ss.get(f"{KEY}_mf6zip_for") == params:
        st.download_button(
            "MODFLOW 6-model (zip)", model_zip(params), file_name="grondwatertoolbox_mf6.zip",
            mime="application/zip", icon=":material/download:", key=f"{KEY}_mf6zip_dl",
        )  # fmt: skip


theory, assumptions = split_theory(p3.THEORY)
NO_RESULT = "Nog geen resultaat: klik op **Bereken**."


def result_tabs(done: dict[str, Any] | None, fig_draw: go.Figure) -> None:
    """Key figures and the tabs Resultaat · Theorie · Aannames · Validatie · Export."""
    if done is not None:
        engine_badge(done["res"])
        show_kpis(done["params"], done["res"], done["res"].seconds)
    t_res, t_theory, t_ass, t_val, t_exp = st.tabs(TABS)
    with t_theory:
        st.markdown(theory)
    with t_ass:
        st.markdown(assumptions)
    if done is None:
        for tab in (t_res, t_val, t_exp):
            tab.info(NO_RESULT)
        return
    out = ss.setdefault(f"{KEY}_out", {})
    with t_res:
        result_panel(done["params"], done["res"], done["wcs"], out)
    with t_val:
        show_validation(done["params"], done["res"])
    with t_exp:
        figs = {"peilopzet2d_kaart": out["fig_res"], "peilopzet2d_tekening": fig_draw}
        if out["fig_prof"] is not None:
            figs["peilopzet2d_profiel"] = out["fig_prof"]
        show_export(done["params"], done["res"], done["wcs"], figs, out)
