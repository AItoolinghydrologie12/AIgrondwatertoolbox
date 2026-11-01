"""Combine imported watercourses into model watercourses with a level and a bottom:

- legger reaches (primary/secondary, with width and bottom) and TOP10NL water (the rest);
  TOP10NL pieces within ``NEAR`` of a legger line are dropped, so nothing counts twice;
- every line is cut into pieces of at most ``STEP`` and each piece gets the level area
  (peilgebied) holding its midpoint, so a line is split where the level changes;
- bottom = legger bottom (mean of upstream and downstream) where known, else level - depth;
  every piece keeps its own bottom;
- connected pieces with the same source, level area and width form one watercourse (its
  bottom is the length-weighted mean, each line keeps its offset from that mean).

Everything here works in model metres (origin at the lower-left corner of the model).
"""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from gwproducten.geo import water
from gwproducten.geo.waterschap import LEVEL_LABEL, LevelArea, Reach

STEP = 10.0  # m, longest piece of a line
NEAR = 10.0  # m, TOP10NL pieces closer to a legger line are dropped
MAX_GROUPS = 40
JOIN = 1.0  # m; a piece whose end lies this close to another piece is connected to it
WIDTH_STEP = 0.25  # m, rounding of the width
MIN_DEPTH = 0.10  # m; a bottom closer to (or above) the level is set to level - MIN_DEPTH
MAX_CELL_LENGTH = 5.0  # x cell: the model's upper limit of watercourse length per cell
DEPTHS = {  # default water depth [m] where the legger has no bottom
    "legger 1": 1.0, "legger 2": 0.6, "0,5 - 3 meter": 0.4, "3 - 6 meter": 0.8, "breed": 1.5,
}  # fmt: skip
KINDS = {  # what can be fetched (DEPTHS keys) -> label
    "legger 1": "Legger primair", "legger 2": "Legger secundair",
    "0,5 - 3 meter": "TOP10NL 0,5–3 m", "3 - 6 meter": "TOP10NL 3–6 m",
    "breed": "TOP10NL breder / vlakken",
}  # fmt: skip

Points = list[tuple[float, float]]


@dataclass
class Group:
    source: str  # e.g. "Legger primair", "TOP10NL 0,5 - 3 m"
    area: str | None  # level area code, None outside every area
    stage: float  # m NAP, NaN if unknown
    width: float
    bottom: float  # m NAP, length-weighted mean of the pieces; NaN if unknown
    legger_share: float = 0.0  # part of the length with its bottom from the legger
    bottom_adjusted: bool = False  # legger bottom at/above the level: set to level - MIN_DEPTH
    lines: list[Points] = field(default_factory=list)
    line_bottoms: list[float] = field(default_factory=list)  # m NAP, one per line
    cells: dict[tuple[int, int], float] = field(default_factory=dict)  # area water: length

    @property
    def bottom_from_legger(self) -> bool:
        return self.legger_share > 0.999

    @property
    def line_offsets(self) -> list[float]:
        """Bottom of each line relative to the watercourse bottom [m]."""
        return [round(b - self.bottom, 3) for b in self.line_bottoms]

    @property
    def length(self) -> float:
        lines = sum(
            math.dist(a, b) for ln in self.lines for a, b in zip(ln[:-1], ln[1:], strict=True)
        )
        return lines + sum(self.cells.values())


@dataclass
class Result:
    groups: list[Group]
    notes: list[str]


def densify(points: Sequence[tuple[float, float]], step: float = STEP) -> Points:
    """The line with extra vertices so no segment is longer than ``step``."""
    out: Points = [tuple(points[0])]  # type: ignore[list-item]
    for (x0, y0), (x1, y1) in zip(points[:-1], points[1:], strict=True):
        n = max(1, math.ceil(math.dist((x0, y0), (x1, y1)) / step))
        out += [(x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n) for k in range(1, n + 1)]
    return out


def area_index(x: np.ndarray, y: np.ndarray, areas: list[LevelArea], origin: tuple) -> np.ndarray:
    """Index of the level area holding each point (model metres); -1 outside all."""
    out = np.full(x.shape, -1)
    for i, area in enumerate(areas):
        inside = np.zeros(x.shape, dtype=bool)
        for ring in area.rings:  # even-odd over all rings: holes and parts
            inside ^= water._inside(x, y, water.to_local(ring, *origin))
        out[(out < 0) & inside] = i
    return out


