"""Shared layered schematisation for the cross-section products.

A :class:`~gwproducten.core.layers.LayerStack` with drainage resistance w on top.
Watercourses are defined by their geometry (bottom depth, water depth, width); the layers
they touch follow from it. Banks are open: a watercourse exchanges water with every
computational node whose layer its wetted perimeter touches.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from gwproducten.analytic.boundaries import (
    equivalent_length,
    radial_resistance,
    strip_resistance,
)
from gwproducten.analytic.multilayer import LayerSystem, LineElement, Solution, solve
from gwproducten.core.grids import refined_axis
from gwproducten.core.layers import LayerStack, Node
from gwproducten.core.params import param, range_errors
from gwproducten.numeric.fd1d import FD1DResult, solve_fd1d
from gwproducten.numeric.xsection import XZDitch, XZLayer, XZResult, XZTrench, solve_xsection


@dataclass(frozen=True)
class Ditch:
    """Watercourse parallel to the y-axis. ``level`` = level change [m] (rise positive)."""

    name: str = "Watergang"
    x: float = param(50.0, "m", "Positie x", -1e4, 1e4)
    width: float = param(2.0, "m", "Bodembreedte", 0.1, 100.0)
    bottom_depth: float = param(1.0, "m", "Bodemdiepte t.o.v. maaiveld", 0.1, 100.0)
    water_depth: float = param(0.5, "m", "Waterdiepte", 0.05, 50.0)
    entry_resistance: float = param(1.0, "d", "Intreeweerstand", 0.0, 1e4)
    level: float = param(0.0, "m", "Peilverandering", -10.0, 10.0)
    length: float = param(1000.0, "m", "Lengte (bovenaanzicht)", 1.0, 1e5)
    y: float = param(0.0, "m", "Positie y midden (bovenaanzicht)", -1e4, 1e4)

    @property
    def water_level_depth(self) -> float:
        """Depth of the water level below the surface [m]."""
        return self.bottom_depth - self.water_depth

    @property
    def wetted_perimeter(self) -> float:
        return self.width + 2.0 * self.water_depth


@dataclass(frozen=True)
class Contact:
    """Part of a watercourse's wetted perimeter in one layer of the stack."""

    layer: int
    node: int | None  # None: intermediate aquitard (no node of its own)
    perimeter: float  # u_i [m]
    has_bottom: bool


