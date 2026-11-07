"""The groundwater model in MODFLOW 6: the calculation engine of the app.

Every layer is an MF6 model layer, aquitards included (K33 = D/c, negligible horizontal K),
so MF6 resolves the quasi-3D resistances itself. A clay top layer (deklaag) becomes a thin
top layer that carries the phreatic level and the clay transmissivity, on top of the clay:
then the full c lies between the water table and pakket 1, as in the app. The boundaries are
the app's own groups (RIV, DRN, GHB, WEL, CHD) with conductances computed by the app; sheet
pile walls are an HFB package (hydraulic characteristic 1/c_w, 0 = impermeable).

The input files are written here directly (plain MF6 text input) and the binary head file is
read directly, without flopy: a run then costs little more than MF6 itself. The ``mf6``
executable is found on the PATH, in the folder of ``get-modflow``, or installed by
:func:`ensure_mf6`, which downloads one fixed MODFLOW 6 release from GitHub once (Windows or
Linux) and checks it against a SHA-256 written in this file; no account, token or API key is
involved.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from gwproducten.numeric.grid2d import (
    Barrier,
    Convertible,
    FixedHeads,
    FluxBoundary,
    GridSolution,
    HeadBoundary,
    barrier_flows,
)
from gwproducten.products import peilopzet_grid as p3

THIN_TOP = 0.01  # [m] thickness of the layer that carries the phreatic level in a clay top
K_AQUITARD_H = 1e-6  # [m/d] horizontal conductivity of an aquitard layer in MF6
RUN_TIMEOUT = 300.0  # [s] one MF6 run
FLOW_REDUCE = 0.1  # WEL AUTO_FLOW_REDUCE: fraction of the cell thickness (MF6 advice)
# attempts per run: (label, Newton, wells may deliver less)
ATTEMPTS = (
    ("standaard", False, False),
    ("Newton", True, False),
    ("Newton, debiet verlaagd", True, True),
)

# MODFLOW 6.7.0, MODFLOW-ORG/executables release 29.0 (public download, fixed version):
# platform -> (zip URL, SHA-256 of the zip, executable inside the zip)
RELEASE = "https://github.com/MODFLOW-ORG/executables/releases/download/29.0/"
DOWNLOADS = {
    "linux": (
        RELEASE + "linux.zip",
        "ab17192a3531dec61d4f4eab3da10f618e85d1f792d5edc71306a3e06e0e22c3",
        "mf6",
    ),
    "win32": (
        RELEASE + "win64.zip",
        "13d1491da8417df85b1084f03bf987e493120857072543ea6f1dd33c0ed1f81e",
        "mf6.exe",
    ),
}
NOT_AVAILABLE = (
    "MODFLOW 6 is op deze computer niet gevonden en kon niet worden opgehaald. De app haalt "
    "MODFLOW 6.7.0 bij de eerste berekening eenmalig op (internet nodig); of zet mf6 zelf op "
    "het PATH (zie README)."
)
# no console window flashing up for every run on Windows
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def _platform() -> str:
    return "linux" if sys.platform.startswith("linux") else sys.platform


def _cache_dir() -> Path:
    if _platform() == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "grondwatertoolbox" / "mf6-29.0"


def _exe() -> str | None:
    found = shutil.which("mf6")
    if found:
        return found
    name = "mf6.exe" if _platform() == "win32" else "mf6"
    for local in (
        _cache_dir() / name,  # ensure_mf6
        Path.home() / "AppData" / "Local" / "flopy" / "bin" / "mf6.exe",  # get-modflow, Windows
    ):
        if local.exists():
            return str(local)
    return None


def available() -> bool:
    return _exe() is not None


def version() -> str:
    """MODFLOW 6 version of the executable, e.g. '6.7.0' (cached)."""
    global _VERSION
    if _VERSION is None:
        try:
            out = subprocess.run(
                [str(_exe()), "--version"], capture_output=True, text=True, timeout=30,
                check=False, **_NO_WINDOW,
            ).stdout  # fmt: skip
            found = re.search(r"\d+\.\d+\.\d+", out)
            _VERSION = found.group(0) if found else "6"
        except OSError, subprocess.SubprocessError:
            _VERSION = "6"
    return _VERSION


_VERSION: str | None = None


def ensure_mf6(timeout: float = 300.0) -> str | None:
    """Path of the mf6 executable; on Windows and Linux download it once if missing (fixed
    release, SHA-256 checked, only the executable extracted). None when not possible; never
    raises."""
    found = _exe()
    if found or _platform() not in DOWNLOADS:
        return found
    url, sha256, member = DOWNLOADS[_platform()]
    target = _cache_dir() / member
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gwt_mf6dl_") as tmp:
            archive = Path(tmp) / "mf6.zip"
            digest = hashlib.sha256()
            with (
                urllib.request.urlopen(url, timeout=timeout) as resp,
                archive.open("wb") as out,
            ):
                while chunk := resp.read(1 << 20):
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest() != sha256:
                return None  # not the expected file: do not use it
            with zipfile.ZipFile(archive) as zf:
                partial = target.with_name(target.name + ".part")
                partial.write_bytes(zf.read(member))
            partial.chmod(0o755)
            partial.replace(target)
    except OSError, KeyError, zipfile.BadZipFile:
        return None
    return str(target)


def _layers(params: p3.Params) -> tuple[np.ndarray, list[np.ndarray], list[dict], list[int]]:
    """MF6 top, bottoms, per-layer properties (k, k33, convertible) and the MF6 layer of
    every app node. Rows north first (MODFLOW 6 row 1 = north edge), see :func:`_north_up`."""
    zmap = _north_up(p3.zone_map(params))
    cols = p3.columns(params)
    clay_top = params.stack.top_mode == "deklaag"

    def field(fn) -> np.ndarray:
        return np.array([fn(s, z) for s, z in cols], dtype=float)[zmap]

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
                {"k": k_h * (z_top - z_bot) / THIN_TOP, "k33": np.full(c.shape, 1e6),
                 "convertible": False}
            )  # fmt: skip
            z_top = z_top - THIN_TOP
        bottoms.append(z_bot)
        props.append(
            {"k": np.full(c.shape, K_AQUITARD_H), "k33": (z_top - z_bot) / c, "convertible": False}
        )
    return top, bottoms, props, node_layer


def _north_up(a: np.ndarray) -> np.ndarray:
    """App rows run south to north (row 0 at y = 0); MODFLOW 6 rows north to south. The same
    flip turns app arrays into MF6 arrays and back (last two axes: row, column)."""
    return a[..., ::-1, :]


def _pname(k: int, name: str) -> str:
    return f"b{k}_" + re.sub(r"[^a-z0-9]", "", name.lower())[:10]


# ---- MF6 input files -------------------------------------------------------------------------
def _num(v: float) -> str:
    return f"{float(v):.15g}"


def _one_array(a) -> str:
    """CONSTANT for a uniform array, else INTERNAL with 10 values a line."""
    a = np.asarray(a, dtype=float).ravel()
    if np.all(a == a[0]):
        return f"    CONSTANT {_num(a[0])}\n"
    text = [f"{v:.15g}" for v in a.tolist()]
    lines = (" ".join(text[i : i + 10]) for i in range(0, len(text), 10))
    return "    INTERNAL FACTOR 1.0\n" + "".join(f"      {line}\n" for line in lines)


def _array(name: str, values) -> str:
    return f"  {name}\n" + _one_array(values)


def _layered(name: str, values: Sequence) -> str:
    return f"  {name} LAYERED\n" + "".join(_one_array(a) for a in values)


def _block(name: str, body: str = "", suffix: str = "") -> str:
    return f"BEGIN {name}{suffix}\n{body}END {name}\n\n"


def _cellid(c: tuple[int, int, int]) -> str:
    return f"{c[0] + 1} {c[1] + 1} {c[2] + 1}"


def _list_package(rows: list[tuple], options: str = "") -> str:
    """Dimensions and period 1 of a list package; a row is ((lay, row, col) 0-based, *values)."""
    lines = "".join(f"  {_cellid(c)} " + " ".join(map(_num, vals)) + "\n" for c, *vals in rows)
    return (
        _block("options", options)
        + _block("dimensions", f"  MAXBOUND {len(rows)}\n")
        + _block("period", lines, " 1")
    )


@dataclass(frozen=True)
class Written:
    """What :func:`build` wrote, as needed to read the run back."""

    node_layer: list[int]  # MF6 layer of every app node
    names: dict[str, str]  # MF6 package name -> app group name
    fixed_t: np.ndarray  # (n_nodes, nrow, ncol), app rows: convertible cell held at fixed T
    fixed_t_groups: dict[str, int]  # group -> cells with its level below the cell bottom


def build(
    params: p3.Params,
    boundaries: list[HeadBoundary],
    fluxes: list[FluxBoundary],
    fixed: tuple[float | None, ...],
    workspace: Path,
    newton: bool = False,
    barriers: Sequence[Barrier] = (),
    fixed_cells: Sequence[FixedHeads] = (),
    flow_reduce: bool = False,
) -> Written:
    """Write the MF6 simulation for one run into ``workspace`` (the app's boundary groups,
    wells, fixed ring, CHD groups and walls; a CHD group replaces the ring where they meet).
    ``flow_reduce`` (Newton only): a well delivers less when its cell falls dry (WEL
    AUTO_FLOW_REDUCE), the actual rates go to ``<package>.afr.csv``.

    MF6 refuses a boundary level (GHB/DRN stage, RIV bottom, CHD head) below the bottom of a
    convertible cell; such a cell keeps a fixed transmissivity (ICELLTYPE 0 for that cell,
    choice of Job 2026-11-08) and is reported in the returned :class:`Written`."""
    nl, nr, nc = len(params.stack.nodes), params.nrow, params.ncol
    top, bottoms, props, node_layer = _layers(params)
    files: dict[str, str] = {}
    packages: list[tuple[str, str, str]] = []  # (ftype, file, pname)

    def cell(key: int) -> tuple[int, int, int]:
        n, r, col = np.unravel_index(key, (nl, nr, nc))
        return (node_layer[n], nr - 1 - int(r), int(col))

    icelltype = [np.full((nr, nc), int(p["convertible"])) for p in props]
    fixed_t_groups: dict[str, int] = {}

    def check_level(group: str, c: tuple[int, int, int], level: float) -> None:
        """A convertible cell whose boundary level lies below its bottom gets a fixed T."""
        lay, r, col = c
        if icelltype[lay][r, col] and level < bottoms[lay][r, col]:
            icelltype[lay][r, col] = 0
            fixed_t_groups[group] = fixed_t_groups.get(group, 0) + 1

    dvclose = 1e-5 if newton else 1e-7  # Newton with drying cells: the usual MF6 tolerance
    files["mfsim.nam"] = (
        _block("options")
        + _block("timing", "  TDIS6 gwt.tdis\n")
        + _block("models", "  gwf6 gwt.nam gwt\n")
        + _block("exchanges")
        + _block("solutiongroup", "  ims6 gwt.ims gwt\n", " 1")
    )
    files["gwt.tdis"] = (
        _block("options", "  TIME_UNITS days\n")
        + _block("dimensions", "  NPER 1\n")
        + _block("perioddata", "  1.0 1 1.0\n")
    )
    files["gwt.ims"] = (
        _block(
            "options",
            f"  COMPLEXITY {'COMPLEX' if newton else 'MODERATE'}\n"
            "  CSV_OUTER_OUTPUT FILEOUT outer.csv\n",
        )
        + _block("nonlinear", f"  OUTER_DVCLOSE {dvclose:g}\n  OUTER_MAXIMUM 500\n")
        + _block(
            "linear",
            f"  INNER_MAXIMUM 500\n  INNER_DVCLOSE {0.01 * dvclose:g}\n"
            f"  INNER_RCLOSE {1e-4 if newton else 1e-7:g}\n"
            f"  LINEAR_ACCELERATION {'BICGSTAB' if newton else 'CG'}\n",
        )
    )
    files["gwt.dis"] = (
        _block(
            "options",
            f"  LENGTH_UNITS meters\n  XORIGIN {_num(params.origin_x)}\n"
            f"  YORIGIN {_num(params.origin_y)}\n",
        )
        + _block("dimensions", f"  NLAY {len(bottoms)}\n  NROW {nr}\n  NCOL {nc}\n")
        + _block(
            "griddata",
            _array("delr", params.cell)
            + _array("delc", params.cell)
            + _array("top", top)
            + _layered("botm", bottoms),
        )  # fmt: skip
    )
    files["gwt.ic"] = _block("options") + _block("griddata", _array("strt", params.boundary_head))
    files["gwt.rcha"] = _block("options", "  READASARRAYS\n") + _block(
        "period", _array("recharge", params.recharge), " 1"
    )
    packages += [
        ("DIS6", "gwt.dis", "dis"), ("NPF6", "gwt.npf", "npf"), ("IC6", "gwt.ic", "ic"),
        ("RCH6", "gwt.rcha", "rch"),
    ]  # fmt: skip

    names: dict[str, str] = {}
    for k, b in enumerate(boundaries):
        pname = _pname(k, b.name)
        names[pname.upper()] = b.name
        rows = zip(b.keys, b.stage, b.conductance, b.bottom, strict=True)
        if b.kind == "riv":
            data = [(cell(key), s, C, bot) for key, s, C, bot in rows]
        else:
            data = [(cell(key), s, C) for key, s, C, _ in rows]
        for c, *vals in data:
            check_level(b.name, c, vals[-1] if b.kind == "riv" else vals[0])
        files[pname] = _list_package(data)
        packages.append(({"riv": "RIV6", "drn": "DRN6"}.get(b.kind, "GHB6"), pname, pname))
    for k, w in enumerate(fluxes, start=len(boundaries)):
        pname = _pname(k, w.name)
        names[pname.upper()] = w.name
        reduce = (
            f"  AUTO_FLOW_REDUCE {FLOW_REDUCE:g}\n  AUTO_FLOW_REDUCE_CSV FILEOUT {pname}.afr.csv\n"
            if flow_reduce and newton
            else ""
        )
        files[pname] = _list_package(
            [(cell(key), float(q)) for key, q in zip(w.keys, w.rate, strict=True)], reduce
        )
        packages.append(("WEL6", pname, pname))
    held = {int(key) for g in fixed_cells for key in g.keys}
    chd = [
        ((node_layer[n], r, col), head)
        for n, head in enumerate(fixed)
        if head is not None
        for r in range(nr)
        for col in range(nc)
        if (r in (0, nr - 1) or col in (0, nc - 1))
        and int(np.ravel_multi_index((n, nr - 1 - r, col), (nl, nr, nc))) not in held
    ]
    for c, head in chd:
        check_level("rand", c, head)
    if chd:
        files["gwt.chd"] = _list_package(chd)
        packages.append(("CHD6", "gwt.chd", "chd"))
    for k, g in enumerate(fixed_cells, start=len(boundaries) + len(fluxes)):
        pname = _pname(k, g.name)
        names[pname.upper()] = g.name
        data = [(cell(key), float(h)) for key, h in zip(g.keys, g.head, strict=True)]
        for c, head in data:
            check_level(g.name, c, head)
        files[pname] = _list_package(data)
        packages.append(("CHD6", pname, pname))
    hfb = []
    thin = params.stack.top_mode == "deklaag"
    for bar in barriers:
        for a, b, hc, ha, hb in zip(bar.a, bar.b, bar.hydchr, *bar.height, strict=True):
            if thin and bar.layer == 0:  # MF6 face height is the thin layer: same C_w
                hc = hc * 0.5 * (ha + hb) / THIN_TOP
            ca = cell(int(bar.layer * nr * nc + a))
            cb = cell(int(bar.layer * nr * nc + b))
            hfb.append(f"  {_cellid(ca)} {_cellid(cb)} {_num(hc)}\n")
    if hfb:
        files["gwt.hfb"] = (
            _block("options")
            + _block("dimensions", f"  MAXHFB {len(hfb)}\n")
            + _block("period", "".join(hfb), " 1")
        )
        packages.append(("HFB6", "gwt.hfb", "hfb"))
    files["gwt.npf"] = _block("options") + _block(
        "griddata",
        _layered("icelltype", icelltype)
        + _layered("k", [p["k"] for p in props])
        + _layered("k33", [p["k33"] for p in props]),
    )
    files["gwt.oc"] = _block(
        "options", "  HEAD FILEOUT gwt.hds\n  BUDGETCSV FILEOUT budget.csv\n"
    ) + _block("period", "  SAVE HEAD ALL\n", " 1")
    packages.append(("OC6", "gwt.oc", "oc"))
    files["gwt.nam"] = _block("options", "  NEWTON UNDER_RELAXATION\n" if newton else "") + _block(
        "packages", "".join(f"  {t} {f} {n}\n" for t, f, n in packages)
    )
    workspace.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (workspace / name).write_text(text, encoding="ascii")
    fixed_t = np.array(
        [(icelltype[lay] == 0) & bool(props[lay]["convertible"]) for lay in node_layer]
    )
    return Written(node_layer, names, _north_up(fixed_t), fixed_t_groups)


def _run(workspace: Path) -> tuple[bool, str]:
    """Run MF6 in ``workspace``: (normal termination, screen output)."""
    exe = _exe()
    if exe is None:
        raise RuntimeError(NOT_AVAILABLE)
    try:
        out = subprocess.run(
            [exe], cwd=workspace, capture_output=True, text=True, timeout=RUN_TIMEOUT,
            check=False, **_NO_WINDOW,
        ).stdout  # fmt: skip
    except subprocess.TimeoutExpired:
        return False, f"MODFLOW 6 duurde langer dan {RUN_TIMEOUT:.0f} s."
    return "normal termination" in out.lower(), out


def read_heads(path: Path, nrow: int, ncol: int) -> np.ndarray:
    """Heads (nlay, nrow, ncol) of the last time step in an MF6 binary head file (DIS grid):
    per layer a header (kstp, kper, pertim, totim, text, ncol, nrow, ilay) and the values."""
    record = np.dtype(
        [("kstp", "<i4"), ("kper", "<i4"), ("pertim", "<f8"), ("totim", "<f8"),
         ("text", "S16"), ("ncol", "<i4"), ("nrow", "<i4"), ("ilay", "<i4"),
         ("data", "<f8", (nrow * ncol,))]
    )  # fmt: skip
    records = np.fromfile(path, dtype=record)
    step = records[["kstp", "kper"]]
    last = records[step == step[-1]]
    return last["data"].reshape(len(last), nrow, ncol)


def _node_heads(workspace: Path, node_layer: list[int], nrow: int, ncol: int) -> np.ndarray:
    """Heads per app node (app row order) from the MF6 head file."""
    heads = _north_up(read_heads(workspace / "gwt.hds", nrow, ncol))
    return np.array([heads[layer] for layer in node_layer])


_BUDGET_COLUMN = re.compile(r"^[A-Z0-9-]+\((.+)\)_(IN|OUT)$")


def _fortran_float(text: str) -> float:
    """Also reads Fortran's exponent without E for |exponent| > 99, e.g. '1.5-100'."""
    try:
        return float(text)
    except ValueError:
        return float(re.sub(r"(\d)([+-]\d{3})$", r"\1E\2", text.strip()))


