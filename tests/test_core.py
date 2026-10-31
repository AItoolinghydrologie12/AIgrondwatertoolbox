"""Unit tests for the shared calculation core."""

import math

import numpy as np
import pytest

from gwproducten.analytic.boundaries import (
    ditch_conductance,
    equivalent_length,
    radial_resistance,
    strip_resistance,
    wetted_perimeter,
)
from gwproducten.analytic.multilayer import LayerSystem, LineElement, solve
from gwproducten.core.grids import refined_axis
from gwproducten.core.layers import LayerStack, build_layers
from gwproducten.numeric.fd1d import solve_fd1d


def test_refined_axis_contains_points_and_respects_spacing() -> None:
    x = refined_axis(-1000, 1000, [0.0, 37.5], dx_min=0.5, dx_max=20, growth=1.2)
    assert x[0] == -1000 and x[-1] == 1000
    assert 0.0 in x and 37.5 in x
    dx = np.diff(x)
    assert (dx > 0).all()
    assert dx.max() <= 20 * 1.0001
    assert dx[np.searchsorted(x, 0.0)] <= 0.5 * 1.5


def test_refined_axis_is_fast_for_tiny_spacing() -> None:
    x = refined_axis(-1e4, 1e4, [0.0], dx_min=0.01, dx_max=0.05, growth=1.1)
    assert x.size > 1e5


def test_conductance_entry_only_gives_classic_equivalent_length() -> None:
    # u >= D: no radial resistance, L* = kD c / B
    k, D, c, B = 10.0, 2.0, 5.0, 4.0
    assert radial_resistance(D, B, k) == 0.0
    C = ditch_conductance(c, B, D, k)
    assert C == pytest.approx(B / c)
    assert equivalent_length(k * D, C) == pytest.approx(k * D * c / B)


def test_conductance_with_radial_resistance() -> None:
    u = wetted_perimeter(2.0, 0.5)
    assert u == 3.0
    C = ditch_conductance(1.0, u, 20.0, 15.0)
    assert 1.0 / C == pytest.approx(1.0 / 3.0 + math.log(20.0 / 3.0) / (math.pi * 15.0))


def test_radial_resistance_anisotropy() -> None:
    D, u, k, B = 20.0, 3.0, 15.0, 2.0
    iso = radial_resistance(D, u, k)
    assert radial_resistance(D, u, k, 1.0, B) == iso
    a = math.sqrt(3.0)  # kv/kh = 1/3: z stretched by sqrt(3), k' = k / sqrt(3)
    expected = math.log(a * D / (B + a * (u - B))) / (math.pi * k / a)
    assert radial_resistance(D, u, k, 1 / 3, B) == pytest.approx(expected)
    values = [radial_resistance(D, u, k, kv, B) for kv in (1.0, 0.33, 0.1)]
    assert values == sorted(values)


def test_single_layer_line_sink_is_exponential() -> None:
    T, c, q = 300.0, 500.0, -5.0
    sol = solve(LayerSystem((T,), (), c), [LineElement(0.0, 0, "flux", q)])
    lam = math.sqrt(T * c)
    x = np.array([0.0, 100.0, -400.0])
    np.testing.assert_allclose(sol.head(x)[0], q * lam / (2 * T) * np.exp(-np.abs(x) / lam))


def test_green_function_is_reciprocal() -> None:
    """Maxwell–Betti: response in layer l to a source in m equals response in m to l."""
    s = LayerSystem((5.0, 300.0, 50.0), (500.0, 20.0), 100.0)
    G = s.green(np.array([0.0, 30.0, 250.0]))
    np.testing.assert_allclose(G, np.transpose(G, (1, 0, 2)), rtol=1e-12)


