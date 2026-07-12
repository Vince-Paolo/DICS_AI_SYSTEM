"""
Aftershock forecasting using the Omori-Utsu law combined with the
Gutenberg-Richter magnitude-frequency relation.

Omori-Utsu law (aftershock rate over time):
    n(t) = K / (t + c) ** p
where t is time since the mainshock (days), and K, c, p are sequence-specific
parameters fit from historical aftershock sequences.

Gutenberg-Richter law (magnitude distribution):
    log10(N(>=M)) = a - b * M
used here to scale the *fraction* of aftershocks expected to reach or exceed
a target magnitude, given the mainshock magnitude.

==============================================================================
IMPORTANT - PARAMETER PROVENANCE
==============================================================================
DEFAULT_OMORI_PARAMS and DEFAULT_GR_PARAMS below are GLOBAL LITERATURE
DEFAULTS (Utsu et al. 1995 summary ranges), NOT fit to Philippine Fault Zone
or Calabarzon-adjacent fault data. This module has no network access to pull
historical PHIVOLCS/USGS ComCat sequences at the time it was written.

Before relying on these forecasts operationally:
  1. Pull historical aftershock sequences for the region (USGS ComCat bulk
     query by region + time window around past M5+ mainshocks, or PHIVOLCS
     catalog if accessible).
  2. Call fit_omori_params() / fit_gr_params() on that data.
  3. Store the fit result in REGION_PARAMS below, keyed by fault zone / area.

Until that calibration happens, treat every forecast's `is_default_params`
flag as a visible caveat to show stakeholders alongside the number.
==============================================================================
"""
import math

# Global literature defaults (Utsu, Ogata & Matsu'ura 1995 summary ranges)
DEFAULT_OMORI_PARAMS = {
    'K': 10.0,   # productivity scale (aftershocks/day at t=0), rescaled per mainshock below
    'c': 0.05,   # days, offset avoiding singularity at t=0
    'p': 1.1,    # decay exponent, typically 0.9-1.5
}

DEFAULT_GR_PARAMS = {
    'a': 4.0,    # productivity intercept (log10 N at M=0), rescaled per mainshock below
    'b': 1.0,    # magnitude scaling, typically ~1.0 for tectonic regions
}

# Reasenberg-Jones (1989) style productivity scaling: log10(K) = a_p + alpha*(M - Mc)
# a_p ~ -1.67 is the commonly cited California default (Reasenberg & Jones 1989/1990);
# alpha ~1.0 is a common global default. This is a DIFFERENT constant from the
# Gutenberg-Richter 'a' value below -- do not conflate the two.
PRODUCTIVITY_A = -1.67
PRODUCTIVITY_ALPHA = 1.0
COMPLETENESS_MAGNITUDE = 2.5

