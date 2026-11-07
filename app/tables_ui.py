"""Editable object tables (watercourses, well/GHB/CHD groups, walls): one pattern for all.

A table shows one row per drawn object; edits are written straight back into the object
dicts in the session, and ticking "Verwijderen" removes the object (with undo). Columns are
described once with :class:`Col`, including their display format and help text."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd
import streamlit as st
from toolbox_state import bump

DELETE = "Verwijderen"


@dataclass(frozen=True)
class Col:
    """One table column.

    ``kind``: "text", "number", "bool", "select" (``choices``: stored value -> shown text) or
    "layer" (stored as layer key, shown as the layer label of the table). ``value``: for a
    read-only column computed from (index, object); else the column shows ``obj[field]``
    (``default`` when missing).
    ``fmt``: printf-style display format of a number column (display only).
    """

    field: str
    label: str
    kind: str = "number"
    choices: dict[str, str] | None = None
    readonly: bool = False
    default: Any = None
    fmt: str | None = None
    help: str | None = None
    value: Callable[[int, dict[str, Any]], Any] | None = None
    image: bool = False


def _choices(col: Col, labels: dict[str, str]) -> dict[str, str] | None:
    """Stored value -> shown text of a choice column (None for other kinds)."""
    if col.kind == "layer":
        return labels
    return col.choices if col.kind == "select" else None


def _shown(col: Col, k: int, obj: dict[str, Any], labels: dict[str, str]) -> Any:
    if col.value is not None:
        return col.value(k, obj)
    v = obj.get(col.field, col.default)
    choices = _choices(col, labels)
    return choices.get(v, v) if choices else v


def _config(col: Col, labels: dict[str, str]) -> Any:
    if col.image:
        return st.column_config.ImageColumn(col.label, width="small", help=col.help)
    choices = _choices(col, labels)
    if choices is not None:
        return st.column_config.SelectboxColumn(
            col.label, options=list(choices.values()), help=col.help, required=True
        )
    if col.kind == "bool":
        return st.column_config.CheckboxColumn(col.label, help=col.help, disabled=col.readonly)
    if col.kind == "text":
        return st.column_config.TextColumn(col.label, help=col.help, disabled=col.readonly)
    return st.column_config.NumberColumn(
        col.label, help=col.help, disabled=col.readonly, format=col.fmt
    )


def object_table(
    items: list[dict[str, Any]],
    cols: list[Col],
    key: str,
    *,
    state_key: str,
    labels: dict[str, str] | None = None,
) -> None:
    """Editable table of ``items`` (the list in ``st.session_state[state_key]``). Changed
    values are written back into the dicts; ticked rows are removed (with a rerun)."""
    labels = labels or {}
    table = pd.DataFrame(
        [
            {**{c.label: _shown(c, k, obj, labels) for c in cols}, DELETE: False}
            for k, obj in enumerate(items)
        ]
    )
    config = {c.label: _config(c, labels) for c in cols}
    config[DELETE] = st.column_config.CheckboxColumn(
        DELETE, help="Vink aan om dit object te verwijderen (ongedaan te maken)."
    )
    edited = st.data_editor(table, hide_index=True, key=key, width="stretch", column_config=config)
    for obj, (_, row) in zip(items, edited.iterrows(), strict=True):
        for c in cols:
            if c.readonly or c.value is not None:
                continue
            v = row[c.label]
            choices = _choices(c, labels)
            if choices is not None:  # shown text -> stored value
                obj[c.field] = {t: s for s, t in choices.items()}.get(v, v)
            elif c.kind == "text":
                obj[c.field] = v
            elif c.kind == "bool":
                obj[c.field] = bool(v)
            else:
                obj[c.field] = float(v)
    if edited[DELETE].any():
        st.session_state[state_key] = [
            obj for obj, rm in zip(items, edited[DELETE], strict=True) if not rm
        ]
        bump()
        st.rerun()
