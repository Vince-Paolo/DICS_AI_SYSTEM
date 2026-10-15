import json
import math
from datetime import timedelta

from sqlalchemy import text

from services.realtime_data import (
    SAN_PABLO_CITY,
    SAN_PABLO_COORDINATES,
    get_all_weather_data,
    get_earthquake_data,
    get_flood_events,
    get_volcano_events,
)
from ai.decision_support import predict_hazard
from models import AIRecommendation, Alert, AuditEvent, db, Incident, utcnow

# External events are dispatcher-only situational alerts, not incidents.
REGIONAL_ALERT_RADIUS_KM = 100
EARTHQUAKE_ALERT_MAGNITUDE = 4.5
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


def _population_density_for_city(city):
    return CITY_POPULATION_DENSITY.get(city, DEFAULT_POPULATION_DENSITY)


def _regional_event_distance_km(latitude, longitude):
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None

    city_latitude, city_longitude = SAN_PABLO_COORDINATES
    latitude_delta = math.radians(latitude - city_latitude)
    longitude_delta = math.radians(longitude - city_longitude)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(math.radians(city_latitude))
        * math.cos(math.radians(latitude))
        * math.sin(longitude_delta / 2) ** 2
    )
    return 6371 * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def _create_dispatcher_event_alert(
    app, source, hazard, event_id, latitude, longitude, title, message, severity,
):
    if not event_id:
        app.logger.warning('%s %s event has no id and cannot be deduplicated', source, hazard)
        return False

    distance_km = _regional_event_distance_km(latitude, longitude)
    if distance_km is None or distance_km > REGIONAL_ALERT_RADIUS_KM:
        return False

    external_event_id = f'{source.lower()}:{event_id}'
    if Alert.query.filter_by(external_event_id=external_event_id).first():
        app.logger.info(
            '%s %s event already logged for dispatchers (event_id=%s)',
            source, hazard, event_id,
        )
        return False

    alert = Alert(
        audience='DISPATCHER',
        external_event_id=external_event_id,
        title=title[:200],
        message=(
            f'{message} Approximately {distance_km:.1f} km from San Pablo City. '
            'Regional external-feed signal; verify locally before response.'
        ),
        severity=severity,
        status='ACTIVE',
    )
    db.session.add(alert)
    db.session.flush()
    db.session.add(AuditEvent(
        entity_type='Alert',
        entity_id=alert.id,
        action='EXTERNAL_EVENT_LOGGED',
        details=(
            f'{source} {hazard} event {event_id} logged for dispatcher review '
            f'at {distance_km:.1f} km from San Pablo City.'
        ),
    ))
    return True


def monitor_earthquakes(app):
    """Log nearby significant USGS earthquakes as dispatcher-only alerts."""
    earthquake_data = get_earthquake_data()
    if not earthquake_data:
        app.logger.info("Earthquake monitoring skipped: no earthquake data available")
        return False

    created_any = False
    for quake in earthquake_data:
        try:
            magnitude = float(quake.get("magnitude") or 0)
        except (TypeError, ValueError):
            app.logger.warning(
                'Earthquake monitoring skipped event with invalid magnitude: %s',
                quake.get('event_id'),
            )
            continue
        if not math.isfinite(magnitude):
            app.logger.warning(
                'Earthquake monitoring skipped event with invalid magnitude: %s',
                quake.get('event_id'),
            )
            continue
        if magnitude < EARTHQUAKE_ALERT_MAGNITUDE:
            continue

        location = quake.get('location') or quake.get('place') or 'Regional area'
        magnitude_text = f'Magnitude {magnitude:.1f}'
        severity = 'CRITICAL' if magnitude >= 6.0 else 'HIGH' if magnitude >= 5.5 else 'MEDIUM'
        created_any |= _create_dispatcher_event_alert(
            app=app,
            source='USGS',
            hazard='earthquake',
            event_id=quake.get('event_id'),
            latitude=quake.get('lat'),
            longitude=quake.get('lon'),
            title=f'{magnitude_text} earthquake near {location}',
            message=(
                f'{magnitude_text} earthquake reported near {location}. '
                f'Event time (epoch ms): {quake.get("time") or "unavailable"}. '
                'Source: USGS.'
            ),
            severity=severity,
        )

    if created_any:
        db.session.commit()
    else:
        db.session.rollback()

    return created_any


def monitor_floods_gdacs(app):
    """Log nearby active Philippine GDACS floods as dispatcher-only alerts."""
    flood_events = get_flood_events()
    if not flood_events:
        app.logger.info("GDACS flood monitoring: no Philippine flood events found")
        return False

    created_any = False
    for flood in flood_events:
        if flood.get('is_current') is False:
            continue
        alert_level = (flood.get('alert_level') or '').strip().upper()
        severity_by_level = {'RED': 'CRITICAL', 'ORANGE': 'HIGH', 'GREEN': 'LOW'}
        severity = severity_by_level.get(alert_level)
        if severity is None:
            app.logger.info(
                "GDACS flood monitoring: unrecognized alert level %r for event %s, skipping",
                flood.get('alert_level'), flood.get('event_id'),
            )
            continue

        location = flood.get('name') or flood.get('country') or 'Philippines'
        created_any |= _create_dispatcher_event_alert(
            app=app,
            source='GDACS',
            hazard='flood',
            event_id=flood.get('event_id'),
            latitude=flood.get('lat'),
            longitude=flood.get('lon'),
            title=f'GDACS {alert_level.title()} flood near {location}',
            message=(
                f'GDACS reports an active {alert_level.title()} flood near {location}'
                f'{": " + str(flood.get("severity_text")) if flood.get("severity_text") else ""}. '
                f'Event start: {flood.get("from_date") or "unavailable"}. Source: GDACS.'
            ),
            severity=severity,
        )

    if created_any:
        db.session.commit()
    else:
        db.session.rollback()

    return created_any


