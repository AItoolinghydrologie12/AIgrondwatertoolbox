"""The same model in MODFLOW 6 (flopy), for validation only; not used by the app.

Every layer is an MF6 model layer, aquitards included (K33 = D/c, negligible horizontal K),
so MF6 resolves the quasi-3D resistances itself. A clay top layer (deklaag) becomes a thin
top layer that carries the phreatic level and the clay transmissivity, on top of the clay:
then the full c lies between the water table and pakket 1, as in the app. The boundaries are
the app's own groups (RIV, DRN, GHB, WEL, CHD) with identical conductances.

Needs ``flopy`` and the ``mf6`` executable (``pip install -e .[mf6]`` and
``get-modflow :flopy``).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import numpy as np

from gwproducten.products import peilopzet_grid as p3

THIN_TOP = 0.01  # [m] thickness of the layer that carries the phreatic level in a clay top
K_AQUITARD_H = 1e-6  # [m/d] horizontal conductivity of an aquitard layer in MF6


def available() -> bool:
    try:
        import flopy  # noqa: F401
    except ImportError:
        return False
    return _exe() is not None


def _exe() -> str | None:
    found = shutil.which("mf6")
    if found:
        return found
    local = Path.home() / "AppData" / "Local" / "flopy" / "bin" / "mf6.exe"
    return str(local) if local.exists() else None


def _layers(params: p3.Params) -> tuple[np.ndarray, list[np.ndarray], list[dict], list[int]]:
    """MF6 top, bottoms, per-layer properties (k, k33, convertible) and the MF6 layer of
    every app node."""
    zmap = p3.zone_map(params)
    cols = p3.columns(params)
    clay_top = params.stack.top_mode == "deklaag"

    def field(fn) -> np.ndarray:
        return np.array([fn(s, z) for s, z in cols])[zmap]

    top = field(lambda s, z: z)
    bottoms, props, node_layer = [], [], []
    for i, lay in enumerate(params.stack.layers):
        z_top = field(lambda s, z, i=i: z + s.z_top[i])
        z_bot = field(lambda s, z, i=i: z + s.z_bottom[i])
        k_h = field(lambda s, z, i=i: s.layers[i].k_h)
        if lay.kind == "pakket":
            node_layer.append(len(bottoms))
            bottoms.append(z_bot)
            props.append(
                {
                    "k": k_h,
                    "k33": k_h * field(lambda s, z, i=i: s.layers[i].anisotropy),
                    "convertible": params.convertible,
                }
            )
            continue
        c = field(lambda s, z, i=i: s.layers[i].c)
        if clay_top and i == 0:  # thin phreatic layer with the clay transmissivity
            node_layer.append(0)
            bottoms.append(z_top - THIN_TOP)
            props.append(
                {"k": k_h * (z_top - z_bot) / THIN_TOP, "k33": np.full_like(c, 1e6),
                 "convertible": False}
            )  # fmt: skip
            z_top = z_top - THIN_TOP
        bottoms.append(z_bot)
        props.append(
            {"k": np.full_like(c, K_AQUITARD_H), "k33": (z_top - z_bot) / c, "convertible": False}
        )
    return top, bottoms, props, node_layer


def run(params: p3.Params, stage: str, workspace: Path, newton: bool = False) -> np.ndarray:
    """Heads per app node (n_nodes, nrow, ncol) [m NAP] computed by MODFLOW 6."""
    import flopy

    params.validate()
    sub = p3.subsoil_helper(params)
    T, c = p3._node_fields(params)
    nl, nr, nc = len(sub.nodes), params.nrow, params.ncol
    top, bottoms, props, node_layer = _layers(params)

    sim = flopy.mf6.MFSimulation(sim_name="gwt", sim_ws=str(workspace), exe_name=_exe())
    flopy.mf6.ModflowTdis(sim, nper=1, perioddata=[(1.0, 1, 1.0)])
    dvclose = 1e-5 if newton else 1e-7  # Newton with drying cells: the usual MF6 tolerance
    flopy.mf6.ModflowIms(
        sim, complexity="COMPLEX" if newton else "MODERATE", outer_dvclose=dvclose,
        outer_maximum=500, inner_dvclose=0.01 * dvclose, rcloserecord=1e-4 if newton else 1e-7,
        inner_maximum=500, linear_acceleration="BICGSTAB" if newton else "CG",
    )  # fmt: skip
    gwf = flopy.mf6.ModflowGwf(
        sim, modelname="gwt", newtonoptions="UNDER_RELAXATION" if newton else None
    )
    flopy.mf6.ModflowGwfdis(
        gwf, nlay=len(bottoms), nrow=nr, ncol=nc, delr=params.cell, delc=params.cell,
        top=top, botm=bottoms,
    )  # fmt: skip
    flopy.mf6.ModflowGwfnpf(
        gwf, icelltype=[int(p["convertible"]) for p in props], k=[p["k"] for p in props],
        k33=[p["k33"] for p in props],
    )  # fmt: skip
    flopy.mf6.ModflowGwfic(gwf, strt=np.full((len(bottoms), nr, nc), params.boundary_head))
    flopy.mf6.ModflowGwfrcha(gwf, recharge=params.recharge)

    def cell(key: int) -> tuple[int, int, int]:
        n, r, col = np.unravel_index(key, (nl, nr, nc))
        return (node_layer[n], int(r), int(col))

    boundaries = p3._boundaries(params, sub, stage, p3._cell_conductances(params, sub))
    ring = p3._ring_ghb(params, T, c)
    if ring is not None:
        boundaries.append(ring)
    for k, b in enumerate(boundaries):
        name = f"b{k}_" + re.sub(r"[^a-z0-9]", "", b.name.lower())[:10]
        if b.kind == "riv":
            data = [
                (cell(key), s, C, bot)
                for key, s, C, bot in zip(b.keys, b.stage, b.conductance, b.bottom, strict=True)
            ]
            flopy.mf6.ModflowGwfriv(gwf, stress_period_data=data, pname=name, filename=name)
        elif b.kind == "drn":
            data = [
                (cell(key), s, C) for key, s, C in zip(b.keys, b.stage, b.conductance, strict=True)
            ]
            flopy.mf6.ModflowGwfdrn(gwf, stress_period_data=data, pname=name, filename=name)
        else:
            data = [
                (cell(key), s, C) for key, s, C in zip(b.keys, b.stage, b.conductance, strict=True)
            ]
            flopy.mf6.ModflowGwfghb(gwf, stress_period_data=data, pname=name, filename=name)
    wells = [
        (cell(key), float(q))
        for w in p3._wells(params, sub, stage)
        for key, q in zip(w.keys, w.rate, strict=True)
    ]
    if wells:
        flopy.mf6.ModflowGwfwel(gwf, stress_period_data=wells)
    chd = [
        (node_layer[n], r, col, head)
        for n, head in enumerate(p3._fixed_head(params))
        if head is not None
        for r in range(nr)
        for col in range(nc)
        if r in (0, nr - 1) or col in (0, nc - 1)
    ]
    if chd:
        flopy.mf6.ModflowGwfchd(
            gwf, stress_period_data=[((lay, r, col), h) for lay, r, col, h in chd]
        )
    flopy.mf6.ModflowGwfoc(gwf, head_filerecord="gwt.hds", saverecord=[("HEAD", "ALL")])
    sim.write_simulation(silent=True)
    ok, out = sim.run_simulation(silent=True)
    if not ok:
        raise RuntimeError("MODFLOW 6 is niet geconvergeerd:\n" + "\n".join(out[-20:]))
    heads = gwf.output.head().get_data()
    return np.array([heads[layer] for layer in node_layer])
