"""Convertible layers (MODFLOW 6 ICELLTYPE > 0): transmissivity = k x saturated thickness."""

import numpy as np
import pytest

from gwproducten.numeric.grid2d import (
    MIN_SATURATED_FRACTION,
    Convertible,
    FluxBoundary,
    HeadBoundary,
    solve_grid,
)


def ends(ncol: int, head: float) -> HeadBoundary:
    """Very conductive GHB in the first and last column of a one-row grid."""
    keys = np.array([0, ncol - 1])
    return HeadBoundary("rand", "ghb", keys, np.full(2, 1e9), np.full(2, head), np.full(2, head))


def test_phreatic_strip_matches_dupuit() -> None:
    """Recharge between two fixed heads on an impermeable base at z = 0:
    h^2 = h0^2 + (N/k) x (L - x). A fixed T = k D gives a much lower mound."""
    k, N, h0, cell, ncol = 5.0, 0.002, 5.0, 10.0, 101
    L = (ncol - 1) * cell
    conv = Convertible(k, 20.0, 0.0)
    sol = solve_grid([k * h0], [], 1, ncol, cell, N, [ends(ncol, h0)], convertible=[conv])
    assert sol.converged
    x = np.arange(ncol) * cell
    expected = np.sqrt(h0**2 + N / k * x * (L - x))
    np.testing.assert_allclose(sol.heads[0, 0], expected, atol=0.01)
    assert sol.heads[0, 0].max() == pytest.approx(np.sqrt(125.0), abs=0.01)  # 11.18 m
    assert sol.mass_balance_error < 1e-8
    fixed = solve_grid([k * 20.0], [], 1, ncol, cell, N, [ends(ncol, h0)])
    assert fixed.heads[0, 0].max() < 8.0  # 7.5 m with T = k D: far off


def test_confined_layer_is_unchanged() -> None:
    """Head above the top everywhere: the convertible layer behaves as a fixed one."""
    args = ([300.0], [], 1, 41, 25.0, 0.001, [ends(41, 0.0)])
    fixed = solve_grid(*args)
    conv = solve_grid(*args, convertible=[Convertible(15.0, -5.0, -25.0)])
    np.testing.assert_allclose(conv.heads, fixed.heads, atol=1e-9)
    assert not conv.dry.any()


def test_drying_cells_converge_at_minimum_thickness() -> None:
    """A large well in a thin phreatic layer: cells fall dry, the model holds them at the
    minimum saturated thickness and still balances."""
    n, cell = 21, 25.0
    conv = Convertible(10.0, 10.0, 0.0)
    well = FluxBoundary("put", np.array([n // 2 * n + n // 2]), np.array([-2000.0]))
    sol = solve_grid(
        [20.0], [], n, n, cell, 0.0, [], fixed_head=2.0, fluxes=[well], convertible=[conv],
        max_iter=200,
    )  # fmt: skip
    assert sol.converged
    assert sol.dry[0, n // 2, n // 2]
    t_min = 10.0 * MIN_SATURATED_FRACTION * 10.0
    assert sol.transmissivity[0, n // 2, n // 2] == pytest.approx(t_min)
    assert sol.mass_balance_error < 1e-6
