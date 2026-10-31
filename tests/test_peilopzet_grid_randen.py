"""Validation of the extra boundaries in Peilopzet 2D: wells (WEL), general head (GHB),
seepage through the bottom (kwel-onderrand) and the model boundary per layer."""

import dataclasses
import math

import numpy as np
import pytest
from scipy.special import k0

from gwproducten.core.layers import LayerStack, build_layers
from gwproducten.core.params import ParamError
from gwproducten.products import peilopzet_grid as p3

ONE_LAYER = LayerStack("freatisch_zand", build_layers("freatisch_zand", [(20.0, 25.0)], []))
TWO_LAYER = LayerStack(
    "freatisch_zand",
    build_layers("freatisch_zand", [(5.0, 5.0), (20.0, 15.0)], [(2.0, 100.0, 0.02)]),
)


def column(col: int, nrow: int) -> tuple[tuple[int, int], ...]:
    return tuple((r, col) for r in range(nrow))


def bare(**kw) -> p3.Params:
    """Model without watercourses, high surface (surface drainage stays inactive)."""
    base = dict(
        stack=ONE_LAYER, surface=20.0, recharge=0.0, size_x=1000.0, size_y=1000.0, cell=25.0,
        boundary="gesloten", watercourses=(),
    )  # fmt: skip
    base.update(kw)
    return p3.Params(**base)


def test_seepage_bottom_uniform_equilibrium() -> None:
    """Closed model, no lateral flow: kwel from phi through c_kwel and c between the layers,
    out through the surface drainage w_mv. Series resistances: exact."""
    phi, c_k, w = 1.0, 400.0, 50.0
    par = bare(
        stack=TWO_LAYER, surface=0.0, size_x=100.0, size_y=100.0, mv_resistance=w,
        seepage=True, seepage_head=phi, seepage_resistance=c_k,
    )  # fmt: skip
    res = p3.compute(par)
    c_between = TWO_LAYER.node_resistances()[0]
    q = phi / (c_k + c_between + w)  # [m/d]
    assert res.heads_ref[0] == pytest.approx(np.full_like(res.heads_ref[0], q * w), rel=1e-9)
    assert res.heads_ref[1] == pytest.approx(
        np.full_like(res.heads_ref[1], q * (w + c_between)), rel=1e-9
    )
    area = par.size_x * par.size_y
    assert res.budget.loc["kwel onderrand", "referentie [m³/d]"] == pytest.approx(q * area)
    assert max(res.mass_balance.values()) < 1e-8
    assert np.allclose(res.rise, 0.0)  # phi equal in both runs


def test_ghb_strip_with_recharge_is_exact_parabola() -> None:
    """One layer, closed boundary, GHB in the first column, recharge everywhere. FV is exact
    for the quadratic: h(x) = h0 + N/(2T)[(L - x0)^2 - (L - x)^2], h0 = H + N L cell / C."""
    N, H, c_g, cell = 0.001, -1.0, 2.0, 25.0
    nrow, L = 4, 500.0
    par = bare(
        size_x=L, size_y=nrow * cell, recharge=N,
        ghb=(p3.GhbGroup("boezem", column(0, nrow), "freatisch", H, H, c_g),),
    )  # fmt: skip
    res = p3.compute(par)
    T = ONE_LAYER.nodes[0].T
    C = cell**2 / c_g
    h0 = H + N * L * cell / C
    x0 = cell / 2
    expected = h0 + N / (2 * T) * ((L - x0) ** 2 - (L - res.x) ** 2)
    assert res.heads_ref[0, 1] == pytest.approx(expected, rel=1e-9)
    assert res.budget.loc["boezem", "referentie [m³/d]"] == pytest.approx(-N * L * nrow * cell)
    assert max(res.mass_balance.values()) < 1e-8


def test_ghb_head_change_gives_rise() -> None:
    par = bare(
        size_x=500.0, size_y=100.0, recharge=0.0,
        ghb=(p3.GhbGroup("plas", column(0, 4), "freatisch", -1.0, -0.5, 1.0),),
    )  # fmt: skip
    res = p3.compute(par)
    # closed model with only the GHB: the head follows the GHB level everywhere
    assert res.rise[0] == pytest.approx(np.full_like(res.rise[0], 0.5), rel=1e-9)


