"""PDOK services (Publieke Dienstverlening Op de Kaart): free government geodata without a
key, used under PDOK Fair Use. Each call asks for one model area, has a timeout and names the
app in its User-Agent; nothing is stored. Coordinates are RD New (EPSG:28992) in metres."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import requests

USER_AGENT = "Grondwatertoolbox/0.1 (+https://github.com/AItoolinghydrologie12/AIgrondwatertoolbox)"
TIMEOUT = 15.0  # s
LOCATIESERVER = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
RD_BOUNDS = (0.0, 300000.0, 300000.0, 625000.0)  # x_min, x_max, y_min, y_max of the Netherlands


class ServiceError(RuntimeError):
    """A web service could not be used; the message is Dutch and meant for the user."""


@dataclass(frozen=True)
class Place:
    name: str
    kind: str
    x: float  # RD [m]
    y: float  # RD [m]


def get(url: str, params: dict[str, Any], service: str) -> requests.Response:
    """GET with timeout and User-Agent; any failure becomes a :class:`ServiceError`."""
    try:
        response = requests.get(
            url, params=params, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
        response.raise_for_status()
    except requests.RequestException as err:
        raise ServiceError(
            f"{service} is nu niet bereikbaar ({type(err).__name__}). Probeer het later "
            "opnieuw; de app werkt ook zonder deze gegevens."
        ) from err
    return response


_POINT = re.compile(r"POINT\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)")


def parse_places(data: dict[str, Any]) -> list[Place]:
    """Places from a Locatieserver 'free' response (fields weergavenaam, type, centroide_rd)."""
    places = []
    for doc in data.get("response", {}).get("docs", []):
        match = _POINT.fullmatch(str(doc.get("centroide_rd", "")).strip())
        if match is None:
            continue
        x, y = float(match[1]), float(match[2])
        places.append(Place(str(doc.get("weergavenaam", "?")), str(doc.get("type", "")), x, y))
    return places


def search_places(query: str, rows: int = 8) -> list[Place]:
    """Addresses, streets, places and postcodes matching ``query`` (PDOK Locatieserver)."""
    if not query.strip():
        return []
    params = {"q": query.strip(), "rows": rows, "fl": "weergavenaam,type,centroide_rd"}
    return parse_places(get(LOCATIESERVER, params, "De PDOK Locatieserver").json())


def in_netherlands(x: float, y: float) -> bool:
    x_min, x_max, y_min, y_max = RD_BOUNDS
    return x_min <= x <= x_max and y_min <= y <= y_max


# ---- background map: BRT-Achtergrondkaart grijs (WMTS tiles) --------------------------------
MAX_PX = 1000  # longest side of a background image [px]
JPEG_QUALITY = 80
WMTS = "https://service.pdok.nl/kadaster/brt-achtergrondkaart/wmts/v2_0/grijs"
TILE_URL_RD = WMTS + "/EPSG:28992/{z:02d}/{col}/{row}.png"  # for the model maps (RD)
TILE_URL_WEB = WMTS + "/EPSG:3857/{z}/{x}/{y}.png"  # for the location map (web mercator)
RD_TILE_ORIGIN = (-285401.92, 903401.92)  # top-left corner of the RD tile matrix set [m]
RD_RES0 = 3440.64  # resolution of level 0 [m/px]; halves per level
RD_MAX_LEVEL = 14
TILE_PX = 256
MAX_TILES = 64  # safety limit per image
TILE_ATTRIBUTION = (
    'Kaartgegevens &copy; <a href="https://www.kadaster.nl">Kadaster</a> '
    "(BRT-Achtergrondkaart grijs, CC BY 4.0) via PDOK"
)
BACKGROUND_SOURCE = {
    "grijs": "Kadaster BRT-Achtergrondkaart grijs (CC BY 4.0) via PDOK",
}

Bbox = tuple[float, float, float, float]  # x_min, y_min, x_max, y_max in RD [m]


def image_size(bbox: Bbox, max_px: int = MAX_PX) -> tuple[int, int]:
    """Width and height in pixels with the bbox aspect ratio, longest side ``max_px``."""
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    scale = max_px / max(w, h)
    return max(1, round(w * scale)), max(1, round(h * scale))


def tile_level(bbox: Bbox, max_px: int = MAX_PX) -> int:
    """RD tile level whose resolution is closest (in log scale) to ``max_px`` over the bbox."""
    import math

    target = max(bbox[2] - bbox[0], bbox[3] - bbox[1]) / max_px
    return min(max(round(math.log2(RD_RES0 / target)), 0), RD_MAX_LEVEL)


def tile_range(bbox: Bbox, level: int) -> tuple[range, range]:
    """Columns and rows of the RD tiles that cover ``bbox`` (rows count down from the top)."""
    import math

    span = RD_RES0 / 2**level * TILE_PX  # tile size [m]
    x0, y0 = RD_TILE_ORIGIN
    cols = range(math.floor((bbox[0] - x0) / span), math.ceil((bbox[2] - x0) / span))
    rows = range(math.floor((y0 - bbox[3]) / span), math.ceil((y0 - bbox[1]) / span))
    return cols, rows


def _tile(url: str) -> Any:
    import io

    from PIL import Image

    data = get(url, {}, "De PDOK-achtergrondkaart").content
    try:
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except OSError as err:  # an error document instead of an image
        raise ServiceError("De PDOK-achtergrondkaart gaf geen afbeelding terug.") from err


def background_image(kind: str, bbox: Bbox) -> bytes:
    """JPEG of the grey BRT background map exactly covering ``bbox``: the RD tiles of a
    suitable level fetched in parallel, stitched, cropped and resized."""
    import io
    from concurrent.futures import ThreadPoolExecutor

    from PIL import Image

    if kind not in BACKGROUND_SOURCE:
        raise ValueError(f"Onbekende achtergrondkaart: {kind}")
    level = tile_level(bbox)
    cols, rows = tile_range(bbox, level)
    while len(cols) * len(rows) > MAX_TILES and level > 0:
        level -= 1
        cols, rows = tile_range(bbox, level)
    urls = [TILE_URL_RD.format(z=level, col=c, row=r) for r in rows for c in cols]
    with ThreadPoolExecutor(max_workers=8) as pool:
        tiles = list(pool.map(_tile, urls))
    mosaic = Image.new("RGBA", (len(cols) * TILE_PX, len(rows) * TILE_PX), "white")
    for k, tile in enumerate(tiles):
        r, c = divmod(k, len(cols))
        mosaic.paste(tile, (c * TILE_PX, r * TILE_PX))
    res = RD_RES0 / 2**level
    left = RD_TILE_ORIGIN[0] + cols.start * TILE_PX * res
    top = RD_TILE_ORIGIN[1] - rows.start * TILE_PX * res
    box = (
        (bbox[0] - left) / res, (top - bbox[3]) / res,
        (bbox[2] - left) / res, (top - bbox[1]) / res,
    )  # fmt: skip
    size = image_size(bbox)
    image = mosaic.resize(size, Image.Resampling.LANCZOS, box=box)
    background = Image.new("RGB", size, "white")
    background.paste(image, mask=image)
    buffer = io.BytesIO()
    background.save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue()
