"""Editable object tables (app/tables_ui.py): what is shown and what is written back."""

import pandas as pd
import pytest
import tables_ui
from tables_ui import DELETE, Col, object_table

LAYERS = {"freatisch": "Freatisch pakket", "pakket1": "Pakket 1"}
KINDS = {"riv": "watergang (RIV)", "drn": "drain (DRN)"}
COLS = [
    Col("name", "Naam", "text"),
    Col("kind", "Type", "select", choices=KINDS),
    Col("layer", "Laag", "layer"),
    Col("q", "Q [m³/d]"),
    Col("on", "Aan", "bool", default=True),
    Col("n", "Cellen", readonly=True, value=lambda k, o: 10 + k),
]


@pytest.fixture
def editor(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace st.data_editor: record the table shown, return an edited copy."""
    seen: dict = {}

    def fake(table: pd.DataFrame, **kwargs) -> pd.DataFrame:  # noqa: ANN003
        seen["shown"] = table.copy()
        seen["config"] = kwargs["column_config"]
        edited = table.copy()
        for (row, col), value in seen.get("edits", {}).items():
            edited.loc[row, col] = value
        return edited

    monkeypatch.setattr(tables_ui.st, "data_editor", fake)
    return seen


def items() -> list[dict]:
    return [
        {"name": "Sloot", "kind": "riv", "layer": "freatisch", "q": 1.0},
        {"name": "Greppel", "kind": "drn", "layer": "pakket1", "q": 2.0, "on": False},
    ]


def test_shows_labels_and_computed_columns(editor: dict) -> None:
    objs = items()
    object_table(objs, COLS, "k", state_key="s", labels=LAYERS)
    shown = editor["shown"]
    assert list(shown["Type"]) == ["watergang (RIV)", "drain (DRN)"]
    assert list(shown["Laag"]) == ["Freatisch pakket", "Pakket 1"]
    assert list(shown["Aan"]) == [True, False]  # default when missing
    assert list(shown["Cellen"]) == [10, 11]
    assert list(shown[DELETE]) == [False, False]


def test_writes_back_stored_values(editor: dict) -> None:
    objs = items()
    editor["edits"] = {
        (0, "Type"): "drain (DRN)", (0, "Laag"): "Pakket 1", (0, "Q [m³/d]"): 7.5,
        (1, "Naam"): "Greppel 2", (1, "Aan"): True,
    }  # fmt: skip
    object_table(objs, COLS, "k", state_key="s", labels=LAYERS)
    assert objs[0] == {"name": "Sloot", "kind": "drn", "layer": "pakket1", "q": 7.5, "on": True}
    assert objs[1]["name"] == "Greppel 2" and objs[1]["on"] is True
    assert isinstance(objs[0]["q"], float)
    assert "n" not in objs[0]  # read-only columns are not written


def test_unchanged_table_keeps_objects_equal(editor: dict) -> None:
    objs = items()
    before = [dict(o) for o in objs]
    object_table(objs, COLS, "k", state_key="s", labels=LAYERS)
    for b, o in zip(before, objs, strict=True):
        assert {k: o[k] for k in b} == b


def test_delete_removes_rows_with_undo(editor: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(tables_ui, "bump", lambda: calls.append("bump"))
    monkeypatch.setattr(tables_ui.st, "rerun", lambda: calls.append("rerun"))
    monkeypatch.setattr(tables_ui.st, "session_state", {})
    objs = items()
    editor["edits"] = {(0, DELETE): True}
    object_table(objs, COLS, "k", state_key="s", labels=LAYERS)
    assert [o["name"] for o in tables_ui.st.session_state["s"]] == ["Greppel"]
    assert calls == ["bump", "rerun"]
