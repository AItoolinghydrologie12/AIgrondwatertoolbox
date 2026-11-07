"""The remembered watercourse geometry (app/grid_ui.wc_geometry_cached) always equals a fresh
calculation, also after a watercourse changes in place."""

from grid_ui import wc_geometry, wc_geometry_cached


def check(wc: dict, cell: float = 25.0, nrow: int = 40, ncol: int = 40) -> None:
    assert wc_geometry_cached(wc, cell, nrow, ncol) == wc_geometry(wc, cell, nrow, ncol)


def test_cached_geometry_follows_every_input() -> None:
    wc = {
        "lines": [[(100.0, 0.0), (150.0, 900.0)]], "points": set(), "length": 25.0,
        "manual": {}, "manual_cell": 25.0,
    }  # fmt: skip
    check(wc)
    wc["lines"][0].append((600.0, 950.0))  # line extended in place
    check(wc)
    wc["points"] = {(512.5, 512.5)}  # lasso cell
    check(wc)
    wc["length"] = 10.0  # default length of lasso cells
    check(wc)
    wc["manual"] = {(512.5, 512.5): 3.0}  # manual length
    check(wc)
    wc["line_offsets"] = [-0.3]  # legger bottom per line
    check(wc)
    wc["rel_lines"] = [[(0.5, 0.0), (0.5, 1.0)]]
    check(wc)
    check(wc, cell=20.0, nrow=50, ncol=50)  # other grid
    check(wc, cell=50.0, nrow=20, ncol=20)
