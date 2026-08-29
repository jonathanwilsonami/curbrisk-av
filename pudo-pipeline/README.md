# pudo-pipeline

Reproducible pipeline for analyzing **PUDO (pick-up/drop-off) complaint risk**
in Waymo's CPUC AV Deployment quarterly reports, covering **2024 Q3 – 2026 Q2**.

Downloads the public report zips from the CPUC website, extracts the
Incidents-Complaints and Month-Level files, and compiles them into three
parquet datasets ready for analysis.

## Quick start

```bash
# pip
pip install -e .

# or conda
conda create -n pudo python=3.11 -y && conda activate pudo && pip install -e .

# run the pipeline (downloads ~8 report zips from cpuc.ca.gov)
pudo-build

# open the analysis notebook
jupyter lab notebooks/pudo_analysis.ipynb
```

Already have the zips? Drop them in `data/raw/` named `<period_id>.zip`
(see `src/pudo_pipeline/config.py` for period ids), or put extracted folders
under `data/extracted/<period_id>/`, then run `pudo-build --no-download`.

Set `PUDO_DATA_DIR=/some/path` to relocate the data directory.

## Outputs (`data/parquet/`)

| File | Grain | Contents |
|---|---|---|
| `complaints.parquet` | one row per reported incident | Y/N complaint & PUDO-collision flags as booleans; `period_id`, `year`, `quarter`, `company`, `source_file` |
| `monthly_activity.parquet` | one row per month | trips, VMT by period (P1 deadhead / P2 en-route / P3 passenger), `vmt_total`, PMT, passengers |
| `pudo_summary.parquet` | one row per reporting period | PUDO complaint/travel-lane/collision counts joined to VMT & trips, rates per 100k |

## Data notes & caveats

- **TCPID is the carrier permit ID** (identifies Waymo), not a record key —
  there is no row-level join across files. Complaints join to exposure at the
  reporting-period level.
- **Incident timestamps and locations are redacted** in the public files, so
  complaints carry period-level dates only (`month` is null). Monthly VMT has
  real Year/Month columns.
- **Reporting periods changed 2025-01-01** from Sep–Nov style quarters to
  calendar quarters. The 2024 portion of the window is covered by the
  Jun–Aug 2024, Sep–Nov 2024, and Dec 2024 (one-month stub) reports. The
  Poisson trend model in the notebook handles unequal exposure via the
  VMT offset.
- The Jun–Aug 2024 zip also contains Cruise files; the loader keeps whatever
  matches the complaint/month-level patterns — filter `company` or inspect
  `source_file` if you extend to multi-carrier analysis.
- Source page: CPUC AV Program Quarterly Reporting
  (cpuc.ca.gov → Licensing → Autonomous Vehicle Programs → Quarterly Reporting).