@dataclass(frozen=True)
class Subsoil:
    """Layer stack, drainage and numerical settings shared by the products."""

    stack: LayerStack = field(default_factory=LayerStack)
    w: float = param(100.0, "d", "Drainageweerstand w", 1.0, 1e5)
    anisotropy: float = param(1.0, "-", "Anisotropie kv/kh pakketten (x-z)", 0.01, 1.0)
    dx_min: float = param(0.5, "m", "Kleinste cel 1D", 0.05, 10.0)
    dx_min_xz: float = param(0.25, "m", "Kleinste cel x-z (breedte)", 0.05, 5.0)
    dz_xz: float = param(0.5, "m", "Celhoogte x-z", 0.05, 5.0)
    run_xsection: bool = field(default=True, metadata={"label": "x-z doorsnede rekenen"})

    # --- derived ------------------------------------------------------------------
    @property
    def nodes(self) -> list[Node]:
        return self.stack.nodes

    def layer_system(self) -> LayerSystem:
        return LayerSystem(tuple(n.T for n in self.nodes), self.stack.node_resistances(), self.w)

    def contacts(self, d: Ditch) -> list[Contact]:
        """Wetted perimeter per layer (banks between water level and bottom, plus bottom)."""
        tops = -self.stack.z_top
        bottoms = -self.stack.z_bottom
        bottom_layer = self.stack.layer_at_depth(d.bottom_depth + 1e-9)
        out = []
        for i in range(len(self.stack.layers)):
            wet = min(bottoms[i], d.bottom_depth) - max(tops[i], d.water_level_depth)
            u = 2.0 * max(wet, 0.0) + (d.width if i == bottom_layer else 0.0)
            if u > 0:
                out.append(Contact(i, self.stack.node_of_layer(i), u, i == bottom_layer))
        return out

    def conductance_parts(self, d: Ditch) -> list[tuple[int, float]]:
        """(node, C'_i) per touched node.

        Aquifer layers: C'_i = 1 / (c_wg/u_i + R_i), with the radial resistance
        R_i = ln(D_i/u_i)/(pi k_i) only for the layer holding the bottom.
        Clay top layer (deklaag): the banks connect to the phreatic node with
        1 / (c_wg/u_banks + ln(D/u_banks)/(pi k_h)); a bottom inside the clay connects to
        pakket 1 through the remaining clay (strip on a slab, see :func:`strip_resistance`).
        A bottom inside a deeper aquitard connects the same way to the aquifer below it;
        banks inside such an aquitard are neglected.
        """
        parts: dict[int, float] = {}

        def add(node: int, resistance: float) -> None:
            parts[node] = parts.get(node, 0.0) + (math.inf if resistance == 0 else 1 / resistance)

        clay_top = self.stack.top_mode == "deklaag"
        bottoms = -self.stack.z_bottom
        for ct in self.contacts(d):
            lay = self.stack.layers[ct.layer]
            if ct.node is None:  # intermediate aquitard: banks negligible
                if ct.has_bottom:  # bottom through the rest of it to the aquifer below
                    below = self.stack.node_of_layer(ct.layer + 1)
                    rest = bottoms[ct.layer] - d.bottom_depth
                    r = strip_resistance(d.width, rest, lay.k_h, lay.thickness / lay.c)
                    add(below, d.entry_resistance / d.width + r)
                continue
            if clay_top and ct.layer == 0:
                u_banks = ct.perimeter - (d.width if ct.has_bottom else 0.0)
                if u_banks > 0:
                    r = radial_resistance(lay.thickness, u_banks, lay.k_h)
                    add(0, d.entry_resistance / u_banks + r)
                if ct.has_bottom:
                    rest = lay.thickness - d.bottom_depth
                    kv = lay.thickness / lay.c
                    r = strip_resistance(d.width, rest, lay.k_h, kv)
                    add(1, d.entry_resistance / d.width + r)
                continue
            r = radial_resistance(lay.thickness, ct.perimeter, lay.k_h) if ct.has_bottom else 0.0
            add(ct.node, d.entry_resistance / ct.perimeter + r)
        return sorted(parts.items())

    def conductance(self, d: Ditch) -> float:
        return float(sum(c for _, c in self.conductance_parts(d)))

    def bottom_node(self, d: Ditch) -> int:
        return max(n for n, _ in self.conductance_parts(d))

    def equivalent_length(self, d: Ditch) -> float:
        return equivalent_length(self.nodes[self.bottom_node(d)].T, self.conductance(d))

    def connection_label(self, d: Ditch) -> str:
        layers = self.stack.layers
        names = []
        for ct in self.contacts(d):
            if ct.node is not None:
                names.append(layers[ct.layer].name)
            elif ct.has_bottom:  # bottom in an intermediate aquitard
                names.append(f"bodem via {layers[ct.layer].name} naar {layers[ct.layer + 1].name}")
        return " + ".join(names)

    def xz_layers(self, ditches: tuple[Ditch, ...] = ()) -> list[XZLayer]:
        """x-z layers; rows of at most dz_xz, and at most a quarter of the water depth in
        layers a watercourse touches (resolves flow toward ditches ending in clay)."""
        out = []
        for i, lay in enumerate(self.stack.layers):
            dz = self.dz_xz
            touching = [d for d in ditches if any(ct.layer == i for ct in self.contacts(d))]
            if touching:
                dz = min(dz, 0.25 * min(d.water_depth for d in touching))
            nsub = int(np.clip(math.ceil(lay.thickness / dz), 2, 60))
            if lay.kind == "pakket":
                out.append(XZLayer(lay.thickness, lay.k_h, lay.k_h * self.anisotropy, nsub))
            else:
                kv = lay.thickness / lay.c
                out.append(XZLayer(lay.thickness, max(lay.k_h, kv), kv, nsub))
        return out

    # --- validation ---------------------------------------------------------------
    def subsoil_errors(self) -> list[str]:
        return range_errors(self) + self.stack.errors()

    def ditch_errors(self, d: Ditch) -> list[str]:
        prefix = f"{d.name}: "
        errors = range_errors(d, prefix)
        if d.water_depth >= d.bottom_depth:
            errors.append(
                f"{prefix}waterdiepte ({d.water_depth:g} m) moet kleiner zijn dan de "
                f"bodemdiepte ({d.bottom_depth:g} m): het waterpeil ligt dan boven maaiveld."
            )
        if d.bottom_depth >= self.stack.total_thickness:
            errors.append(
                f"{prefix}bodemdiepte ({d.bottom_depth:g} m) reikt tot of onder de basis van "
                f"de lagenopbouw ({self.stack.total_thickness:g} m)."
            )
        return errors


