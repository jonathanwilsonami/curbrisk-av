# CLAUDE.md — pudo-pipeline

## Working rules (read first)

1. **Discuss non-trivial changes before making them.** For anything beyond an
   obvious fix, diagnose and outline the approach first, then implement. Editing
   files under `src/` and `notebooks/` directly is fine once the approach is
   agreed.
2. **Do not dump large volumes of raw data.** When inspecting CSVs, read at
   most ~50 rows (`pl.read_csv(f, n_rows=50)` or `head -50`). Aggregate counts
   over full files are fine — printing thousands of records is not. If you
   genuinely need a full scan, say why first.
3. Prefer minimal, validated increments over rewrites. One fix, verified,
   then the next.
4. Polars, not pandas, for the pipeline. Pandas only inside the notebook for
   statsmodels interop.

## What this project is

Coursework risk analysis (grad AI-risk course). Question:

> Is the frequency of PUDO (pick-up/drop-off) complaints for Waymo robotaxis
> **rising or falling** over 2024 Q3 – 2026 Q2 (8 quarters), **controlling for
> exposure via miles driven**? Then place the risk on a likelihood × severity
> risk chart.

Data: Waymo's quarterly CPUC AV Deployment reports (public, redacted).
Source page: cpuc.ca.gov → Licensing → Autonomous Vehicle Programs →
Quarterly Reporting.

Deliverable is the answer + notebook plots, not a production system. But the
package should be runnable by teammates via `pip install -e . && pudo-build`.

## Repo layout

```
src/pudo_pipeline/
  config.py     CPUC zip URLs, raw period -> months, raw->analysis period map,
                column keeps, WAYMO_TCPIDS
  download.py   fetch + unzip (idempotent, handles nested zips)
  transform.py  CSV -> complaints / monthly / pudo_counts / summary frames
  build.py      `pudo-build` CLI, writes 5 parquet files
notebooks/pudo_analysis.ipynb   plots + Poisson/NB trend test
```

Outputs land in `data/parquet/` (override root with `PUDO_DATA_DIR`):
`complaints.parquet` (ride-level, 2025Q1+ only), `monthly_activity.parquet`,
`pudo_counts.parquet` (numerator + `all_complaints`, one row per analysis period),
`pudo_summary.parquet` (the full deliverable table), and
`final_pudo_summary.parquet` (just `period_label` / `trips` / `pudo_complaints`,
one row per period — the minimal hand-off).

Every period carries `period_id` (clean `YYYYQn` for sort/filter/join),
`period_label` (`"2024 Q3 (Jun-Aug)"` etc. — 2024's names are not calendar
quarters), `window_start` / `window_end` dates, and `is_calendar_quarter`
(false only for 2024Q3/2024Q4). Set in `config._period_meta`.

`pudo_summary` also carries five exposure denominators and a matching
`pudo_per_100k_*` rate for each (`vmt` / `vmt_total`, `vmt_p1`, `vmt_p3`,
`vmt_p1_p3`, `trips`), plus `deadhead_share` (= `vmt_p1 / vmt_total`) and
`pudo_share_of_complaints` (= `pudo_complaints / all_complaints`).
`build_summary` takes `(pudo_counts, monthly)`.

## Source data facts (established, don't re-derive)

- **`TCPID` is Waymo's CPUC carrier permit = `PSG0038152`.** BUT the ride-level
  `Incidents_Complaints` exports carry a transposed variant **`PSG0031852`** on
  most rows (some parts mix both). Both are Waymo — see `WAYMO_TCPIDS`. Cruise
  is `PSG0039080`. Not a join key; no row-level link between complaints and
  trips.
- **The "Incidents-Complaints" file has TWO schemas** — `transform.load_pudo_counts`
  detects and handles both, summing counts into analysis periods:
  - **2024 reports (2024P3/P4/P5): wide AGGREGATE** — 120 cols, ONE summed row.
    Numerator is the integer column **`ComplaintsPUDO`** (plural), collisions
    are **`CollisionsPUDOAll`**. `PUDOTravelLaneAll` present but redacted.
  - **2025Q1+: ride-level MICRODATA** — ~50 cols, millions of rows (~1.15–1.45 ×
    `TotalTrips`), Y/N flag **`ComplaintPUDO`** (singular). Numerator is the
    `Y` count. Also a **header-only stub** file (no `Part`) — skipped by the
    `height == 0` check.
  - Because the two eras are collected differently, watch for a level shift at
    the 2024/2025 boundary.
- **`PUDOTravelLane` / `PUDOTravelLaneAll` is redacted in every file of every
  period** (aggregate, microdata, and `Incidents_Location`). Reported as null,
  never zero. Not plotted in the notebook.
- **`TimeofIncident`, `IncLat`, `IncLong`, `IncidenceTract`, `IncidenceZip`,
  `VIN` are redacted.** Complaints are dated only to the reporting period of
  their file; `month` is null by design. No within-quarter timing/location.
- **`Month_Level` is the exposure source**: real `Year`/`Month`, `TotalTrips`,
  `TotalVMTPeriod1/2/3`, `TotalPMT`. (P1 = deadhead to pickup,
  P3 = passenger-carrying; PUDO events occur at the P1/P3 boundary.)
  `vmt_total = P1 + P2 + P3`.