# Per-region calibrated params, fit from real historical sequences.
# Falls back to DEFAULT_OMORI_PARAMS / DEFAULT_GR_PARAMS if a region isn't listed.
REGION_PARAMS = {
    'calabarzon_batangas_offshore': {
        # Reference epicenter (2021-07-24 M6.6 Calatagan mainshock) and the
        # radius within which this calibration is considered applicable.
        # region_radius_km is deliberately generous (encompasses the historical
        # cluster of Calatagan/Nasugbu M5+ events seen in the PHIVOLCS catalog,
        # 2017-2026) -- NOT the same thing as spatial.fit_valid_to_km, which is
        # the much tighter near-field aftershock-density fit radius used inside
        # a single forecast.
        'center_lat': 13.71,
        'center_lon': 120.57,
        'region_radius_km': 60.0,
        'geometry': 'circle',
        # Fit from the 2021-07-24 M6.6 Calatagan, Batangas mainshock sequence
        # (PHIVOLCS catalog, 90-day window, 100km radius, Mc=2.0, n=568 events).
        # NOTE: this sequence included a M5.5 event just 9 minutes after the
        # mainshock, which likely triggered its own secondary aftershock burst
        # and flattened the observed Omori decay -- the fitted p=0.49 is lower
        # than the typical global range (0.9-1.5) as a result. b=0.97 closely
        # matches the global GR default (~1.0), a good sanity check that the
        # magnitude-distribution side of this fit is solid. Treat 'p' with
        # some caution until it's cross-checked against a cleaner (single-
        # mainshock) sequence from the same fault system.
        # Cross-validation attempted against the 2020-12-25 M6.3 Calatagan
        # sequence (a cleaner, non-contaminated mainshock -- no other M5+
        # nearby within 1 day). That sequence only had 201 events (vs 568
        # here), and its rate-vs-time bins were too noisy at that sample size
        # to fit independently: the optimizer pinned p at the upper bound
        # (3.0) with a standard error larger than the estimate itself, both
        # with the full 90-day window and restricted to the first 3 days.
        # That is a genuine data-sparsity problem, not a competing p estimate
        # -- it should not be read as evidence this fit's p=0.49 is wrong,
        # but it also means p=0.49 remains cross-validated against only one
        # sequence. Treat it as provisional until a sequence with enough
        # events (n > ~400) to fit cleanly becomes available.
        'omori': {'K': 29.01, 'c': 0.0064, 'p': 0.489},
        'gr': {'a': 2.75, 'b': 0.97},
        'spatial': {'D0': 2.706, 'L_km': 4.05, 'r90_km': 35.5, 'fit_valid_to_km': 50.0},
        # Spatial decay fit from the SAME 2021 sequence, restricted to the
        # clean near-field radius (<=50km, n=416 events, Mc=2.0). Beyond
        # ~90km the raw catalog picks up an unrelated cluster near Sablayan,
        # Occidental Mindoro (~150 events) that is NOT part of this
        # aftershock sequence -- excluded from this fit. Density(r) =
        # D0 * exp(-r/L_km), where r is distance in km from the mainshock
        # epicenter. r90_km is the radius containing 90% of the clean
        # near-field events, offered as a simple default search radius when
        # a caller doesn't specify one.
        'productivity_a': PRODUCTIVITY_A,  # not separately re-derived from this fit yet
        'is_proxy': False,
        'source': '2021-07-24 M6.6 Calatagan, Batangas (PHIVOLCS catalog, direct fit)',
    },
    'marikina_valley_fault_proxy': {
        # ==================================================================
        # PROXY REGION -- read before using this in any stakeholder-facing
        # context.
        #
        # The Marikina Valley Fault System (MVFS / West Valley Fault) has NOT
        # ruptured during the instrumental seismic recording era. It is
        # characterized only through paleoseismic trenching (per published
        # neotectonic/paleoseismic studies): estimated magnitude range
        # M6.0-7.5 (single-event offsets suggest M7.3-7.7), average recurrence
        # interval ~310 years. No aftershock sequence exists to measure decay
        # behavior from, and a literature search found no fault-specific
        # Omori/Gutenberg-Richter parameters for MVFS anywhere.
        #
        # The omori/gr values below are NOT fit to MVFS data. They are
        # borrowed directly from the calabarzon_batangas_offshore fit, on the
        # grounds that both are Philippine strike-slip fault systems (a
        # closer tectonic analogy than an arbitrary global default), which is
        # a reasonable but UNVERIFIED assumption. If MVFS ever ruptures and
        # produces a real aftershock sequence, that data should replace this
        # proxy immediately -- this is not a substitute for real calibration.
        # ==================================================================
        'geometry': 'polyline',
        # Approximate corridor through named towns along the fault's known
        # path (Bulacan -> Rizal -> Metro Manila -> Cavite/Laguna), built
        # from general place-location knowledge, NOT digitized from a
        # geological fault-trace map. Treat as a rough corridor for "is this
        # event near MVFS", not a precise rupture-trace geometry -- for
        # actual hazard planning, use PHIVOLCS's official FaultFinder trace.
        'trace_points': [
            (14.98, 121.03),  # Dona Remedios Trinidad, Bulacan (north end)
            (14.65, 121.10),  # Marikina City
            (14.58, 121.09),  # Pasig City
            (14.52, 121.05),  # Taguig City
            (14.38, 121.04),  # Muntinlupa City
            (14.20, 121.15),  # Canlubang, Laguna (south end)
        ],
        'corridor_half_width_km': 15.0,
        'omori': {'K': 29.01, 'c': 0.0064, 'p': 0.489},   # borrowed from Batangas fit
        'gr': {'a': 2.75, 'b': 0.97},                      # borrowed from Batangas fit
        'spatial': None,  # no near-field density fit available for this proxy
        'productivity_a': PRODUCTIVITY_A,
        'is_proxy': True,
        'source': 'PROXY: borrowed from calabarzon_batangas_offshore fit (2021 M6.6 Calatagan). '
                   'MVFS itself has no instrumental aftershock record; only paleoseismic slip-rate '
                   'data exists (M6.0-7.5, ~310yr recurrence). Covers Rizal, Cavite, Laguna corridor.',
    },
}

