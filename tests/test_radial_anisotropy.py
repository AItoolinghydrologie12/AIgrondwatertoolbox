"""Radial resistance in an anisotropic aquifer (z stretched by sqrt(kh/kv)) against the
x-z cross-section, which resolves the flow toward the ditch with kh and kv per cell."""

import pytest

from gwproducten.core.layers import LayerStack, build_layers
from gwproducten.products._layered import Ditch, Subsoil, run


def ratio(top: str, anisotropy: float, entry: float) -> float:
    """Analytic / x-z exchange for a 2 m wide ditch, 1 m water, in a 20 m aquifer."""
    if top == "deklaag":
        layers = build_layers("deklaag", [(20.0, 15.0, anisotropy)], [(1.0, 200.0, 0.01)])
    else:
        layers = build_layers("freatisch_zand", [(20.0, 15.0, anisotropy)], [])
    sub = Subsoil(stack=LayerStack(top, layers), w=100.0)
    d = Ditch(
        name="s", x=0.0, level=0.2, width=2.0, bottom_depth=2.0, water_depth=1.0,
        entry_resistance=entry,
    )  # fmt: skip
    r = run(sub, (d,), None)
    return float(r.analytic.Q_ditches[0] / r.xz.Q_ditches[0])


@pytest.mark.parametrize("top", ["deklaag", "freatisch_zand"])
@pytest.mark.parametrize("anisotropy", [1.0, 0.33, 0.1])
@pytest.mark.parametrize("entry", [1.0, 0.1])
def test_against_xz_cross_section(top: str, anisotropy: float, entry: float) -> None:
    """Measured: deklaag within 1 %, freatisch zand within 6 % (kv/kh = 0.1, c_wg = 0.1 d);
    the isotropic formula overestimated the exchange by up to 18 %."""
    assert 0.94 < ratio(top, anisotropy, entry) < 1.02
