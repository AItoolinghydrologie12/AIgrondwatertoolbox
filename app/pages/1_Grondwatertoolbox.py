"""Grondwatertoolbox — peilopzet en grondwaterstroming op een rooster (numeriek).

The steps live in their own modules: ``subsoil_ui`` (1), ``draw_ui`` (2) and ``result_ui``
(3); this page holds the sidebar and the order of the steps."""

from __future__ import annotations

import dataclasses
import hashlib
import time
from typing import Any

import streamlit as st
from draw_ui import draw_step
from engine_ui import ENGINE_LABEL, mf6_status, run
from location_ui import location_inputs
from project_io import apply_project, parse_project, to_json
from result_ui import result_tabs
from subsoil_ui import subsoil_step
from toolbox_state import DEFAULTS, KEY, Grid, build_params, example, reset, ss
from ui_common import number, page_header, show_param_error

from gwproducten.core.params import ParamError, meta
from gwproducten.products import peilopzet_grid as p3

STEPS = ["1 · Ondergrond en maaiveld", "2 · Watergangen tekenen", "3 · Berekenen en resultaat"]

page_header(p3.META)

if f"{KEY}_wcs" not in ss:
    reset(example())
ss.setdefault(f"{KEY}_zones", [])
_pending = ss.pop(f"{KEY}_pending_project", None)
if _pending is not None:  # loaded in the previous run, applied before the widgets exist
    apply_project(ss, KEY, _pending)
    ss.pop(f"{KEY}_result", None)
    ss[f"{KEY}_ver"] = ss.get(f"{KEY}_ver", 0) + 1


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
    mf6_ok, mf6_msg = mf6_status()
    if not mf6_ok and ss.get(f"{KEY}_engine") == "mf6":
        ss[f"{KEY}_engine"] = "eigen"  # e.g. a project saved where MODFLOW 6 was installed
    values["engine"] = st.radio(
        "Rekenkern", list(ENGINE_LABEL), format_func=ENGINE_LABEL.get, key=f"{KEY}_engine",
        disabled=not mf6_ok,
        help="Eigen solver: snel, rekent dezelfde vergelijkingen als MODFLOW 6. MODFLOW 6: "
        "het model wordt echt door MODFLOW 6 (USGS) gerekend; langzamer. Zie de pagina "
        "'Rekenkern MODFLOW 6' voor de verschillen. " + mf6_msg,
    )  # fmt: skip

step1, step2, step3 = st.tabs(STEPS, key=f"{KEY}_step")

# ---- stap 1: ondergrond, maaiveld en drainage ------------------------------------------
with step1:
    location_inputs(values)
    stack, zone_values, schematic_of, schematic_box = subsoil_step(values)

cell = float(values["cell"])
grid = Grid(cell, int(round(values["size_y"] / cell)), int(round(values["size_x"] / cell)))
if f"{KEY}_wells" not in ss:
    ss[f"{KEY}_wells"], ss[f"{KEY}_ghb"] = [], []
node_labels = {n.key: n.label for n in stack.nodes} if not stack.errors() else {}

try:
    schem_params = build_params(grid, stack, zone_values, values, grid.watercourses())
    if schematic_of != "basis":  # cross-section with the layers and surface of that zone
        zid = next(z["id"] for z in ss[f"{KEY}_zones"] if z["name"] == schematic_of)
        zstack, zsurface = zone_values[zid]
        schem_params = dataclasses.replace(schem_params, stack=zstack, surface=zsurface, zones=())
    schematic_box.plotly_chart(p3.schematic(schem_params), width="stretch")
except (ParamError, ValueError) as err:
    schematic_box.info(f"Lagenopbouw niet te tekenen: {err}")

# ---- stap 2: tekenen --------------------------------------------------------------
with step2:
    fig_draw = draw_step(grid, values, node_labels, zone_values)

# ---- stap 3: rekenen (alleen na "Bereken") en resultaat --------------------------------
wcs_view = grid.watercourses()
try:
    current = build_params(grid, stack, zone_values, values, wcs_view)
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
    status = ("success", f"Berekend in {done['res'].seconds:.1f} s.")
getattr(c_msg, status[0])(status[1])
getattr(status_side, status[0])(status[1])
if input_error is not None:
    with step3:
        show_param_error(input_error)
    status_side.error("De invoer is niet geldig; zie stap 3.")

with step3:
    result_tabs(ss.get(f"{KEY}_result"), fig_draw)

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
