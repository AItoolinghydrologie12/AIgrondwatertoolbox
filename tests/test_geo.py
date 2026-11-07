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


# ---- background maps -------------------------------------------------------------------------
def _png(colour: tuple[int, int, int], size: tuple[int, int] = (256, 256)) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


def test_background_tiles_cover_bbox_in_rd() -> None:
    """The RD tile level gives about MAX_PX over the bbox; the tiles cover it exactly."""
    bbox = (136300.0, 455400.0, 138300.0, 456400.0)  # 2 km x 1 km
    assert pdok.image_size(bbox) == (pdok.MAX_PX, pdok.MAX_PX // 2)
    level = pdok.tile_level(bbox)
    res = pdok.RD_RES0 / 2**level
    assert 0.7 < 2000.0 / res / pdok.MAX_PX < 1.42
    cols, rows = pdok.tile_range(bbox, level)
    span = res * pdok.TILE_PX
    x0, y0 = pdok.RD_TILE_ORIGIN
    assert x0 + cols.start * span <= bbox[0] < x0 + (cols.start + 1) * span
    assert x0 + cols.stop * span >= bbox[2] > x0 + (cols.stop - 1) * span
    assert y0 - rows.start * span >= bbox[3] > y0 - (rows.start + 1) * span
    assert y0 - rows.stop * span <= bbox[1] < y0 - (rows.stop - 1) * span


def test_background_stitches_tiles_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tiles stitched row by row from the top; the result is a JPEG of the bbox aspect."""
    import io

    from PIL import Image

    seen = []

    def fake_get(url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        seen.append(url)
        return FakeResponse(_png((200, 200, 200)))

    monkeypatch.setattr(pdok.requests, "get", fake_get)
    bbox = (136300.0, 455400.0, 138300.0, 456400.0)
    data = pdok.background_image("grijs", bbox)
    image = Image.open(io.BytesIO(data))
    assert image.format == "JPEG" and image.size == pdok.image_size(bbox)
    cols, rows = pdok.tile_range(bbox, pdok.tile_level(bbox))
    assert len(seen) == len(cols) * len(rows)
    assert all("/grijs/EPSG:28992/" in u for u in seen)


def test_background_error_document_gives_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        return FakeResponse(b"<ExceptionReport>fout</ExceptionReport>")

    monkeypatch.setattr(pdok.requests, "get", fake_get)
    with pytest.raises(pdok.ServiceError, match="geen afbeelding"):
        pdok.background_image("grijs", (136300.0, 455400.0, 137300.0, 456400.0))


@pytest.mark.online
def test_online_background() -> None:
    data = pdok.background_image("grijs", (136300.0, 455400.0, 137300.0, 456400.0))
    assert data[:2] == b"\xff\xd8" and 20_000 < len(data) < 600_000  # a real JPEG map


# ---- RD <-> WGS84 ---------------------------------------------------------------------------
def test_rd_wgs84_amersfoort_and_round_trip() -> None:
    from gwproducten.geo import rd

    lat, lon = rd.rd_to_wgs84(155000.0, 463000.0)
    assert (lat, lon) == pytest.approx((52.15517440, 5.38720621), abs=1e-9)
    for x, y in [(13000.0, 380000.0), (250000.0, 600000.0), (136700.0, 455900.0)]:
        assert rd.wgs84_to_rd(*rd.rd_to_wgs84(x, y)) == pytest.approx((x, y), abs=0.05)


@pytest.mark.parametrize(
    ("x", "y", "lat", "lon"),
    [
        (136785.854, 455850.86, 52.09061667, 5.12143155),  # Domplein 21, Utrecht
        (233553.682, 582376.83, 53.22222947, 6.56334263),  # Groningen
    ],
)
def test_rd_wgs84_against_pdok(x: float, y: float, lat: float, lon: float) -> None:
    """Points with both coordinates from the PDOK Locatieserver: within about 1 m."""
    from gwproducten.geo import rd

    assert rd.rd_to_wgs84(x, y) == pytest.approx((lat, lon), abs=1.5e-5)  # ~1 m
    assert rd.wgs84_to_rd(lat, lon) == pytest.approx((x, y), abs=1.5)


# ---- AHN surface level ----------------------------------------------------------------------
def test_ahn_cell_medians_orientation_and_nodata() -> None:
    """Raster rows run north to south, app rows south to north; no-data cells become NaN."""
    import numpy as np

    from gwproducten.geo import ahn

    n = ahn.SAMPLES
    raster = np.zeros((2 * n, 3 * n))
    raster[:n, :] = 5.0  # northern row of cells
    raster[n:, :] = 1.0
    raster[n:, 2 * n :] = np.nan  # south-east cell without data
    raster[0, 0] = 100.0  # one outlier (a building) does not move the median
    med = ahn.cell_medians(raster, 2, 3)
    assert med[1].tolist() == [5.0, 5.0, 5.0]  # app row 1 = north
    assert med[0, :2].tolist() == [1.0, 1.0] and np.isnan(med[0, 2])
    s = ahn.stats(med)
    assert s is not None and s.coverage == pytest.approx(5 / 6) and s.median == 5.0
    assert ahn.stats(np.full((2, 2), np.nan)) is None


def test_ahn_saved_response() -> None:
    from gwproducten.geo import ahn

    bbox = (127500.0, 445500.0, 128500.0, 446500.0)
    raster = ahn.read_tiff((DATA / "ahn_lopik_4x4.tif").read_bytes(), bbox)
    assert raster.shape == (20, 20)
    s = ahn.stats(ahn.cell_medians(raster, 4, 4))
    assert s is not None and -1.3 < s.median < -0.8  # Lopikerwaard polder
    with pytest.raises(pdok.ServiceError, match="niet op het modelgebied"):
        ahn.read_tiff((DATA / "ahn_lopik_4x4.tif").read_bytes(), (0.0, 0.0, 1000.0, 1000.0))
    with pytest.raises(pdok.ServiceError, match="geen hoogtekaart"):
        ahn.read_tiff(b"<ExceptionReport/>", bbox)


@pytest.mark.online
def test_online_ahn() -> None:
    from gwproducten.geo import ahn

    values = ahn.surface_per_cell((127500.0, 445500.0, 128500.0, 446500.0), 40, 40)
    s = ahn.stats(values)
    assert s is not None and s.coverage > 0.9 and -2.0 < s.median < 0.0
