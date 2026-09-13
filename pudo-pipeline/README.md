# pudo-pipeline

Reproducible pipeline for analyzing **PUDO (pick-up/drop-off) complaint risk**
in Waymo's CPUC AV Deployment quarterly reports, covering **2024 Q3 – 2026 Q2**
(8 quarters).

Downloads the public report zips from the CPUC website, extracts the
Incidents-Complaints and Month-Level files, and compiles them into five parquet
datasets ready for analysis.

## Quick start

```bash
# pip
pip install -e .

# or conda
conda create -n pudo python=3.11 -y && conda activate pudo && pip install -e .

# run the pipeline (downloads the report zips from cpuc.ca.gov)
pudo-build

# open the analysis notebook
notebooks/pudo_analysis.ipynb
```

Already have the zips? Drop them in `data/raw/` named `<period_id>.zip`
(see `PERIODS` in `src/pudo_pipeline/config.py` for the ids), or put extracted
folders under `data/extracted/<period_id>/`, then run `pudo-build --no-download`.

Set `PUDO_DATA_DIR=/some/path` to relocate the data directory.

## Reporting periods

The CPUC switched from Sep–Nov-style quarters to calendar quarters on
2025-01-01, so the raw reports are: Jun–Aug 2024, Sep–Nov 2024, a Dec 2024
one-month stub, then 2025Q1–2026Q2 — **9 raw periods**. `config.RAW_TO_ANALYSIS`
recombines Sep–Nov + Dec into a single `2024Q4` (Sep–Dec 2024), giving **8
analysis quarters** (`2024Q3`, `2024Q4`, `2025Q1` … `2026Q2`). Download and
extraction still use the 9 raw ids; only the analysis frames use the 8.
`2024Q3` (3 months) and `2024Q4` (4 months) have unequal exposure, absorbed by
the `log(exposure)` offset in the trend model.

## Exposure

The primary denominator is **trips** — one completed trip = one pick-up + one
drop-off = one PUDO opportunity, with no deadhead ambiguity. `vmt_total` is
kept as a secondary cross-check; the trend direction is the same under it.

The pipeline also computes VMT split by phase (`vmt_p1`/`vmt_p3`/`vmt_p1_p3`,
plus `deadhead_share`), but **the P1/P2/P3 phase labels are not confirmed by
CPUC** — none of the "Reference Key" data-dictionary files across any of the 9
reports define what "Period 1/2/3" mean; the deadhead/en-route/passenger
labels are a carried-over working guess. The notebook's analysis does not use
these phase-split columns for that reason.

## Outputs (`data/parquet/`)

Every per-period row carries `period_id` (`YYYYQn`, clean to sort/filter/join),
`period_label` (`"2024 Q3 (Jun–Aug)"` — 2024's names aren't calendar quarters),
`window_start` / `window_end` dates, and `is_calendar_quarter`.

| File | Grain | Contents |
|---|---|---|
| `complaints.parquet` | one row per ride (**2025Q1+ only** — the 2024 reports have no ride-level data) | Y/N complaint & PUDO-collision flags as booleans; `period_id`, `raw_period`, `year`, `quarter`, `company`, `source_file` |
| `monthly_activity.parquet` | one row per month | Waymo driverless trips, VMT by phase (P1 deadhead / P2 en-route / P3 passenger), `vmt_total`, PMT, passengers |
| `pudo_counts.parquet` | one row per analysis period | `pudo_complaints`, `pudo_collisions`, `all_complaints` (every complaint category), `ride_rows`, `pudo_travel_lane` (always null — redacted), `schema` (`aggregate` / `microdata`) |
| `pudo_summary.parquet` | one row per analysis period | `pudo_counts` joined to exposure. Five denominators — `trips` (primary), `vmt_total`, `vmt_p1` (deadhead→pickup), `vmt_p3` (passenger→dropoff), `vmt_p1_p3` — each with a `pudo_per_100k_*` rate, plus `deadhead_share`, `pudo_share_of_complaints`, `pudo_per_million_rides` |
| `final_pudo_summary.parquet` | one row per analysis period | the minimal hand-off: `period_label`, `trips`, `pudo_complaints` |

## Data notes & caveats

- **TCPID** identifies the carrier (Waymo `PSG0038152`), not a record — there is
  no row-level join across files; complaints join to exposure at the period
  level. The ride-level complaint exports also use a transposed variant
  `PSG0031852` on most rows; both are treated as Waymo (`config.WAYMO_TCPIDS`).
- **Two complaint-file schemas.** 2024 reports give a one-row *aggregate* table
  (integer `ComplaintsPUDO`); 2025Q1+ give *ride-level microdata* (~1M rows per
  part, Y/N `ComplaintPUDO` flag). `load_pudo_counts` handles both. Because the
  eras are collected differently, expect a possible level shift at the boundary.
- **Redaction.** Incident timestamps and locations are redacted, so complaints
  carry period-level dates only (`month` is null). `PUDOTravelLane` is redacted
  in every file — reported as null, never zero.
- **Month-Level numerics** are comma-grouped / quoted from 2024P4 on
  (`"354,124"`); the loader strips separators before casting and warns loudly if
  a whole column still fails to parse or an expected column is missing.
- **Filename drift.** The Month-Level file is variously `AV_Month-Level*.csv`,
  `AV_ Month_Level-Deployment.csv`, or (2026Q1/Q2) `AV_Month_Part0*.csv`; the
  loader matches all of these and ignores the `Monthly_Tract` file.
- The Jun–Aug 2024 zip also contains **Cruise** files and every report ships a
  header-only **Drivered** tree; both are filtered out by path + TCPID.
- Source page: CPUC AV Program Quarterly Reporting
  (cpuc.ca.gov → Licensing → Autonomous Vehicle Programs → Quarterly Reporting).
