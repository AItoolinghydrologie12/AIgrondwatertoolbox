"""Smoke tests: every page of the Streamlit app runs without exceptions."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parent.parent / "app"
PAGES = [APP / "Home.py", *sorted((APP / "pages").glob("*.py"))]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.stem)
def test_page_runs_without_errors(page: Path) -> None:
    at = AppTest.from_file(str(page), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]


def test_grid_product_page_example_and_views() -> None:
    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    at.button(key="p3_calc").click().run()
    for view in ("opzet", "ref", "gws"):
        at.selectbox(key="p3_view").set_value(view).run()
        assert not at.exception, [e.value for e in at.exception]
    at.radio(key="p3_bnd").set_value("gesloten").run()
    at.number_input(key="p3_cell").set_value(50.0).run()  # watercourses follow in metres
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]


def test_grid_product_computes_only_after_button() -> None:
    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=120).run()
    assert "p3_result" not in at.session_state
    assert any("Nog niet gerekend" in i.value for i in at.info)
    at.button(key="p3_calc").click().run()
    first = at.session_state["p3_result"]
    assert any("Berekend in" in s.value for s in at.success)
    at.number_input(key="p3_rch").set_value(1.5).run()  # change input: no recompute
    assert at.session_state["p3_result"]["params"] == first["params"]
    assert any("invoer is gewijzigd" in w.value for w in at.warning)
    at.button(key="p3_calc_side").click().run()  # sidebar button recomputes
    assert at.session_state["p3_result"]["params"].recharge == pytest.approx(0.0015)
    assert any("Berekend in" in s.value for s in at.success)
    assert not at.exception, [e.value for e in at.exception]


def test_grid_product_line_drawing_gives_partial_cells() -> None:
    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=120).run()
    n_before = len(at.session_state["p3_wcs"])
    # a line from a cell centre to halfway a cell: last cell gets 12.5 m
    at.session_state["p3_line"] = [(112.5, 100.0), (112.5, 162.5)]
    at.run()
    at.text_input(key="p3_np_name").set_value("Lijnsloot")
    at.button(key="p3_finish").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    wcs = at.session_state["p3_wcs"]
    assert len(wcs) == n_before + 1
    new = wcs[-1]
    assert new["name"] == "Lijnsloot" and new["lines"] == [[(112.5, 100.0), (112.5, 162.5)]]


def test_grid_product_profile_modes() -> None:
    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=120).run()
    at.button(key="p3_calc").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.metric) >= 6  # KPI cards
    at.slider(key="p3_ph").set_value(400.0).run()
    assert not at.exception, [e.value for e in at.exception]
    at.session_state["p3_pmode"] = "verticaal"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    at.session_state["p3_pmode"] = "lijn"
    at.session_state["p3_prof"] = [(100.0, 500.0), (900.0, 500.0)]
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]


def test_grid_product_wells_ghb_seepage_and_layer_boundary() -> None:
    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=120).run()
    at.session_state["p3_wells"] = [
        {
            "name": "Bemaling",
            "layer": "pakket1",
            "q_ref": 0.0,
            "q_new": 150.0,
            "points": {(312.5, 512.5)},
        }
    ]
    at.session_state["p3_ghb"] = [
        {
            "name": "Boezem",
            "layer": "freatisch",
            "head_ref": -0.6,
            "head_new": -0.6,
            "resistance": 2.0,
            "points": {(12.5 + 25 * r, 12.5) for r in range(40)},
        }
    ]
    at.run()
    at.toggle(key="p3_kwel").set_value(True).run()
    at.radio(key="p3_bnd").set_value("per_laag").run()
    at.selectbox(key="p3_bkind_freatisch").set_value("gesloten").run()
    at.selectbox(key="p3_bkind_pakket1").set_value("doorlopend").run()
    at.button(key="p3_calc").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    par = at.session_state["p3_result"]["params"]
    assert par.seepage and par.boundary_layers == (None, 3.8)
    assert par.boundary_kinds == ("gesloten", "doorlopend")
    assert [g.name for g in par.wells] == ["Bemaling"] and len(par.ghb[0].cells) == 40
    budget = at.session_state["p3_result"]["res"].budget
    assert {"Bemaling", "Boezem", "kwel onderrand"} <= set(budget.index)
    at.selectbox(key="p3_view").set_value("putten").run()
    assert not at.exception, [e.value for e in at.exception]


def test_drawing_figure_is_fast_on_fine_grid() -> None:
    """100 x 100 cells: one grid-line trace and a WebGL target keep the figure light."""
    import sys
    import time

    sys.path.insert(0, str(APP))
    from grid_ui import drawing_figure, wc_geometry

    cell, n = 10.0, 100
    wcs = [
        {"name": f"w{k}", "lines": [[(400.0 + 200 * k, 0.0), (420.0 + 200 * k, 1000.0)]],
         "points": set(), "length": 10.0}
        for k in range(3)
    ]  # fmt: skip
    for wc in wcs:
        wc["cells"], wc["lengths"] = wc_geometry(wc, cell, n, n)
    t0 = time.perf_counter()
    drawing_figure(n, n, cell, wcs, ()).to_json()
    assert time.perf_counter() - t0 < 2.0


def test_grid_product_page_fine_cells() -> None:
    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=120).run()
    at.number_input(key="p3_cell").set_value(10.0).run()
    at.button(key="p3_calc").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]


def test_grid_product_model_size_follows_cell_size() -> None:
    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=120).run()
    at.number_input(key="p3_cell").set_value(50.0).run()
    at.number_input(key="p3_sx").set_value(5000.0).run()
    at.number_input(key="p3_sy").set_value(5000.0).run()
    at.button(key="p3_calc").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    at.number_input(key="p3_cell").set_value(10.0).run()  # max shrinks to 1 km: clipped
    assert at.number_input(key="p3_sx").value == 1000.0
    assert not at.exception, [e.value for e in at.exception]


def test_grid_product_four_aquifers() -> None:
    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=120).run()
    at.number_input(key="p3_naq_freatisch_zand").set_value(4).run()
    at.radio(key="p3_bnd").set_value("per_laag").run()
    at.toggle(key="p3_kwel").set_value(True).run()
    at.button(key="p3_calc").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    for view in ("opzet", "ref", "gws"):
        at.selectbox(key="p3_view").set_value(view).run()
        assert not at.exception, [e.value for e in at.exception]


def test_grid_product_zones() -> None:
    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=120).run()
    at.button(key="p3_zone_add").click().run()
    zones = at.session_state["p3_zones"]
    assert [z["name"] for z in zones] == ["Zone 1"]
    # cells as on the map (centres in metres), across the raised ditch
    zones[0]["points"] = {
        ((c + 0.5) * 25.0, (r + 0.5) * 25.0) for r in range(10, 30) for c in range(18, 23)
    }
    at.session_state["p3_zones"] = zones
    at.number_input(key="p3_z1_mv").set_value(0.5).run()
    at.number_input(key="p3_z1_freatisch_zand_2_0_d").set_value(8.0).run()
    at.selectbox(key="p3_schem_of").set_value("Zone 1").run()
    at.radio(key="p3_obj").set_value("zone").run()
    at.radio(key="p3_how").set_value("toevoegen").run()
    at.button(key="p3_calc").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    for view in ("opzet", "gws"):
        at.selectbox(key="p3_view").set_value(view).run()
        assert not at.exception, [e.value for e in at.exception]
    at.button(key="p3_z1_del").click().run()
    assert at.session_state["p3_zones"] == []
    assert not at.exception, [e.value for e in at.exception]


@pytest.mark.parametrize(("cell", "size"), [(25.0, 1000.0), (10.0, 1000.0), (50.0, 5000.0)])
def test_default_example_scales_with_model(cell: float, size: float) -> None:
    """The example watercourses stay at 1/3 and 2/3 of the width over the full height, in
    the app (relative lines) and in the core defaults (``default_watercourses``)."""
    import sys

    sys.path.insert(0, str(APP))
    from grid_ui import wc_geometry

    from gwproducten.products import peilopzet_grid as p3

    n = int(size / cell)
    core = p3.default_watercourses(size, size, cell)
    for (frac, spec), wc_core in zip(p3.DEFAULT_WATERCOURSE_SPECS, core, strict=True):
        drawn = {"rel_lines": [[(frac, 0.0), (frac, 1.0)]], "length": cell}
        cells, lengths = wc_geometry(drawn, cell, n, n)
        cols = {c for _, c in cells}
        assert cols == {int(frac * n)} == {c for _, c in wc_core.cells}
        assert len(cells) == n and sum(lengths) == pytest.approx(size)
        assert wc_core.name == spec.name
    par = p3.Params(cell=cell, size_x=size, size_y=size, watercourses=core)
    res = p3.compute(par)
    assert max(res.mass_balance.values()) < 1e-8
    assert res.rise.max() > 0.0  # the raised Wetering lifts the groundwater


def test_grid_product_drawing_menu_all_combinations() -> None:
    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=120).run()
    for how in ("lijn", "cellen", "wissen"):
        at.radio(key="p3_wc_how").set_value(how).run()
        assert not at.exception, [e.value for e in at.exception]
    at.radio(key="p3_wc_how").set_value("cellen").run()
    at.selectbox(key="p3_wc_target").set_value("Wetering").run()
    for obj in ("drain", "put", "ghb", "zone"):
        at.radio(key="p3_obj").set_value(obj).run()
        for how in ("toevoegen", "verwijderen"):
            at.radio(key="p3_how").set_value(how).run()
            assert not at.exception, (obj, how, [e.value for e in at.exception])


def test_maps_keep_zoom_and_snap_generously() -> None:
    """uirevision keeps zoom/pan between reruns; in click mode a click within 50 px picks
    the nearest snap point and dragging pans the map."""
    import sys

    import numpy as np

    sys.path.insert(0, str(APP))
    from grid_ui import drawing_figure, result_figure

    draw = drawing_figure(40, 40, 25.0, [], (), snap=5.0)
    assert draw.layout.uirevision == "40x40x25.0"
    assert draw.layout.hoverdistance == 50 and draw.layout.dragmode == "pan"
    x = y = (np.arange(40) + 0.5) * 25.0
    res = result_figure(x, y, np.zeros((40, 40)), 25.0, [], label="h", click_snap=10.0)
    assert res.layout.uirevision and res.layout.hoverdistance == 50


def test_grid_product_project_roundtrip() -> None:
    """Change inputs and drawings, save, load into a fresh session: same model."""
    import json
    import sys

    sys.path.insert(0, str(APP))
    from project_io import export_project, parse_project

    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=120).run()
    at.number_input(key="p3_cell").set_value(20.0).run()
    at.number_input(key="p3_mv").set_value(4.5).run()
    at.number_input(key="p3_naq_freatisch_zand").set_value(3).run()
    at.number_input(key="p3_freatisch_zand_2_a").set_value(0.1).run()
    at.radio(key="p3_bnd").set_value("gesloten").run()
    at.toggle(key="p3_kwel").set_value(True).run()
    at.button(key="p3_zone_add").click().run()
    zones = at.session_state["p3_zones"]
    zones[0]["points"] = {(110.0, 110.0), (130.0, 110.0)}
    at.session_state["p3_zones"] = zones
    at.number_input(key="p3_z1_mv").set_value(5.2).run()
    at.session_state["p3_drain"] = {(10.0, 10.0)}
    at.button(key="p3_calc").click().run()
    assert not at.exception, [e.value for e in at.exception]
    before = at.session_state["p3_result"]["params"]
    assert before.zones and before.zones[0].surface == 5.2 and before.cell == 20.0
    assert before.stack.layers[2].anisotropy == 0.1

    text = json.dumps(export_project(at.session_state.to_dict(), "p3"))
    data, errors = parse_project(text)
    assert not errors

    fresh = AppTest.from_file(page, default_timeout=120).run()
    fresh.session_state["p3_pending_project"] = data
    fresh.run()
    fresh.button(key="p3_calc").click().run()
    assert not fresh.exception, [e.value for e in fresh.exception]
    # same model, same messages (kwel above this surface gives model warnings)
    assert [w.value for w in fresh.warning] == [w.value for w in at.warning]
    after = fresh.session_state["p3_result"]["params"]
    assert after == before


def test_invalid_project_file_gives_message() -> None:
    import sys

    sys.path.insert(0, str(APP))
    from project_io import parse_project

    assert parse_project("geen json")[1]
    assert "geen projectbestand" in parse_project('{"a": 1}')[1][0]
    assert "versie" in parse_project('{"product": "peilopzet_grid", "versie": 99}')[1][0].lower()


def test_grid_product_validation_benchmark_button() -> None:
    """The benchmark in the validation tab runs for the allowed cell sizes."""
    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=180).run()
    at.button(key="p3_calc").click().run()
    at.button(key="p3_bench").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]


def test_grid_product_with_modflow6_engine() -> None:
    """Engine MODFLOW 6: label 'Berekend met MODFLOW 6', same result as the own solver, and
    the MF6 model as zip in the export tab. Skipped without flopy/mf6."""
    from gwproducten.products.peilopzet_grid import mf6

    if not mf6.available():
        pytest.skip("flopy/mf6 niet geïnstalleerd")
    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=180).run()
    at.button(key="p3_calc").click().run()
    own = at.session_state["p3_result"]["res"]
    at.radio(key="p3_engine").set_value("mf6").run()
    at.button(key="p3_calc").click().run()
    assert not at.exception, [e.value for e in at.exception]
    res = at.session_state["p3_result"]["res"]
    assert res.engine == "mf6"
    assert any("Berekend met **MODFLOW 6" in s.value for s in at.success)
    assert abs(res.heads_new - own.heads_new).max() < 1e-5
    at.button(key="p3_mf6zip").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["p3_mf6zip_for"] == at.session_state["p3_result"]["params"]


def test_engine_comparison_page() -> None:
    """Page 'Rekenkern MODFLOW 6': explanation without a model; with the last model of the
    Grondwatertoolbox page the comparison button shows tiny differences."""
    from gwproducten.products import peilopzet_grid as p3
    from gwproducten.products.peilopzet_grid import mf6

    page = str(APP / "pages" / "2_Rekenkern_MODFLOW6.py")
    at = AppTest.from_file(page, default_timeout=180).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Bereken eerst" in i.value for i in at.info)
    if not mf6.available():
        pytest.skip("flopy/mf6 niet geïnstalleerd")
    par = p3.Params()
    at.session_state["p3_result"] = {"params": par, "res": p3.compute(par), "wcs": []}
    at.run()
    at.button(key="p3_cmp").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert float(at.metric[0].value.split()[0]) < 1e-5  # max head difference [m]
    # equal results: a message instead of a map of rounding noise
    assert any("hetzelfde resultaat" in s.value for s in at.success)
    assert not [r for r in at.radio if r.key == "p3_cmp_sit"]

    # drying cells around a well: MF6 needs Newton, the results differ and the map is shown
    wells = (p3.WellGroup("bem", ((20, 20), (20, 21)), "freatisch", 0.0, 30.0),)
    par = p3.Params(wells=wells)
    at.session_state["p3_result"] = {"params": par, "res": p3.compute(par), "wcs": []}
    at.run()
    at.button(key="p3_cmp").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("droog" in w.value for w in at.warning)
    at.radio(key="p3_cmp_sit").set_value("referentie").run()
    assert not at.exception, [e.value for e in at.exception]


def test_grid_product_line_undo_and_cancel_in_drawing_panel() -> None:
    """The line buttons live in the drawing fragment; they also work in a full-page run."""
    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=120).run()
    at.session_state["p3_line"] = [(100.0, 100.0), (200.0, 100.0), (200.0, 300.0)]
    at.run()
    at.button(key="p3_undo").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["p3_line"] == [(100.0, 100.0), (200.0, 100.0)]
    at.button(key="p3_cancel").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["p3_line"] == []


def test_drawing_figure_cells_are_compact_and_in_place() -> None:
    """Cell layers only carry the bounding box of their cells, at the right position, so a
    100 x 100 drawing stays small enough to send on every click."""
    import sys

    import numpy as np

    sys.path.insert(0, str(APP))
    from grid_ui import drawing_figure

    cell, n = 10.0, 100
    wc = {"name": "w", "cells": ((5, 40), (6, 40), (6, 41)), "lengths": (10.0, 4.0, 6.0)}
    snap = max(cell / 5.0, n * cell / 200.0)  # as on the page
    fig = drawing_figure(n, n, cell, [wc], ((50, 50),), snap=snap)
    heat = [t for t in fig.data if t.type == "heatmap"]
    assert [t.name for t in heat] == ["buisdrainage", "w"]
    w = heat[1]
    z = np.asarray(w.z, dtype=float)
    assert z.shape == (2, 2)  # rows 5-6, columns 40-41
    assert (w.x0, w.y0, w.dx, w.dy) == (405.0, 55.0, cell, cell)  # centre of cell (5, 40)
    assert z[0, 0] == 10.0 and z[1, 0] == 4.0 and z[1, 1] == 6.0 and np.isnan(z[0, 1])
    assert len(fig.to_json()) < 600_000  # as sent by Streamlit (typed arrays)
    empty = drawing_figure(n, n, cell, [], ())
    assert not [t for t in empty.data if t.type == "heatmap"]  # no drains: no layer


def test_grid_product_location_sets_rd_origin() -> None:
    """With a location the lower-left RD corner goes into Params (MF6 export); without it
    the model stays in local metres. The location is saved in the project."""
    import sys

    sys.path.insert(0, str(APP))
    from project_io import export_project

    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=120).run()
    at.button(key="p3_calc").click().run()
    assert at.session_state["p3_result"]["params"].origin_x == 0.0
    at.toggle(key="p3_geo").set_value(True).run()
    at.number_input(key="p3_cx").set_value(136800.0).run()
    at.number_input(key="p3_cy").set_value(455900.0).run()
    at.button(key="p3_calc").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    params = at.session_state["p3_result"]["params"]
    assert (params.origin_x, params.origin_y) == (136300.0, 455400.0)  # centre - 500 m
    saved = export_project(at.session_state, "p3")["invoer"]
    assert saved["geo"] is True and saved["cx"] == 136800.0 and saved["cy"] == 455900.0


def test_grid_product_background_map(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a location and a background choice both maps get the image (service mocked)."""
    from gwproducten.geo import pdok

    seen = []

    def fake_image(kind: str, bbox: tuple) -> bytes:
        seen.append((kind, bbox))
        return b"\xff\xd8fake"

    monkeypatch.setattr(pdok, "background_image", fake_image)
    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=120).run()
    at.toggle(key="p3_geo").set_value(True).run()
    at.number_input(key="p3_cx").set_value(120500.0).run()
    at.selectbox(key="p3_bg").set_value("luchtfoto").run()
    at.button(key="p3_calc").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert ("luchtfoto", (120000.0, 462500.0, 121000.0, 463500.0)) in seen
    assert at.session_state["p3_fig_draw"].layout.images[0].source.startswith("data:image/jpeg")
    assert any("Beeldmateriaal" in c.value for c in at.caption)


