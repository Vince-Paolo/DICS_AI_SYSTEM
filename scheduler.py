from datetime import datetime, timedelta

from services.realtime_data import (
    get_all_weather_data,
    get_weather_data,
    get_earthquake_data,
    get_flood_events,
    get_volcano_events,
)
from ai.decision_support import predict_hazard
from models import db, Incident, utcnow

# Earthquake severity is judged from real USGS magnitude readings, not the
# rainfall/river/soil-moisture AI decision support call (which has no
# meaningful relationship to seismic magnitude).
EARTHQUAKE_ALERT_MAGNITUDE = 4.5

# GDACS alert levels (Green/Orange/Red) map directly to a score/level pair,
# the same "confirmed external signal, skip the AI" treatment earthquakes
# get -- a body already tracking floods worldwide reporting an active event
# in the Philippines is itself the hazard signal.
GDACS_ALERT_LEVEL_MAP = {
    'RED': {'score': 90.0, 'level': 'Severe', 'alert': True, 'status': 'ACTIVE'},
    'ORANGE': {'score': 65.0, 'level': 'High', 'alert': True, 'status': 'ACTIVE'},
    'GREEN': {'score': 30.0, 'level': 'Moderate', 'alert': False, 'status': 'NEW'},
}
DEFAULT_POPULATION_DENSITY = 1200
CITY_POPULATION_DENSITY = {
    'Lipa': 1650,
    'Batangas': 2400,
    'Tanauan': 1900,
    'Calamba': 3800,
    'San Pablo': 2400,
    'Lucena': 1350,
    'Tagaytay': 1100,
    'Imus': 16000,
    'Dasmariñas': 12800,
    'Cavite': 6600,
    'Taytay': 13000,
    'Antipolo': 6300,
    'Quezon': 1100,
    'Rizal': 1100,
    'Carmona': 4200,
    'Alaminos': 900,
    'Nagcarlan': 1400,
    'San Fernando': 10300,
}


def _estimate_river_level(rainfall_mm):
    # No real gauge is available, so use a simple rainfall-derived proxy.
    return round(min(15.0, max(0.0, rainfall_mm / 10.0)), 2)


def _population_density_for_city(city):
    return CITY_POPULATION_DENSITY.get(city, DEFAULT_POPULATION_DENSITY)


def _magnitude_to_level(magnitude):
    if magnitude >= 6.0:
        return "Severe"
    if magnitude >= 5.5:
        return "High"
    if magnitude >= 5.0:
        return "Moderate"
    return "Low"


def _magnitude_to_score(magnitude):
    # Simple linear mapping for display purposes: M4.5 -> 50, M7.5+ -> 100
    score = (magnitude - 4.5) / (7.5 - 4.5) * 100
    return max(0.0, min(100.0, round(score, 1)))


def _parse_epoch_millis(raw_value):
    """USGS gives earthquake time as milliseconds since epoch. Returns a
    naive UTC datetime (matching the rest of the app's convention), or None
    if raw_value is missing or malformed -- a bad/absent timestamp from the
    feed should never block incident creation, just leave event_time unset
    so the UI falls back to created_at."""
    if raw_value is None:
        return None
    try:
        return datetime.utcfromtimestamp(float(raw_value) / 1000)
    except (TypeError, ValueError, OSError):
        return None


def _parse_iso_datetime(raw_value):
    """GDACS (fromdate) and EONET (date) both give ISO 8601 datetime
    strings, EONET's with a trailing 'Z'. Returns a naive UTC datetime, or
    None if raw_value is missing or malformed."""
    if not raw_value:
        return None
    try:
        return datetime.fromisoformat(str(raw_value).replace('Z', '+00:00')).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def monitor_earthquakes(app):
    """Check real earthquake feed and raise an alert for significant events."""
    earthquake_data = get_earthquake_data()
    if not earthquake_data:
        app.logger.info("Earthquake monitoring skipped: no earthquake data available")
        return False

    created_any = False
    for quake in earthquake_data:
        magnitude = float(quake.get("magnitude") or 0)
        if magnitude < EARTHQUAKE_ALERT_MAGNITUDE:
            continue

        location = quake.get("location") or quake.get("place") or "CALABARZON region"
        raw_event_id = quake.get("event_id")
        external_event_id = f"usgs:{raw_event_id}" if raw_event_id else None
        event_time = _parse_epoch_millis(quake.get("time"))

        # De-dupe on the quake's own USGS event id when available, not on a
        # wall-clock window: get_earthquake_data() has no guarantee the same
        # physical earthquake won't still be the "most recent" result days
        # or weeks later (CALABARZON doesn't reliably produce 10 new quakes
        # to push an old one out), so a time-boxed check here would just
        # re-alert on that same old event every time the box happened to be
        # running again after the window lapsed. Falling back to a location
        # match (still unbounded, not time-boxed) only covers the rare case
        # where USGS didn't supply a feature id.
        existing_query = Incident.query.filter_by(hazard_type="earthquake")
        if external_event_id:
            existing_query = existing_query.filter_by(external_event_id=external_event_id)
        else:
            existing_query = existing_query.filter_by(location=location)
        recent_incident = existing_query.order_by(Incident.created_at.desc()).first()

        if recent_incident:
            app.logger.info(
                "Earthquake monitoring: incident already recorded for %s (event_id=%s)",
                location, external_event_id,
            )
            continue

        incident = Incident(
            hazard_type="earthquake",
            location=location,
            external_event_id=external_event_id,
            event_time=event_time,
            rainfall_mm=0.0,
            river_level_m=None,
            humidity_pct=0.0,
            population_density=0,
            score=_magnitude_to_score(magnitude),
            level=_magnitude_to_level(magnitude),
            message=f"Magnitude {magnitude:.1f} earthquake detected near {location}.",
            alert=True,
            status='ACTIVE',
            reported_by='system',
        )
        db.session.add(incident)
        created_any = True
        app.logger.info(
            "Earthquake monitoring: created alert for M%.1f near %s", magnitude, location
        )

    if created_any:
        db.session.commit()
    else:
        db.session.rollback()

    return created_any


