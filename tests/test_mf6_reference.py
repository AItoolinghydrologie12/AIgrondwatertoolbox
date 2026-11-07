"""The app's MODFLOW 6 engine against fixed reference results.

The reference (tests/data/reference_cases.*) holds heads, water balance, well table, wall
flows and messages of ten cases computed on 2026-11-07 by the app's former own solver, which
agreed with MODFLOW 6 (flopy-built models) within 1e-5 m in every case. The cases cover the
quasi-3D resistances, the harmonic mean, RIV/DRN/GHB/WEL/CHD, the continuing boundary, zones,
convertible transmissivity, sheet pile walls (HFB) and the MF6 row order. Skipped when the
mf6 executable is missing."""

import dataclasses
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gwproducten.core.grids import outline_faces, polyline_faces
from gwproducten.core.layers import LayerStack, build_layers
from gwproducten.products import peilopzet_grid as p3
from gwproducten.products.peilopzet_grid import mf6

pytestmark = pytest.mark.skipif(not mf6.available(), reason="mf6 niet geïnstalleerd")

CLAY = LayerStack(
    "deklaag",
    build_layers(
        "deklaag", [(20.0, 15.0), (30.0, 25.0)], [(2.0, 500.0, 0.02), (5.0, 2000.0, 0.01)]
    ),
)
ZONE_STACK = LayerStack(
    "freatisch_zand",
    build_layers("freatisch_zand", [(8.0, 3.0), (25.0, 10.0)], [(1.0, 50.0, 0.02)]),
)
THICK = LayerStack(
    "freatisch_zand",
    build_layers("freatisch_zand", [(10.0, 10.0), (20.0, 15.0)], [(2.0, 200.0, 0.01)]),
)
SIX_AQ = LayerStack(
    "deklaag",
    build_layers(
        "deklaag",
        [(20.0, 15.0), (15.0, 20.0), (30.0, 25.0), (20.0, 10.0), (25.0, 30.0), (40.0, 20.0)],
        [(3.0, 300.0, 0.05), (3.0, 800.0, 0.01), (5.0, 2000.0, 0.01), (4.0, 5000.0, 0.01),
         (5.0, 3000.0, 0.01), (6.0, 10000.0, 0.01)],
    ),
)  # fmt: skip

CASES = {
    "standaard, vaste T": p3.Params(convertible=False),
    "standaard, meebewegende T": p3.Params(),
    "deklaag, put pakket 1, kwel, doorlopende rand": p3.Params(
        stack=CLAY, seepage=True, boundary="doorlopend",
        wells=(p3.WellGroup("bem", ((20, 20),), "pakket1", 0.0, 500.0),),
    ),
    "zone, rand per laag": p3.Params(
        zones=(
            p3.Zone(
                "z", tuple((r, c) for r in range(10, 25) for c in range(5, 20)), ZONE_STACK, 5.5
            ),
        ),
        boundary="per_laag", boundary_layers=(None, 3.8), boundary_kinds=("gesloten", "doorlopend"),
    ),
    "freatische put, natte dikte 10 -> 7,7 m": p3.Params(
        stack=THICK, wells=(p3.WellGroup("bem", ((20, 20),), "freatisch", 0.0, 200.0),)
    ),
    "put in het zuidoosten, RD-locatie (rijvolgorde MF6)": p3.Params(
        wells=(p3.WellGroup("bem", ((4, 33),), "pakket1", 0.0, 100.0),),
        origin_x=155000.0, origin_y=463000.0,
    ),
    "damwanden: lekkende bouwput tot pakket 1, ondoorlatend scherm freatisch": p3.Params(
        wells=(p3.WellGroup("bem", ((19, 19),), "pakket1", 0.0, 150.0),),
        walls=(
            p3.Wall("bouwput", outline_faces({(r, c) for r in range(18, 22)
                                              for c in range(18, 22)}, 40, 40), "pakket1", 50.0),
            p3.Wall("scherm", polyline_faces([(500.0, 100.0), (500.0, 300.0), (700.0, 400.0)],
                                             25.0, 40, 40), "freatisch", None, reference=True),
        ),
    ),
    "damwand in een deklaag tot pakket 1 (dunne MF6-toplaag)": p3.Params(
        stack=CLAY, wells=(p3.WellGroup("bem", ((20, 20),), "pakket1", 0.0, 300.0),),
        walls=(p3.Wall("bouwput", outline_faces({(r, c) for r in range(19, 23)
                                                 for c in range(19, 23)}, 40, 40), "pakket1",
                       20.0),),
    ),
    "6 pakketten, put in pakket 6, kwel": p3.Params(
        stack=SIX_AQ, seepage=True,
        wells=(p3.WellGroup("bem", ((20, 20),), "pakket6", 0.0, 800.0),),
    ),
    "bodem per cel (legger), deklaag": p3.Params(
        stack=CLAY,
        watercourses=tuple(
            dataclasses.replace(
                wc, bottom_offsets=tuple((-0.4, 0.0, 0.2)[k % 3] for k in range(len(wc.cells)))
            )
            for wc in p3.default_watercourses(1000.0, 1000.0, 25.0)
        ),
    ),
}  # fmt: skip


