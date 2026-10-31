"""Steady x-z cross-section (cell-centred finite volumes) for layered leaky systems.

Unlike the 1D models, vertical flow inside each layer is resolved, so the flow
contraction toward a watercourse (the radial resistance that the analytic C' contains
via ln(D/u)/(pi k)) follows from the grid itself.

Watercourses are rectangular troughs from the surface down to their bottom. Cells
inside the trough are inactive. Below the water level every face between the trough and
an active cell exchanges water with conductance A / (c_entry + d / k), with d the
distance from cell centre to face, whatever layer that cell is in (open banks). Above
the water level the trough is dry and its faces are closed. Because the wetted
perimeter is resolved geometrically, no De Lange cell-size correction is applied; the
grid must be fine near the ditch instead (verified by the refinement tests).

The trench is a column of cells over the full thickness of its layer with a very high
vertical conductivity (uniform head over the screen).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from gwproducten.core.grids import refined_axis
from gwproducten.numeric._sparse import ConductanceSystem


@dataclass(frozen=True)
class XZLayer:
    thickness: float  # [m]
    kh: float  # [m/d]
    kv: float  # [m/d]
    nsub: int  # number of computational rows


@dataclass(frozen=True)
class XZDitch:
    x: float  # centre [m]
    width: float  # bottom width b [m]
    z_bottom: float  # bottom level [m], negative below surface
    z_water: float  # water level [m], between z_bottom and 0
    entry_resistance: float  # c_entry [d]
    level: float = 0.0  # level change [m]
    name: str = ""


@dataclass(frozen=True)
class XZTrench:
    x: float
    layer: int
    kind: str  # "flux" (prescribed injection, extraction < 0) or "head"
    value: float


@dataclass(frozen=True)
class XZResult:
    x: np.ndarray  # cell centres
    z: np.ndarray  # cell centres (0 = top, negative downward)
    phi: np.ndarray  # (nz, nx), NaN in inactive cells
    layer_of_row: np.ndarray
    layer_mean: np.ndarray  # (n_layers, nx) thickness-weighted mean head change per layer
    Q_trench: float
    Q_ditches: np.ndarray
    drainage: float
    lateral: float
    mass_balance_error: float
    n_cells: int


def _neighbours(mask: np.ndarray, shift: int, axis: int) -> np.ndarray:
    """Cells directly next to ``mask`` in one direction (no wrap-around)."""
    nb = np.roll(mask, shift, axis=axis)
    edge = 0 if shift == 1 else -1
    if axis == 1:
        nb[:, edge] = False
    else:
        nb[edge, :] = False
    return nb


def solve_xsection(
    layers: list[XZLayer],
    w: float,
    ditches: list[XZDitch],
    trench: XZTrench | None,
    *,
    extent: float,
    dx_min: float = 0.25,
    dx_max: float = 20.0,
    growth: float = 1.15,
) -> XZResult:
    # vertical axis: layer interfaces plus ditch bottoms
    tops = -np.concatenate([[0.0], np.cumsum([lay.thickness for lay in layers])])
    z_edges: list[float] = []
    for i, lay in enumerate(layers):
        z_edges.extend(np.linspace(tops[i], tops[i + 1], lay.nsub + 1)[:-1].tolist())
    z_edges.append(float(tops[-1]))
    for d in ditches:
        z_edges += [d.z_bottom, d.z_water]
    ze = np.unique(np.round(z_edges, 9))[::-1]  # top -> bottom
    zc = 0.5 * (ze[:-1] + ze[1:])
    dz = ze[:-1] - ze[1:]
    row_layer = np.searchsorted(-tops[1:], -zc)  # layer index per row
    kh = np.array([layers[i].kh for i in row_layer])
    kv = np.array([layers[i].kv for i in row_layer])

    # horizontal axis: trench column and ditch walls are cell edges
    forced: list[float] = []
    xs = [d.x for d in ditches]
    if trench is not None:
        forced += [trench.x - dx_min / 2, trench.x + dx_min / 2]
        xs.append(trench.x)
    for d in ditches:
        forced += [d.x - d.width / 2, d.x + d.width / 2]
    xe = refined_axis(min(xs) - extent, max(xs) + extent, forced, dx_min, dx_max, growth)
    xc = 0.5 * (xe[:-1] + xe[1:])
    dx = np.diff(xe)
    nz, nx = zc.size, xc.size

    # trough cells are inactive; wet trough cells remember which ditch they belong to
    ditch_of = np.full((nz, nx), -1)
    trough = np.zeros((nz, nx), dtype=bool)
    for n, d in enumerate(ditches):
        in_x = (xc > d.x - d.width / 2) & (xc < d.x + d.width / 2)
        trough[np.ix_(zc > d.z_bottom, in_x)] = True
        ditch_of[np.ix_((zc > d.z_bottom) & (zc < d.z_water), in_x)] = n
    active = ~trough
    kv_cell = np.broadcast_to(kv[:, None], (nz, nx)).copy()
    if trench is not None:
        trench_col = int(np.argmin(np.abs(xc - trench.x)))
        trench_rows = np.flatnonzero(row_layer == trench.layer)
        kv_cell[trench_rows, trench_col] = 1e6 * kh[trench_rows]

    index = np.full((nz, nx), -1)
    index[active] = np.arange(active.sum())
    cs = ConductanceSystem(int(active.sum()))

    # horizontal faces between columns i and i+1
    Ch = dz[:, None] / (dx[None, :-1] / (2 * kh[:, None]) + dx[None, 1:] / (2 * kh[:, None]))
    both = active[:, :-1] & active[:, 1:]
    cs.connect(index[:, :-1][both], index[:, 1:][both], Ch[both])
    # vertical faces between rows j and j+1
    Cv = dx[None, :] / (dz[:-1, None] / (2 * kv_cell[:-1]) + dz[1:, None] / (2 * kv_cell[1:]))
    both = active[:-1] & active[1:]
    cs.connect(index[:-1][both], index[1:][both], Cv[both])

    # wet ditch faces (open banks: every layer)
    ditch_terms: list[tuple[np.ndarray, np.ndarray]] = []
    for n, d in enumerate(ditches):
        cells, cond = [], []
        for shift, axis in ((1, 1), (-1, 1), (1, 0), (-1, 0)):
            nb = _neighbours(ditch_of == n, shift, axis)
            nb &= active
            jj, ii = np.nonzero(nb)
            if axis == 1:
                area, dist, k = dz[jj], dx[ii] / 2, kh[jj]
            else:
                area, dist, k = dx[ii], dz[jj] / 2, kv[jj]
            cells.append(index[jj, ii])
            cond.append(area / (d.entry_resistance + dist / k))
        cells_n, cond_n = np.concatenate(cells), np.concatenate(cond)
        cs.boundary(cells_n, cond_n, d.level)
        ditch_terms.append((cells_n, cond_n))

    # drainage through w on top of the active top-row cells
    top_cols = np.flatnonzero(active[0])
    C_drain = dx[top_cols] / (w + dz[0] / (2 * kv[0]))
    cs.boundary(index[0, top_cols], C_drain, 0.0)

    # lateral ends: zero head change at the outer faces
    lat_cells, lat_cond = [], []
    for col in (0, nx - 1):
        rows = np.flatnonzero(active[:, col])
        lat_cells.append(index[rows, col])
        lat_cond.append(dz[rows] / (dx[col] / (2 * kh[rows])))
    lat_cells_a, lat_cond_a = np.concatenate(lat_cells), np.concatenate(lat_cond)
    cs.boundary(lat_cells_a, lat_cond_a, 0.0)

    fixed: dict[int, float] = {}
    Q_trench = 0.0
    if trench is not None:
        trench_cells = index[trench_rows, trench_col]
        if trench.kind == "flux":
            share = kh[trench_rows] * dz[trench_rows]
            cs.source(trench_cells, trench.value * share / share.sum())
            Q_trench = trench.value
        else:
            fixed = {int(k): trench.value for k in trench_cells}

    phi_a, external = cs.solve(fixed)
    if fixed:
        Q_trench = float(external.sum())
    Q_ditches = np.array(
        [
            float(np.sum(cond * (d.level - phi_a[cells])))
            for d, (cells, cond) in zip(ditches, ditch_terms, strict=True)
        ]
    )
    drainage = float(np.sum(C_drain * phi_a[index[0, top_cols]]))
    lateral = float(np.sum(lat_cond_a * phi_a[lat_cells_a]))
    terms = np.concatenate([[Q_trench], Q_ditches, [-drainage, -lateral]])
    error = abs(terms.sum()) / max(np.abs(terms).sum() / 2.0, 1e-300)

    phi = np.full((nz, nx), np.nan)
    phi[active] = phi_a
    layer_mean = np.full((len(layers), nx), np.nan)
    for i in range(len(layers)):
        rows = row_layer == i
        wgt = np.where(active[rows], dz[rows, None], 0.0)
        total = wgt.sum(axis=0)
        ok = total > 0
        layer_mean[i, ok] = (np.nan_to_num(phi[rows]) * wgt).sum(axis=0)[ok] / total[ok]
    return XZResult(
        x=xc,
        z=zc,
        phi=phi,
        layer_of_row=row_layer,
        layer_mean=layer_mean,
        Q_trench=Q_trench,
        Q_ditches=Q_ditches,
        drainage=drainage,
        lateral=lateral,
        mass_balance_error=float(error),
        n_cells=int(active.sum()),
    )
