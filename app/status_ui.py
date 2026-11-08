"""Status of the calculation shown next to the Bereken buttons."""

from __future__ import annotations

from typing import Any

import streamlit as st

from gwproducten.core.layers import LayerStack
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


DONE, TODO, OPTIONAL = ":material/check_circle:", ":material/radio_button_unchecked:", "–"


def progress_list(stack: LayerStack, grid: Any, located: bool, status: str) -> None:
    """Short checklist per step in the sidebar: what is filled in, what is still missing."""
    ss = st.session_state
    n_wc = len(ss.get("p3_wcs", []))
    others = {
        "putgroep": len(ss.get("p3_wells", [])), "vast peil (GHB)": len(ss.get("p3_ghb", [])),
        "vaste stijghoogte (CHD)": len(ss.get("p3_chd", [])),
        "damwand": len(ss.get("p3_walls", [])), "zone": len(ss.get("p3_zones", [])),
    }  # fmt: skip
    layers_ok = not stack.errors()
    lines = [
        f"{DONE if layers_ok else TODO} **Ondergrond**: "
        + (f"{len(stack.nodes)} rekenlagen, {grid.ncol} × {grid.nrow} cellen"
           if layers_ok else "lagen nog niet geldig"),
        f"{DONE if n_wc else TODO} **Tekenen**: "
        + (f"{n_wc} watergang{'en' if n_wc != 1 else ''}" if n_wc else "nog geen watergangen")
        + "".join(f", {n} {name}" for name, n in others.items() if n),
        f"{DONE if status == 'success' else TODO} **Resultaat**: "
        + {"success": "actueel", "warning": "hoort bij vorige invoer",
           "error": "niet berekend"}.get(status, "nog niet gerekend"),
        f"{OPTIONAL} Locatie: " + ("aan" if located else "uit (optioneel)"),
    ]  # fmt: skip
    st.markdown("  \n".join(lines))
