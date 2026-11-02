"""GIS export of a Grondwatertoolbox result: contour lines of the rise (and drawdown) per layer
and the drawn objects, in RD (EPSG:28992), as a GeoPackage for QGIS."""

from __future__ import annotations

import math
from collections.abc import Sequence

import contourpy
import numpy as np

from gwproducten.geo.gpkg import Layer, Point, write_gpkg
from gwproducten.products import peilopzet_grid as p3

MAX_LEVELS = 40  # contour levels per sign; beyond this the step grows (multiples of the limit)


def contour_levels(z: np.ndarray, step: float) -> list[float]:
    """Multiples of ``step`` (the 5 cm limit) inside the range of ``z``, without 0; with a
    large range the step becomes a multiple of ``step`` so that each sign has at most
    MAX_LEVELS levels."""
    lo, hi = float(np.nanmin(z)), float(np.nanmax(z))
    n = math.ceil(max(abs(lo), abs(hi)) / step)
    step *= max(1, math.ceil(n / MAX_LEVELS))
    k = range(1, math.ceil(max(abs(lo), abs(hi)) / step) + 1)
    levels = [-i * step for i in reversed(k)] + [i * step for i in k]
    return [round(v, 6) for v in levels if lo < v < hi]


def _cell_ring(params: p3.Params, r: int, c: int) -> list[Point]:
    x0, y0, d = params.origin_x + c * params.cell, params.origin_y + r * params.cell, params.cell
    return [(x0, y0), (x0 + d, y0), (x0 + d, y0 + d), (x0, y0 + d), (x0, y0)]


def _face_line(params: p3.Params, r: int, c: int, axis: str) -> list[Point]:
    """East face ("x") or north face ("y") of cell (r, c)."""
    d = params.cell
    x, y = params.origin_x + (c + 1) * d, params.origin_y + (r + 1) * d
    return [(x, y - d), (x, y)] if axis == "x" else [(x - d, y), (x, y)]


def gis_layers(
    params: p3.Params, res: p3.Result, wc_lines: dict[str, Sequence[Sequence[Point]]]
) -> list[Layer]:
    """The export layers; ``wc_lines``: drawn watercourse lines per name in model metres."""
    ox, oy = params.origin_x, params.origin_y
    w, h = params.size_x, params.size_y
    area = Layer("modelgebied", "POLYGON", {"celgrootte_m": float, "rekenkern": str})
    area.add([(ox, oy), (ox + w, oy), (ox + w, oy + h), (ox, oy + h)],
             celgrootte_m=params.cell, rekenkern=res.engine_note or res.engine)  # fmt: skip

    contours = Layer(
        "contouren_verhoging", "LINESTRING",
        {"laag": str, "niveau_m": float, "soort": str, "grens": int},
        description="Verhoging opzet - referentie [m]; negatief = verlaging. grens = 1: de "
        f"{params.threshold * 100:.0f} cm-grens.",
    )  # fmt: skip
    for j, label in enumerate(res.node_labels.values()):
        z = res.rise[j]
        gen = contourpy.contour_generator(res.x + ox, res.y + oy, z, line_type="Separate")
        for level in contour_levels(z, params.threshold):
            for line in gen.lines(level):
                if len(line) < 2:
                    continue
                contours.add(
                    [(float(a), float(b)) for a, b in line], laag=label, niveau_m=level,
                    soort="verhoging" if level > 0 else "verlaging",
                    grens=int(math.isclose(abs(level), params.threshold)),
                )  # fmt: skip

    wc_fields = {
        "naam": str, "type": str, "peil_ref": float, "peil_opzet": float, "bodem": float,
        "breedte": float, "intreeweerstand": float,
    }  # fmt: skip
    lines = Layer("watergangen", "LINESTRING", wc_fields, description="Getekende lijnen")
    cells = Layer(
        "watergang_cellen", "POLYGON", {**wc_fields, "lengte_in_cel_m": float},
        description="Rekencellen van de watergangen met de lengte per cel",
    )  # fmt: skip
    for wc in params.watercourses:
        attrs = {
            "naam": wc.name, "type": wc.kind, "peil_ref": wc.stage_ref,
            "peil_opzet": wc.stage_new, "bodem": wc.bottom, "breedte": wc.width,
            "intreeweerstand": wc.entry_resistance,
        }  # fmt: skip
        for line in wc_lines.get(wc.name, []):
            if len(line) >= 2:
                lines.add([(ox + x, oy + y) for x, y in line], **attrs)
        for (r, c), length in zip(wc.cells, wc.cell_lengths(), strict=True):
            cells.add(_cell_ring(params, r, c), **attrs, lengte_in_cel_m=length)

    wells = Layer(
        "putten", "POINT",
        {"naam": str, "laag": str, "q_ref_m3d": float, "q_opzet_m3d": float},
        description="Debiet per put; positief = onttrekking",
    )  # fmt: skip
    for g in params.wells:
        for r, c in g.cells:
            wells.add(
                (ox + (c + 0.5) * params.cell, oy + (r + 0.5) * params.cell),
                naam=g.name, laag=res.node_labels.get(g.layer, g.layer), q_ref_m3d=g.q_ref,
                q_opzet_m3d=g.q_new,
            )  # fmt: skip

    heads = Layer(
        "ghb_chd", "POLYGON",
        {"naam": str, "soort": str, "laag": str, "h_ref": float, "h_opzet": float,
         "weerstand_d": float},
    )  # fmt: skip
    for kind, groups in (("GHB", params.ghb), ("CHD", params.chd)):
        for g in groups:
            for r, c in g.cells:
                heads.add(
                    _cell_ring(params, r, c), naam=g.name, soort=kind,
                    laag=res.node_labels.get(g.layer, g.layer), h_ref=g.head_ref,
                    h_opzet=g.head_new, weerstand_d=getattr(g, "resistance", None),
                )  # fmt: skip

    walls = Layer(
        "damwanden", "LINESTRING",
        {"naam": str, "tot_en_met_laag": str, "weerstand_d": float, "in_referentie": int},
        description="Celranden met een damwand; weerstand leeg = ondoorlatend",
    )  # fmt: skip
    for wall in params.walls:
        for r, c, axis in wall.faces:
            walls.add(
                _face_line(params, r, c, axis), naam=wall.name,
                tot_en_met_laag=res.node_labels.get(wall.layer, wall.layer),
                weerstand_d=wall.resistance, in_referentie=int(wall.reference),
            )  # fmt: skip
    return [area, contours, lines, cells, wells, heads, walls]


def to_gpkg(
    params: p3.Params, res: p3.Result, wc_lines: dict[str, Sequence[Sequence[Point]]]
) -> bytes:
    if params.origin_x == 0.0 and params.origin_y == 0.0:
        raise ValueError("GIS-export kan alleen met een locatie in RD (stap 1).")
    return write_gpkg(gis_layers(params, res, wc_lines))
