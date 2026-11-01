"""Watercourses from TOP10NL: parsing saved responses, width classes, culverts, and the
conversion of lines and areas to length per cell."""

import json
import math
from pathlib import Path

import pytest

from gwproducten.core.grids import polyline_cell_lengths
from gwproducten.geo import water

DATA = Path(__file__).resolve().parent / "data"


def _load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def test_parse_lines_drops_culverts() -> None:
    data = _load("top10nl_waterdeel_lijn.json")
    raw = data["features"]
    lines = water.parse_waterdelen(data, area=False)
    culverts = [f for f in raw if f["properties"].get("fysiekvoorkomen") == "in duiker"]
    assert len(lines) == len(raw) - len(culverts)
    assert {w.width_class for w in lines} <= {"0,5 - 3 meter", "3 - 6 meter"}
    assert all(not w.area and len(w.parts[0]) >= 2 for w in lines)


def test_parse_areas() -> None:
    areas = water.parse_waterdelen(_load("top10nl_waterdeel_vlak.json"), area=True)
    assert areas and all(w.area and w.width_class == "6 - 12 meter" for w in areas)
    assert areas[0].parts[0][0] == areas[0].parts[0][-1]  # closed ring


@pytest.mark.parametrize(
    ("label", "width"),
    [("0,5 - 3 meter", 1.75), ("3 - 6 meter", 4.5), ("6 - 12 meter", 9.0),
     ("groter dan 12 meter", water.WIDE_WIDTH), ("onbekend", 1.5)],
)  # fmt: skip
def test_class_width(label: str, width: float) -> None:
    assert water.class_width(label) == pytest.approx(width)


def test_rectangle_area_gives_length_per_cell() -> None:
    """A 10 m wide, 100 m long canal along cell centres: area / width = 100 m in total,
    spread over the cells it covers."""
    ring = [(5.0, 20.0), (105.0, 20.0), (105.0, 30.0), (5.0, 30.0), (5.0, 20.0)]
    lengths = water.polygon_cell_lengths([ring], 10.0, 25.0, 8, 8, samples=10)
    assert sum(lengths.values()) == pytest.approx(100.0, rel=0.03)
    assert set(lengths) == {(0, 0), (0, 1), (0, 2), (0, 3), (0, 4), (1, 0), (1, 1), (1, 2),
                            (1, 3), (1, 4)}  # fmt: skip


def test_area_hole_and_outside_grid() -> None:
    outer = [(0.0, 0.0), (50.0, 0.0), (50.0, 50.0), (0.0, 50.0)]
    hole = [(10.0, 10.0), (40.0, 10.0), (40.0, 40.0), (10.0, 40.0)]
    lengths = water.polygon_cell_lengths([outer, hole], 1.0, 50.0, 2, 2, samples=50)
    assert sum(lengths.values()) == pytest.approx(50 * 50 - 30 * 30, rel=0.01)
    assert water.polygon_cell_lengths([[(500.0, 500.0), (600, 500), (600, 600)]], 1.0, 50.0,
                                      2, 2) == {}  # fmt: skip


def test_lines_to_local_cells_keep_length_inside_model() -> None:
    lines = water.parse_waterdelen(_load("top10nl_waterdeel_lijn.json"), area=False)
    x0, y0, size, cell = 127500.0, 445500.0, 1000.0, 25.0
    n = int(size / cell)
    for w in lines:
        local = water.to_local(w.parts[0], x0, y0)
        inside = sum(polyline_cell_lengths(local, cell, n, n).values())
        total = sum(math.dist(a, b) for a, b in zip(local[:-1], local[1:], strict=True))
        assert 0.0 <= inside <= total + 1e-6


def test_fetch_follows_next_links(monkeypatch: pytest.MonkeyPatch) -> None:
    from gwproducten.geo import pdok

    page1 = _load("top10nl_waterdeel_lijn.json")
    page1 = {**page1, "links": [{"rel": "next", "href": "https://example/next"}]}
    page2 = {**_load("top10nl_waterdeel_lijn.json"), "links": []}
    calls = []

    class Resp:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def json(self) -> dict:
            return self.payload

    def fake_get(url, params, service):  # noqa: ANN001, ANN202
        calls.append((url, dict(params)))
        if "waterdeel_vlak" in url:
            return Resp({"features": [], "links": []})
        return Resp(page2 if url == "https://example/next" else page1)

    monkeypatch.setattr(water, "get", fake_get)
    result = water.fetch_waterdelen((127500.0, 445500.0, 128500.0, 446500.0))
    n = len(water.parse_waterdelen(page1, area=False))
    assert len(result) == 2 * n
    assert calls[0][1]["crs"] == water.RD_CRS and calls[1] == ("https://example/next", {})
    assert pdok  # same ServiceError type is used


@pytest.mark.online
def test_online_top10nl_polder() -> None:
    result = water.fetch_waterdelen((127500.0, 445500.0, 128500.0, 446500.0))
    assert sum(not w.area for w in result) > 50 and any(w.area for w in result)