def split_by_area(
    points: Points, areas: list[LevelArea], origin: tuple, keep: np.ndarray | None = None
) -> list[tuple[int, Points]]:
    """Pieces (area index, points) of a densified line; segments with keep False are left
    out (e.g. near a legger line)."""
    p = np.asarray(points)
    if len(p) < 2:
        return []
    mid = 0.5 * (p[:-1] + p[1:])
    idx = area_index(mid[:, 0], mid[:, 1], areas, origin)
    keep = np.ones(len(mid), dtype=bool) if keep is None else keep
    pieces: list[tuple[int, Points]] = []
    current: Points = []
    current_idx = None
    for k in range(len(mid)):
        if not keep[k] or (current and idx[k] != current_idx):
            if len(current) >= 2:
                pieces.append((int(current_idx), current))
            current = []
        if keep[k]:
            if not current:
                current = [tuple(p[k])]
                current_idx = idx[k]
            current.append(tuple(p[k + 1]))
    if len(current) >= 2:
        pieces.append((int(current_idx), current))
    return pieces


def _stage(areas: list[LevelArea], i: int, choice: str, fallback: float, notes: set) -> float:
    if i < 0:
        return fallback
    value, used = areas[i].level(choice)
    if used is None:
        notes.add(f"Peilgebied {areas[i].code} heeft geen {LEVEL_LABEL[choice]}; peil invullen.")
        return fallback
    if used != choice:
        notes.add(
            f"Peilgebied {areas[i].code} heeft geen {LEVEL_LABEL[choice]}: "
            f"{LEVEL_LABEL[used]} gebruikt."
        )
    return value


@dataclass
class _Piece:
    """A line piece (``line``) or the area-water cells (``cells``) of one level area."""

    label: str
    area: int  # level area index, -1 outside all
    stage: float
    width: float
    bottom: float
    known: bool  # bottom from the legger
    line: Points | None = None
    cells: dict[tuple[int, int], float] | None = None
    adjusted: bool = False

    @property
    def length(self) -> float:
        if self.line is not None:
            return sum(math.dist(a, b) for a, b in zip(self.line[:-1], self.line[1:], strict=True))
        return sum((self.cells or {}).values())


