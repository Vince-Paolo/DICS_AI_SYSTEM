# data/phivolcs_catalog.csv

## What this is
A PHIVOLCS earthquake bulletin export covering 2016-01-01 through 2026-07-06
(135,281 events nationwide). Columns: `Date_Time_PH`, `Latitude`, `Longitude`,
`Depth`, `Magnitude`, `Location`.

## Why it's committed to the repo
`scripts/calibrate_aftershock_regions.py` reads this file to (re)fit
Omori-Utsu + Gutenberg-Richter aftershock forecasting parameters (see
`services/aftershock.py`). The monthly scheduled recalibration workflow
(`.github/workflows/aftershock-recalibration.yml`) expects it at exactly
this path.

## Known limitation: this file will go stale
Nothing in this project automatically re-fetches a fresh PHIVOLCS export.
The scheduled recalibration workflow will keep re-fitting against whatever
is committed here until someone manually replaces it -- it does NOT reach
out to PHIVOLCS itself. A real integration would need either:
  - A scraper against https://earthquake.phivolcs.dost.gov.ph/ (their
    bulletin site doesn't offer a clean bulk CSV export as far as this
    project has confirmed), or
  - Switching to a source with a proper query API for Philippine events
    (e.g. USGS ComCat's FDSN endpoint covers the Philippines too, though
    with less complete coverage of smaller local events than PHIVOLCS's own
    network -- see the Phase 2 sourcing discussion earlier in this
    project's history for why PHIVOLCS was preferred).

## How to refresh this file
1. Obtain a newer PHIVOLCS export in the same column format.
2. Replace this file: `cp new_export.csv data/phivolcs_catalog.csv`
3. Run `python scripts/calibrate_aftershock_regions.py data/phivolcs_catalog.csv`
   locally first to sanity-check the output before committing (or let the
   scheduled workflow do it and review the PR it opens).
4. Run `python test_aftershock.py` to confirm nothing broke.
5. Commit both the updated catalog and the regenerated
   `services/region_params.json` together.

## Data quality notes (carried over from earlier analysis)
- Coverage before ~2016 has known gaps in PHIVOLCS's own published bulletin
  (confirmed via a third-party scraper project encountering the same 404s),
  and pre-2016 data has worse detection completeness (64 seismic stations
  before 2016 vs 85+ after) -- do NOT extend this file's date range
  backwards without re-checking whether `COMPLETENESS_MC = 2.0` in the
  calibration script still holds for that older data.
- This file is nationwide, but the calibration pipeline defaults to a
  Luzon-only bounding box (see `LUZON_BBOX` in
  `scripts/calibrate_aftershock_regions.py`) since this is a
  CALABARZON-focused system. Pass `--nationwide` to override.
