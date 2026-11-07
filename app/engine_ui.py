"""MODFLOW 6, the calculation engine: availability (installed once per server), cached runs,
the model zip and the "Berekend met" label."""

from __future__ import annotations

import streamlit as st

from gwproducten.products import peilopzet_grid as p3
from gwproducten.products.peilopzet_grid import mf6


@st.cache_resource(show_spinner="MODFLOW 6 klaarzetten (eenmalig)…")
def mf6_status() -> tuple[bool, str]:
    """(available, explanation); checked once per server. When missing, this downloads the
    fixed MODFLOW 6 release once (Windows or Linux) and verifies its SHA-256."""
    mf6.ensure_mf6()
    if mf6.available():
        return True, f"Rekenkern: MODFLOW {mf6.version()} (USGS)."
    return False, mf6.NOT_AVAILABLE


@st.cache_data(show_spinner="Rekenen met MODFLOW 6…", max_entries=20)  # bounded: shared memory
def run(params: p3.Params) -> p3.Result:
    return p3.compute(params)


@st.cache_data(show_spinner="MODFLOW 6-model schrijven…", max_entries=5)
def model_zip(params: p3.Params) -> bytes:
    return mf6.model_zip(params)


def engine_badge(res: p3.Result) -> None:
    """Which MODFLOW 6 formulation produced the result, and the time."""
    text = f"Berekend met **{res.engine_note}** in {res.seconds:.1f} s."
    if res.engine_note.endswith("debiet verlaagd)"):
        st.warning(text + " Een of meer putten leveren minder dan gevraagd; zie de meldingen.")
    else:
        st.caption(text)