def combine(
    top10: list[water.Water],
    reaches: list[Reach],
    areas: list[LevelArea],
    origin: tuple[float, float],
    grid: tuple[float, int, int],
    *,
    choice: str = "winter",
    depths: dict[str, float] | None = None,
    default_stage: float = math.nan,
    include: Collection[str] | None = None,
) -> Result:
    """Model watercourses (see module docstring). ``grid`` = (cell, nrow, ncol); ``include``:
    the KINDS to keep (default all). Left-out legger reaches still remove the TOP10NL water
    along them, so TOP10NL stays 'the rest'."""
    depths = {**DEPTHS, **(depths or {})}
    include = set(KINDS if include is None else include)
    cell, nrow, ncol = grid
    notes: set[str] = set()
    pieces: list[_Piece] = []

    legger_pts: list[np.ndarray] = []
    for r in reaches:
        label = "Legger primair" if r.category == 1 else "Legger secundair"
        key = f"legger {1 if r.category == 1 else 2}"
        for line in r.lines:
            dense = densify(water.to_local(line, *origin), STEP / 2)
            legger_pts.append(np.asarray(dense))
            if key not in include:
                continue
            for i, piece in split_by_area(dense, areas, origin):
                stage = _stage(areas, i, choice, default_stage, notes)
                known = math.isfinite(r.bottom)
                bottom = r.bottom if known else stage - depths[key]
                width = r.width if math.isfinite(r.width) and r.width > 0 else 1.0
                pieces.append(_Piece(label, i, stage, width, bottom, known, line=piece))
    tree = cKDTree(np.vstack(legger_pts)) if legger_pts else None

    capped = False
    for w in top10:
        width = water.class_width(w.width_class)
        if w.area and w.width_class == "onbekend":
            width = water.WIDE_WIDTH  # an area without width class is wide water
        key = w.width_class if w.width_class in depths else "breed"
        if key not in include:
            continue
        label = (
            f"TOP10NL {w.width_class.replace(' meter', ' m')}"
            if w.width_class != "onbekend"
            else "TOP10NL breedte onbekend"
        )
        if w.area:
            rings = [water.to_local(r, *origin) for r in w.parts]
            cells = water.polygon_cell_lengths(rings, width, cell, nrow, ncol)
            if not cells:
                continue
            if max(cells.values()) > MAX_CELL_LENGTH * cell:
                capped = True
                cells = {c: min(v, MAX_CELL_LENGTH * cell) for c, v in cells.items()}
            rc = np.array(list(cells))
            cx, cy = (rc[:, 1] + 0.5) * cell, (rc[:, 0] + 0.5) * cell
            idx = area_index(cx, cy, areas, origin)
            near = (
                np.isfinite(tree.query(np.c_[cx, cy], distance_upper_bound=cell / 2)[0])
                if tree is not None
                else np.zeros(len(rc), dtype=bool)
            )
            for i in sorted(set(idx[~near].tolist())):
                part = {
                    (int(c[0]), int(c[1])): cells[(int(c[0]), int(c[1]))]
                    for c, j, n in zip(rc, idx, near, strict=True)
                    if j == i and not n
                }
                stage = _stage(areas, i, choice, default_stage, notes)
                bottom = stage - depths["breed"]
                pieces.append(_Piece(label, i, stage, width, bottom, False, cells=part))
            continue
        dense = densify(water.to_local(w.parts[0], *origin))
        p = np.asarray(dense)
        mid = 0.5 * (p[:-1] + p[1:])
        keep = (
            ~np.isfinite(tree.query(mid, distance_upper_bound=NEAR)[0])
            if tree is not None
            else None
        )
        for i, piece in split_by_area(dense, areas, origin, keep):
            stage = _stage(areas, i, choice, default_stage, notes)
            pieces.append(_Piece(label, i, stage, width, stage - depths[key], False, line=piece))

    dry: set[str] = set()
    for pc in pieces:
        pc.bottom = round(pc.bottom, 2) if math.isfinite(pc.bottom) else math.nan
        if math.isfinite(pc.stage) and pc.bottom > pc.stage - MIN_DEPTH:
            if pc.known:
                dry.add(areas[pc.area].code if pc.area >= 0 else "-")
                pc.adjusted = True
            pc.bottom = round(pc.stage - MIN_DEPTH, 2)
    groups, merged = _group(pieces, areas)
    if merged:
        notes.add(
            f"Meer dan {MAX_GROUPS} losse watergangen: stukken van dezelfde soort, peilgebied "
            "en breedte zijn samengevoegd, ook als ze niet aan elkaar vastzitten. Kies minder "
            "soorten voor een overzichtelijker tabel."
        )
    if dry:
        notes.add(
            "Bodem uit de legger ligt op of boven het gekozen peil in peilgebied "
            f"{', '.join(sorted(dry))} (de watergang staat dan vrijwel droog). Voor het model is "
            f"die bodem op peil − {MIN_DEPTH:.2f} m gezet; pas aan in de tabel, of zet het type "
            "op drain."
        )
    if capped:
        notes.add(
            f"Bij brede wateren is de lengte per cel begrensd op {MAX_CELL_LENGTH:g} × de "
            "celgrootte (de bovengrens van het model)."
        )
    if any(g.area is None for g in groups):
        notes.add("Een deel van de watergangen ligt buiten de peilgebieden: peil invullen.")
    return Result(groups, sorted(notes))


