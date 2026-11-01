"""Subsoil from REGIS II (TNO Geologische Dienst Nederland, BRO): the hydrogeological units
at one point (100 m grid) via the public OPeNDAP server of TNO, and a proposal for the app's
layer stack (aquifers and aquitards, at most four aquifers).

The proposal is a schematisation, not a fact: the user checks and edits it. Rules:

- unit type from the REGIS code: sand (z) and chalk (q) are aquifers; clay (k), complex (c),
  peat (v) and lignite (b) units are aquitards;
- an aquitard with c below ``c_min`` counts as part of the aquifer around it;
- consecutive units of one type are merged: kD = sum(kh d), c = sum(c) (or d / kv),
  kv/kh = harmonic over the aquifer units (``DEFAULT_ANISOTROPY`` where REGIS has no kv);
- below the last of ``max_aquifers`` aquifers the base is closed.

REGIS has no hydraulic values for the Holocene top layer (code ``hlc``); its resistance has
to be filled in by the user.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from gwproducten.core.layers import DEFAULT_ANISOTROPY
from gwproducten.geo.pdok import ServiceError, get

REGIS_URL = "https://www.dinodata.nl/opendap/REGIS/REGIS.nc.ascii"
SOURCE = "Ondergrond: REGIS II, TNO Geologische Dienst Nederland (BRO), via dinodata.nl"
N_UNITS = 132
GRID = 100.0  # m
X0, Y0 = 0.0, 300000.0  # lower-left corner of the REGIS grid (RD)
NX, NY = 2800, 3250
VARIABLES = ("top", "bottom", "kh", "kv", "c")
AQUIFER_CODES = ("z", "q")
NODATA = -9990.0
_VALUE = re.compile(r'(\w+)\.\1\[\1\.layer="([^"]+)"\]\[\1\.y=[-\d.]+\],\s*(\S+)')


@dataclass(frozen=True)
class Unit:
    code: str
    top: float  # m NAP
    bottom: float  # m NAP
    kh: float  # m/d, NaN if not given
    kv: float  # m/d, NaN if not given
    c: float  # d, NaN if not given

    @property
    def thickness(self) -> float:
        return self.top - self.bottom

    @property
    def is_aquifer(self) -> bool:
        match = re.search(r"([a-z])\d*$", self.code)
        return match is not None and match[1] in AQUIFER_CODES

    def resistance(self) -> float:
        """Vertical resistance [d]: c, else D / kv, else NaN."""
        if math.isfinite(self.c):
            return self.c
        if math.isfinite(self.kv) and self.kv > 0:
            return self.thickness / self.kv
        return math.nan


@dataclass(frozen=True)
class Group:
    """Merged units of one type."""

    aquifer: bool
    units: tuple[Unit, ...]
    thickness: float
    kh: float = math.nan  # aquifer
    anisotropy: float = math.nan  # aquifer
    c: float = math.nan  # aquitard; NaN if a unit has no value

    @property
    def codes(self) -> str:
        return ", ".join(u.code for u in self.units)


@dataclass
class Proposal:
    top_mode: str  # "deklaag" or "freatisch_zand"
    surface: float  # REGIS 'mv' [m NAP]
    layers: list[Group]  # from the top, alternating
    notes: list[str] = field(default_factory=list)

    @property
    def n_aquifers(self) -> int:
        return sum(g.aquifer for g in self.layers)


def grid_index(x: float, y: float) -> tuple[int, int]:
    """(ix, iy) of the REGIS cell holding the RD point; ServiceError outside REGIS."""
    ix, iy = int((x - X0) // GRID), int((y - Y0) // GRID)
    if not (0 <= ix < NX and 0 <= iy < NY):
        raise ServiceError("Deze locatie ligt buiten REGIS II (alleen Nederland).")
    return ix, iy


def query(ix: int, iy: int) -> str:
    sel = f"[0:1:{N_UNITS - 1}][{iy}:1:{iy}][{ix}:1:{ix}]"
    return ",".join(f"{v}{sel}" for v in VARIABLES)


def parse_ascii(text: str) -> tuple[float, list[Unit]]:
    """(surface, units present at the point from top to bottom) from an OPeNDAP ASCII reply."""
    values: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        match = _VALUE.match(line.strip())
        if match:
            value = float(match[3])
            values.setdefault(match[2], {})[match[1]] = value if value > NODATA else math.nan
    surface = values.pop("mv", {}).get("top", math.nan)
    units = [
        Unit(code, v["top"], v["bottom"], v.get("kh", math.nan), v.get("kv", math.nan),
             v.get("c", math.nan))  # fmt: skip
        for code, v in values.items()
        if math.isfinite(v.get("top", math.nan)) and math.isfinite(v.get("bottom", math.nan))
        and v["top"] > v["bottom"]
    ]  # fmt: skip
    units.sort(key=lambda u: -u.top)
    return surface, units


def fetch_point(x: float, y: float) -> tuple[float, list[Unit]]:
    """REGIS units at the RD point (x, y). Raises ServiceError."""
    ix, iy = grid_index(x, y)
    response = get(f"{REGIS_URL}?{query(ix, iy)}", {}, "De REGIS-server van TNO (dinodata.nl)")
    surface, units = parse_ascii(response.text)
    if not units:
        raise ServiceError("REGIS heeft op deze plek geen lagen (bijvoorbeeld buitendijks).")
    return surface, units


def _aquifer_group(units: list[Unit]) -> Group:
    d = sum(u.thickness for u in units)
    kd = sum(u.kh * u.thickness for u in units if math.isfinite(u.kh))
    kh = kd / d
    vertical = 0.0  # sum of d / kv
    for u in units:
        if math.isfinite(u.kv) and u.kv > 0:
            vertical += u.thickness / u.kv
        elif math.isfinite(u.kh) and u.kh > 0:
            vertical += u.thickness / (DEFAULT_ANISOTROPY * u.kh)
        elif math.isfinite(u.c):
            vertical += u.c
    anisotropy = (d / vertical) / kh if vertical > 0 and kh > 0 else DEFAULT_ANISOTROPY
    return Group(True, tuple(units), d, kh=kh, anisotropy=min(max(anisotropy, 0.001), 1.0))


def _aquitard_group(units: list[Unit]) -> Group:
    return Group(
        False, tuple(units), sum(u.thickness for u in units),
        c=sum(u.resistance() for u in units),  # NaN if any unit has no value
    )  # fmt: skip


def propose(
    surface: float, units: list[Unit], c_min: float = 5.0, max_aquifers: int = 4
) -> Proposal:
    """Layer stack proposal from REGIS units (see the module docstring for the rules)."""
    kinds = []
    for u in units:
        thin = not u.is_aquifer and math.isfinite(u.resistance()) and u.resistance() < c_min
        kinds.append(u.is_aquifer or thin)
    runs: list[tuple[bool, list[Unit]]] = []
    for u, aquifer in zip(units, kinds, strict=True):
        if runs and runs[-1][0] == aquifer:
            runs[-1][1].append(u)
        else:
            runs.append((aquifer, [u]))
    groups = [_aquifer_group(us) if aq else _aquitard_group(us) for aq, us in runs]
    groups = [g for g in groups if not (g.aquifer and g.kh <= 0)]
    notes: list[str] = []
    top_mode = "freatisch_zand" if groups and groups[0].aquifer else "deklaag"
    kept: list[Group] = []
    n_aq = 0
    for g in groups:
        if g.aquifer:
            if n_aq == max_aquifers:
                break
            n_aq += 1
        kept.append(g)
    if kept and not kept[-1].aquifer:
        below = kept.pop()
        notes.append(
            f"Onder het laatste pakket ligt {below.codes} (c ≈ {below.c:.0f} d); de app rekent "
            "met een dichte basis. Gebruik eventueel kwel/wegzijging met deze weerstand."
        )
    if len(kept) < len(groups):
        deepest = kept[-1].units[-1].bottom if kept else surface
        notes.append(
            f"Dieper dan {deepest:.0f} m NAP is weggelaten (maximaal {max_aquifers} pakketten); "
            "de basis van het model is dicht."
        )
    thin = [u.code for u, aq in zip(units, kinds, strict=True) if aq and not u.is_aquifer]
    if thin:
        notes.append(
            f"Dunne kleilagen met c < {c_min:g} d tellen mee in het pakket: {', '.join(thin)}."
        )
    missing = [g.codes for g in kept if not g.aquifer and not math.isfinite(g.c)]
    if missing:
        notes.append(
            "REGIS geeft geen weerstand voor " + "; ".join(missing) + " (o.a. de Holocene "
            "deklaag 'hlc'): de huidige waarde in de app blijft staan; vul die zelf in."
        )
    return Proposal(top_mode, surface, kept, notes)
