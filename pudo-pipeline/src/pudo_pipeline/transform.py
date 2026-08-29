"""Stage 2: parse extracted CSVs into tidy Polars frames.

Outputs
-------
complaints    one row per reported incident, Y/N flags as booleans,
              tagged with period_id / year / quarter / company.
monthly       one row per (year, month) of fleet activity (trips, VMT, PMT).
summary       one row per reporting period: PUDO counts joined to exposure,
              with complaints-per-100k-VMT rates.

Notes
-----
* TCPID is the carrier's CPUC permit number - it identifies Waymo, not a
  record. There is no row-level join key across files; complaint counts and
  VMT join at the reporting-period level.
* TimeofIncident is redacted, so complaints carry period-level dates only
  (month is null). Month_Level has true Year/Month, so quarter columns there
  are real calendar quarters.
* Columns that are entirely "Redacted" or NULL are dropped; per-row
  "Redacted" strings in kept columns become nulls.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from .config import (
    COMPANY,
    COMPLAINT_FLAGS,
    COMPLAINT_KEEP,
    COLLISION_PUDO_FLAGS,
    MONTH_LEVEL_KEEP,
    PERIODS,
)


def _read_csv_str(path: Path) -> pl.DataFrame:
    """Read everything as strings; normalize 'Redacted'/'NULL' to null."""
    df = pl.read_csv(path, infer_schema_length=0, ignore_errors=True)
    return df.with_columns(
        pl.all().str.strip_chars().replace(["Redacted", "NULL", ""], None)
    )


def _flag_to_bool(col: str) -> pl.Expr:
    return (pl.col(col) == "Y").fill_null(False).alias(col)


def _period_meta(period_id: str) -> dict:
    months = PERIODS[period_id][1]
    last_year, last_month = months[-1]
    return {
        "period_id": period_id,
        "year": last_year,
        "quarter": (last_month - 1) // 3 + 1,  # quarter of period end
        "company": COMPANY,
    }


def load_complaints(extract_dir: Path) -> pl.DataFrame:
    frames = []
    for period_id in PERIODS:
        pdir = extract_dir / period_id
        if not pdir.exists():
            continue
        files = sorted(
            f
            for f in pdir.rglob("*.csv")
            if "complaint" in f.name.lower().replace("-", "_")
            and "part" in f.name.lower()
        )
        # Older periods may not split into parts - fall back to any
        # complaints file that actually has rows.
        if not files:
            files = [
                f
                for f in pdir.rglob("*.csv")
                if "complaint" in f.name.lower().replace("-", "_")
            ]
        for f in files:
            df = _read_csv_str(f)
            if df.height == 0:
                continue
            keep = [c for c in COMPLAINT_KEEP if c in df.columns]
            df = df.select(keep).with_columns(
                [
                    _flag_to_bool(c)
                    for c in COMPLAINT_FLAGS + COLLISION_PUDO_FLAGS
                    if c in df.columns
                ]
            )
            meta = _period_meta(period_id)
            df = df.with_columns(
                pl.lit(meta["period_id"]).alias("period_id"),
                pl.lit(meta["year"]).cast(pl.Int32).alias("year"),
                pl.lit(meta["quarter"]).cast(pl.Int8).alias("quarter"),
                pl.lit(None, dtype=pl.Int8).alias("month"),  # redacted at source
                pl.lit(meta["company"]).alias("company"),
                pl.lit(f.name).alias("source_file"),
            )
            frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No complaint CSVs found under {extract_dir}")
    out = pl.concat(frames, how="diagonal")
    # A collision during PUDO counts as a PUDO-related incident too
    pudo_collision_cols = [c for c in COLLISION_PUDO_FLAGS if c in out.columns]
    return out.with_columns(
        pl.any_horizontal(pudo_collision_cols).alias("CollisionPUDOAny")
    )


def load_monthly(extract_dir: Path) -> pl.DataFrame:
    frames = []
    for period_id in PERIODS:
        pdir = extract_dir / period_id
        if not pdir.exists():
            continue
        for f in sorted(pdir.rglob("*.csv")):
            if "month_level" not in f.name.lower().replace(" ", "_").replace("-", "_"):
                continue
            df = _read_csv_str(f)
            keep = [c for c in MONTH_LEVEL_KEEP if c in df.columns]
            df = df.select(keep).with_columns(
                pl.lit(period_id).alias("period_id"),
                pl.lit(COMPANY).alias("company"),
            )
            frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No Month_Level CSVs found under {extract_dir}")
    out = pl.concat(frames, how="diagonal")
    numeric = [c for c in MONTH_LEVEL_KEEP if c not in ("TCPID",)]
    out = out.with_columns(
        [pl.col(c).cast(pl.Float64, strict=False) for c in numeric]
    ).with_columns(
        pl.col("Year").cast(pl.Int32).alias("year"),
        pl.col("Month").cast(pl.Int8).alias("month"),
    )
    out = out.with_columns(
        ((pl.col("month") - 1) // 3 + 1).cast(pl.Int8).alias("quarter"),
        (
            pl.col("TotalVMTPeriod1").fill_null(0)
            + pl.col("TotalVMTPeriod2").fill_null(0)
            + pl.col("TotalVMTPeriod3").fill_null(0)
        ).alias("vmt_total"),
    )
    # Reports occasionally restate a month; keep the latest submission
    return (
        out.sort("period_id")
        .unique(subset=["year", "month"], keep="last")
        .drop(["Year", "Month"])
        .sort(["year", "month"])
    )


def build_summary(complaints: pl.DataFrame, monthly: pl.DataFrame) -> pl.DataFrame:
    counts = complaints.group_by("period_id").agg(
        pl.col("ComplaintPUDO").sum().alias("pudo_complaints"),
        pl.col("PUDOTravelLane").sum().alias("pudo_travel_lane"),
        pl.col("CollisionPUDOAny").sum().alias("pudo_collisions"),
        pl.len().alias("total_incident_rows"),
    )

    # Exposure: sum monthly VMT over the exact months each period covers
    month_map = pl.DataFrame(
        [
            {"period_id": pid, "year": y, "month": m}
            for pid, (_, months) in PERIODS.items()
            for (y, m) in months
        ]
    ).with_columns(pl.col("year").cast(pl.Int32), pl.col("month").cast(pl.Int8))
    exposure = (
        month_map.join(monthly, on=["year", "month"], how="left")
        .group_by("period_id")
        .agg(
            pl.col("vmt_total").sum().alias("vmt"),
            pl.col("TotalTrips").sum().alias("trips"),
            pl.len().alias("n_months"),
        )
    )

    # A period whose files are missing sums to 0.0, not null - make that
    # explicit so rates come out null instead of infinite.
    exposure = exposure.with_columns(
        pl.when(pl.col("vmt") > 0).then(pl.col("vmt")).otherwise(None).alias("vmt"),
        pl.when(pl.col("trips") > 0).then(pl.col("trips")).otherwise(None).alias("trips"),
    )

    meta = pl.DataFrame([_period_meta(p) for p in PERIODS])
    return (
        meta.join(counts, on="period_id", how="left")
        .join(exposure, on="period_id", how="left")
        .with_columns(
            (pl.col("pudo_complaints") / pl.col("vmt") * 100_000).alias(
                "pudo_per_100k_vmt"
            ),
            (pl.col("pudo_complaints") / pl.col("trips") * 100_000).alias(
                "pudo_per_100k_trips"
            ),
        )
        .sort("period_id")
    )
