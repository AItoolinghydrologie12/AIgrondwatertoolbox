"""Step 1 of the Grondwatertoolbox page: layers, zones, surface, drainage, boundary and
seepage."""

from __future__ import annotations

from typing import Any

import streamlit as st
from draw_actions import new_zone
from toolbox_state import DEFAULTS, KEY, bump, clear_undo, ss
from ui_common import number, stack_inputs, zone_stack_inputs

from gwproducten.core.layers import SPLIT_RESISTANCE, SPLIT_THICKNESS, LayerStack, split_aquifer
from gwproducten.core.params import meta
from gwproducten.products import peilopzet_grid as p3

BOUNDARY_LABEL = {
    "vast": "vaste stijghoogte",
    "doorlopend": "doorlopend pakket",
    "gesloten": "gesloten",
    "per_laag": "per laag",
}
BOUNDARY_HELP = (
    "Vaste stijghoogte: de buitenste ring houdt het randpeil (CHD). Doorlopend pakket: het "
    "pakket loopt buiten het model door; de rand watert af naar het randpeil met de weerstand "
    "van een half oneindig lekkend pakket (spreidingslengte λ). Gesloten: geen stroming."
)


def size_input(name: str, key: str, size_max: float) -> float:
    """Model width/height whose maximum follows the cell size; an old value above the new
    maximum is clipped instead of raising a widget error."""
    m = meta(p3.Params, name)
    ss.setdefault(key, float(getattr(DEFAULTS, name)))
    ss[key] = min(float(ss[key]), size_max)
    return st.number_input(
        f"{m['label']} [{m['unit']}]", float(m["min"]), size_max, step=25.0, format="%g",
        key=key,
    )  # fmt: skip


def model_area_inputs(values: dict[str, Any]) -> None:
    """Cell size, width and height of the model; fills ``values``."""
    values["cell"] = number(p3.Params, "cell", DEFAULTS.cell, f"{KEY}_cell", 5.0)
    size_max = p3.MAX_CELLS_PER_SIDE * values["cell"]
    for name, k in (("size_x", "sx"), ("size_y", "sy")):
        values[name] = size_input(name, f"{KEY}_{k}", size_max)
    st.caption(
        f"Maximaal {p3.MAX_CELLS_PER_SIDE} × {p3.MAX_CELLS_PER_SIDE} cellen: bij "
        f"{values['cell']:g} m is het model hooguit {size_max:g} m breed en hoog. Bij veel "
        "lagen minder: "
        + ", ".join(f"{n} rekenlagen {m} × {m}" for n, m in p3.MAX_CELLS_BY_NODES.items())
        + "."
    )


def zone_inputs(base: LayerStack) -> dict[int, tuple[LayerStack, float]]:
    """Zones in step 1: name, surface and layer values per zone (structure of the base).
    Cells are assigned in step 2. Returns (stack, surface) per zone id."""
    st.subheader("Zones")
    st.caption(
        "Een zone heeft dezelfde opbouw als de basis, maar eigen diktes, k, c en maaiveld. De "
        "diktes tellen vanaf het maaiveld van de zone. Cellen wijs je toe in stap 2."
    )
    zones = ss[f"{KEY}_zones"]
    out: dict[int, tuple[LayerStack, float]] = {}
    for z in list(zones):
        k = f"{KEY}_z{z['id']}"
        with st.expander(f"Zone {z['name']}", expanded=False):
            c1, c2 = st.columns(2)
            z["name"] = c1.text_input("Naam", z["name"], key=f"{k}_name").strip() or z["name"]
            with c2:
                surface = number(p3.Zone, "surface", DEFAULTS.surface, f"{k}_mv", 0.1)
            n_cells = len(z["points"])
            st.caption(f"{n_cells} cellen toegewezen" if n_cells else "Nog geen cellen.")
            zstack = zone_stack_inputs(base, k) if not base.errors() else base
            out[z["id"]] = (zstack, surface)
            if st.button("Zone verwijderen", key=f"{k}_del", icon=":material/delete:"):
                ss[f"{KEY}_zones"] = [w for w in zones if w["id"] != z["id"]]
                bump()
                st.rerun()
    if st.button(
        "Zone toevoegen", key=f"{KEY}_zone_add", icon=":material/add:",
        disabled=len(zones) >= p3.MAX_ZONES,
        help=f"Maximaal {p3.MAX_ZONES} zones.",
    ):  # fmt: skip
        new_zone(ss, KEY)
        bump()
        st.rerun()
    return out


