"""Rekenkern: eigen solver naast MODFLOW 6 — uitleg en vergelijking van het huidige model."""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import streamlit as st
from engine_ui import mf6_status, run
from grid_ui import result_figure

from gwproducten.products import peilopzet_grid as p3

KEY = "p3"  # the model of the Grondwatertoolbox page
MAP_CONFIG = {"scrollZoom": True, "displaylogo": False}

st.set_page_config(page_title="Rekenkern MODFLOW 6", layout="wide")
st.title("Rekenkern: eigen solver en MODFLOW 6")
st.markdown(
    "De Grondwatertoolbox heeft **twee rekenkernen** die dezelfde vergelijkingen oplossen: een "
    "**eigen solver** in Python (standaard, snel) en **MODFLOW 6** van de USGS. De keuze staat "
    "in de zijbalk van de pagina *Grondwatertoolbox* onder *Beoordeling en numeriek*. Op deze "
    "pagina lees je wat gelijk is en wat verschilt, en kun je je eigen model met beide "
    "doorrekenen."
)

uitleg, vergelijk = st.tabs(["Uitleg verschillen", "Vergelijk je model"])

# ---- deel A: uitleg -------------------------------------------------------------------
SAME = pd.DataFrame(
    [
        ("Discretisatie", "eindige volumes, celcentra, vierkante cellen (MF6: DIS)"),
        ("Horizontale stroming", "T tussen cellen als harmonisch gemiddelde (MF6-standaard)"),
        ("Verticale stroming", "weerstand ½D₁/kv₁ + c + ½D₂/kv₂ tussen de pakketten"),
        ("Watergangen", "RIV: q = C(peil − h), begrensd op de bodem; drain alleen afvoer"),
        ("Drainage, maaiveld", "DRN op drainniveau resp. maaiveld"),
        ("GHB, kwel, doorlopende rand", "GHB: q = C(h_ext − h)"),
        ("Putten", "WEL: vast debiet per cel"),
        ("Vaste rand", "CHD; geen neerslag/randen/putten op die cellen"),
        ("Meebewegende T", "T = kh × natte dikte (MF6 'convertible', ICELLTYPE 1)"),
        ("Aan/uit RIV en DRN", "iteratief tot er niets meer wisselt"),
    ],
    columns=["Onderdeel", "Hoe (in beide kernen gelijk)"],
)
APP = pd.DataFrame(
    [
        ("Conductance watergangen", "natte omtrek per laag, intreeweerstand, radiale weerstand "
         "(Ernst met kv/kh), strookweerstand bij een bodem in klei, celgrootte-correctie "
         "(De Lange). In MODFLOW moet de modelleur dit zelf invullen."),
        ("Doorlopende rand", "GHB-conductance T·Δy/(Δx/2 + λ) uit de spreidingslengte"),
        ("Stijghoogte in de put", "Peaceman-correctie met de putstraal (MF6: MAW-pakket)"),
        ("Invoer en presentatie", "tekenen, zones, kaarten, dwarsprofiel, meldingen, export"),
    ],
    columns=["Wat de app altijd zelf doet (ook met MODFLOW 6)", "Toelichting"],
)  # fmt: skip
DIFF = pd.DataFrame(
    [
        ("Lagen", "één rekenlaag per pakket; scheidende laag als weerstand c (quasi-3D)",
         "elke laag een modellaag, ook de klei (K33 = D/c)", "rekenkundig gelijk"),
        ("Deklaag als bovenkant", "grondwaterstand bovenin de klei, hele c eronder",
         "dunne toplaag (1 cm) + kleilaag, zodat ook hier de hele c eronder zit",
         "gelijk"),
        ("Oplosser", "directe LU-ontbinding, hergebruikt als voorconditionering",
         "iteratief (IMS: CG, bij Newton BiCGSTAB)", "verschil < 10⁻⁶ m"),
        ("Droogvallende cellen", "rekenen door met 1 % natte dikte; kaart toont onderkant laag",
         "standaard: cel valt uit; daarna Newton (kan niet convergeren)",
         "kan cm's tot meer verschillen rond de put"),
        ("Newton (alleen MF6, bij droogval)", "–",
         "natte dikte stroomopwaarts gewogen", "enkele cm bij grote verlaging"),
        ("Rekentijd (100×100, 4 pakketten)", "± 1,5–2 s", "± 5–6 s", "–"),
    ],
    columns=["Onderdeel", "Eigen solver", "MODFLOW 6", "Effect op de uitkomst"],
)  # fmt: skip
TESTS = pd.DataFrame(
    [
        ("Standaardmodel, vaste T", "< 10⁻⁶ m"),
        ("Standaardmodel, meebewegende T", "< 10⁻⁶ m"),
        ("Deklaag, put in pakket 1, kwel, doorlopende rand", "< 10⁻⁶ m"),
        ("Zone met eigen lagen, rand per laag", "< 10⁻⁶ m"),
        ("Freatische put, natte dikte 10 → 7,7 m", "< 10⁻⁶ m (MF6-Newton: enkele cm)"),
    ],
    columns=["Testgeval (tests/test_mf6_comparison.py)", "Max. verschil stijghoogte"],
)

