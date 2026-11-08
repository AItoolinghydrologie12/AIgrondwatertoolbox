"""Startpagina van de Grondwatertoolbox: `streamlit run app/Home.py`."""

import streamlit as st

from gwproducten import __version__
from gwproducten.products import peilopzet_grid

st.set_page_config(page_title="Grondwatertoolbox", page_icon=":material/water_drop:", layout="wide")
st.title("Grondwatertoolbox")
st.markdown(
    "Rekenen aan grondwater bij een peilopzet, bemaling of damwand: meerdere lagen, "
    "watergangen met intree- én radiale weerstand, op een rooster in bovenaanzicht. "
    "Gerekend wordt met **MODFLOW 6**."
)

m = peilopzet_grid.META
with st.container(border=True):
    c1, c2 = st.columns([3, 2], gap="large", vertical_alignment="center")
    with c1:
        st.subheader(m["titel"])
        st.markdown(
            "1. **Ondergrond en maaiveld**: lagen, zones, drainage, modelrand (optioneel een "
            "locatie met kaart, maaiveld en ondergrond uit open data).\n"
            "2. **Tekenen**: watergangen, drainage, putten, vaste peilen, damwanden en zones.\n"
            "3. **Berekenen en resultaat**: verhoging of verlaging, dwarsprofiel, waterbalans "
            "en export (CSV, GIS, MODFLOW 6-model)."
        )
    with c2:
        st.page_link(
            "pages/1_Grondwatertoolbox.py", label="Open de Grondwatertoolbox",
            icon=":material/arrow_forward:",
        )  # fmt: skip
        st.caption(f"{m['categorie']} · {m['regime']} · eenheden m en d")

st.caption(
    "**Disclaimer**: indicatieve berekeningen, zonder garantie op juistheid. Controleer "
    "uitkomsten en aannames (tabbladen *Aannames* en *Validatie*) voordat je ze in een "
    "rapportage gebruikt. Rekenkern: MODFLOW 6 (U.S. Geological Survey, publiek domein; "
    "Langevin e.a., 2017). "
    f"gwproducten {__version__} · verlaging positief = lagere stijghoogte."
)
