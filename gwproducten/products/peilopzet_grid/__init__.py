"""Product 3 — Raised level on a plan-view grid (numerical, steady)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from gwproducten.core.layers import TOP_MODE_LABEL, LayerStack, build_layers
from gwproducten.core.params import ParamError, param, range_errors
from gwproducten.numeric.grid2d import (
    MIN_SATURATED_FRACTION,
    Convertible,
    Factorization,
    FluxBoundary,
    HeadBoundary,
    grid_resistance,
    solve_grid,
)
from gwproducten.products._layered import Ditch, Subsoil
from gwproducten.products.peilopzet_grid.schematic import schematic

META = {
    "slug": "peilopzet_grid",
    "titel": "Grondwatertoolbox",
    "categorie": "Peilbeheer",
    "regime": "stationair",
    "methoden_kort": ["numeriek rooster (bovenaanzicht)"],
    "methoden": [
        "numeriek: eindige volumes in bovenaanzicht, quasi-3D lagen, RIV/DRN-randen (Picard)",
        "putten (WEL), algemene stijghoogteranden (GHB) en kwel via de onderrand",
        "zones met eigen lagenopbouw en maaiveld (harmonisch gemiddelde tussen cellen)",
    ],
}

THEORY = (Path(__file__).parent / "theory.md").read_text(encoding="utf-8")

MAX_AQUIFERS = 4
MAX_CELLS_PER_SIDE = 100
MAX_ZONES = 5
BOUNDARIES = ("vast", "doorlopend", "gesloten", "per_laag")
RING_KINDS = ("vast", "doorlopend", "gesloten")  # per layer with "per_laag"
RING_GHB = "rand (doorlopend)"
RESERVED_NAMES = (
    "neerslag", "buisdrainage", "maaiveld", "rand", RING_GHB, "kwel onderrand", "basis",
)  # fmt: skip
WARN_PHREATIC_DRAWDOWN = 0.25  # fraction of the phreatic layer thickness
KINDS = ("riv", "drn")
KIND_LABEL = {"riv": "RIV (in- en uitstroom)", "drn": "drain (alleen afvoer)"}

Cell = tuple[int, int]  # (row, col), row 0 at the south edge


@dataclass(frozen=True)
class Watercourse:
    name: str = "Watergang"
    cells: tuple[Cell, ...] = ()
    kind: str = "riv"
    stage_ref: float = param(3.8, "m NAP", "Peil referentie", -50.0, 50.0)
    stage_new: float = param(3.8, "m NAP", "Peil na opzet", -50.0, 50.0)
    bottom: float = param(3.4, "m NAP", "Bodemhoogte", -100.0, 50.0)
    width: float = param(1.0, "m", "Bodembreedte", 0.1, 100.0)
    length: float = param(25.0, "m", "Lengte per cel", 0.1, 500.0)
    entry_resistance: float = param(1.0, "d", "Intreeweerstand", 0.0, 1e4)
    conductance: float = param(0.0, "m²/d", "Conductance per cel (0 = berekenen)", 0.0, 1e8)
    lengths: tuple[float, ...] | None = None  # length per cell [m]; None = ``length`` everywhere

    def cell_lengths(self) -> tuple[float, ...]:
        return self.lengths if self.lengths is not None else (self.length,) * len(self.cells)


@dataclass(frozen=True)
class WellGroup:
    """Wells in a set of cells of one computational layer (node key)."""

    name: str = "Putten"
    cells: tuple[Cell, ...] = ()
    layer: str = "freatisch"
    q_ref: float = param(0.0, "m³/d", "Debiet per put referentie (+ = onttrekking)", -1e5, 1e5)
    q_new: float = param(0.0, "m³/d", "Debiet per put na opzet (+ = onttrekking)", -1e5, 1e5)


@dataclass(frozen=True)
class GhbGroup:
    """General head boundary on a set of cells of one layer: q = A/c (h_ext - h)."""

    name: str = "GHB"
    cells: tuple[Cell, ...] = ()
    layer: str = "freatisch"
    head_ref: float = param(3.8, "m NAP", "Stijghoogte referentie", -50.0, 50.0)
    head_new: float = param(3.8, "m NAP", "Stijghoogte na opzet", -50.0, 50.0)
    resistance: float = param(1.0, "d", "Weerstand c", 0.01, 1e6)


def column_cells(col: int, rows: range | list[int]) -> tuple[Cell, ...]:
    return tuple((r, col) for r in rows)


DEFAULT_STACK = LayerStack(
    "freatisch_zand",
    build_layers("freatisch_zand", [(5.0, 1.0), (20.0, 15.0)], [(2.0, 500.0, 0.004)]),
)
DEFAULT_SURFACE = 5.0
# Default example: two north-south watercourses over the full height, at 1/3 (raised) and
# 2/3 of the model width; they scale with the model size.
DEFAULT_WATERCOURSE_SPECS = (
    (1.0 / 3.0, Watercourse(name="Wetering", stage_ref=3.8, stage_new=4.0, bottom=2.6, width=5.0)),
    (2.0 / 3.0, Watercourse(name="Sloot", stage_ref=3.8, stage_new=3.8, bottom=3.4, width=1.0)),
)


def default_watercourses(size_x: float, size_y: float, cell: float) -> tuple[Watercourse, ...]:
    """The default example on a grid: full columns at 1/3 and 2/3 of the width."""
    nrow, ncol = int(round(size_y / cell)), int(round(size_x / cell))
    return tuple(
        dataclasses.replace(
            wc, cells=column_cells(min(int(frac * ncol), ncol - 1), range(nrow)), length=cell
        )
        for frac, wc in DEFAULT_WATERCOURSE_SPECS
    )


DEFAULT_WATERCOURSES = default_watercourses(1000.0, 1000.0, 25.0)


@dataclass(frozen=True)
class Zone:
    """Cells with their own layer values and surface; same structure as the base stack.
    Layer thicknesses count from the zone's own surface."""

    name: str = "Zone"
    cells: tuple[Cell, ...] = ()
    stack: LayerStack = field(default=DEFAULT_STACK)
    surface: float = param(DEFAULT_SURFACE, "m NAP", "Maaiveldhoogte", -10.0, 50.0)