DATA = Path(__file__).parent / "data"
REFERENCE = np.load(DATA / "reference_cases.npz")
META = json.loads((DATA / "reference_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", list(CASES))
def test_compute_matches_reference(name: str) -> None:
    """Same heads, water balance per item (read from the MF6 listing), well table, wall flows,
    inundated cells and messages as the reference."""
    res = p3.compute(CASES[name])
    ref, i = META[name], META[name]["index"]
    assert res.engine_note.startswith("MODFLOW 6")
    assert np.max(np.abs(res.heads_ref - REFERENCE[f"c{i}_ref"])) < 1e-5
    assert np.max(np.abs(res.heads_new - REFERENCE[f"c{i}_new"])) < 1e-5
    budget = pd.DataFrame(ref["budget"]).loc[res.budget.index]
    scale = budget.abs().to_numpy().max()
    diff = (res.budget - budget).drop(index="som (balansfout)").abs().to_numpy().max()
    assert diff < 1e-5 * scale
    assert max(res.mass_balance.values()) < 1e-5
    assert res.inundated == ref["inundated"]
    assert res.warnings == ref["warnings"]
    if f"c{i}_wd" in REFERENCE:
        assert np.max(np.abs(res.well_drawdown - REFERENCE[f"c{i}_wd"])) < 1e-4
    if ref["wells"] is not None:
        delivered = res.wells.pop("Q opzet geleverd [m³/d]")
        np.testing.assert_allclose(delivered, res.wells["Q opzet [m³/d]"], rtol=1e-9)
        assert np.allclose(res.wells.select_dtypes("number"), np.array(ref["wells"], float),
                           atol=1e-4, equal_nan=True)  # fmt: skip
    if ref["walls"] is not None:
        assert np.allclose(res.walls.select_dtypes("number"), np.array(ref["walls"], float),
                           atol=1e-5 * scale)  # fmt: skip


def test_newton_differs_only_by_upstream_weighting(tmp_path: Path) -> None:
    """MF6 Newton weights the saturated thickness upstream instead of the harmonic mean: a
    few cm in the well cell at 2.3 m drawdown, MF6's own two formulations differ that much."""
    par = CASES["freatische put, natte dikte 10 -> 7,7 m"]
    res = p3.compute(par)
    h = mf6.run(par, "new", tmp_path, newton=True)
    assert np.max(np.abs(h - res.heads_new)) < 0.05
    assert 0.01 < np.max(np.abs(h - res.heads_new))  # really the other formulation


def test_without_convertible_equals_single_run(tmp_path: Path) -> None:
    par = dataclasses.replace(CASES["freatische put, natte dikte 10 -> 7,7 m"], convertible=False)
    res = p3.compute(par)
    h = mf6.run(par, "new", tmp_path)
    assert np.max(np.abs(h - res.heads_new)) < 1e-9


def test_missing_mf6_gives_a_clear_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mf6, "_exe", lambda: None)
    with pytest.raises(RuntimeError, match="niet gevonden"):
        p3.compute(p3.Params())


def test_failing_mf6_gives_a_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mf6, "_run", lambda ws: (False, "fout"))
    with pytest.raises(RuntimeError, match="geen oplossing"):
        p3.compute(p3.Params())


def test_head_file_reader(tmp_path: Path) -> None:
    """The binary head file layout of MF6 (DIS): header per layer, then the values."""
    nrow, ncol = 2, 3
    record = np.dtype(
        [("kstp", "<i4"), ("kper", "<i4"), ("pertim", "<f8"), ("totim", "<f8"),
         ("text", "S16"), ("ncol", "<i4"), ("nrow", "<i4"), ("ilay", "<i4"),
         ("data", "<f8", (nrow * ncol,))]
    )  # fmt: skip
    data = np.zeros(4, dtype=record)
    for k in range(4):  # two time steps of two layers: the last step counts
        data[k] = (1 + k // 2, 1, 1.0, 1.0, b"            HEAD", ncol, nrow, 1 + k % 2,
                   np.arange(6.0) + 10 * k)  # fmt: skip
    data.tofile(tmp_path / "gwt.hds")
    heads = mf6.read_heads(tmp_path / "gwt.hds", nrow, ncol)
    assert heads.shape == (2, nrow, ncol)
    np.testing.assert_array_equal(heads[1].ravel(), np.arange(6.0) + 30)


def test_model_zip_is_north_up_with_rd_origin() -> None:
    """MF6 row 1 is the north edge; DIS carries the RD corner so the model lies right in GIS."""
    import io
    import zipfile

    par = CASES["put in het zuidoosten, RD-locatie (rijvolgorde MF6)"]
    with zipfile.ZipFile(io.BytesIO(mf6.model_zip(par))) as zf:
        dis = zf.read("opzet/gwt.dis").decode().lower()
        wel = next(n for n in zf.namelist() if n.startswith("opzet/b") and "bem" in n)
        wel_text = zf.read(wel).decode()
        readme = zf.read("LEESMIJ.txt").decode()
    options = dict(line.split()[:2] for line in dis.splitlines() if "origin" in line)
    assert float(options["xorigin"]) == 155000.0 and float(options["yorigin"]) == 463000.0
    # app row 4 (south) of 40 rows -> MF6 row 36 (1-based), column 34
    assert any(line.split()[1:3] == ["36", "34"] for line in wel_text.splitlines()
               if line.strip() and line.split()[0].isdigit())  # fmt: skip
    assert "noordrand" in readme and "155000" in readme


def test_level_below_layer_bottom_keeps_fixed_t_there() -> None:
    """A GHB stage below the bottom of the convertible phreatic layer: MF6 refuses that, so
    those cells keep a fixed transmissivity (choice of Job); the rest stays convertible."""
    cells = tuple((1, c) for c in range(1, 39))  # inside the fixed outer ring
    par = p3.Params(ghb=(p3.GhbGroup("Boezem", cells, "freatisch", -0.6, -0.6, 2.0),))
    bottom = p3.layer_geometry(par)[0][2]
    assert np.all(np.asarray(bottom) > -0.6)
    res = p3.compute(par)
    assert any(w.startswith("Boezem: in 38 cellen ligt het peil") for w in res.warnings)
    assert res.budget.loc["Boezem", "opzet [m³/d]"] < 0  # it drains the model
    assert max(res.mass_balance.values()) < 1e-5