def test_grid_product_top10nl_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Import with the service mocked by a saved response: one watercourse per width class,
    lines in model metres, a reminder to fill in the levels; the model computes."""
    import json

    from gwproducten.geo import water

    data = json.loads((Path(__file__).parent / "data" / "top10nl_waterdeel_lijn.json").read_text())
    vlak = json.loads((Path(__file__).parent / "data" / "top10nl_waterdeel_vlak.json").read_text())
    items = water.parse_waterdelen(data, False) + water.parse_waterdelen(vlak, True)
    monkeypatch.setattr(water, "fetch_waterdelen", lambda bbox: items)
    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=120).run()
    at.toggle(key="p3_geo").set_value(True).run()
    at.number_input(key="p3_cx").set_value(128000.0).run()
    at.number_input(key="p3_cy").set_value(446000.0).run()
    at.button(key="p3_clear").click().run()
    at.button(key="p3_top10_get").click().run()
    assert not at.exception, [e.value for e in at.exception]
    at.button(key="p3_top10_add").click().run()
    wcs = at.session_state["p3_wcs"]
    assert [wc["width"] for wc in wcs] == [1.75, 4.5, 9.0]
    assert all(wc["name"].endswith("(peil invullen)") for wc in wcs)
    x, y = wcs[0]["lines"][0][0]
    assert -1000.0 < x < 2000.0 and -1000.0 < y < 2000.0  # local metres, not RD
    assert wcs[2]["points"] and not wcs[2]["lines"]  # the areas
    assert any("peil invullen" in w.value for w in at.warning)
    at.button(key="p3_calc").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.session_state["p3_result"]["params"].watercourses) == 3


def test_grid_product_surface_from_ahn(monkeypatch: pytest.MonkeyPatch) -> None:
    """AHN (mocked): the model gets the median of all cells, a zone the median of its own
    cells; values go into the surface inputs of step 1."""
    import numpy as np

    from gwproducten.geo import ahn

    def fake(bbox: tuple, nrow: int, ncol: int) -> np.ndarray:
        values = np.full((nrow, ncol), -1.0)
        values[:5, :5] = 2.0  # south-west corner higher
        return values

    monkeypatch.setattr(ahn, "surface_per_cell", fake)
    page = str(APP / "pages" / "1_Grondwatertoolbox.py")
    at = AppTest.from_file(page, default_timeout=120).run()
    at.button(key="p3_clear").click().run()  # the example ditches lie above this surface
    at.button(key="p3_zone_add").click().run()
    zones = at.session_state["p3_zones"]
    zones[0]["points"] = {(12.5, 12.5), (37.5, 37.5)}  # two cells in the corner
    at.session_state["p3_zones"] = zones
    at.toggle(key="p3_geo").set_value(True).run()
    at.button(key="p3_ahn_get").click().run()
    assert not at.exception, [e.value for e in at.exception]
    at.button(key="p3_ahn_use").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.number_input(key="p3_mv").value == -1.0
    assert at.number_input(key="p3_z1_mv").value == 2.0
    at.button(key="p3_calc").click().run()
    assert not at.error, [e.value for e in at.error]
    params = at.session_state["p3_result"]["params"]
    assert params.surface == -1.0 and params.zones[0].surface == 2.0