def write_stack(state: Any, prefix: str, stack: LayerStack) -> None:
    """Layer values of ``stack`` into the widget keys ``{prefix}_{i}_d/_k/_a/_c/_kh``."""
    for i, lay in enumerate(stack.layers):
        k = f"{prefix}_{i}"
        state[f"{k}_d"] = round(lay.thickness, 3)
        if lay.kind == "pakket":
            state[f"{k}_k"], state[f"{k}_a"] = lay.k_h, lay.anisotropy
        else:
            state[f"{k}_c"] = lay.c
            if i == 0:
                state[f"{k}_kh"] = lay.k_h


def apply_split(state: Any, split: dict[str, Any]) -> None:
    """Apply a split (prepared by :func:`_split_inputs`) before the widgets exist: the new
    layer values of the base and the zones, and the layers of wells, GHB/CHD groups, walls and
    the boundary per layer shifted one node down below the split."""
    old_keys, new_keys, node = split["old_keys"], split["new_keys"], split["node"]
    remap = {k: new_keys[j + (j > node)] for j, k in enumerate(old_keys)}
    base: LayerStack = split["base"]
    mode, n = base.top_mode, sum(lay.kind == "pakket" for lay in base.layers)
    state[f"{KEY}_naq_{mode}"] = n
    write_stack(state, f"{KEY}_{mode}", base)
    for zid, zstack in split["zones"].items():
        write_stack(state, f"{KEY}_z{zid}_{mode}_{n}", zstack)
    for name in ("wells", "ghb", "chd", "walls"):
        for g in state.get(f"{KEY}_{name}", []):
            g["layer"] = remap.get(g["layer"], g["layer"])
    for j in range(len(old_keys) - 1, -1, -1):  # bottom up: no value is overwritten too early
        for field in ("bkind", "bh", "bfix"):
            old = f"{KEY}_{field}_{old_keys[j]}"
            if old in state:
                state[f"{KEY}_{field}_{remap[old_keys[j]]}"] = state[old]
                if j == node:  # the lower part keeps the boundary of the upper part
                    state[f"{KEY}_{field}_{new_keys[node + 1]}"] = state[old]