# Load pipeline-generated calibrations (scripts/calibrate_aftershock_regions.py)
# if present, overriding the hardcoded direct-fit entries above with whatever
# was most recently calibrated. Proxy regions (is_proxy=True, e.g. the MVFS
# entry) are defined by hand above and are NOT touched by the pipeline, so
# they always survive this merge even if region_params.json exists.
# Falls back silently to the hardcoded values if the file is missing or
# malformed -- the app must keep working with zero setup.
def _load_calibrated_regions():
    import json
    from pathlib import Path
    json_path = Path(__file__).resolve().parent / 'region_params.json'
    if not json_path.exists():
        return
    try:
        with open(json_path) as f:
            calibrated = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        import warnings
        warnings.warn(f"Could not load {json_path}, using hardcoded REGION_PARAMS: {exc}", UserWarning)
        return
    for region_key, params in calibrated.items():
        REGION_PARAMS[region_key] = params


_load_calibrated_regions()


def get_region_for_location(latitude, longitude):
    """
    Determine which calibrated region (if any) a given earthquake location
    falls into. Supports two region geometry types:
      - 'circle': distance to a single reference epicenter (used for
        calabarzon_batangas_offshore, an offshore point-source fault zone)
      - 'polyline': shortest distance to a multi-segment fault-trace corridor
        (used for marikina_valley_fault_proxy, a ~135km linear fault)

    Returns the region_key string, or None if no calibrated region matches
    (caller should fall back to global default parameters in that case).
    If multiple regions match, the closest one (as a fraction of its own
    allowed radius/corridor width) wins.
    """
    if latitude is None or longitude is None:
        return None
    best_match = None
    best_score = None
    for region_key, region in REGION_PARAMS.items():
        geometry = region.get('geometry', 'circle')
        if geometry == 'circle':
            center_lat = region.get('center_lat')
            center_lon = region.get('center_lon')
            radius = region.get('region_radius_km')
            if center_lat is None or center_lon is None or radius is None:
                continue
            dist = _haversine_km(latitude, longitude, center_lat, center_lon)
            if dist <= radius:
                score = dist / radius  # normalized, so circle/polyline scores are comparable
                if best_score is None or score < best_score:
                    best_match, best_score = region_key, score
        elif geometry == 'polyline':
            points = region.get('trace_points')
            half_width = region.get('corridor_half_width_km')
            if not points or half_width is None:
                continue
            dist = _point_to_polyline_km(latitude, longitude, points)
            if dist <= half_width:
                score = dist / half_width
                if best_score is None or score < best_score:
                    best_match, best_score = region_key, score
    return best_match


