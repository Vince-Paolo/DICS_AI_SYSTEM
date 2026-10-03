import math
import unittest
from datetime import datetime, timedelta

from services.aftershock import (
    REGION_PARAMS,
    fit_gr_params,
    forecast_for_event,
    get_region_for_location,
    probability_of_aftershock,
)


class AftershockTestCase(unittest.TestCase):
    def test_known_epicenter_resolves_to_calibrated_region(self):
        self.assertEqual(
            get_region_for_location(13.71, 120.57),
            'calabarzon_batangas_offshore',
        )

    def test_calibrated_forecast_reports_bounded_probability(self):
        region_key = 'calabarzon_batangas_offshore'
        self.assertIn(region_key, REGION_PARAMS)

        forecast = probability_of_aftershock(
            mainshock_magnitude=6.0,
            target_magnitude=4.5,
            hours_since_mainshock=24,
            window_hours=24,
            region_key=region_key,
            radius_km=20,
        )

        self.assertGreaterEqual(forecast['probability'], 0)
        self.assertLessEqual(forecast['probability'], 1)
        self.assertTrue(forecast['is_radius_modeled'])
        self.assertFalse(forecast['is_default_params'])

    def test_unknown_region_uses_default_parameters(self):
        forecast = probability_of_aftershock(
            mainshock_magnitude=6.0,
            target_magnitude=4.5,
            hours_since_mainshock=24,
            window_hours=24,
            region_key='unknown_region',
            radius_km=20,
        )

        self.assertTrue(forecast['is_default_params'])
        self.assertFalse(forecast['is_radius_modeled'])
        self.assertTrue(math.isfinite(forecast['probability']))

    def test_forecast_for_event_handles_missing_and_valid_events(self):
        self.assertIsNone(forecast_for_event(None, datetime.now()))

        forecast = forecast_for_event(
            6.0,
            datetime.now() - timedelta(hours=1),
        )
        self.assertIn('probability', forecast)
        self.assertIn('message', forecast)

    def test_gr_fit_filters_incomplete_magnitudes(self):
        result = fit_gr_params([1.5, 2.5, 3.5], mc=2.0)

        self.assertEqual(result['n_events'], 2)
        self.assertGreater(result['b'], 0)

    def test_gr_fit_requires_two_complete_magnitudes(self):
        with self.assertRaises(ValueError):
            fit_gr_params([1.5, 2.5], mc=2.0)


if __name__ == '__main__':
    unittest.main()
