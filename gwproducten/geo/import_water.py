"""Combine imported watercourses into model watercourses with a level and a bottom:

- legger reaches (primary/secondary, with width and bottom) and TOP10NL water (the rest);
  TOP10NL pieces within ``NEAR`` of a legger line are dropped, so nothing counts twice;
- every line is cut into pieces of at most ``STEP`` and each piece gets the level area
  (peilgebied) holding its midpoint, so a line is split where the level changes;
- bottom = legger bottom (mean of upstream and downstream) where known, else level - depth;
- pieces with the same source, level area, width and bottom form one watercourse.

Everything here works in model metres (origin at the lower-left corner of the model).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from gwproducten.geo import water
from gwproducten.geo.waterschap import LEVEL_LABEL, LevelArea, Reach

STEP = 10.0  # m, longest piece of a line
NEAR = 10.0  # m, TOP10NL pieces closer to a legger line are dropped
MAX_GROUPS = 40
MIN_DEPTH = 0.10  # m; a bottom closer to (or above) the level is set to level - MIN_DEPTH
MAX_CELL_LENGTH = 5.0  # x cell: the model's upper limit of watercourse length per cell
DEPTHS = {  # default water depth [m] where the legger has no bottom
    "legger 1": 1.0, "legger 2": 0.6, "0,5 - 3 meter": 0.4, "3 - 6 meter": 0.8, "breed": 1.5,
}  # fmt: skip

Points = list[tuple[float, float]]


@dataclass
class Group:
    source: str  # e.g. "Legger primair", "TOP10NL 0,5 - 3 m"
    area: str | None  # level area code, None outside every area
    stage: float  # m NAP, NaN if unknown
    width: float
    bottom: float  # m NAP, NaN if unknown
    bottom_from_legger: bool
    bottom_adjusted: bool = False  # legger bottom at/above the level: set to level - MIN_DEPTH
    lines: list[Points] = field(default_factory=list)
    cells: dict[tuple[int, int], float] = field(default_factory=dict)  # area water: length

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
) -> Result:
    """Model watercourses (see module docstring). ``grid`` = (cell, nrow, ncol)."""
    depths = {**DEPTHS, **(depths or {})}
    cell, nrow, ncol = grid
    notes: set[str] = set()
    raw: list[tuple[str, int, float, float, bool, Points | None, dict | None]] = []

    legger_pts: list[np.ndarray] = []
    for r in reaches:
        label = "Legger primair" if r.category == 1 else "Legger secundair"
        key = f"legger {1 if r.category == 1 else 2}"
        for line in r.lines:
            dense = densify(water.to_local(line, *origin), STEP / 2)
            legger_pts.append(np.asarray(dense))
            for i, piece in split_by_area(dense, areas, origin):
                stage = _stage(areas, i, choice, default_stage, notes)
                known = math.isfinite(r.bottom)
                bottom = r.bottom if known else stage - depths[key]
                width = r.width if math.isfinite(r.width) and r.width > 0 else 1.0
                raw.append((label, i, stage, width, bottom, known, piece, None))  # type: ignore[arg-type]
    tree = cKDTree(np.vstack(legger_pts)) if legger_pts else None

    capped = False
    for w in top10:
        width = water.class_width(w.width_class)
        if w.area and w.width_class == "onbekend":
            width = water.WIDE_WIDTH  # an area without width class is wide water
        key = w.width_class if w.width_class in depths else "breed"
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
                raw.append((label, i, stage, width, stage - depths["breed"], False, None, part))  # type: ignore[arg-type]
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
            raw.append((label, i, stage, width, stage - depths[key], False, piece, None))  # type: ignore[arg-type]

    groups, coarse = _group(raw, areas)
    if coarse:
        notes.add(
            f"Veel verschillende profielen: bodem afgerond op {coarse[0]:g} m en breedte op "
            f"{coarse[1]:g} m om het aantal watergangen te beperken (max. {MAX_GROUPS})."
        )
    dry = set()
    for g in groups:
        if math.isfinite(g.stage) and g.bottom > g.stage - MIN_DEPTH:
            if g.bottom_from_legger:
                dry.add(g.area or "-")
                g.bottom_adjusted = True
            g.bottom = round(g.stage - MIN_DEPTH, 2)
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


def _group(raw: list, areas: list[LevelArea]) -> tuple[list[Group], tuple[float, float] | None]:
    """Groups of equal source, area, width and bottom; rounding gets coarser until there are
    at most MAX_GROUPS. Returns (groups, the coarser rounding used or None)."""
    steps = ((0.1, 0.25), (0.25, 1.0), (0.5, 2.0), (1.0, 5.0))
    for bottom_step, width_step in steps:
        groups: dict[tuple, Group] = {}
        for label, i, stage, width, bottom, known, piece, cells in raw:
            w = max(round(width / width_step) * width_step, width_step)
            b = round(bottom / bottom_step) * bottom_step if math.isfinite(bottom) else math.nan
            key = (label, i, w, b if math.isfinite(b) else None, known)
            if key not in groups:
                area = areas[i].code if i >= 0 else None
                groups[key] = Group(label, area, stage, w, b, known)
            g = groups[key]
            if piece is not None:
                g.lines.append(piece)
            if cells:
                for c, length in cells.items():
                    g.cells[c] = g.cells.get(c, 0.0) + length
        if len(groups) <= MAX_GROUPS:
            break
    coarse = None if (bottom_step, width_step) == steps[0] else (bottom_step, width_step)
    return sorted(groups.values(), key=lambda g: (g.area or "~", g.source, g.bottom)), coarse
