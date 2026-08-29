import unittest

from app import app, db
from models import Barangay, Municipality, Province
from seed.demo_data import seed_geography_data


class GeographySeedTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_seed_geography_data_creates_records(self):
        seed_geography_data()
        self.assertGreaterEqual(Province.query.count(), 1)
        self.assertGreaterEqual(Municipality.query.count(), 1)
        self.assertGreaterEqual(Barangay.query.count(), 1)

    def test_seed_geography_data_covers_all_calabarzon_provinces(self):
        seed_geography_data()
        province_names = {p.name for p in Province.query.all()}
        self.assertEqual(province_names, {'Batangas', 'Cavite', 'Laguna', 'Quezon', 'Rizal'})
        # Exact counts sourced from PSGC (via the `psgc` npm package's data)
        # and cross-verified municipality-by-municipality against
        # municipalities.json before being written to
        # seed/calabarzon_geography.json -- see that build process for how
        # these were derived, not hand-typed.
        self.assertEqual(Municipality.query.count(), 142)
        self.assertEqual(Barangay.query.count(), 4018)

    def test_seed_geography_data_known_city_has_correct_barangay_count(self):
        """Spot-check against a publicly documented figure (Lipa City is
        well known to have 72 barangays) rather than only checking totals,
        since a totals-only check can't catch barangays being misattributed
        to the wrong municipality while the grand total still comes out
        right."""
        seed_geography_data()
        lipa = Municipality.query.filter_by(name='Lipa City').first()
        self.assertIsNotNone(lipa)
        self.assertEqual(Barangay.query.filter_by(municipality_id=lipa.id).count(), 72)

    def test_seed_geography_data_is_idempotent(self):
        seed_geography_data()
        seed_geography_data()
        self.assertEqual(Province.query.count(), 5)
        self.assertEqual(Municipality.query.count(), 142)
        self.assertEqual(Barangay.query.count(), 4018)

    def test_seed_geography_data_migrates_legacy_row_in_place_without_duplicating(self):
        """Regression test for the exact bug this seeding rewrite had to
        avoid: the pre-CALABARZON-expansion demo data seeded Batangas
        municipalities under made-up codes ('LIPA', 'TANAUAN') rather than
        real PSGC codes. A naive re-seed matching only on `code` would treat
        those as unrelated and insert a second 'Lipa City' row alongside the
        legacy one -- duplicating a real place in the dropdown, the same
        class of bug already fixed once in this codebase for Incident rows
        (see scheduler.py's external_event_id de-duplication)."""
        province = Province(code='BAT', name='Batangas')
        db.session.add(province)
        db.session.flush()
        legacy_lipa = Municipality(province_id=province.id, code='LIPA', name='Lipa City')
        db.session.add(legacy_lipa)
        db.session.commit()
        legacy_lipa_id = legacy_lipa.id

        seed_geography_data()

        matches = Municipality.query.filter_by(name='Lipa City', province_id=province.id).all()
        self.assertEqual(len(matches), 1, f"expected exactly one Lipa City row, got {[(m.id, m.code) for m in matches]}")
        self.assertEqual(matches[0].id, legacy_lipa_id, "the legacy row's id must be preserved for any existing foreign keys pointing at it")
        self.assertEqual(matches[0].code, '041014', "the legacy row's code must be migrated to the real PSGC code")


if __name__ == '__main__':
    unittest.main()
