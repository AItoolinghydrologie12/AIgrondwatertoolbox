"""REGIS II subsoil: parsing a saved OPeNDAP reply, and the rules of the layer proposal
(conservation of kD and c, thin clay, at most four aquifers, missing Holocene values)."""

import math
from pathlib import Path

import pytest

from gwproducten.geo import regis
from gwproducten.geo.pdok import ServiceError

DATA = Path(__file__).resolve().parent / "data"


def _lopik() -> tuple[float, list[regis.Unit]]:
    return regis.parse_ascii((DATA / "regis_lopik.ascii").read_text(encoding="utf-8"))


def _unit(code: str, top: float, bottom: float, kh=math.nan, kv=math.nan, c=math.nan):  # noqa: ANN001, ANN202
    return regis.Unit(code, top, bottom, kh, kv, c)


def test_parse_saved_reply() -> None:
    surface, units = _lopik()
    assert surface == pytest.approx(-0.84)
    assert units[0].code == "NUhlc" and not units[0].is_aquifer
    assert all(a.bottom == pytest.approx(b.top) for a, b in zip(units[:-1], units[1:], strict=True))
    assert sum(u.is_aquifer for u in units) > 10


def test_grid_index_and_outside() -> None:
    assert regis.grid_index(128050.0, 446099.0) == (1280, 1460)
    with pytest.raises(ServiceError, match="buiten REGIS"):
        regis.grid_index(-10.0, 446000.0)


def test_proposal_conserves_kd_and_c() -> None:
    surface, units = _lopik()
    p = regis.propose(surface, units, c_min=5.0, max_aquifers=4)
    assert p.top_mode == "deklaag" and p.n_aquifers == 4
    assert [g.aquifer for g in p.layers] == [False, True] * 4
    kept = [u for g in p.layers for u in g.units]
    assert kept == units[: len(kept)]  # contiguous from the top
    for g in p.layers:
        if g.aquifer:
            kd = sum(u.kh * u.thickness for u in g.units if math.isfinite(u.kh))
            assert g.kh * g.thickness == pytest.approx(kd)
            assert 0.001 <= g.anisotropy <= 1.0
        elif g.units[0].code != "NUhlc":
            assert g.c == pytest.approx(sum(u.resistance() for u in g.units))
    assert math.isnan(p.layers[0].c)  # Holocene: no value in REGIS
    assert any("hlc" in n for n in p.notes) and any("dichte basis" in n for n in p.notes)


def test_thin_clay_joins_aquifer_and_max_aquifers() -> None:
    units = [
        _unit("Az1", 0.0, -10.0, kh=10.0),
        _unit("Bk1", -10.0, -11.0, kv=0.5, c=2.0),  # thin: c < 5 d
        _unit("Cz1", -11.0, -20.0, kh=20.0),
        _unit("Dk1", -20.0, -25.0, c=300.0),
        _unit("Ez1", -25.0, -40.0, kh=30.0),
        _unit("Fk1", -40.0, -45.0, c=800.0),
    ]
    p = regis.propose(0.0, units, c_min=5.0, max_aquifers=4)
    assert p.top_mode == "freatisch_zand"
    first, clay, second = p.layers
    assert first.thickness == 20.0 and first.kh * first.thickness == pytest.approx(280.0)
    # vertical: 10/(0.33*10) + 2 + 9/(0.33*20) -> harmonic kv/kh of the whole aquifer
    vertical = 10 / (0.33 * 10) + 2.0 + 9 / (0.33 * 20)
    assert first.anisotropy == pytest.approx((20 / vertical) / first.kh)
    assert clay.c == 300.0 and second.thickness == 15.0
    assert any("Fk1" in n for n in p.notes)  # aquitard below the last aquifer
    assert any("Bk1" in n for n in p.notes)  # thin clay
    one = regis.propose(0.0, units, max_aquifers=1)
    assert [g.aquifer for g in one.layers] == [True]


@pytest.mark.online
def test_online_regis_point() -> None:
    surface, units = regis.fetch_point(128000.0, 446000.0)
    assert -3.0 < surface < 1.0 and len(units) > 10