def _budget(csv: Path) -> dict[str, float]:
    """Rate per package name (in - out) [m3/d] at the last time of the MF6 budget CSV
    (OC BUDGETCSV: full precision, unlike the rounded table in the listing)."""
    header, *rows = csv.read_text().strip().splitlines()
    values = rows[-1].split(",")
    out: dict[str, float] = {}
    for column, value in zip(header.split(","), values, strict=True):
        if m := _BUDGET_COLUMN.match(column):
            sign = 1.0 if m.group(2) == "IN" else -1.0
            out[m.group(1)] = out.get(m.group(1), 0.0) + sign * _fortran_float(value)
    return out


def solve(
    params: p3.Params,
    boundaries: list[HeadBoundary],
    fluxes: list[FluxBoundary],
    fixed: tuple[float | None, ...],
    T: list[np.ndarray],
    convertible: list[Convertible | None],
    workspace: Path,
    barriers: Sequence[Barrier] = (),
    fixed_cells: Sequence[FixedHeads] = (),
) -> tuple[GridSolution, bool]:
    """One run with MODFLOW 6, as a :class:`GridSolution`. First the standard formulation;
    with dry cells or no convergence again with Newton; if that fails too and there are
    wells, Newton with wells that deliver less when their cell falls dry (AUTO_FLOW_REDUCE).
    Returns (solution, label of the attempt used); RuntimeError if MF6 fails."""
    nl, nr, nc = len(params.stack.nodes), params.nrow, params.ncol
    label = ""
    for k, attempt in enumerate(ATTEMPTS):
        label, newton, reduce = attempt
        if reduce and not fluxes:
            continue
        ws = workspace / f"poging{k + 1}"
        written = build(
            params, boundaries, fluxes, fixed, ws, newton, barriers, fixed_cells, reduce
        )
        ok, _ = _run(ws)
        if not ok:
            continue
        heads = _node_heads(ws, written.node_layer, nr, nc)
        if np.any(np.abs(heads) > 1e20):  # HDRY: cells switched off as dry
            continue
        break
    else:
        raise RuntimeError(
            "MODFLOW 6 vindt geen oplossing (ook niet met Newton). Controleer de invoer, "
            "bijvoorbeeld zeer grote debieten of peilen ver onder de onderkant van een laag."
        )
    rates_wel = {w.name: np.asarray(w.rate, float).copy() for w in fluxes}
    names = written.names
    for pname, group in names.items():
        csv = ws / f"{pname.lower()}.afr.csv"
        if group not in rates_wel:
            continue
        if csv.exists():
            for line in csv.read_text().strip().splitlines()[1:]:
                parts = line.split(",")
                rates_wel[group][int(parts[3]) - 1] = float(parts[6])

    rates = _budget(ws / "budget.csv")
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
    full = [np.broadcast_to(np.asarray(T[j], float), shape) for j in range(nl)]
    transmissivity = np.array(
        [
            full[j]
            if cv is None
            else np.where(written.fixed_t[j], full[j], np.asarray(cv.k) * cv.saturated(heads[j]))
            for j, cv in enumerate(conv)
        ]
    )
    dry = np.array(
        [
            np.zeros(shape, dtype=bool)
            if cv is None
            else np.broadcast_to(cv.dry(heads[j]), shape) & ~written.fixed_t[j]
            for j, cv in enumerate(conv)
        ]
    )
    solution = GridSolution(
        heads=heads, fluxes=fluxes_out, recharge=recharge, lateral=lateral,
        iterations=max(len(outer) - 1, 1), mass_balance_error=float(error), active=active,
        transmissivity=transmissivity, dry=dry,
        barrier_flow=barrier_flows(barriers, heads, transmissivity, conv, params.cell),
        well_rates=rates_wel, fixed_t_groups=written.fixed_t_groups,
    )  # fmt: skip
    return solution, label


