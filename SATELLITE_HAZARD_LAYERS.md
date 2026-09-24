# Satellite hazard layers — what changed and how to enable it

## Files (drop-in replacements for the matching paths in your repo)
- `services/realtime_data.py` -> replaces `services/realtime_data.py`
- `app.py` -> replaces `app.py`
- `hazard_map.html` -> replaces `templates/pages/hazard_map.html`
- `test_external_hazard_feeds.py` -> replaces `tests/test_external_hazard_feeds.py`

No database migration needed — these are new API endpoints and frontend
overlays, not new tables.

## What was added

**1. Satellite basemap toggle** — "Satellite View" button swaps the OSM
street layer for Esri World Imagery (free, no API key). Ground truth under
the markers: terrain, flood plains, urban density.

**2. Volcanic — satellite thermal-hotspot overlay (NASA FIRMS)**
`get_thermal_hotspots()` pulls near-real-time VIIRS thermal-anomaly
detections over Calabarzon. This is a genuine independent satellite
cross-check next to EONET's "open event" status — EONET says a volcano is
active, FIRMS shows the actual thermal signature. Rendered as small orange
dots via `/api/hazard-layers/thermal-hotspots`, toggled with "Satellite
Thermal Hotspots."
**Caveat baked into the code comments and popup text:** FIRMS doesn't
distinguish volcanic thermal signatures from wildfires/agricultural
burning — worth a line in your thesis limitations section too.
**Setup:** register a free MAP_KEY at
https://firms.modaps.eosdis.nasa.gov/api/map_key/ and set
`FIRMS_MAP_KEY=your_key` in `.env` (same pattern as `OPENWEATHER_API_KEY`).
Without it, the endpoint just returns `[]` — nothing breaks.

**3. Flood — satellite/hydrological flood-extent polygons (GDACS/EC-JRC)**
`get_flood_events()` now also captures each event's `episode_id`.
`get_flood_footprints()` uses that to call GDACS's polygon endpoint
(`/api/polygons/getgeometry`) and get the actual EC-JRC-modeled flood
extent — a real shape, not just a point. Rendered as a translucent blue
polygon via `/api/hazard-layers/flood-footprints`, toggled with "Flood
Extent (Satellite)." No API key needed. Not every flood episode has a
published footprint — those are silently skipped.

**4. Earthquake** — intentionally left alone. Satellites don't do live
quake detection; the honest framing is post-event damage assessment
(Copernicus EMS rapid mapping), which is a different feature, not a live
hazard layer. Worth stating as a scoping decision in your paper rather
than forcing something that doesn't reflect how satellite data actually
works for seismic events.

## Testing
- Added 5 new unit tests (`test_get_thermal_hotspots_*`,
  `test_get_flood_footprints_*`) alongside your existing hazard-feed
  tests, same style (mocked `_fetch_json`/`_fetch_text`, no live network
  calls).
- Ran the full existing suite against these changes: 26/26 in
  `test_external_hazard_feeds.py`, 176/178 overall. The 2 failures
  (`test_secret_key_generates_random_value_when_unset`,
  `test_load_dotenv_file_sets_environment`) reproduce identically on your
  original unmodified code — pre-existing test-isolation flakiness around
  `SECRET_KEY`/dotenv loading order, unrelated to this change.
- Couldn't live-test against the real FIRMS/GDACS endpoints from this
  sandbox (network egress here is limited to package registries), so
  test it against the live APIs in your own environment before treating
  it as done — same as you'd already need to do for any new integration.

## Thesis alignment note
Given the Chapter 1-3 mismatch you already had to fix, if you ship this,
whichever chapter describes your GIS/hazard-map architecture should
mention the FIRMS/GDACS-footprint layers and the volcanic-vs-wildfire
caveat, so the same "thesis says X, system does Y" gap doesn't reopen.