def _point_to_segment_km(lat, lon, lat1, lon1, lat2, lon2):
    """Approximate shortest distance from a point to a line segment, using a
    local equirectangular projection (adequate at this scale, ~100s of km)."""
    lat0 = math.radians((lat1 + lat2) / 2)
    km_per_deg_lat = 111.32
    km_per_deg_lon = 111.32 * math.cos(lat0)

    px, py = lon * km_per_deg_lon, lat * km_per_deg_lat
    x1, y1 = lon1 * km_per_deg_lon, lat1 * km_per_deg_lat
    x2, y2 = lon2 * km_per_deg_lon, lat2 * km_per_deg_lat

    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    proj_x, proj_y = x1 + t * dx, y1 + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def _point_to_polyline_km(lat, lon, points):
    """Shortest distance from a point to any segment of a multi-point polyline."""
    return min(
        _point_to_segment_km(lat, lon, points[i][0], points[i][1], points[i + 1][0], points[i + 1][1])
        for i in range(len(points) - 1)
    )


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def spatial_fraction_within_radius(radius_km, region_key=None):
    """
    Fraction of aftershocks expected to occur within `radius_km` of the
    mainshock epicenter, based on the fitted density(r) = D0*exp(-r/L) decay.
    Falls back to 1.0 (no spatial discounting) if no region-specific spatial
    fit is available -- callers should treat that as "radius not modeled",
    not as "100% confidently within radius".
    """
    region = REGION_PARAMS.get(region_key) if region_key else None
    spatial = region.get('spatial') if region else None
    if not spatial:
        return 1.0, False  # (fraction, is_modeled)

    D0, L = spatial['D0'], spatial['L_km']
    # Integrate the 2D radial density D0*exp(-r/L) over a disc of radius R:
    # count(R) = integral_0^R D0*exp(-r/L) * 2*pi*r dr
    #          = 2*pi*D0*L^2 * (1 - (1 + R/L)*exp(-R/L))
    def cumulative(R):
        return 2 * math.pi * D0 * L ** 2 * (1 - (1 + R / L) * math.exp(-R / L))

    # Normalize against a large reference radius (effectively "all" near-field
    # aftershocks) rather than infinity, since the fit is only valid out to
    # where it was actually measured (~50km for this region).
    reference_radius = spatial.get('fit_valid_to_km', 50.0)
    total = cumulative(reference_radius)
    within = cumulative(min(radius_km, reference_radius))
    if total <= 0:
        return 1.0, False
    return min(1.0, within / total), True


def _scaled_K(mainshock_magnitude, productivity_a=PRODUCTIVITY_A, alpha=PRODUCTIVITY_ALPHA,
              mc=COMPLETENESS_MAGNITUDE):
    """Scale Omori productivity K by mainshock magnitude (bigger mainshock -> more aftershocks)."""
    log10_k = productivity_a + alpha * (mainshock_magnitude - mc)
    return 10 ** log10_k


def omori_rate(t_days, K, c, p):
    """Instantaneous aftershock rate (events/day) at time t_days since mainshock."""
    if t_days < 0:
        return 0.0
    return K / ((t_days + c) ** p)


def expected_aftershocks(t1_days, t2_days, K, c, p):
    """Expected number of aftershocks (of any magnitude >= completeness) between
    t1 and t2 days after the mainshock. Closed-form integral of the Omori-Utsu law."""
    if t2_days <= t1_days:
        return 0.0
    if abs(p - 1.0) < 1e-9:
        integral = math.log(t2_days + c) - math.log(t1_days + c)
    else:
        integral = ((t2_days + c) ** (1 - p) - (t1_days + c) ** (1 - p)) / (1 - p)
    return max(0.0, K * integral)


def gr_fraction_at_least(target_magnitude, mainshock_magnitude, b=DEFAULT_GR_PARAMS['b'],
                          mc=COMPLETENESS_MAGNITUDE):
    """Fraction of aftershocks (out of all aftershocks >= mc) expected to reach or
    exceed target_magnitude, using the Gutenberg-Richter relation. Aftershocks
    are conventionally assumed to top out below the mainshock magnitude, so this
    fraction is 0 if target_magnitude >= mainshock_magnitude."""
    if target_magnitude >= mainshock_magnitude:
        return 0.0
    if target_magnitude <= mc:
        return 1.0
    # N(>=M) ~ 10^(-b*(M - Mc)); fraction relative to N(>=Mc) = 1
    return 10 ** (-b * (target_magnitude - mc))