def connected_lines(lines: list[Points], tol: float = JOIN) -> list[list[int]]:
    """Indices of the lines per connected set: two lines are connected when an end of one
    lies within ``tol`` of the other (end to end, or a side ditch onto a main ditch)."""
    n = len(lines)
    if n == 0:
        return []
    parent = list(range(n))

    def root(k: int) -> int:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    seg_a = np.vstack([np.asarray(ln[:-1], dtype=float) for ln in lines])
    seg_b = np.vstack([np.asarray(ln[1:], dtype=float) for ln in lines])
    owner = np.concatenate([np.full(len(ln) - 1, k) for k, ln in enumerate(lines)])
    d = seg_b - seg_a
    dd = np.maximum((d**2).sum(axis=1), 1e-12)
    for k, ln in enumerate(lines):
        for end in (ln[0], ln[-1]):
            e = np.asarray(end, dtype=float)
            t = np.clip(((e - seg_a) * d).sum(axis=1) / dd, 0.0, 1.0)
            dist = np.hypot(*(seg_a + t[:, None] * d - e).T)
            for j in set(owner[(dist <= tol) & (owner != k)].tolist()):
                parent[root(j)] = root(k)
    sets: dict[int, list[int]] = {}
    for k in range(n):
        sets.setdefault(root(k), []).append(k)
    return list(sets.values())


def _cell_sets(cells: dict[tuple[int, int], float]) -> list[dict[tuple[int, int], float]]:
    """Area-water cells per connected set (8-connected)."""
    rc = np.array(list(cells))
    r0, c0 = rc.min(axis=0)
    mask = np.zeros(tuple(rc.max(axis=0) - (r0, c0) + 1), dtype=bool)
    mask[rc[:, 0] - r0, rc[:, 1] - c0] = True
    labels, n = ndimage.label(mask, structure=np.ones((3, 3)))
    out: list[dict[tuple[int, int], float]] = [{} for _ in range(n)]
    for (r, c), length in cells.items():
        out[labels[r - r0, c - c0] - 1][(r, c)] = length
    return out


def _group(pieces: list[_Piece], areas: list[LevelArea]) -> tuple[list[Group], bool]:
    """Watercourses: per source, level area and width (rounded to WIDTH_STEP) the connected
    lines, and the connected area-water cells. With more than MAX_GROUPS the split by
    connection is dropped (returns merged=True)."""
    buckets: dict[tuple, list[_Piece]] = {}
    for pc in pieces:
        w = max(round(pc.width / WIDTH_STEP) * WIDTH_STEP, WIDTH_STEP)
        buckets.setdefault((pc.label, pc.area, w), []).append(pc)

    def sets(merge: bool) -> list[tuple[tuple, list[_Piece], dict | None]]:
        out: list[tuple[tuple, list[_Piece], dict | None]] = []
        for key, members in buckets.items():
            lines = [pc for pc in members if pc.line is not None]
            if lines:
                parts = (
                    [list(range(len(lines)))]
                    if merge
                    else connected_lines([pc.line for pc in lines])  # type: ignore[misc]
                )
                out += [(key, [lines[k] for k in part], None) for part in parts]
            cells: dict[tuple[int, int], float] = {}
            for pc in members:
                for c, length in (pc.cells or {}).items():
                    cells[c] = cells.get(c, 0.0) + length
            if cells:
                area_pcs = [pc for pc in members if pc.cells]
                out += [(key, area_pcs, part) for part in ([cells] if merge else _cell_sets(cells))]
        return out

    found = sets(merge=False)
    merged = len(found) > MAX_GROUPS
    if merged:
        found = sets(merge=True)
    groups = []
    for (label, i, w), members, cells in found:
        code = areas[i].code if i >= 0 else None
        if cells is not None:  # area water: one bottom per bucket
            groups.append(Group(label, code, members[0].stage, w, members[0].bottom, cells=cells))
            continue
        lengths = np.array([pc.length for pc in members])
        bottoms = np.array([pc.bottom for pc in members])
        total = max(float(lengths.sum()), 1e-9)
        mean = (
            round(float((lengths * bottoms).sum() / total), 2)
            if np.isfinite(bottoms).all()
            else math.nan
        )
        groups.append(
            Group(
                label, code, members[0].stage, w, mean,
                legger_share=float(lengths[[pc.known for pc in members]].sum() / total),
                bottom_adjusted=any(pc.adjusted for pc in members),
                lines=[pc.line for pc in members],  # type: ignore[misc]
                line_bottoms=[float(b) for b in bottoms],
            )
        )  # fmt: skip
    groups.sort(key=lambda g: (g.area or "~", g.source, -g.length))
    return groups, merged
