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
