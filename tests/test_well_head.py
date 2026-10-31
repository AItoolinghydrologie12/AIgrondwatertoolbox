"""Head in the well (Peaceman): cell head plus -Q/(2 pi T) ln(0.198 dx / r_w)."""

import dataclasses
import math

import pytest
from scipy.special import k0

from gwproducten.core.layers import LayerStack, build_layers
from gwproducten.products import peilopzet_grid as p3

ONE_LAYER = LayerStack("freatisch_zand", build_layers("freatisch_zand", [(20.0, 25.0)], []))


def well_model(cell: float, radius: float = 0.15) -> p3.Params:
    """Well in one leaky aquifer (T = 500 m2/d, c = 20 d, lambda = 100 m), edge at ~5 lambda."""
    n = int(round(990.0 / cell)) | 1  # odd: the well in the middle cell
    mid = n // 2
    return p3.Params(
        stack=ONE_LAYER, surface=20.0, recharge=0.0, cell=cell, size_x=n * cell,
        size_y=n * cell, watercourses=(), boundary="vast", boundary_head=-1.0,
        seepage=True, seepage_head=-1.0, seepage_resistance=20.0, convertible=False,
        wells=(p3.WellGroup("put", ((mid, mid),), "freatisch", 0.0, 500.0, radius),),
    )  # fmt: skip


@pytest.mark.parametrize("cell", [10.0, 30.0])
def test_drawdown_in_well_matches_de_glee(cell: float) -> None:
    """De Glee at r = r_w: s = Q/(2 pi T) K0(r_w / lambda); within 2 %, independent of the
    cell size, while the cell average is far smaller."""
    res = p3.compute(well_model(cell))
    row = res.wells.iloc[0]
    expected = 500.0 / (2 * math.pi * 500.0) * k0(0.15 / 100.0)
    assert row["Verlaging in put [m]"] == pytest.approx(expected, rel=0.02)
    assert row["Verlaging cel [m]"] < 0.7 * expected
    assert row["h put opzet [m NAP]"] == pytest.approx(-1.0 - expected, abs=0.03)


def test_radius_changes_well_head_only() -> None:
    small = p3.compute(well_model(10.0, 0.1))
    large = p3.compute(dataclasses.replace(well_model(10.0, 0.5)))
    assert small.wells.iloc[0]["Verlaging cel [m]"] == pytest.approx(
        large.wells.iloc[0]["Verlaging cel [m]"]
    )
    dif = small.wells.iloc[0]["Verlaging in put [m]"] - large.wells.iloc[0]["Verlaging in put [m]"]
    assert dif == pytest.approx(500.0 / (2 * math.pi * 500.0) * math.log(5.0), rel=1e-9)


def test_unreachable_well_head_warns() -> None:
    """A thin layer and a large rate: the head in the well lies below the layer bottom."""
    thin = LayerStack("freatisch_zand", build_layers("freatisch_zand", [(3.0, 5.0)], []))
    par = dataclasses.replace(well_model(10.0), stack=thin, surface=0.0, boundary_head=-1.0)
    res = p3.compute(par)
    assert any("niet haalbaar" in w for w in res.warnings)
