"""RD New (EPSG:28992) <-> WGS84 latitude/longitude with the approximation series of
Schreutelkamp & Strang van Hees (2001, "Benaderingsformules voor de transformatie tussen RD-
en WGS84-kaartcoördinaten"). Accuracy about 1 m within the Netherlands: enough to place a
model on a web map, not meant for surveying."""

from __future__ import annotations

X0, Y0 = 155000.0, 463000.0  # Amersfoort [m]
PHI0, LAM0 = 52.15517440, 5.38720621  # Amersfoort [degrees]

# (p, q, coefficient): RD -> WGS84 in arc seconds, terms dX^p * dY^q (dX, dY in 100 km)
_K = (
    (0, 1, 3235.65389), (2, 0, -32.58297), (0, 2, -0.24750), (2, 1, -0.84978),
    (0, 3, -0.06550), (2, 2, -0.01709), (1, 0, -0.00738), (4, 0, 0.00530),
    (2, 3, -0.00039), (4, 1, 0.00033), (1, 1, -0.00012),
)  # fmt: skip
_L = (
    (1, 0, 5260.52916), (1, 1, 105.94684), (1, 2, 2.45656), (3, 0, -0.81885),
    (1, 3, 0.05594), (3, 1, -0.05607), (0, 1, 0.01199), (3, 2, -0.00256),
    (1, 4, 0.00128), (0, 2, 0.00022), (2, 0, -0.00022), (5, 0, 0.00026),
)  # fmt: skip
# WGS84 -> RD in metres, terms dphi^p * dlam^q (in units of 10 000 arc seconds)
_R = (
    (0, 1, 190094.945), (1, 1, -11832.228), (2, 1, -114.221), (0, 3, -32.391),
    (1, 0, -0.705), (3, 1, -2.340), (1, 3, -0.608), (0, 2, -0.008), (2, 3, 0.148),
)  # fmt: skip
_S = (
    (1, 0, 309056.544), (0, 2, 3638.893), (2, 0, 73.077), (1, 2, -157.984),
    (3, 0, 59.788), (0, 1, 0.433), (2, 2, -6.439), (1, 1, -0.032), (0, 4, 0.092),
    (1, 4, -0.054),
)  # fmt: skip


def rd_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """(latitude, longitude) in degrees of the RD point (x, y) [m]."""
    dx, dy = (x - X0) * 1e-5, (y - Y0) * 1e-5
    lat = PHI0 + sum(k * dx**p * dy**q for p, q, k in _K) / 3600.0
    lon = LAM0 + sum(k * dx**p * dy**q for p, q, k in _L) / 3600.0
    return lat, lon


def wgs84_to_rd(lat: float, lon: float) -> tuple[float, float]:
    """RD (x, y) [m] of the point (latitude, longitude) in degrees."""
    dphi, dlam = 0.36 * (lat - PHI0), 0.36 * (lon - LAM0)
    x = X0 + sum(r * dphi**p * dlam**q for p, q, r in _R)
    y = Y0 + sum(s * dphi**p * dlam**q for p, q, s in _S)
    return x, y
