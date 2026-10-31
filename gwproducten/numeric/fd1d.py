"""1D finite-volume model of a multi-layer leaky system (quasi-3D, Dupuit per layer).

Same schematisation as :mod:`gwproducten.analytic.multilayer`, so it serves as an
independent check of the analytic superposition and of grid convergence.

Watercourses: a node lies exactly on each line element and the element is attached to
that node with conductance C' (entry + radial resistance, see
:func:`gwproducten.analytic.boundaries.ditch_conductance`). Because the 1D grid has no
vertical resolution, the flow contraction toward the ditch is not modelled by the grid
itself; the radial term in C' is therefore not double-counted and no De Lange
cell-size correction is needed. The control-volume head at a node equals the head on
the line, so the result converges to the analytic line-element solution as dx -> 0.

Lateral ends are fixed at zero head change; place them far away (>= 10 lambda).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from gwproducten.analytic.multilayer import LayerSystem, LineElement
from gwproducten.core.grids import refined_axis
from gwproducten.numeric._sparse import ConductanceSystem


@dataclass(frozen=True)
class FD1DResult:
    x: np.ndarray
    phi: np.ndarray  # (n_layers, n_nodes)
    Q: np.ndarray  # injection per element [m2/d per m]
    drainage: float  # outflow through w [m2/d per m]
    lateral: float  # outflow through the lateral ends [m2/d per m]
    mass_balance_error: float
    n_nodes: int


def solve_fd1d(
    system: LayerSystem,
    elements: list[LineElement] | tuple[LineElement, ...],
    *,
    dx_min: float = 0.5,
    dx_max: float | None = None,
    growth: float = 1.05,  # mild stretching keeps the scheme close to second order
    extent: float | None = None,
) -> FD1DResult:
    elements = tuple(elements)
    xs = [e.x for e in elements]
    if extent is None:
        extent = 12.0 * float(system.leakage_factors[0])
    if dx_max is None:
        # resolve the shortest mode, but never below 4 dx_min or lambda_1/100: a mode much
        # shorter than the refinement near the elements carries no information farther away
        lam = system.leakage_factors
        dx_max = min(25.0, max(0.5 * float(lam[-1]), 4.0 * dx_min, float(lam[0]) / 100.0))
    x = refined_axis(min(xs) - extent, max(xs) + extent, xs, dx_min, dx_max, growth)
    nx, nl = x.size, system.n
    idx = np.arange(nl * nx).reshape(nl, nx)

    vol = np.empty(nx)  # control length per node
    vol[1:-1] = 0.5 * (x[2:] - x[:-2])
    vol[0], vol[-1] = 0.5 * (x[1] - x[0]), 0.5 * (x[-1] - x[-2])

    cs = ConductanceSystem(nl * nx)
    for layer, T in enumerate(system.T):
        cs.connect(idx[layer, :-1], idx[layer, 1:], T / np.diff(x))
    for layer, c in enumerate(system.c):
        cs.connect(idx[layer], idx[layer + 1], vol / c)
    C_drain = vol / system.w
    cs.boundary(idx[0], C_drain, 0.0)

    fixed: dict[int, float] = {}
    for layer in range(nl):
        fixed[int(idx[layer, 0])] = 0.0
        fixed[int(idx[layer, -1])] = 0.0
    node_of = [int(np.argmin(np.abs(x - e.x))) for e in elements]
    for e, i in zip(elements, node_of, strict=True):
        k = int(idx[e.layer, i])
        if e.kind == "flux":
            cs.source(k, e.value)
        elif math.isinf(e.conductance):
            fixed[k] = e.value
        else:
            cs.boundary(k, e.conductance, e.value)

    phi_flat, external = cs.solve(fixed)
    phi = phi_flat.reshape(nl, nx)
    ext = dict(zip(fixed.keys(), external, strict=True))

    Q = np.empty(len(elements))
    for n, (e, i) in enumerate(zip(elements, node_of, strict=True)):
        k = int(idx[e.layer, i])
        if e.kind == "flux":
            Q[n] = e.value
        elif math.isinf(e.conductance):
            Q[n] = ext.pop(k)
        else:
            Q[n] = e.conductance * (e.value - phi_flat[k])
    lateral = -float(sum(ext.values()))
    drainage = float(np.sum(C_drain * phi[0]))
    terms = np.concatenate([Q, [-drainage, -lateral]])
    error = abs(terms.sum()) / max(np.abs(terms).sum() / 2.0, 1e-300)
    return FD1DResult(x, phi, Q, drainage, lateral, float(error), nx)
