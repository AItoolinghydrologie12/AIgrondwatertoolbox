"""Model boundary 'doorlopend pakket': the ring cells drain through a GHB to the boundary
head with the resistance of a semi-infinite leaky aquifer outside (half cell + lambda / T)."""

import dataclasses
import math

import numpy as np
import pytest
from scipy.special import k0

from gwproducten.core.layers import LayerStack, build_layers
from gwproducten.core.params import ParamError
from gwproducten.products import peilopzet_grid as p3

ONE_LAYER = LayerStack("freatisch_zand", build_layers("freatisch_zand", [(20.0, 25.0)], []))


def well_model(boundary: str, n: int) -> p3.Params:
    """Well in one leaky aquifer (T = 500 m2/d, c = 20 d, lambda = 100 m), cell 10 m."""
    mid = n // 2
    return p3.Params(
        stack=ONE_LAYER, surface=20.0, recharge=0.0, cell=10.0, size_x=n * 10.0,
        size_y=n * 10.0, watercourses=(), boundary=boundary, boundary_head=-1.0,
        seepage=True, seepage_head=-1.0, seepage_resistance=20.0, convertible=False,
        wells=(p3.WellGroup("put", ((mid, mid),), "freatisch", 500.0, 500.0),),
    )  # fmt: skip


def de_glee_error(boundary: str, n: int) -> float:
    """Largest deviation from De Glee between 3 cells and 1.5 lambda from the well [m]."""
    res = p3.compute(well_model(boundary, n))
    mid = n // 2
    s = -1.0 - res.heads_ref[0, mid]
    r = np.abs(res.x - res.x[mid])
    sel = (r >= 30.0) & (r <= 150.0)
    expected = 500.0 / (2 * math.pi * 500.0) * k0(r[sel] / 100.0)
    return float(np.max(np.abs(s[sel] - expected)))


def test_well_near_continuing_boundary_matches_de_glee() -> None:
    """Model edge at 2 lambda: the continuing boundary stays close to the infinite-aquifer
    solution, a fixed head at that distance clearly pulls the drawdown down."""
    cont = de_glee_error("doorlopend", 41)
    fixed = de_glee_error("vast", 41)
    far = de_glee_error("vast", 99)  # edge at 5 lambda: reference for the grid error
    assert cont < 0.5 * fixed
    assert cont < far + 0.005


def test_small_continuing_model_matches_large_fixed_model() -> None:
    """Default subsoil, Wetering raised 0.2 m, no recharge (surroundings at the boundary
    head): a 1 km model with a continuing boundary against the middle of a 2.5 km model with
    a fixed boundary. Measured: continuing 1.1 cm, fixed 18 cm, closed 10 cm largest error."""
    cell = 25.0

    def model(size: float, boundary: str) -> p3.Params:
        off = (size - 1000.0) / 2 / cell
        wcs = tuple(
            dataclasses.replace(wc, cells=tuple((r + int(off), c + int(off)) for r, c in wc.cells))
            for wc in p3.default_watercourses(1000.0, 1000.0, cell)
        )
        return p3.Params(
            size_x=size, size_y=size, cell=cell, watercourses=wcs, boundary=boundary,
            convertible=False, recharge=0.0,
        )  # fmt: skip

    small = p3.compute(model(1000.0, "doorlopend"))
    big = p3.compute(model(2500.0, "vast"))
    inner = big.rise[:, 30:70, 30:70]
    fixed_small = p3.compute(model(1000.0, "vast"))
    err_cont = np.max(np.abs(small.rise - inner))
    err_fixed = np.max(np.abs(fixed_small.rise - inner))
    assert err_cont < err_fixed
    assert err_cont < 0.015  # m, of a 0.19 m rise


def test_per_layer_kinds() -> None:
    """Per layer: continuing in the deep aquifer, closed at the top."""
    par = dataclasses.replace(
        p3.Params(), boundary="per_laag", boundary_layers=(None, 3.8),
        boundary_kinds=("gesloten", "doorlopend"),
    )  # fmt: skip
    res = p3.compute(par)
    assert "rand (doorlopend)" in res.budget.index
    assert max(res.mass_balance.values()) < 1e-8
    with pytest.raises(ParamError, match="Modelrand"):
        dataclasses.replace(par, boundary_kinds=("gesloten", "anders")).validate()
