"""Watercourses as line boundaries: conductance with entry and radial resistance."""

from __future__ import annotations

import math

from scipy.special import ellipk, ellipkm1


def wetted_perimeter(width: float, depth: float) -> float:
    """Wetted perimeter u [m] of a rectangular profile: bottom width + two banks."""
    return width + 2.0 * depth


def radial_resistance(
    D: float, u: float, k: float, anisotropy: float = 1.0, horizontal: float = 0.0
) -> float:
    """Ernst-type radial resistance per metre ditch, ln(D/u)/(pi k) [d/m].

    Anisotropic layer (``anisotropy`` = kv/kh): stretching z by a = sqrt(kh/kv) gives an
    isotropic layer with k' = sqrt(kh kv), thickness a D and wetted perimeter
    u' = horizontal + a (u - horizontal), where ``horizontal`` is the bottom width (not
    stretched) and the rest of u are banks. Then R = ln(D'/u')/(pi k').

    Zero when the wetted perimeter is not small compared with the layer thickness
    (u' >= D'): then there is no flow contraction toward the ditch.
    """
    a = math.sqrt(1.0 / anisotropy)
    u_t = horizontal + a * (u - horizontal)
    return max(math.log(a * D / u_t), 0.0) / (math.pi * k / a)


def strip_resistance(width: float, thickness: float, k_h: float, k_v: float) -> float:
    """Resistance [d/m] between a strip of width ``width`` on top of an anisotropic layer of
    ``thickness`` and a constant head below it (the rest of the top closed).

    Conformal mapping of the (isotropically scaled) strip on a slab gives
    Q / (k' dh) = K(1 - p) / K(p), with p = exp(-pi b' / t), b' = b sqrt(kv/kh),
    k' = sqrt(kh kv) and K the complete elliptic integral (parameter form). For a wide
    strip this tends to t / (kv b), plain vertical flow.
    """
    if thickness <= 0:
        return 0.0
    k_eff = math.sqrt(k_h * k_v)
    p = math.exp(-math.pi * width * math.sqrt(k_v / k_h) / thickness)
    return float(ellipk(p) / (k_eff * ellipkm1(p)))  # ellipkm1(p) = K(1 - p), accurate for p -> 0


def ditch_conductance(
    c: float, u: float, D: float, k: float, anisotropy: float = 1.0, horizontal: float = 0.0
) -> float:
    """Conductance C' [m/d] of a line boundary: C' = 1 / (c/u + ln(D/u)/(pi k)).

    Inflow per metre ditch is C' * (ditch level - head in the layer) [m2/d].
    ``c`` = entry resistance [d], ``u`` = wetted perimeter [m], ``D`` = layer
    thickness [m], ``k`` = horizontal conductivity [m/d]; ``anisotropy`` and
    ``horizontal`` as in :func:`radial_resistance` (the entry term uses the real u).
    """
    resistance = c / u + radial_resistance(D, u, k, anisotropy, horizontal)
    return math.inf if resistance == 0.0 else 1.0 / resistance


def equivalent_length(T: float, conductance: float) -> float:
    """Extra flow length L* = kD / C' [m] that makes a leaky boundary an ideal one.

    With only entry resistance (C' = B/c) this is the familiar L* = kD c / B.
    """
    return 0.0 if math.isinf(conductance) else T / conductance


def mazure_discharge(T: float, leakage_factor: float, head_change: float) -> float:
    """Mazure: inflow [m2/d per m] from an ideal boundary into a semi-infinite leaky aquifer."""
    return T * head_change / leakage_factor