def test_analytic_mass_balance_and_ideal_boundary() -> None:
    s = LayerSystem((5.0, 300.0), (500.0,), 100.0)
    el = [LineElement(0.0, 1, "flux", -5.0), LineElement(-80.0, 1, "head", 0.0)]
    sol = solve(s, el)
    assert sol.mass_balance_error() < 1e-12
    assert abs(sol.head([-80.0])[1, 0]) < 1e-12


def test_fd1d_matches_three_layer_analytic() -> None:
    s = LayerSystem((5.0, 300.0, 100.0), (500.0, 50.0), 150.0)
    el = [
        LineElement(0.0, 2, "flux", -4.0),
        LineElement(40.0, 0, "head", 0.0, 1.5),
        LineElement(-120.0, 1, "head", 0.3, 4.0),
    ]
    a = solve(s, el)
    r = solve_fd1d(s, el, dx_min=0.25)
    assert r.mass_balance_error < 1e-10
    np.testing.assert_allclose(r.Q, a.Q, rtol=2e-3, atol=1e-5)
    exact = a.head(r.x)
    assert np.abs(r.phi - exact).max() < 2e-4 * np.abs(exact).max()


def test_strip_resistance_limits() -> None:
    """Wide strip: close to plain vertical flow t/(kv b), slightly less because water also
    spreads beyond the edges (asymptote b'/t + 2 ln4/pi). Narrow strip: spreading matters."""
    t, kh, kv = 2.0, 0.05, 0.006
    wide = strip_resistance(200.0, t, kh, kv)
    assert wide == pytest.approx(t / (kv * 200.0), rel=0.05)
    assert wide < t / (kv * 200.0)
    widths = [0.5, 1.0, 2.0, 5.0, 20.0]
    res = [strip_resistance(b, t, kh, kv) for b in widths]
    assert all(r < t / (kv * b) for r, b in zip(res, widths, strict=True))
    assert all(np.diff(res) < 0)
    assert strip_resistance(1.0, 0.0, kh, kv) == 0.0


def test_layer_stack_nodes_and_resistances() -> None:
    clay = LayerStack(
        "deklaag",
        build_layers(
            "deklaag", [(20.0, 15.0), (30.0, 25.0)], [(3.0, 500.0, 0.05), (5.0, 2000.0, 0.01)]
        ),
    )
    assert [n.key for n in clay.nodes] == ["freatisch", "pakket1", "pakket2"]
    assert clay.nodes[0].T == pytest.approx(0.15)
    # aquitard c plus D/2 / kv of each aquifer node (kv = 0.33 kh by default); the clay top
    # layer carries its full c, without a half-layer term
    h1, h2 = 0.5 * 20.0 / (0.33 * 15.0), 0.5 * 30.0 / (0.33 * 25.0)
    assert clay.node_resistances() == pytest.approx((500.0 + h1, h1 + 2000.0 + h2))
    assert clay.aquifer_nodes() == [1, 2]
    assert clay.layer_at_depth(2.9) == 0 and clay.layer_at_depth(3.1) == 1
    assert clay.errors() == []

    sand = LayerStack(
        "freatisch_zand",
        build_layers("freatisch_zand", [(5.0, 1.0), (20.0, 15.0)], [(2.0, 500.0, 0.004)]),
    )
    assert [lay.name for lay in sand.layers] == [
        "Freatisch pakket",
        "Scheidende laag 1",
        "Pakket 1",
    ]
    assert [n.T for n in sand.nodes] == [5.0, 300.0]
    assert sand.node_resistances() == pytest.approx(
        (0.5 * 5.0 / 0.33 + 500.0 + 0.5 * 20.0 / (0.33 * 15.0),)
    )
    iso = LayerStack(
        "freatisch_zand",
        build_layers("freatisch_zand", [(5.0, 1.0, 1.0), (20.0, 15.0, 1.0)], [(2.0, 500.0, 0.004)]),
    )
    assert iso.node_resistances() == pytest.approx((2.5 + 500.0 + 10.0 / 15.0,))
    assert sand.aquifer_nodes() == [0, 1]
