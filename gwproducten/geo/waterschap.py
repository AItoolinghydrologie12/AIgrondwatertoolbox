"""Water levels and watercourse profiles of a water board (waterschap), from its public
ArcGIS services: target levels per level area (peilgebied) and the primary/secondary
watercourses of the legger with bottom width and bottom level.

Each water board publishes other services and field names; :data:`WATERSCHAPPEN` holds them,
so adding a board is one entry. For now: Waterschap Drents Overijsselse Delta (WDODelta).

The bottom level of a legger reach is given upstream and downstream; the app uses the mean.
Lines come from the open layers *Primaire/Secundaire watergang*; where the adopted legger
(13-12-2022, attributes only) has values for the same watercourse code, those are used.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from gwproducten.geo.pdok import Bbox, ServiceError, get

LEVELS = ("zomer", "winter", "min", "max")
LEVEL_LABEL = {"zomer": "zomerpeil", "winter": "winterpeil", "min": "minpeil", "max": "maxpeil"}
FALLBACK = {"min": "winter", "winter": "min", "max": "zomer", "zomer": "max"}
PAGE = 1000

Ring = tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Board:
    name: str
    source: str
    level_url: str  # FeatureServer layer with level areas
    level_fields: dict[str, str]  # "code" and the keys of LEVELS -> field name
    line_urls: tuple[tuple[str, str], ...]  # (layer URL, field prefix) with geometry
    attribute_urls: tuple[tuple[str, str], ...]  # (layer URL, field prefix), attributes only
    line_fields: dict[str, str]  # ident, category, width, bottom_up, bottom_down


_AGOL = "https://services6.arcgis.com/BZiPrSbS4NknjGsQ/arcgis/rest/services"
_LEGGER = (
    "https://gis.wdodelta.nl/arcgis/rest/services/LeggerWatersysteem/"
    "Vastgestelde_Legger_13_12_2022/MapServer"
)
WDODELTA = Board(
    name="Waterschap Drents Overijsselse Delta",
    source=(
        "Peilgebieden en watergangen (legger, vastgesteld 13-12-2022; lijnen 2018): "
        "Waterschap Drents Overijsselse Delta"
    ),
    level_url=f"{_AGOL}/Peilgebieden_opendata/FeatureServer/0",
    level_fields={
        "code": "GPGIDENT",
        "zomer": "GPGZMRPL",
        "winter": "GPGWNTPL",
        "min": "IWS_GPGONDP",
        "max": "IWS_GPGBOVP",
    },  # fmt: skip
    line_urls=(
        (f"{_AGOL}/Primaire_watergang_20_3_2018/FeatureServer/0", ""),
        (f"{_AGOL}/Secundaire_watergang20_3_2018/FeatureServer/0", ""),
    ),
    attribute_urls=((f"{_LEGGER}/31", "GW_OVK."), (f"{_LEGGER}/11", "GW_OVK.")),
    line_fields={
        "ident": "OVKIDENT",
        "category": "IWS_CATEGORIEEN",
        "width": "AVVBODDR",
        "bottom_up": "IWS_AVVHOBOS_L",
        "bottom_down": "IWS_AVVHOBES_L",
    },  # fmt: skip
)
WATERSCHAPPEN = {"wdodelta": WDODELTA}


@dataclass(frozen=True)
class LevelArea:
    code: str
    rings: tuple[Ring, ...]  # all rings (outer and holes, all parts): even-odd inside test
    levels: dict[str, float] = field(default_factory=dict)  # LEVELS -> m NAP (NaN if unknown)

    def level(self, choice: str) -> tuple[float, str | None]:
        """(level, used choice): the chosen level, else its counterpart (min <-> winter,
        max <-> zomer), else (NaN, None)."""
        for key in (choice, FALLBACK[choice]):
            value = self.levels.get(key, math.nan)
            if math.isfinite(value):
                return value, key
        return math.nan, None


@dataclass(frozen=True)
class Reach:
    """A legger watercourse (reach) with its profile."""

    ident: str
    category: int  # 1 primary, 2 secondary
    width: float  # bottom width [m], NaN if unknown
    bottom_up: float  # m NAP, NaN if unknown
    bottom_down: float
    lines: tuple[Ring, ...]

    @property
    def bottom(self) -> float:
        """Mean of the upstream and downstream bottom level; one of them if the other is
        missing; NaN without either."""
        known = [v for v in (self.bottom_up, self.bottom_down) if math.isfinite(v)]
        return sum(known) / len(known) if known else math.nan


def _number(value: Any) -> float:
    try:
        number = float(value)
    except TypeError, ValueError:
        return math.nan
    return number if math.isfinite(number) and number > -999.0 else math.nan


def query(url: str, bbox: Bbox, fields: list[str], geometry: bool, service: str) -> list[dict]:
    """All features of an ArcGIS layer intersecting ``bbox`` (RD), with paging."""
    features: list[dict] = []
    params: dict[str, Any] = {
        "geometry": ",".join(f"{v:.2f}" for v in bbox), "geometryType": "esriGeometryEnvelope",
        "inSR": 28992, "outSR": 28992, "spatialRel": "esriSpatialRelIntersects",
        "outFields": ",".join(fields), "returnGeometry": str(geometry).lower(),
        "geometryPrecision": 2, "resultRecordCount": PAGE, "f": "json",
    }  # fmt: skip
    for page in range(20):
        data = get(f"{url}/query", {**params, "resultOffset": page * PAGE}, service).json()
        if "error" in data:
            raise ServiceError(f"{service} gaf een fout: {data['error'].get('message', '?')}.")
        features += data.get("features", [])
        if not data.get("exceededTransferLimit"):
            return features
    raise ServiceError(f"{service}: te veel objecten in dit gebied; maak het model kleiner.")


def _parts(geometry: dict | None, key: str) -> tuple[Ring, ...]:
    return tuple(
        tuple((float(x), float(y)) for x, y, *_ in part) for part in (geometry or {}).get(key, [])
    )


def parse_level_areas(features: list[dict], board: Board = WDODELTA) -> list[LevelArea]:
    f = board.level_fields
    areas = []
    for feature in features:
        attrs = feature.get("attributes", {})
        rings = _parts(feature.get("geometry"), "rings")
        if rings:
            levels = {k: _number(attrs.get(f[k])) for k in LEVELS}
            areas.append(LevelArea(str(attrs.get(f["code"]) or "?"), rings, levels))
    return areas


def level_areas(bbox: Bbox, board: Board = WDODELTA) -> list[LevelArea]:
    """Level areas (peilgebieden) in ``bbox``. Raises ServiceError."""
    fields = list(board.level_fields.values())
    return parse_level_areas(
        query(board.level_url, bbox, fields, True, f"De peilgebiedenkaart van {board.name}"),
        board,
    )


def parse_reaches(
    line_features: list[dict], attribute_features: list[dict], board: Board = WDODELTA
) -> list[Reach]:
    """Reaches from line features; attributes of the adopted legger (same code) win where
    they are filled. Features are dicts with "attributes" (field names without prefix)."""
    f = board.line_fields
    legger: dict[str, dict[str, float]] = {}
    for feature in attribute_features:
        attrs = feature["attributes"]
        ident = str(attrs.get(f["ident"]) or "")
        values = {k: _number(attrs.get(f[k])) for k in ("width", "bottom_up", "bottom_down")}
        if ident and any(math.isfinite(v) for v in values.values()):
            legger.setdefault(ident, values)
    reaches: dict[str, Reach] = {}
    for feature in line_features:
        attrs = feature.get("attributes", {})
        lines = _parts(feature.get("geometry"), "paths")
        ident = str(attrs.get(f["ident"]) or f"zonder code {len(reaches) + 1}")
        if not lines:
            continue
        values = {k: _number(attrs.get(f[k])) for k in ("width", "bottom_up", "bottom_down")}
        for k, v in legger.get(ident, {}).items():
            if math.isfinite(v):
                values[k] = v
        cat = _number(attrs.get(f["category"]))
        category = int(cat) if math.isfinite(cat) else 2
        if ident in reaches:  # one code in several features: join the lines
            old = reaches[ident]
            reaches[ident] = Reach(ident, old.category, old.width, old.bottom_up,
                                   old.bottom_down, old.lines + lines)  # fmt: skip
        else:
            reaches[ident] = Reach(ident, category, values["width"], values["bottom_up"],
                                   values["bottom_down"], lines)  # fmt: skip
    return list(reaches.values())


def _strip(features: list[dict], prefix: str) -> list[dict]:
    if not prefix:
        return features
    return [
        {**feat, "attributes": {k.removeprefix(prefix): v for k, v in feat["attributes"].items()}}
        for feat in features
    ]


def reaches(bbox: Bbox, board: Board = WDODELTA) -> list[Reach]:
    """Primary and secondary watercourses in ``bbox`` with their profile. Raises
    ServiceError."""
    fields = list(board.line_fields.values())
    service = f"De watergangen van {board.name}"
    lines = [
        feat
        for url, prefix in board.line_urls
        for feat in _strip(query(url, bbox, [prefix + n for n in fields], True, service), prefix)
    ]
    attributes = [
        feat
        for url, prefix in board.attribute_urls
        for feat in _strip(
            query(url, bbox, [prefix + n for n in fields], False, f"De legger van {board.name}"),
            prefix,
        )
    ]
    return parse_reaches(lines, attributes, board)
