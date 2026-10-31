"""Cross-section drawing of the layered schematisation (scales with the input).

The top zone (deepest watercourse plus margin) is drawn enlarged so that ditches stay
visible next to thick aquifers; the vertical scale changes at a marked depth.
"""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

from gwproducten.products._layered import Ditch, Subsoil

AQUIFER_COLORS = ("#e9d8a6", "#f1e3b8", "#e4cf92", "#efdcab")
AQUITARD_COLORS = ("#9c7b5b", "#8a8178", "#7d6f63", "#938373")
WATER = "rgba(60, 130, 200, 0.95)"
DRY = "rgba(255, 255, 255, 0.95)"
TRENCH = "#c62828"
HEIGHT = 420


class _DepthScale:
    """Piecewise-linear depth axis: the top ``zoom`` metres take half the plot height."""

    def __init__(self, total: float, zoom: float) -> None:
        self.total = total
        self.zoom = zoom if zoom < 0.5 * total else total  # no break when not needed

    def __call__(self, depth: float | np.ndarray) -> float | np.ndarray:
        d = np.asarray(depth, dtype=float)
        if self.zoom >= self.total:
            y = -d
        else:
            half = 0.5 * self.total
            y = np.where(
                d <= self.zoom,
                -d * half / self.zoom,
                -half - (d - self.zoom) * half / (self.total - self.zoom),
            )
        return float(y) if y.ndim == 0 else y

    def ticks(self) -> tuple[list[float], list[str]]:
        top = _nice_ticks(self.zoom)
        deep = [] if self.zoom >= self.total else _nice_ticks(self.total, start=self.zoom)
        gap = 0.15 * (self.total - self.zoom)  # keep labels apart around the scale break
        deep = [d for d in deep if d - self.zoom > gap]
        depths = sorted(set(top + deep + [self.total]))
        return [self(d) for d in depths], [f"{-d + 0.0:g}" for d in depths]


def _nice_ticks(upto: float, start: float = 0.0) -> list[float]:
    span = upto - start
    step = next(s for s in (0.5, 1, 2, 5, 10, 20, 50, 100) if span / s <= 6)
    first = np.ceil(start / step) * step
    return [round(float(v), 6) for v in np.arange(first, upto + 1e-9, step)]


