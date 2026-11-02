"""GIS export (GeoPackage for QGIS): file structure, geometry encoding, contour lines on the
right level and place in RD, drawn objects, and the requirement of a location."""

import dataclasses
import sqlite3
import struct
from pathlib import Path

import numpy as np
import pytest
from scipy.interpolate import RegularGridInterpolator

from gwproducten.geo.gpkg import APPLICATION_ID, RD_SRS_ID, Layer, geometry_blob, write_gpkg
from gwproducten.products import peilopzet_grid as p3
from gwproducten.products.peilopzet_grid.gis import contour_levels, gis_layers, to_gpkg

ORIGIN = (136300.0, 455400.0)


def located(**kw) -> p3.Params:
    return dataclasses.replace(p3.Params(), origin_x=ORIGIN[0], origin_y=ORIGIN[1], **kw)


def open_gpkg(data: bytes, tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "x.gpkg"
    path.write_bytes(data)
    return sqlite3.connect(path)


def read_blob(blob: bytes) -> tuple[int, int, list[tuple[float, float]]]:
    """(srs id, WKB type, points) of a GeoPackageBinary geometry."""
    assert blob[:2] == b"GP" and blob[2] == 0 and blob[3] == 0b011
    srs = struct.unpack("<i", blob[4:8])[0]
    wkb = blob[8 + 32 :]
    order, kind = struct.unpack("<BI", wkb[:5])
    assert order == 1
    if kind == 1:
        return srs, kind, [struct.unpack("<2d", wkb[5:21])]
    off = 5 + (4 if kind == 3 else 0)
    n = struct.unpack("<I", wkb[off : off + 4])[0]
    pts = [struct.unpack("<2d", wkb[off + 4 + 16 * i : off + 20 + 16 * i]) for i in range(n)]
    return srs, kind, pts


def test_geopackage_structure_and_geometries(tmp_path: Path) -> None:
    pts = Layer("punten", "POINT", {"naam": str, "q": float})
    pts.add((155000.0, 463000.0), naam="a", q=1.5)
    poly = Layer("vlakken", "POLYGON", {"n": int})
    poly.add([(0.0, 0.0), (10.0, 0.0), (10.0, 5.0)], n=3)  # ring closed by the writer
    empty = Layer("leeg", "LINESTRING", {})
    db = open_gpkg(write_gpkg([pts, poly, empty]), tmp_path)
    assert db.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
    assert db.execute(
        "SELECT organization_coordsys_id FROM gpkg_spatial_ref_sys WHERE srs_id = ?",
        (RD_SRS_ID,),
    ).fetchone() == (28992,)
    tables = dict(db.execute("SELECT table_name, geometry_type_name FROM gpkg_geometry_columns"))
    assert tables == {"punten": "POINT", "vlakken": "POLYGON"}  # empty layer left out
    blob, naam, q = db.execute("SELECT geom, naam, q FROM punten").fetchone()
    assert (naam, q) == ("a", 1.5)
    assert read_blob(blob) == (RD_SRS_ID, 1, [(155000.0, 463000.0)])
    srs, kind, ring = read_blob(db.execute("SELECT geom FROM vlakken").fetchone()[0])
    assert kind == 3 and ring[0] == ring[-1] and len(ring) == 4
    extent = db.execute(
        "SELECT min_x, max_x, max_y FROM gpkg_contents WHERE table_name = 'vlakken'"
    )
    assert extent.fetchone() == (0.0, 10.0, 5.0)
    header = struct.unpack("<4d", geometry_blob("POLYGON", ring)[8:40])
    assert header == (0.0, 10.0, 0.0, 5.0)  # envelope min x, max x, min y, max y


def test_contour_levels_multiples_of_the_limit() -> None:
    z = np.array([[-0.12, 0.0], [0.07, 0.31]])
    assert contour_levels(z, 0.05) == [-0.1, -0.05, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3]
    big = contour_levels(np.array([[0.0, 9.9]]), 0.05)  # 198 multiples -> step 0.25 m
    assert len(big) <= 40 and big[0] == pytest.approx(0.25)


def test_contours_lie_on_their_level_in_rd(tmp_path: Path) -> None:
    """Every vertex of an exported contour has the rise of its level (linear interpolation
    between cell centres, as the contouring), shifted by the RD origin; the 5 cm lines are
    marked as limit."""
    par = located()
    res = p3.compute(par)
    db = open_gpkg(to_gpkg(par, res, {}), tmp_path)
    rows = db.execute("SELECT geom, laag, niveau_m, soort, grens FROM contouren_verhoging")
    rows = rows.fetchall()
    assert rows and {r[1] for r in rows} <= set(res.node_labels.values())
    labels = list(res.node_labels.values())
    for blob, laag, level, soort, grens in rows:
        interp = RegularGridInterpolator((res.y, res.x), res.rise[labels.index(laag)])
        _, _, pts = read_blob(blob)
        xy = np.array([(y - ORIGIN[1], x - ORIGIN[0]) for x, y in pts])
        assert interp(xy) == pytest.approx(level, abs=1e-9)
        assert soort == ("verhoging" if level > 0 else "verlaging")
        assert grens == (abs(level) == pytest.approx(par.threshold))
    assert any(r[4] == 1 for r in rows)


def test_drawn_objects_in_rd(tmp_path: Path) -> None:
    par = located(
        wells=(p3.WellGroup("bem", ((4, 7),), "pakket1", 0.0, 100.0),),
        chd=(p3.ChdGroup("plas", ((10, 10), (10, 11)), "freatisch", 3.8, 3.9),),
        walls=(p3.Wall("scherm", ((2, 3, "x"), (2, 3, "y")), "freatisch"),),
    )
    res = p3.compute(par)
    lines = {"Wetering": [[(100.0, 0.0), (100.0, 300.0)]]}
    db = open_gpkg(to_gpkg(par, res, lines), tmp_path)
    blob, q = db.execute("SELECT geom, q_opzet_m3d FROM putten").fetchone()
    assert q == 100.0
    assert read_blob(blob)[2] == [(ORIGIN[0] + 7.5 * 25.0, ORIGIN[1] + 4.5 * 25.0)]
    assert db.execute("SELECT count(*) FROM ghb_chd WHERE soort = 'CHD'").fetchone() == (2,)
    faces = [read_blob(b)[2] for (b,) in db.execute("SELECT geom FROM damwanden")]
    x, y = ORIGIN[0] + 4 * 25.0, ORIGIN[1] + 3 * 25.0  # north-east corner of cell (2, 3)
    assert faces == [[(x, y - 25.0), (x, y)], [(x - 25.0, y), (x, y)]]
    blob, naam = db.execute("SELECT geom, naam FROM watergangen").fetchone()
    assert naam == "Wetering" and read_blob(blob)[2][0] == (ORIGIN[0] + 100.0, ORIGIN[1])
    n_cells = sum(len(wc.cells) for wc in par.watercourses)
    assert db.execute("SELECT count(*) FROM watergang_cellen").fetchone() == (n_cells,)
    names = {layer.name for layer in gis_layers(par, res, lines)}
    assert {"modelgebied", "contouren_verhoging"} <= names


def test_export_needs_a_location() -> None:
    par = p3.Params()
    with pytest.raises(ValueError, match="locatie"):
        to_gpkg(par, p3.compute(par), {})
