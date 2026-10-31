"""Validation of product 3: raised level on a plan-view grid (numerical, steady)."""

import dataclasses
import time

import numpy as np
import pytest

from gwproducten.analytic.multilayer import LayerSystem, LineElement, solve
from gwproducten.core.layers import LayerStack, build_layers
from gwproducten.core.params import ParamError
from gwproducten.products import peilopzet_grid as p3

BASE = p3.Params()
ONE_LAYER = LayerStack("freatisch_zand", build_layers("freatisch_zand", [(10.0, 2.0)], []))
TWO_LAYER = LayerStack(
    "freatisch_zand",
    build_layers("freatisch_zand", [(5.0, 5.0), (20.0, 15.0)], [(2.0, 100.0, 0.02)]),
)


def column(col: int, nrow: int) -> tuple[tuple[int, int], ...]:
    return tuple((r, col) for r in range(nrow))


def test_defaults_are_valid_and_run() -> None:
    BASE.validate()
    res = p3.compute(BASE)
    assert res.heads_ref.shape == (len(res.node_labels), res.y.size, res.x.size)
    assert np.isfinite(res.rise).all()


def test_parabola_between_two_ideal_ditches() -> None:
    """Single layer, closed boundary, recharge, two very conductive ditches: the FD solution
    equals the parabola h = h0 + N/(2T) (x - x0)(x1 - x) (exact for a quadratic)."""
    N, T, h0 = 0.001, 20.0, -1.0
    ditches = tuple(
        p3.Watercourse(
            name=f"sloot {c}", cells=column(c, 4), stage_ref=h0, stage_new=h0, bottom=-5.0,
            conductance=1e7,
        )
        for c in (0, 19)
    )  # fmt: skip
    par = dataclasses.replace(
        BASE, stack=ONE_LAYER, size_x=500.0, size_y=100.0, recharge=N, surface=2.0,
        boundary="gesloten", watercourses=ditches, convertible=False,
    )  # fmt: skip
    res = p3.compute(par)
    x0, x1 = res.x[0], res.x[-1]
    expected = h0 + N / (2 * T) * (res.x - x0) * (x1 - res.x)
    np.testing.assert_allclose(res.heads_ref[0, 2], expected, atol=1e-4)


def test_mass_balance_both_runs() -> None:
    res = p3.compute(BASE)
    for run, err in res.mass_balance.items():
        assert err < 1e-8, run


def test_surface_drainage_caps_head() -> None:
    """No ditches, closed boundary: all recharge leaves via the surface drain, h = mv + N w."""
    par = dataclasses.replace(
        BASE, watercourses=(), boundary="gesloten", recharge=0.002, surface=0.0,
        mv_resistance=5.0,
    )  # fmt: skip
    res = p3.compute(par)
    np.testing.assert_allclose(res.heads_ref[0], 0.002 * 5.0, atol=1e-8)
    assert res.inundated["referentie"] == res.x.size * res.y.size


def test_riv_infiltration_limited_below_bottom() -> None:
    wc = p3.Watercourse(
        name="hoog", cells=((5, 5),), stage_ref=0.5, stage_new=0.5, bottom=0.0, conductance=10.0
    )
    par = dataclasses.replace(
        BASE, stack=ONE_LAYER, size_x=275.0, size_y=275.0, recharge=0.0, boundary="vast",
        boundary_head=-2.0, surface=5.0, watercourses=(wc,),
    )  # fmt: skip
    res = p3.compute(par)
    assert res.heads_ref[0, 5, 5] < 0.0  # below the bottom
    assert res.budget.loc["hoog", "referentie [m³/d]"] == pytest.approx(10.0 * 0.5, rel=1e-9)


def test_drain_type_only_discharges() -> None:
    wc = p3.Watercourse(
        name="greppel", cells=((5, 5),), kind="drn", stage_ref=0.5, stage_new=0.5, bottom=0.0,
        conductance=10.0,
    )  # fmt: skip
    par = dataclasses.replace(
        BASE, stack=ONE_LAYER, size_x=275.0, size_y=275.0, recharge=0.0, boundary="vast",
        boundary_head=-2.0, surface=5.0, watercourses=(wc,),
    )  # fmt: skip
    res = p3.compute(par)
    assert res.budget.loc["greppel", "referentie [m³/d]"] == 0.0


