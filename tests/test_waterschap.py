"""Levels and legger of a water board (WDODelta): parsing saved replies, level choice with
fallback, bottom = mean of upstream and downstream, splitting lines over level areas,
dropping TOP10NL water that is already in the legger, and grouping."""

import json
import math
from pathlib import Path

import pytest

from gwproducten.geo import import_water as iw
from gwproducten.geo import water
from gwproducten.geo import waterschap as ws

DATA = Path(__file__).resolve().parent / "data"
BBOX = (185500.0, 511500.0, 186500.0, 512500.0)  # Kampereiland, 1 km


def _saved() -> dict:
    return json.loads((DATA / "wdod_kampereiland.json").read_text(encoding="utf-8"))


def _square(code: str, x0: float, x1: float, levels: dict) -> ws.LevelArea:
    ring = ((x0, 0.0), (x1, 0.0), (x1, 1000.0), (x0, 1000.0), (x0, 0.0))
    return ws.LevelArea(code, (ring,), levels)


def _reach(ident: str, cat: int, line, up=math.nan, down=math.nan, width=1.0) -> ws.Reach:  # noqa: ANN001
    return ws.Reach(ident, cat, width, up, down, (tuple(line),))


def test_parse_saved_level_areas_and_reaches() -> None:
    raw = _saved()
    areas = ws.parse_level_areas(raw["levels"])
    assert len(areas) == 8 and all(a.rings for a in areas)
    a = next(a for a in areas if a.code == "125")
    assert a.levels == {"zomer": -1.0, "winter": -1.35, "min": -1.35, "max": -1.0}
    reaches = ws.parse_reaches(raw["lines"], raw["attributes"])
    assert {r.category for r in reaches} == {1, 2}
    assert sum(math.isfinite(r.bottom) for r in reaches) >= len(reaches) - 2
    assert len({r.ident for r in reaches}) == len(reaches)


def test_level_choice_and_fallback() -> None:
    area = ws.LevelArea("1", (), {"zomer": -0.4, "winter": -0.6, "min": math.nan, "max": -0.3})
    assert area.level("zomer") == (-0.4, "zomer")
    assert area.level("min") == (-0.6, "winter")  # min missing: winter
    empty = ws.LevelArea("2", (), dict.fromkeys(ws.LEVELS, math.nan))
    assert empty.level("max")[1] is None


def test_bottom_is_mean_of_upstream_and_downstream() -> None:
    assert _reach("a", 1, [(0, 0), (1, 0)], 0.10, 0.20).bottom == pytest.approx(0.15)
    assert _reach("b", 1, [(0, 0), (1, 0)], math.nan, -1.2).bottom == -1.2
    assert math.isnan(_reach("c", 1, [(0, 0), (1, 0)]).bottom)


def test_adopted_legger_values_win_by_code() -> None:
    line = {"attributes": {"OVKIDENT": "X1", "IWS_CATEGORIEEN": 2, "AVVBODDR": 0.5,
                           "IWS_AVVHOBOS_L": None, "IWS_AVVHOBES_L": None},
            "geometry": {"paths": [[[0, 0], [100, 0]]]}}  # fmt: skip
    legger = {"attributes": {"OVKIDENT": "X1", "AVVBODDR": 0.8, "IWS_AVVHOBOS_L": -1.1,
                             "IWS_AVVHOBES_L": -1.3}}  # fmt: skip
    (reach,) = ws.parse_reaches([line], [legger])
    assert reach.width == 0.8 and reach.bottom == pytest.approx(-1.2) and reach.category == 2


def test_line_is_split_where_the_level_changes() -> None:
    """A 1 km line over two level areas: lengths kept, each piece its own level; bottom
    from the legger (mean) in both pieces."""
    areas = [_square("W", 0.0, 400.0, {"winter": -1.0}), _square("O", 400.0, 1000.0,
                                                                  {"winter": -0.5})]  # fmt: skip
    reach = _reach("R", 1, [(0.0, 500.0), (1000.0, 500.0)], -1.5, -1.7)
    res = iw.combine([], [reach], areas, (0.0, 0.0), (25.0, 40, 40), choice="winter")
    by_area = {g.area: g for g in res.groups}
    assert by_area["W"].stage == -1.0 and by_area["O"].stage == -0.5
    assert by_area["W"].length == pytest.approx(400.0, abs=6)
    assert by_area["O"].length == pytest.approx(600.0, abs=6)
    assert by_area["W"].bottom == pytest.approx(-1.6) and by_area["W"].bottom_from_legger


