"""Status of the calculation shown next to the Bereken buttons."""

from __future__ import annotations

from typing import Any

from gwproducten.products import peilopzet_grid as p3


def calc_status(
    mf6_ok: bool,
    mf6_msg: str,
    calc_error: str | None,
    done: dict[str, Any] | None,
    current: p3.Params | None,
) -> tuple[str, str]:
    """(Streamlit message kind, text) for the current state of the calculation."""
    if not mf6_ok:
        return "error", mf6_msg
    if calc_error is not None:
        return "error", f"Niet berekend: {calc_error}"
    if done is None:
        return "info", "Nog niet gerekend. Teken de watergangen en klik op **Bereken**."
    if current != done["params"]:
        return (
            "warning",
            "De invoer is gewijzigd: het resultaat hoort bij de vorige berekening. "
            "Klik op **Bereken** om opnieuw te rekenen.",
        )
    return "success", f"Berekend in {done['res'].seconds:.1f} s."
