#!/usr/bin/env python3
"""
Test suite for services/aftershock.py.

Run directly (matches this project's existing test_ai_prediction.py style,
no pytest dependency required):
    python test_aftershock.py

Exits non-zero if any assertion fails, so it's usable in CI later even
though nothing wires it into CI yet (see Phase 2 known-limitations list).
"""
import sys
import math

from services.aftershock import (
    probability_of_aftershock,
    expected_aftershocks,
    gr_fraction_at_least,
    spatial_fraction_within_radius,
    get_region_for_location,
    fit_omori_params,
    fit_gr_params,
    build_forecast_message,
    REGION_PARAMS,
)

passed = 0
failed = 0


def check(description, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {description}")
    else:
        failed += 1
        print(f"  FAIL: {description}")


def section(title):
    print(f"\n=== {title} ===")


# ----------------------------------------------------------------------
# Core math sanity checks -- these are the properties any correct Omori-
# Utsu/GR implementation must satisfy, regardless of which parameters are
# plugged in. Catches the exact productivity-constant conflation bug found
# earlier in this project (which produced 100% probability from a K
# scaled 10^7 too high).
# ----------------------------------------------------------------------
section("Core math sanity checks")

f_base = probability_of_aftershock(5.5, 4.5, hours_since_mainshock=0, window_hours=24)
check("probability is between 0 and 1", 0.0 <= f_base['probability'] <= 1.0)
check("probability is not degenerately 100% for a moderate mainshock",
      f_base['probability_pct'] < 99.0)

f_later = probability_of_aftershock(5.5, 4.5, hours_since_mainshock=72, window_hours=24)
check("probability decreases as time since mainshock increases (Omori decay)",
      f_later['probability_pct'] < f_base['probability_pct'])

f_bigger = probability_of_aftershock(6.5, 4.5, hours_since_mainshock=0, window_hours=24)
check("probability increases for a bigger mainshock (productivity scaling)",
      f_bigger['probability_pct'] > f_base['probability_pct'])

f_equal_mag = probability_of_aftershock(5.0, 5.5, hours_since_mainshock=0, window_hours=24)
check("probability is exactly 0 when target magnitude >= mainshock magnitude",
      f_equal_mag['probability_pct'] == 0.0)

check("expected_aftershocks is non-negative for a valid window",
      expected_aftershocks(0, 1, K=10, c=0.05, p=1.1) >= 0)
check("expected_aftershocks is 0 for an empty/reversed window",
      expected_aftershocks(5, 1, K=10, c=0.05, p=1.1) == 0)

check("gr_fraction_at_least is 1.0 at or below completeness magnitude",
      gr_fraction_at_least(1.0, mainshock_magnitude=6.0, b=1.0, mc=2.0) == 1.0)
check("gr_fraction_at_least is 0.0 when target >= mainshock",
      gr_fraction_at_least(6.0, mainshock_magnitude=6.0, b=1.0) == 0.0)
check("gr_fraction_at_least decreases as target magnitude increases",
      gr_fraction_at_least(3.0, 6.0, b=1.0) > gr_fraction_at_least(4.0, 6.0, b=1.0))


# ----------------------------------------------------------------------
# Region matching -- covers the exact bug fixed in this project (string-
# matching missed events not labeled with the province name in the place
# text; distance-based matching fixes it).
# ----------------------------------------------------------------------
section("Region matching (circle + polyline geometries)")

check("Batangas circle region matches at its own epicenter",
      get_region_for_location(13.71, 120.57) == 'calabarzon_batangas_offshore')
check("Batangas circle region matches a nearby point with an unrelated place label"
      " (this is the exact case string-matching used to miss)",
      get_region_for_location(13.85, 120.65) == 'calabarzon_batangas_offshore')
check("Batangas circle region does NOT match a point outside its radius",
      get_region_for_location(14.6, 121.2) != 'calabarzon_batangas_offshore')
check("Batangas circle region does NOT match the excluded Sablayan contamination cluster (~90km away)",
      get_region_for_location(12.85, 120.75) is None)

check("MVFS polyline region matches a point directly on the trace (Marikina City)",
      get_region_for_location(14.65, 121.10) == 'marikina_valley_fault_proxy')
check("MVFS polyline region matches a point near the trace but off it (Antipolo)",
      get_region_for_location(14.6, 121.18) == 'marikina_valley_fault_proxy')
check("MVFS polyline region does NOT match a point far off the corridor",
      get_region_for_location(14.5, 121.5) is None)

check("get_region_for_location returns None (not an exception) for missing coordinates",
      get_region_for_location(None, None) is None)
check("get_region_for_location returns None for a lat with no matching lon",
      get_region_for_location(14.0, None) is None)


# ----------------------------------------------------------------------
# Proxy vs. calibrated vs. default distinction -- covers the case where a
# proxy region could be silently mistaken for a real calibration. This
# distinction is the entire point of the is_proxy flag added in this
# project; a regression here would be a real credibility problem, not
# just a cosmetic one.
# ----------------------------------------------------------------------
section("Proxy / calibrated / default distinction")

f_calibrated = probability_of_aftershock(5.5, 4.5, 0, 24, region_key='calabarzon_batangas_offshore')
check("a real calibrated region reports is_default_params=False",
      f_calibrated['is_default_params'] is False)
check("a real calibrated region reports is_proxy=False",
      f_calibrated['is_proxy'] is False)
check("a real calibrated region's message contains no proxy/default caveat",
      'borrowed' not in build_forecast_message(f_calibrated).lower()
      and 'default parameters' not in build_forecast_message(f_calibrated).lower())

f_proxy = probability_of_aftershock(5.5, 4.5, 0, 24, region_key='marikina_valley_fault_proxy')
check("a proxy region reports is_default_params=False (it does have SOME params)",
      f_proxy['is_default_params'] is False)
check("a proxy region reports is_proxy=True",
      f_proxy['is_proxy'] is True)
check("a proxy region's message explicitly says 'borrowed'",
      'borrowed' in build_forecast_message(f_proxy).lower())

f_default = probability_of_aftershock(5.5, 4.5, 0, 24, region_key=None)
check("no region at all reports is_default_params=True",
      f_default['is_default_params'] is True)
check("no region at all's message says 'default parameters'",
      'default parameters' in build_forecast_message(f_default).lower())

f_unknown = probability_of_aftershock(5.5, 4.5, 0, 24, region_key='not_a_real_region_key')
check("an unrecognized region_key falls back to default params rather than raising",
      f_unknown['is_default_params'] is True)


# ----------------------------------------------------------------------
# Spatial radius handling
# ----------------------------------------------------------------------
section("Spatial radius handling")

f_no_radius = probability_of_aftershock(5.5, 4.5, 0, 24, region_key='calabarzon_batangas_offshore')
f_tight_radius = probability_of_aftershock(5.5, 4.5, 0, 24,
                                            region_key='calabarzon_batangas_offshore', radius_km=10)
check("a tight radius reduces expected probability vs. no radius restriction",
      f_tight_radius['probability_pct'] <= f_no_radius['probability_pct'])
check("radius result is flagged as modeled when a spatial fit exists",
      f_tight_radius['is_radius_modeled'] is True)

f_proxy_radius = probability_of_aftershock(5.5, 4.5, 0, 24,
                                            region_key='marikina_valley_fault_proxy', radius_km=10)
check("requesting a radius on a region with no spatial fit is flagged as NOT modeled",
      f_proxy_radius['is_radius_modeled'] is False)
check("an un-modeled radius request degrades to the zone-wide estimate, not an error/zero",
      f_proxy_radius['probability_pct'] == f_proxy['probability_pct'])

frac, is_modeled = spatial_fraction_within_radius(1000, region_key='calabarzon_batangas_offshore')
check("spatial fraction is clamped to 1.0 for a radius far beyond the fit's valid range",
      frac <= 1.0)


# ----------------------------------------------------------------------
# Fit reliability self-checks -- covers the exact data-sparsity failure
# mode found with the Quezon/Jomalig (63-event) and second Batangas
# (201-event) sequences during this project's calibration work.
# ----------------------------------------------------------------------
section("Fit reliability self-checks")

# A deliberately sparse, noisy synthetic sequence -- should be flagged unreliable
sparse_days = [0.1, 0.5, 1.0, 5.0, 20.0]
sparse_rates = [50, 3, 40, 2, 8]  # non-monotonic, unrealistic for real Omori decay
sparse_fit = fit_omori_params(sparse_days, sparse_rates)
check("a sparse/noisy sequence fit is flagged as unreliable",
      sparse_fit['reliable'] is False)

# A clean synthetic sequence generated FROM the Omori law itself -- fitting
# it back out should recover parameters close to the originals and be
# flagged reliable, proving the fitter works correctly on well-behaved data.
import numpy as np
true_K, true_c, true_p = 20.0, 0.1, 1.1
clean_days = np.logspace(-1, 1.5, 30)
clean_rates = true_K / (clean_days + true_c) ** true_p
clean_fit = fit_omori_params(clean_days.tolist(), clean_rates.tolist())
check("a clean synthetic Omori sequence recovers p within 0.1 of the true value",
      abs(clean_fit['p'] - true_p) < 0.1)
check("a clean synthetic Omori sequence is flagged reliable",
      clean_fit['reliable'] is True)

gr_fit = fit_gr_params([2.0, 2.2, 2.5, 2.8, 3.0, 3.5, 4.0, 2.1, 2.3, 2.6], mc=2.0)
check("fit_gr_params returns a positive b-value for a normal magnitude distribution",
      gr_fit['b'] > 0)


# ----------------------------------------------------------------------
# REGION_PARAMS structural integrity -- catches malformed entries (e.g. a
# region_params.json produced by a broken pipeline run) before they'd
# cause a confusing runtime error deep inside probability_of_aftershock.
# ----------------------------------------------------------------------
section("REGION_PARAMS structural integrity")

for key, region in REGION_PARAMS.items():
    check(f"region '{key}' has a geometry type", region.get('geometry') in ('circle', 'polyline'))
    check(f"region '{key}' has omori params with K, c, p",
          all(k in region.get('omori', {}) for k in ('K', 'c', 'p')))
    check(f"region '{key}' has gr params with a, b",
          all(k in region.get('gr', {}) for k in ('a', 'b')))
    check(f"region '{key}' has an is_proxy flag (bool)", isinstance(region.get('is_proxy'), bool))
    if region['geometry'] == 'circle':
        check(f"circle region '{key}' has center_lat/lon and region_radius_km",
              region.get('center_lat') is not None and region.get('region_radius_km') is not None)
    if region['geometry'] == 'polyline':
        check(f"polyline region '{key}' has trace_points and corridor_half_width_km",
              region.get('trace_points') and region.get('corridor_half_width_km') is not None)


# ----------------------------------------------------------------------
print(f"\n{'=' * 60}")
print(f"{passed} passed, {failed} failed")
if failed > 0:
    sys.exit(1)