def _build_stage(params: p3.Params, stage: str, workspace: Path, newton: bool = False) -> Written:
    """Write the MF6 simulation of the reference ("ref") or opzet ("new") with all app
    inputs."""
    params.validate()
    sub = p3.subsoil_helper(params)
    T, c = p3._node_fields(params)
    boundaries = p3._boundaries(params, sub, stage, p3._cell_conductances(params, sub))
    ring = p3._ring_ghb(params, T, c)
    if ring is not None:
        boundaries.append(ring)
    return build(
        params, boundaries, p3._wells(params, sub, stage), p3._fixed_head(params), workspace,
        newton, p3._barriers(params, stage), p3._fixed_cells(params, sub, stage),
    )  # fmt: skip


README_TEXT = """MODFLOW 6-model uit de Grondwatertoolbox
=========================================

Mappen: referentie/ (peilen voor de ingreep) en opzet/ (na de ingreep). Draai in elke map:

    mf6

(MODFLOW 6, https://www.usgs.gov/software/modflow-6-usgs-modular-hydrologic-model). De
stijghoogten staan daarna in gwt.hds, de waterbalans in gwt.lst. Dit zijn precies de
bestanden waarmee de app zelf rekent.

Lagen (van boven naar beneden):
{layers}

Eenheden: meter en dag. Rooster: {nrow} rijen x {ncol} kolommen, cel {cell:g} m; rij 1 is
de noordrand van de app-kaart (zoals gebruikelijk in MODFLOW 6). {origin}
Neerslag {recharge:g} m/d.
Watergangen (RIV), drains (DRN), GHB-groepen, putten (WEL) en CHD-groepen staan elk in een
eigen pakket, genoemd naar de groep in de app; de vaste modelrand staat in het pakket chd.
Damwanden staan in het HFB-pakket (hydraulische karakteristiek 1/c_w; 0 = ondoorlatend). De
conductances zijn door de app berekend (Ernst met kv/kh, celgrootte-correctie De Lange).
Transmissiviteit: {conv}.
"""