def test_pipe_drainage_only_in_selected_cells() -> None:
    nrow = ncol = 20
    cells = tuple((r, c) for r in range(nrow) for c in range(ncol // 2))  # west half
    par = dataclasses.replace(
        BASE, stack=ONE_LAYER, size_x=500.0, size_y=500.0, watercourses=(), boundary="gesloten",
        recharge=0.002, surface=0.0, drain_cells=cells, drain_depth=1.0, drain_resistance=50.0,
    )  # fmt: skip
    res = p3.compute(par)
    h = res.heads_ref[0]
    assert h[:, : ncol // 2].mean() < h[:, ncol // 2 :].mean()
    assert res.budget.loc["buisdrainage", "referentie [m³/d]"] < 0.0


def _strip_case(cell: float, correction: bool) -> tuple[float, float]:
    """Long parallel ditches (full columns), closed boundary, pipe drainage everywhere at the
    polder level (-1 m) as the regional drainage w, no recharge. Everything stays linear
    (heads >= drain level, above the ditch bottoms), so the rise equals the 1D analytic
    peilopzet. Returns the numerical and analytic infiltration of the raised ditch per metre."""
    nrow = max(4, int(np.ceil(50.0 / cell)))
    size_x, size_y = 1000.0, nrow * cell
    ncol = int(size_x / cell)
    mid, off = ncol // 2, int(round(100.0 / cell))
    wcs = (
        p3.Watercourse(
            name="opzet", cells=column(mid, nrow), stage_ref=-1.0, stage_new=-0.8, bottom=-1.6,
            width=2.0, length=cell,
        ),
        p3.Watercourse(name="west", cells=column(mid - off, nrow), stage_ref=-1.0, stage_new=-1.0,
                       bottom=-1.4, width=1.0, length=cell),
        p3.Watercourse(name="oost", cells=column(mid + off, nrow), stage_ref=-1.0, stage_new=-1.0,
                       bottom=-1.4, width=1.0, length=cell),
    )  # fmt: skip
    w = 100.0
    par = dataclasses.replace(
        BASE, stack=TWO_LAYER, size_x=size_x, size_y=size_y, cell=cell, recharge=0.0,
        surface=0.0, boundary="gesloten", watercourses=wcs,
        drain_cells=tuple((r, c) for r in range(nrow) for c in range(ncol)),
        drain_depth=1.0, drain_resistance=w, grid_correction=correction, convertible=False,
    )  # fmt: skip
    res = p3.compute(par)
    q_num = (
        res.budget.loc["opzet", "opzet [m³/d]"] - res.budget.loc["opzet", "referentie [m³/d]"]
    ) / size_y
    sub = p3.subsoil_helper(par)
    system = LayerSystem(tuple(n.T for n in sub.nodes), sub.stack.node_resistances(), w)
    els = []
    for wc, level in zip(wcs, (0.2, 0.0, 0.0), strict=True):
        x = (wc.cells[0][1] + 0.5) * cell
        for node, C in sub.conductance_parts(p3.as_ditch(par, wc)):
            els.append(LineElement(x, node, "head", level, C))
    sol = solve(system, els)
    q_an = float(sum(q for q, e in zip(sol.Q, els, strict=True) if e.x == els[0].x))
    return q_num, q_an


def test_raised_level_matches_1d_analytic_with_grid_correction() -> None:
    q_num, q_an = _strip_case(25.0, correction=True)
    assert q_num == pytest.approx(q_an, rel=0.05)


def test_grid_correction_improves_coarse_grid() -> None:
    with_c, q_an = _strip_case(25.0, correction=True)
    without, _ = _strip_case(25.0, correction=False)
    assert abs(with_c - q_an) < abs(without - q_an)


def test_grid_refinement_converges_to_analytic() -> None:
    coarse, q_an = _strip_case(25.0, correction=False)
    fine, _ = _strip_case(10.0, correction=False)
    assert abs(fine - q_an) < abs(coarse - q_an)
    assert fine == pytest.approx(q_an, rel=0.05)


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"size_x": 3000.0}, "te groot"),
        ({"cell": 5.0}, "Celgrootte"),
        ({"boundary": "onzin"}, "Modelrand"),
        ({"cell": 33.0}, "deelbaar"),
    ],
)
def test_invalid_model_input(change: dict, fragment: str) -> None:
    with pytest.raises(ParamError) as exc:
        dataclasses.replace(BASE, **change).validate()
    assert any(fragment in m for m in exc.value.messages)


def test_too_many_aquifers_rejected() -> None:
    stack = LayerStack(
        "deklaag",
        build_layers("deklaag", [(10.0, 10.0)] * 5, [(2.0, 500.0, 0.05)] * 5),
    )
    with pytest.raises(ParamError, match="4 watervoerende pakketten"):
        dataclasses.replace(BASE, stack=stack).validate()


