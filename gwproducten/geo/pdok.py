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
