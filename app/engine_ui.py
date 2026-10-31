"""Calculation engines (own solver, MODFLOW 6) shared by the pages: cached runs, MF6
availability and the "Berekend met" label."""

from __future__ import annotations

import streamlit as st

from gwproducten.products import peilopzet_grid as p3
from gwproducten.products.peilopzet_grid import mf6

ENGINE_LABEL = {"eigen": "eigen solver (snel)", "mf6": "MODFLOW 6"}


@st.cache_resource(show_spinner="MODFLOW 6 klaarzetten (eenmalig)…")
def mf6_status() -> tuple[bool, str]:
    """(available, explanation); checked once per server. On the Linux cloud server this
    downloads the fixed MODFLOW 6 release once and verifies its SHA-256."""
    mf6.ensure_mf6()
    if mf6.available():
        return True, f"MODFLOW {mf6.version()} beschikbaar."
    return False, "MODFLOW 6 is op deze server niet geïnstalleerd; de eigen solver rekent."


@st.cache_data(show_spinner="Rekenen…", max_entries=20)  # bounded: shared memory in the cloud
def run(params: p3.Params) -> p3.Result:
    return p3.compute(params)


@st.cache_data(show_spinner="MODFLOW 6-model schrijven…", max_entries=5)
def model_zip(params: p3.Params) -> bytes:
    return mf6.model_zip(params)


def engine_badge(params: p3.Params, res: p3.Result) -> None:
    """Which engine produced the result, and why when MODFLOW 6 was asked but not used."""
    if res.engine == "mf6":
        st.success(f"Berekend met **{res.engine_note}** in {res.seconds:.1f} s.", icon="✅")
    elif params.engine == "mf6":
        st.warning(f"MODFLOW 6 niet gebruikt: {res.engine_note}", icon="⚠️")
    else:
        st.caption(f"Berekend met de eigen solver in {res.seconds:.1f} s.")
