"""Map data for a model location: parsers on saved service responses (offline), error
handling, and a few checks against the real services (``pytest -m online``)."""

import json
from pathlib import Path

import pytest
import requests

from gwproducten.geo import pdok

DATA = Path(__file__).resolve().parent / "data"


class FakeResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self.content, self.status_code = payload, status
        self.text = payload.decode(errors="replace")

    def json(self) -> object:
        return json.loads(self.content)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


def test_parse_places_from_locatieserver() -> None:
    data = json.loads((DATA / "locatieserver_domplein.json").read_text(encoding="utf-8"))
    places = pdok.parse_places(data)
    assert len(places) == 3
    assert places[0].name == "Domplein, Utrecht" and places[0].kind == "weg"
    assert places[0].x == pytest.approx(136797.557) and places[0].y == pytest.approx(455862.387)
    assert all(pdok.in_netherlands(p.x, p.y) for p in places)


def test_search_sends_user_agent_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_get(url, params, timeout, headers):  # noqa: ANN001, ANN202
        seen.update(url=url, params=params, timeout=timeout, headers=headers)
        return FakeResponse((DATA / "locatieserver_domplein.json").read_bytes())

    monkeypatch.setattr(pdok.requests, "get", fake_get)
    assert pdok.search_places("  ") == []  # nothing sent for an empty query
    assert not seen
    places = pdok.search_places(" Domplein Utrecht ")
    assert len(places) == 3
    assert seen["params"]["q"] == "Domplein Utrecht"
    assert seen["timeout"] == pdok.TIMEOUT
    assert seen["headers"]["User-Agent"].startswith("Grondwatertoolbox")


@pytest.mark.parametrize("failure", ["timeout", "http"])
def test_unreachable_service_gives_dutch_message(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    def fake_get(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        if failure == "timeout":
            raise requests.Timeout("traag")
        return FakeResponse(b"{}", status=503)

    monkeypatch.setattr(pdok.requests, "get", fake_get)
    with pytest.raises(pdok.ServiceError, match="niet bereikbaar"):
        pdok.search_places("Utrecht")


@pytest.mark.online
def test_online_locatieserver() -> None:
    places = pdok.search_places("Domplein Utrecht")
    assert places and pdok.in_netherlands(places[0].x, places[0].y)