def monitor_volcanoes_eonet(app):
    """Log nearby open EONET volcano events as dispatcher-only alerts."""
    volcano_events = get_volcano_events()
    if not volcano_events:
        app.logger.info("EONET volcano monitoring: no open events near Calabarzon")
        return False

    created_any = False
    for volcano in volcano_events:
        location = volcano.get('title') or 'Calabarzon volcano'
        created_any |= _create_dispatcher_event_alert(
            app=app,
            source='EONET',
            hazard='volcano',
            event_id=volcano.get('event_id'),
            latitude=volcano.get('lat'),
            longitude=volcano.get('lon'),
            title=f'Open volcanic event: {location}',
            message=(
                f'NASA EONET reports an open volcanic event at {location} '
                f'(observed {volcano.get("date") or "date unavailable"}). '
                f'Source: NASA EONET. {volcano.get("link") or ""}'
            ),
            severity='HIGH',
        )

    if created_any:
        db.session.commit()
    else:
        db.session.rollback()

    return created_any


_HAZARD_MONITOR_LOCK_KEY = 82463917


def monitor_hazards():
    """Run one monitor cycle, allowing only one Postgres worker to proceed."""
    from app import app

    with app.app_context():
        if db.engine.dialect.name == 'postgresql':
            # A module-level flag cannot coordinate separate Gunicorn workers.
            # Keep the advisory-lock connection open for the whole cycle.
            with db.engine.connect() as lock_connection:
                acquired = lock_connection.execute(
                    text('SELECT pg_try_advisory_lock(:key)'),
                    {'key': _HAZARD_MONITOR_LOCK_KEY},
                ).scalar()
                if not acquired:
                    app.logger.info('Hazard monitoring skipped: another worker owns the monitor lock')
                    return
                try:
                    _monitor_hazards_once(app)
                finally:
                    lock_connection.execute(
                        text('SELECT pg_advisory_unlock(:key)'),
                        {'key': _HAZARD_MONITOR_LOCK_KEY},
                    )
                    lock_connection.commit()
            return

        _monitor_hazards_once(app)


def _monitor_hazards_once(app):
        monitor_earthquakes(app)
        monitor_floods_gdacs(app)
        monitor_volcanoes_eonet(app)

        weather_by_city = get_all_weather_data()
        san_pablo_weather = weather_by_city.get(SAN_PABLO_CITY) if weather_by_city else None
        if not san_pablo_weather:
            app.logger.info("Hazard monitoring skipped: no San Pablo weather data available")
            return

        weather_by_city = {SAN_PABLO_CITY: san_pablo_weather}
        created_any = False
        for city, weather_data in weather_by_city.items():
            if not weather_data:
                app.logger.info("Hazard monitoring skipped for %s: no weather data", city)
                continue

            rainfall_mm = float(weather_data.get("rainfall", 0) or 0)
            humidity_pct = float(weather_data.get("humidity", 0) or 0)
            river_level_m = None
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

                city_coordinates = SAN_PABLO_COORDINATES if city == SAN_PABLO_CITY else None
                incident = Incident(
                    hazard_type=prediction.get("type", config["hazard_type"]),
                    location=city,
                    latitude=(weather_data.get('lat') if weather_data.get('lat') is not None else (city_coordinates[0] if city_coordinates else None)),
                    longitude=(weather_data.get('lon') if weather_data.get('lon') is not None else (city_coordinates[1] if city_coordinates else None)),
                    rainfall_mm=rainfall_mm,
                    river_level_m=river_level_m,
                    humidity_pct=humidity_pct,
                    population_density=population_density,
                    score=float(prediction.get("score", 0) or 0),
                    level=prediction.get("level", "Moderate"),
                    message=prediction.get("message", "High hazard risk detected."),
                    alert=bool(prediction.get("alert", False)),
                    status='NEW',
                    reported_by='system',
                )
                db.session.add(incident)
                db.session.flush()
                recommendation = AIRecommendation(
                    incident_id=incident.id,
                    provider=prediction.get('provider'),
                    model=prediction.get('model'),
                    recommendation_type='hazard_prediction',
                    summary=prediction.get('message', '').strip() or 'AI hazard prediction generated.',
                    confidence_score=prediction.get('confidence'),
                    recommended_resources=json.dumps(prediction.get('recommended_resources', [])),
                    primary_factors=json.dumps(prediction.get('primary_factors', [])),
                )
                db.session.add(recommendation)
                db.session.flush()
                db.session.add(AuditEvent(
                    entity_type='AIRecommendation',
                    entity_id=recommendation.id,
                    action='CREATED',
                    details=(
                        f"Scheduled hazard prediction created from provider={prediction.get('provider')} "
                        f"model={prediction.get('model')} score={prediction.get('score')} "
                        f"for incident_id={incident.id}"
                    ),
                ))
                created_any = True

        if created_any:
            db.session.commit()
            app.logger.info("Created hazard incidents for monitored hazards in San Pablo City")
        else:
            db.session.rollback()
            app.logger.info("Hazard monitoring: no high-risk incidents created")