def draw_cross_section(
    sub: Subsoil,
    ditches: tuple[Ditch, ...],
    *,
    trench: tuple[float, int, str] | None = None,
    title: str = "",
    top_note: str | None = None,
    surface_nap: float | None = None,
) -> go.Figure:
    """``trench`` = (x, node index, label). ``top_note`` replaces the default drainage label
    at the surface; with ``surface_nap`` the vertical axis is in m NAP instead of m-mv."""
    stack = sub.stack
    xs = [d.x for d in ditches] + ([trench[0]] if trench else [])
    if not xs:
        xs = [0.0]
    span = max(xs) - min(xs)
    pad = max(20.0, 0.2 * span) + max([d.width / 2 for d in ditches], default=0.0)
    x0, x1 = min(xs) - pad, max(xs) + pad

    total = stack.total_thickness
    deepest = max([d.bottom_depth for d in ditches] + [stack.layers[0].thickness])
    has_elements = bool(ditches) or trench is not None
    scale = _DepthScale(total, min(total, 1.6 * deepest + 1.0) if has_elements else total)

    fig = go.Figure()
    n_aq = n_at = 0
    for lay, zt, zb in zip(stack.layers, -stack.z_top, -stack.z_bottom, strict=True):
        if lay.kind == "pakket":
            color, n_aq = AQUIFER_COLORS[n_aq % 4], n_aq + 1
            text = f"<b>{lay.name}</b>  k = {lay.k_h:g} m/d · D = {lay.thickness:g} m"
        else:
            color, n_at = AQUITARD_COLORS[n_at % 4], n_at + 1
            text = f"<b>{lay.name}</b>  c = {lay.c:g} d · D = {lay.thickness:g} m"
        ya, yb = scale(zt), scale(zb)
        fig.add_shape(
            type="rect",
            x0=x0,
            x1=x1,
            y0=yb,
            y1=ya,
            fillcolor=color,
            line={"color": "rgba(0,0,0,0.35)", "width": 1},
            layer="below",
        )
        fig.add_annotation(
            x=x0,
            y=0.5 * (ya + yb),
            text=text,
            showarrow=False,
            xanchor="left",
            xshift=4,
            font={"size": 11, "color": "white" if lay.kind == "scheidend" else "#222"},
            bgcolor="rgba(60,45,30,0.55)" if lay.kind == "scheidend" else "rgba(255,255,255,0.6)",
        )
    fig.add_shape(
        type="line",
        x0=x0,
        x1=x1,
        y0=scale(total),
        y1=scale(total),
        line={"color": "black", "width": 4},
    )
    fig.add_annotation(
        x=x1,
        y=scale(total),
        text="dichte basis",
        showarrow=False,
        xanchor="right",
        yanchor="top",
        font={"size": 10},
    )
    fig.add_shape(type="line", x0=x0, x1=x1, y0=0, y1=0, line={"color": "#3d6b2a", "width": 3})
    fig.add_annotation(
        x=x0,
        y=0,
        text=top_note
        if top_note is not None
        else f"maaiveld · drainage naar polderpeil, w = {sub.w:g} d",
        showarrow=False,
        xanchor="left",
        yanchor="bottom",
        font={"size": 10},
    )
    if scale.zoom < total:
        yb = scale(scale.zoom)
        fig.add_shape(
            type="line",
            x0=x0,
            x1=x1,
            y0=yb,
            y1=yb,
            line={"color": "rgba(0,0,0,0.5)", "width": 1, "dash": "dot"},
        )
        fig.add_annotation(
            x=x1,
            y=yb,
            text=f"↑ vergroot (bovenste {scale.zoom:g} m)",
            showarrow=False,
            xanchor="right",
            yanchor="bottom",
            font={"size": 9, "color": "#333"},
        )

    for d in ditches:
        xl, xr = d.x - d.width / 2, d.x + d.width / 2
        y_bot, y_wat = scale(d.bottom_depth), scale(d.water_level_depth)
        fig.add_shape(type="rect", x0=xl, x1=xr, y0=y_wat, y1=0, fillcolor=DRY, line={"width": 0})
        fig.add_shape(
            type="rect", x0=xl, x1=xr, y0=y_bot, y1=y_wat, fillcolor=WATER, line={"width": 0}
        )
        fig.add_shape(
            type="path",
            path=f"M {xl},0 L {xl},{y_bot} L {xr},{y_bot} L {xr},0",
            line={"color": "navy", "width": 2},
        )
        if d.level:
            y_new = scale(max(d.water_level_depth - d.level, 0.0))
            fig.add_shape(
                type="line",
                x0=xl,
                x1=xr,
                y0=y_new,
                y1=y_new,
                line={"color": "navy", "width": 2, "dash": "dash"},
            )
        level = f" · Δh = {d.level:+.2f} m" if d.level else ""
        fig.add_annotation(
            x=d.x,
            y=0,
            text=f"<b>{d.name}</b>{level}<br>c = {d.entry_resistance:g} d · "
            f"sluit aan op {sub.connection_label(d)}",
            showarrow=True,
            arrowhead=0,
            ay=-38,
            font={"size": 10},
        )

    if trench is not None:
        xt, node, label = trench
        lay_i = sub.nodes[node].layer
        zt, zb = -stack.z_top[lay_i], -stack.z_bottom[lay_i]
        fig.add_shape(
            type="line",
            x0=xt,
            x1=xt,
            y0=scale(zb),
            y1=scale(zt),
            line={"color": TRENCH, "width": 5},
        )
        fig.add_shape(
            type="line",
            x0=xt,
            x1=xt,
            y0=scale(zt),
            y1=0,
            line={"color": TRENCH, "width": 1, "dash": "dot"},
        )
        fig.add_annotation(
            x=xt,
            y=0,
            text=label,
            showarrow=True,
            arrowhead=0,
            ay=-38,
            font={"size": 10, "color": TRENCH},
        )

    tickvals, ticktext = scale.ticks()
    if surface_nap is not None:
        ticktext = [f"{float(t) + surface_nap:g}" for t in ticktext]
    fig.update_layout(
        title=title or None,
        height=HEIGHT,
        margin={"l": 60, "r": 10, "t": 75, "b": 40},
        showlegend=False,
        plot_bgcolor="white",
        xaxis={
            "title": "x [m]" if has_elements else None,
            "range": [x0, x1],
            "showgrid": False,
            "zeroline": False,
            "showticklabels": has_elements,
        },
        yaxis={
            "title": "hoogte [m NAP]" if surface_nap is not None else "hoogte t.o.v. maaiveld [m]",
            "range": [scale(total) - 0.03 * total, 0.06 * total],
            "tickvals": tickvals,
            "ticktext": ticktext,
            "showgrid": False,
            "zeroline": False,
        },
    )
    return fig
