# CurbRisk AV

**Quantifying driverless pick-up and drop-off risk using real-world autonomous vehicle deployment data.**

CurbRisk AV evaluates **PUDO (pick-up/drop-off) risk for driverless vehicles** using Waymo deployment and complaint data reported to the California Public Utilities Commission (CPUC).

The project focuses on three fundamental questions in risk analysis:

1. **What are the hazards?**
   Identify potential sources of danger, harm, or loss associated with autonomous vehicle PUDO operations.

2. **What are the consequences?**
   Assess the potential severity and impact of those hazards.

3. **How likely are they?**
   Estimate the frequency of PUDO-related complaints using CPUC data from **2024 Q3 through 2026 Q2**, while controlling for vehicle exposure using **vehicle trips**.

## Goal

The goal of CurbRisk AV is to move the discussion of driverless PUDO safety from **anecdotal reports to measurable risk**, combining hazard identification, consequence assessment, and exposure-adjusted likelihood estimation.

## Analysis

The analysis examines whether PUDO complaint risk is **increasing, decreasing, or remaining stable over time**.

Rather than comparing raw complaint counts alone, complaint frequency is normalized by exposure. The primary exposure measure is **vehicle trips** — every completed trip has exactly one pick-up and one drop-off, so trips count PUDO *opportunities* directly:

$$
\text{PUDO Risk Rate} =
\frac{\text{PUDO Complaints}}{\text{Vehicle Trips}}
$$

Total vehicle miles traveled (VMT) is also computed and reported as a robustness check — the trend direction is the same under trips and under VMT. (VMT is also split by trip phase in the raw data, but those phase labels aren't actually defined anywhere in CPUC's own documentation, so the analysis doesn't rely on them.)

Exposure-adjusted rates and statistical trend models are then used to evaluate changes across reporting periods and place PUDO hazards on a **likelihood × consequence risk matrix**.

## Data

Public data comes from the **California Public Utilities Commission Autonomous Vehicle Program Quarterly Reports**.

The reproducible [`pudo-pipeline`](pudo-pipeline/) package downloads and processes the quarterly reports into analysis-ready Parquet datasets containing:

* PUDO complaint and PUDO-collision counts per quarter (from two different CPUC file schemas — a 2024 aggregate table and 2025+ ride-level microdata)
* Monthly Waymo driverless vehicle activity: trips (primary exposure) and vehicle miles traveled
* Exposure-adjusted PUDO complaint rates (per 100k trips, plus per-100k-VMT cross-checks) and the trend fit

## Repository Structure

```text
PUDO/
├── pudo-pipeline/            # the package
│   ├── src/pudo_pipeline/    # CPUC ingestion + data preparation (pudo-build)
│   ├── notebooks/            # pudo_analysis.ipynb — rates, trend test, risk chart
│   └── data/parquet/         # generated datasets (gitignored)
├── project-site/             # Quarto site: project paper, published to GitHub Pages
│   ├── paper.qmd             # the project paper
│   ├── assets/               # figures/tables/text the notebook exports for the paper
│   └── docs/                 # rendered site output (published, not hand-edited)
├── CLAUDE.md                 # working context + source-data facts
└── README.md
```

See [`pudo-pipeline/README.md`](pudo-pipeline/README.md) for how to run the pipeline.

## Site Page & Paper

CurbRisk AV includes a Quarto-based project site that serves as the public-facing home for the research, results, team information, and project paper, published through GitHub Pages at [jonathanwilsonami.github.io/curbrisk-av](https://jonathanwilsonami.github.io/curbrisk-av/).

The paper never has numbers pasted into it by hand. The analysis notebook ([`pudo_analysis.ipynb`](pudo-pipeline/notebooks/pudo_analysis.ipynb)) exports the figures, tables, and headline results it computes as plain PNG/Markdown files into [`project-site/assets/`](project-site/assets/); `paper.qmd` pulls them in from there as properly numbered, cross-referenced Figures and Tables. Re-running the notebook and re-rendering the site is what keeps the paper in sync — there's no copy of the notebook inside `project-site/`, and no separate write-up to maintain by hand. See [`project-site/README.md`](project-site/README.md) for the exact workflow, and for instructions on installing Quarto, rendering, and contributing to the site and paper.
