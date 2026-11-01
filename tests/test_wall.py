"""Sheet pile walls (damwanden) on cell faces: the solver against the series-resistance
formula, the face geometry, an enclosed building pit, a leaky pit's inflow, a partial wall,
validation messages, splitting an aquifer at the wall tip, and grid refinement. The comparison
with MODFLOW 6 (HFB) is in test_mf6_comparison.py."""

import dataclasses

import numpy as np
import pytest

from gwproducten.core.grids import outline_faces, polyline_faces
from gwproducten.core.layers import DEFAULT_ANISOTROPY, LayerStack, build_layers, split_aquifer
from gwproducten.core.params import ParamError
from gwproducten.numeric.grid2d import Barrier, HeadBoundary, solve_grid
from gwproducten.products import peilopzet_grid as p3

PIT = tuple((r, c) for r in range(18, 22) for c in range(18, 22))  # 100 x 100 m, model centre


def _strip(hydchr: float) -> tuple[float, float, float]:
    """3 x 10 strip, cells 10 m, T = 50 (k 5, D 10), GHB at both ends, a wall between columns
    4 and 5 in every row. Returns (inflow left, outflow right, flow through the wall)."""
    nrow, ncol, cell, k, D = 3, 10, 10.0, 5.0, 10.0
    idx = np.arange(nrow * ncol).reshape(nrow, ncol)
    ends = [
        HeadBoundary(name, "ghb", idx[:, col], np.full(nrow, 1e3), np.full(nrow, h), np.zeros(nrow))
        for name, col, h in (("links", 0, 1.0), ("rechts", ncol - 1, 0.0))
    ]
    wall = Barrier("wand", 0, idx[:, 4], idx[:, 5], np.full(nrow, hydchr), np.full((2, nrow), D))
    sol = solve_grid([k * D], [], nrow, ncol, cell, 0.0, ends, barriers=[wall])
    assert hydchr == 0 or sol.mass_balance_error < 1e-10  # (relative: undefined at no flow)
    flow = sol.barrier_flow[0]
    assert flow.shape == (nrow,)
    return sol.fluxes["links"], -sol.fluxes["rechts"], float(flow.sum())


def test_strip_wall_is_extra_length_k_cw() -> None:
    """Q = T B dh / (L + k c_w) per row, plus the two GHB resistances: exact."""
    c_w, T, k, cell, B = 20.0, 50.0, 5.0, 10.0, 10.0
    q_in, q_out, q_wall = _strip(1.0 / c_w)
    L = 9 * cell  # nine faces between the centres of column 0 and column 9
    per_row = 1.0 / (2 / 1e3 + (L + k * c_w) / (T * B))
    assert q_in == pytest.approx(3 * per_row, rel=1e-10)
    assert q_out == pytest.approx(q_in, rel=1e-10) and q_wall == pytest.approx(q_in, rel=1e-10)


def test_impermeable_wall_stops_the_flow() -> None:
    q_in, q_out, q_wall = _strip(0.0)
    assert abs(q_in) < 1e-10 and abs(q_out) < 1e-10 and abs(q_wall) < 1e-10


def test_outline_faces_of_a_selection() -> None:
    assert set(outline_faces({(1, 1)}, 3, 3)) == {
        (1, 0, "x"), (1, 1, "x"), (0, 1, "y"), (1, 1, "y"),
    }  # fmt: skip
    assert set(outline_faces({(0, 0)}, 3, 3)) == {(0, 0, "x"), (0, 0, "y")}  # model edge
    block = {(r, c) for r in (1, 2) for c in (1, 2)}
    assert len(outline_faces(block, 4, 4)) == 8


def test_polyline_faces_follow_the_grid_lines() -> None:
    """Clicked points snap to cell corners; a straight line along a grid line gives the faces
    on it, a diagonal a staircase; edges on the model boundary are no faces."""
    straight = polyline_faces([(20.0, 0.0), (21.0, 29.0)], 10.0, 3, 4)
    assert set(straight) == {(0, 1, "x"), (1, 1, "x"), (2, 1, "x")}
    stairs = polyline_faces([(0.0, 0.0), (20.0, 20.0)], 10.0, 3, 3)
    assert set(stairs) == {(0, 0, "x"), (0, 1, "y"), (1, 1, "x")}
    assert polyline_faces([(0.0, 0.0), (30.0, 0.0)], 10.0, 3, 3) == ()  # along the edge


def _pit(q: float, resistance: float | None = None, layer: str = "pakket1", **kw) -> p3.Params:
    return p3.Params(
        walls=(p3.Wall("bouwput", outline_faces(set(PIT), 40, 40), layer, resistance),),
        wells=(p3.WellGroup("bemaling", ((19, 19),), kw.pop("well_layer", "pakket1"), 0.0, q),),
        **kw,
    )


def test_enclosed_pit_to_the_base_no_drawdown_outside() -> None:
    """Impermeable wall through all layers to the closed base: the well only takes the
    recharge inside (the rest leaves over the surface); outside nothing changes."""
    res = p3.compute(_pit(5.0))  # 5 < recharge inside = 16 * 625 * 0.0008 = 8 m3/d
    outside = np.ones((40, 40), dtype=bool)
    outside[18:22, 18:22] = False
    assert np.max(np.abs(res.well_drawdown[:, outside])) < 1e-9
    assert np.all(res.well_drawdown[:, ~outside] > -1e-9)


