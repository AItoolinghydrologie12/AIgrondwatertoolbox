"""Schematic cross-section for product 3: the layer stack only (same in every cell)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import plotly.graph_objects as go

from gwproducten.products._schematic import draw_cross_section

if TYPE_CHECKING:
    from gwproducten.products.peilopzet_grid import Params


def schematic(params: Params) -> go.Figure:
    from gwproducten.products.peilopzet_grid import subsoil_helper

    note = (
        f"maaiveld {params.surface:g} m NAP · neerslagoverschot {params.recharge * 1000:g} mm/d"
        f" · maaivelddrainage w = {params.mv_resistance:g} d"
        f" · buisdrainage (per cel) {params.drain_depth:g} m-mv, w = {params.drain_resistance:g} d"
    )
    fig = draw_cross_section(
        subsoil_helper(params),
        (),
        title="Lagenopbouw (in elke cel gelijk)",
        top_note=note,
        surface_nap=params.surface,
    )
    if params.seepage:
        fig.add_annotation(
            xref="paper", yref="paper", x=0.5, y=-0.02, yanchor="top", showarrow=False,
            text=(
                f"▲ kwel-onderrand: diep pakket φ = {params.seepage_head:g} m NAP, "
                f"weerstand c = {params.seepage_resistance:g} d"
            ),
            font={"size": 13, "color": "#0b5394"}, bgcolor="rgba(255,255,255,0.8)",
        )  # fmt: skip
    return fig
