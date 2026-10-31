"""The same model in MODFLOW 6 (flopy): second calculation engine of the app and the
reference for the comparison tests.

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

from gwproducten.numeric.grid2d import Convertible, FluxBoundary, GridSolution, HeadBoundary
from gwproducten.products import peilopzet_grid as p3

THIN_TOP = 0.01  # [m] thickness of the layer that carries the phreatic level in a clay top
K_AQUITARD_H = 1e-6  # [m/d] horizontal conductivity of an aquitard layer in MF6


def available() -> bool:
    try:
        import flopy  # noqa: F401
    except ImportError:
        return False
    return _exe() is not None


def version() -> str:
    """MODFLOW 6 version of the executable, e.g. '6.7.0' (cached)."""
    global _VERSION
    if _VERSION is None:
        import subprocess

        try:
            out = subprocess.run(
                [_exe(), "--version"], capture_output=True, text=True, timeout=30, check=False
            ).stdout
            found = re.search(r"\d+\.\d+\.\d+", out)
            _VERSION = found.group(0) if found else "6"
        except OSError, subprocess.SubprocessError:
            _VERSION = "6"
    return _VERSION


_VERSION: str | None = None


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


def _pname(k: int, name: str) -> str:
    return f"b{k}_" + re.sub(r"[^a-z0-9]", "", name.lower())[:10]


def build(
    params: p3.Params,
    boundaries: list[HeadBoundary],
    fluxes: list[FluxBoundary],
    fixed: tuple[float | None, ...],
    workspace: Path,
    newton: bool = False,
) -> tuple[object, list[int], dict[str, str]]:
    """Write the MF6 simulation for one run (the app's boundary groups, wells and fixed
    ring). Returns the simulation, the MF6 layer of every app node and package name -> group
    name."""
    import flopy

    nl, nr, nc = len(params.stack.nodes), params.nrow, params.ncol
    top, bottoms, props, node_layer = _layers(params)

    sim = flopy.mf6.MFSimulation(sim_name="gwt", sim_ws=str(workspace), exe_name=_exe())
    flopy.mf6.ModflowTdis(sim, nper=1, perioddata=[(1.0, 1, 1.0)])
    dvclose = 1e-5 if newton else 1e-7  # Newton with drying cells: the usual MF6 tolerance
    flopy.mf6.ModflowIms(
        sim, complexity="COMPLEX" if newton else "MODERATE", outer_dvclose=dvclose,
        outer_maximum=500, inner_dvclose=0.01 * dvclose, rcloserecord=1e-4 if newton else 1e-7,
        inner_maximum=500, linear_acceleration="BICGSTAB" if newton else "CG",
        csv_outer_output_filerecord="outer.csv",
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
    flopy.mf6.ModflowGwfrcha(gwf, recharge=params.recharge, pname="rch")

    def cell(key: int) -> tuple[int, int, int]:
        n, r, col = np.unravel_index(key, (nl, nr, nc))
        return (node_layer[n], int(r), int(col))

    names: dict[str, str] = {}
    for k, b in enumerate(boundaries):
        pname = _pname(k, b.name)
        names[pname.upper()] = b.name
        rows = zip(b.keys, b.stage, b.conductance, b.bottom, strict=True)
        if b.kind == "riv":
            data = [(cell(key), s, C, bot) for key, s, C, bot in rows]
            flopy.mf6.ModflowGwfriv(gwf, stress_period_data=data, pname=pname, filename=pname)
        else:
            data = [(cell(key), s, C) for key, s, C, _ in rows]
            package = flopy.mf6.ModflowGwfdrn if b.kind == "drn" else flopy.mf6.ModflowGwfghb
            package(gwf, stress_period_data=data, pname=pname, filename=pname)
    for k, w in enumerate(fluxes, start=len(boundaries)):
        pname = _pname(k, w.name)
        names[pname.upper()] = w.name
        data = [(cell(key), float(q)) for key, q in zip(w.keys, w.rate, strict=True)]
        flopy.mf6.ModflowGwfwel(gwf, stress_period_data=data, pname=pname, filename=pname)
    chd = [
        ((node_layer[n], r, col), head)
        for n, head in enumerate(fixed)
        if head is not None
        for r in range(nr)
        for col in range(nc)
        if r in (0, nr - 1) or col in (0, nc - 1)
    ]
    if chd:
        flopy.mf6.ModflowGwfchd(gwf, stress_period_data=chd, pname="chd")
    flopy.mf6.ModflowGwfoc(gwf, head_filerecord="gwt.hds", saverecord=[("HEAD", "ALL")])
    return sim, node_layer, names


_BUDGET_LINE = re.compile(r"^\s*\S+\s*=\s*\S+\s+\S+\s*=\s*(\S+)\s+(\S+)\s*$")


def _budget(listing: Path) -> dict[str, float]:
    """Rate per package name (in - out) [m3/d] from the last budget table of the listing."""
    lines = listing.read_text(errors="replace").splitlines()
    start = max(i for i, line in enumerate(lines) if "VOLUME BUDGET FOR ENTIRE MODEL" in line)
    out: dict[str, float] = {}
    sign = 1.0
    for line in lines[start:]:
        if line.strip().startswith("IN:"):
            sign = 1.0
        elif line.strip().startswith("OUT:"):
            sign = -1.0
        elif "PERCENT DISCREPANCY" in line:
            break
        elif m := _BUDGET_LINE.match(line):
            out[m.group(2)] = out.get(m.group(2), 0.0) + sign * float(m.group(1))
    return out


def solve(
    params: p3.Params,
    boundaries: list[HeadBoundary],
    fluxes: list[FluxBoundary],
    fixed: tuple[float | None, ...],
    T: list[np.ndarray],
    convertible: list[Convertible | None],
    workspace: Path,
) -> tuple[GridSolution, bool]:
    """One run with MODFLOW 6, as a :class:`GridSolution` (same fields as the app's solver).
    First the standard formulation (identical to the app); with dry cells or no convergence
    again with Newton. Returns (solution, Newton used); RuntimeError if MF6 fails."""
    nl, nr, nc = len(params.stack.nodes), params.nrow, params.ncol
    for newton in (False, True):
        ws = workspace / ("newton" if newton else "standaard")
        sim, node_layer, names = build(params, boundaries, fluxes, fixed, ws, newton)
        sim.write_simulation(silent=True)
        ok, _ = sim.run_simulation(silent=True)
        if not ok:
            continue
        all_heads = sim.get_model("gwt").output.head().get_data()
        heads = np.array([all_heads[layer] for layer in node_layer])
        if np.any(np.abs(heads) > 1e20):  # HDRY: cells switched off as dry
            continue
        break
    else:
        raise RuntimeError("MODFLOW 6 is niet geconvergeerd (standaard en Newton).")

    rates = _budget(ws / "gwt.lst")
    fluxes_out = {names[p]: q for p, q in rates.items() if p in names}
    recharge = rates.get("RCH", 0.0)
    lateral = rates.get("CHD", 0.0)
    terms = np.array(list(rates.values()))
    error = abs(terms.sum()) / max(np.abs(terms).sum() / 2.0, 1e-300)
    outer = (ws / "outer.csv").read_text().strip().splitlines()
    active = {}
    for b in boundaries:
        hb = heads.ravel()[b.keys]
        if b.kind == "ghb":
            active[b.name] = np.ones(hb.size, dtype=bool)
        else:
            active[b.name] = hb > (b.bottom if b.kind == "riv" else b.stage)
    conv = list(convertible) + [None] * (nl - len(convertible))
    shape = (nr, nc)
    transmissivity = np.array(
        [
            np.broadcast_to(np.asarray(T[j], float), shape)
            if cv is None
            else np.asarray(cv.k) * cv.saturated(heads[j])
            for j, cv in enumerate(conv)
        ]
    )
    dry = np.array(
        [
            np.zeros(shape, dtype=bool) if cv is None else np.broadcast_to(cv.dry(heads[j]), shape)
            for j, cv in enumerate(conv)
        ]
    )
    solution = GridSolution(
        heads=heads, fluxes=fluxes_out, recharge=recharge, lateral=lateral,
        iterations=max(len(outer) - 1, 1), converged=True, mass_balance_error=float(error),
        active=active, flux_response=None, transmissivity=transmissivity, dry=dry,
    )  # fmt: skip
    return solution, newton


def run(params: p3.Params, stage: str, workspace: Path, newton: bool = False) -> np.ndarray:
    """Heads per app node (n_nodes, nrow, ncol) [m NAP] computed by MODFLOW 6 with one fixed
    formulation (for the comparison tests)."""
    params.validate()
    sub = p3.subsoil_helper(params)
    T, c = p3._node_fields(params)
    boundaries = p3._boundaries(params, sub, stage, p3._cell_conductances(params, sub))
    ring = p3._ring_ghb(params, T, c)
    if ring is not None:
        boundaries.append(ring)
    sim, node_layer, _ = build(
        params, boundaries, p3._wells(params, sub, stage), p3._fixed_head(params), workspace,
        newton,
    )  # fmt: skip
    sim.write_simulation(silent=True)
    ok, out = sim.run_simulation(silent=True)
    if not ok:
        raise RuntimeError("MODFLOW 6 is niet geconvergeerd:\n" + "\n".join(out[-20:]))
    heads = sim.get_model("gwt").output.head().get_data()
    return np.array([heads[layer] for layer in node_layer])
