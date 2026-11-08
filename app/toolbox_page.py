"""The Grondwatertoolbox page: sidebar, the three steps and the order in which they are
built. The steps live in their own modules: ``subsoil_ui`` (1), ``draw_ui`` (2) and
``result_ui`` (3)."""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Any

import streamlit as st
from draw_ui import draw_step
from engine_ui import mf6_status, run
from location_ui import location_inputs
from project_io import apply_project, parse_project, to_json
from result_ui import result_tabs
from status_ui import calc_status, progress_list
from subsoil_ui import model_area_inputs, subsoil_step
from toolbox_state import DEFAULTS, KEY, Grid, build_params, checkpoint, clear_undo, reset, ss
from ui_common import number, page_header, show_param_error

from gwproducten.core.params import ParamError
from gwproducten.products import peilopzet_grid as p3

STEPS = ["1 · Ondergrond en maaiveld", "2 · Watergangen tekenen", "3 · Berekenen en resultaat"]


def main() -> None:
    """Build the page (one Streamlit run)."""
    page_header(p3.META)

    if f"{KEY}_wcs" not in ss:
        reset([])  # start empty; "Voorbeeld laden" gives the two example watercourses
    for _name in ("zones", "walls", "wells", "ghb", "chd"):  # also sessions from older versions
        ss.setdefault(f"{KEY}_{_name}", [])
    _pending = ss.pop(f"{KEY}_pending_project", None)
    if _pending is not None:  # loaded in the previous run, applied before the widgets exist
        apply_project(ss, KEY, _pending)
        ss.pop(f"{KEY}_result", None)
        ss[f"{KEY}_ver"] = ss.get(f"{KEY}_ver", 0) + 1
        clear_undo()
    checkpoint()  # the drawing before any change in this run ("Ongedaan maken")

    # ---- zijbalk: rekenen, model en numeriek ----------------------------------------------
    clicked = st.sidebar.button(
        "Bereken", type="primary", icon=":material/play_arrow:", key=f"{KEY}_calc_side",
        width="stretch",
    )  # fmt: skip
    status_side = st.sidebar.empty()
    progress_side = st.sidebar.container()
    values: dict[str, Any] = {}
    project_box = st.sidebar.expander("Project opslaan of laden", expanded=False)
    with st.sidebar.expander("Beoordeling en numeriek", expanded=False):
        values["threshold"] = number(
            p3.Params, "threshold", DEFAULTS.threshold, f"{KEY}_thr", 0.01,
            help="Beoordelingsgrens voor verhoging en verlaging (0,05 m = 5 cm). Bepaalt de "
            "dikke grenslijnen op de kaart en het oppervlak in de kerngetallen; de berekening "
            "zelf verandert niet.",
        )  # fmt: skip
        values["grid_correction"] = st.checkbox(
            "Celgrootte-correctie (De Lange-principe)", value=True, key=f"{KEY}_gc",
            help="Corrigeert de uitwisseling tussen watergang en grondwater voor de celgrootte, "
            "zodat grove cellen dezelfde infiltratie geven als een fijn rooster. Laat aan.",
        )  # fmt: skip
        values["convertible"] = st.checkbox(
            "Transmissiviteit volgt de natte dikte (zoals MODFLOW 6 'convertible')",
            value=True,
            key=f"{KEY}_conv",
            help="Uit: T = kh·D blijft vast (lineair model). Aan: T = kh × natte dikte in het "
            "freatisch pakket en in pakketten waar de stijghoogte onder de bovenkant zakt.",
        )
        mf6_ok, mf6_msg = mf6_status()
        if mf6_ok:
            st.caption(mf6_msg)
    if not mf6_ok:
        st.sidebar.error(mf6_msg)

    step1, step2, step3 = st.tabs(STEPS, key=f"{KEY}_step")

    # ---- stap 1: ondergrond, maaiveld en drainage ------------------------------------------
    with step1:
        location_box = st.container()  # shown first, filled once the model size is known
        model_area_inputs(values)
        with location_box:
            location_inputs(values)
        stack, zone_values, schematic_of, schematic_box = subsoil_step(values)

    cell = float(values["cell"])
    grid = Grid(cell, int(round(values["size_y"] / cell)), int(round(values["size_x"] / cell)))
    max_cells = p3.max_cells_per_side(len(stack.nodes)) if not stack.errors() else grid.nrow
    if max(grid.nrow, grid.ncol) > max_cells:
        with step1:
            st.warning(
                f"Met {len(stack.nodes)} rekenlagen kan het model maximaal "
                f"{max_cells} × {max_cells} cellen hebben (nu {grid.ncol} × {grid.nrow}), om "
                "de rekentijd en het geheugen binnen de perken te houden. Kies grotere cellen "
                "of een kleiner model (Modelgebied, bovenaan stap 1)."
            )
    node_labels = {n.key: n.label for n in stack.nodes} if not stack.errors() else {}

    try:
        schem_params = build_params(grid, stack, zone_values, values, grid.watercourses())
        if schematic_of != "basis":  # cross-section with the layers and surface of that zone
            zid = next(z["id"] for z in ss[f"{KEY}_zones"] if z["name"] == schematic_of)
            zstack, zsurface = zone_values[zid]
            schem_params = dataclasses.replace(
                schem_params, stack=zstack, surface=zsurface, zones=()
            )
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
            width="stretch", disabled=not mf6_ok,
            help=None if mf6_ok else mf6_msg,
        )  # fmt: skip

    calc_error = None
    if clicked and input_error is None and mf6_ok:
        try:
            ss[f"{KEY}_result"] = {"params": current, "res": run(current), "wcs": wcs_view}
        except ParamError as err:
            input_error = err
        except RuntimeError as err:  # MODFLOW 6 missing or without a solution
            calc_error = str(err)

    status = calc_status(mf6_ok, mf6_msg, calc_error, ss.get(f"{KEY}_result"), current)
    getattr(c_msg, status[0])(status[1])
    getattr(status_side, status[0])(status[1])
    with progress_side:
        progress_list(stack, grid, ss.get(f"{KEY}_geo", False), status[0])
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
