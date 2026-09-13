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
   statsmodels/PyMC interop.

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

There is also a Quarto project site at `project-site/` (course paper +
write-up, published to GitHub Pages) — separate from the pipeline. `paper.qmd`
never has numbers pasted in by hand: the notebook's setup cell defines
`save_fig` / `save_table` / `save_text` helpers that write PNGs and captioned
Pandoc-table/prose `.md` fragments straight into `project-site/assets/`
(relative path from the notebook, no copying of the notebook itself), and
`paper.qmd` pulls them in as numbered, cross-referenceable figures/tables via
`![]()` and `{{< include >}}`. See `project-site/README.md` for the exact
workflow and the one gotcha (`{{< include >}}` must be alone on its source
line or Quarto silently drops it). This design exists because `{{< embed
notebook.ipynb#tag >}}` was tried first and rejected: Quarto refuses to
render/embed a notebook living outside its own project directory (errors
trying to clean up `_files/` dirs it doesn't own) — don't re-attempt embed
across the `pudo-pipeline/` ↔ `project-site/` boundary.

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

`pudo_summary` (the parquet, pipeline-side) still carries five exposure
denominators and a matching `pudo_per_100k_*` rate for each (`vmt` /
`vmt_total`, `vmt_p1`, `vmt_p3`, `vmt_p1_p3`, `trips`), plus `deadhead_share`
(= `vmt_p1 / vmt_total`) and `pudo_share_of_complaints`
(= `pudo_complaints / all_complaints`). `build_summary` takes
`(pudo_counts, monthly)`. **The notebook, however, only uses two of these**
(`trips` and `vmt_total`) — see the note below on the P1/P2/P3 columns.

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
  `TotalVMTPeriod1/2/3`, `TotalPMT`. `vmt_total = P1 + P2 + P3` is solid (just
  addition). **What P1/P2/P3 individually mean is NOT verified** — grepped every
  CPUC Reference Key file across all 9 reports for "Period" or "VMT" and got
  zero hits; the "P1 = deadhead to pickup, P3 = passenger-carrying" labels in
  `config.py`/`transform.py` comments are a carried-over working guess from an
  earlier session, not a confirmed CPUC definition. Consequence: the notebook
  now treats `trips` as the primary exposure measure (team decision — a trip
  count has no deadhead ambiguity) and keeps only `vmt_total` (not the phase
  splits) as a secondary cross-check; `vmt_p1`/`vmt_p3`/`vmt_p1_p3`/
  `deadhead_share` are computed in the pipeline but no longer used in the
  notebook's analysis.
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
2. Denominator: **trips** (primary — team decision; one PU + one DO per trip,
   no deadhead ambiguity) with **`vmt_total`** kept as the one secondary
   cross-check. The phase-split VMT variants (`vmt_p1`, `vmt_p3`, `vmt_p1_p3`)
   and `deadhead_share` are dropped from the notebook's analysis — see the
   P1/P2/P3 note above.
3. **§1 Baseline** — scoped to `final_pudo_summary`'s columns (`period_label`,
   `trips`, `pudo_complaints`). (1a) descriptive summary-stats table for
   counts / trips / rate — no VMT; (1b) count-model justification:
   index-of-dispersion test (D = (n-1)·VMR ≈ 41.5 ~ χ²₇, p<0.001 →
   overdispersed) + why exact Poisson CIs over Wald; (1c) per-period rate
   (trips) with an exact Poisson 95% CI (χ² method, `scipy.stats.chi2`) and
   the pooled rate (headline **1.97 / 100k trips**). Plus PUDO's share of all
   complaint categories (unaffected by the trips/VMT choice).
4. **§2 Distribution** — 3-panel counts/exposure(trips)/rate, rate with CI
   error bars, an indexed-to-100 chart (complaints/trips/rate), and a
   trips-vs-VMT-cross-check small-multiples panel. Deadhead-share panel
   removed (built entirely on the unverified `vmt_p1`).
