"""Convertible layers (MODFLOW 6 ICELLTYPE > 0): transmissivity = k x saturated thickness,
checked with MODFLOW 6 against Dupuit and limit cases."""

import dataclasses
from pathlib import Path

import numpy as np
import pytest

from gwproducten.core.layers import LayerStack, build_layers
from gwproducten.numeric.grid2d import (
    MIN_SATURATED_FRACTION,
    FluxBoundary,
    HeadBoundary,
)
from gwproducten.products import peilopzet_grid as p3
from gwproducten.products.peilopzet_grid import mf6

pytestmark = pytest.mark.skipif(not mf6.available(), reason="mf6 niet geïnstalleerd")


def one_aquifer(D: float, k: float, surface: float, **kw) -> p3.Params:
    """Phreatic sand of thickness D on an impermeable base; geometry only (not validated)."""
    stack = LayerStack("freatisch_zand", build_layers("freatisch_zand", [(D, k)], []))
    return p3.Params(stack=stack, surface=surface, watercourses=(), **kw)


def solve(par: p3.Params, boundaries, tmp_path: Path, fluxes=(), fixed=(None,)):
    T, _ = p3._node_fields(par)
    return mf6.solve(par, list(boundaries), list(fluxes), fixed, T, p3._convertible(par),
                     tmp_path)  # fmt: skip


def ends(ncol: int, head: float) -> HeadBoundary:
    """Very conductive GHB in the first and last column of a one-row grid."""
    keys = np.array([0, ncol - 1])
    return HeadBoundary("rand", "ghb", keys, np.full(2, 1e9), np.full(2, head), np.full(2, head))


def test_phreatic_strip_matches_dupuit(tmp_path: Path) -> None:
    """Recharge between two fixed heads on an impermeable base at z = 0:
    h^2 = h0^2 + (N/k) x (L - x). A fixed T = k D gives a much lower mound."""
    k, N, h0, cell, ncol = 5.0, 0.002, 5.0, 10.0, 101
    L = (ncol - 1) * cell
    par = one_aquifer(20.0, k, 20.0, recharge=N, size_x=ncol * cell, size_y=cell, cell=cell,
                      boundary_head=h0)  # fmt: skip
    sol, attempt = solve(par, [ends(ncol, h0)], tmp_path / "conv")
    assert attempt == "standaard"
    x = np.arange(ncol) * cell
    expected = np.sqrt(h0**2 + N / k * x * (L - x))
    np.testing.assert_allclose(sol.heads[0, 0], expected, atol=0.01)
    assert sol.heads[0, 0].max() == pytest.approx(np.sqrt(125.0), abs=0.01)  # 11.18 m
    assert sol.mass_balance_error < 1e-5
    fixed, _ = solve(dataclasses.replace(par, convertible=False), [ends(ncol, h0)],
                     tmp_path / "fixed")  # fmt: skip
    assert fixed.heads[0, 0].max() < 8.0  # 7.5 m with T = k D: far off


def test_confined_layer_is_unchanged(tmp_path: Path) -> None:
    """Head above the top everywhere: the convertible layer behaves as a fixed one."""
    par = one_aquifer(20.0, 15.0, -5.0, recharge=0.001, size_x=41 * 25.0, size_y=25.0,
                      cell=25.0, boundary_head=0.0)  # fmt: skip
    conv, _ = solve(par, [ends(41, 0.0)], tmp_path / "conv")
    fixed, _ = solve(dataclasses.replace(par, convertible=False), [ends(41, 0.0)],
                     tmp_path / "fixed")  # fmt: skip
    np.testing.assert_allclose(conv.heads, fixed.heads, atol=1e-6)
    assert not conv.dry.any()


def test_too_large_well_delivers_what_the_layer_can(tmp_path: Path) -> None:
    """A well asking 2000 m3/d from 2 m saturated sand (k 10): the layer falls dry around it.
    MF6 Newton with AUTO_FLOW_REDUCE lowers the rate to what the layer delivers: near the
    Dupuit maximum pi k (H^2 - h^2) / ln(R / r) with h = 0 (measured 1.24 times it: Newton
    takes the saturated thickness upstream and the fixed ring is square); the budget
    balances."""
    n, cell = 21, 25.0
    par = one_aquifer(10.0, 10.0, 10.0, recharge=0.0, size_x=n * cell, size_y=n * cell,
                      cell=cell, boundary_head=2.0)  # fmt: skip
    well = FluxBoundary("put", np.array([n // 2 * n + n // 2]), np.array([-2000.0]))
    sol, attempt = solve(par, [], tmp_path, fluxes=[well], fixed=(2.0,))
    assert attempt == "Newton, debiet verlaagd"
    q = -float(sol.well_rates["put"].sum())
    R = 10 * cell  # distance to the fixed ring
    r_e = 0.198 * cell  # Peaceman radius of the cell
    q_dupuit = np.pi * 10.0 * (2.0**2 - 0.0) / np.log(R / r_e)
    assert 0.8 * q_dupuit < q < 1.5 * q_dupuit  # about 40 m3/d instead of 2000
    assert sol.fluxes["put"] == pytest.approx(-q, rel=1e-4)  # balance uses the delivered rate
    assert sol.mass_balance_error < 1e-4
    assert sol.heads[0, n // 2, n // 2] < 0.1 * 2.0  # well cell (nearly) dry
    assert sol.transmissivity[0, n // 2, n // 2] >= 10.0 * MIN_SATURATED_FRACTION * 10.0


def test_wells_falling_dry_give_result_and_warning() -> None:
    """Two wells in a thin, poor phreatic layer (T = 5 m2/d): the layer falls dry around
    them; the app still gives a balanced result, says the wells deliver less and shows the
    delivered rate in the well table."""
    for q in (150.0, 3000.0):
        wells = (p3.WellGroup("bem", ((20, 20), (20, 21)), "freatisch", 0.0, q),)
        res = p3.compute(p3.Params(wells=wells))
        assert max(res.mass_balance.values()) < 1e-4, q
        assert res.engine_note.endswith("(Newton, debiet verlaagd)")
        assert any("kan het gevraagde debiet niet leveren" in w for w in res.warnings)
        delivered = res.wells["Q opzet geleverd [m³/d]"]
        assert (delivered < q).all() and (delivered > 0).all()
        assert res.budget.loc["bem", "opzet [m³/d]"] == pytest.approx(-delivered.sum(), rel=1e-3)
