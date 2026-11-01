"""The app against MODFLOW 6 (flopy) on the same grid and boundaries. Skipped when flopy or
the mf6 executable is missing (``pip install -e .[mf6]`` and ``get-modflow :flopy``).

MF6 gets every layer as a model layer (aquitards with K33 = D/c), so this checks the
quasi-3D resistances, the harmonic mean, the RIV/DRN/GHB/WEL/CHD terms, the continuing
boundary and the convertible transmissivity. The watercourse conductances are the app's own
(the radial resistance is checked against the x-z model in test_radial_anisotropy.py)."""

import dataclasses
from pathlib import Path

import numpy as np
import pytest

from gwproducten.core.grids import outline_faces, polyline_faces
from gwproducten.core.layers import LayerStack, build_layers
from gwproducten.products import peilopzet_grid as p3
from gwproducten.products.peilopzet_grid import mf6

pytestmark = pytest.mark.skipif(not mf6.available(), reason="flopy/mf6 niet geïnstalleerd")

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


@pytest.mark.parametrize("name", list(CASES))
def test_heads_equal_modflow6(name: str, tmp_path: Path) -> None:
    """Measured: all heads within 1e-6 m of MODFLOW 6 (standard formulation)."""
    par = CASES[name]
    res = p3.compute(par)
    for stage, heads in (("ref", res.heads_ref), ("new", res.heads_new)):
        h = mf6.run(par, stage, tmp_path / stage)
        assert np.max(np.abs(h - heads)) < 1e-5, stage


def test_newton_differs_only_by_upstream_weighting(tmp_path: Path) -> None:
    """MF6 Newton weights the saturated thickness upstream instead of the harmonic mean: a
    few cm in the well cell at 2.3 m drawdown, MF6's own two formulations differ that much."""
    par = CASES["freatische put, natte dikte 10 -> 7,7 m"]
    res = p3.compute(par)
    h = mf6.run(par, "new", tmp_path, newton=True)
    assert np.max(np.abs(h - res.heads_new)) < 0.05


def test_without_convertible_matches_mf6_confined(tmp_path: Path) -> None:
    par = dataclasses.replace(CASES["freatische put, natte dikte 10 -> 7,7 m"], convertible=False)
    res = p3.compute(par)
    h = mf6.run(par, "new", tmp_path)
    assert np.max(np.abs(h - res.heads_new)) < 1e-5


@pytest.mark.parametrize("name", list(CASES))
def test_compute_with_mf6_engine_equals_own_solver(name: str) -> None:
    """The whole product with MODFLOW 6 as engine: same heads, same water balance per item
    (read from the MF6 listing), same well table and messages as the own solver."""
    own = p3.compute(CASES[name])
    mf = p3.compute(dataclasses.replace(CASES[name], engine="mf6"))
    assert (own.engine, mf.engine) == ("eigen", "mf6")
    assert mf.engine_note.startswith("MODFLOW 6")
    assert np.max(np.abs(mf.heads_new - own.heads_new)) < 1e-5
    assert np.max(np.abs(mf.rise - own.rise)) < 1e-5
    scale = own.budget.abs().to_numpy().max()
    diff = (mf.budget - own.budget).drop(index="som (balansfout)").abs().to_numpy().max()
    assert diff < 1e-4 * scale
    assert max(mf.mass_balance.values()) < 1e-6
    assert mf.inundated == own.inundated
    if own.well_drawdown is not None:
        assert np.max(np.abs(mf.well_drawdown - own.well_drawdown)) < 1e-4
        assert np.allclose(mf.wells.select_dtypes("number"), own.wells.select_dtypes("number"),
                           atol=1e-4, equal_nan=True)  # fmt: skip
    if own.walls is not None:
        assert np.allclose(mf.walls.select_dtypes("number"), own.walls.select_dtypes("number"),
                           atol=1e-4 * scale)  # fmt: skip
    strip = [w for w in own.warnings if "geconvergeerd" not in w]
    assert [w for w in mf.warnings if "geconvergeerd" not in w] == strip


def test_mf6_failure_falls_back_to_own_solver(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("MODFLOW 6 is niet geconvergeerd (standaard en Newton).")

    monkeypatch.setattr(mf6, "solve", fail)
    res = p3.compute(dataclasses.replace(p3.Params(), engine="mf6"))
    assert res.engine == "eigen"
    assert "niet geconvergeerd" in res.engine_note and "eigen solver" in res.engine_note
    np.testing.assert_allclose(res.heads_new, p3.compute(p3.Params()).heads_new)


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
