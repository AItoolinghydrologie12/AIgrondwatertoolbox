"""Parameter metadata (unit, label, range) and validation with Dutch messages."""

from __future__ import annotations

import dataclasses
import math
from typing import Any


class ParamError(ValueError):
    """Invalid input. ``messages`` holds Dutch, user-facing descriptions."""

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__("\n".join(messages))


def param(
    default: float,
    unit: str,
    label: str,
    vmin: float | None = None,
    vmax: float | None = None,
    *,
    min_inclusive: bool = True,
    help: str = "",
) -> Any:
    """Dataclass field with physical metadata."""
    return dataclasses.field(
        default=default,
        metadata={
            "unit": unit,
            "label": label,
            "min": vmin,
            "max": vmax,
            "min_inclusive": min_inclusive,
            "help": help,
        },
    )


def meta(cls_or_obj: Any, name: str) -> dict[str, Any]:
    """Metadata of a field declared with :func:`param`."""
    for f in dataclasses.fields(cls_or_obj):
        if f.name == name:
            return dict(f.metadata)
    raise KeyError(name)


def label_with_unit(cls_or_obj: Any, name: str) -> str:
    m = meta(cls_or_obj, name)
    return f"{m['label']} [{m['unit']}]" if m["unit"] else m["label"]


def range_errors(obj: Any, prefix: str = "") -> list[str]:
    """Check every :func:`param` field of a dataclass instance against its range."""
    errors: list[str] = []
    for f in dataclasses.fields(obj):
        if "unit" not in f.metadata:
            continue
        value = getattr(obj, f.name)
        m = f.metadata
        name = f"{prefix}{m['label']}"
        if not isinstance(value, int | float) or not math.isfinite(value):
            errors.append(f"{name}: waarde '{value}' is geen eindig getal.")
            continue
        lo, hi = m["min"], m["max"]
        if lo is not None:
            if m["min_inclusive"] and value < lo:
                errors.append(f"{name} moet minimaal {lo:g} {m['unit']} zijn (nu {value:g}).")
            elif not m["min_inclusive"] and value <= lo:
                errors.append(f"{name} moet groter zijn dan {lo:g} {m['unit']} (nu {value:g}).")
        if hi is not None and value > hi:
            errors.append(f"{name} mag maximaal {hi:g} {m['unit']} zijn (nu {value:g}).")
    return errors
