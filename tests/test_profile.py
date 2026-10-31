"""Cross-section profile through a plan-view result: sampling, interpolation, crossings."""

import numpy as np
import pytest
from profile_ui import crossings, interpolate, profile_table, sample_line


def test_sample_line_spacing_and_length() -> None:
    d, x, y = sample_line([(0.0, 0.0), (30.0, 40.0), (30.0, 60.0)], step=7.0)
    assert d[-1] == pytest.approx(70.0)
    assert np.all(np.diff(d) <= 7.0 + 1e-12)
    assert (x[-1], y[-1]) == pytest.approx((30.0, 60.0))
    assert np.hypot(np.diff(x), np.diff(y)) == pytest.approx(np.diff(d))


def test_interpolation_is_exact_for_a_plane_and_constant_outside() -> None:
    cell = 25.0
    x = (np.arange(8) + 0.5) * cell
    y = (np.arange(6) + 0.5) * cell
    field = 0.01 * x[None, :] - 0.02 * y[:, None] + 1.0
    xs, ys = np.array([12.5, 40.0, 199.0]), np.array([12.5, 70.0, 140.0])
    expected = 0.01 * np.clip(xs, x[0], x[-1]) - 0.02 * np.clip(ys, y[0], y[-1]) + 1.0
    assert interpolate(x, y, field, xs, ys) == pytest.approx(expected)


def test_profile_table_and_crossings_on_a_grid() -> None:
    cell, n = 25.0, 10
    x = y = (np.arange(n) + 0.5) * cell
    ref = np.full((1, n, n), -1.0)
    new = ref + 0.1 * np.exp(-(((x[None, :] - 125.0) / 50.0) ** 2))[None]
    df = profile_table(
        [(0.0, 112.5), (250.0, 112.5)], x, y, cell, 0.0, ref, new, {"freatisch": "freatisch"}
    )
    # the peak (x = 125 m) lies between two centres: linear interpolation gives their value
    assert df["verhoging freatisch [m]"].max() == pytest.approx(0.1 * np.exp(-0.0625))
    assert (df["maaiveld [m NAP]"] == 0.0).all()
    wc = {"name": "sloot", "cells": tuple((r, 4) for r in range(n)), "stage_ref": -1.0,
          "stage_new": -0.8}  # fmt: skip
    found = crossings(df["afstand [m]"].to_numpy(), df["x [m]"].to_numpy(),
                      df["y [m]"].to_numpy(), cell, [wc])  # fmt: skip
    assert len(found) == 1
    assert found[0].name == "sloot"
    assert 100.0 <= found[0].distance <= 125.0