with uitleg:
    st.subheader("Wat is in beide kernen gelijk")
    st.dataframe(SAME, hide_index=True, width="stretch")
    st.subheader("Wat de app altijd zelf doet")
    st.dataframe(APP, hide_index=True, width="stretch")
    st.subheader("Waar ze verschillen")
    st.dataframe(DIFF, hide_index=True, width="stretch")
    st.subheader("Testresultaten")
    st.markdown(
        "Automatische tests rekenen vaste gevallen met beide kernen; ook de waterbalans per "
        "post, de puttabel en de meldingen zijn gelijk."
    )
    st.dataframe(TESTS, hide_index=True, width="stretch")
    st.subheader("Hoe zeg je het tegen collega's")
    st.info(
        "*Het grondwatermodel kan met MODFLOW 6 (USGS) rekenen; standaard rekent een eigen "
        "solver die dezelfde vergelijkingen oplost en daartegen getest is. De app maakt de "
        "invoer (o.a. de conductances van watergangen) en de presentatie. Het MODFLOW 6-model "
        "is als zip te downloaden en zelf na te rekenen.*"
    )

# ---- deel B: vergelijking van het huidige model ------------------------------------------
with vergelijk:
    mf6_ok, mf6_msg = mf6_status()
    done = st.session_state.get(f"{KEY}_result")
    if done is None:
        st.info("Bereken eerst een model op de pagina **Grondwatertoolbox**; dat model wordt "
                "hier met beide kernen doorgerekend.")  # fmt: skip
        st.stop()
    if not mf6_ok:
        st.warning(mf6_msg)
        st.stop()
    params: p3.Params = done["params"]
    st.markdown(
        f"Model van de laatste berekening: {params.nrow} × {params.ncol} cellen van "
        f"{params.cell:g} m, {len(params.stack.nodes)} rekenlagen, "
        f"{len(params.watercourses)} watergangen, {len(params.wells)} putgroepen."
    )
    if st.button("Vergelijk eigen solver en MODFLOW 6", type="primary", key=f"{KEY}_cmp"):
        st.session_state[f"{KEY}_cmp_for"] = params
    if st.session_state.get(f"{KEY}_cmp_for") != params:
        st.stop()

    own = run(dataclasses.replace(params, engine="eigen"))
    mf = run(dataclasses.replace(params, engine="mf6"))
    if mf.engine != "mf6":
        st.warning(f"MODFLOW 6 kon dit model niet rekenen: {mf.engine_note}")
        st.stop()

    labels = list(own.node_labels.values())
    d_new = mf.heads_new - own.heads_new
    d_ref = mf.heads_ref - own.heads_ref
    raised = [wc.name for wc in params.watercourses]
    q_own = float(own.budget.loc[raised, "opzet [m³/d]"].sum()) if raised else 0.0
    q_mf = float(mf.budget.loc[raised, "opzet [m³/d]"].sum()) if raised else 0.0
    cols = st.columns(4)
    cols[0].metric(
        "Max. verschil stijghoogte",
        f"{np.abs(d_new).max():.1e} m",
        border=True,
        help="Grootste |h_MF6 − h_eigen| over alle lagen en cellen (opzet).",
    )
    cols[1].metric("Gem. verschil stijghoogte", f"{np.abs(d_new).mean():.1e} m", border=True)
    cols[2].metric(
        "Verschil uitwisseling watergangen",
        f"{q_mf - q_own:+.2e} m³/d",
        border=True,
        help=f"Totaal na opzet: eigen {q_own:.1f}, MF6 {q_mf:.1f} m³/d.",
    )
    cols[3].metric("Rekentijd eigen / MF6", f"{own.seconds:.1f} / {mf.seconds:.1f} s",
                   border=True, help=f"MODFLOW 6-formulering: {mf.engine_note}.")  # fmt: skip

    st.markdown("**Verschil per laag** (|h_MF6 − h_eigen|, m)")
    st.dataframe(
        pd.DataFrame(
            {
                "Laag": labels,
                "Max. referentie": np.abs(d_ref).max(axis=(1, 2)),
                "Gem. referentie": np.abs(d_ref).mean(axis=(1, 2)),
                "Max. opzet": np.abs(d_new).max(axis=(1, 2)),
                "Gem. opzet": np.abs(d_new).mean(axis=(1, 2)),
            }
        ).style.format(
            "{:.1e}", subset=["Max. referentie", "Gem. referentie", "Max. opzet", "Gem. opzet"]
        ),  # fmt: skip
        hide_index=True,
        width="stretch",
    )

    c1, c2 = st.columns(2)
    which = c1.radio("Situatie", ["opzet", "referentie"], horizontal=True, key=f"{KEY}_cmp_sit")
    layer = c2.selectbox("Laag", range(len(labels)), format_func=labels.__getitem__,
                         key=f"{KEY}_cmp_layer")  # fmt: skip
    z = (d_new if which == "opzet" else d_ref)[layer]
    fig = result_figure(
        own.x,
        own.y,
        z,
        params.cell,
        done["wcs"],
        label="h_MF6 − h_eigen [m]",
        diverging=True,
        wells=[{"name": g.name, "cells": g.cells} for g in params.wells],
        min_span=0.01,
    )
    st.plotly_chart(fig, width="stretch", config=MAP_CONFIG, key=f"{KEY}_cmp_map")
    st.caption("Blauw = MODFLOW 6 hoger, rood = lager dan de eigen solver. De kleurschaal loopt "
               "minimaal van −1 tot +1 cm: een witte kaart betekent verschillen van hooguit "
               "enkele millimeters (hier meestal alleen rekenruis).")  # fmt: skip

    st.markdown("**Waterbalans na opzet [m³/d]**")
    budget = pd.DataFrame(
        {
            "eigen solver": own.budget["opzet [m³/d]"],
            "MODFLOW 6": mf.budget["opzet [m³/d]"],
        }
    )
    budget["verschil"] = budget["MODFLOW 6"] - budget["eigen solver"]
    st.dataframe(budget.style.format("{:.3f}", subset=["eigen solver", "MODFLOW 6"])
                 .format("{:.1e}", subset=["verschil"]), width="stretch")  # fmt: skip

    dry = [w for w in own.warnings + mf.warnings if "droog" in w]
    if dry or "Newton" in mf.engine_note:
        st.warning(
            "In dit model vallen cellen (bijna) droog. Daar behandelen de kernen het anders: "
            "de eigen solver rekent door met 1 % natte dikte, MODFLOW 6 met Newton. Verschillen "
            "rond die cellen zijn dan te verwachten."
        )
