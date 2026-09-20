"""
Recalibration pipeline for aftershock forecasting regions.

Reads a PHIVOLCS earthquake catalog CSV and (re)fits Omori-Utsu + Gutenberg-
Richter parameters for each known candidate mainshock, writing the result to
services/region_params.json. services/aftershock.py loads that file at import
time if present, falling back to its own hardcoded REGION_PARAMS if the file
is missing (so the app still works with zero setup, but recalibrating is now
a matter of running this script, not hand-editing source code).

Usage:
    python scripts/calibrate_aftershock_regions.py path/to/phivolcs_catalog.csv

Expected CSV columns (matches the PHIVOLCS bulletin export format used so
far in this project): Date_Time_PH, Latitude, Longitude, Magnitude, Location

What this script does NOT do:
  - It does not discover new mainshock candidates automatically. The list of
    candidate (name, time, lat, lon, magnitude, search_radius_km) mainshocks
    is defined in CANDIDATE_MAINSHOCKS below and must be updated by hand when
    a new significant earthquake is worth attempting to calibrate against.
    Automating discovery (e.g. "any M5+ event with no other M5+ within Xkm/
    Ydays") is a reasonable future improvement, not implemented here.
  - It does not silently accept unreliable fits. Any fit where
    fit_omori_params() flags reliable=False is EXCLUDED from the output file
    and logged as rejected, exactly like the Quezon/Jomalig attempt earlier
    in this project's history. Rerunning this script will not resurrect a
    rejected fit just because you want more data points -- it takes a larger
    or cleaner sequence to change the outcome.
"""
import sys
import csv
import json
import math
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.aftershock import fit_omori_params, fit_gr_params, PRODUCTIVITY_A  # noqa: E402

COMPLETENESS_MC = 2.0
AFTERSHOCK_WINDOW_DAYS = 90
CONTAMINATION_CHECK_DAYS = 1
CONTAMINATION_CHECK_KM = 100
MIN_EVENTS_FOR_ATTEMPT = 100  # below this, don't even bother attempting a fit
NEAR_FIELD_RADIUS_KM = 50  # for the spatial density fit, restricted to avoid contamination

MIN_MAINSHOCK_MAGNITUDE = 5.0  # candidate mainshocks must be at least this size
CLUSTER_RADIUS_KM = 60.0  # events within this radius of each other are treated as one candidate
                            # cluster (matches the validated Batangas region_radius_km)

# Auto-discovery generates region keys from province names (e.g. "batangas_auto").
# For regions that already have an established, referenced key elsewhere in the
# codebase (scheduler.py's fallback string-match, test_aftershock.py assertions),
# map the auto-generated key to the stable existing one so a rerun doesn't
# silently orphan those references. Add an entry here whenever a region_key is
# hardcoded anywhere outside this pipeline.
STABLE_REGION_KEY_ALIASES = {
    'batangas_auto': 'calabarzon_batangas_offshore',
}


def discover_candidate_mainshocks(catalog, bbox=None):
    """
    Scan the full catalog for M>=MIN_MAINSHOCK_MAGNITUDE events, group them
    into spatial clusters (any two M5+ events within CLUSTER_RADIUS_KM of
    each other are considered the same fault zone), and treat the largest
    event in each cluster as that cluster's mainshock candidate.

    This replaces a manually maintained list -- re-running this script
    against an updated catalog will pick up new mainshock-scale earthquakes
    automatically, without anyone having to notice and hand-add them.

    bbox, if given, is (min_lat, max_lat, min_lon, max_lon) and restricts
    discovery to that area -- this project's PHIVOLCS catalog is national,
    but DICS is a CALABARZON-focused system, so the CLI defaults to a
    Luzon-wide box (see main()) rather than fitting all ~120 nationwide
    clusters (most in Mindanao/Visayas, well outside this system's scope).
    Pass bbox=None to discover nationwide.

    Returns a list of candidate dicts in the same shape the rest of this
    script expects (region_key, name, time, lat, lon, magnitude,
    search_radius_km, region_radius_km, geometry).

    Known limitation: this is simple single-link spatial clustering, not a
    real declustering algorithm (e.g. Reasenberg 1985). Two genuinely
    distinct nearby fault systems within CLUSTER_RADIUS_KM of each other
    would be merged into one candidate. For CALABARZON's known geography
    this hasn't caused a problem (Batangas and Mindoro-edge events cluster
    separately in practice), but it's worth knowing about if this script is
    ever pointed at a denser seismic region.
    """
    big_events = [r for r in catalog if r['_mag'] >= MIN_MAINSHOCK_MAGNITUDE]
    if bbox:
        min_lat, max_lat, min_lon, max_lon = bbox
        big_events = [r for r in big_events
                      if min_lat <= r['_lat'] <= max_lat and min_lon <= r['_lon'] <= max_lon]
    big_events.sort(key=lambda r: -r['_mag'])  # process largest first

    clusters = []  # each entry: {'events': [...], 'center_lat', 'center_lon'}
    assigned = set()
    for i, event in enumerate(big_events):
        if i in assigned:
            continue
        cluster_events = [event]
        assigned.add(i)
        for j, other in enumerate(big_events):
            if j in assigned:
                continue
            if haversine_km(event['_lat'], event['_lon'], other['_lat'], other['_lon']) <= CLUSTER_RADIUS_KM:
                cluster_events.append(other)
                assigned.add(j)
        clusters.append(cluster_events)

    candidates = []
    for cluster_events in clusters:
        mainshock = max(cluster_events, key=lambda r: r['_mag'])
        province = mainshock['Location'].split('(')[-1].replace(')', '').strip() \
            if '(' in mainshock['Location'] else 'unknown'
        region_key = f"{province.lower().replace(' ', '_')}_auto"
        region_key = STABLE_REGION_KEY_ALIASES.get(region_key, region_key)
        candidates.append({
            'region_key': region_key,
            'name': f"{mainshock['Date_Time_PH']} M{mainshock['_mag']} {mainshock['Location']}",
            'time': mainshock['_dt'],
            'lat': mainshock['_lat'], 'lon': mainshock['_lon'], 'magnitude': mainshock['_mag'],
            'search_radius_km': 100,
            'region_radius_km': CLUSTER_RADIUS_KM,
            'geometry': 'circle',
            'cluster_size': len(cluster_events),  # how many M5+ events contributed to this cluster
        })

    candidates.sort(key=lambda c: -c['magnitude'])
    return candidates


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def load_catalog(csv_path):
    rows = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row['_lat'] = float(row['Latitude'])
                row['_lon'] = float(row['Longitude'])
                row['_mag'] = float(row['Magnitude'])
                row['_dt'] = datetime.strptime(row['Date_Time_PH'], '%Y-%m-%d %H:%M:%S')
                rows.append(row)
            except (ValueError, TypeError, KeyError):
                continue
    return rows


