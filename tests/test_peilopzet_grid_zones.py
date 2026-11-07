"""Validation of zones in Peilopzet 2D: own layer values and surface per set of cells."""

import dataclasses

import numpy as np
import pytest

from gwproducten.core.layers import LayerStack, build_layers
from gwproducten.core.params import ParamError
from gwproducten.products import peilopzet_grid as p3
from gwproducten.products._layered import Subsoil

# The zone tests were written for a surface at 0 m NAP with ditches at -1 m NAP.
LEGACY_WATERCOURSES = (
    p3.Watercourse(
        name="Opgezette sloot", cells=tuple((r, 20) for r in range(10, 30)),
        stage_ref=-1.0, stage_new=-0.8, bottom=-1.6, width=2.0,
    ),
    p3.Watercourse(name="Sloot west", cells=tuple((r, 16) for r in range(40)), stage_ref=-1.0,
                   stage_new=-1.0, bottom=-1.4, width=1.0),
    p3.Watercourse(name="Sloot oost", cells=tuple((r, 24) for r in range(40)), stage_ref=-1.0,
                   stage_new=-1.0, bottom=-1.4, width=1.0),
)  # fmt: skip
BASE = p3.Params(
    surface=0.0, boundary_head=-1.0, seepage_head=0.0, watercourses=LEGACY_WATERCOURSES
)
ONE_LAYER = LayerStack("freatisch_zand", build_layers("freatisch_zand", [(10.0, 2.0)], []))
CLAY = LayerStack(
    "deklaag", build_layers("deklaag", [(20.0, 15.0)], [(1.0, 200.0, 0.02)])
)  # fmt: skip
THICK_CLAY = LayerStack(
    "deklaag", build_layers("deklaag", [(20.0, 15.0)], [(3.0, 800.0, 0.02)])
)  # fmt: skip


def column(col: int, rows: range) -> tuple[tuple[int, int], ...]:
    return tuple((r, col) for r in rows)


def block(rows: range, cols: range) -> tuple[tuple[int, int], ...]:
    return tuple((r, c) for r in rows for c in cols)


def all_cells(par: p3.Params) -> tuple[tuple[int, int], ...]:
    return block(range(par.nrow), range(par.ncol))


def test_zone_equal_to_base_changes_nothing() -> None:
    zone = p3.Zone("alles", all_cells(BASE), BASE.stack, BASE.surface)
    plain = p3.compute(BASE)
    zoned = p3.compute(dataclasses.replace(BASE, zones=(zone,)))
    np.testing.assert_allclose(zoned.heads_ref, plain.heads_ref, atol=1e-10)
    np.testing.assert_allclose(zoned.heads_new, plain.heads_new, atol=1e-10)


def test_zone_over_everything_equals_base_with_those_values() -> None:
    other = LayerStack(
        "freatisch_zand",
        build_layers("freatisch_zand", [(4.0, 3.0), (25.0, 12.0)], [(3.0, 900.0, 0.004)]),
    )
    zone = p3.Zone("alles", all_cells(BASE), other, 0.4)
    zoned = p3.compute(dataclasses.replace(BASE, zones=(zone,)))
    direct = p3.compute(dataclasses.replace(BASE, stack=other, surface=0.4))
    np.testing.assert_allclose(zoned.heads_ref, direct.heads_ref, atol=1e-10)
    np.testing.assert_allclose(zoned.heads_new, direct.heads_new, atol=1e-10)
    np.testing.assert_allclose(zoned.surface, 0.4)


