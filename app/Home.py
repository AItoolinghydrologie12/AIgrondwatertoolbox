"""Startpagina van de Grondwatertoolbox: `streamlit run app/Home.py`."""

import streamlit as st

from gwproducten import __version__
from gwproducten.products import peilopzet_grid

st.set_page_config(page_title="Grondwatertoolbox", layout="wide")
st.title("Grondwatertoolbox")
st.markdown(
    "Rekenproducten voor geohydrologische vraagstukken: meerlagensystemen en watergangen "
    "met intree- én radiale weerstand, numeriek op een rooster. Kies links een product."
)
st.warning(
    "**Disclaimer** — indicatieve berekeningen; geen garantie op juistheid. Controleer "
    "uitkomsten en aannames zelf (tabs *Aannames* en *Validatie*) voordat je ze in een "
    "rapportage gebruikt.",
)

for product in (peilopzet_grid,):
    m = product.META
    with st.container(border=True):
        st.subheader(m["titel"])
        st.markdown(f"*{m['categorie']} · {m['regime']}*")
        st.markdown("\n".join(f"- {method}" for method in m["methoden"]))

st.caption(
    "Rekenkern: MODFLOW 6 (U.S. Geological Survey, publiek domein; Langevin e.a., 2017). De "
    "app schrijft het model en laat MODFLOW 6 rekenen; het model is te downloaden bij Export."
)

st.caption(f"gwproducten {__version__} · eenheden m en d · verlaging positief = lagere stijghoogte")
