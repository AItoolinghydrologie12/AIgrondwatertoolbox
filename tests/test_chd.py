"""Validation of the fixed-head groups (CHD) in the Grondwatertoolbox: exact parabola, equal to
a GHB with a tiny resistance, budget per group, two layers, warnings and input checks."""

import dataclasses

import numpy as np
import pytest

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
    """Model without watercourses, high surface (surface drainage stays inactive), fixed
    transmissivity (linear benchmarks)."""
    base = dict(
        stack=ONE_LAYER, surface=20.0, recharge=0.0, size_x=1000.0, size_y=1000.0, cell=25.0,
        boundary="gesloten", watercourses=(), convertible=False,
    )  # fmt: skip
    base.update(kw)
    return p3.Params(**base)


def test_strip_between_two_chd_columns_is_exact_parabola() -> None:
    """One layer, closed boundary, CHD in the first and last column, recharge in between (not
    on the CHD cells). FV is exact for the quadratic h = H + N/(2T)(x - x0)(x1 - x), and both
    CHD groups take half of the recharge."""
    N, H, cell, nrow, L = 0.001, -1.0, 25.0, 4, 500.0
    ncol = int(L / cell)
    par = bare(
        size_x=L, size_y=nrow * cell, recharge=N,
        chd=(p3.ChdGroup("west", column(0, nrow), "freatisch", H, H),
             p3.ChdGroup("oost", column(ncol - 1, nrow), "freatisch", H, H)),
    )  # fmt: skip
    res = p3.compute(par)
    T = ONE_LAYER.nodes[0].T
    x0, x1 = cell / 2, L - cell / 2
    expected = H + N / (2 * T) * (res.x - x0) * (x1 - res.x)
    assert res.heads_ref[0, 1] == pytest.approx(expected, rel=1e-9, abs=1e-9)
    recharge = N * (L - 2 * cell) * nrow * cell  # CHD cells get no recharge (as MODFLOW 6)
    assert res.budget.loc["neerslag", "referentie [m³/d]"] == pytest.approx(recharge)
    for name in ("west", "oost"):
        assert res.budget.loc[name, "referentie [m³/d]"] == pytest.approx(-recharge / 2)
    assert max(res.mass_balance.values()) < 1e-8


def test_chd_equals_ghb_with_tiny_resistance() -> None:
    """A CHD is the limit of a GHB with c -> 0: same heads (within 1 mm at c = 0.01 d) and
    nearly the same flux, with two layers, recharge and a well."""
    cells = column(0, 40)
    common = dict(
        stack=TWO_LAYER, recharge=0.001,
        wells=(p3.WellGroup("put", ((20, 30),), "pakket1", 0.0, 300.0),),
    )  # fmt: skip
    chd = bare(chd=(p3.ChdGroup("boezem", cells, "pakket1", -1.0, -1.2),), **common)
    ghb = bare(ghb=(p3.GhbGroup("boezem", cells, "pakket1", -1.0, -1.2, 0.01),), **common)
    r_chd, r_ghb = p3.compute(chd), p3.compute(ghb)
    assert np.max(np.abs(r_chd.heads_ref - r_ghb.heads_ref)) < 1e-3
    assert np.max(np.abs(r_chd.heads_new - r_ghb.heads_new)) < 1e-3
    for col in ("referentie [m³/d]", "opzet [m³/d]"):
        q_chd, q_ghb = r_chd.budget.loc["boezem", col], r_ghb.budget.loc["boezem", col]
        assert q_chd == pytest.approx(q_ghb, rel=1e-3)
    assert max(r_chd.mass_balance.values()) < 1e-8


def test_chd_head_change_gives_rise_and_budget_row() -> None:
    """Closed model with only a CHD: the head follows the CHD everywhere; the CHD has its own
    row in the water balance (zero flow at equilibrium)."""
    par = bare(
        stack=TWO_LAYER, size_x=500.0, size_y=100.0,
        chd=(p3.ChdGroup("plas", column(0, 4), "pakket1", -1.0, -0.5),),
    )  # fmt: skip
    res = p3.compute(par)
    assert res.rise == pytest.approx(np.full_like(res.rise, 0.5), rel=1e-9)
    assert res.budget.loc["plas"].abs().max() < 1e-8
    assert list(res.budget.index).index("plas") < list(res.budget.index).index("rand")


def test_object_in_a_chd_cell_gives_warning() -> None:
    """A well in a CHD cell of the same layer does nothing (as MODFLOW 6): warned; in another
    layer it works and there is no warning."""
    chd = (p3.ChdGroup("plas", column(0, 4), "freatisch", -1.0, -1.0),)
    same = bare(stack=TWO_LAYER, size_x=500.0, size_y=100.0, chd=chd,
                wells=(p3.WellGroup("put", ((1, 0),), "freatisch", 0.0, 50.0),))  # fmt: skip
    res = p3.compute(same)
    assert any("plas" in w and "put" in w for w in res.warnings)
    assert res.budget.loc["put", "opzet [m³/d]"] == 0.0
    other = dataclasses.replace(same, wells=(p3.WellGroup("put", ((1, 0),), "pakket1", 0.0, 50.0),))
    res = p3.compute(other)
    assert not any("plas" in w for w in res.warnings)
    assert res.budget.loc["put", "opzet [m³/d]"] == pytest.approx(-50.0)


def test_chd_groups_hold_their_heads_and_balance() -> None:
    """CHD groups (also on the fixed outer ring) as separate MF6 CHD packages: the cells keep
    their heads, each group has its own budget row and the budget closes."""
    par = dataclasses.replace(
        p3.Params(),
        chd=(p3.ChdGroup("plas", tuple((r, c) for r in range(15, 20) for c in range(0, 6)),
                         "pakket1", 3.0, 3.3),
             p3.ChdGroup("kanaal", column(39, 40), "freatisch", 3.6, 3.6)),
    )  # fmt: skip
    res = p3.compute(par)
    keys = list(res.node_labels)
    plas = res.heads_new[keys.index("pakket1"), 15:20, 0:6]
    np.testing.assert_allclose(plas, 3.3, atol=1e-9)
    np.testing.assert_allclose(res.heads_new[keys.index("freatisch"), :, 39], 3.6, atol=1e-9)
    for name in ("plas", "kanaal", "rand"):
        assert name in res.budget.index
    scale = res.budget.abs().to_numpy().max()
    assert abs(res.budget.loc["som (balansfout)", "opzet [m³/d]"]) < 1e-4 * scale


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"chd": (p3.ChdGroup("plas", ((0, 0),), "pakket9", 1.0, 1.0),)}, "laag"),
        ({"chd": (p3.ChdGroup("plas", ((99, 0),), "freatisch", 1.0, 1.0),)}, "buiten"),
        ({"chd": (p3.ChdGroup("plas", (), "freatisch", 1.0, 1.0),)}, "geen cellen"),
        ({"chd": (p3.ChdGroup("Sloot", ((0, 0),), "freatisch", 1.0, 1.0),)}, "uniek"),
        (
            {"chd": (p3.ChdGroup("a", ((0, 0), (0, 1)), "freatisch", 1.0, 1.0),
                     p3.ChdGroup("b", ((0, 1),), "freatisch", 1.0, 1.0))},
            "meer dan één CHD",
        ),
    ],
)  # fmt: skip
def test_invalid_chd_rejected(change: dict, fragment: str) -> None:
    with pytest.raises(ParamError) as err:
        dataclasses.replace(p3.Params(), **change).validate()
    assert any(fragment in m for m in err.value.messages)