THREE_AQ = LayerStack(
    "freatisch_zand",
    build_layers(
        "freatisch_zand", [(5.0, 5.0), (20.0, 15.0), (15.0, 20.0)],
        [(2.0, 100.0, 0.02), (3.0, 800.0, 0.01)],
    ),
)  # fmt: skip
FOUR_AQ = LayerStack(
    "freatisch_zand",
    build_layers(
        "freatisch_zand", [(5.0, 5.0), (20.0, 15.0), (15.0, 20.0), (30.0, 25.0)],
        [(2.0, 100.0, 0.02), (3.0, 800.0, 0.01), (5.0, 2000.0, 0.01)],
    ),
)  # fmt: skip
FOUR_AQ_CLAY = LayerStack(
    "deklaag",
    build_layers(
        "deklaag", [(20.0, 15.0), (15.0, 20.0), (30.0, 25.0), (20.0, 10.0)],
        [(3.0, 300.0, 0.05), (3.0, 800.0, 0.01), (5.0, 2000.0, 0.01), (4.0, 5000.0, 0.01)],
    ),
)  # fmt: skip


@pytest.mark.parametrize("stack", [THREE_AQ, FOUR_AQ, FOUR_AQ_CLAY], ids=["3fz", "4fz", "4dk"])
def test_many_aquifers_match_multilayer_analytic(stack: LayerStack) -> None:
    """Strip benchmark against the analytic multilayer solution (eigenvalue method) for 3
    and 4 aquifers; with the grid correction within 2 % at 25 m (measured: < 0.6 %)."""
    q_num, q_an = p3.strip_benchmark(stack, 25.0, correction=True)
    assert q_num == pytest.approx(q_an, rel=0.02)


def test_four_aquifers_with_all_boundaries_balance() -> None:
    nodes = FOUR_AQ_CLAY.nodes
    par = dataclasses.replace(
        BASE, stack=FOUR_AQ_CLAY, boundary="per_laag",
        boundary_layers=(-1.0, None, -1.2, None, -1.5),
        seepage=True, seepage_head=0.5, seepage_resistance=2000.0,
        wells=(p3.WellGroup("Putten", ((20, 20),), nodes[-1].key, 200.0, 400.0),),
        ghb=(p3.GhbGroup("GHB", ((5, 5), (5, 6)), nodes[2].key, -1.0, -0.9, 10.0),),
    )  # fmt: skip
    res = p3.compute(par)
    assert res.heads_ref.shape[0] == 5
    assert max(res.mass_balance.values()) < 1e-8
    assert res.well_drawdown is not None and res.well_drawdown[-1, 20, 20] > 0


def test_invalid_watercourse_rejected() -> None:
    outside = p3.Watercourse(name="buiten", cells=((100, 100),))
    with pytest.raises(ParamError, match="buiten het rooster"):
        dataclasses.replace(BASE, watercourses=(outside,)).validate()
    dry = p3.Watercourse(name="droog", cells=((1, 1),), stage_ref=-2.0, bottom=-1.0)
    with pytest.raises(ParamError, match="boven het peil"):
        dataclasses.replace(BASE, watercourses=(dry,)).validate()


def test_compute_is_fast_enough() -> None:
    t0 = time.perf_counter()
    p3.compute(BASE)
    assert time.perf_counter() - t0 < 3.0


def fine_params() -> p3.Params:
    """The largest grid: 100 x 100 cells (here 10 m, 1 km), the speed target."""
    wcs = p3.default_watercourses(1000.0, 1000.0, 10.0)
    wells = (p3.WellGroup("Putten", ((30, 30), (70, 70)), "pakket1", 0.0, 800.0),)
    return dataclasses.replace(BASE, cell=10.0, watercourses=wcs, wells=wells)


def test_largest_model_is_valid_and_too_large_rejected() -> None:
    for cell in (10.0, 25.0, 50.0):
        size = p3.MAX_CELLS_PER_SIDE * cell
        dataclasses.replace(BASE, cell=cell, size_x=size, size_y=size).validate()
        with pytest.raises(ParamError, match="te groot"):
            dataclasses.replace(BASE, cell=cell, size_x=size + cell).validate()


def test_largest_grid_with_four_aquifers_is_fast_enough() -> None:
    """100 x 100 cells x 5 nodes (deklaag + 4 aquifers) = 50 000 unknowns."""
    par = dataclasses.replace(fine_params(), stack=FOUR_AQ_CLAY, wells=())
    t0 = time.perf_counter()
    res = p3.compute(par)
    assert time.perf_counter() - t0 < 3.0
    assert max(res.mass_balance.values()) < 1e-8


@pytest.mark.parametrize("cell", [10.0, 50.0])
def test_fine_grid_is_fast_enough(cell: float) -> None:
    par = fine_params()
    if cell != 10.0:  # 5 km at 50 m: same cell indices, watercourses scale along
        par = dataclasses.replace(
            par, cell=cell, size_x=1000.0 * cell / 10.0, size_y=1000.0 * cell / 10.0,
            watercourses=tuple(dataclasses.replace(wc, length=cell) for wc in par.watercourses),
        )  # fmt: skip
    t0 = time.perf_counter()
    res = p3.compute(par)
    assert time.perf_counter() - t0 < 3.0
    assert max(res.mass_balance.values()) < 1e-8


