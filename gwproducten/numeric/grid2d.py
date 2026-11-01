"""Steady plan-view grid model (cell-centred finite volumes, quasi-3D layers).

Uniform square cells, one computational layer per node, recharge on the top node, and
MODFLOW-style head-dependent boundaries:

- ``riv``: flux C (stage - h) while h > bottom, else the fixed C (stage - bottom);
- ``drn``: flux C (level - h) while h > level, else 0;
- ``ghb``: flux C (stage - h), always active;

plus fixed fluxes (wells), an outer ring of fixed heads per layer, and horizontal flow
barriers (sheet pile walls) on cell faces, as the MODFLOW 6 HFB package.

The on/off switching is solved by Picard iteration: solve, update every state, repeat until
no state changes. A layer can be *convertible* (MODFLOW 6 ICELLTYPE > 0): its transmissivity
follows the saturated thickness and is updated in the same iteration until the heads settle.
Absolute heads [m]; flux positive into the groundwater.

The states only change the diagonal, so one LU factorisation (minimum-degree ordering) is
reused: exactly for an identical matrix, otherwise as preconditioner for conjugate gradients.
A :class:`Factorization` can be shared between runs on the same grid (reference and opzet).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import LinearOperator, cg, splu

PCG_RTOL = 1e-12
PCG_MAXITER = 40  # more iterations than this: refactorise instead
PCG_MAXITER_REUSE = 12  # same, when many coefficients changed a little (``reuse``)
# PCG converges in at most k+1 steps for k changed entries; refactorise beyond this many
PCG_MAX_CHANGED = 50
PCG_MAX_CHANGED_FRACTION = 0.02
MAX_CACHED_LU = 3  # factorisations kept (reference, opzet, run without wells)
HEAD_TOL = 1e-5  # [m] head change between iterations that ends the convertible iteration
# transmissivity update: after this many iterations, the step of a cell whose head reverses
# is multiplied by RELAX (down to RELAX_MIN)
RELAX_AFTER, RELAX, RELAX_MIN = 3, 0.5, 0.01
MIN_SATURATED_FRACTION = 0.01  # saturated thickness never below 1 % of the layer thickness


class Factorization:
    """Cache of the last few LU factorisations; a nearby matrix is solved by CG
    preconditioned with the closest cached LU."""

    def __init__(self) -> None:
        self.cache: list[tuple[sp.csc_matrix, object]] = []
        self.factorizations = 0

    def _factor(self, M: sp.csc_matrix):
        lu = splu(M, permc_spec="MMD_AT_PLUS_A", options={"SymmetricMode": True})
        self.cache = [*self.cache, (M, lu)][-MAX_CACHED_LU:]
        self.factorizations += 1
        return lu

    def solve(
        self, M: sp.csc_matrix, b: np.ndarray, x0: np.ndarray | None = None, reuse: bool = False
    ) -> np.ndarray:
        """``reuse``: try CG with a cached LU even when many entries changed (small changes of
        many coefficients, such as a transmissivity update)."""
        cached = [(A, lu) for A, lu in self.cache if A.shape == M.shape]
        if not cached:
            return self._factor(M).solve(b)
        diffs = [M - A for A, _ in cached]
        best = int(np.argmin([abs(d).sum() for d in diffs]))
        lu, changed = cached[best][1], diffs[best].count_nonzero()
        if changed == 0:
            return lu.solve(b)
        if not reuse and changed > max(PCG_MAX_CHANGED, PCG_MAX_CHANGED_FRACTION * M.shape[0]):
            return self._factor(M).solve(b)
        P = LinearOperator(M.shape, matvec=lu.solve, dtype=float)
        maxiter = PCG_MAXITER_REUSE if reuse else PCG_MAXITER
        x, info = cg(M, b, x0=x0, M=P, rtol=PCG_RTOL, atol=0.0, maxiter=maxiter)
        if info == 0:
            return x
        return self._factor(M).solve(b)


@dataclass(frozen=True)
class HeadBoundary:
    """Group of head-dependent cells (one watercourse, the pipe drains, ...)."""

    name: str
    kind: str  # "riv", "drn" or "ghb"
    keys: np.ndarray  # flat unknown indices (node, row, col)
    conductance: np.ndarray  # [m2/d] per entry
    stage: np.ndarray  # [m] stage (riv) or drain level (drn)
    bottom: np.ndarray  # [m] riv bottom; ignored for drn and ghb


@dataclass(frozen=True)
class FluxBoundary:
    """Group of fixed fluxes (wells): ``rate`` [m3/d] per entry, positive = into the
    groundwater (injection), negative = extraction."""

    name: str
    keys: np.ndarray
    rate: np.ndarray


@dataclass(frozen=True)
class Barrier:
    """Horizontal flow barrier on cell faces of one layer (MODFLOW 6 HFB): in series with the
    face conductance C comes C_w = hydchr * b * dy, b = mean thickness of the two cells (the
    saturated thickness for a convertible layer); hydchr = 1/c_w [1/d], 0 = impermeable.
    ``a`` and ``b``: flat cell indices (row * ncol + col) of the neighbours on both sides;
    ``height``: (2, n) full layer thickness [m] in ``a`` and ``b``."""

    name: str
    layer: int
    a: np.ndarray
    b: np.ndarray
    hydchr: np.ndarray
    height: np.ndarray


def _barrier_conductance(
    bar: Barrier, C: np.ndarray, t: np.ndarray, conv: Convertible | None, cell: float
) -> np.ndarray:
    """Face conductance with the barrier in series; ``C`` the face conductance without it,
    ``t`` (nrow, ncol) transmissivity of the layer."""
    if conv is None:
        b = 0.5 * (bar.height[0] + bar.height[1])
    else:  # T = k b_sat: the saturated thickness of the current transmissivity
        k = np.broadcast_to(np.asarray(conv.k, float), t.shape).ravel()
        b = 0.5 * (t.ravel()[bar.a] / k[bar.a] + t.ravel()[bar.b] / k[bar.b])
    Cw = bar.hydchr * b * cell
    return np.where(bar.hydchr > 0, C * Cw / np.maximum(C + Cw, 1e-300), 0.0)


def _faces(bar: Barrier, ncol: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(row, col, along x) of every face: ``cx[row, col]`` or ``cy[row, col]``."""
    r, col = np.divmod(np.minimum(bar.a, bar.b), ncol)
    return r, col, np.abs(bar.b - bar.a) == 1


