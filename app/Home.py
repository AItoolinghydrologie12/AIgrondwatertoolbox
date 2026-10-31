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

with st.container(border=True):
    st.subheader("Rekenkern MODFLOW 6")
    st.markdown(
        "Uitleg van de twee rekenkernen (eigen solver en MODFLOW 6), wat gelijk is en wat "
        "verschilt, en een vergelijking van je eigen model met beide."
    )

st.caption(f"gwproducten {__version__} · eenheden m en d · verlaging positief = lagere stijghoogte")
