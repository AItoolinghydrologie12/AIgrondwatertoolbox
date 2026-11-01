"""Watercourses from BRT TOP10NL (Kadaster, CC BY 4.0) via the PDOK OGC API: centre lines of
narrow water (width class 0.5-3 m and 3-6 m) and areas of wider water. Culverts and other
water below ground level are left out. Only geometry and width class are used; water levels
and bottoms are not in TOP10NL."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import numpy as np

from gwproducten.geo.pdok import Bbox, ServiceError, get

TOP10NL_ITEMS = "https://api.pdok.nl/brt/top10nl/ogc/v1/collections/{collection}/items"
RD_CRS = "http://www.opengis.net/def/crs/EPSG/0/28992"
PAGE_LIMIT = 1000
MAX_PAGES = 10
WIDE_WIDTH = 20.0  # m, for the open class "groter dan 12 meter"
SOURCE = "Watergangen: Kadaster BRT TOP10NL (CC BY 4.0) via PDOK"

Ring = list[tuple[float, float]]


@dataclass(frozen=True)
class Water:
    width_class: str  # as in TOP10NL, e.g. "0,5 - 3 meter"
    area: bool  # False: centre line, True: polygon
    parts: tuple[tuple[tuple[float, float], ...], ...]  # lines, or rings (exterior first)


def class_width(label: str) -> float:
    """Representative width [m] of a TOP10NL width class: the middle of the class."""
    numbers = [float(v.replace(",", ".")) for v in re.findall(r"\d+(?:,\d+)?", label)]
    if len(numbers) >= 2:
        return 0.5 * (numbers[0] + numbers[1])
    if numbers and "groter" in label:
        return max(WIDE_WIDTH, numbers[0])
    return numbers[0] if numbers else 1.5


def _below_ground(props: dict[str, Any]) -> bool:
    """Culverts, tunnels and other water below the surface do not exchange with the
    groundwater like an open watercourse."""
    level = props.get("hoogteniveau")
    if isinstance(level, int | float) and level < 0:
        return True
    return props.get("fysiekvoorkomen") not in (None, "", "op vast deel van brug")


def parse_waterdelen(data: dict[str, Any], area: bool) -> list[Water]:
    """Water features from one OGC API response (GeoJSON in RD)."""
    out = []
    for feature in data.get("features", []):
        props = feature.get("properties", {}) or {}
        geom = feature.get("geometry") or {}
        if _below_ground(props):
            continue
        kind, coords = geom.get("type"), geom.get("coordinates", [])
        if kind == "LineString":
            groups = [[coords]]
        elif kind == "MultiLineString":
            groups = [[line] for line in coords]
        elif kind == "Polygon":
            groups = [coords]
        elif kind == "MultiPolygon":
            groups = list(coords)
        else:
            continue
        label = str(props.get("breedteklasse") or "onbekend")
        for parts in groups:
            rings = tuple(tuple((float(p[0]), float(p[1])) for p in part) for part in parts)
            out.append(Water(label, area, rings))
    return out


def _next_url(data: dict[str, Any]) -> str | None:
    return next((link["href"] for link in data.get("links", []) if link.get("rel") == "next"), None)


def fetch_waterdelen(bbox: Bbox) -> list[Water]:
    """All lines and areas of water in ``bbox`` (RD). Raises ServiceError."""
    out: list[Water] = []
    for collection, area in (("waterdeel_lijn", False), ("waterdeel_vlak", True)):
        url: str | None = TOP10NL_ITEMS.format(collection=collection)
        params: dict[str, Any] = {
            "bbox": ",".join(f"{v:.2f}" for v in bbox), "bbox-crs": RD_CRS, "crs": RD_CRS,
            "f": "json", "limit": PAGE_LIMIT,
        }  # fmt: skip
        for _ in range(MAX_PAGES):
            if url is None:
                break
            data = get(url, params, "De TOP10NL-dienst van PDOK").json()
            out += parse_waterdelen(data, area)
            url, params = _next_url(data), {}  # the next link carries all parameters
        else:
            raise ServiceError("Te veel waterdelen in dit gebied; maak het model kleiner.")
    return out


def to_local(points: tuple[tuple[float, float], ...], x0: float, y0: float) -> Ring:
    """RD coordinates to model metres (origin at the lower-left corner)."""
    return [(x - x0, y - y0) for x, y in points]


def _inside(x: np.ndarray, y: np.ndarray, ring: Ring) -> np.ndarray:
    """Even-odd point-in-polygon test, vectorised over the points."""
    px = np.array([p[0] for p in ring])
    py = np.array([p[1] for p in ring])
    qx, qy = np.roll(px, -1), np.roll(py, -1)
    inside = np.zeros(x.shape, dtype=bool)
    for ax, ay, bx, by in zip(px, py, qx, qy, strict=True):
        if ay == by:
            continue
        crosses = (ay > y) != (by > y)
        x_cross = ax + (y - ay) * (bx - ax) / (by - ay)
        inside ^= crosses & (x < x_cross)
    return inside


def polygon_cell_lengths(
    rings: list[Ring], width: float, cell: float, nrow: int, ncol: int, samples: int = 5
) -> dict[tuple[int, int], float]:
    """Equivalent watercourse length per cell of a water area: area in the cell / width.
    The area per cell is sampled on ``samples`` x ``samples`` points (exterior ring minus
    holes); cells outside the grid are dropped."""
    xs = np.concatenate([[p[0] for p in r] for r in rings[:1]])
    ys = np.concatenate([[p[1] for p in r] for r in rings[:1]])
    c0, c1 = max(int(xs.min() // cell), 0), min(int(xs.max() // cell), ncol - 1)
    r0, r1 = max(int(ys.min() // cell), 0), min(int(ys.max() // cell), nrow - 1)
    if c0 > c1 or r0 > r1:
        return {}
    offsets = (np.arange(samples) + 0.5) / samples * cell
    cols, rows = np.arange(c0, c1 + 1), np.arange(r0, r1 + 1)
    gx = (cols[:, None] * cell + offsets[None, :]).ravel()
    gy = (rows[:, None] * cell + offsets[None, :]).ravel()
    x, y = np.meshgrid(gx, gy)
    hit = _inside(x, y, rings[0])
    for hole in rings[1:]:
        hit &= ~_inside(x, y, hole)
    per_cell = hit.reshape(len(rows), samples, len(cols), samples).sum(axis=(1, 3))
    area = per_cell * (cell / samples) ** 2
    return {
        (int(rows[i]), int(cols[j])): float(area[i, j] / width)
        for i, j in zip(*np.nonzero(per_cell), strict=True)
    }