def probability_of_aftershock(mainshock_magnitude, target_magnitude, hours_since_mainshock,
                               window_hours, region_key=None, radius_km=None):
    """
    Main entry point: probability of at least one aftershock with magnitude
    >= target_magnitude occurring within `radius_km` of the mainshock epicenter
    in the next `window_hours`, given the mainshock occurred
    `hours_since_mainshock` hours ago.

    Uses a non-homogeneous Poisson process assumption (standard in aftershock
    forecasting): P(>=1 event) = 1 - exp(-lambda), where lambda is the expected
    count of qualifying aftershocks in the window (Omori-Utsu rate x GR fraction
    x spatial fraction within radius_km).

    If radius_km is None, or no spatial fit exists for the region, the spatial
    term is skipped (fraction=1.0) and the result describes "anywhere in the
    aftershock zone" rather than a specific radius -- check
    `is_radius_modeled` in the result to know which case applies.

    Returns a dict with the probability plus the parameters/assumptions used,
    so the result can be displayed with appropriate caveats.
    """
    region = REGION_PARAMS.get(region_key) if region_key else None
    omori = region['omori'] if region else DEFAULT_OMORI_PARAMS
    gr = region['gr'] if region else DEFAULT_GR_PARAMS
    productivity_a = region.get('productivity_a', PRODUCTIVITY_A) if region else PRODUCTIVITY_A
    is_default = region is None
    is_proxy = region.get('is_proxy', False) if region else False

    t1 = hours_since_mainshock / 24.0
    t2 = (hours_since_mainshock + window_hours) / 24.0

    K = _scaled_K(mainshock_magnitude, productivity_a=productivity_a, alpha=PRODUCTIVITY_ALPHA,
                  mc=COMPLETENESS_MAGNITUDE)
    c = omori['c']
    p = omori['p']

    expected_all = expected_aftershocks(t1, t2, K, c, p)
    fraction_mag = gr_fraction_at_least(target_magnitude, mainshock_magnitude, b=gr['b'],
                                         mc=COMPLETENESS_MAGNITUDE)

    if radius_km is not None:
        fraction_spatial, is_radius_modeled = spatial_fraction_within_radius(radius_km, region_key)
    else:
        fraction_spatial, is_radius_modeled = 1.0, False

    lam = expected_all * fraction_mag * fraction_spatial
    probability = 1 - math.exp(-lam)

    return {
        'probability': round(probability, 4),
        'probability_pct': round(probability * 100, 1),
        'expected_count': round(lam, 3),
        'mainshock_magnitude': mainshock_magnitude,
        'target_magnitude': target_magnitude,
        'window_hours': window_hours,
        'hours_since_mainshock': round(hours_since_mainshock, 2),
        'radius_km': radius_km,
        'is_radius_modeled': is_radius_modeled,
        'params_used': {'K': round(K, 3), 'c': c, 'p': p, 'a': gr['a'], 'b': gr['b']},
        'is_default_params': is_default,
        'is_proxy': is_proxy,
        'region_key': region_key,
    }