@dataclass(frozen=True)
class Params:
    stack: LayerStack = field(default=DEFAULT_STACK)
    surface: float = param(DEFAULT_SURFACE, "m NAP", "Maaiveldhoogte", -10.0, 50.0)
    recharge: float = param(0.0008, "m/d", "Neerslagoverschot", -0.01, 0.02)
    size_x: float = param(1000.0, "m", "Breedte model (x)", 50.0, 5000.0)
    size_y: float = param(1000.0, "m", "Hoogte model (y)", 50.0, 5000.0)
    cell: float = param(25.0, "m", "Celgrootte", 10.0, 50.0)
    boundary: str = "vast"
    boundary_head: float = param(3.8, "m NAP", "Stijghoogte vaste rand", -50.0, 50.0)
    boundary_layers: tuple[float | None, ...] = ()  # per node with "per_laag"; None = closed
    # per node with "per_laag", one of RING_KINDS; empty: from boundary_layers (None = closed)
    boundary_kinds: tuple[str, ...] = ()
    mv_resistance: float = param(5.0, "d", "Weerstand maaivelddrainage", 0.1, 1e4)
    drain_cells: tuple[Cell, ...] = ()
    drain_depth: float = param(1.0, "m", "Diepte buisdrainage onder maaiveld", 0.1, 5.0)
    drain_resistance: float = param(50.0, "d", "Drainageweerstand buisdrainage", 1.0, 1e4)
    watercourses: tuple[Watercourse, ...] = field(default=DEFAULT_WATERCOURSES)
    wells: tuple[WellGroup, ...] = ()
    ghb: tuple[GhbGroup, ...] = ()
    zones: tuple[Zone, ...] = ()
    seepage: bool = False
    seepage_head: float = param(4.8, "m NAP", "Stijghoogte diep pakket φ", -50.0, 50.0)
    seepage_resistance: float = param(1000.0, "d", "Weerstand c naar diep pakket", 1.0, 1e6)
    threshold: float = param(0.05, "m", "Grens verhoging", 0.001, 1.0)
    grid_correction: bool = True
    convertible: bool = True  # T follows the saturated thickness (MODFLOW 6 convertible)

    @property
    def nrow(self) -> int:
        return int(round(self.size_y / self.cell))

    @property
    def ncol(self) -> int:
        return int(round(self.size_x / self.cell))

    def validate(self) -> None:
        errors = range_errors(self) + self.stack.errors()
        if sum(lay.kind == "pakket" for lay in self.stack.layers) > MAX_AQUIFERS:
            errors.append(
                f"Dit product ondersteunt maximaal {MAX_AQUIFERS} watervoerende pakketten."
            )
        if self.boundary not in BOUNDARIES:
            errors.append(
                f"Modelrand moet een van {', '.join(BOUNDARIES)} zijn (nu '{self.boundary}')."
            )
        n_nodes = len(self.stack.nodes) if not self.stack.errors() else 0
        if self.boundary == "per_laag":
            if len(self.boundary_layers) != n_nodes or len(self.boundary_kinds) not in (
                0, n_nodes,
            ):  # fmt: skip
                errors.append(
                    f"Modelrand per laag: geef voor elk van de {n_nodes} rekenlagen een "
                    f"randtype en stijghoogte op (nu {len(self.boundary_layers)})."
                )
            elif any(k not in RING_KINDS for k in self.boundary_kinds):
                errors.append(f"Modelrand per laag: kies uit {', '.join(RING_KINDS)}.")
            elif any(kind != "gesloten" and head is None for kind, head in ring_layers(self)):
                errors.append("Modelrand per laag: een vaste of doorlopende rand heeft een peil.")
        for size, name in ((self.size_x, "Breedte"), (self.size_y, "Hoogte")):
            if abs(size / self.cell - round(size / self.cell)) > 1e-9:
                errors.append(
                    f"{name} van het model ({size:g} m) moet deelbaar zijn door de celgrootte "
                    f"({self.cell:g} m)."
                )
            elif round(size / self.cell) > MAX_CELLS_PER_SIDE:
                errors.append(
                    f"{name} van het model ({size:g} m) is te groot: maximaal "
                    f"{MAX_CELLS_PER_SIDE} cellen per zijde, bij {self.cell:g} m dus hooguit "
                    f"{MAX_CELLS_PER_SIDE * self.cell:g} m."
                )
        if errors:
            raise ParamError(errors)
        names = [g.name for g in (*self.watercourses, *self.wells, *self.ghb, *self.zones)]
        if len(set(names)) != len(names):
            errors.append(
                "Elke watergang, putgroep, GHB-groep en zone moet een unieke naam hebben."
            )
        if any(n in RESERVED_NAMES for n in names):
            errors.append(f"Deze namen zijn gereserveerd: {', '.join(RESERVED_NAMES)}.")
        errors += self._zone_errors()
        if errors:
            raise ParamError(errors)
        node_keys = [n.key for n in self.stack.nodes]
        for g in (*self.wells, *self.ghb):
            prefix = f"{g.name}: "
            errors += range_errors(g, prefix)
            if g.layer not in node_keys:
                errors.append(
                    f"{prefix}laag '{g.layer}' bestaat niet (kies uit {', '.join(node_keys)})."
                )
            if not g.cells:
                errors.append(f"{prefix}er zijn geen cellen toegewezen.")
            if any(not (0 <= r < self.nrow and 0 <= c < self.ncol) for r, c in g.cells):
                errors.append(f"{prefix}een of meer cellen liggen buiten het rooster.")
        cols = columns(self)
        zmap = zone_map(self)
        for wc in self.watercourses:
            prefix = f"{wc.name}: "
            errors += range_errors(wc, prefix)
            if wc.kind not in KINDS:
                errors.append(f"{prefix}type moet 'riv' of 'drn' zijn.")
            if not wc.cells:
                errors.append(f"{prefix}er zijn geen cellen toegewezen.")
            if any(not (0 <= r < self.nrow and 0 <= c < self.ncol) for r, c in wc.cells):
                errors.append(f"{prefix}een of meer cellen liggen buiten het rooster.")
            if wc.lengths is not None and len(wc.lengths) != len(wc.cells):
                errors.append(f"{prefix}het aantal lengtes past niet bij het aantal cellen.")
            elif any(not 0.0 < L <= 5.0 * self.cell for L in wc.cell_lengths()):
                errors.append(
                    f"{prefix}lengte per cel moet groter dan 0 en hooguit {5 * self.cell:g} m zijn."
                )
            if min(wc.stage_ref, wc.stage_new) <= wc.bottom:
                errors.append(f"{prefix}de bodem ({wc.bottom:g} m NAP) ligt boven het peil.")
                continue
            inside = [(r, c) for r, c in wc.cells if 0 <= r < self.nrow and 0 <= c < self.ncol]
            for z in sorted({int(zmap[r, c]) for r, c in inside}):
                stack_z, surface_z = cols[z]
                where = "" if z == 0 else f" (zone {self.zones[z - 1].name})"
                if wc.bottom >= surface_z:
                    errors.append(f"{prefix}de bodem ligt boven maaiveld{where}.")
                else:
                    errors += [
                        e + where
                        for e in Subsoil(stack=stack_z).ditch_errors(as_ditch(self, wc, surface_z))
                        if "Positie" not in e
                    ]
        if any(not (0 <= r < self.nrow and 0 <= c < self.ncol) for r, c in self.drain_cells):
            errors.append("Buisdrainage: een of meer cellen liggen buiten het rooster.")
        if errors:
            raise ParamError(errors)

    def _zone_errors(self) -> list[str]:
        errors: list[str] = []
        if len(self.zones) > MAX_ZONES:
            errors.append(f"Maximaal {MAX_ZONES} zones (nu {len(self.zones)}).")
        base = self.stack
        for z in self.zones:
            prefix = f"Zone {z.name}: "
            errors += range_errors(z, prefix)
            errors += [prefix + e for e in z.stack.errors()]
            if z.stack.top_mode != base.top_mode:
                errors.append(
                    f"{prefix}de schematisering van de bovenkant moet gelijk zijn aan de basis "
                    f"({TOP_MODE_LABEL[base.top_mode]})."
                )
            elif len(z.stack.layers) != len(base.layers):
                errors.append(
                    f"{prefix}het aantal lagen ({len(z.stack.layers)}) moet gelijk zijn aan dat "
                    f"van de basis ({len(base.layers)})."
                )
            if not z.cells:
                errors.append(f"{prefix}er zijn geen cellen toegewezen.")
            if any(not (0 <= r < self.nrow and 0 <= c < self.ncol) for r, c in z.cells):
                errors.append(f"{prefix}een of meer cellen liggen buiten het rooster.")
        return errors


