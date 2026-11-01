"""Drawing actions of the grid page (the selection logic behind 'Toepassen')."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from draw_actions import NEW, apply_selection  # noqa: E402

KEY = "p3"
GRID = (25.0, 40, 40)  # cell, nrow, ncol


def centres(*cells: tuple[int, int]) -> set[tuple[float, float]]:
    return {((c + 0.5) * 25.0, (r + 0.5) * 25.0) for r, c in cells}


def empty_state() -> dict:
    return {f"{KEY}_{k}": [] for k in ("wcs", "wells", "ghb", "zones")} | {f"{KEY}_drain": set()}


def test_watercourse_new_add_and_remove_cells() -> None:
    st = empty_state()
    props = {"name": "W", "length": 25.0}
    apply_selection(st, KEY, GRID, "watergang", "cellen", NEW, props, centres((1, 1), (2, 1)))
    assert [w["name"] for w in st["p3_wcs"]] == ["W"]
    apply_selection(st, KEY, GRID, "watergang", "cellen", "W", {}, centres((3, 1)))
    assert len(st["p3_wcs"][0]["points"]) == 3
    apply_selection(st, KEY, GRID, "watergang", "wissen", None, {}, centres((1, 1), (2, 1), (3, 1)))
    assert st["p3_wcs"] == []  # no cells left: watercourse removed


def test_drain_wells_ghb_add_and_remove() -> None:
    st = empty_state()
    apply_selection(st, KEY, GRID, "drain", "toevoegen", None, {}, centres((0, 0), (0, 1)))
    apply_selection(st, KEY, GRID, "drain", "verwijderen", None, {}, centres((0, 0)))
    assert st["p3_drain"] == centres((0, 1))
    for obj, name in (("put", "wells"), ("ghb", "ghb")):
        apply_selection(st, KEY, GRID, obj, "toevoegen", None, {"name": "g"}, centres((5, 5)))
        assert len(st[f"p3_{name}"]) == 1
        apply_selection(st, KEY, GRID, obj, "verwijderen", None, {}, centres((5, 5)))
        assert st[f"p3_{name}"] == []


def test_zone_new_moves_cells_and_remove() -> None:
    st = empty_state()
    apply_selection(st, KEY, GRID, "zone", "toevoegen", NEW, {}, centres((1, 1), (1, 2)))
    apply_selection(st, KEY, GRID, "zone", "toevoegen", NEW, {}, centres((1, 2)))
    z1, z2 = st["p3_zones"]
    assert (z1["name"], z2["name"]) == ("Zone 1", "Zone 2")
    assert z1["points"] == centres((1, 1)) and z2["points"] == centres((1, 2))  # moved
    apply_selection(st, KEY, GRID, "zone", "verwijderen", None, {}, centres((1, 1), (1, 2)))
    assert not z1["points"] and not z2["points"]


def test_wall_around_a_selection_and_on_another_grid() -> None:
    """A wall around 2 x 2 cells: 8 faces; stored in metres, so on a 12.5 m grid the same
    outline gives 16 faces."""
    from grid_ui import wall_faces

    st = empty_state() | {"p3_walls": []}
    props = {"name": "bouwput", "layer": "pakket1", "impermeable": True, "reference": False}
    apply_selection(
        st, KEY, GRID, "damwand", "rondom", NEW, props, centres((5, 5), (5, 6), (6, 5), (6, 6))
    )
    (wall,) = st["p3_walls"]
    assert len(wall_faces(wall, 25.0, 40, 40)) == 8
    assert len(wall_faces(wall, 12.5, 80, 80)) == 16
    apply_selection(st, KEY, GRID, "damwand", "rondom", "bouwput", {}, centres((20, 20)))
    assert len(wall_faces(st["p3_walls"][0], 25.0, 40, 40)) == 12
