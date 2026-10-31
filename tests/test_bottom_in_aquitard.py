"""Watercourse bottom inside a deeper aquitard: bottom through the rest of that aquitard to
the aquifer below (strip on a slab), banks inside the aquitard neglected."""

import dataclasses

import pytest

from gwproducten.analytic.boundaries import strip_resistance
from gwproducten.core.layers import LayerStack, build_layers
from gwproducten.products import peilopzet_grid as p3
from gwproducten.products._layered import Ditch, Subsoil, run

# phreatic aquifer 0-5 m, aquitard 5-9 m (c = 400 d), pakket 1 9-29 m
STACK = LayerStack(
    "freatisch_zand",
    build_layers("freatisch_zand", [(5.0, 2.0), (20.0, 15.0)], [(4.0, 400.0, 0.01)]),
)
SUB = Subsoil(stack=STACK, w=100.0)


def ditch(depth: float, water: float = 1.5) -> Ditch:
    return Ditch(
        name="s", x=0.0, level=0.2, width=2.0, bottom_depth=depth, water_depth=water,
        entry_resistance=1.0,
    )  # fmt: skip


def test_bottom_connects_through_rest_of_aquitard() -> None:
    d = ditch(6.0)  # water 4.5-6 m: banks 0.5 m in the phreatic aquifer, bottom 1 m in clay
    parts = dict(SUB.conductance_parts(d))
    assert set(parts) == {0, 1}
    lay = STACK.layers[1]
    expected = 1.0 / (1.0 / 2.0 + strip_resistance(2.0, 3.0, lay.k_h, lay.thickness / lay.c))
    assert parts[1] == pytest.approx(expected, rel=1e-12)
    assert parts[0] == pytest.approx(2 * 0.5 / 1.0)  # banks: c_wg / u, no radial term
    assert "bodem via Scheidende laag 1 naar Pakket 1" in SUB.connection_label(d)
    assert not SUB.ditch_errors(d)


def test_bottom_connection_grows_as_less_clay_remains() -> None:
    values = [dict(SUB.conductance_parts(ditch(z)))[1] for z in (6.0, 7.0, 8.0, 8.9)]
    assert values == sorted(values)
    # just above the aquitard base vs just inside pakket 1: same order of magnitude
    above = SUB.conductance(ditch(8.99))
    inside = SUB.conductance(ditch(9.01))
    assert 0.3 < above / inside < 1.0


@pytest.mark.parametrize(("depth", "low", "high"), [(6.0, 0.9, 1.25), (7.0, 0.3, 1.0)])
def test_against_xz_cross_section(depth: float, low: float, high: float) -> None:
    """Analytic exchange / x-z exchange: +13 % with only the bottom in the clay; about 2.5x
    too low when the whole wetted profile is in the clay (banks in the clay neglected)."""
    r = run(SUB, (ditch(depth),), None)
    ratio = float(r.analytic.Q_ditches[0] / r.xz.Q_ditches[0])
    assert low < ratio < high


def test_grid_model_with_bottom_in_aquitard_runs_and_warns() -> None:
    wcs = tuple(
        dataclasses.replace(wc, bottom=p3.DEFAULT_SURFACE - 6.0, stage_ref=-0.5, stage_new=-0.3)
        if wc.name == "Wetering"
        else wc
        for wc in p3.DEFAULT_WATERCOURSES
    )
    par = dataclasses.replace(p3.Params(), stack=STACK, watercourses=wcs)
    res = p3.compute(par)
    assert max(res.mass_balance.values()) < 1e-8
    assert any("Wetering" in w and "Scheidende laag 1" in w for w in res.warnings)
    assert "bodem via" in res.conductances.loc[0, "Sluit aan op"]
