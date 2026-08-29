"""Stage 1: fetch CPUC report zips and extract them.

Idempotent: existing downloads/extractions are skipped, so a team member who
already has the zips (or received them out-of-band) can drop them into
data/raw/ named <period_id>.zip and skip the network entirely.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import requests

from .config import CPUC_MEDIA, PERIODS


def download_all(raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    for period_id, (filename, _) in PERIODS.items():
        dest = raw_dir / f"{period_id}.zip"
        if dest.exists():
            print(f"[download] {period_id}: already present, skipping")
            continue
        url = CPUC_MEDIA + filename
        print(f"[download] {period_id}: {url}")
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        dest.write_bytes(resp.content)


def extract_all(raw_dir: Path, extract_dir: Path) -> None:
    extract_dir.mkdir(parents=True, exist_ok=True)
    for period_id in PERIODS:
        src = raw_dir / f"{period_id}.zip"
        dest = extract_dir / period_id
        if dest.exists():
            print(f"[extract] {period_id}: already extracted, skipping")
            continue
        if not src.exists():
            print(f"[extract] WARNING: {src} missing - period will be absent")
            continue
        print(f"[extract] {period_id}")
        with zipfile.ZipFile(src) as zf:
            zf.extractall(dest)
        # Some CPUC zips nest a second zip (e.g. Waymo folder inside a
        # multi-carrier archive). Extract one level of nested zips too.
        for nested in dest.rglob("*.zip"):
            with zipfile.ZipFile(nested) as zf:
                zf.extractall(nested.parent / nested.stem)
