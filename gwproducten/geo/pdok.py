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


# ---- background maps -----------------------------------------------------------------------
MAX_PX = 1000  # longest side of a background image [px]
JPEG_QUALITY = 75
WMS = {
    "luchtfoto": ("https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0", "Actueel_ortho25"),
    "top10nl": ("https://service.pdok.nl/brt/top10nl/wms/v1_0", "top10nl"),
    "ahn": ("https://service.pdok.nl/rws/ahn/wms/v1_0", "dtm_05m"),
}
BACKGROUND_SOURCE = {
    "luchtfoto": "Luchtfoto: Beeldmateriaal Nederland (CC BY 4.0) via PDOK",
    "top10nl": "Topografie: Kadaster BRT TOP10NL (CC BY 4.0) via PDOK",
    "ahn": "Hoogtekaart: AHN (CC0) via PDOK",
}

Bbox = tuple[float, float, float, float]  # x_min, y_min, x_max, y_max in RD [m]


def image_size(bbox: Bbox, max_px: int = MAX_PX) -> tuple[int, int]:
    """Width and height in pixels with the bbox aspect ratio, longest side ``max_px``."""
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    scale = max_px / max(w, h)
    return max(1, round(w * scale)), max(1, round(h * scale))


def wms_params(layer: str, bbox: Bbox, size: tuple[int, int], fmt: str) -> dict[str, Any]:
    """GetMap 1.3.0 in RD; for EPSG:28992 the axis order is x (east), y (north)."""
    return {
        "service": "WMS", "version": "1.3.0", "request": "GetMap", "layers": layer,
        "styles": "", "crs": "EPSG:28992", "bbox": ",".join(f"{v:.2f}" for v in bbox),
        "width": size[0], "height": size[1], "format": fmt,
    }  # fmt: skip


def _to_jpeg(image: Any) -> bytes:
    import io

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


def background_image(kind: str, bbox: Bbox) -> bytes:
    """JPEG of the map ``kind`` (top10nl, luchtfoto, ahn) exactly covering ``bbox``."""
    import io

    from PIL import Image

    url, layer = WMS[kind]
    fmt = "image/jpeg" if kind == "luchtfoto" else "image/png"
    data = get(url, wms_params(layer, bbox, image_size(bbox), fmt), "De PDOK-kaart").content
    try:
        image = Image.open(io.BytesIO(data))
    except OSError as err:  # a WMS error document instead of an image
        raise ServiceError("De PDOK-kaart gaf geen afbeelding terug.") from err
    if kind == "luchtfoto":
        return _to_jpeg(image)  # the service's JPEG is about 3 times larger
    background = Image.new("RGB", image.size, "white")
    background.paste(image.convert("RGBA"), mask=image.convert("RGBA"))
    return _to_jpeg(background)
