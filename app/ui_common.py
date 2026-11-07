"""Shared Streamlit building blocks: inputs from parameter metadata, tabs, export."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from gwproducten.core.layers import (
    DEFAULT_ANISOTROPY,
    MAX_AQUIFERS,
    TOP_MODE_LABEL,
    TOP_MODES,
    Layer,
    LayerStack,
    build_layers,
)
from gwproducten.core.params import ParamError, meta
from gwproducten.products._layered import Subsoil

TABS = ["Resultaat", "Theorie", "Aannames", "Validatie", "Export"]

# generic layer values used when a layer is added or the top schematisation changes
_AQUIFER_DEFAULT = (20.0, 15.0, DEFAULT_ANISOTROPY)
_PHREATIC_SAND_DEFAULT = (5.0, 1.0, DEFAULT_ANISOTROPY)
_TOP_CLAY_DEFAULT = (3.0, 500.0, 0.05)
_AQUITARD_DEFAULT = (5.0, 2000.0, 0.01)


def page_header(product_meta: dict[str, Any]) -> None:
    st.set_page_config(page_title=product_meta["titel"], layout="wide")
    st.title(product_meta["titel"])
    st.caption(
        " · ".join(
            [product_meta["categorie"], product_meta["regime"], *product_meta["methoden_kort"]]
        )
    )


def number(
    cls: Any, name: str, default: Any, key: str, step: float | None = None, help: str | None = None
) -> Any:
    """Number input with label, unit, range and help from the dataclass metadata (``help``
    replaces the help of the metadata)."""
    m = meta(cls, name)
    label = f"{m['label']} [{m['unit']}]" if m["unit"] not in ("", "-") else m["label"]
    if isinstance(default, int) and not isinstance(default, bool):
        return st.number_input(
            label, value=default, min_value=m["min"], max_value=m["max"], step=1, key=key
        )
    return st.number_input(
        label,
        value=float(default),
        min_value=None if m["min"] is None else float(m["min"]),
        max_value=None if m["max"] is None else float(m["max"]),
        step=step,
        format="%g",
        help=help or m["help"] or None,
        key=key,
    )


def _layer_defaults(
    mode: str, stack: LayerStack
) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """Aquifer (D, k, kv/kh) and aquitard (D, c, k_h) values for up to MAX_AQUIFERS
    aquifers."""
    if stack.top_mode == mode:
        aq = [
            (lay.thickness, lay.k_h, lay.anisotropy) for lay in stack.layers if lay.kind == "pakket"
        ]
        at = [(lay.thickness, lay.c, lay.k_h) for lay in stack.layers if lay.kind == "scheidend"]
    elif mode == "deklaag":
        aq, at = [_AQUIFER_DEFAULT], [_TOP_CLAY_DEFAULT]
    else:
        aq, at = [_PHREATIC_SAND_DEFAULT, _AQUIFER_DEFAULT], [_TOP_CLAY_DEFAULT]
    aq += [_AQUIFER_DEFAULT] * (MAX_AQUIFERS - len(aq))
    at += [_AQUITARD_DEFAULT] * (MAX_AQUIFERS - len(at))
    return aq, at


def stack_inputs(
    defaults: Any,
    key: str,
    *,
    max_aquifers: int = MAX_AQUIFERS,
    with_w: bool = True,
    container: Any = None,
) -> tuple[LayerStack, float]:
    """Layer stack (top schematisation, number of aquifers, one card per layer) and w.

    ``defaults`` needs a ``stack`` (and ``w`` when ``with_w``); without w, NaN is returned.
    Inputs go to ``container`` (default: the sidebar).
    """
    box = st.sidebar if container is None else container
    with box:
        mode = st.radio(
            "Bovenkant",
            TOP_MODES,
            index=TOP_MODES.index(defaults.stack.top_mode),
            format_func=TOP_MODE_LABEL.get,
            horizontal=True,
            key=f"{key}_mode_top",
        )
        n_default = sum(lay.kind == "pakket" for lay in defaults.stack.layers)
        if defaults.stack.top_mode != mode:
            n_default = 1 if mode == "deklaag" else 2
        n = int(
            st.number_input(
                "Aantal watervoerende pakketten (incl. freatisch pakket)",
                1,
                max_aquifers,
                min(n_default, max_aquifers),
                1,
                key=f"{key}_naq_{mode}",
            )
        )
        w = number(Subsoil, "w", defaults.w, f"{key}_w", 10.0) if with_w else float("nan")
        aq_def, at_def = _layer_defaults(mode, defaults.stack)
        n_at = n if mode == "deklaag" else n - 1
        template = build_layers(mode, aq_def[:n], at_def[:n_at])
        aquifers, aquitards = _layer_cards(template, f"{key}_{mode}")
    return LayerStack(mode, build_layers(mode, aquifers, aquitards)), w


def zone_stack_inputs(base: LayerStack, key: str) -> LayerStack:
    """Layer values of a zone: the structure (top schematisation, number of layers) follows
    the base stack, whose values are the defaults. A new structure gives fresh inputs."""
    n = sum(lay.kind == "pakket" for lay in base.layers)
    aquifers, aquitards = _layer_cards(base.layers, f"{key}_{base.top_mode}_{n}")
    return LayerStack(base.top_mode, build_layers(base.top_mode, aquifers, aquitards))


def _layer_cards(
    template: tuple[Layer, ...], key: str
) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """One card per layer: thickness, k and kv/kh (aquifer) or c (aquitard; k_h for a clay
    top)."""
    aquifers: list[tuple[float, float, float]] = []
    aquitards: list[tuple[float, float, float]] = []
    for i, lay in enumerate(template):
        k = f"{key}_{i}"
        with st.container(border=True):
            kind = "watervoerend" if lay.kind == "pakket" else "scheidend"
            st.markdown(f"**{lay.name}** · {kind}")
            cols = st.columns(3)
            with cols[0]:
                d = number(Layer, "thickness", lay.thickness, f"{k}_d", 0.5)
            if lay.kind == "pakket":
                with cols[1]:
                    kh = number(Layer, "k_h", lay.k_h, f"{k}_k", 1.0)
                with cols[2]:
                    a = number(Layer, "anisotropy", lay.anisotropy, f"{k}_a", 0.05)
                aquifers.append((d, kh, a))
                st.caption(f"kD = {d * kh:.0f} m²/d · kv = {a * kh:.3g} m/d")
            else:
                with cols[1]:
                    c = number(Layer, "c", lay.c, f"{k}_c", 50.0)
                kh = lay.k_h
                if i == 0:  # the clay top layer carries the phreatic level
                    with cols[2]:
                        kh = number(Layer, "k_h", lay.k_h, f"{k}_kh", 0.01)
                aquitards.append((d, c, kh))
    return aquifers, aquitards


def show_param_error(err: ParamError) -> None:
    st.error("De invoer is niet geldig:\n\n" + "\n".join(f"- {m}" for m in err.messages))


def split_theory(markdown: str) -> tuple[str, str]:
    """Split theory.md into (theory, assumptions) at the 'Aannames' heading."""
    parts = re.split(r"(?m)^## Aannames.*$", markdown, maxsplit=1)
    if len(parts) == 1:
        return markdown, ""
    rest = parts[1]
    nxt = re.search(r"(?m)^## ", rest)
    assumptions = rest[: nxt.start()] if nxt else rest
    theory = parts[0] + (rest[nxt.start() :] if nxt else "")
    return theory, assumptions


def dict_table(
    d: dict[str, float], value_label: str = "Waarde", fmt: str = "{:.4g}"
) -> pd.DataFrame:
    return pd.DataFrame({"Grootheid": list(d), value_label: [fmt.format(v) for v in d.values()]})


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")


def csv_button(df: pd.DataFrame | Callable[[], pd.DataFrame], filename: str, label: str) -> None:
    """Download button whose CSV is only made on a click (Streamlit's deferred download):
    writing the full grid as text on every rerun took most of the page time. ``df`` may be
    a function that builds the table."""
    st.download_button(
        label,
        lambda: csv_bytes(df() if callable(df) else df),
        file_name=filename,
        mime="text/csv",
        on_click="ignore",
    )


def png_buttons(figures: dict[str, go.Figure], key: str) -> None:
    """PNG via kaleido (needs Chrome). The camera icon in each figure always works."""
    st.caption(
        "Elke figuur heeft ook een camera-icoon (rechtsboven) om direct als PNG op te slaan."
    )
    if not st.button("PNG-bestanden maken", key=f"{key}_png"):
        return
    for name, fig in figures.items():
        try:
            data = fig.to_image(format="png", width=1200, height=600, scale=2)
        except Exception as exc:  # kaleido/Chrome missing or failing
            st.warning(
                f"PNG-export via kaleido lukt niet ({type(exc).__name__}). Installeer Chrome "
                "of draai `plotly_get_chrome`, of gebruik het camera-icoon in de figuur."
            )
            return
        st.download_button(f"{name}.png", data, file_name=f"{name}.png", mime="image/png")
