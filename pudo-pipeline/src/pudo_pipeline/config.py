"""Configuration: source URLs, reporting-period map, paths, column selections.

CPUC reporting periods changed on 2025-01-01 from Sep-Nov style quarters to
calendar quarters. To cover the assignment window (2024 Q3 - 2026 Q2) we pull
the Jun-Aug 2024 and Sep-Nov 2024 reports plus the Dec 2024 one-month stub,
then the six calendar-quarter reports of 2025-2026.

The Sep-Nov 2024 report and the Dec 2024 one-month stub are recombined into a
single ``2024Q4`` analysis period (Sep-Dec 2024) so the window is exactly eight
quarters. The unequal month counts (2024Q3 = 3 months, 2024Q4 = 4 months) are
absorbed by the VMT offset in the Poisson trend model. See ``RAW_TO_ANALYSIS``.

``period_id`` stays a clean ``YYYYQn`` string for sorting / filtering / joining.
Because the two 2024 analysis periods are NOT the calendar quarters their names
suggest (``2024Q3`` = Jun-Aug, ``2024Q4`` = Sep-Dec), every period also carries
``period_label`` (e.g. ``"2024 Q3 (Jun-Aug)"``), ``window_start`` /
``window_end`` dates, and an ``is_calendar_quarter`` flag - see
``ANALYSIS_PERIOD_META``. 2025Q1 onward are true calendar quarters.

Complaint rows have redacted timestamps, so a complaint can only be attributed
to the reporting period of the file it came from. VMT (Month_Level) has real
Year/Month columns, so exposure is summed over the exact months of each
period. Numerator and denominator therefore always cover identical windows.

Source-data facts worth not re-deriving:
* The 2024 "Incidents-Complaints" files use a WIDE AGGREGATE schema (120 cols,
  one row, integer count column ``ComplaintsPUDO``). From 2025Q1 on they are
  RIDE-LEVEL MICRODATA (~50 cols, millions of rows, Y/N flag ``ComplaintPUDO``).
  ``transform.load_pudo_counts`` handles both.
* ``PUDOTravelLane`` / ``PUDOTravelLaneAll`` is redacted in every file of every
  period - it is reported as null, never as a zero count.
* The Jun-Aug 2024 zip also contains Cruise files (TCPID ``PSG0039080``); every
  report also ships a parallel header-only "Drivered" tree. Both are filtered
  out by ``WAYMO_TCPID`` + a ``drivered`` path check.
"""

import calendar
from datetime import date
from pathlib import Path

CPUC_MEDIA = (
    "https://www.cpuc.ca.gov/-/media/cpuc-website/divisions/"
    "consumer-protection-and-enforcement-division/documents/tlab/av-programs/"
)

# Waymo's CPUC carrier permit number, used to drop the Cruise rows bundled in
# the Jun-Aug 2024 multi-carrier zip (Cruise = PSG0039080).
#
# Caveat: the ride-level Incidents-Complaints exports carry a TRANSPOSED variant
# ``PSG0031852`` on most rows (a Waymo export artifact - it appears only in
# Waymo-named files inside Waymo deployment folders, and some parts mix both
# spellings). Both spellings are Waymo. Month-Level / Trips / aggregate files
# use the correct ``PSG0038152``.
WAYMO_TCPID = "PSG0038152"
WAYMO_TCPIDS = ("PSG0038152", "PSG0031852")

# raw reporting-period id -> (zip filename on CPUC site, [(year, month), ...])
PERIODS: dict[str, tuple[str, list[tuple[int, int]]]] = {
    "2024P3_JunAug": (
        "av-deployment_jun_aug_2024_public.zip",
        [(2024, 6), (2024, 7), (2024, 8)],
    ),
    "2024P4_SepNov": (
        "waymo-dep-sep-nov-2024-public_2a.zip",
        [(2024, 9), (2024, 10), (2024, 11)],
    ),
    "2024P5_Dec": (
        "waymo-dep-dec-2024--public_2.zip",
        [(2024, 12)],
    ),
    "2025Q1": (
        "waymo-driverless-deployment-2025-q1.zip",
        [(2025, 1), (2025, 2), (2025, 3)],
    ),
    "2025Q2": ("waymo-deployment-2025q2.zip", [(2025, 4), (2025, 5), (2025, 6)]),
    "2025Q3": ("waymo-deployment-2025q3.zip", [(2025, 7), (2025, 8), (2025, 9)]),
    "2025Q4": ("waymo-deployment-2025q4.zip", [(2025, 10), (2025, 11), (2025, 12)]),
    "2026Q1": ("waymo-deployment-2026q1.zip", [(2026, 1), (2026, 2), (2026, 3)]),
    "2026Q2": ("waymo-deployment-2026q2.zip", [(2026, 4), (2026, 5), (2026, 6)]),
}

