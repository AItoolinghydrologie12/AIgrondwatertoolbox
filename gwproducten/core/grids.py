"""Grid generation: 1D axes refined around prescribed points."""

from __future__ import annotations

import numpy as np


def _fill(
    length: float, dx_min: float, dx_max: float, growth: float, grow_left: bool, grow_right: bool
) -> np.ndarray:
    """Cell sizes covering ``length``, small at the refined end(s), growing geometrically."""
    if length <= 0:
        return np.empty(0)
    dx_max = max(dx_max, dx_min)
    n_ramp = int(np.ceil(np.log(dx_max / dx_min) / np.log(growth))) if dx_max > dx_min else 0
    ramp = dx_min * growth ** np.arange(n_ramp)
    ramp = ramp[ramp < dx_max]
    sides = int(grow_left) + int(grow_right)
    if sides == 0:
        sizes = np.full(max(int(np.ceil(length / dx_max)), 1), dx_max)
    elif sides * ramp.sum() >= length:
        k = int(np.searchsorted(sides * np.cumsum(ramp), length)) + 1
        part = ramp[:k]
        sizes = np.concatenate([part, part[::-1]]) if sides == 2 else part
    else:
        rest = length - sides * ramp.sum()
        middle = np.full(int(np.ceil(rest / dx_max)), dx_max)
        left = ramp if grow_left else np.empty(0)
        right = ramp[::-1] if grow_right else np.empty(0)
        sizes = np.concatenate([left, middle, right])
    return sizes * (length / sizes.sum())


def refined_axis(
    xmin: float,
    xmax: float,
    points: list[float] | np.ndarray,
    dx_min: float,
    dx_max: float,
    growth: float = 1.2,
) -> np.ndarray:
    """Sorted coordinates from ``xmin`` to ``xmax`` that contain every point in ``points``.

    Spacing is ``dx_min`` next to each point and grows by ``growth`` per step up to
    ``dx_max``. The domain ends are not refined.
    """
    if not xmin < xmax:
        raise ValueError("xmin must be smaller than xmax")
    pts = np.unique(np.clip(np.asarray(points, dtype=float), xmin, xmax))
    knots = np.unique(np.concatenate([[xmin], pts, [xmax]]))
    refined = set(pts.tolist())
    coords = [knots[0]]
    for a, b in zip(knots[:-1], knots[1:], strict=True):
        sizes = _fill(b - a, dx_min, dx_max, growth, a in refined, b in refined)
        coords.extend((a + np.cumsum(sizes))[:-1].tolist())
        coords.append(b)
    return np.asarray(coords)


def polyline_cell_lengths(
    vertices: list[tuple[float, float]], cell: float, nrow: int, ncol: int
) -> dict[tuple[int, int], float]:
    """Length [m] of a polyline inside every cell of a square grid with origin (0, 0).

    Each segment is cut at all crossings with the grid lines; every piece belongs to the
    cell holding its midpoint (a piece exactly on a grid line goes to the cell on the
    upper/right side). Parts outside the grid are dropped. Keys are (row, col), row 0 at
    the bottom (y = 0).
    """
    width, height = ncol * cell, nrow * cell
    out: dict[tuple[int, int], float] = {}
    for (x0, y0), (x1, y1) in zip(vertices[:-1], vertices[1:], strict=True):
        dx, dy = x1 - x0, y1 - y0
        seg_len = float(np.hypot(dx, dy))
        if seg_len == 0.0:
            continue
        ts = [0.0, 1.0]
        for p0, d, n in ((x0, dx, ncol), (y0, dy, nrow)):
            if d != 0.0:
                k = np.arange(0, n + 1) * cell
                t = (k - p0) / d
                ts.extend(t[(t > 0.0) & (t < 1.0)].tolist())
        ts_a = np.unique(ts)
        for ta, tb in zip(ts_a[:-1], ts_a[1:], strict=True):
            tm = 0.5 * (ta + tb)
            xm, ym = x0 + tm * dx, y0 + tm * dy
            if not (0.0 <= xm <= width and 0.0 <= ym <= height):
                continue
            col = min(int(xm // cell), ncol - 1)
            row = min(int(ym // cell), nrow - 1)
            out[(row, col)] = out.get((row, col), 0.0) + (tb - ta) * seg_len
    return out