def test_harmonic_mean_between_zones_is_exact() -> None:
    """One layer, T1 in the west half (zone) and T2 in the east half, heads held by a stiff
    GHB in the first and last column, no recharge: the flow per metre is
    dh / (L1/T1 + L2/T2 + resistances of both GHBs), with L1, L2 measured from the centres of
    the first and last cell to the zone boundary. Exact for finite volumes with the
    harmonic mean."""
    cell, nrow, ncol = 25.0, 4, 40
    zone_stack = LayerStack("freatisch_zand", build_layers("freatisch_zand", [(10.0, 20.0)], []))
    T1, T2 = zone_stack.nodes[0].T, ONE_LAYER.nodes[0].T
    h_w, h_e, c_g = 1.0, 0.0, 0.5
    par = dataclasses.replace(
        BASE, stack=ONE_LAYER, surface=50.0, recharge=0.0, cell=cell,
        size_x=ncol * cell, size_y=nrow * cell, boundary="gesloten", watercourses=(),
        ghb=(
            p3.GhbGroup("west", column(0, range(nrow)), "freatisch", h_w, h_w, c_g),
            p3.GhbGroup("oost", column(ncol - 1, range(nrow)), "freatisch", h_e, h_e, c_g),
        ),
        zones=(p3.Zone("westhelft", block(range(nrow), range(ncol // 2)), zone_stack, 50.0),),
        convertible=False,
    )  # fmt: skip
    res = p3.compute(par)
    L1 = L2 = (ncol // 2 - 0.5) * cell
    r_ghb = c_g / cell  # per metre width: c / dx
    q = (h_w - h_e) / (L1 / T1 + L2 / T2 + 2 * r_ghb)
    assert res.budget.loc["west", "referentie [m³/d]"] == pytest.approx(
        q * nrow * cell, rel=1e-6
    )  # MF6 closure
    assert res.mass_balance["referentie"] < 1e-6


def test_watercourse_conductance_follows_zone_of_each_cell() -> None:
    """A ditch (bottom 1.6 m below surface) cuts through the 1 m clay of the base into
    pakket 1, but stays inside the 3 m clay of the zone: per cell the connection and the
    conductance follow the local stack."""
    wc = p3.Watercourse(
        name="sloot",
        cells=column(20, range(40)),
        stage_ref=-1.0,
        stage_new=-1.0,
        bottom=-1.6,
        width=2.0,
    )
    zone = p3.Zone("dik", block(range(20, 40), range(40)), THICK_CLAY, 0.0)
    par = dataclasses.replace(
        BASE, stack=CLAY, watercourses=(wc,), zones=(zone,), grid_correction=False
    )
    par.validate()
    cond = p3._cell_conductances(par, p3.subsoil_helper(par))[0]
    per_cell: dict[tuple[int, int], dict[int, float]] = {}
    for node, pos, C in cond:
        for k, value in zip(pos, C, strict=True):
            per_cell.setdefault(wc.cells[k], {})[node] = float(value)
    for stack, rows in ((CLAY, range(20)), (THICK_CLAY, range(20, 40))):
        expected = dict(Subsoil(stack=stack).conductance_parts(p3.as_ditch(par, wc, 0.0)))
        for r in rows:
            got = per_cell[(r, 20)]
            assert got.keys() == expected.keys()
            for node, C in expected.items():
                assert got[node] == pytest.approx(C * wc.length, rel=1e-12)
    assert per_cell[(0, 20)].keys() != per_cell[(39, 20)].keys()  # other connection


def test_zone_surface_sets_drainage_levels() -> None:
    zone = p3.Zone("hoog", block(range(10), range(10)), BASE.stack, 0.8)
    par = dataclasses.replace(BASE, zones=(zone,), drain_cells=((5, 5), (30, 30)), drain_depth=1.0)
    sub = p3.subsoil_helper(par)
    bnds = {b.name: b for b in p3._boundaries(par, sub, "ref", p3._cell_conductances(par, sub))}
    surface = p3.surface_map(par)
    assert surface[5, 5] == 0.8 and surface[30, 30] == BASE.surface
    mv = bnds["maaiveld"]
    np.testing.assert_allclose(mv.stage, surface.ravel())
    drn = bnds["buisdrainage"]
    np.testing.assert_allclose(drn.stage, [0.8 - 1.0, BASE.surface - 1.0])


def test_later_zone_wins_on_overlap() -> None:
    a = p3.Zone("a", block(range(10), range(10)), BASE.stack, 0.1)
    b = p3.Zone("b", block(range(5, 15), range(5, 15)), BASE.stack, 0.2)
    zmap = p3.zone_map(dataclasses.replace(BASE, zones=(a, b)))
    assert zmap[0, 0] == 1 and zmap[7, 7] == 2 and zmap[20, 20] == 0


def test_zones_with_all_boundaries_keep_mass_balance() -> None:
    zone = p3.Zone("kom", block(range(5, 25), range(10, 30)), THICK_CLAY, -0.5)
    deep = p3.Zone("ondiep", block(range(30, 40), range(0, 40)), CLAY, 0.3)
    par = dataclasses.replace(
        BASE, stack=CLAY, zones=(zone, deep), seepage=True, seepage_head=0.5,
        wells=(p3.WellGroup("put", ((12, 12),), "pakket1", 100.0, 300.0),),
        drain_cells=block(range(30, 40), range(0, 10)),
    )  # fmt: skip
    res = p3.compute(par)
    assert max(res.mass_balance.values()) < 1e-6
    assert res.zone_map.shape == (par.nrow, par.ncol)
    assert set(np.unique(res.zone_map)) == {0, 1, 2}


@pytest.mark.parametrize(
    ("zone", "fragment"),
    [
        (p3.Zone("z", ((1, 1),), CLAY, 0.0), "schematisering"),
        (
            p3.Zone(
                "z", ((1, 1),),
                LayerStack(
                    "freatisch_zand",
                    build_layers("freatisch_zand", [(5.0, 1.0)] * 3, [(2.0, 500.0, 0.004)] * 2),
                ),
                0.0,
            ),
            "aantal lagen",
        ),
        (p3.Zone("z", ((100, 1),), BASE.stack, 0.0), "buiten het rooster"),
        (p3.Zone("z", (), BASE.stack, 0.0), "geen cellen"),
        (p3.Zone("Sloot west", ((1, 1),), BASE.stack, 0.0), "unieke naam"),
    ],
)  # fmt: skip
def test_invalid_zone_rejected(zone: p3.Zone, fragment: str) -> None:
    with pytest.raises(ParamError) as exc:
        dataclasses.replace(BASE, zones=(zone,)).validate()
    assert any(fragment in m for m in exc.value.messages), exc.value.messages


def test_too_many_zones_rejected() -> None:
    zones = tuple(p3.Zone(f"z{k}", ((k, 0),), BASE.stack, 0.0) for k in range(p3.MAX_ZONES + 1))
    with pytest.raises(ParamError, match="zones"):
        dataclasses.replace(BASE, zones=zones).validate()


def test_watercourse_bottom_above_zone_surface_rejected() -> None:
    low = p3.Zone("laag", block(range(10, 30), range(20, 21)), BASE.stack, -1.7)
    with pytest.raises(ParamError, match="laag"):
        dataclasses.replace(BASE, zones=(low,)).validate()


def test_largest_grid_with_five_zones_is_fast_enough() -> None:
    """100 x 100 cells, 5 zones; watercourses run through several zones."""
    import time

    import test_peilopzet_grid as base_tests

    par = base_tests.fine_params()
    zones = tuple(
        p3.Zone(
            f"z{k}", block(range(20 * k, 20 * k + 20), range(100)), par.stack, par.surface + 0.1 * k
        )
        for k in range(p3.MAX_ZONES)
    )
    par = dataclasses.replace(par, zones=zones)
    t0 = time.perf_counter()
    res = p3.compute(par)
    assert time.perf_counter() - t0 < 3.0
    assert max(res.mass_balance.values()) < 1e-6
    assert "z0:" in res.conductances.loc[1, "Sluit aan op"]
