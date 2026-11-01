"""Drawing and result maps for the plan-view grid product (Grondwatertoolbox)."""

from __future__ import annotations

from typing import Any

import numpy as np
import plotly.graph_objects as go

from gwproducten.core.grids import polyline_cell_lengths

PALETTE = (
    "#1f77b4", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f",
)  # fmt: skip
DRAIN_COLOR = "#7fbf7b"
GHB_COLOR = "#17becf"
WELL_COLOR = "#111111"
THRESHOLD_COLOR = "#d62828"
ZONE_COLORS = ("#c9a227", "#6a994e", "#bc4749", "#577590", "#9d4edd")


def to_cells(points: set[tuple[float, float]], cell: float, nrow: int, ncol: int) -> tuple:
    """Points in metres -> sorted unique (row, col) inside the grid."""
    cells = {(int(y // cell), int(x // cell)) for x, y in points}
    return tuple(sorted((r, c) for r, c in cells if 0 <= r < nrow and 0 <= c < ncol))


def cell_centres(cells: tuple, cell: float) -> set[tuple[float, float]]:
    return {((c + 0.5) * cell, (r + 0.5) * cell) for r, c in cells}


def centre_key(r: int, c: int, cell: float) -> tuple[float, float]:
    return (round((c + 0.5) * cell, 3), round((r + 0.5) * cell, 3))


def absolute_lines(
    wc: dict[str, Any], width: float, height: float
) -> list[list[tuple[float, float]]]:
    """Drawn lines in metres plus lines given as fractions of the model size (``rel_lines``,
    the default example), so the example scales with the model."""
    lines = [list(line) for line in wc.get("lines", [])]
    for line in wc.get("rel_lines", []):
        lines.append([(fx * width, fy * height) for fx, fy in line])
    return lines


def wc_geometry(
    wc: dict[str, Any], cell: float, nrow: int, ncol: int
) -> tuple[tuple[tuple[int, int], ...], tuple[float, ...]]:
    """Cells and length per cell of a drawn watercourse.

    Drawn lines give the length of the line inside each cell; lasso/box cells get the
    watercourse's default length; manual lengths (per cell centre, only valid for the cell
    size they were made with) override both, and 0 removes a cell.
    """
    lengths: dict[tuple[int, int], float] = {}
    for line in absolute_lines(wc, ncol * cell, nrow * cell):
        for rc, length in polyline_cell_lengths(line, cell, nrow, ncol).items():
            lengths[rc] = lengths.get(rc, 0.0) + length
    for rc in to_cells(wc.get("points", set()), cell, nrow, ncol):
        lengths.setdefault(rc, float(wc["length"]))
    if wc.get("manual_cell") == cell:
        for r, c in list(lengths):
            key = centre_key(r, c, cell)
            if key in wc.get("manual", {}):
                lengths[(r, c)] = wc["manual"][key]
    cells = tuple(sorted(rc for rc, length in lengths.items() if length > 1e-9))
    return cells, tuple(lengths[rc] for rc in cells)


def drawing_figure(
    nrow: int,
    ncol: int,
    cell: float,
    watercourses: list[dict[str, Any]],
    drain_cells: tuple,
    *,
    current_line: list[tuple[float, float]] | None = None,
    snap: float | None = None,
    wells: list[dict[str, Any]] | None = None,
    ghbs: list[dict[str, Any]] | None = None,
    zones: list[dict[str, Any]] | None = None,
) -> go.Figure:
    """Grid with zones (subsoil), watercourses (shade = length in the cell), drawn lines and
    pipe-drained cells. The last trace is the selection target: cell centres (box/lasso), or
    a snap grid of spacing ``snap`` for clicking line vertices."""
    x = (np.arange(ncol) + 0.5) * cell
    y = (np.arange(nrow) + 0.5) * cell
    fig = go.Figure()
    for k, zone in enumerate(zones or []):
        colour = ZONE_COLORS[k % len(ZONE_COLORS)]
        fig.add_trace(
            _cell_heatmap(
                dict.fromkeys(zone["cells"], 1.0), cell, colorscale=[[0, colour], [1, colour]],
                showscale=False, opacity=0.35, name=zone["name"],
                hovertemplate=f"zone {zone['name']}<extra></extra>",
            )
        )  # fmt: skip
        fig.add_trace(
            go.Scatter(
                x=[None], y=[None], mode="markers", name=f"zone {zone['name']}",
                marker={"symbol": "square", "size": 12, "color": colour, "opacity": 0.5},
            )
        )  # fmt: skip
    if drain_cells:
        fig.add_trace(
            _cell_heatmap(
                dict.fromkeys(drain_cells, 1.0), cell,
                colorscale=[[0, DRAIN_COLOR], [1, DRAIN_COLOR]], showscale=False,
                hoverinfo="skip", name="buisdrainage",
            )
        )  # fmt: skip
    for k, wc in enumerate(watercourses):
        colour = PALETTE[k % len(PALETTE)]
        fig.add_trace(
            _cell_heatmap(
                dict(zip(wc["cells"], wc["lengths"], strict=True)), cell, zmin=0.0, zmax=cell,
                colorscale=[[0, "rgba(255,255,255,0.6)"], [1, colour]], showscale=False,
                hovertemplate=wc["name"] + "<br>lengte in cel %{z:.1f} m<extra></extra>",
                name=wc["name"],
            )
        )  # fmt: skip
        for line in wc.get("lines", []):
            fig.add_trace(
                go.Scatter(
                    x=[p[0] for p in line], y=[p[1] for p in line], mode="lines",
                    line={"color": "black", "width": 2}, hoverinfo="skip", showlegend=False,
                )
            )  # fmt: skip
        fig.add_trace(  # legend entry
            go.Scatter(
                x=[None], y=[None], mode="markers", name=wc["name"],
                marker={"symbol": "square", "size": 12, "color": colour},
            )
        )  # fmt: skip
    for g in ghbs or []:
        fig.add_trace(
            _cell_heatmap(
                dict.fromkeys(g["cells"], 1.0), cell, colorscale=[[0, GHB_COLOR], [1, GHB_COLOR]],
                showscale=False, opacity=0.7, name=g["name"],
                hovertemplate=g["name"] + " (GHB)<extra></extra>",
            )
        )  # fmt: skip
        fig.add_trace(
            go.Scatter(
                x=[None], y=[None], mode="markers", name=f"{g['name']} (GHB)",
                marker={"symbol": "square", "size": 12, "color": GHB_COLOR},
            )
        )  # fmt: skip
    _add_wells(fig, wells or [], cell)
    if current_line:
        fig.add_trace(
            go.Scatter(
                x=[p[0] for p in current_line], y=[p[1] for p in current_line],
                mode="lines+markers", line={"color": THRESHOLD_COLOR, "width": 3},
                marker={"size": 8}, name="lijn in aanbouw", hoverinfo="skip",
            )
        )  # fmt: skip
    if drain_cells:
        fig.add_trace(
            go.Scatter(
                x=[None], y=[None], mode="markers", name="buisdrainage",
                marker={"symbol": "square", "size": 12, "color": DRAIN_COLOR},
            )
        )  # fmt: skip
    fig.add_trace(_grid_lines(nrow, ncol, cell))
    if snap is None:
        gx, gy = np.meshgrid(x, y)
        target = go.Scattergl(
            x=_f4(gx), y=_f4(gy), mode="markers", name="cellen",
            marker={"size": 4, "color": "rgba(80,80,80,0.35)"}, showlegend=False,
            hovertemplate="x = %{x:.0f} m<br>y = %{y:.0f} m<extra></extra>",
        )  # fmt: skip
    else:
        sx = np.arange(0.0, ncol * cell + 0.5 * snap, snap)
        sy = np.arange(0.0, nrow * cell + 0.5 * snap, snap)
        gx, gy = np.meshgrid(sx, sy)
        target = go.Scattergl(
            x=_f4(gx), y=_f4(gy), mode="markers", name="klikpunten",
            marker={"size": 3, "color": "rgba(60,60,60,0.25)"}, showlegend=False,
            hovertemplate="klik: x = %{x:.1f} m, y = %{y:.1f} m<extra></extra>",
        )  # fmt: skip
    fig.add_trace(target)
    _square_layout(fig, ncol * cell, nrow * cell, cell)
    fig.update_layout(
        dragmode="select" if snap is None else "pan",
        clickmode="event+select",
        legend={"orientation": "h", "y": -0.12},
        uirevision=f"{nrow}x{ncol}x{cell}",  # keep zoom/pan while the grid stays the same
        hovermode="closest",
        hoverdistance=50,  # a click picks the nearest snap point within ~50 px
    )
    return fig


def result_figure(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    cell: float,
    watercourses: list[dict[str, Any]],
    *,
    label: str,
    threshold: float | None = None,
    diverging: bool = False,
    reverse: bool = False,
    profile: list[tuple[float, float]] | None = None,
    click_snap: float | None = None,
    wells: list[dict[str, Any]] | None = None,
    min_span: float = 0.0,
) -> go.Figure:
    """Contour map of a result field with watercourse cells outlined, an optional profile
    line, and (``click_snap``) a last trace of click points on a snap grid. ``min_span``:
    smallest half-range of a diverging scale, so that tiny values stay near white."""
    if diverging:
        vmax = max(float(np.nanmax(np.abs(z))), min_span) or 1.0
        scale = {"colorscale": "RdBu", "zmin": -vmax, "zmax": vmax}
    else:
        scale = {"colorscale": "Viridis_r" if reverse else "Viridis"}
    z32 = np.asarray(z, dtype=np.float32)  # half the bytes; mm precision is kept
    fig = go.Figure(
        go.Contour(
            x=x, y=y, z=z32, ncontours=20, contours={"showlabels": True}, colorbar={"title": label},
            hovertemplate="x = %{x:.0f} m<br>y = %{y:.0f} m<br>%{z:.3f} m<extra></extra>",
            **scale,
        )
    )  # fmt: skip
    if threshold is not None and np.nanmax(z) > threshold:
        fig.add_trace(
            go.Contour(
                x=x, y=y, z=z32,
                contours={"start": threshold, "end": threshold, "size": 1, "coloring": "none"},
                line={"color": THRESHOLD_COLOR, "width": 4}, showscale=False, hoverinfo="skip",
                name=f"{threshold * 100:.0f} cm-grens", showlegend=True,
            )
        )  # fmt: skip
    for k, wc in enumerate(watercourses):
        xs = [(c + 0.5) * cell for _, c in wc["cells"]]
        ys = [(r + 0.5) * cell for r, _ in wc["cells"]]
        fig.add_trace(
            go.Scatter(
                x=xs, y=ys, mode="markers", name=wc["name"],
                marker={"symbol": "square-open", "size": 7, "color": PALETTE[k % len(PALETTE)],
                        "line": {"width": 2}},
            )
        )  # fmt: skip
    _add_wells(fig, wells or [], cell)
    if profile:
        fig.add_trace(
            go.Scatter(
                x=[p[0] for p in profile], y=[p[1] for p in profile], mode="lines+markers",
                line={"color": "white", "width": 3, "dash": "dash"},
                marker={"size": 8, "color": "black"}, name="profiel", hoverinfo="skip",
            )
        )  # fmt: skip
        fig.add_annotation(
            x=profile[0][0], y=profile[0][1], text="A", showarrow=False, yshift=14,
            font={"size": 14, "color": "black"}, bgcolor="white",
        )  # fmt: skip
        if len(profile) > 1:
            fig.add_annotation(
                x=profile[-1][0], y=profile[-1][1], text="B", showarrow=False, yshift=14,
                font={"size": 14, "color": "black"}, bgcolor="white",
            )  # fmt: skip
    width, height = x[-1] + x[0], y[-1] + y[0]
    if click_snap is not None:
        sx = np.arange(0.0, width + 0.5 * click_snap, click_snap)
        sy = np.arange(0.0, height + 0.5 * click_snap, click_snap)
        gx, gy = np.meshgrid(sx, sy)
        fig.add_trace(
            go.Scattergl(
                x=_f4(gx), y=_f4(gy), mode="markers", name="klikpunten",
                marker={"size": 3, "color": "rgba(255,255,255,0.25)"}, showlegend=False,
                hovertemplate="klik: x = %{x:.0f} m, y = %{y:.0f} m<extra></extra>",
            )
        )  # fmt: skip
        fig.update_layout(dragmode="pan", clickmode="event+select", hoverdistance=50)
    _square_layout(fig, width, height, cell)
    fig.update_layout(uirevision=f"{width}x{height}x{cell}")
    fig.update_layout(legend={"orientation": "h", "y": -0.12})
    return fig


def _f4(a: np.ndarray) -> np.ndarray:
    """Flattened float32 copy: sent to the browser as half the bytes of float64."""
    return np.ravel(a).astype(np.float32)


def _cell_heatmap(values: dict[tuple[int, int], float], cell: float, **kwargs: Any) -> go.Heatmap:
    """Heatmap of only the bounding box of the given cells (row, col) -> value, placed with
    x0/dx and y0/dy; the rest of the grid is not sent to the browser."""
    if not values:
        return go.Heatmap(z=[[None]], x0=0.5 * cell, dx=cell, y0=0.5 * cell, dy=cell, **kwargs)
    rows = [r for r, _ in values]
    cols = [c for _, c in values]
    r0, c0 = min(rows), min(cols)
    z = np.full((max(rows) - r0 + 1, max(cols) - c0 + 1), np.nan, dtype=np.float32)
    for (r, c), v in values.items():
        z[r - r0, c - c0] = v
    return go.Heatmap(z=z, x0=(c0 + 0.5) * cell, dx=cell, y0=(r0 + 0.5) * cell, dy=cell, **kwargs)


def _grid_lines(nrow: int, ncol: int, cell: float) -> go.Scatter:
    """Cell edges as one trace (None-separated segments); they help to see partial cells.
    One trace instead of a shape per line keeps fine grids fast."""
    w, h = ncol * cell, nrow * cell
    xs: list[float | None] = []
    ys: list[float | None] = []
    for k in range(ncol + 1):
        xs += [k * cell, k * cell, None]
        ys += [0.0, h, None]
    for k in range(nrow + 1):
        xs += [0.0, w, None]
        ys += [k * cell, k * cell, None]
    return go.Scatter(
        x=xs, y=ys, mode="lines", line={"color": "rgba(0,0,0,0.08)", "width": 1},
        hoverinfo="skip", showlegend=False, name="rooster",
    )  # fmt: skip


def _add_wells(fig: go.Figure, wells: list[dict[str, Any]], cell: float) -> None:
    for g in wells:
        fig.add_trace(
            go.Scatter(
                x=[(c + 0.5) * cell for _, c in g["cells"]],
                y=[(r + 0.5) * cell for r, _ in g["cells"]],
                mode="markers", name=f"{g['name']} (put)",
                marker={"symbol": "x-thin", "size": 11, "color": WELL_COLOR,
                        "line": {"width": 3, "color": WELL_COLOR}},
                hovertemplate=g["name"] + "<extra>put</extra>",
            )
        )  # fmt: skip


def _square_layout(fig: go.Figure, width: float, height: float, cell: float) -> None:
    fig.update_layout(
        height=640,
        margin={"t": 30},
        xaxis={"title": "x [m]", "range": [0, width], "constrain": "domain"},
        yaxis={"title": "y [m]", "range": [0, height], "scaleanchor": "x", "scaleratio": 1},
    )