def validity_warnings(sub: Subsoil, ditches: tuple[Ditch, ...]) -> list[str]:
    """Situations where the analytic schematisation is known to be inaccurate."""
    warnings = []
    for d in ditches:
        for ct in sub.contacts(d):
            lay = sub.stack.layers[ct.layer]
            if ct.has_bottom and lay.kind == "pakket" and ct.perimeter >= lay.thickness:
                warnings.append(
                    f"{d.name}: natte omtrek in {lay.name} (u = {ct.perimeter:.1f} m) is even "
                    f"groot of groter dan de laagdikte ({lay.thickness:g} m). De radiale "
                    "weerstand ln(D/u)/(πk) valt dan weg en de analytische uitwisseling is "
                    "onnauwkeurig (~10 %); vergelijk met de x-z doorsnede."
                )
        bottom = sub.stack.layer_at_depth(d.bottom_depth + 1e-9)
        if sub.stack.top_mode == "deklaag" and bottom == 0:
            warnings.append(
                f"{d.name} eindigt in de deklaag. De uitwisseling is analytisch benaderd "
                "(wanden naar de freatische laag, bodem via de resterende klei naar pakket 1); "
                "afwijking t.o.v. de x-z doorsnede kan 25–50 % zijn. De x-z doorsnede is hier "
                "maatgevend."
            )
    if sub.anisotropy < 0.2:
        warnings.append(
            "Sterke anisotropie (kv/kh < 0,2): de analytische radiale weerstand is dan te "
            "klein; de x-z doorsnede is maatgevend."
        )
    return warnings


def overlap_errors(items: list[tuple[str, float, float]]) -> list[str]:
    """Elements given as (name, x, half width) must not overlap."""
    errors = []
    items = sorted(items, key=lambda t: t[1])
    for (n1, x1, h1), (n2, x2, h2) in zip(items[:-1], items[1:], strict=True):
        if x2 - x1 < h1 + h2 + 0.5:
            errors.append(
                f"{n1} en {n2} liggen te dicht bij elkaar (x = {x1:g} m en {x2:g} m); "
                "houd minstens 0,5 m tussen de randen."
            )
    return errors


@dataclass(frozen=True)
class Trench:
    """Trench or line of wells in one node. kind "flux": injection [m2/d per m]
    (extraction < 0); kind "head": level change [m] (drawdown s0 -> value = -s0)."""

    x: float
    node: int
    kind: Literal["flux", "head"]
    value: float


@dataclass(frozen=True)
class Variant:
    """Result of one method: head change per node (key) on the display grid."""

    phi: dict[str, np.ndarray]
    Q_ditches: np.ndarray
    Q_trench: float
    mass_balance_error: float


@dataclass(frozen=True)
class Run:
    x: np.ndarray
    lambdas: np.ndarray
    conductances: np.ndarray
    analytic: Variant
    analytic_solution: Solution
    ideal: Variant
    no_ditches: Variant | None
    fd1d: Variant
    fd1d_raw: FD1DResult
    fd1d_error: float  # max |analytic - 1D| at the 1D nodes, all layers [m]
    xz: Variant | None
    xz_raw: XZResult | None


def _elements(
    sub: Subsoil,
    ditches: tuple[Ditch, ...],
    trench: Trench | None,
    ideal: tuple[bool, ...] | bool = False,
) -> tuple[list[LineElement], list[int]]:
    """Line elements and, per element, the index of its ditch (-1 for the trench)."""
    flags = (ideal,) * len(ditches) if isinstance(ideal, bool) else ideal
    els, owner = [], []
    for i, (d, flag) in enumerate(zip(ditches, flags, strict=True)):
        for node, C in sub.conductance_parts(d):
            els.append(LineElement(d.x, node, "head", d.level, math.inf if flag else C, d.name))
            owner.append(i)
    if trench is not None:
        els.append(LineElement(trench.x, trench.node, trench.kind, trench.value))
        owner.append(-1)
    return els, owner


def _per_ditch(Q: np.ndarray, owner: list[int], n_ditches: int) -> np.ndarray:
    out = np.zeros(n_ditches)
    for q, i in zip(Q, owner, strict=True):
        if i >= 0:
            out[i] += q
    return out


def _variant(
    sol: Solution, x: np.ndarray, owner: list[int], n_ditches: int, keys: list[str]
) -> Variant:
    phi = sol.head(x)
    trench_q = [q for q, i in zip(sol.Q, owner, strict=True) if i < 0]
    return Variant(
        phi=dict(zip(keys, phi, strict=True)),
        Q_ditches=_per_ditch(sol.Q, owner, n_ditches),
        Q_trench=float(trench_q[0]) if trench_q else 0.0,
        mass_balance_error=sol.mass_balance_error(),
    )


def analytic_solution(
    sub: Subsoil, ditches: tuple[Ditch, ...], trench: Trench | None = None
) -> tuple[Solution, list[int]]:
    """Analytic solution with leaky ditches only (no numerics), e.g. for parameter sweeps."""
    els, owner = _elements(sub, ditches, trench)
    return solve(sub.layer_system(), els), owner


def analytic_variant(
    sub: Subsoil,
    ditches: tuple[Ditch, ...],
    trench: Trench | None,
    x: np.ndarray,
    ideal: tuple[bool, ...],
) -> Variant:
    """Analytic solution where ditch i is an ideal boundary when ``ideal[i]``."""
    els, owner = _elements(sub, ditches, trench, ideal)
    keys = [n.key for n in sub.nodes]
    return _variant(solve(sub.layer_system(), els), x, owner, len(ditches), keys)


