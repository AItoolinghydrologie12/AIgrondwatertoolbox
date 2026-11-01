"""Surface level from AHN (Actueel Hoogtebestand Nederland, digital terrain model 0.5 m, CC0)
via the PDOK WCS: sampled at a fifth of the cell size and reduced to a median per cell, so
ditches, paths and single buildings hardly affect a cell."""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np

from gwproducten.geo.pdok import Bbox, ServiceError, get

AHN_WCS = "https://service.pdok.nl/rws/ahn/wcs/v1_0"
COVERAGE = "dtm_05m"
SAMPLES = 5  # samples per cell side
SOURCE = "Maaiveld: AHN (Rijkswaterstaat/Het Waterschapshuis, CC0) via PDOK"
_TIEPOINT, _PIXEL_SCALE, _NODATA = 33922, 33550, 42113  # GeoTIFF tags


@dataclass(frozen=True)
class Stats:
    median: float
    p10: float
    p90: float
    coverage: float  # fraction of cells with AHN data


def wcs_params(bbox: Bbox, width: int, height: int) -> dict[str, object]:
    return {
        "service": "WCS", "version": "1.0.0", "request": "GetCoverage", "coverage": COVERAGE,
        "crs": "EPSG:28992", "bbox": ",".join(f"{v:.2f}" for v in bbox), "width": width,
        "height": height, "format": "image/tiff",
    }  # fmt: skip


def read_tiff(data: bytes, bbox: Bbox) -> np.ndarray:
    """Float raster (rows north to south) with NaN for no data; checks that the image
    starts at the upper-left corner of ``bbox``."""
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(data))
        raster = np.array(image, dtype=float)
    except OSError as err:
        raise ServiceError("De AHN-dienst gaf geen hoogtekaart terug.") from err
    tie = image.tag_v2.get(_TIEPOINT)
    if tie is not None and (abs(tie[3] - bbox[0]) > 1.0 or abs(tie[4] - bbox[3]) > 1.0):
        raise ServiceError("De AHN-hoogtekaart ligt niet op het modelgebied.")
    nodata = image.tag_v2.get(_NODATA)
    if nodata is not None:
        raster[np.isclose(raster, float(nodata), rtol=1e-6)] = np.nan
    raster[np.abs(raster) > 1e4] = np.nan  # any other fill value
    return raster


def cell_medians(raster: np.ndarray, nrow: int, ncol: int) -> np.ndarray:
    """Median per cell (rows south to north, like the app) of a raster with SAMPLES x
    SAMPLES values per cell (rows north to south); NaN where a cell has no data."""
    blocks = raster.reshape(nrow, SAMPLES, ncol, SAMPLES).transpose(0, 2, 1, 3)
    flat = blocks.reshape(nrow, ncol, SAMPLES * SAMPLES)
    out = np.full((nrow, ncol), np.nan)
    has = ~np.all(np.isnan(flat), axis=2)
    out[has] = np.nanmedian(flat[has], axis=1)
    return out[::-1]


def surface_per_cell(bbox: Bbox, nrow: int, ncol: int) -> np.ndarray:
    """AHN surface level [m NAP] per model cell (rows south to north). Raises ServiceError."""
    width, height = ncol * SAMPLES, nrow * SAMPLES
    data = get(AHN_WCS, wcs_params(bbox, width, height), "De AHN-dienst van PDOK").content
    raster = read_tiff(data, bbox)
    if raster.shape != (height, width):
        raise ServiceError("De AHN-hoogtekaart heeft een onverwachte grootte.")
    return cell_medians(raster, nrow, ncol)


def stats(values: np.ndarray) -> Stats | None:
    """Median and spread of cell values; None if no cell has data."""
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return None
    p10, med, p90 = np.percentile(finite, [10, 50, 90])
    return Stats(float(med), float(p10), float(p90), finite.size / values.size)