def monitor_floods_gdacs(app):
    """Check the GDACS global flood feed for active events affecting the
    Philippines and raise an alert directly -- mirrors monitor_earthquakes:
    a confirmed external event is the hazard signal, not an input to the AI.
    """
    flood_events = get_flood_events()
    if not flood_events:
        app.logger.info("GDACS flood monitoring: no Philippine flood events found")
        return False

    created_any = False
    for flood in flood_events:
        alert_level = (flood.get('alert_level') or '').strip().upper()
        mapping = GDACS_ALERT_LEVEL_MAP.get(alert_level)
        if not mapping:
            app.logger.info(
                "GDACS flood monitoring: unrecognized alert level %r for event %s, skipping",
                flood.get('alert_level'), flood.get('event_id'),
            )
            continue

        location = flood.get('name') or flood.get('country') or 'Philippines'
        raw_event_id = flood.get('event_id')
        external_event_id = f"gdacs:{raw_event_id}" if raw_event_id else None
        event_time = _parse_iso_datetime(flood.get('from_date'))

        # De-dupe on the GDACS event's own id (unbounded, and independent of
        # alert level) rather than a wall-clock window. The previous version
        # filtered on alert=True unconditionally, which meant GREEN-level
        # events (alert=False) never matched any prior record and could
        # duplicate on every single 5-minute poll for as long as GDACS kept
        # reporting them as current.
        existing_query = Incident.query.filter_by(hazard_type="flood")
        if external_event_id:
            existing_query = existing_query.filter_by(external_event_id=external_event_id)
        else:
            existing_query = existing_query.filter_by(location=location)
        recent_incident = existing_query.order_by(Incident.created_at.desc()).first()

        if recent_incident:
            app.logger.info("GDACS flood monitoring: incident already recorded for %s (event_id=%s)", location, external_event_id)
            continue

        incident = Incident(
            hazard_type="flood",
            location=location,
            external_event_id=external_event_id,
            event_time=event_time,
            rainfall_mm=0.0,
            river_level_m=None,
            humidity_pct=0.0,
            population_density=0,
            score=mapping['score'],
            level=mapping['level'],
            message=(
                f"GDACS {alert_level.title()} flood alert for {flood.get('country')}"
                f"{': ' + flood.get('name') if flood.get('name') else ''}. "
                f"Source: GDACS (gdacs.org), event #{flood.get('event_id')}."
            ),
            alert=mapping['alert'],
            status=mapping['status'],
            reported_by='system',
        )
        db.session.add(incident)
        created_any = True
        app.logger.info(
            "GDACS flood monitoring: created %s incident for %s", mapping['level'], location
        )

    if created_any:
        db.session.commit()
    else:
        db.session.rollback()

    return created_any