def test_enclosed_pit_without_supply_is_a_clear_error() -> None:
    with pytest.raises(ParamError, match="geen aanvoer"):
        p3.compute(_pit(50.0))


def test_leaky_pit_inflow_equals_well_minus_recharge() -> None:
    """Leaky wall (c_w = 100 d) through all layers: the flow through the wall into the pit is
    the extraction minus the recharge inside (the pit lies far below the surface)."""
    res = p3.compute(_pit(200.0, resistance=100.0))
    walls = res.walls.set_index(["Damwand", "Laag"])
    assert set(walls.index.get_level_values("Laag")) == {"Freatisch pakket", "Pakket 1"}
    assert walls["referentie [m³/d]"].abs().max() < 1e-9  # only in the opzet
    assert walls["opzet [m³/d]"].sum() == pytest.approx(200.0 - 16 * 625 * 0.0008, rel=1e-6)


def test_wall_in_both_situations_changes_nothing_by_itself() -> None:
    """No raised level, no pumping: a wall in both situations gives no rise; a wall only in
    the opzet is itself the change."""
    par = _pit(0.0, resistance=None, layer="freatisch")
    par = dataclasses.replace(
        par, watercourses=tuple(dataclasses.replace(w, stage_new=w.stage_ref)
                                for w in par.watercourses),
    )  # fmt: skip
    both = dataclasses.replace(par, walls=(dataclasses.replace(par.walls[0], reference=True),))
    assert np.max(np.abs(p3.compute(both).rise)) < 1e-5  # iteration tolerance
    assert np.max(np.abs(p3.compute(par).rise)) > 1e-3  # only in the opzet: it does


def test_partial_wall_reduces_but_does_not_stop() -> None:
    """Wall through the phreatic layer only, well in that layer: water comes in via pakket 1
    under the wall; drawdown outside is smaller than without the wall, but not zero."""
    kw = {"well_layer": "freatisch"}
    with_wall = p3.compute(_pit(5.0, layer="freatisch", **kw)).well_drawdown[0, 19, 17]
    no_wall = p3.compute(dataclasses.replace(_pit(5.0, **kw), walls=())).well_drawdown[0, 19, 17]
    assert 0.0 < with_wall < 0.9 * no_wall


def test_wall_validation_messages() -> None:
    faces = ((5, 5, "x"),)
    bad = [
        (p3.Wall("w", faces, "pakket7"), "laag 'pakket7' bestaat niet"),
        (p3.Wall("w", ((39, 39, "x"),)), "buiten het rooster"),
        (p3.Wall("w", ((5, 5, "z"),)), "buiten het rooster"),
        (p3.Wall("w", ()), "geen celranden"),
        (p3.Wall("w", faces, "freatisch", -1.0), "weerstand"),
        (p3.Wall("Wetering", faces), "unieke naam"),
    ]
    for wall, message in bad:
        with pytest.raises(ParamError, match=message):
            p3.Params(walls=(wall,)).validate()


def test_split_aquifer_at_the_wall_tip() -> None:
    """Pakket 1 (20 m) split 8 m below its top: 8 m + 0.1 m aquitard (c 0.01 d) + 11.9 m; the
    vertical resistance between the halves is D1/2kv + 0.01 + D2/2kv."""
    stack = LayerStack(
        "freatisch_zand", build_layers("freatisch_zand", [(5.0, 1.0), (20.0, 15.0)],
                                       [(2.0, 500.0, 0.004)]),
    )  # fmt: skip
    split = split_aquifer(stack, 2, 8.0)
    assert not split.errors()
    assert [lay.kind for lay in split.layers] == [
        "pakket", "scheidend", "pakket", "scheidend", "pakket",
    ]  # fmt: skip
    assert [lay.thickness for lay in split.layers] == pytest.approx([5.0, 2.0, 8.0, 0.1, 11.9])
    assert split.total_thickness == pytest.approx(stack.total_thickness)
    kv = 15.0 * DEFAULT_ANISOTROPY
    assert split.node_resistances()[1] == pytest.approx(8.0 / (2 * kv) + 0.01 + 11.9 / (2 * kv))
    with pytest.raises(ValueError):
        split_aquifer(stack, 1, 1.0)  # an aquitard
    with pytest.raises(ValueError):
        split_aquifer(stack, 2, 19.95)  # leaves less than 0.1 m


def test_grid_refinement_of_a_wall() -> None:
    """Impermeable wall of 400 m in pakket 1 and the phreatic layer, across the flow from the
    raised Wetering to the Sloot. Measured: the rise behind the wall relative to the rise
    without the wall is 0.67 (25 m), 0.68 (12.5 m) and 0.67 (10 m cells)."""

    def ratio(cell: float) -> float:
        n = int(round(1000 / cell))
        par = p3.Params(cell=cell, watercourses=p3.default_watercourses(1000.0, 1000.0, cell))
        line = polyline_faces([(500.0, 300.0), (500.0, 700.0)], cell, n, n)
        walled = dataclasses.replace(par, walls=(p3.Wall("scherm", line, "pakket1"),))
        r, c = int(500 / cell), int(600 / cell)
        return float(p3.compute(walled).rise[0, r, c] / p3.compute(par).rise[0, r, c])

    coarse, fine = ratio(25.0), ratio(12.5)
    assert 0.5 < fine < 0.8
    assert abs(coarse - fine) < 0.03