# raw reporting period -> analysis period (the trend-model time axis).
# 2024P4_SepNov + 2024P5_Dec collapse into 2024Q4 (Sep-Dec 2024).
RAW_TO_ANALYSIS: dict[str, str] = {
    "2024P3_JunAug": "2024Q3",
    "2024P4_SepNov": "2024Q4",
    "2024P5_Dec": "2024Q4",
    "2025Q1": "2025Q1",
    "2025Q2": "2025Q2",
    "2025Q3": "2025Q3",
    "2025Q4": "2025Q4",
    "2026Q1": "2026Q1",
    "2026Q2": "2026Q2",
}

# analysis periods in chronological order
ANALYSIS_PERIODS: list[str] = list(dict.fromkeys(RAW_TO_ANALYSIS.values()))

# months covered by each analysis period (union of its raw periods' months)
ANALYSIS_PERIOD_MONTHS: dict[str, list[tuple[int, int]]] = {}
for _raw, (_zip, _months) in PERIODS.items():
    ANALYSIS_PERIOD_MONTHS.setdefault(RAW_TO_ANALYSIS[_raw], []).extend(_months)

COMPANY = "Waymo"

_MONTH_ABBR = ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _period_meta(pid: str) -> dict:
    """Static metadata for one analysis period, incl. the real month window.

    ``period_id`` (``YYYYQn``) is kept for machine use; ``period_label`` spells
    out the actual coverage because 2024's names are not calendar quarters.
    """
    months = ANALYSIS_PERIOD_MONTHS[pid]
    year, quarter = int(pid[:4]), int(pid[-1])
    (y0, m0), (y1, m1) = months[0], months[-1]
    is_calendar_quarter = months == [(year, 3 * quarter - 2),
                                     (year, 3 * quarter - 1),
                                     (year, 3 * quarter)]
    label = f"{year} Q{quarter}"
    if not is_calendar_quarter:
        label += f" ({_MONTH_ABBR[m0]}-{_MONTH_ABBR[m1]})"
    return {
        "period_id": pid,
        "period_label": label,
        "window_start": date(y0, m0, 1),
        "window_end": date(y1, m1, calendar.monthrange(y1, m1)[1]),
        "is_calendar_quarter": is_calendar_quarter,
        "year": year,
        "quarter": quarter,
        "company": COMPANY,
    }


ANALYSIS_PERIOD_META: dict[str, dict] = {
    pid: _period_meta(pid) for pid in ANALYSIS_PERIODS
}

# Project-relative data layout (override with PUDO_DATA_DIR env var)
DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw"          # downloaded zips
EXTRACT_DIR = DATA_DIR / "extracted"  # unzipped, one folder per raw period_id
OUT_DIR = DATA_DIR / "parquet"      # pipeline outputs

# Columns that are redacted in every public file - never treat as real zeros.
REDACTED_COLUMNS = [
    "PUDOTravelLane",
    "PUDOTravelLaneAll",
    "TimeofIncident",
    "IncLat",
    "IncLong",
    "IncidenceTract",
    "IncidenceZip",
    "VIN",
]

# Incidents-Complaints MICRODATA columns worth keeping (2025Q1+). Y/N flags
# parse to booleans downstream. PUDOTravelLane is excluded - it is redacted.
COMPLAINT_FLAGS = [
    "ComplaintSafety",
    "ComplaintPUDO",
    "ComplaintAccessibility",
    "ComplaintWAV",
    "ComplaintCustomerService",
    "ComplaintOther",
]
COLLISION_PUDO_FLAGS = [
    "CollisionPUDOPassenger",
    "CollisionPUDOPedestrian",
    "CollisionPUDOBicycleScooter",
    "CollisionPUDOMotorcycles",
    "CollisionPUDOOtherVehicle",
    "CollisionPUDORail",
    "CollisionPUDOProperty",
]
COMPLAINT_KEEP = ["TCPID"] + COLLISION_PUDO_FLAGS + COMPLAINT_FLAGS

# Incidents-Complaints AGGREGATE columns (2024 reports: one summed row).
AGG_PUDO_COMPLAINTS_COL = "ComplaintsPUDO"
AGG_PUDO_COLLISIONS_COL = "CollisionsPUDOAll"

# All complaint-category count columns, for "PUDO as a share of all complaints".
# Aggregate schema uses the plural names; microdata uses COMPLAINT_FLAGS (singular).
AGG_COMPLAINT_COLS = [
    "ComplaintsSafety",
    "ComplaintsPUDO",
    "ComplaintsAccessibility",
    "ComplaintsWAV",
    "ComplaintsCustomerService",
    "ComplaintsOther",
]

# Month_Level columns (denominator / activity)
MONTH_LEVEL_KEEP = [
    "TCPID",
    "Year",
    "Month",
    "TotalTrips",
    "TotalVMTPeriod1",
    "TotalVMTPeriod2",
    "TotalVMTPeriod3",
    "TotalVMTZEV",
    "TotalPassengersCarried",
    "TotalPMT",
]
MONTH_LEVEL_VMT_COLS = ["TotalVMTPeriod1", "TotalVMTPeriod2", "TotalVMTPeriod3"]
