"""Lengths of a drawn polyline per grid cell (iMOD-style watercourse geometry)."""

import math

import pytest

from gwproducten.core.grids import polyline_cell_lengths

CELL, NROW, NCOL = 25.0, 40, 40


def test_straight_line_through_four_cells() -> None:
    out = polyline_cell_lengths([(0.0, 12.5), (100.0, 12.5)], CELL, NROW, NCOL)
    assert out == {(0, c): pytest.approx(25.0) for c in range(4)}


def test_diagonal_through_one_cell() -> None:
    out = polyline_cell_lengths([(25.0, 25.0), (50.0, 50.0)], CELL, NROW, NCOL)
    assert out == {(1, 1): pytest.approx(25.0 * math.sqrt(2))}


def test_line_ending_halfway_a_cell() -> None:
    out = polyline_cell_lengths([(12.5, 0.0), (12.5, 62.5)], CELL, NROW, NCOL)
    assert out[(0, 0)] == pytest.approx(25.0)
    assert out[(1, 0)] == pytest.approx(25.0)
    assert out[(2, 0)] == pytest.approx(12.5)


def test_bend_and_total_length() -> None:
    pts = [(10.0, 10.0), (110.0, 10.0), (110.0, 73.0), (40.0, 140.0)]
    out = polyline_cell_lengths(pts, CELL, NROW, NCOL)
    total = sum(math.dist(a, b) for a, b in zip(pts[:-1], pts[1:], strict=True))
    assert sum(out.values()) == pytest.approx(total)
    assert all(v > 0 for v in out.values())


def test_line_on_grid_line_goes_to_one_side() -> None:
    out = polyline_cell_lengths([(50.0, 0.0), (50.0, 50.0)], CELL, NROW, NCOL)
    assert out == {(0, 2): pytest.approx(25.0), (1, 2): pytest.approx(25.0)}


def test_part_outside_model_is_clipped() -> None:
    out = polyline_cell_lengths([(-50.0, 12.5), (50.0, 12.5)], CELL, NROW, NCOL)
    assert sum(out.values()) == pytest.approx(50.0)
    assert set(out) == {(0, 0), (0, 1)}
