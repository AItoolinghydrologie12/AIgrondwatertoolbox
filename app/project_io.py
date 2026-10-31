"""Save and load a Grondwatertoolbox project as JSON: all inputs (widget values) and everything
drawn (watercourses, drainage, wells, GHB groups, zones). Works on a plain state mapping so
it can be tested without Streamlit."""

from __future__ import annotations

import json
import re
from collections.abc import MutableMapping
from typing import Any

APP_NAME = "Grondwatertoolbox"
PRODUCT = "peilopzet_grid"
VERSION = 1

# input widgets, without the "p3_" prefix
INPUT_PATTERNS = [
    r"(sx|sy|cell|thr|gc|mv|rch|wmv|dd|dw|bnd|bh|kwel|phi|ck|mode_top)",
    r"(bfix|bh)_[a-z0-9]+",
    r"naq_(deklaag|freatisch_zand)",
    r"(deklaag|freatisch_zand)_\d+_(d|k|c|kh|a)",
    r"z\d+_(name|mv)",
    r"z\d+_(deklaag|freatisch_zand)_\d+_\d+_(d|k|c|kh|a)",
]
OBJECTS = ("wcs", "drain", "wells", "ghb", "zones")


def _is_input(name: str, key: str) -> bool:
    if not name.startswith(f"{key}_"):
        return False
    rest = name[len(key) + 1 :]
    return any(re.fullmatch(p, rest) for p in INPUT_PATTERNS)


def _encode(value: Any) -> Any:
    """JSON-safe: sets and dicts with tuple keys become tagged lists, tuples become lists."""
    if isinstance(value, set):
        return {"__set__": [_encode(v) for v in sorted(value)]}
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            return {"__items__": [[_encode(k), _encode(v)] for k, v in value.items()]}
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_encode(v) for v in value]
    return value


def _decode(value: Any) -> Any:
    """Inverse of :func:`_encode`; lists of two numbers become (x, y) tuples (points)."""
    if isinstance(value, dict):
        if set(value) == {"__set__"}:
            return {_decode(v) for v in value["__set__"]}
        if set(value) == {"__items__"}:
            return {_decode(k): _decode(v) for k, v in value["__items__"]}
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        items = [_decode(v) for v in value]
        if len(items) == 2 and all(isinstance(v, int | float) for v in items):
            return tuple(items)
        return items
    return value


def export_project(state: MutableMapping[str, Any], key: str) -> dict[str, Any]:
    inputs = {name: value for name, value in state.items() if _is_input(name, key)}
    return {
        "app": APP_NAME,
        "product": PRODUCT,
        "versie": VERSION,
        "invoer": {name[len(key) + 1 :]: _encode(v) for name, v in sorted(inputs.items())},
        "objecten": {name: _encode(state.get(f"{key}_{name}", [])) for name in OBJECTS},
    }


def to_json(state: MutableMapping[str, Any], key: str) -> str:
    return json.dumps(export_project(state, key), indent=1, ensure_ascii=False)


def parse_project(text: str | bytes) -> tuple[dict[str, Any] | None, list[str]]:
    """Read and check a project file; returns (data, Dutch error messages)."""
    try:
        data = json.loads(text)
    except (ValueError, UnicodeDecodeError) as err:
        return None, [f"Het bestand is geen geldige JSON ({err})."]
    if not isinstance(data, dict) or data.get("product") != PRODUCT:
        return None, ["Dit is geen projectbestand van de Grondwatertoolbox."]
    version = data.get("versie")
    if not isinstance(version, int) or version > VERSION:
        return None, [
            f"Projectversie {version!r} wordt niet ondersteund (deze app leest t/m {VERSION})."
        ]
    errors = []
    if not isinstance(data.get("invoer"), dict) or not isinstance(data.get("objecten"), dict):
        errors.append("Het projectbestand mist 'invoer' of 'objecten'.")
    else:
        unknown = [k for k in data["invoer"] if not _is_input(f"x_{k}", "x")]
        if unknown:
            errors.append(f"Onbekende invoer in het projectbestand: {', '.join(unknown[:5])}.")
    return (None, errors) if errors else (data, [])


def apply_project(state: MutableMapping[str, Any], key: str, data: dict[str, Any]) -> None:
    """Replace all inputs and objects in ``state`` by those of a checked project."""
    for name in [n for n in state if _is_input(n, key)]:
        del state[name]
    for name, value in data["invoer"].items():
        decoded = _decode(value)
        state[f"{key}_{name}"] = decoded if not isinstance(decoded, tuple) else list(decoded)
    objects = {name: _decode(data["objecten"].get(name, [])) for name in OBJECTS}
    state[f"{key}_drain"] = set(objects["drain"]) if objects["drain"] else set()
    for name in ("wcs", "wells", "ghb", "zones"):
        state[f"{key}_{name}"] = [_fix_object(o) for o in objects[name]]
    state[f"{key}_line"] = []


def _fix_object(obj: dict[str, Any]) -> dict[str, Any]:
    """Restore the in-memory types of a drawn object (point sets, lines as lists)."""
    obj = dict(obj)
    if "points" in obj:
        obj["points"] = set(obj["points"]) if obj["points"] else set()
    for name in ("lines", "rel_lines"):
        if name in obj:
            obj[name] = [list(line) for line in obj[name]]
    if "manual" in obj and not isinstance(obj["manual"], dict):
        obj["manual"] = {}
    return obj