def bin_omori_rate(days_arr, n_bins=25):
    days_arr = days_arr[days_arr > 0]
    if len(days_arr) == 0:
        return [], []
    bin_edges = list(__import__('numpy').logspace(
        math.log10(max(days_arr.min(), 0.005)), math.log10(days_arr.max()), n_bins))
    centers, rates = [], []
    for i in range(len(bin_edges) - 1):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        count = ((days_arr >= lo) & (days_arr < hi)).sum()
        if count > 0:
            centers.append((lo + hi) / 2)
            rates.append(count / (hi - lo))
    return centers, rates


def fit_spatial_density(near_field_events, mainshock_lat, mainshock_lon):
    """Fit density(r) = D0*exp(-r/L) to near-field event distances. Returns
    None if too few events for a meaningful fit."""
    import numpy as np
    from scipy.optimize import curve_fit

    distances = np.array([haversine_km(mainshock_lat, mainshock_lon, e['_lat'], e['_lon'])
                           for e in near_field_events])
    if len(distances) < 50:
        return None

    bins = np.arange(0, NEAR_FIELD_RADIUS_KM + 5, 5)
    centers, densities = [], []
    for i in range(len(bins) - 1):
        lo, hi = bins[i], bins[i + 1]
        count = np.sum((distances >= lo) & (distances < hi))
        ring_area = math.pi * (hi ** 2 - lo ** 2)
        if count > 0:
            centers.append((lo + hi) / 2)
            densities.append(count / ring_area)

    if len(centers) < 4:
        return None

    def exp_decay(r, D0, L):
        return D0 * np.exp(-r / L)

    try:
        popt, _ = curve_fit(exp_decay, centers, densities, p0=[0.5, 10], maxfev=5000)
    except RuntimeError:
        return None

    sorted_d = np.sort(distances)
    r90 = float(sorted_d[int(len(sorted_d) * 0.9) - 1])
    return {'D0': round(float(popt[0]), 4), 'L_km': round(float(popt[1]), 3),
            'r90_km': round(r90, 1), 'fit_valid_to_km': float(NEAR_FIELD_RADIUS_KM)}


