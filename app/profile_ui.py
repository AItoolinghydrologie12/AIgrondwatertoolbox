"""Cross-section profile along a line through a plan-view grid result (Grondwatertoolbox)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.interpolate import RegularGridInterpolator

SURFACE_COLOR = "#8c6d46"
RISE_COLOR = "rgba(214,40,40,0.25)"
DRAWDOWN_SHOWN = 0.01  # [m] smallest freatic drawdown that shows the drawdown limit and tiles
LAYER_COLORS = ("#1f77b4", "#2ca02c")


@dataclass(frozen=True)
class Crossing:
    name: str
    distance: float  # [m] along the profile
    stage_ref: float
    stage_new: float


def sample_line(
    points: list[tuple[float, float]], step: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Densify a polyline: (distance along the line, x, y) every ``step`` m or less."""
    d_out, x_out, y_out = [0.0], [points[0][0]], [points[0][1]]
    for (x0, y0), (x1, y1) in zip(points[:-1], points[1:], strict=True):
        seg = math.dist((x0, y0), (x1, y1))
        if seg == 0:
            continue
        n = max(1, math.ceil(seg / step))
        t = np.arange(1, n + 1) / n
        d_out.extend(d_out[-1] + t * seg)
        x_out.extend(x0 + t * (x1 - x0))
        y_out.extend(y0 + t * (y1 - y0))
    return np.array(d_out), np.array(x_out), np.array(y_out)


def interpolate(
    x: np.ndarray, y: np.ndarray, field: np.ndarray, xs: np.ndarray, ys: np.ndarray
) -> np.ndarray:
    """Bilinear interpolation of a cell-centred field; constant beyond the outer centres."""
    f = RegularGridInterpolator((y, x), field, method="linear")
    pts = np.column_stack([np.clip(ys, y[0], y[-1]), np.clip(xs, x[0], x[-1])])
    return f(pts)