5. **§3 Trend** — Poisson GLM `complaints ~ t`, `offset=log(exposure)`, fit on
   **trips**; auto-switch to Negative Binomial (alpha by MLE) when Pearson
   χ²/df > 1.5 (it's ≈5). Refit under the VMT-total cross-check; Mann-Kendall
   (`kendalltau`) as a distribution-free check; fitted-trend + CI ribbon
   overlay (trips); leave-one-out and drop-2024Q3 sensitivity (trips). Guard:
   raises if <4 periods have exposure. **§3b Bayesian NB check** (PyMC/NUTS,
   new dependency `pymc`/`arviz`): same model, weakly-informative priors
   (`b0 ~ Normal(log(pooled rate), 2)`, `b1 ~ Normal(0, 1)`,
   `alpha ~ Exponential(1)`), 4 chains × 2000 draws — reports the full
   posterior on the slope instead of a Wald CI built on an MLE-plugged-in
   dispersion, plus P(slope<0) and a prior-width sensitivity check (barely
   moves across 0.5–2.0 sigma on the slope prior — data-driven, not
   prior-driven). **Gotcha:** PyMC's `NegativeBinomial(mu, alpha)` parametrizes
   `variance = mu + mu²/alpha` (higher alpha = closer to Poisson) — the
   *inverse* sense from statsmodels' NB2 `alpha` (`variance = mu + alpha·mu²`,
   higher = more overdispersion). Never compare the two alphas numerically.
   Any future Bayesian addition should follow the same pattern established
   here: report R-hat + divergence count, and run a prior-sensitivity sweep
   before trusting the posterior.
6. **§5 Risk curve & matrix** — (already trips-based, unchanged) risk curve =
   PUDO event rate per 100k trips at each severity step (complaint 1.97 →
   collision 1.69 → VRU collision 0.05 → severe/fatal 0, upper bound 0.02);
   sets Impact = **Minor**. 5×5 Impact × Probability heat-map with
   `PROB_BASIS` toggle: `per_period` (P≥1 in a quarter ≈1 → Almost certain)
   and `bayesian` → **Medium**; `per_trip` (~1 in 51k) → Moderate → **Low**.
   Headline placement **Medium / monitor-and-mitigate**, direction arrow
   flat-to-down.

Current result: pooled baseline **1.97 PUDO complaints per 100k trips**
(417 over 21.2M trips; exact 95% CI 1.78–2.16). Exposure-adjusted trend is
**falling** — NB −10.7 %/quarter (95% CI −18 % to −3 %, p=0.007), total ≈ −55 %
over the window; **same direction under the VMT-total cross-check**
(−12.7 %/qtr, p=0.001) and Mann-Kendall τ negative under both (MK p 0.40–0.55
— the series is non-monotone). BUT dropping the 2024Q3 launch-ramp quarter
(rate 2.9× the rest) cuts the slope to −3.0 %/qtr, **p=0.25 — no longer
significant**; since 2025Q1 the rate is flat ~1.48–1.88 / 100k trips.
**Bayesian NB check agrees but is more cautious**: posterior median −10.9%/qtr,
95% credible interval [−25.2%, +7.6%] (crosses zero, unlike the frequentist
Wald CI), **P(rate is declining) = 90%** — insensitive to prior width (90.0–
90.6% across σ=0.5–2.0 on the slope prior). Framing call for the write-up:
"fell during scale-up, then plateaued — very likely, not certain."

Risk placement: **Medium** (Almost certain × Minor), monitor-and-mitigate,
arrow flat-to-down.

## Editing the notebook (tooling notes)

- **Read tool chokes on the executed notebook** once it has enough baked-in PNG
  outputs (~10+ figures pushes it over the 25k-token read limit), regardless of
  `offset`/`limit`. Fix: strip outputs first —
  `for c in nb["cells"]: c["outputs"]=[]; c["execution_count"]=None` via a
  small Python/json script — then Read, make edits, then
  `jupyter nbconvert --to notebook --execute --inplace` to regenerate outputs.
- **NotebookEdit `insert` always lands immediately after the given `cell_id`.**
  To insert two new cells A then B (in that reading order) after some anchor,
  insert B first (anchor→B), then insert A with the same anchor (anchor→A→B).
- Re-execute the *whole* notebook top-to-bottom after any cell edit and check
  `outputs` for `output_type: error` before trusting it — cells share one
  kernel namespace, so a change early on can silently break something later
  that isn't obvious from the edited cell alone.

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