def display_grid(xs: list[float], lam1: float, dx_min: float = 0.25) -> np.ndarray:
    margin = max(min(2.5 * lam1, 3000.0), 100.0)
    lo, hi = min(xs) - margin, max(xs) + margin
    return refined_axis(lo, hi, xs, dx_min, (hi - lo) / 400.0, 1.1)


def run(
    sub: Subsoil,
    ditches: tuple[Ditch, ...],
    trench: Trench | None,
    *,
    with_no_ditch_variant: bool = True,
) -> Run:
    """Analytic (leaky, ideal and without ditches), 1D numeric and optional x-z."""
    system = sub.layer_system()
    lambdas = system.leakage_factors
    keys = [n.key for n in sub.nodes]
    xs = [d.x for d in ditches] + ([trench.x] if trench is not None else [])
    x = display_grid(xs, float(lambdas[0]))
    nd = len(ditches)

    leaky_el, owner = _elements(sub, ditches, trench)
    leaky_sol = solve(system, leaky_el)
    analytic = _variant(leaky_sol, x, owner, nd, keys)
    ideal_el, ideal_owner = _elements(sub, ditches, trench, ideal=True)
    ideal = _variant(solve(system, ideal_el), x, ideal_owner, nd, keys)
    no_ditches = None
    if with_no_ditch_variant and trench is not None:
        el0, own0 = _elements(sub, (), trench)
        no_ditches = _variant(solve(system, el0), x, own0, 0, keys)

    fd = solve_fd1d(system, leaky_el, dx_min=sub.dx_min)
    trench_q = [q for q, i in zip(fd.Q, owner, strict=True) if i < 0]
    fd1d = Variant(
        phi={k: np.interp(x, fd.x, fd.phi[n]) for n, k in enumerate(keys)},
        Q_ditches=_per_ditch(fd.Q, owner, nd),
        Q_trench=float(trench_q[0]) if trench_q else 0.0,
        mass_balance_error=fd.mass_balance_error,
    )

    xz = xz_raw = None
    if sub.run_xsection:
        xz_raw = solve_xsection(
            sub.xz_layers(ditches),
            sub.w,
            [
                XZDitch(
                    d.x,
                    d.width,
                    -d.bottom_depth,
                    -d.water_level_depth,
                    d.entry_resistance,
                    d.level,
                    d.name,
                )
                for d in ditches
            ],
            None
            if trench is None
            else XZTrench(trench.x, sub.nodes[trench.node].layer, trench.kind, trench.value),
            extent=12.0 * float(lambdas[0]),
            dx_min=sub.dx_min_xz,
            dx_max=far_field_dx(lambdas, sub.dx_min_xz, 20.0),
        )
        profiles = {n.key: xz_raw.layer_mean[n.layer] for n in sub.nodes}
        if sub.stack.top_mode == "deklaag":
            profiles["freatisch"] = xz_raw.phi[0]  # water table at the top of the clay
        xz = Variant(
            phi={k: _interp_nan(x, xz_raw.x, v) for k, v in profiles.items()},
            Q_ditches=xz_raw.Q_ditches,
            Q_trench=xz_raw.Q_trench,
            mass_balance_error=xz_raw.mass_balance_error,
        )

    return Run(
        x=x,
        lambdas=lambdas,
        conductances=np.array([sub.conductance(d) for d in ditches]),
        analytic=analytic,
        analytic_solution=leaky_sol,
        ideal=ideal,
        no_ditches=no_ditches,
        fd1d=fd1d,
        fd1d_raw=fd,
        fd1d_error=float(np.abs(fd.phi - leaky_sol.head(fd.x)).max()),
        xz=xz,
        xz_raw=xz_raw,
    )


def far_field_dx(lambdas: np.ndarray, dx_min: float, cap: float) -> float:
    """Largest cell size: resolve the shortest spreading length, but not below 4 dx_min
    or lambda_1/100. Very short modes (a clay top layer) are confined to the refined zone
    near the elements, so they need not set the cell size kilometres away."""
    return min(cap, max(0.5 * float(lambdas[-1]), 4.0 * dx_min, float(lambdas[0]) / 100.0))


def _interp_nan(x: np.ndarray, xp: np.ndarray, fp: np.ndarray) -> np.ndarray:
    """Interpolate, bridging columns that are fully inside a ditch (NaN)."""
    ok = np.isfinite(fp)
    return np.interp(x, xp[ok], fp[ok])


__all__ = [
    "Contact",
    "Ditch",
    "Run",
    "Subsoil",
    "Trench",
    "Variant",
    "analytic_solution",
    "analytic_variant",
    "overlap_errors",
    "run",
    "validity_warnings",
]
