"""Minimal GeoPackage writer (OGC 12-128r18) with only the standard library: vector layers of
points, lines or polygons in one coordinate system, read directly by QGIS.

Geometries are stored as GeoPackageBinary: header 'GP', version 0, flags (little endian, xy
envelope), srs id and envelope, followed by little-endian WKB."""

from __future__ import annotations

import sqlite3
import struct
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

APPLICATION_ID = 0x47504B47  # "GPKG"
USER_VERSION = 10300  # GeoPackage 1.3
RD_SRS_ID = 28992
RD_WKT = (
    'PROJCS["Amersfoort / RD New",GEOGCS["Amersfoort",DATUM["Amersfoort",SPHEROID["Bessel 1841",'
    "6377397.155,299.1528128],TOWGS84[565.2369,50.0087,465.658,-0.406857,0.350733,-1.87035,"
    '4.0812]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
    'PROJECTION["Oblique_Stereographic"],PARAMETER["latitude_of_origin",52.1561605555556],'
    'PARAMETER["central_meridian",5.38763888888889],PARAMETER["scale_factor",0.9999079],'
    'PARAMETER["false_easting",155000],PARAMETER["false_northing",463000],UNIT["metre",1],'
    'AUTHORITY["EPSG","28992"]]'
)
WGS84_WKT = (
    'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
    'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433],AUTHORITY["EPSG","4326"]]'
)
WKB_TYPES = {"POINT": 1, "LINESTRING": 2, "POLYGON": 3}
SQL_TYPES = {float: "REAL", int: "INTEGER", str: "TEXT"}

Point = tuple[float, float]


@dataclass
class Layer:
    """One feature table: ``geometry`` POINT (a point), LINESTRING (list of points) or
    POLYGON (closed ring of points); ``fields`` name -> float, int or str."""

    name: str
    geometry: str
    fields: dict[str, type]
    features: list[tuple[Any, dict[str, Any]]] = field(default_factory=list)
    description: str = ""

    def add(self, geom: Any, **attrs: Any) -> None:
        self.features.append((geom, attrs))


def _points(geometry: str, geom: Any) -> list[Point]:
    return [geom] if geometry == "POINT" else list(geom)


def _wkb(geometry: str, geom: Any) -> bytes:
    head = struct.pack("<BI", 1, WKB_TYPES[geometry])
    if geometry == "POINT":
        return head + struct.pack("<2d", *geom)
    pts = [(float(x), float(y)) for x, y in geom]
    if geometry == "POLYGON":
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        head += struct.pack("<I", 1)  # one ring
    return head + struct.pack("<I", len(pts)) + b"".join(struct.pack("<2d", *p) for p in pts)


def geometry_blob(geometry: str, geom: Any, srs_id: int = RD_SRS_ID) -> bytes:
    xs, ys = zip(*_points(geometry, geom), strict=True)
    header = b"GP" + struct.pack("<BBi4d", 0, 0b011, srs_id, min(xs), max(xs), min(ys), max(ys))
    return header + _wkb(geometry, geom)


def _create_meta(db: sqlite3.Connection) -> None:
    db.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    db.execute(f"PRAGMA user_version = {USER_VERSION}")
    db.execute(
        "CREATE TABLE gpkg_spatial_ref_sys (srs_name TEXT NOT NULL, srs_id INTEGER PRIMARY KEY, "
        "organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL, "
        "definition TEXT NOT NULL, description TEXT)"
    )
    db.executemany(
        "INSERT INTO gpkg_spatial_ref_sys VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("Undefined cartesian SRS", -1, "NONE", -1, "undefined", None),
            ("Undefined geographic SRS", 0, "NONE", 0, "undefined", None),
            ("WGS 84 geodetic", 4326, "EPSG", 4326, WGS84_WKT, None),
            ("Amersfoort / RD New", RD_SRS_ID, "EPSG", RD_SRS_ID, RD_WKT, None),
        ],
    )
    db.execute(
        "CREATE TABLE gpkg_contents (table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT "
        "NULL, identifier TEXT UNIQUE, description TEXT DEFAULT '', last_change DATETIME NOT "
        "NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), min_x DOUBLE, min_y DOUBLE, "
        "max_x DOUBLE, max_y DOUBLE, srs_id INTEGER, CONSTRAINT fk_gc_r_srs_id FOREIGN KEY "
        "(srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id))"
    )
    db.execute(
        "CREATE TABLE gpkg_geometry_columns (table_name TEXT NOT NULL, column_name TEXT NOT "
        "NULL, geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL, z TINYINT NOT NULL, "
        "m TINYINT NOT NULL, CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name), "
        "CONSTRAINT fk_gc_tn FOREIGN KEY (table_name) REFERENCES gpkg_contents(table_name), "
        "CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys (srs_id))"
    )


def _write_layer(db: sqlite3.Connection, layer: Layer, srs_id: int) -> None:
    cols = "".join(f', "{n}" {SQL_TYPES[t]}' for n, t in layer.fields.items())
    db.execute(
        f'CREATE TABLE "{layer.name}" (fid INTEGER PRIMARY KEY AUTOINCREMENT, '
        f"geom {layer.geometry}{cols})"
    )
    names = list(layer.fields)
    marks = ", ".join("?" * (len(names) + 1))
    quoted = "".join(f', "{n}"' for n in names)
    db.executemany(
        f'INSERT INTO "{layer.name}" (geom{quoted}) VALUES ({marks})',
        [
            (geometry_blob(layer.geometry, geom, srs_id), *(attrs.get(n) for n in names))
            for geom, attrs in layer.features
        ],
    )
    pts = [p for geom, _ in layer.features for p in _points(layer.geometry, geom)]
    xs, ys = zip(*pts, strict=True)
    db.execute(
        "INSERT INTO gpkg_contents (table_name, data_type, identifier, description, min_x, "
        "min_y, max_x, max_y, srs_id) VALUES (?, 'features', ?, ?, ?, ?, ?, ?, ?)",
        (layer.name, layer.name, layer.description, min(xs), min(ys), max(xs), max(ys), srs_id),
    )
    db.execute(
        "INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', ?, ?, 0, 0)",
        (layer.name, layer.geometry, srs_id),
    )


def write_gpkg(layers: Sequence[Layer], srs_id: int = RD_SRS_ID) -> bytes:
    """The GeoPackage as bytes; layers without features are left out."""
    with tempfile.TemporaryDirectory(prefix="gwt_gpkg_") as tmp:
        path = Path(tmp) / "export.gpkg"
        db = sqlite3.connect(path)
        try:
            _create_meta(db)
            for layer in layers:
                if layer.features:
                    _write_layer(db, layer, srs_id)
            db.commit()
        finally:
            db.close()
        return path.read_bytes()