def face_conductances(
    t: np.ndarray, layer: int, barriers: Sequence[Barrier], conv: Convertible | None, cell: float
) -> tuple[np.ndarray, np.ndarray]:
    """Conductances of the faces between (r, c) and (r, c + 1) (``cx``) and between (r, c)
    and (r + 1, c) (``cy``) of one layer with transmissivity ``t`` (nrow, ncol): square cells,
    T dy/dx = T, harmonic mean; the barriers of this layer applied in order."""
    cx = _harmonic(t[:, :-1], t[:, 1:])
    cy = _harmonic(t[:-1, :], t[1:, :])
    for bar in barriers:
        if bar.layer != layer:
            continue
        r, col, along_x = _faces(bar, t.shape[1])
        C = np.empty(r.size)
        C[along_x] = cx[r[along_x], col[along_x]]
        C[~along_x] = cy[r[~along_x], col[~along_x]]
        C = _barrier_conductance(bar, C, t, conv, cell)
        cx[r[along_x], col[along_x]] = C[along_x]
        cy[r[~along_x], col[~along_x]] = C[~along_x]
    return cx, cy


def barrier_flows(
    barriers: Sequence[Barrier],
    heads: np.ndarray,
    transmissivity: np.ndarray,
    convertible: Sequence[Convertible | None],
    cell: float,
) -> tuple[np.ndarray, ...]:
    """Flow from side ``a`` to side ``b`` [m3/d] per face of every barrier, for heads and
    transmissivities (n_layers, nrow, ncol)."""
    conv = list(convertible) + [None] * (len(heads) - len(convertible))
    faces = {
        layer: face_conductances(transmissivity[layer], layer, barriers, conv[layer], cell)
        for layer in {bar.layer for bar in barriers}
    }
    flows = []
    for bar in barriers:
        cx, cy = faces[bar.layer]
        r, col, along_x = _faces(bar, heads.shape[2])
        C = np.empty(r.size)
        C[along_x] = cx[r[along_x], col[along_x]]
        C[~along_x] = cy[r[~along_x], col[~along_x]]
        h = heads[bar.layer].ravel()
        flows.append(C * (h[bar.a] - h[bar.b]))
    return tuple(flows)