def test_well_matches_de_glee() -> None:
    """One aquifer with leakage to a fixed deep head (kwel-onderrand): the drawdown of a well
    is s = Q/(2 pi T) K0(r/lambda). Model edge 5 lambda away (fixed head = phi)."""
    Q, c_k, cell = 500.0, 20.0, 10.0
    T = ONE_LAYER.nodes[0].T  # 500 m2/d -> lambda = 100 m
    lam = math.sqrt(T * c_k)
    mid = 50  # cell centre at 505 m
    par = bare(
        cell=cell, boundary="vast", boundary_head=-1.0,
        seepage=True, seepage_head=-1.0, seepage_resistance=c_k,
        wells=(p3.WellGroup("put", ((mid, mid),), "freatisch", Q, Q),),
    )  # fmt: skip
    res = p3.compute(par)
    s = -1.0 - res.heads_ref[0, mid]  # drawdown along the row through the well
    r = np.abs(res.x - res.x[mid])
    sel = (r >= 3 * cell) & (r <= 2.5 * lam)
    analytic = Q / (2 * math.pi * T) * k0(r[sel] / lam)
    assert s[sel] == pytest.approx(analytic, rel=0.02)
    assert res.budget.loc["put", "referentie [m³/d]"] == pytest.approx(-Q)
    assert res.well_drawdown is not None
    assert res.well_drawdown[0, mid] == pytest.approx(s, abs=1e-9)  # superposition, linear
    assert max(res.mass_balance.values()) < 1e-8


def test_well_drawdown_map_is_superposition_with_nonlinear_boundaries() -> None:
    """With RIV/DRN the drawdown map is computed with the final (frozen) states; check it
    against the difference of two full runs where no state changes."""
    wells = (p3.WellGroup("bemaling", ((20, 12),), "pakket1", 0.0, 200.0),)
    par = dataclasses.replace(p3.Params(), wells=wells)
    res = p3.compute(par)
    assert res.well_drawdown is not None
    # opzet run = raised ditch + wells; ref run has no wells. Remove the opzet itself:
    no_opzet = dataclasses.replace(
        par,
        watercourses=tuple(
            dataclasses.replace(wc, stage_new=wc.stage_ref) for wc in par.watercourses
        ),
    )
    res2 = p3.compute(no_opzet)
    direct = res2.heads_ref - res2.heads_new  # drawdown by the wells alone
    assert res2.well_drawdown == pytest.approx(direct, abs=2e-3)
    assert res2.well_drawdown[1, 20, 12] > 0.0


def test_well_in_phreatic_layer_warns_on_large_drawdown() -> None:
    wells = (p3.WellGroup("put", ((20, 20),), "freatisch", 3000.0, 3000.0),)
    par = dataclasses.replace(p3.Params(), wells=wells)
    res = p3.compute(par)
    assert any("put" in w and "freatisch" in w.lower() for w in res.warnings)


def test_boundary_per_layer_equal_heads_equals_fixed() -> None:
    a = p3.compute(p3.Params())
    b = p3.compute(
        dataclasses.replace(p3.Params(), boundary="per_laag", boundary_layers=(3.8, 3.8))
    )
    assert b.heads_new == pytest.approx(a.heads_new, abs=1e-10)


def test_boundary_per_layer_closed_top_fixed_deep() -> None:
    par = dataclasses.replace(
        p3.Params(), boundary="per_laag", boundary_layers=(None, -1.2),
        seepage=True, seepage_head=-0.5, seepage_resistance=500.0,
    )  # fmt: skip
    res = p3.compute(par)
    assert res.heads_ref[1, 0, 0] == pytest.approx(-1.2)  # fixed deep ring
    assert res.heads_ref[0, 0, 0] != pytest.approx(-1.2)  # top ring free
    assert max(res.mass_balance.values()) < 1e-8


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"boundary": "per_laag", "boundary_layers": (-1.0,)}, "per laag"),
        ({"wells": (p3.WellGroup("put", ((0, 0),), "pakket9", 1.0, 1.0),)}, "laag"),
        ({"wells": (p3.WellGroup("put", ((99, 0),), "freatisch", 1.0, 1.0),)}, "buiten"),
        ({"ghb": (p3.GhbGroup("Sloot", ((0, 0),), "freatisch", 0.0, 0.0, 1.0),)}, "uniek"),
    ],
)
def test_invalid_boundaries_rejected(change: dict, fragment: str) -> None:
    with pytest.raises(ParamError) as err:
        dataclasses.replace(p3.Params(), **change).validate()
    assert any(fragment in m for m in err.value.messages)