def calibrate_one(candidate, catalog):
    name = candidate['name']
    mtime, mlat, mlon, mmag = candidate['time'], candidate['lat'], candidate['lon'], candidate['magnitude']
    print(f"\n--- {name} ---")

    contaminated = [
        r for r in catalog
        if mtime < r['_dt'] <= mtime + timedelta(days=CONTAMINATION_CHECK_DAYS)
        and haversine_km(mlat, mlon, r['_lat'], r['_lon']) < CONTAMINATION_CHECK_KM
        and r['_mag'] >= 5.0
    ]
    if contaminated:
        print(f"  WARNING: {len(contaminated)} other M5+ event(s) within "
              f"{CONTAMINATION_CHECK_DAYS}day/{CONTAMINATION_CHECK_KM}km -- sequence may be "
              f"contaminated by a secondary triggered burst.")

    window_end = mtime + timedelta(days=AFTERSHOCK_WINDOW_DAYS)
    aftershocks = [
        r for r in catalog
        if mtime < r['_dt'] <= window_end
        and haversine_km(mlat, mlon, r['_lat'], r['_lon']) <= candidate['search_radius_km']
        and r['_mag'] >= COMPLETENESS_MC
    ]
    print(f"  Aftershocks found: {len(aftershocks)}")

    if len(aftershocks) < MIN_EVENTS_FOR_ATTEMPT:
        print(f"  REJECTED: fewer than {MIN_EVENTS_FOR_ATTEMPT} events, not worth attempting a fit.")
        return None

    import numpy as np
    days_arr = np.array([(r['_dt'] - mtime).total_seconds() / 86400 for r in aftershocks])
    centers, rates = bin_omori_rate(days_arr)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        omori_fit = fit_omori_params(centers, rates)
        for w in caught:
            print(f"  {w.message}")

    if not omori_fit.get('reliable', False):
        print(f"  REJECTED: Omori fit flagged unreliable (p={omori_fit['p']:.3f}, "
              f"stderr={omori_fit['p_stderr']:.3f}). Not written to output.")
        return None

    gr_fit = fit_gr_params([r['_mag'] for r in aftershocks], mc=COMPLETENESS_MC)
    print(f"  Omori: K={omori_fit['K']:.3f} c={omori_fit['c']:.4f} p={omori_fit['p']:.3f}")
    print(f"  GR: a={gr_fit['a']:.3f} b={gr_fit['b']:.3f} (n={gr_fit['n_events']})")

    near_field = [r for r in aftershocks
                  if haversine_km(mlat, mlon, r['_lat'], r['_lon']) <= NEAR_FIELD_RADIUS_KM]
    spatial_fit = fit_spatial_density(near_field, mlat, mlon)
    if spatial_fit:
        print(f"  Spatial: D0={spatial_fit['D0']} L={spatial_fit['L_km']}km r90={spatial_fit['r90_km']}km "
              f"(n={len(near_field)} near-field events)")
    else:
        print("  Spatial: not enough near-field events for a reliable density fit, skipped.")

    return {
        'geometry': candidate['geometry'],
        'center_lat': mlat, 'center_lon': mlon,
        'region_radius_km': candidate['region_radius_km'],
        'omori': {'K': round(omori_fit['K'], 3), 'c': round(omori_fit['c'], 4), 'p': round(omori_fit['p'], 3)},
        'gr': {'a': round(gr_fit['a'], 3), 'b': round(gr_fit['b'], 3)},
        'spatial': spatial_fit,
        'productivity_a': PRODUCTIVITY_A,
        'is_proxy': False,
        'source': f'{name} (PHIVOLCS catalog, {AFTERSHOCK_WINDOW_DAYS}day window, '
                  f'{candidate["search_radius_km"]}km radius, Mc={COMPLETENESS_MC}, n={len(aftershocks)})',
        'calibrated_at': datetime.now(timezone.utc).isoformat(),
    }


# Default discovery scope: Luzon-wide (covers CALABARZON plus the broader
# island where a DICS-relevant fault system could plausibly matter, e.g. the
# 2019 Zambales/Castillejos M6.1 cluster). Does NOT cover Mindanao/Visayas,
# where most of the country's largest, most active clusters are (Davao,
# Surigao, Sarangani) -- those are real and well-recorded, just outside this
# system's operating scope. Pass --nationwide to discover everywhere.
LUZON_BBOX = (12.0, 19.5, 119.0, 124.0)  # (min_lat, max_lat, min_lon, max_lon)


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/calibrate_aftershock_regions.py path/to/phivolcs_catalog.csv [--nationwide]")
        sys.exit(1)

    nationwide = '--nationwide' in sys.argv
    bbox = None if nationwide else LUZON_BBOX

    catalog = load_catalog(sys.argv[1])
    print(f"Loaded {len(catalog)} usable events from catalog.")
    print(f"Discovery scope: {'nationwide' if nationwide else f'Luzon bbox {LUZON_BBOX}'}")

    candidates = discover_candidate_mainshocks(catalog, bbox=bbox)
    print(f"\nAuto-discovered {len(candidates)} candidate mainshock cluster(s) "
          f"(M{MIN_MAINSHOCK_MAGNITUDE}+, {CLUSTER_RADIUS_KM}km clustering radius):")
    for c in candidates:
        print(f"  {c['region_key']:35s} M{c['magnitude']}  ({c['cluster_size']} M5+ events in cluster)  {c['name']}")

    results = {}
    for candidate in candidates:
        fit = calibrate_one(candidate, catalog)
        if fit:
            results[candidate['region_key']] = fit

    output_path = Path(__file__).resolve().parent.parent / 'services' / 'region_params.json'
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n{'=' * 60}")
    print(f"Wrote {len(results)} calibrated region(s) -> {output_path}")
    print(f"Regions NOT included (rejected or below minimum event threshold): "
          f"{len(candidates) - len(results)}")
    print("Proxy regions (e.g. marikina_valley_fault_proxy) are defined separately "
          "in services/aftershock.py and are not touched by this script.")


if __name__ == '__main__':
    main()
