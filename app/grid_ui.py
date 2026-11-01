"""Drawing and result maps for the plan-view grid product (Grondwatertoolbox)."""

from __future__ import annotations

from typing import Any

import numpy as np
import plotly.graph_objects as go

from gwproducten.core.grids import Face, outline_faces, polyline_cell_lengths, polyline_faces

PALETTE = (
    "#1f77b4", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f",
)  # fmt: skip
DRAIN_COLOR = "#7fbf7b"
GHB_COLOR = "#17becf"
WELL_COLOR = "#111111"
WALL_COLOR = "#000000"
THRESHOLD_COLOR = "#d62828"
MAX_LEGEND = 8  # watercourses in the legend; more would push the map away
SNAP_POINTS = 100  # click points along the longest side when drawing a line
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


def line_snap(cell: float, size_x: float, size_y: float) -> float:
    """Spacing of the click points for drawing a line: a fifth of a cell, but at most
    SNAP_POINTS along the longest side (the points go to the browser on every rerun)."""
    return max(cell / 5.0, max(size_x, size_y) / SNAP_POINTS)


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
    outlines: list[list[tuple[float, float]]] | None = None,
    walls: list[dict[str, Any]] | None = None,
) -> go.Figure:
    """Grid with zones (subsoil), level-area borders (``outlines``), watercourses (shade =
    length in the cell), drawn lines, pipe-drained cells and walls (on cell faces). The last
    trace is the selection target: cell centres (box/lasso), or a snap grid of spacing
    ``snap`` for clicking line vertices."""
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
        fig.add_trace(  # legend entry; with many watercourses only the hover names them
            go.Scatter(
                x=[None], y=[None], mode="markers", name=wc["name"],
                marker={"symbol": "square", "size": 12, "color": colour},
                showlegend=len(watercourses) <= MAX_LEGEND,
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
    add_walls(fig, walls or [], cell)
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
    if outlines:
        xs: list[float | None] = []
        ys: list[float | None] = []
        for ring in outlines:
            xs += [p[0] for p in ring] + [None]
            ys += [p[1] for p in ring] + [None]
        fig.add_trace(
            go.Scatter(
                x=xs, y=ys, mode="lines", name="peilgebiedgrens", hoverinfo="skip",
                line={"color": "#52514e", "width": 1.5, "dash": "dash"},
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
    walls: list[dict[str, Any]] | None = None,
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
                showlegend=len(watercourses) <= MAX_LEGEND,
            )
        )  # fmt: skip
    _add_wells(fig, wells or [], cell)
    add_walls(fig, walls or [], cell)
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


def add_background(
    fig: go.Figure, background: tuple[str, float] | None, width: float, height: float
) -> None:
    """Map image (data URI, opacity) under the traces, stretched over the model area."""
    if background is None:
        return
    uri, opacity = background
    fig.add_layout_image(
        source=uri, xref="x", yref="y", x=0.0, y=height, sizex=width, sizey=height,
        sizing="stretch", layer="below", opacity=opacity,
    )  # fmt: skip


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


def face_segment(face: Face, cell: float) -> list[tuple[float, float]]:
    """The two end points [m] of a cell face."""
    r, c, axis = face
    if axis == "x":
        return [((c + 1) * cell, r * cell), ((c + 1) * cell, (r + 1) * cell)]
    return [(c * cell, (r + 1) * cell), ((c + 1) * cell, (r + 1) * cell)]


def wall_faces(wall: dict[str, Any], cell: float, nrow: int, ncol: int) -> tuple[Face, ...]:
    """Faces of a drawn wall on the current grid: its lines (in metres) follow the grid
    lines; a wall drawn around a selection is stored as the segments of its outline."""
    faces: dict[Face, None] = {}
    for line in wall.get("lines", []):
        faces.update(dict.fromkeys(polyline_faces(line, cell, nrow, ncol)))
    return tuple(faces)


def outline_lines(
    points: set[tuple[float, float]], cell: float, nrow: int, ncol: int
) -> list[list[tuple[float, float]]]:
    """Segments [m] of the outline of the cells at ``points`` (cell centres)."""
    cells = set(to_cells(points, cell, nrow, ncol))
    return [face_segment(f, cell) for f in outline_faces(cells, nrow, ncol)]


def add_walls(fig: go.Figure, walls: list[dict[str, Any]], cell: float) -> None:
    """One thick line trace per wall along its faces."""
    for w in walls:
        xs: list[float | None] = []
        ys: list[float | None] = []
        for f in w["faces"]:
            (x0, y0), (x1, y1) = face_segment(f, cell)
            xs += [x0, x1, None]
            ys += [y0, y1, None]
        fig.add_trace(
            go.Scatter(
                x=xs, y=ys, mode="lines", name=f"{w['name']} (damwand)",
                line={"color": WALL_COLOR, "width": 5},
                hovertemplate=w["name"] + "<extra>damwand</extra>",
            )
        )  # fmt: skip


def _square_layout(fig: go.Figure, width: float, height: float, cell: float) -> None:
    fig.update_layout(
        height=640,
        margin={"t": 30},
        xaxis={"title": "x [m]", "range": [0, width], "constrain": "domain"},
        yaxis={"title": "y [m]", "range": [0, height], "scaleanchor": "x", "scaleratio": 1},
    )
