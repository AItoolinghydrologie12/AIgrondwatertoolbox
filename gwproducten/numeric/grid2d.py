"""Building blocks of the plan-view grid model that MODFLOW 6 solves.

Uniform square cells, one model layer per node (quasi-3D: aquitards as resistances), recharge
on the top node, and MODFLOW-style boundary groups:

- ``riv``: flux C (stage - h) while h > bottom, else the fixed C (stage - bottom);
- ``drn``: flux C (level - h) while h > level, else 0;
- ``ghb``: flux C (stage - h), always active;

plus fixed fluxes (wells), groups of fixed-head cells (CHD) and horizontal flow barriers
(sheet pile walls, HFB) on cell faces. A layer can be *convertible* (MODFLOW 6 ICELLTYPE > 0):
its transmissivity follows the saturated thickness. Absolute heads [m]; flux positive into the
groundwater. The groups are written to MODFLOW 6 by ``products.peilopzet_grid.mf6``; the
solution comes back as a :class:`GridSolution`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

MIN_SATURATED_FRACTION = 0.01  # saturated thickness never below 1 % of the layer thickness


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
class FixedHeads:
    """Group of fixed-head cells (CHD): ``head`` [m] per entry. Its flux is what holds the
    heads (the residual of the cell equations), positive = into the groundwater."""

    name: str
    keys: np.ndarray  # flat unknown indices (node, row, col)
    head: np.ndarray


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
    iterations: int  # outer iterations of MODFLOW 6
    mass_balance_error: float
    active: dict[str, np.ndarray]  # per group: head-dependent state per entry
    transmissivity: np.ndarray  # (n_layers, nrow, ncol) [m2/d] at the final heads
    dry: np.ndarray  # (n_layers, nrow, ncol): convertible cells at the minimum thickness
    barrier_flow: tuple[np.ndarray, ...] = ()  # per barrier: flow a -> b per face [m3/d]
    # per well group: actual rate per entry [m3/d] (less than asked when a cell fell dry)
    well_rates: dict[str, np.ndarray] = dataclasses.field(default_factory=dict)
    # per boundary group: convertible cells held at fixed T (level below the cell bottom)
    fixed_t_groups: dict[str, int] = dataclasses.field(default_factory=dict)


def _harmonic(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Harmonic mean 2ab/(a+b): two half cells in series (exact a for a == b)."""
    return np.where(a == b, a, 2.0 * a * b / np.maximum(a + b, 1e-300))


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
