"""Seed helpers for CALABARZON reference geography (province / municipality /
barangay) used to populate the citizen incident-report location dropdowns.

The actual data lives in calabarzon_geography.json rather than inline in this
file: at full CALABARZON coverage (5 provinces, 142 cities/municipalities,
~4,018 barangays) an inline Python literal would be an unreviewable ~4,000+
line file. The JSON was built from the Philippine Standard Geographic Code
(PSGC) via the `psgc` npm package's published data (itself derived from PSA's
official PSGC publication), filtered to Region IV-A (CALABARZON) municipality
codes and cross-verified so every municipality here has exactly the barangays
PSGC records for it -- see the data-build notes in that file's directory.
"""

import json
from pathlib import Path

from models import Barangay, Municipality, Province, db

GEOGRAPHY_DATA_PATH = Path(__file__).parent / "calabarzon_geography.json"


def _load_geography_data():
    with open(GEOGRAPHY_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def seed_geography_data():
    """Populate/refresh province, municipality, and barangay reference data
    for all of CALABARZON.

    Idempotent and safe to call on a database that already has data,
    including the older 1-province demo seed this replaces: rows are
    matched by `code` first (the real PSGC code, for anything already
    migrated), falling back to matching by name within the right parent
    (province for municipalities, municipality for barangays) so a legacy
    row like the old Batangas municipalities gets its `code` corrected in
    place instead of leaving a duplicate row alongside a newly-inserted one.

    Uses bulk fetch-then-insert rather than a per-row exists-check query,
    since this runs in test setUp() for many test classes (after a fresh
    drop_all/create_all) as well as once at production startup -- a
    per-row query loop over ~4,000 barangays would make every test using
    it noticeably slower.
    """
    data = _load_geography_data()

    existing_provinces_by_code = {p.code: p for p in Province.query.all() if p.code}
    province_id_by_json_code = {}
    new_provinces = []
    for prov in data:
        existing = existing_provinces_by_code.get(prov["code"])
        if existing:
            province_id_by_json_code[prov["code"]] = existing.id
        else:
            new_provinces.append(Province(code=prov["code"], name=prov["name"]))
    if new_provinces:
        db.session.add_all(new_provinces)
        db.session.flush()
        for p in new_provinces:
            province_id_by_json_code[p.code] = p.id

    existing_munis = Municipality.query.all()
    muni_by_code = {m.code: m for m in existing_munis if m.code}
    muni_by_province_and_name = {(m.province_id, m.name): m for m in existing_munis}

    new_munis = []
    muni_json_to_existing = {}  # json muni code -> Municipality object (existing or newly created)
    for prov in data:
        province_id = province_id_by_json_code[prov["code"]]
        for muni in prov["municipalities"]:
            display_name = muni["name"] + (" City" if muni["is_city"] and not muni["name"].endswith("City") else "")
            existing = muni_by_code.get(muni["code"])
            if existing is None:
                existing = muni_by_province_and_name.get((province_id, display_name))
                if existing is not None:
                    existing.code = muni["code"]  # migrate legacy row onto the real PSGC code
            if existing is not None:
                muni_json_to_existing[muni["code"]] = existing
            else:
                new_muni = Municipality(province_id=province_id, code=muni["code"], name=display_name)
                new_munis.append(new_muni)
                muni_json_to_existing[muni["code"]] = new_muni
    if new_munis:
        db.session.add_all(new_munis)
        db.session.flush()

    existing_barangays = Barangay.query.all()
    brgy_by_code = {b.code: b for b in existing_barangays if b.code}
    brgy_by_muni_and_name = {(b.municipality_id, b.name): b for b in existing_barangays}

    new_barangays = []
    for prov in data:
        for muni in prov["municipalities"]:
            municipality = muni_json_to_existing[muni["code"]]
            municipality_id = municipality.id
            for brgy in muni["barangays"]:
                existing = brgy_by_code.get(brgy["code"])
                if existing is None:
                    existing = brgy_by_muni_and_name.get((municipality_id, brgy["name"]))
                    if existing is not None:
                        existing.code = brgy["code"]  # migrate legacy row onto the real PSGC code
                if existing is None:
                    new_barangays.append(Barangay(municipality_id=municipality_id, code=brgy["code"], name=brgy["name"]))
    if new_barangays:
        db.session.add_all(new_barangays)

    db.session.commit()


def seed_demo_data():
    seed_geography_data()


if __name__ == "__main__":
    seed_demo_data()