def _split_inputs(
    stack: LayerStack, zone_values: dict[int, tuple[LayerStack, float]], surface: float
) -> None:
    """Split an aquifer at the tip of a sheet pile wall: the model lets a wall end at the
    bottom of a whole layer."""
    with st.expander("Pakket splitsen bij de punt van een damwand", expanded=False):
        st.caption(
            "Het model rekent met één rekenpunt per pakket. Eindigt een damwand halverwege een "
            "pakket, splits het dan hier op de diepte van de punt: de app voegt een scheidende "
            f"laag van {SPLIT_THICKNESS:g} m met c = {SPLIT_RESISTANCE:g} d in. Daarna laat je "
            "de wand tot en met het bovenste deel lopen. Zones worden op dezelfde NAP-hoogte "
            "gesplitst."
        )
        if stack.errors():
            return
        n = sum(lay.kind == "pakket" for lay in stack.layers)
        tip = st.number_input(
            "Punt van de damwand [m NAP]", -200.0, 50.0, round(surface - 10.0, 1), 0.5,
            format="%g", key=f"{KEY}_split_tip",
        )  # fmt: skip
        depth = surface - tip
        if not 0.0 < depth < stack.total_thickness:
            st.info("Die hoogte ligt niet binnen de lagenopbouw.")
            return
        i = stack.layer_at_depth(depth)
        lay = stack.layers[i]
        if lay.kind != "pakket":
            st.info(
                f"De punt ligt in {lay.name} (scheidend): splitsen is niet nodig. Laat de wand "
                "tot en met het pakket erboven lopen."
            )
            return
        if n >= p3.MAX_AQUIFERS:
            st.info(f"Er zijn al {p3.MAX_AQUIFERS} pakketten (het maximum).")
            return
        try:
            new = split_aquifer(stack, i, depth + stack.z_top[i])
        except ValueError as err:
            st.info(str(err))
            return
        node = stack.node_of_layer(i)
        upper, lower = new.layers[i].thickness, new.layers[i + 2].thickness
        st.caption(
            f"{lay.name} wordt {upper:.2f} m + {SPLIT_THICKNESS:g} m scheiding + {lower:.2f} m. "
            f"Verticale weerstand tussen de delen: {new.node_resistances()[node]:.2f} d "
            "(halve pakketten D/2kv plus de scheiding)."
        )
        if st.button("Splitsen", key=f"{KEY}_split_go", icon=":material/call_split:"):
            zones = {}
            for zid, (zstack, zsurface) in zone_values.items():
                zl = zstack.layers[i]
                at = zsurface - tip + zstack.z_top[i]  # depth below the top of that aquifer
                at = min(max(at, SPLIT_THICKNESS), zl.thickness - 2 * SPLIT_THICKNESS)
                zones[zid] = split_aquifer(zstack, i, at)
            ss[f"{KEY}_split_pending"] = {
                "base": new, "zones": zones, "node": node,
                "old_keys": [nd.key for nd in stack.nodes],
                "new_keys": [nd.key for nd in new.nodes],
            }  # fmt: skip
            st.rerun()


def _surface_inputs(values: dict[str, Any]) -> None:
    st.subheader("Maaiveld, neerslag en drainage")
    with st.container(border=True):
        c1, c2 = st.columns(2)
        with c1:
            values["surface"] = number(p3.Params, "surface", DEFAULTS.surface, f"{KEY}_mv", 0.1)
        values["recharge"] = (
            c2.number_input(
                "Neerslagoverschot [mm/d]", -10.0, 20.0, DEFAULTS.recharge * 1000, 0.1,
                format="%g", key=f"{KEY}_rch",
                help="Jaargemiddeld Nederland ~0,8 mm/d (≈ 290 mm/jaar); natte winter ~2 mm/d.",
            )
            / 1000.0
        )  # fmt: skip
        c1, c2, c3 = st.columns(3)
        with c1:
            values["mv_resistance"] = number(
                p3.Params, "mv_resistance", DEFAULTS.mv_resistance, f"{KEY}_wmv", 1.0
            )
        with c2:
            values["drain_depth"] = number(
                p3.Params, "drain_depth", DEFAULTS.drain_depth, f"{KEY}_dd", 0.1
            )
        with c3:
            values["drain_resistance"] = number(
                p3.Params, "drain_resistance", DEFAULTS.drain_resistance, f"{KEY}_dw", 10.0
            )
        st.caption(
            "Maaivelddrainage werkt overal zodra het grondwater boven maaiveld komt. "
            "Buisdrainage teken je per cel in stap 2; diepte en weerstand gelden voor alle "
            "gedraineerde cellen."
        )