def _origin_text(params: p3.Params) -> str:
    if params.origin_x == 0.0 and params.origin_y == 0.0:
        return "Het model heeft geen locatie: XORIGIN/YORIGIN zijn 0 (lokale meters)."
    return (
        f"Linksonder (XORIGIN, YORIGIN in DIS) = RD x {params.origin_x:.0f}, y "
        f"{params.origin_y:.0f} m (EPSG:28992), zodat het model in GIS op zijn plek ligt."
    )


def model_zip(params: p3.Params) -> bytes:
    """Zip of the MF6 input files (reference and opzet) plus a short LEESMIJ.txt; only model
    input, no paths or other data of the user."""
    import io

    _, _, props, node_layer = _layers(params)
    labels = {layer: n.label for layer, n in zip(node_layer, params.stack.nodes, strict=True)}
    layers = "\n".join(
        f"  laag {i + 1}: {labels.get(i, 'scheidende laag (K33 = D/c)')}" for i in range(len(props))
    )
    text = README_TEXT.format(
        layers=layers, nrow=params.nrow, ncol=params.ncol, cell=params.cell,
        recharge=params.recharge, origin=_origin_text(params),
        conv="volgt de natte dikte (ICELLTYPE 1)" if params.convertible else "vast",
    )  # fmt: skip
    buffer = io.BytesIO()
    with (
        tempfile.TemporaryDirectory(prefix="gwt_zip_") as tmp,
        zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf,
    ):
        zf.writestr("LEESMIJ.txt", text)
        for stage, folder in (("ref", "referentie"), ("new", "opzet")):
            ws = Path(tmp) / folder
            _build_stage(params, stage, ws)
            for f in sorted(ws.iterdir()):
                zf.write(f, f"{folder}/{f.name}")
    return buffer.getvalue()


def run(params: p3.Params, stage: str, workspace: Path, newton: bool = False) -> np.ndarray:
    """Heads per app node (n_nodes, nrow, ncol) [m NAP] computed by MODFLOW 6 with one fixed
    formulation (for tests)."""
    written = _build_stage(params, stage, workspace, newton)
    ok, out = _run(workspace)
    if not ok:
        raise RuntimeError("MODFLOW 6 is niet geconvergeerd:\n" + out[-2000:])
    return _node_heads(workspace, written.node_layer, params.nrow, params.ncol)
