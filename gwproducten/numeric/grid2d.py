"""Steady plan-view grid model (cell-centred finite volumes, quasi-3D layers).

Uniform square cells, one computational layer per node with fixed transmissivity,
recharge on the top node, and MODFLOW-style head-dependent boundaries:

- ``riv``: flux C (stage - h) while h > bottom, else the fixed C (stage - bottom);
- ``drn``: flux C (level - h) while h > level, else 0;
- ``ghb``: flux C (stage - h), always active;

plus fixed fluxes (wells) and an outer ring of fixed heads per layer.

The on/off switching is solved by Picard iteration: solve, update every state, repeat until
no state changes. Absolute heads [m]; flux positive into the groundwater.

The states only change the diagonal, so one LU factorisation (minimum-degree ordering) is
reused: exactly for an identical matrix, otherwise as preconditioner for conjugate gradients.
A :class:`Factorization` can be shared between runs on the same grid (reference and opzet).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import LinearOperator, cg, splu

PCG_RTOL = 1e-12
PCG_MAXITER = 40  # more iterations than this: refactorise instead
# PCG converges in at most k+1 steps for k changed entries; refactorise beyond this many
PCG_MAX_CHANGED = 50
PCG_MAX_CHANGED_FRACTION = 0.02


class Factorization:
    """Cached LU of the last factorised matrix; solves nearby matrices by preconditioned CG."""

    def __init__(self) -> None:
        self.matrix: sp.csc_matrix | None = None
        self.lu = None
        self.factorizations = 0

    def _factor(self, M: sp.csc_matrix) -> None:
        self.matrix = M
        self.lu = splu(M, permc_spec="MMD_AT_PLUS_A", options={"SymmetricMode": True})
        self.factorizations += 1

    def solve(self, M: sp.csc_matrix, b: np.ndarray, x0: np.ndarray | None = None) -> np.ndarray:
        if self.matrix is None or self.matrix.shape != M.shape:
            self._factor(M)
            return self.lu.solve(b)
        changed = (self.matrix != M).nnz
        if changed == 0:
            return self.lu.solve(b)
        if changed > max(PCG_MAX_CHANGED, PCG_MAX_CHANGED_FRACTION * M.shape[0]):
            self._factor(M)
            return self.lu.solve(b)
        P = LinearOperator(M.shape, matvec=self.lu.solve, dtype=float)
        x, info = cg(M, b, x0=x0, M=P, rtol=PCG_RTOL, atol=0.0, maxiter=PCG_MAXITER)
        if info == 0:
            return x
        self._factor(M)
        return self.lu.solve(b)


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
) -> GridSolution:
    """``T`` per layer and ``c`` between layers: a number, or an (nrow, ncol) array for a
    subsoil that varies per cell.

    ``fixed_head``: one head for the outer ring of every layer, or one value per layer
    (None = closed for that layer), or None (closed model).

    ``initial_active``: starting state per group (default: all on), e.g. the final states
    of a previous run. ``factorization``: shared LU cache, only for runs on the same grid."""
    factorization = factorization or Factorization()
    nl = len(T)
    n = nl * nrow * ncol
    idx = np.arange(n).reshape(nl, nrow, ncol)
    area = cell * cell

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

    shape = (nrow, ncol)
    for layer, t_in in enumerate(T):  # square cells: T dy/dx = T; harmonic mean between cells
        t = np.broadcast_to(np.asarray(t_in, dtype=float), shape)
        link(idx[layer, :, :-1], idx[layer, :, 1:], _harmonic(t[:, :-1], t[:, 1:]))
        link(idx[layer, :-1, :], idx[layer, 1:, :], _harmonic(t[:-1, :], t[1:, :]))
    for layer, ci in enumerate(c):
        link(idx[layer], idx[layer + 1], area / np.broadcast_to(np.asarray(ci, float), shape))
    base = sp.csr_matrix(
        (np.concatenate(vals) if vals else np.empty(0),
         (np.concatenate(rows) if rows else np.empty(0, int),
          np.concatenate(cols) if cols else np.empty(0, int))),
        shape=(n, n),
    ) + sp.diags(diag)  # fmt: skip
    rhs_fixed_flux = np.zeros(n)
    for f in fluxes:
        np.add.at(rhs_fixed_flux, f.keys, f.rate)
    rhs0 = rhs_fixed_flux.copy()
    rhs0[idx[0].ravel()] += recharge * area

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
        h[free] = factorization.solve(Mf, b_free, x0)

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
        if not changed:
            converged = True
            break

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

    total_recharge = recharge * area * nrow * ncol
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