def test_top10nl_near_legger_dropped_and_assumed_bottom() -> None:
    areas = [_square("A", 0.0, 1000.0, {"winter": -1.0, "zomer": -0.8})]
    reach = _reach("R", 1, [(0.0, 500.0), (1000.0, 500.0)], -1.6, -1.6)
    same = water.Water("0,5 - 3 meter", False, (((0.0, 503.0), (1000.0, 503.0)),))  # 3 m away
    other = water.Water("0,5 - 3 meter", False, (((0.0, 800.0), (500.0, 800.0)),))
    res = iw.combine([same, other], [reach], areas, (0.0, 0.0), (25.0, 40, 40), choice="zomer")
    top = [g for g in res.groups if g.source.startswith("TOP10NL")]
    assert len(top) == 1 and top[0].length == pytest.approx(500.0, abs=1)  # only the other
    assert top[0].stage == -0.8 and top[0].bottom == pytest.approx(-0.8 - 0.4)
    assert not top[0].bottom_from_legger and top[0].width == 1.75


def test_outside_areas_and_dry_bottom_notes() -> None:
    areas = [_square("A", 0.0, 500.0, {"winter": -2.0})]
    reach = _reach("R", 2, [(0.0, 500.0), (1000.0, 500.0)], -1.5, -1.5)  # above -2.0
    res = iw.combine([], [reach], areas, (0.0, 0.0), (25.0, 40, 40), default_stage=-1.0)
    assert {g.area for g in res.groups} == {"A", None}
    assert any("droog" in n for n in res.notes)
    dry = next(g for g in res.groups if g.area == "A")
    assert dry.bottom_adjusted and dry.bottom == pytest.approx(-2.0 - iw.MIN_DEPTH)
    assert any("buiten de peilgebieden" in n for n in res.notes)


def test_saved_kampereiland_combination() -> None:
    raw = _saved()
    areas = ws.parse_level_areas(raw["levels"])
    reaches = ws.parse_reaches(raw["lines"], raw["attributes"])
    top = water.parse_waterdelen(
        json.loads((DATA / "top10nl_waterdeel_lijn.json").read_text()), area=False
    )  # another area: only checks that unrelated TOP10NL water does not disturb
    res = iw.combine(top, reaches, areas, (BBOX[0], BBOX[1]), (25.0, 40, 40), choice="winter")
    legger = [g for g in res.groups if g.source.startswith("Legger")]
    assert legger and all(math.isfinite(g.stage) for g in legger if g.area is not None)
    assert sum(g.bottom_from_legger for g in legger) >= len(legger) - 2
    assert len(res.groups) <= iw.MAX_GROUPS


def test_grouping_gets_coarser_with_many_profiles() -> None:
    areas = [_square("A", 0.0, 1000.0, {"winter": -1.0})]
    reaches = [
        _reach(f"R{k}", 1, [(0.0, 10.0 * k), (1000.0, 10.0 * k)], -1.0 - 0.1 * k, -1.0 - 0.1 * k)
        for k in range(60)
    ]
    res = iw.combine([], reaches, areas, (0.0, 0.0), (25.0, 40, 40))
    assert len(res.groups) <= iw.MAX_GROUPS
    assert any("afgerond" in n for n in res.notes)


@pytest.mark.online
def test_online_wdodelta_kampereiland() -> None:
    areas = ws.level_areas(BBOX)
    reaches = ws.reaches(BBOX)
    assert len(areas) >= 3 and reaches
    assert sum(math.isfinite(r.bottom) for r in reaches) >= 0.8 * len(reaches)
