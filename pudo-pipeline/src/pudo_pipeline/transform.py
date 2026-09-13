"""Stage 2: parse extracted CSVs into tidy Polars frames.

Outputs
-------
complaints    one row per ride (2025Q1+ microdata only), Y/N flags as booleans,
              tagged with analysis period_id / year / quarter / company. The
              2024 reports have no ride-level data (aggregate only) so they are
              absent here - use ``pudo_counts`` for their numerators.
monthly       one row per (year, month) of Waymo driverless fleet activity
              (trips, VMT, PMT).
pudo_counts   one row per analysis period: PUDO complaint / collision counts
              (+ ``all_complaints``), derived from whichever schema that
              period's files use.
summary       one row per analysis period: pudo_counts joined to exposure, with
              a rate per 100k under every denominator. ``build.py`` also writes
              ``final_pudo_summary`` = just ``period_label`` / ``trips`` /
              ``pudo_complaints``.

Notes
-----
* TCPID is the carrier's CPUC permit number - it identifies Waymo, not a
  record. Rows are filtered to ``WAYMO_TCPIDS`` (the correct ``PSG0038152`` plus
  the transposed ``PSG0031852`` that Waymo's complaint exports use) to drop the
  Cruise files bundled in the Jun-Aug 2024 zip. Files under a ``drivered`` path
  or a ``cruise`` path are skipped.
* Numeric fields are comma-grouped and sometimes currency-formatted from
  2024P4 onward (``"354,124"``, ``"1,625,253.10"``). ``_to_float`` strips ``,``
  and ``$`` before casting; a loud warning fires if a whole non-empty column
  still nulls out, or an expected column is missing.
* TimeofIncident is redacted, so complaints carry period-level dates only
  (month is null). Month_Level has true Year/Month.
* PUDOTravelLane is redacted in every file - reported as null, never zero.
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import polars as pl

from .config import (
    AGG_COMPLAINT_COLS,
    AGG_PUDO_COLLISIONS_COL,
    AGG_PUDO_COMPLAINTS_COL,
    ANALYSIS_PERIOD_META,
    ANALYSIS_PERIOD_MONTHS,
    COMPANY,
    COMPLAINT_FLAGS,
    COMPLAINT_KEEP,
    COLLISION_PUDO_FLAGS,
    INCIDENTS_LOCATION_KEEP,
    MONTH_LEVEL_KEEP,
    MONTH_LEVEL_VMT_COLS,
    MONTHLY_TRACT_KEEP,
    PERIODS,
    RAW_TO_ANALYSIS,
    WAYMO_TCPIDS,
)

_MONTH_LEVEL_RE = re.compile(r"month[_ -]?level|month[_ -]?part\d", re.IGNORECASE)
_MONTHLY_TRACT_RE = re.compile(r"monthly[_ -]?tract", re.IGNORECASE)
_INCIDENTS_LOCATION_RE = re.compile(r"incidents[_-]location", re.IGNORECASE)


def _normalize_tract_geoid(col: str = "Tract") -> pl.Expr:
    """CPUC's ``Tract`` is a 10-char unpadded GEOID (``6037139705``, i.e. a
    1-digit CA state code + 3-digit county + 6-digit tract). Zero-pad to the
    standard 11-char Census GEOID (``06037139705``) so it joins cleanly against
    TIGER/Line boundary data."""
    return pl.col(col).str.zfill(11).alias("tract_geoid")


def _read_csv_str(path: Path) -> pl.DataFrame:
    """Read everything as strings; normalize 'Redacted'/'NULL' to null."""
    df = pl.read_csv(path, infer_schema_length=0, ignore_errors=True)
    return df.with_columns(
        pl.all().str.strip_chars().replace(["Redacted", "NULL", "N/A", ""], None)
    )


def _to_float(col: str) -> pl.Expr:
    """Strip thousands separators / currency symbols, then cast to Float64."""
    return (
        pl.col(col)
        .str.replace_all(",", "")
        .str.replace_all(r"\$", "")
        .str.strip_chars()
        .cast(pl.Float64, strict=False)
        .alias(col)
    )


def _flag_to_bool(col: str) -> pl.Expr:
    return (pl.col(col) == "Y").fill_null(False).alias(col)


def _warn_missing_columns(path: Path, expected: list[str], present: list[str]) -> None:
    missing = [c for c in expected if c not in present]
    if missing:
        warnings.warn(
            f"{path.name}: expected column(s) absent: {missing}", stacklevel=3
        )


def _cast_numeric_with_warning(df: pl.DataFrame, cols: list[str], path: Path) -> pl.DataFrame:
    """Cast ``cols`` to float; warn loudly if a non-empty column nulls out."""
    had_values = {
        c: (df.height > 0 and df[c].null_count() < df.height)
        for c in cols
        if c in df.columns
    }
    df = df.with_columns([_to_float(c) for c in cols if c in df.columns])
    for c, had in had_values.items():
        if had and df[c].null_count() == df.height:
            warnings.warn(
                f"{path.name}: column {c!r} cast to ALL-NULL "
                f"(unparseable format - check for stray characters)",
                stacklevel=3,
            )
    return df


def _is_drivered(path: Path) -> bool:
    return "drivered" in str(path).lower()


def _is_other_carrier(path: Path) -> bool:
    """Cruise files are bundled in the Jun-Aug 2024 zip under a 'Cruise-...' dir."""
    return "cruise" in str(path).lower()


def _filter_waymo(df: pl.DataFrame) -> pl.DataFrame:
    if "TCPID" in df.columns:
        return df.filter(pl.col("TCPID").is_in(WAYMO_TCPIDS))
    return df


# --------------------------------------------------------------------------- #
# monthly activity / exposure
# --------------------------------------------------------------------------- #
def _month_level_files(pdir: Path) -> list[Path]:
    out = []
    for f in sorted(pdir.rglob("*.csv")):
        name = f.name
        if _MONTHLY_TRACT_RE.search(name):
            continue
        if not _MONTH_LEVEL_RE.search(name):
            continue
        if _is_drivered(f) or _is_other_carrier(f):
            continue
        out.append(f)
    return out


def load_monthly(extract_dir: Path) -> pl.DataFrame:
    frames = []
    for raw_period in PERIODS:
        pdir = extract_dir / raw_period
        if not pdir.exists():
            continue
        files = _month_level_files(pdir)
        if not files:
            warnings.warn(f"{raw_period}: no Month_Level file found", stacklevel=2)
        for f in files:
            df = _read_csv_str(f)
            if df.height == 0:
                continue
            _warn_missing_columns(f, MONTH_LEVEL_KEEP, df.columns)
            keep = [c for c in MONTH_LEVEL_KEEP if c in df.columns]
            df = _filter_waymo(df.select(keep))
            if df.height == 0:
                continue
            numeric = [c for c in keep if c != "TCPID"]
            df = _cast_numeric_with_warning(df, numeric, f)
            df = df.with_columns(
                pl.lit(raw_period).alias("period_id"),
                pl.lit(RAW_TO_ANALYSIS[raw_period]).alias("analysis_period"),
                pl.lit(COMPANY).alias("company"),
            )
            frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No Month_Level CSVs found under {extract_dir}")

    out = pl.concat(frames, how="diagonal")
    out = out.with_columns(
        pl.col("Year").cast(pl.Int32, strict=False).alias("year"),
        pl.col("Month").cast(pl.Int8, strict=False).alias("month"),
    )
    vmt_present = pl.any_horizontal(
        [pl.col(c).is_not_null() for c in MONTH_LEVEL_VMT_COLS]
    )
    out = out.with_columns(
        ((pl.col("month") - 1) // 3 + 1).cast(pl.Int8).alias("quarter"),
        pl.when(vmt_present)
        .then(pl.sum_horizontal([pl.col(c).fill_null(0) for c in MONTH_LEVEL_VMT_COLS]))
        .otherwise(None)
        .alias("vmt_total"),
    )
    # A month can be restated in a later report; keep the row that actually
    # carries VMT (and, among those, the latest submission by period_id).
    out = (
        out.with_columns(pl.col("vmt_total").is_not_null().alias("_has_vmt"))
        .sort(["year", "month", "_has_vmt", "period_id"])
        .unique(subset=["year", "month"], keep="last")
        .drop(["Year", "Month", "_has_vmt"])
        .sort(["year", "month"])
    )
    return out


# --------------------------------------------------------------------------- #
# tract-level PUDO collision counts (Section 6 spatial map)
# --------------------------------------------------------------------------- #
def _location_files(pdir: Path) -> list[Path]:
    return [
        f
        for f in sorted(pdir.rglob("*.csv"))
        if _INCIDENTS_LOCATION_RE.search(f.name)
        and not _is_drivered(f)
        and not _is_other_carrier(f)
    ]


def load_pudo_locations(extract_dir: Path) -> pl.DataFrame:
    """One row per (analysis period, tract): PUDO collision counts by Census
    tract, for the Section 6 spatial map.

    Handles a missing file per period gracefully (warns, skips - some periods
    may not ship this dataset). The file's own ``Year``/``Quarter`` columns are
    a filing tag, not the coverage window (see ``config`` module docstring) -
    rows are tagged with the same directory-based ``raw_period`` ->
    ``analysis_period`` map used everywhere else. ``PUDOTravelLane`` is always
    null (redacted at source in every file).
    """
    frames = []
    for raw_period in PERIODS:
        pdir = extract_dir / raw_period
        if not pdir.exists():
            continue
        files = _location_files(pdir)
        if not files:
            warnings.warn(
                f"{raw_period}: no AV_Incidents_Location file found - spatial "
                "coverage will have a gap for this period",
                stacklevel=2,
            )
            continue
        for f in files:
            df = _read_waymo(f)
            if df.height == 0:
                continue
            _warn_missing_columns(f, INCIDENTS_LOCATION_KEEP, df.columns)
            keep = [c for c in INCIDENTS_LOCATION_KEEP if c in df.columns]
            df = df.select(keep)
            if "Tract" not in df.columns:
                warnings.warn(f"{f.name}: no Tract column - skipped", stacklevel=2)
                continue
            numeric = [c for c in ("CollisionsAll", "CollisionsPUDO") if c in df.columns]
            df = _cast_numeric_with_warning(df, numeric, f)
            meta = ANALYSIS_PERIOD_META[RAW_TO_ANALYSIS[raw_period]]
            df = df.with_columns(
                _normalize_tract_geoid(),
                pl.lit(meta["period_id"]).alias("period_id"),
                pl.lit(meta["period_label"]).alias("period_label"),
                pl.lit(raw_period).alias("raw_period"),
            )
            frames.append(df)
    if not frames:
        raise FileNotFoundError(
            f"No AV_Incidents_Location CSVs found under {extract_dir}"
        )

    out = pl.concat(frames, how="diagonal")
    # A tract can in principle appear twice within one period (e.g. a stray
    # duplicate export) - sum rather than assume one row per tract per period.
    agg_exprs = []
    if "CollisionsPUDO" in out.columns:
        agg_exprs.append(pl.col("CollisionsPUDO").sum().alias("collisions_pudo"))
    if "CollisionsAll" in out.columns:
        agg_exprs.append(pl.col("CollisionsAll").sum().alias("collisions_all"))
    return (
        out.group_by(["period_id", "period_label", "tract_geoid"])
        .agg(*agg_exprs)
        .with_columns(
            pl.lit(None, dtype=pl.Int64).alias("pudo_travel_lane")  # redacted
        )
        .sort(["period_id", "tract_geoid"])
    )


# --------------------------------------------------------------------------- #
# tract-level exposure (Section 6 optional exposure-adjusted map)
# --------------------------------------------------------------------------- #
def _tract_files(pdir: Path) -> list[Path]:
    return [
        f
        for f in sorted(pdir.rglob("*.csv"))
        if _MONTHLY_TRACT_RE.search(f.name)
        and not _is_drivered(f)
        and not _is_other_carrier(f)
    ]


def load_tract_exposure(extract_dir: Path) -> pl.DataFrame:
    """One row per (analysis period, tract): trips starting or ending in that
    tract, for the optional exposure-adjusted map in Section 6.

    Unlike Incidents_Location, this file's own ``Year``/``Month`` ARE the real
    coverage months (same convention as Month_Level), so they are used
    directly rather than the directory-based period map. ``TripsStart`` /
    ``TripsEnd`` are fully redacted from 2025Q1 onward (see the config module
    docstring) - periods after 2024Q4 will have an all-null ``tract_trips``.
    """
    frames = []
    for raw_period in PERIODS:
        pdir = extract_dir / raw_period
        if not pdir.exists():
            continue
        files = _tract_files(pdir)
        if not files:
            warnings.warn(
                f"{raw_period}: no AV_Monthly_Tract file found", stacklevel=2
            )
            continue
        for f in files:
            df = _read_waymo(f)
            if df.height == 0:
                continue
            _warn_missing_columns(f, MONTHLY_TRACT_KEEP, df.columns)
            keep = [c for c in MONTHLY_TRACT_KEEP if c in df.columns]
            df = df.select(keep)
            if "Tract" not in df.columns:
                continue
            numeric = [c for c in ("TripsStart", "TripsEnd") if c in df.columns]
            df = _cast_numeric_with_warning(df, numeric, f)
            df = df.with_columns(
                _normalize_tract_geoid(),
                pl.col("Year").cast(pl.Int32, strict=False).alias("year"),
                pl.col("Month").cast(pl.Int8, strict=False).alias("month"),
                pl.lit(raw_period).alias("raw_period"),
            )
            frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No AV_Monthly_Tract CSVs found under {extract_dir}")

    out = pl.concat(frames, how="diagonal")
    trip_cols = [c for c in ("TripsStart", "TripsEnd") if c in out.columns]
    if trip_cols:
        # Track "is this row redacted" BEFORE the groupby-sum below: Polars'
        # sum() of an all-null group silently returns 0.0, not null, which
        # would erase the 2025Q1+ redaction signal if checked after summing.
        out = out.with_columns(
            pl.any_horizontal([pl.col(c).is_not_null() for c in trip_cols])
            .alias("_row_has_trips")
        )
        out = out.group_by(["year", "month", "tract_geoid"]).agg(
            *[pl.col(c).sum().alias(c) for c in trip_cols],
            pl.col("_row_has_trips").any().alias("_has_trips"),
        ).with_columns(
            pl.sum_horizontal([pl.col(c).fill_null(0) for c in trip_cols])
            .alias("_sum")
        ).with_columns(
            pl.when(pl.col("_has_trips")).then(pl.col("_sum")).otherwise(None)
            .alias("tract_trips")
        ).drop(["_sum", "_has_trips", *trip_cols])
    else:
        out = out.group_by(["year", "month", "tract_geoid"]).agg()
        out = out.with_columns(pl.lit(None, dtype=pl.Float64).alias("tract_trips"))

    # fold months into analysis periods, same map build_summary uses for VMT/trips
    month_map = pl.DataFrame(
        [
            {"period_id": pid, "year": y, "month": m}
            for pid, months in ANALYSIS_PERIOD_MONTHS.items()
            for (y, m) in months
        ]
    ).with_columns(pl.col("year").cast(pl.Int32), pl.col("month").cast(pl.Int8))

    return (
        month_map.join(out, on=["year", "month"], how="left")
        .group_by(["period_id", "tract_geoid"])
        .agg(
            pl.when(pl.col("tract_trips").is_not_null().any())
            .then(pl.col("tract_trips").sum())
            .otherwise(None)
            .alias("tract_trips")
        )
        .filter(pl.col("tract_geoid").is_not_null())
        .sort(["period_id", "tract_geoid"])
    )


# --------------------------------------------------------------------------- #
# complaint / collision counts (numerator)
# --------------------------------------------------------------------------- #
def _complaint_files(pdir: Path) -> list[Path]:
    return [
        f
        for f in sorted(pdir.rglob("*.csv"))
        if "complaint" in f.name.lower().replace("-", "_")
        and not _is_drivered(f)
        and not _is_other_carrier(f)
    ]


def _read_waymo(path: Path) -> pl.DataFrame:
    return _filter_waymo(_read_csv_str(path))


def _microdata_pudo_counts(df: pl.DataFrame) -> tuple[int, int, int, int]:
    """(pudo_complaints, pudo_collisions, all_complaints, ride_rows) for one frame."""
    complaints = int(df.select((pl.col("ComplaintPUDO") == "Y").sum()).item())
    coll_cols = [c for c in COLLISION_PUDO_FLAGS if c in df.columns]
    if coll_cols:
        collisions = int(
            df.select(
                pl.any_horizontal([pl.col(c) == "Y" for c in coll_cols]).sum()
            ).item()
        )
    else:
        collisions = 0
    cflags = [c for c in COMPLAINT_FLAGS if c in df.columns]
    if cflags:
        all_complaints = int(
            df.select(
                pl.sum_horizontal([(pl.col(c) == "Y").sum() for c in cflags]).alias("n")
            ).item()
        )
    else:
        all_complaints = 0
    return complaints, collisions, all_complaints, df.height


def load_pudo_counts(extract_dir: Path) -> pl.DataFrame:
    """One row per analysis period with PUDO complaint / collision counts.

    Handles both file schemas: the 2024 wide aggregate (one summed row,
    ``ComplaintsPUDO``) and the 2025Q1+ ride-level microdata (Y/N
    ``ComplaintPUDO`` flag). ``all_complaints`` is the total across every
    complaint category (Safety / PUDO / Accessibility / WAV / CustomerService /
    Other), for the "PUDO share of all complaints" view.
    ``pudo_travel_lane`` is always null (redacted).
    """
    recs = []
    for raw_period in PERIODS:
        pdir = extract_dir / raw_period
        if not pdir.exists():
            continue

        schema = None
        agg_frames: list[pl.DataFrame] = []
        m_complaints = m_collisions = m_all = m_rows = 0

        for f in _complaint_files(pdir):
            df = _read_waymo(f)
            if df.height == 0:
                continue
            if AGG_PUDO_COMPLAINTS_COL in df.columns:
                schema = "aggregate"
                agg_frames.append(df)
            elif "ComplaintPUDO" in df.columns:
                schema = "microdata"
                c, k, a, n = _microdata_pudo_counts(df)
                m_complaints += c
                m_collisions += k
                m_all += a
                m_rows += n
            else:
                warnings.warn(
                    f"{f.name}: no recognised PUDO complaint column - skipped",
                    stacklevel=2,
                )

        if schema is None:
            warnings.warn(
                f"{raw_period}: no usable Waymo complaint file found", stacklevel=2
            )
            continue

        if schema == "aggregate":
            agg = pl.concat(agg_frames, how="diagonal")
            _warn_missing_columns(
                pdir, [AGG_PUDO_COMPLAINTS_COL, AGG_PUDO_COLLISIONS_COL], agg.columns
            )
            complaints = int(
                agg.select(_to_float(AGG_PUDO_COMPLAINTS_COL)).sum().item() or 0
            )
            if AGG_PUDO_COLLISIONS_COL in agg.columns:
                collisions = agg.select(_to_float(AGG_PUDO_COLLISIONS_COL)).sum().item()
                collisions = None if collisions is None else int(collisions)
            else:
                collisions = None
            acols = [c for c in AGG_COMPLAINT_COLS if c in agg.columns]
            all_complaints = int(
                agg.select(
                    pl.sum_horizontal([_to_float(c) for c in acols])
                ).sum().item() or 0
            ) if acols else None
            ride_rows = None
        else:
            complaints = m_complaints
            collisions = m_collisions
            all_complaints = m_all
            ride_rows = m_rows

        recs.append(
            {
                "raw_period": raw_period,
                "period_id": RAW_TO_ANALYSIS[raw_period],
                "schema": schema,
                "pudo_complaints": complaints,
                "pudo_collisions": collisions,
                "all_complaints": all_complaints,
                "ride_rows": ride_rows,
            }
        )

    if not recs:
        raise FileNotFoundError(f"No complaint CSVs found under {extract_dir}")

    raw = pl.DataFrame(recs)
    counts = (
        raw.group_by("period_id")
        .agg(
            pl.col("pudo_complaints").sum(),
            pl.col("pudo_collisions").sum(),
            pl.col("all_complaints").sum().alias("all_complaints"),
            pl.col("ride_rows").sum().alias("ride_rows"),
            pl.col("schema").unique().sort().str.join("+").alias("schema"),
        )
        .with_columns(
            pl.lit(None, dtype=pl.Int64).alias("pudo_travel_lane"),  # redacted
            pl.when(pl.col("schema").str.contains("microdata"))
            .then(pl.col("ride_rows"))
            .otherwise(None)
            .alias("ride_rows"),
        )
        .sort("period_id")
    )
    labels = pl.DataFrame(
        [{"period_id": p, "period_label": m["period_label"]}
         for p, m in ANALYSIS_PERIOD_META.items()]
    )
    return labels.join(counts, on="period_id", how="right").sort("period_id")


# --------------------------------------------------------------------------- #
# complaints (ride-level, microdata periods only - for inspection / sampling)
# --------------------------------------------------------------------------- #
def load_complaints(extract_dir: Path) -> pl.DataFrame:
    frames = []
    for raw_period in PERIODS:
        pdir = extract_dir / raw_period
        if not pdir.exists():
            continue
        for f in _complaint_files(pdir):
            df = _read_waymo(f)
            if df.height == 0 or "ComplaintPUDO" not in df.columns:
                continue  # aggregate schema / header-only stub
            keep = [c for c in COMPLAINT_KEEP if c in df.columns]
            df = df.select(keep).with_columns(
                [
                    _flag_to_bool(c)
                    for c in COMPLAINT_FLAGS + COLLISION_PUDO_FLAGS
                    if c in df.columns
                ]
            )
            meta = ANALYSIS_PERIOD_META[RAW_TO_ANALYSIS[raw_period]]
            df = df.with_columns(
                pl.lit(meta["period_id"]).alias("period_id"),
                pl.lit(raw_period).alias("raw_period"),
                pl.lit(meta["year"]).cast(pl.Int32).alias("year"),
                pl.lit(meta["quarter"]).cast(pl.Int8).alias("quarter"),
                pl.lit(None, dtype=pl.Int8).alias("month"),  # redacted at source
                pl.lit(meta["company"]).alias("company"),
                pl.lit(f.name).alias("source_file"),
            )
            frames.append(df)
    if not frames:
        raise FileNotFoundError(
            f"No ride-level complaint CSVs found under {extract_dir}"
        )
    out = pl.concat(frames, how="diagonal")
    pudo_collision_cols = [c for c in COLLISION_PUDO_FLAGS if c in out.columns]
    return out.with_columns(
        pl.any_horizontal(pudo_collision_cols).alias("CollisionPUDOAny")
    )


# --------------------------------------------------------------------------- #
# period summary
# --------------------------------------------------------------------------- #
# Exposure denominators carried on the summary. Each gets a matching
# `pudo_per_100k_<name>` rate column. `vmt_total` = P1+P2+P3 (what the
# assignment asks for); `trips` is the most literal "one PUDO opportunity per
# trip" count; the phase splits let the trend be checked under a tighter PUDO
# proxy (P1 ends in a pickup, P3 ends in a dropoff).
EXPOSURE_COLS = ["vmt_total", "vmt_p1", "vmt_p2", "vmt_p3", "vmt_p1_p3", "trips"]


def build_summary(pudo_counts: pl.DataFrame, monthly: pl.DataFrame) -> pl.DataFrame:
    # Exposure: sum monthly VMT / trips over the exact months each analysis
    # period covers.
    month_map = pl.DataFrame(
        [
            {"period_id": pid, "year": y, "month": m}
            for pid, months in ANALYSIS_PERIOD_MONTHS.items()
            for (y, m) in months
        ]
    ).with_columns(pl.col("year").cast(pl.Int32), pl.col("month").cast(pl.Int8))

    exposure = (
        month_map.join(monthly, on=["year", "month"], how="left")
        .group_by("period_id")
        .agg(
            pl.col("vmt_total").sum().alias("vmt_total"),
            pl.col("TotalVMTPeriod1").sum().alias("vmt_p1"),
            pl.col("TotalVMTPeriod2").sum().alias("vmt_p2"),
            pl.col("TotalVMTPeriod3").sum().alias("vmt_p3"),
            pl.col("TotalTrips").sum().alias("trips"),
            pl.col("vmt_total").is_not_null().sum().alias("months_with_vmt"),
            pl.len().alias("n_months"),
        )
        .with_columns((pl.col("vmt_p1") + pl.col("vmt_p3")).alias("vmt_p1_p3"))
    )
    # A period whose monthly rows are all missing sums to 0.0, not null - make
    # that explicit so rates come out null instead of infinite / zero.
    exposure = exposure.with_columns(
        [
            pl.when(pl.col(c) > 0).then(pl.col(c)).otherwise(None).alias(c)
            for c in EXPOSURE_COLS
        ]
    )

    meta = pl.DataFrame(list(ANALYSIS_PERIOD_META.values()))
    # meta is the authority on labels; drop pudo_counts' copy to avoid a dup column
    counts = pudo_counts.drop("period_label", strict=False)
    return (
        meta.join(counts, on="period_id", how="left")
        .join(exposure, on="period_id", how="left")
        .with_columns(pl.col("vmt_total").alias("vmt"))  # back-compat alias
        .with_columns(
            [
                (pl.col("pudo_complaints") / pl.col(c) * 100_000).alias(
                    f"pudo_per_100k_{'vmt' if c == 'vmt_total' else c}"
                )
                for c in EXPOSURE_COLS
            ]
        )
        .with_columns(
            (pl.col("pudo_complaints") / pl.col("ride_rows") * 1_000_000).alias(
                "pudo_per_million_rides"
            ),
            (pl.col("vmt_p1") / pl.col("vmt_total")).alias("deadhead_share"),
            (pl.col("pudo_complaints") / pl.col("all_complaints")).alias(
                "pudo_share_of_complaints"
            ),
        )
        .sort("period_id")
    )