def crossings(
    distance: np.ndarray,
    xs: np.ndarray,
    ys: np.ndarray,
    cell: float,
    watercourses: list[dict[str, Any]],
) -> list[Crossing]:
    """Watercourses whose cells the profile passes; one entry per contiguous pass, placed
    at the middle of that pass."""
    rc = list(zip((ys // cell).astype(int), (xs // cell).astype(int), strict=True))
    out = []
    for wc in watercourses:
        cells = set(wc["cells"])
        inside = np.array([c in cells for c in rc])
        k = 0
        while k < inside.size:
            if not inside[k]:
                k += 1
                continue
            start = k
            while k < inside.size and inside[k]:
                k += 1
            end = distance[k] if k < inside.size else distance[-1]  # first sample outside
            mid = 0.5 * (distance[start] + end)
            out.append(Crossing(wc["name"], float(mid), wc["stage_ref"], wc["stage_new"]))
    return sorted(out, key=lambda c: c.distance)


def wall_crossings(
    distance: np.ndarray, xs: np.ndarray, ys: np.ndarray, cell: float, walls: list[Any]
) -> list[tuple[str, float]]:
    """(wall name, distance) where the profile passes a face of a wall: two successive
    samples in neighbouring cells on both sides of that face."""
    r = (ys // cell).astype(int)
    c = (xs // cell).astype(int)
    out = []
    for w in walls:
        faces = set(w.faces)
        for k in range(len(distance) - 1):
            (r0, c0), (r1, c1) = (r[k], c[k]), (r[k + 1], c[k + 1])
            if abs(r1 - r0) + abs(c1 - c0) != 1:
                continue
            face = (min(r0, r1), min(c0, c1), "x" if r0 == r1 else "y")
            if face in faces:
                out.append((w.name, float(0.5 * (distance[k] + distance[k + 1]))))
    return sorted(out, key=lambda item: item[1])


def profile_table(
    points: list[tuple[float, float]],
    x: np.ndarray,
    y: np.ndarray,
    cell: float,
    surface: float | np.ndarray,
    heads_ref: np.ndarray,
    heads_new: np.ndarray,
    node_labels: dict[str, str],
) -> pd.DataFrame:
    distance, xs, ys = sample_line(points, cell / 4.0)
    df = pd.DataFrame({"afstand [m]": distance, "x [m]": xs, "y [m]": ys})
    if np.ndim(surface) == 0:
        df["maaiveld [m NAP]"] = surface
    else:  # per cell (zones): the value of the cell under each sample point
        row = np.clip((ys // cell).astype(int), 0, surface.shape[0] - 1)
        col = np.clip((xs // cell).astype(int), 0, surface.shape[1] - 1)
        df["maaiveld [m NAP]"] = surface[row, col]
    for j, label in enumerate(node_labels.values()):
        href = interpolate(x, y, heads_ref[j], xs, ys)
        hnew = interpolate(x, y, heads_new[j], xs, ys)
        df[f"h ref {label} [m NAP]"] = href
        df[f"h opzet {label} [m NAP]"] = hnew
        df[f"verhoging {label} [m]"] = hnew - href
    return df


def profile_figure(
    df: pd.DataFrame,
    node_labels: dict[str, str],
    crossed: list[Crossing],
    threshold: float,
    walls: list[tuple[str, float]] = (),  # type: ignore[assignment]
) -> go.Figure:
    """Heads (ref dashed, opzet solid) per layer and the surface; rise of the top layer on
    the second axis; crossed watercourses with their levels and crossed walls."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    d = df["afstand [m]"]
    labels = list(node_labels.values())
    top = labels[0]
    fig.add_trace(
        go.Scatter(
            x=d, y=df[f"verhoging {top} [m]"], name=f"verhoging {top} [m]", fill="tozeroy",
            line={"color": "rgba(214,40,40,0.6)", "width": 1}, fillcolor=RISE_COLOR,
            hovertemplate="%{y:.3f} m<extra>verhoging</extra>",
        ),
        secondary_y=True,
    )  # fmt: skip
    rise = df[f"verhoging {top} [m]"]
    rise_max = max(float(np.nanmax(rise)), threshold)
    drawdown = -min(float(np.nanmin(rise)), 0.0)
    fig.add_hline(
        y=threshold, secondary_y=True, line={"color": "#d62828", "dash": "dot", "width": 1},
        annotation_text=f"{threshold * 100:.0f} cm", annotation_position="right",
    )  # fmt: skip
    if drawdown >= DRAWDOWN_SHOWN:  # a dewatering: the drawdown limit as well
        drawdown = max(drawdown, threshold)
        fig.add_hline(
            y=-threshold, secondary_y=True, line={"color": "#1f5fbf", "dash": "dot", "width": 1},
            annotation_text=f"−{threshold * 100:.0f} cm", annotation_position="right",
        )  # fmt: skip
    else:
        drawdown = 0.0
    fig.add_trace(
        go.Scatter(
            x=d, y=df["maaiveld [m NAP]"], name="maaiveld", mode="lines",
            line={"color": SURFACE_COLOR, "width": 3},
        )
    )  # fmt: skip
    for j, label in enumerate(labels):
        colour = LAYER_COLORS[j % len(LAYER_COLORS)]
        fig.add_trace(
            go.Scatter(
                x=d, y=df[f"h ref {label} [m NAP]"], name=f"{label} referentie", mode="lines",
                line={"color": colour, "dash": "dash", "width": 2},
                hovertemplate="%{y:.2f} m NAP<extra>" + label + " ref</extra>",
            )
        )  # fmt: skip
        fig.add_trace(
            go.Scatter(
                x=d, y=df[f"h opzet {label} [m NAP]"], name=f"{label} na opzet", mode="lines",
                line={"color": colour, "width": 3},
                hovertemplate="%{y:.2f} m NAP<extra>" + label + " opzet</extra>",
            )
        )  # fmt: skip
    for c in crossed:
        fig.add_vline(x=c.distance, line={"color": "rgba(0,0,0,0.25)", "width": 1})
        fig.add_trace(
            go.Scatter(
                x=[c.distance, c.distance], y=[c.stage_ref, c.stage_new], mode="markers",
                marker={"symbol": ["triangle-down-open", "triangle-down"], "size": 13,
                        "color": "#0b5394"},
                name=c.name, showlegend=False,
                hovertemplate=c.name + "<br>peil %{y:.2f} m NAP<extra></extra>",
            )
        )  # fmt: skip
        fig.add_annotation(
            x=c.distance, y=1.0, yref="paper", text=c.name, showarrow=False, textangle=-30,
            font={"size": 11}, yanchor="bottom",
        )  # fmt: skip
    for name, dist in walls:
        fig.add_vline(x=dist, line={"color": "black", "width": 4})
        fig.add_annotation(
            x=dist, y=0.0, yref="paper", text=f"{name} (damwand)", showarrow=False,
            textangle=-90, font={"size": 11}, xanchor="right", yanchor="bottom",
        )  # fmt: skip
    fig.update_yaxes(title_text="stijghoogte [m NAP]", secondary_y=False)
    fig.update_yaxes(
        title_text="verhoging [m]" if drawdown == 0.0 else "verhoging (− = verlaging) [m]",
        secondary_y=True,
        range=[-3.0 * drawdown, 3.0 * rise_max],
        showgrid=False,
        tickmode="auto",
        nticks=6,
        tickformat=".2f",
    )
    fig.update_layout(
        height=460, margin={"t": 60}, xaxis_title="afstand langs profiel [m]",
        legend={"orientation": "h", "y": -0.2}, hovermode="x unified",
    )  # fmt: skip
    return fig