@dataclass(frozen=True)
class Convertible:
    """Layer whose transmissivity follows the saturated thickness (MODFLOW 6 convertible):
    T = k (min(h, top) - bottom), at least ``MIN_SATURATED_FRACTION`` of the thickness.
    k [m/d], top and bottom [m]: numbers or (nrow, ncol) arrays."""

    k: np.ndarray | float
    top: np.ndarray | float
    bottom: np.ndarray | float

    def _wet(self, h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        D = np.asarray(self.top) - np.asarray(self.bottom)
        return np.minimum(h, self.top) - self.bottom, D

    def saturated(self, h: np.ndarray) -> np.ndarray:
        """Saturated thickness [m] at head ``h``."""
        wet, D = self._wet(h)
        return np.clip(wet, MIN_SATURATED_FRACTION * D, D)

    def dry(self, h: np.ndarray) -> np.ndarray:
        """Cells held at the minimum saturated thickness."""
        wet, D = self._wet(h)
        return wet <= MIN_SATURATED_FRACTION * D


@dataclass(frozen=True)
class GridSolution:
    heads: np.ndarray  # (n_layers, nrow, ncol)
    fluxes: dict[str, float]  # per boundary group [m3/d], positive = into the groundwater
    recharge: float  # total recharge [m3/d]
    lateral: float  # through the fixed outer ring [m3/d], positive = into the model
    iterations: int
    converged: bool
    mass_balance_error: float
    active: dict[str, np.ndarray]  # per group: head-dependent state per entry
    flux_response: np.ndarray | None  # head change by the fixed fluxes alone (frozen states)
    transmissivity: np.ndarray  # (n_layers, nrow, ncol) [m2/d] of the final solve
    dry: np.ndarray  # (n_layers, nrow, ncol): convertible cells at the minimum thickness
    barrier_flow: tuple[np.ndarray, ...] = ()  # per barrier: flow a -> b per face [m3/d]


def _harmonic(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Harmonic mean 2ab/(a+b): two half cells in series (exact a for a == b)."""
    return np.where(a == b, a, 2.0 * a * b / np.maximum(a + b, 1e-300))


def solve_grid(
    T: Sequence[float | np.ndarray],
    c: Sequence[float | np.ndarray],
    nrow: int,
    ncol: int,
    cell: float,
    recharge: float,
    boundaries: list[HeadBoundary],
    *,
    fixed_head: float | Sequence[float | None] | None = None,
    fluxes: Sequence[FluxBoundary] = (),
    max_iter: int = 60,
    initial_active: dict[str, np.ndarray] | None = None,
    factorization: Factorization | None = None,
    convertible: Sequence[Convertible | None] = (),
    barriers: Sequence[Barrier] = (),
) -> GridSolution:
    """``T`` per layer and ``c`` between layers: a number, or an (nrow, ncol) array for a
    subsoil that varies per cell.

    ``fixed_head``: one head for the outer ring of every layer, or one value per layer
    (None = closed for that layer), or None (closed model).

    ``initial_active``: starting state per group (default: all on), e.g. the final states
    of a previous run. ``factorization``: shared LU cache, only for runs on the same grid.
    ``convertible``: per layer a :class:`Convertible` or None (fixed); for a convertible
    layer ``T`` is only the starting value. ``barriers``: horizontal flow barriers on cell
    faces, applied in order (two on the same face act in series)."""
    factorization = factorization or Factorization()
    nl = len(T)
    n = nl * nrow * ncol
    idx = np.arange(n).reshape(nl, nrow, ncol)
    area = cell * cell

    shape = (nrow, ncol)
    conv = list(convertible) + [None] * (nl - len(convertible))
    t_cur = np.array([np.broadcast_to(np.asarray(t, dtype=float), shape) for t in T])

    def build_base(t_layers: np.ndarray) -> sp.csr_matrix:
        rows, cols, vals = [], [], []
        diag = np.zeros(n)

        def link(a: np.ndarray, b: np.ndarray, cond: float | np.ndarray) -> None:
            cond = np.broadcast_to(cond, a.shape).ravel()
            a, b = a.ravel(), b.ravel()
            rows.extend([a, b])
            cols.extend([b, a])
            vals.extend([-cond, -cond])
            np.add.at(diag, a, cond)
            np.add.at(diag, b, cond)

        for layer, t in enumerate(t_layers):
            cx, cy = face_conductances(t, layer, barriers, conv[layer], cell)
            link(idx[layer, :, :-1], idx[layer, :, 1:], cx)
            link(idx[layer, :-1, :], idx[layer, 1:, :], cy)
        for layer, ci in enumerate(c):
            link(idx[layer], idx[layer + 1], area / np.broadcast_to(np.asarray(ci, float), shape))
        return sp.csr_matrix(
            (np.concatenate(vals) if vals else np.empty(0),
             (np.concatenate(rows) if rows else np.empty(0, int),
              np.concatenate(cols) if cols else np.empty(0, int))),
            shape=(n, n),
        ) + sp.diags(diag)  # fmt: skip

    base = build_base(t_cur)

    if fixed_head is None or np.isscalar(fixed_head):
        ring_heads: list[float | None] = [fixed_head] * nl  # type: ignore[list-item]
    else:
        ring_heads = list(fixed_head)
        if len(ring_heads) != nl:
            raise ValueError("fixed_head needs one value per layer")
    h_fix = np.zeros(n)
    fixed = np.zeros(n, dtype=bool)
    ring = np.zeros((nrow, ncol), dtype=bool)
    ring[[0, -1], :] = True
    ring[:, [0, -1]] = True
    for layer, hf in enumerate(ring_heads):
        if hf is not None:
            fixed[idx[layer, ring]] = True
            h_fix[idx[layer, ring]] = hf
    free = np.flatnonzero(~fixed)
    fix = np.flatnonzero(fixed)

    # as MODFLOW 6: recharge, head-dependent boundaries and wells do not act on fixed-head
    # cells (their water would only pass straight into the fixed head)
    boundaries = [
        dataclasses.replace(b, conductance=np.where(fixed[b.keys], 0.0, b.conductance))
        for b in boundaries
    ]
    fluxes = [dataclasses.replace(f, rate=np.where(fixed[f.keys], 0.0, f.rate)) for f in fluxes]
    rhs_fixed_flux = np.zeros(n)
    for f in fluxes:
        np.add.at(rhs_fixed_flux, f.keys, f.rate)
    rhs0 = rhs_fixed_flux.copy()
    top_free = idx[0].ravel()[~fixed[idx[0].ravel()]]
    rhs0[top_free] += recharge * area

    def assemble(active: dict[str, np.ndarray]) -> tuple[sp.csr_matrix, np.ndarray]:
        d_add = np.zeros(n)
        rhs = rhs0.copy()
        for b in boundaries:
            on = active[b.name]
            np.add.at(d_add, b.keys[on], b.conductance[on])
            np.add.at(rhs, b.keys[on], b.conductance[on] * b.stage[on])
            if b.kind == "riv":  # inactive riv cells: fixed infiltration
                off = ~on
                np.add.at(rhs, b.keys[off], b.conductance[off] * (b.stage[off] - b.bottom[off]))
        return (base + sp.diags(d_add)).tocsr(), rhs

    active = {b.name: np.ones(b.keys.size, dtype=bool) for b in boundaries}
    for name, state in (initial_active or {}).items():
        if name in active and np.shape(state) == active[name].shape:
            active[name] = np.asarray(state, dtype=bool).copy()
    h = np.full(n, np.nan)
    h_t = None  # heads behind the current transmissivities of convertible layers
    converged = False
    iterations = 0
    M = base
    for iterations in range(1, max_iter + 1):  # noqa: B007 - used after the loop
        M, rhs = assemble(active)
        h_prev = h
        h = h_fix.copy()
        Mf = M[free][:, free].tocsc()
        b_free = rhs[free] - (M[free][:, fix] @ h[fix] if fix.size else 0.0)
        x0 = h_prev[free] if np.all(np.isfinite(h_prev[free])) else None
        h[free] = factorization.solve(Mf, b_free, x0, reuse=any(conv))

        changed = False
        for b in boundaries:
            hb = h[b.keys]
            if b.kind == "ghb":
                new = np.ones(hb.size, dtype=bool)
            else:
                new = hb > (b.bottom if b.kind == "riv" else b.stage)
            if not np.array_equal(new, active[b.name]):
                changed = True
            active[b.name] = new
        layers = [i for i, cv in enumerate(conv) if cv is not None]
        if layers:  # head change outside dry cells (whose head is meaningless and ill-posed)
            heads, prev = h.reshape(nl, nrow, ncol), h_prev.reshape(nl, nrow, ncol)
            for i in layers:
                wet = ~(conv[i].dry(heads[i]) & conv[i].dry(prev[i]))
                if not np.all(np.isfinite(prev[i])) or (
                    np.any(wet) and np.max(np.abs(heads[i] - prev[i])[wet]) > HEAD_TOL
                ):
                    changed = True
        if not changed:
            converged = True
            break
        if layers:  # new transmissivities for the next solve
            if h_t is None:
                h_t, step, relax = heads.copy(), np.zeros_like(heads), np.ones_like(heads)
            else:  # per cell: halve the step where it reverses (delta-bar-delta, as MF6)
                new_step = heads - h_t
                if iterations > RELAX_AFTER:
                    flip = new_step * step < 0
                    relax = np.where(flip, np.maximum(RELAX * relax, RELAX_MIN), relax)
                step = relax * new_step
                h_t = h_t + step
            for i in layers:
                t_cur[i] = np.asarray(conv[i].k) * conv[i].saturated(h_t[i])
            base = build_base(t_cur)

    flux_out = {}
    for b in boundaries:
        on, hb = active[b.name], h[b.keys]
        if b.kind == "riv":
            q = np.where(on, b.conductance * (b.stage - hb), b.conductance * (b.stage - b.bottom))
        else:
            q = np.where(on, b.conductance * (b.stage - hb), 0.0)
        flux_out[b.name] = float(q.sum())
    for f in fluxes:
        flux_out[f.name] = flux_out.get(f.name, 0.0) + float(f.rate.sum())

    M, rhs = assemble(active)
    lateral = 0.0
    if fix.size:  # external inflow needed to hold the ring = residual of the full equations
        lateral = float((M @ h - rhs)[fix].sum())

    response = None
    if fluxes and np.any(rhs_fixed_flux != 0):  # linear response to the fixed fluxes alone
        dh = np.zeros(n)
        dh[free] = factorization.solve(M[free][:, free].tocsc(), rhs_fixed_flux[free])
        response = dh.reshape(nl, nrow, ncol)

    flows = barrier_flows(barriers, h.reshape(nl, nrow, ncol), t_cur, conv, cell)
    total_recharge = recharge * area * top_free.size
    terms = np.array([total_recharge, lateral, *flux_out.values()])
    error = abs(terms.sum()) / max(np.abs(terms).sum() / 2.0, 1e-300)
    return GridSolution(
        heads=h.reshape(nl, nrow, ncol),
        fluxes=flux_out,
        recharge=total_recharge,
        lateral=lateral,
        iterations=iterations,
        converged=converged,
        mass_balance_error=float(error),
        active=active,
        flux_response=response,
        transmissivity=t_cur,
        dry=np.array(
            [
                np.zeros(shape, dtype=bool) if cv is None else np.broadcast_to(cv.dry(hl), shape)
                for cv, hl in zip(conv, h.reshape(nl, nrow, ncol), strict=True)
            ]
        ),
        barrier_flow=flows,
    )


def grid_resistance(T: float, c_eff: float, dx: float) -> float:
    """Extra resistance [d/m] between a line (watercourse) and the cell it lies in.

    Stiffness matching (De Lange principle) for a long line in a leaky layer with
    spreading length lambda = sqrt(T c_eff): exact 2T/lambda, discrete
    2T(1 - r)/dx + dx/c_eff with r + 1/r = 2 + (dx/lambda)^2.
    """
    if not np.isfinite(c_eff) or c_eff <= 0:
        return 0.0
    lam = np.sqrt(T * c_eff)
    alpha = (dx / lam) ** 2
    if alpha < 1e-8:
        return 0.0
    r = 1.0 + 0.5 * alpha - np.sqrt(alpha + 0.25 * alpha**2)
    k_exact = 2.0 * T / lam
    k_grid = 2.0 * T * (1.0 - r) / dx + dx / c_eff
    return float(max(1.0 / k_exact - 1.0 / k_grid, 0.0))