def test_shared_factorization_matches_fresh_solves() -> None:
    """Reusing the LU (exact or as PCG preconditioner) and the reference states as start
    must give the same heads as independent direct solves."""
    from gwproducten.numeric.grid2d import Factorization, solve_grid

    par = dataclasses.replace(
        BASE, watercourses=(dataclasses.replace(BASE.watercourses[0], stage_new=-0.3),
                            *BASE.watercourses[1:]),
        wells=(p3.WellGroup("Putten", ((20, 30),), "pakket1", 100.0, 600.0),),
    )  # fmt: skip
    sub = p3.subsoil_helper(par)
    T = tuple(n.T for n in sub.nodes)
    c = sub.stack.node_resistances()
    cond = p3._cell_conductances(par, sub)

    def run(stage: str, **kw):
        return solve_grid(
            T, c, par.nrow, par.ncol, par.cell, par.recharge,
            p3._boundaries(par, sub, stage, cond), fixed_head=par.boundary_head,
            fluxes=p3._wells(par, sub, stage), **kw,
        )  # fmt: skip

    fact = Factorization()
    ref = run("ref", factorization=fact)
    shared = run("new", factorization=fact, initial_active=ref.active)
    fresh = run("new")
    assert shared.converged and fresh.converged
    np.testing.assert_allclose(shared.heads, fresh.heads, atol=1e-8)
    np.testing.assert_allclose(shared.flux_response, fresh.flux_response, atol=1e-8)
    assert shared.iterations <= fresh.iterations


def test_conductance_scales_with_length_per_cell() -> None:
    full = p3.Watercourse(name="sloot", cells=((5, 5), (6, 5)), lengths=(25.0, 25.0))
    half_end = dataclasses.replace(full, lengths=(25.0, 12.5))
    par = dataclasses.replace(BASE, size_x=275.0, size_y=275.0)
    sub = p3.subsoil_helper(par)
    c_full = p3._cell_conductances(dataclasses.replace(par, watercourses=(full,)), sub)[0]
    c_half = p3._cell_conductances(dataclasses.replace(par, watercourses=(half_end,)), sub)[0]
    for (_, _, cf), (_, _, ch) in zip(c_full, c_half, strict=True):
        assert ch[0] == pytest.approx(cf[0])
        assert ch[1] == pytest.approx(0.5 * cf[1])


def test_user_conductance_scales_with_length() -> None:
    wc = p3.Watercourse(name="s", cells=((5, 5), (6, 5)), lengths=(25.0, 5.0), conductance=100.0)
    par = dataclasses.replace(BASE, size_x=275.0, size_y=275.0, watercourses=(wc,))
    parts = p3._cell_conductances(par, p3.subsoil_helper(par))[0]
    total = np.sum([cells for _, _, cells in parts], axis=0)
    np.testing.assert_allclose(total, [100.0, 20.0])


def test_invalid_lengths_rejected() -> None:
    wc = p3.Watercourse(name="s", cells=((1, 1), (1, 2)), lengths=(25.0,))
    with pytest.raises(ParamError, match="aantal lengtes"):
        dataclasses.replace(BASE, watercourses=(wc,)).validate()


def test_aquifer_anisotropy_adds_vertical_resistance() -> None:
    """kv/kh of the aquifers adds D/2/kv per aquifer node to the resistance between nodes;
    the strip benchmark stays consistent with the analytic multilayer solution."""

    def stack(a: float) -> LayerStack:
        return LayerStack(
            "freatisch_zand",
            build_layers("freatisch_zand", [(5.0, 5.0, a), (20.0, 15.0, a)], [(2.0, 100.0, 0.02)]),
        )

    iso, aniso = stack(1.0), stack(0.1)
    assert aniso.node_resistances()[0] - iso.node_resistances()[0] == pytest.approx(
        0.5 * 5.0 / 0.5 + 0.5 * 20.0 / 1.5 - 0.5 * 5.0 / 5.0 - 0.5 * 20.0 / 15.0
    )
    q_num, q_an = p3.strip_benchmark(aniso, 25.0, correction=True)
    assert q_num == pytest.approx(q_an, rel=0.02)
    res_iso = p3.compute(dataclasses.replace(BASE, stack=iso))
    res_aniso = p3.compute(dataclasses.replace(BASE, stack=aniso))
    assert not np.allclose(res_iso.rise, res_aniso.rise)