- **Month_Level numerics are comma-grouped / quoted from 2024P4 on**
  (`"354,124"`, `"1,625,253.10"`). `_to_float` strips `,` and `$` before the
  cast; `_cast_numeric_with_warning` / `_warn_missing_columns` warn loudly if a
  whole non-empty column still nulls or an expected column is missing. Silent
  nulling here was the original blocker — keep those warnings.
- **Filenames vary a lot.** Month_Level appears as `AV_Month-Level.csv`,
  `AV_ Month_Level-Deployment.csv` (stray space), `..._AV_Month-Level_Part0.csv`,
  and — in **2026Q1/Q2** — `AV_Month_Part0-Deployment.csv` (no "Level"!).
  `_MONTH_LEVEL_RE` matches all of these and rejects `Monthly_Tract`.
- **Every report also ships a parallel header-only `Drivered` tree** (Waymo's CA
  drivered deployment has no data rows). Skipped by a `drivered` path check.
- **Reporting periods changed 2025-01-01** from Sep–Nov-style to calendar
  quarters. The 9 raw periods are Jun–Aug 2024, Sep–Nov 2024, a Dec 2024
  one-month stub, then 2025Q1–2026Q2. `RAW_TO_ANALYSIS` recombines Sep–Nov + Dec
  into a single **`2024Q4`** (Sep–Dec 2024), giving exactly 8 analysis quarters:
  `2024Q3` (3 months), `2024Q4` (4 months), `2025Q1`…`2026Q2`. Raw `PERIODS`
  and the download/extract paths keep the 9 original ids/dirs untouched — only
  the analysis layer is 8. The unequal month counts are absorbed by the
  `log(VMT)` offset in the trend model.
- The Jun–Aug 2024 zip also contains **Cruise** files (dir name `Cruise-...`,
  TCPID `PSG0039080`) — excluded by `_is_other_carrier` + the TCPID filter.
- **`pudo_collisions` vs `pudo_complaints`:** they measure different things and
  collisions can exceed complaints. The `CollisionPUDOAny` OR-of-flags total for
  2025Q2 (54) matches the independent `Incidents_Location.CollisionsPUDO` total,
  so the flag logic is sound.

## Analysis plan (implemented in the notebook)

1. Numerator: `pudo_counts.pudo_complaints` per analysis period (aggregate
   `ComplaintsPUDO` for 2024, microdata `ComplaintPUDO=="Y"` sum for 2025Q1+).
2. Denominator: five options, all summed from `monthly` over the covered months —
   `vmt_total` (assignment default), `vmt_p1` (deadhead→pickup, tightest PUDO
   proxy), `vmt_p3` (passenger→dropoff), `vmt_p1_p3`, and `trips` (one PU + one
   DO per trip; best-justified on first principles). Report VMT; note trips.
3. **§1 Baseline** — per-period rate with an exact Poisson 95% CI (χ² method,
   `scipy.stats.chi2`), the pooled rate (headline: ~0.31 / 100k mi), rate
   dispersion (CV, VMR), and PUDO's share of all complaint categories.
4. **§2 Distribution** — 3-panel counts/exposure/rate, rate with CI error bars,
   an indexed-to-100 chart, small multiples of the rate under each denominator,
   and the deadhead share over time.
5. **§3 Trend** — Poisson GLM `complaints ~ t`, `offset=log(exposure)`; auto-
   switch to Negative Binomial (alpha by MLE) when Pearson χ²/df > 1.5 (it's
   ≈5). Refit under every denominator; Mann-Kendall (`kendalltau`) as a
   distribution-free check; fitted-trend + CI ribbon overlay; leave-one-out and
   drop-2024Q3 sensitivity. Guard: raises if <4 periods have exposure.
6. Risk chart: high likelihood / low severity → medium "monitor and mitigate";
   direction arrow flat-to-down.

Current result: pooled baseline **0.31 PUDO complaints per 100k miles**
(417 over 137M mi; exact 95% CI 0.28–0.34) ≈ 1.97 per 100k trips. Exposure-
adjusted trend is **falling** — NB −12.7 %/quarter (95% CI −20 % to −5 %,
p≈0.001), total ≈ −61 % over the window; **negative under all five
denominators** (−8 % to −15 %/qtr) and Mann-Kendall τ negative under all
(though MK p only 0.06–0.72 — the series is non-monotone). BUT dropping the
2024Q3 launch-ramp quarter (rate 3.2× the rest) roughly halves the slope to
−5 %/qtr, p≈0.05; since 2025Q1 the rate is flat ~0.24–0.31 / 100k VMT. Framing
call for the write-up: "fell during scale-up, then plateaued."

## Known caveats for the write-up

- Complaints are self-reported by the carrier to its regulator; complaint rate
  measures *reported* PUDO problems, not underlying PUDO behavior. Reporting
  propensity may itself change over time.
- Waymo expanded to new cities during this window; a rate change may reflect
  new-market composition rather than degrading behavior.
- The 2024 reporting periods are not calendar quarters (`2024Q3` = Jun–Aug,
  `2024Q4` = Sep–Dec), and their numerators come from a one-row aggregate table
  rather than the 2025Q1+ ride-level microdata.
- Redaction prevents any within-quarter timing or location analysis, and the
  travel-lane PUDO signal is entirely redacted.
