"""Census TIGER/Line tract boundary download, for the notebook's Section 6
spatial map only.

Deliberately separate from ``download.py``/``build.py``: these are one-time
~30MB whole-state shapefiles that almost never change, not part of the
quarterly CPUC refresh cycle. ``pudo-build`` does not call this module, so
running the pipeline never requires a teammate to also pull tract boundaries.
Call ``load_combined_tract_boundaries()`` directly from the notebook.

**Why two vintages:** a single TIGER year is not enough. CPUC/Waymo's
``Tract`` GEOIDs mix tracts from before AND after the 2020 Census
redistricting - empirically, of the ~1,967 distinct tracts observed in
``AV_Incidents_Location`` across the study window, only 1,796 match current
(2020-vintage) boundaries and only 1,603 match the prior (2010-vintage)
boundaries used before that redistricting; the union of both vintages matches
all 1,967. Discovered while building the Section 6 join - see CLAUDE.md.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from .config import CENSUS_STATE_FIPS, CENSUS_TIGER_YEARS


def _tiger_url(year: int) -> str:
    return (
        f"https://www2.census.gov/geo/tiger/TIGER{year}/TRACT/"
        f"tl_{year}_{CENSUS_STATE_FIPS}_tract.zip"
    )


def ensure_tract_boundaries(dest_dir: Path, year: int) -> Path:
    """Download + unzip one CA TIGER/Line tract shapefile vintage if not
    already present. Idempotent - safe to call on every notebook run.

    Returns the path to the ``.shp`` file (its sibling ``.dbf``/``.shx``/
    ``.prj`` must stay alongside it; geopandas reads them together).
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = f"tl_{year}_{CENSUS_STATE_FIPS}_tract"
    shp = dest_dir / f"{stem}.shp"
    if shp.exists():
        return shp

    zip_path = dest_dir / f"{stem}.zip"
    if not zip_path.exists():
        resp = requests.get(_tiger_url(year), timeout=180)
        resp.raise_for_status()
        zip_path.write_bytes(resp.content)

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_dir)

    if not shp.exists():
        raise FileNotFoundError(
            f"Expected {shp.name} after extracting {zip_path.name}, not found"
        )
    return shp


def load_combined_tract_boundaries(dest_dir: Path) -> gpd.GeoDataFrame:
    """CA tract boundaries covering BOTH the 2020 and 2010 Census vintages,
    downloading each (idempotent) as needed.

    Returns a ``GeoDataFrame`` with ``GEOID`` + ``geometry`` (CRS EPSG:4326),
    one row per distinct GEOID across both vintages - the newer (2020)
    geometry is kept when a GEOID exists in both. See the module docstring
    for why this union is necessary rather than using one TIGER year.
    """
    frames = []
    for year in CENSUS_TIGER_YEARS:  # dict order = newest vintage first
        shp = ensure_tract_boundaries(dest_dir, year)
        frames.append(gpd.read_file(shp, columns=["GEOID"]).to_crs(4326))

    combined = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True), crs=frames[0].crs
    ).drop_duplicates(subset="GEOID", keep="first")
    return combined.reset_index(drop=True)
