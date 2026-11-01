"""Layer stack: aquifers (pakketten) and aquitards (scheidende lagen) from the surface down.

Two schematisations of the top:

- ``"deklaag"``: the top layer is an aquitard (clay/peat) that holds the phreatic level.
  Stack: deklaag, pakket 1, scheidende laag 2, pakket 2, ...
- ``"freatisch_zand"``: the top layer is a phreatic aquifer.
  Stack: freatisch pakket, scheidende laag 1, pakket 1, scheidende laag 2, pakket 2, ...

The base is impermeable. For the quasi-3D calculation each stack has computational
*nodes*: node 0 is the phreatic layer (the top layer, drained through w); every aquifer
below it is a further node. Aquitards between nodes give the resistances c.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from gwproducten.core.params import param, range_errors

LayerKind = Literal["pakket", "scheidend"]
TopMode = Literal["deklaag", "freatisch_zand"]
TOP_MODES: tuple[TopMode, ...] = ("deklaag", "freatisch_zand")
TOP_MODE_LABEL = {
    "deklaag": "deklaag met freatisch niveau (polder)",
    "freatisch_zand": "freatisch zandpakket",
}
MAX_AQUIFERS = 4
DEFAULT_ANISOTROPY = 0.33  # kv/kh of an aquifer
# aquitard inserted where an aquifer is split (e.g. at the tip of a sheet pile wall)
SPLIT_THICKNESS = 0.1  # [m], the minimum layer thickness
SPLIT_RESISTANCE = 0.01  # [d]
# lower bound for the horizontal transmissivity of a clay top layer [m2/d]; keeps the
# eigenvalue problem well conditioned without changing results noticeably
MIN_TOP_TRANSMISSIVITY = 1e-3


@dataclass(frozen=True)
class Layer:
    name: str
    kind: LayerKind
    thickness: float = param(10.0, "m", "Dikte", 0.1, 500.0)
    k_h: float = param(10.0, "m/d", "Horizontale doorlatendheid k", 1e-5, 1000.0)
    c: float = param(500.0, "d", "Verticale weerstand c", 0.01, 1e6)
    anisotropy: float = param(DEFAULT_ANISOTROPY, "-", "Anisotropie kv/kh", 0.001, 1.0)

    @property
    def half_resistance(self) -> float:
        """Vertical resistance of half an aquifer, D/2 / kv [d] (0 for an aquitard, whose
        resistance is c)."""
        if self.kind != "pakket":
            return 0.0
        return 0.5 * self.thickness / (self.anisotropy * self.k_h)


@dataclass(frozen=True)
class Node:
    """Computational layer of the quasi-3D model."""

    key: str  # column name fragment, e.g. "freatisch", "pakket1"
    label: str  # Dutch label for the UI
    layer: int  # index in the stack
    T: float  # transmissivity [m2/d]


def build_layers(
    top_mode: TopMode,
    aquifers: list[tuple[float, ...]],
    aquitards: list[tuple[float, float, float]],
) -> tuple[Layer, ...]:
    """Assemble a valid alternating stack.

    ``aquifers``: (thickness, k) or (thickness, k, kv/kh) per aquifer, from the top.
    ``aquitards``: (thickness, c, k_h) per aquitard, from the top. ``deklaag`` needs as many
    aquitards as aquifers; ``freatisch_zand`` one fewer.
    """
    layers: list[Layer] = []

    def aquifer(name: str, values: tuple[float, ...]) -> Layer:
        d, k, *rest = values
        return Layer(name, "pakket", d, k, anisotropy=rest[0] if rest else DEFAULT_ANISOTROPY)

    if top_mode == "deklaag":
        for i, (aq, (d_s, c, kh)) in enumerate(zip(aquifers, aquitards, strict=True)):
            name = "Deklaag" if i == 0 else f"Scheidende laag {i + 1}"
            layers.append(Layer(name, "scheidend", d_s, kh, c))
            layers.append(aquifer(f"Pakket {i + 1}", aq))
    else:
        layers.append(aquifer("Freatisch pakket", aquifers[0]))
        for i, ((d_s, c, kh), aq) in enumerate(zip(aquitards, aquifers[1:], strict=True), start=1):
            layers.append(Layer(f"Scheidende laag {i}", "scheidend", d_s, kh, c))
            layers.append(aquifer(f"Pakket {i}", aq))
    return tuple(layers)


DEFAULT_LAYERS = build_layers(
    "deklaag",
    aquifers=[(20.0, 15.0), (30.0, 25.0)],
    aquitards=[(3.0, 500.0, 0.05), (5.0, 2000.0, 0.01)],
)


@dataclass(frozen=True)
class LayerStack:
    top_mode: TopMode = "deklaag"
    layers: tuple[Layer, ...] = field(default=DEFAULT_LAYERS)

    # --- geometry (z = 0 at the surface, negative downward) -------------------------
    @property
    def z_top(self) -> np.ndarray:
        return -np.concatenate([[0.0], np.cumsum([lay.thickness for lay in self.layers])[:-1]])

    @property
    def z_bottom(self) -> np.ndarray:
        return self.z_top - np.array([lay.thickness for lay in self.layers])

    @property
    def total_thickness(self) -> float:
        return float(sum(lay.thickness for lay in self.layers))

    def layer_at_depth(self, depth: float) -> int:
        """Index of the layer containing ``depth`` [m below surface, positive]."""
        bottoms = -self.z_bottom
        return int(min(np.searchsorted(bottoms, depth, side="left"), len(self.layers) - 1))

    # --- quasi-3D nodes -----------------------------------------------------------
    @property
    def nodes(self) -> list[Node]:
        top = self.layers[0]
        label0 = "Freatisch (deklaag)" if self.top_mode == "deklaag" else "Freatisch pakket"
        nodes = [Node("freatisch", label0, 0, max(top.k_h * top.thickness, MIN_TOP_TRANSMISSIVITY))]
        n = 0
        for i, lay in enumerate(self.layers[1:], start=1):
            if lay.kind == "pakket":
                n += 1
                nodes.append(Node(f"pakket{n}", lay.name, i, lay.k_h * lay.thickness))
        return nodes

    def node_resistances(self) -> tuple[float, ...]:
        """Resistance between consecutive nodes: the aquitard between them (for a clay top
        layer its own c lies between the phreatic node and pakket 1) plus half of each
        aquifer node, D/2 / kv, as for one model layer per aquifer in MODFLOW 6."""
        res = []
        nodes = self.nodes
        for a, b in zip(nodes[:-1], nodes[1:], strict=True):
            c = sum(lay.c for lay in self.layers[a.layer : b.layer] if lay.kind == "scheidend")
            halves = self.layers[a.layer].half_resistance + self.layers[b.layer].half_resistance
            res.append(c + halves)
        return tuple(res)

    def node_of_layer(self, i: int) -> int | None:
        """Node index that represents layer ``i``; None for an intermediate aquitard."""
        for n, node in enumerate(self.nodes):
            if node.layer == i:
                return n
        return None

    def aquifer_nodes(self) -> list[int]:
        """Node indices of the aquifers below the phreatic layer (for a trench choice)."""
        return [
            n for n, node in enumerate(self.nodes) if n > 0 or self.top_mode == "freatisch_zand"
        ]

    # --- validation ---------------------------------------------------------------
    def errors(self) -> list[str]:
        errors: list[str] = []
        if self.top_mode not in TOP_MODES:
            return [f"Bovenkant moet 'deklaag' of 'freatisch_zand' zijn (nu '{self.top_mode}')."]
        kinds = [lay.kind for lay in self.layers]
        first = "scheidend" if self.top_mode == "deklaag" else "pakket"
        expected = [
            first if i % 2 == 0 else ("pakket" if first == "scheidend" else "scheidend")
            for i in range(len(kinds))
        ]
        if kinds != expected or not kinds or kinds[-1] != "pakket":
            errors.append(
                "De lagenopbouw moet afwisselend scheidende laag en pakket zijn, beginnend met "
                f"{'een deklaag' if first == 'scheidend' else 'een freatisch pakket'} en "
                "eindigend met een pakket (dichte basis)."
            )
        n_aq = kinds.count("pakket")
        if n_aq > MAX_AQUIFERS:
            errors.append(f"Maximaal {MAX_AQUIFERS} pakketten (nu {n_aq}).")
        for lay in self.layers:
            errors += range_errors(lay, prefix=f"{lay.name}: ")
        return errors


def split_aquifer(stack: LayerStack, index: int, depth: float) -> LayerStack:
    """Split aquifer ``index`` at ``depth`` [m below its top] into two aquifers with the same
    k and kv/kh, separated by a thin aquitard (SPLIT_THICKNESS, SPLIT_RESISTANCE) taken from
    the lower part; the total thickness stays the same. The vertical resistance between the
    halves is then D1/2kv + SPLIT_RESISTANCE + D2/2kv (see :meth:`LayerStack.node_resistances`).
    """
    lay = stack.layers[index]
    if lay.kind != "pakket":
        raise ValueError(f"Laag {index} ({lay.name}) is geen pakket.")
    lower = lay.thickness - depth - SPLIT_THICKNESS
    if depth < SPLIT_THICKNESS or lower < SPLIT_THICKNESS:
        raise ValueError(
            f"{lay.name}: splitsen op {depth:g} m laat een deel dunner dan "
            f"{SPLIT_THICKNESS:g} m over (dikte {lay.thickness:g} m)."
        )
    aquifers, aquitards = [], []
    for i, other in enumerate(stack.layers):
        if other.kind == "scheidend":
            aquitards.append((other.thickness, other.c, other.k_h))
        elif i == index:
            aquifers.append((depth, other.k_h, other.anisotropy))
            aquitards.append((SPLIT_THICKNESS, SPLIT_RESISTANCE, other.k_h))
            aquifers.append((lower, other.k_h, other.anisotropy))
        else:
            aquifers.append((other.thickness, other.k_h, other.anisotropy))
    # top to bottom per kind; build_layers interleaves them again
    return LayerStack(stack.top_mode, build_layers(stack.top_mode, aquifers, aquitards))