def _boundary_inputs(values: dict[str, Any], stack: LayerStack) -> None:
    st.subheader("Modelrand en kwel")
    with st.container(border=True):
        nodes = stack.nodes if not stack.errors() else []
        values["boundary"] = st.radio(
            "Modelrand (buitenste ring cellen)", p3.BOUNDARIES, horizontal=True,
            key=f"{KEY}_bnd", format_func=BOUNDARY_LABEL.get, help=BOUNDARY_HELP,
        )  # fmt: skip
        if values["boundary"] in ("vast", "doorlopend"):
            values["boundary_head"] = number(
                p3.Params, "boundary_head", DEFAULTS.boundary_head, f"{KEY}_bh", 0.1
            )
        elif values["boundary"] == "per_laag":
            per_layer: list[float | None] = []
            kinds: list[str] = []
            for node in nodes:
                c1, c2 = st.columns([1, 1], vertical_alignment="bottom")
                kind_key = f"{KEY}_bkind_{node.key}"
                if kind_key not in ss and ss.get(f"{KEY}_bfix_{node.key}") is False:
                    ss[kind_key] = "gesloten"  # project saved with the old on/off switch
                kind = c1.selectbox(
                    f"Rand {node.label}", p3.RING_KINDS, key=kind_key,
                    format_func=BOUNDARY_LABEL.get,
                )  # fmt: skip
                head = c2.number_input(
                    f"Stijghoogte rand {node.label} [m NAP]", -50.0, 50.0,
                    DEFAULTS.boundary_head, 0.1, format="%g", key=f"{KEY}_bh_{node.key}",
                    disabled=kind == "gesloten",
                )  # fmt: skip
                per_layer.append(None if kind == "gesloten" else head)
                kinds.append(kind)
            values["boundary_layers"] = tuple(per_layer)
            values["boundary_kinds"] = tuple(kinds)
        values["seepage"] = st.toggle(
            "Kwel/wegzijging via de onderrand", value=False, key=f"{KEY}_kwel",
            help="Diep pakket onder de onderste rekenlaag met een vaste stijghoogte φ, "
            "via een weerstand c. φ is gelijk in referentie en opzet.",
        )  # fmt: skip
        if values["seepage"]:
            c1, c2 = st.columns(2)
            with c1:
                values["seepage_head"] = number(
                    p3.Params, "seepage_head", DEFAULTS.seepage_head, f"{KEY}_phi", 0.1
                )
            with c2:
                values["seepage_resistance"] = number(
                    p3.Params, "seepage_resistance", DEFAULTS.seepage_resistance,
                    f"{KEY}_ck", 100.0,
                )  # fmt: skip


def subsoil_step(
    values: dict[str, Any],
) -> tuple[LayerStack, dict[int, tuple[LayerStack, float]], str, Any]:
    """Step 1; fills ``values``. Returns the base stack, the zone values, which schematic
    to draw ("basis" or a zone name) and the empty box for that schematic."""
    pending = ss.pop(f"{KEY}_split_pending", None)
    if pending is not None:  # prepared in the previous run, applied before the widgets exist
        apply_split(ss, pending)
        bump()
        clear_undo()  # the old drawings refer to the layers before the split
    col_in, col_fig = st.columns([2, 3], gap="large")
    with col_in:
        st.subheader("Lagenopbouw")
        stack, _ = stack_inputs(
            DEFAULTS, KEY, max_aquifers=p3.MAX_AQUIFERS, with_w=False, container=st.container()
        )
        zone_values = zone_inputs(stack)
        _split_inputs(stack, zone_values, float(ss.get(f"{KEY}_mv", DEFAULTS.surface)))
    with col_fig:
        zone_names = [z["name"] for z in ss[f"{KEY}_zones"]]
        schematic_of = (
            st.selectbox(
                "Doorsnede van", ["basis", *zone_names], key=f"{KEY}_schem_of",
                format_func=lambda n: "basisopbouw" if n == "basis" else f"zone {n}",
            )
            if zone_names
            else "basis"
        )  # fmt: skip
        schematic_box = st.empty()
        _surface_inputs(values)
        _boundary_inputs(values, stack)
    return stack, zone_values, schematic_of, schematic_box