def fit_omori_params(times_days, counts):
    """
    Fit K, c, p to a historical aftershock sequence via nonlinear least squares.

    Args:
        times_days: list of time bin centers (days since mainshock)
        counts: list of observed aftershock counts (rate, events/day) per bin

    Returns:
        dict with fitted K, c, p, and the covariance-based standard errors.

    Requires scipy (already a project dependency via scikit-learn's stack).
    Not called anywhere yet -- wire this up once real ComCat/PHIVOLCS
    sequence data is available (see module docstring).
    """
    from scipy.optimize import curve_fit
    import numpy as np

    times_days = np.asarray(times_days, dtype=float)
    counts = np.asarray(counts, dtype=float)

    def model(t, K, c, p):
        return K / (t + c) ** p

    # Reasonable starting guesses
    p0 = [max(counts.max(), 1.0), 0.1, 1.1]
    popt, pcov = curve_fit(model, times_days, counts, p0=p0,
                            bounds=([0, 1e-6, 0.3], [np.inf, 10, 3.0]), maxfev=10000)
    perr = np.sqrt(np.diag(pcov))

    # Flag degenerate fits: a parameter pinned at its bound, or a standard
    # error larger than the estimate itself, both indicate the data wasn't
    # informative enough for a reliable fit -- don't trust it silently.
    p_val, p_err = float(popt[2]), float(perr[2])
    hit_bound = p_val >= 2.999 or p_val <= 0.301
    unstable = p_err > abs(p_val)
    if hit_bound or unstable:
        import warnings
        warnings.warn(
            f"fit_omori_params: p={p_val:.3f} (stderr={p_err:.3f}) looks unreliable "
            f"(hit_bound={hit_bound}, stderr>estimate={unstable}). Likely insufficient "
            f"or too-noisy data (n={len(times_days)} bins). Do not treat this fit as "
            f"a trustworthy calibration.", UserWarning
        )

    return {
        'K': float(popt[0]), 'c': float(popt[1]), 'p': p_val,
        'K_stderr': float(perr[0]), 'c_stderr': float(perr[1]), 'p_stderr': p_err,
        'reliable': not (hit_bound or unstable),
    }


def fit_gr_params(magnitudes, mc=COMPLETENESS_MAGNITUDE):
    """
    Fit the Gutenberg-Richter b-value from a catalog of aftershock magnitudes,
    using Aki's (1965) maximum-likelihood estimator:
        b = log10(e) / (mean(M) - Mc)

    Args:
        magnitudes: list of aftershock magnitudes (should already be filtered
                    to >= mc, the catalog completeness magnitude)
        mc: completeness magnitude used as the reference

    Returns:
        dict with fitted b-value and the implied a-value (log10 of the count).

    Not called anywhere yet -- wire this up once real sequence data is available.
    """
    import numpy as np

    magnitudes = np.asarray([m for m in magnitudes if m >= mc], dtype=float)
    if len(magnitudes) < 2:
        raise ValueError('Need at least 2 magnitudes >= mc to fit a b-value')

    mean_m = magnitudes.mean()
    b = math.log10(math.e) / (mean_m - mc)
    a = math.log10(len(magnitudes))  # N(>=Mc) = 10^a at M=Mc reference
    return {'a': float(a), 'b': float(b), 'n_events': int(len(magnitudes))}


def build_forecast_message(forecast):
    """Human-readable forecast message matching how USGS communicates aftershock
    forecasts: an elevated-probability window, not a deterministic prediction."""
    if forecast['is_default_params']:
        caveat = ' (using global default parameters, not yet calibrated for this region)'
    elif forecast.get('is_proxy'):
        caveat = ' (using parameters borrowed from a different, tectonically similar fault -- not directly calibrated for this fault)'
    else:
        caveat = ''

    if forecast.get('radius_km') is not None and forecast.get('is_radius_modeled'):
        radius_phrase = f" within {forecast['radius_km']:.0f}km of the epicenter"
    elif forecast.get('radius_km') is not None:
        radius_phrase = f" (requested {forecast['radius_km']:.0f}km radius not modeled for this region; showing zone-wide estimate)"
    else:
        radius_phrase = ""

    return (
        f"Elevated probability window: {forecast['probability_pct']}% chance of a "
        f"M{forecast['target_magnitude']}+ aftershock{radius_phrase} within {forecast['window_hours']}h "
        f"of the M{forecast['mainshock_magnitude']} mainshock{caveat}. "
        f"This is a probabilistic estimate, not a deterministic prediction."
    )
