import os
import unittest

os.environ['DATABASE_URL'] = 'sqlite://'

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

    def test_seed_geography_data_is_scoped_to_san_pablo_city(self):
        seed_geography_data()
        province_names = {p.name for p in Province.query.all()}
        self.assertEqual(province_names, {'Laguna'})
        municipalities = Municipality.query.all()
        self.assertEqual([municipality.name for municipality in municipalities], ['San Pablo City'])
        self.assertEqual(Barangay.query.count(), 80)

    def test_seed_geography_data_known_city_has_correct_barangay_count(self):
        """Spot-check San Pablo City's barangays rather than only checking totals,
        since a totals-only check can't catch barangays being misattributed
        to the wrong municipality while the grand total still comes out
        right."""
        seed_geography_data()
        san_pablo = Municipality.query.filter_by(name='San Pablo City').first()
        self.assertIsNotNone(san_pablo)
        self.assertEqual(Barangay.query.filter_by(municipality_id=san_pablo.id).count(), 80)

    def test_seed_geography_data_is_idempotent(self):
        seed_geography_data()
        seed_geography_data()
        self.assertEqual(Province.query.count(), 1)
        self.assertEqual(Municipality.query.count(), 1)
        self.assertEqual(Barangay.query.count(), 80)

    def test_seed_geography_data_migrates_legacy_row_in_place_without_duplicating(self):
        """Upgrade a legacy San Pablo row in place without duplicating it."""
        province = Province(code='LAG', name='Laguna')
        db.session.add(province)
        db.session.flush()
        legacy_san_pablo = Municipality(
            province_id=province.id,
            code='SAN_PABLO',
            name='San Pablo City',
        )
        db.session.add(legacy_san_pablo)
        db.session.commit()
        legacy_san_pablo_id = legacy_san_pablo.id

        seed_geography_data()

        matches = Municipality.query.filter_by(name='San Pablo City', province_id=province.id).all()
        self.assertEqual(len(matches), 1, f"expected exactly one San Pablo City row, got {[(m.id, m.code) for m in matches]}")
        self.assertEqual(matches[0].id, legacy_san_pablo_id, "the legacy row's id must be preserved for any existing foreign keys pointing at it")
        self.assertEqual(matches[0].code, '043424', "the legacy row's code must be migrated to the real PSGC code")


if __name__ == '__main__':
    unittest.main()
