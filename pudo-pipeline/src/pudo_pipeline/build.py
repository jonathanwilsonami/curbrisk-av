"""Utility script for Running the full pipeline and write parquet outputs.

Usage:
    pudo-build                 # download + extract + transform + write
    pudo-build --no-download   # use already-extracted data in data/extracted/
    PUDO_DATA_DIR=/some/path pudo-build   # relocate the data directory
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import polars as pl

from . import config
from .download import download_all, extract_all
from .transform import (
    build_summary,
    load_complaints,
    load_monthly,
    load_pudo_counts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build PUDO parquet datasets")
    parser.add_argument("--no-download", action="store_true",
                        help="skip network; use existing data/extracted/")
    args = parser.parse_args()

    data_dir = Path(os.environ.get("PUDO_DATA_DIR", config.DATA_DIR))
    raw = data_dir / "raw"
    extracted = data_dir / "extracted"
    out = data_dir / "parquet"
    out.mkdir(parents=True, exist_ok=True)

    if not args.no_download:
        download_all(raw)
        extract_all(raw, extracted)

    complaints = load_complaints(extracted)
    monthly = load_monthly(extracted)
    pudo_counts = load_pudo_counts(extracted)
    summary = build_summary(pudo_counts, monthly)

    complaints.write_parquet(out / "complaints.parquet")
    monthly.write_parquet(out / "monthly_activity.parquet")
    pudo_counts.write_parquet(out / "pudo_counts.parquet")
    summary.write_parquet(out / "pudo_summary.parquet")

    print(f"\n[done] wrote 4 parquet files to {out}/")
    with pl.Config(tbl_cols=-1, tbl_width_chars=200):
        print(summary)


if __name__ == "__main__":
    main()