def monitor_volcanoes_eonet(app):
    """Check NASA EONET for open volcanic events near Calabarzon (e.g. Taal)
    and raise an alert directly, same deterministic treatment as
    monitor_earthquakes and monitor_floods_gdacs."""
    volcano_events = get_volcano_events()
    if not volcano_events:
        app.logger.info("EONET volcano monitoring: no open events near Calabarzon")
        return False

    created_any = False
    for volcano in volcano_events:
        location = volcano.get('title') or 'Calabarzon volcano'
        raw_event_id = volcano.get('event_id')
        external_event_id = f"eonet:{raw_event_id}" if raw_event_id else None
        event_time = _parse_iso_datetime(volcano.get('date'))

        # De-dupe on the EONET event's own id (unbounded, not time-boxed),
        # same reasoning as monitor_earthquakes/monitor_floods_gdacs: EONET
        # marks an event "open" for as long as it's actively tracked, which
        # for something like Taal can be weeks -- a wall-clock window here
        # just re-alerts on the same open event every time the window lapses.
        existing_query = Incident.query.filter_by(hazard_type="volcanic")
        if external_event_id:
            existing_query = existing_query.filter_by(external_event_id=external_event_id)
        else:
            existing_query = existing_query.filter_by(location=location)
        recent_incident = existing_query.order_by(Incident.created_at.desc()).first()

        if recent_incident:
            app.logger.info(
                "EONET volcano monitoring: incident already recorded for %s (event_id=%s)",
                location, external_event_id,
            )
            continue

        incident = Incident(
            hazard_type="volcanic",
            location=location,
            external_event_id=external_event_id,
            event_time=event_time,
            rainfall_mm=0.0,
            river_level_m=None,
            humidity_pct=0.0,
            population_density=0,
            score=75.0,
            level="High",
            message=(
                f"NASA EONET reports an open volcanic event: {location} "
                f"(observed {volcano.get('date')}). Source: eonet.gsfc.nasa.gov, "
                f"event {volcano.get('event_id')}."
            ),
            alert=True,
            status='ACTIVE',
            reported_by='system',
        )
        db.session.add(incident)
        created_any = True
        app.logger.info("EONET volcano monitoring: created alert for %s", location)

    if created_any:
        db.session.commit()
    else:
        db.session.rollback()

    return created_any


def monitor_hazards():
    from app import app

    with app.app_context():
        monitor_earthquakes(app)
        monitor_floods_gdacs(app)
        monitor_volcanoes_eonet(app)

        weather_by_city = get_all_weather_data()
        if not weather_by_city:
            app.logger.info("Hazard monitoring skipped: no weather data available")
            return

        created_any = False
        for city, weather_data in weather_by_city.items():
            if not weather_data:
                app.logger.info("Hazard monitoring skipped for %s: no weather data", city)
                continue

            rainfall_mm = float(weather_data.get("rainfall", 0) or 0)
            humidity_pct = float(weather_data.get("humidity", 0) or 0)
            river_level_m = _estimate_river_level(rainfall_mm)
            population_density = _population_density_for_city(city)

            hazard_configs = [
                {
                    "hazard_type": "flood",
                    "rainfall_mm": rainfall_mm,
                    "river_level_m": river_level_m,
                    "humidity_pct": humidity_pct,
                    "population_density": population_density,
                },
                {
                    "hazard_type": "landslide",
                    "rainfall_mm": rainfall_mm,
                    "river_level_m": river_level_m,
                    "humidity_pct": humidity_pct,
                    "population_density": population_density,
                },
            ]

            for config in hazard_configs:
                try:
                    prediction = predict_hazard(**config)
                except Exception as exc:
                    app.logger.warning(
                        "Hazard monitoring: failed to predict %s for %s: %s",
                        config["hazard_type"], city, exc,
                    )
                    continue

                if not prediction:
                    continue

                threshold = 50.0
                level = str(prediction.get("level") or '').strip().upper()
                if level in {'UNKNOWN', 'INSUFFICIENT_DATA', 'INSUFFICIENT DATA', 'INSUFFICIENT-DATA'}:
                    app.logger.warning(
                        "Hazard monitoring: %s in %s returned insufficient data (level %s) and will not be treated as a low-risk outcome.",
                        config["hazard_type"], city, prediction.get("level"),
                    )
                    continue
                if prediction.get("score", 0) < threshold:
                    app.logger.info(
                        "Hazard monitoring: %s in %s score %.1f below threshold %.1f",
                        config["hazard_type"], city,
                        prediction.get("score", 0),
                        threshold,
                    )
                    continue

                recent_incident = Incident.query.filter_by(
                    hazard_type=prediction.get("type", config["hazard_type"]),
                    location=city,
                    alert=True,
                ).filter(Incident.created_at >= utcnow() - timedelta(hours=6)).order_by(Incident.created_at.desc()).first()

                if recent_incident:
                    app.logger.info(
                        "Hazard monitoring: recent alert already exists for %s in %s",
                        prediction.get("type", config["hazard_type"]),
                        city,
                    )
                    continue

                incident = Incident(
                    hazard_type=prediction.get("type", config["hazard_type"]),
                    location=city,
                    rainfall_mm=rainfall_mm,
                    river_level_m=river_level_m,
                    humidity_pct=humidity_pct,
                    population_density=population_density,
                    score=float(prediction.get("score", 0) or 0),
                    level=prediction.get("level", "Moderate"),
                    message=prediction.get("message", "High hazard risk detected."),
                    alert=bool(prediction.get("alert", False)),
                    status='ACTIVE' if prediction.get("alert") else 'NEW',
                    reported_by='system',
                )
                db.session.add(incident)
                created_any = True

        if created_any:
            db.session.commit()
            app.logger.info("Created hazard incidents for monitored hazards in CALABARZON")
        else:
            db.session.rollback()
            app.logger.info("Hazard monitoring: no high-risk incidents created")