def subsoil_helper(params: Params) -> Subsoil:
    """Layer stack wrapper that provides nodes and watercourse conductances."""
    return Subsoil(stack=params.stack)


def columns(params: Params) -> list[tuple[LayerStack, float]]:
    """(stack, surface) per zone index: 0 = base, k = ``params.zones[k - 1]``."""
    return [(params.stack, params.surface)] + [(z.stack, z.surface) for z in params.zones]


def zone_map(params: Params) -> np.ndarray:
    """Zone index per cell (0 = base); on overlap the later zone wins."""
    zmap = np.zeros((params.nrow, params.ncol), dtype=int)
    for k, z in enumerate(params.zones, start=1):
        for r, c in z.cells:
            zmap[r, c] = k
    return zmap


def surface_map(params: Params) -> np.ndarray:
    """Surface level per cell [m NAP]."""
    return np.array([s for _, s in columns(params)])[zone_map(params)]


def _node_fields(params: Params) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Transmissivity per node and resistance between nodes, per cell."""
    zmap = zone_map(params)
    stacks = [s for s, _ in columns(params)]
    nl = len(params.stack.nodes)
    T = [np.array([s.nodes[j].T for s in stacks])[zmap] for j in range(nl)]
    c = [np.array([s.node_resistances()[j] for s in stacks])[zmap] for j in range(nl - 1)]
    return T, c


def layer_geometry(params: Params) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """(k_h, top, bottom) per cell [m/d, m NAP] for the layer of every node."""
    zmap = zone_map(params)
    out = []
    for node in params.stack.nodes:
        k, top, bottom = (
            np.array(v)[zmap]
            for v in zip(
                *(
                    (s.layers[node.layer].k_h, z + s.z_top[node.layer], z + s.z_bottom[node.layer])
                    for s, z in columns(params)
                ),
                strict=True,
            )
        )
        out.append((k, top, bottom))
    return out


def _convertible(params: Params) -> list[Convertible | None]:
    """Convertible aquifer nodes (not a clay top layer); empty when switched off."""
    if not params.convertible:
        return []
    clay_top = params.stack.top_mode == "deklaag"
    return [
        None if (j == 0 and clay_top) else Convertible(k, top, bottom)
        for j, (k, top, bottom) in enumerate(layer_geometry(params))
    ]


def as_ditch(params: Params, wc: Watercourse, surface: float | None = None) -> Ditch:
    """Cross-section of a watercourse (reference water depth) for its conductance;
    ``surface``: local surface level (default: the base surface)."""
    return Ditch(
        name=wc.name,
        width=wc.width,
        bottom_depth=(params.surface if surface is None else surface) - wc.bottom,
        water_depth=wc.stage_ref - wc.bottom,
        entry_resistance=wc.entry_resistance,
    )


@dataclass(frozen=True)
class Result:
    x: np.ndarray  # cell centres [m]
    y: np.ndarray
    heads_ref: np.ndarray  # (n_nodes, nrow, ncol) [m NAP]
    heads_new: np.ndarray
    rise: np.ndarray  # heads_new - heads_ref [m]
    budget: pd.DataFrame  # [m3/d], positive = into the groundwater
    mass_balance: dict[str, float]
    iterations: dict[str, int]
    inundated: dict[str, int]  # cells with the surface drain active
    conductances: pd.DataFrame
    node_labels: dict[str, str]
    warnings: list[str]
    zone_map: np.ndarray  # (nrow, ncol) zone index, 0 = base
    surface: np.ndarray  # (nrow, ncol) surface level [m NAP]
    zone_names: list[str]  # per zone index, "basis" first
    well_drawdown: np.ndarray | None = None  # (n_nodes, nrow, ncol) by the wells (opzet) [m]


def _cell_conductances(
    params: Params, sub: Subsoil
) -> list[list[tuple[int, np.ndarray, np.ndarray]]]:
    """Per watercourse: (node, positions in ``wc.cells``, conductance per position) for every
    touched node. Each cell uses the layers and surface of its own zone, so the connection
    can differ along a watercourse."""
    cols = columns(params)
    zmap = zone_map(params)
    drained = np.zeros((params.nrow, params.ncol), dtype=bool)
    for r, c in params.drain_cells:
        drained[r, c] = True
    out = []
    for wc in params.watercourses:
        lengths = wc.cell_lengths()
        zone_of = np.array([zmap[r, c] for r, c in wc.cells], dtype=int)
        acc: dict[int, list[tuple[int, float]]] = {}
        for z in np.unique(zone_of):
            stack_z, surface_z = cols[z]
            sub_z = Subsoil(stack=stack_z)
            nodes = sub_z.nodes
            res_between = stack_z.node_resistances()
            parts = sub_z.conductance_parts(as_ditch(params, wc, surface_z))
            total = sum(C for _, C in parts)
            for node, C in parts:
                leak = 0.0
                if node > 0:
                    leak += 1.0 / res_between[node - 1]
                if node < len(nodes) - 1:
                    leak += 1.0 / res_between[node]
                for k in np.flatnonzero(zone_of == z):
                    (r, c), L = wc.cells[k], lengths[k]
                    lk = leak + (
                        1.0 / params.drain_resistance if node == 0 and drained[r, c] else 0.0
                    )
                    c_eff = 1.0 / lk if lk > 0 else np.inf
                    R = (
                        grid_resistance(nodes[node].T, c_eff, params.cell)
                        if params.grid_correction
                        else 0.0
                    )
                    if wc.conductance > 0:  # user value for a full cell, split like C'
                        value = wc.conductance * (L / params.cell) * C / total
                    else:
                        value = L / (1.0 / C + R)
                    acc.setdefault(node, []).append((int(k), value))
        per_node = []
        for node, entries in sorted(acc.items()):
            entries.sort()
            per_node.append(
                (node, np.array([k for k, _ in entries]), np.array([v for _, v in entries]))
            )
        out.append(per_node)
    return out


def _boundaries(params: Params, sub: Subsoil, stage: str, cond) -> list[HeadBoundary]:
    nl, nr, nc = len(sub.nodes), params.nrow, params.ncol
    idx = np.arange(nl * nr * nc).reshape(nl, nr, nc)
    out = []
    for wc, per_node in zip(params.watercourses, cond, strict=True):
        rc = np.array(wc.cells, dtype=int).reshape(-1, 2)
        keys_a = np.concatenate([idx[node, rc[pos, 0], rc[pos, 1]] for node, pos, _ in per_node])
        C_a = np.concatenate([C for _, _, C in per_node])
        level = wc.stage_ref if stage == "ref" else wc.stage_new
        out.append(
            HeadBoundary(
                wc.name, wc.kind, keys_a, C_a, np.full(keys_a.size, level),
                np.full(keys_a.size, wc.bottom),
            )
        )  # fmt: skip
    area = params.cell**2
    surface = surface_map(params)
    if params.drain_cells:
        keys_d = np.array([idx[0, r, c] for r, c in params.drain_cells])
        lvl = np.array([surface[r, c] for r, c in params.drain_cells]) - params.drain_depth
        out.append(
            HeadBoundary(
                "buisdrainage", "drn", keys_d, np.full(keys_d.size, area / params.drain_resistance),
                lvl, lvl,
            )
        )  # fmt: skip
    for g in params.ghb:
        j = [n.key for n in sub.nodes].index(g.layer)
        keys_g = np.array([idx[j, r, c] for r, c in g.cells])
        head = g.head_ref if stage == "ref" else g.head_new
        out.append(
            HeadBoundary(
                g.name, "ghb", keys_g, np.full(keys_g.size, area / g.resistance),
                np.full(keys_g.size, head), np.full(keys_g.size, head),
            )
        )  # fmt: skip
    if params.seepage:
        keys_k = idx[nl - 1].ravel()
        out.append(
            HeadBoundary(
                "kwel onderrand", "ghb", keys_k,
                np.full(keys_k.size, area / params.seepage_resistance),
                np.full(keys_k.size, params.seepage_head), np.full(keys_k.size, 0.0),
            )
        )  # fmt: skip
    keys_mv = idx[0].ravel()
    out.append(
        HeadBoundary(
            "maaiveld", "drn", keys_mv, np.full(keys_mv.size, area / params.mv_resistance),
            surface.ravel(), surface.ravel(),
        )
    )  # fmt: skip
    return out


def _wells(params: Params, sub: Subsoil, stage: str) -> list[FluxBoundary]:
    nl, nr, nc = len(sub.nodes), params.nrow, params.ncol
    idx = np.arange(nl * nr * nc).reshape(nl, nr, nc)
    keys = [n.key for n in sub.nodes]
    out = []
    for g in params.wells:
        j = keys.index(g.layer)
        k = np.array([idx[j, r, c] for r, c in g.cells])
        q = g.q_ref if stage == "ref" else g.q_new
        out.append(FluxBoundary(g.name, k, np.full(k.size, -q)))  # extraction positive
    return out


def ring_layers(params: Params) -> list[tuple[str, float | None]]:
    """(kind, head) of the model boundary per node; kind in RING_KINDS."""
    n = len(params.stack.nodes)
    if params.boundary == "per_laag":
        heads = list(params.boundary_layers)
        kinds = list(params.boundary_kinds) or ["gesloten" if h is None else "vast" for h in heads]
        return list(zip(kinds, heads, strict=True))
    if params.boundary == "gesloten":
        return [("gesloten", None)] * n
    return [(params.boundary, params.boundary_head)] * n


def _fixed_head(params: Params) -> tuple[float | None, ...]:
    return tuple(h if kind == "vast" else None for kind, h in ring_layers(params))


def _ring_ghb(params: Params, T: list[np.ndarray], c: list[np.ndarray]) -> HeadBoundary | None:
    """GHB on the outer ring for layers with a continuing boundary: per outer cell face
    C = T dy / (dx/2 + lambda), lambda = sqrt(T c_eff) of a semi-infinite leaky aquifer;
    1/c_eff = sum of the conductances to the adjacent nodes (and the seepage boundary)."""
    nl, nr, nc = len(T), params.nrow, params.ncol
    idx = np.arange(nl * nr * nc).reshape(nl, nr, nc)
    rr, cc = np.indices((nr, nc))
    faces = (
        (rr == 0).astype(int) + (rr == nr - 1) + (cc == 0) + (cc == nc - 1)
    )  # outer faces per cell (corners 2)
    ring = faces > 0
    keys, cond, stage = [], [], []
    for j, (kind, head) in enumerate(ring_layers(params)):
        if kind != "doorlopend":
            continue
        leak = np.zeros((nr, nc))
        if j > 0:
            leak += 1.0 / np.broadcast_to(c[j - 1], (nr, nc))
        if j < nl - 1:
            leak += 1.0 / np.broadcast_to(c[j], (nr, nc))
        if j == nl - 1 and params.seepage:
            leak += 1.0 / params.seepage_resistance
        t = np.broadcast_to(T[j], (nr, nc))
        with np.errstate(divide="ignore"):
            lam = np.sqrt(t / leak)  # infinite without leakage: no outflow (closed)
        C = faces * t * params.cell / (0.5 * params.cell + lam)
        keys.append(idx[j][ring])
        cond.append(C[ring])
        stage.append(np.full(int(ring.sum()), head))
    if not keys:
        return None
    s = np.concatenate(stage)
    return HeadBoundary(RING_GHB, "ghb", np.concatenate(keys), np.concatenate(cond), s, s)


def compute(params: Params) -> Result:
    params.validate()
    sub = subsoil_helper(params)
    nodes = sub.nodes
    T, c = _node_fields(params)
    cols = columns(params)
    zmap = zone_map(params)
    cond = _cell_conductances(params, sub)
    fixed = _fixed_head(params)
    ring_ghb = _ring_ghb(params, T, c)
    convertible = _convertible(params)

    # Both runs share the grid matrix; the opzet run starts from the reference states.
    sols = {}
    factorization = Factorization()

    def solve_run(run: str, with_wells: bool = True, start=None):
        """``start``: earlier run whose states (and transmissivities) are the first guess."""
        return solve_grid(
            T if start is None else list(start.transmissivity),
            c, params.nrow, params.ncol, params.cell, params.recharge,
            _boundaries(params, sub, run, cond) + ([ring_ghb] if ring_ghb else []),
            fixed_head=fixed,
            fluxes=_wells(params, sub, run) if with_wells else (),
            initial_active=None if start is None else start.active,
            factorization=factorization, convertible=convertible,
        )  # fmt: skip

    sols["ref"] = solve_run("ref")
    sols["new"] = solve_run("new", start=sols["ref"])
    ref, new = sols["ref"], sols["new"]

    rows = [
        "neerslag",
        *[wc.name for wc in params.watercourses],
        *[g.name for g in params.wells],
        *[g.name for g in params.ghb],
    ]
    if params.seepage:
        rows.append("kwel onderrand")
    if params.drain_cells:
        rows.append("buisdrainage")
    rows += ["maaiveld", *([RING_GHB] if ring_ghb else []), "rand"]

    def column(s) -> list[float]:
        vals = [s.recharge] + [s.fluxes[name] for name in rows[1:-1]] + [s.lateral]
        return vals

    budget = pd.DataFrame(
        {"referentie [m³/d]": column(ref), "opzet [m³/d]": column(new)}, index=rows
    )
    budget.loc["som (balansfout)"] = budget.sum()

    zone_names = ["basis", *[z.name for z in params.zones]]

    def connection(wc: Watercourse) -> str:
        """Connection per zone the watercourse runs through."""
        zones = sorted({int(zmap[r, c]) for r, c in wc.cells})
        labels = [
            Subsoil(stack=cols[z][0]).connection_label(as_ditch(params, wc, cols[z][1]))
            for z in zones
        ]
        if len(zones) == 1:
            return labels[0]
        return "; ".join(f"{zone_names[z]}: {lab}" for z, lab in zip(zones, labels, strict=True))

    conductances = pd.DataFrame(
        [
            {
                "Watergang": wc.name,
                "Type": KIND_LABEL[wc.kind],
                "Cellen": len(wc.cells),
                "Sluit aan op": connection(wc),
                **{
                    f"C per cel {nodes[node].label} [m²/d]": float(np.mean(cells_C))
                    for node, _, cells_C in per_node
                },
            }
            for wc, per_node in zip(params.watercourses, cond, strict=True)
        ]
    )

    warnings = []
    lam_min = np.inf
    if len(nodes) > 1:
        lam_min = min(
            float(Subsoil(stack=cols[z][0]).layer_system().leakage_factors[-1])
            for z in np.unique(zmap)
        )
    if lam_min < params.cell:
        warnings.append(
            f"De kleinste spreidingslengte ({lam_min:.0f} m) is kleiner dan de celgrootte "
            f"({params.cell:g} m): het verloop rond de watergangen is grof. De "
            "celgrootte-correctie houdt de uitwisseling goed; verklein de cel voor meer detail."
        )
    for wc in params.watercourses:  # bottom in an aquitard: approximated connection
        for z in sorted({int(zmap[r, c]) for r, c in wc.cells}):
            stack_z, surface_z = cols[z]
            lay = stack_z.layers[stack_z.layer_at_depth(surface_z - wc.bottom + 1e-9)]
            if lay.kind == "scheidend":
                where = "" if z == 0 else f" (zone {zone_names[z]})"
                warnings.append(
                    f"{wc.name}: de bodem ligt in {lay.name}{where}. De uitwisseling is "
                    "benaderd (bodem via de rest van die laag naar het pakket eronder); zie "
                    "Theorie voor de afwijking t.o.v. een x-z doorsnede."
                )
                break
    for name, s in sols.items():
        if not s.converged:
            warnings.append(f"Iteratie RIV/DRN niet geconvergeerd ({name}); resultaat onzeker.")
    inundated = {
        "referentie": int(ref.active["maaiveld"].sum()),
        "opzet": int(new.active["maaiveld"].sum()),
    }
    if inundated["opzet"] > inundated["referentie"]:
        warnings.append(
            f"Na opzet komt het grondwater in {inundated['opzet'] - inundated['referentie']} "
            "cellen extra boven maaiveld (maaivelddrainage actief)."
        )

    geometry = layer_geometry(params)

    def shown(sol) -> np.ndarray:
        """Heads for display: a dry cell (minimum saturated thickness) at its layer bottom,
        as MODFLOW 6 marks it dry; its computed head lies below the bottom."""
        h = sol.heads.copy()
        for j, cv in enumerate(convertible):
            if cv is not None:
                h[j] = np.where(sol.dry[j], geometry[j][2], h[j])
        return h

    heads_ref, heads_new = shown(ref), shown(new)
    well_drawdown = None
    if new.flux_response is not None and any(cv is not None for cv in convertible):
        # nonlinear: drawdown = difference with the same run without the wells
        well_drawdown = shown(solve_run("new", with_wells=False, start=ref)) - heads_new
    elif new.flux_response is not None:
        well_drawdown = -new.flux_response  # linear: exact response, drawdown positive
    for j, node in enumerate(nodes):
        if j < len(convertible) and convertible[j] is not None:
            n_dry = int(np.maximum(ref.dry[j], new.dry[j]).sum())
            if n_dry:
                warnings.append(
                    f"{node.label}: in {n_dry} cellen valt de laag (bijna) droog. Daar rekent het "
                    f"model met een minimale natte dikte ({MIN_SATURATED_FRACTION:.0%} van de "
                    "laagdikte), zoals MODFLOW 6 (Newton). Kaarten tonen daar de onderkant van "
                    "de laag als stijghoogte (droog)."
                )
        if j == 0:  # phreatic layer or clay top: no confined top
            continue
        top = geometry[j][1]
        n_unconf = int(np.maximum(ref.heads[j] < top, new.heads[j] < top).sum())
        if n_unconf:
            how = (
                "de transmissiviteit rekent daar met de natte dikte"
                if params.convertible
                else "de transmissiviteit blijft in het model vast"
            )
            warnings.append(
                f"{node.label}: in {n_unconf} cellen zakt de stijghoogte onder de bovenkant "
                f"van het pakket (plaatselijk freatisch); {how}."
            )
    if well_drawdown is not None and not params.convertible:
        for g in params.wells:
            if g.layer != nodes[0].key or g.q_new <= 0:
                continue
            # largest drawdown relative to the local phreatic thickness (zone of the cell)
            s_max, thick = max(
                ((float(well_drawdown[0, r, c]), cols[zmap[r, c]][0].layers[0].thickness)
                 for r, c in g.cells),
                key=lambda st: st[0] / st[1],
            )  # fmt: skip
            if s_max > WARN_PHREATIC_DRAWDOWN * thick:
                warnings.append(
                    f"{g.name}: de verlaging in de freatische laag ({s_max:.2f} m, "
                    f"celgemiddelde) is meer dan {WARN_PHREATIC_DRAWDOWN:.0%} van de dikte "
                    f"({thick:g} m). De transmissiviteit blijft in het model vast; de werkelijke "
                    "verlaging is dan groter."
                )

    x = (np.arange(params.ncol) + 0.5) * params.cell
    y = (np.arange(params.nrow) + 0.5) * params.cell
    return Result(
        x=x,
        y=y,
        heads_ref=heads_ref,
        heads_new=heads_new,
        rise=heads_new - heads_ref,
        budget=budget,
        mass_balance={"referentie": ref.mass_balance_error, "opzet": new.mass_balance_error},
        iterations={"referentie": ref.iterations, "opzet": new.iterations},
        inundated=inundated,
        conductances=conductances,
        node_labels={n.key: n.label for n in nodes},
        warnings=warnings,
        zone_map=zmap,
        surface=surface_map(params),
        zone_names=zone_names,
        well_drawdown=well_drawdown,
    )


def strip_benchmark(stack: LayerStack, cell: float, correction: bool) -> tuple[float, float]:
    """Benchmark against the 1D analytic peilopzet: long parallel ditches (full columns),
    closed boundary, pipe drainage at the polder level everywhere (w = 100 d), no recharge
    and fixed transmissivity, so the model stays linear. Returns (numerical, analytic)
    infiltration of the raised ditch [m2/d per m]."""
    from gwproducten.analytic.multilayer import LayerSystem, LineElement, solve

    nrow = max(4, int(np.ceil(50.0 / cell)))
    size_x, size_y = 1000.0, nrow * cell
    ncol = int(round(size_x / cell))
    mid, off = ncol // 2, int(round(100.0 / cell))
    wcs = (
        Watercourse("opzet", column_cells(mid, range(nrow)), "riv", -1.0, -0.8, -1.6, 2.0, cell),
        Watercourse(
            "west", column_cells(mid - off, range(nrow)), "riv", -1.0, -1.0, -1.4, 1.0, cell
        ),
        Watercourse(
            "oost", column_cells(mid + off, range(nrow)), "riv", -1.0, -1.0, -1.4, 1.0, cell
        ),
    )
    w = 100.0
    par = Params(
        stack=stack, surface=0.0, recharge=0.0, size_x=size_x, size_y=size_y, cell=cell,
        boundary="gesloten", watercourses=wcs,
        drain_cells=tuple((r, c) for r in range(nrow) for c in range(ncol)),
        drain_depth=1.0, drain_resistance=w, grid_correction=correction, convertible=False,
    )  # fmt: skip
    res = compute(par)
    q_num = (
        float(
            res.budget.loc["opzet", "opzet [m³/d]"] - res.budget.loc["opzet", "referentie [m³/d]"]
        )
        / size_y
    )
    sub = subsoil_helper(par)
    system = LayerSystem(tuple(n.T for n in sub.nodes), sub.stack.node_resistances(), w)
    els, owner = [], []
    for k, (wc, level) in enumerate(zip(wcs, (0.2, 0.0, 0.0), strict=True)):
        x = (wc.cells[0][1] + 0.5) * cell
        for node, C in sub.conductance_parts(as_ditch(par, wc)):
            els.append(LineElement(x, node, "head", level, C))
            owner.append(k)
    sol = solve(system, els)
    q_an = float(sum(q for q, k in zip(sol.Q, owner, strict=True) if k == 0))
    return q_num, q_an


__all__ = [
    "META",
    "THEORY",
    "Params",
    "Result",
    "GhbGroup",
    "Watercourse",
    "WellGroup",
    "Zone",
    "columns",
    "surface_map",
    "zone_map",
    "as_ditch",
    "column_cells",
    "default_watercourses",
    "compute",
    "schematic",
    "strip_benchmark",
    "subsoil_helper",
]
