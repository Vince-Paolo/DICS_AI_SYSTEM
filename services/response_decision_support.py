"""Transparent, rule-based response guidance for San Pablo emergencies."""

import math
import re

from flask import current_app
from itsdangerous import URLSafeTimedSerializer

from models import (
    Barangay,
    IncidentResponse,
    Municipality,
    Resource,
    User,
    db,
)
from services.realtime_data import (
    SAN_PABLO_BARANGAYS,
    SAN_PABLO_CITY,
    SAN_PABLO_COORDINATES,
    get_rainfall_watch,
)


OUTCOMES = {
    'BARANGAY_MONITOR': 'Barangay can handle it',
    'BARANGAY_SUPPORT': 'Barangay needs support',
    'CDRRMO_TAKEOVER': 'CDRRMO takes over',
}
DECISION_SUPPORT_VERSION = 'rules-v1'
ASSESSMENT_TOKEN_SALT = 'dispatcher-response-assessment-v1'
ASSESSMENT_TOKEN_MAX_AGE_SECONDS = 15 * 60


def is_assessable_response_incident(incident):
    return (
        incident.hazard_type == 'EMERGENCY'
        or (
            incident.reported_by == 'system'
            and incident.location == SAN_PABLO_CITY
        )
    )


def sign_response_assessment(incident_id, assessment):
    serializer = URLSafeTimedSerializer(
        current_app.secret_key,
        salt=ASSESSMENT_TOKEN_SALT,
    )
    return serializer.dumps({
        'incident_id': incident_id,
        'assessment': assessment,
    })


def load_response_assessment(token, incident_id):
    serializer = URLSafeTimedSerializer(
        current_app.secret_key,
        salt=ASSESSMENT_TOKEN_SALT,
    )
    payload = serializer.loads(token, max_age=ASSESSMENT_TOKEN_MAX_AGE_SECONDS)
    if payload.get('incident_id') != incident_id:
        raise ValueError('Assessment token does not match the incident.')
    assessment = payload.get('assessment')
    if (
        not isinstance(assessment, dict)
        or assessment.get('outcome') not in {*OUTCOMES, None}
    ):
        raise ValueError('Assessment token does not contain a valid outcome.')
    return assessment


def _distance_km(point_a, point_b):
    lat_a, lon_a = map(math.radians, point_a)
    lat_b, lon_b = map(math.radians, point_b)
    delta_lat = lat_b - lat_a
    delta_lon = lon_b - lon_a
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
    )
    haversine = min(1.0, max(0.0, haversine))
    return 6371 * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def _incident_barangay(incident):
    municipality = Municipality.query.filter_by(code='043424').first()
    if municipality is None:
        return None
    if incident.barangay_id:
        barangay = db.session.get(Barangay, incident.barangay_id)
        return barangay if barangay and barangay.municipality_id == municipality.id else None

    location = (incident.location or '').casefold()
    if not location:
        return None
    barangays = Barangay.query.filter_by(municipality_id=municipality.id).all()
    matches = [
        barangay for barangay in barangays
        if re.search(rf'(?<!\w){re.escape(barangay.name.casefold())}(?!\w)', location)
    ]
    return max(matches, key=lambda barangay: len(barangay.name), default=None)


def _active_resource_counts(incident):
    responders = User.query.filter_by(
        role='field_responder',
        is_disabled=False,
    ).all()
    active_responses = IncidentResponse.query.filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).all()
    assigned_responder_ids = set()
    for response in active_responses:
        for task in response.tasks:
            if task.status not in {'PENDING', 'IN_PROGRESS'}:
                continue
            if task.assigned_to_user_id:
                assigned_responder_ids.add(task.assigned_to_user_id)
            if task.assigned_to_team:
                assigned_responder_ids.update(
                    member.id for member in task.assigned_to_team.members
                    if member.role == 'field_responder' and not member.is_disabled
                )

    available_responders = sum(
        responder.id not in assigned_responder_ids for responder in responders
    )
    available_vehicles = db.session.query(
        db.func.coalesce(db.func.sum(Resource.quantity), 0)
    ).filter(
        Resource.resource_type.ilike('%vehicle%'),
        Resource.status == 'AVAILABLE',
    ).scalar()
    competing_incidents = sum(
        response.incident_id != incident.id for response in active_responses
    )
    return {
        'available_responders': available_responders,
        'total_responders': len(responders),
        'available_vehicles': int(available_vehicles or 0),
        'competing_incidents': competing_incidents,
    }


def _gather_assessment_context(incident):
    barangay = _incident_barangay(incident)
    centroid_by_code = {
        entry['code']: (entry['lat'], entry['lon'])
        for entry in SAN_PABLO_BARANGAYS
    }
    rainfall_by_code = {}
    if barangay:
        rainfall_by_code = {
            entry['barangay_code']: entry
            for entry in get_rainfall_watch()
            if entry.get('barangay_code')
        }
    rainfall = rainfall_by_code.get(barangay.code) if barangay else None
    coordinates = None
    if incident.latitude is not None and incident.longitude is not None:
        latitude, longitude = float(incident.latitude), float(incident.longitude)
        if math.isfinite(latitude) and math.isfinite(longitude) and (
            -90 <= latitude <= 90 and -180 <= longitude <= 180
        ):
            coordinates = (latitude, longitude)
    if coordinates is None and barangay:
        coordinates = centroid_by_code.get(barangay.code)

    distance_km = (
        _distance_km(coordinates, SAN_PABLO_COORDINATES)
        if coordinates
        else None
    )
    unit = barangay.response_unit if barangay else None
    return {
        'barangay_name': barangay.name if barangay else None,
        'barangay_unit': unit,
        'rainfall': rainfall,
        'distance_km': distance_km,
        **_active_resource_counts(incident),
    }


def build_response_assessment(incident):
    context = _gather_assessment_context(incident)
    factors = []

    def add_factor(name, value, points, basis):
        factors.append({
            'name': name,
            'value': value,
            'points': points,
            'basis': basis,
        })

    severity = (incident.level or '').strip().casefold()
    severity_known = severity in {'severe', 'critical', 'high', 'moderate', 'medium', 'low'}
    if severity in {'severe', 'critical'}:
        severity_points = 30
        severity_label = incident.level
    elif severity == 'high':
        severity_points = 22
        severity_label = incident.level
    elif severity in {'moderate', 'medium'}:
        severity_points = 14
        severity_label = incident.level
    elif severity == 'low':
        severity_points = 6
        severity_label = incident.level
    elif incident.score is not None:
        score = float(incident.score)
        if math.isfinite(score) and 0 <= score <= 100:
            severity_known = True
            severity_points = 30 if score >= 75 else 22 if score >= 50 else 14 if score >= 25 else 6
            severity_label = f'{score:g}/100 recorded score'
        else:
            severity_points = 0
            severity_label = 'Not assessed'
    else:
        severity_points = 0
        severity_label = 'Not assessed'
    add_factor('Severity', severity_label, severity_points,
               'A missing or invalid severity is not scored; the response category is withheld.')

    hazard_text = f'{incident.hazard_type or ""} {incident.message or ""}'.casefold()
    named_hazard = any(
        word in hazard_text
        for word in ('flood', 'landslide', 'fire', 'earthquake', 'collapse', 'collision', 'medical')
    )
    hazard_points = 8 if named_hazard else 0
    add_factor(
        'Hazard type',
        incident.hazard_type or 'Not recorded',
        hazard_points,
        'A recognized hazard in the report adds 8 points; an unspecified hazard adds no points.',
    )

    patient_count = max(0, incident.patient_count or 0)
    patient_points = 15 if patient_count >= 10 else 10 if patient_count >= 3 else 5 if patient_count else 0
    add_factor('Patients', str(patient_count), patient_points,
               'Patient count adds 5, 10, or 15 points at 1, 3, or 10 patients.')
    add_factor('Vulnerable people', 'Not separately recorded', 0,
               'No structured vulnerable-person count is available in the emergency record.')

    distance_km = context['distance_km']
    if distance_km is None:
        distance_points = 0
        distance_value = 'Unavailable; incident has no usable coordinates or barangay match'
        distance_basis = 'Distance cannot be estimated; the response category is withheld.'
    else:
        distance_points = 10 if distance_km > 10 else 5 if distance_km > 5 else 0
        distance_value = f'{distance_km:.1f} km from San Pablo city-center proxy'
        distance_basis = (
            'This is an approximate city-center distance, not a geocoded CDRRMO base '
            'or barangay-unit distance.'
        )
    add_factor('Distance to response units', distance_value, distance_points, distance_basis)

    responder_count = context['available_responders']
    total_responders = context['total_responders']
    responder_points = (
        8 if total_responders > 0 and responder_count == 0
        else 4 if total_responders > 0 and responder_count <= 2
        else 0
    )
    add_factor(
        'Free CDRRMO personnel',
        f'{responder_count} of {total_responders} active responders not assigned to open tasks',
        responder_points,
        'Availability is inferred from active responders and pending/in-progress assignments.',
    )
    add_factor(
        'Available vehicles',
        f"{context['available_vehicles']} recorded as available",
        0,
        'Vehicle inventory completeness is unknown; this count does not establish fleet-wide availability.',
    )

    unit = context['barangay_unit']
    if unit is None or unit.team_size is None:
        capacity_points = 0
        capacity_value = 'Not recorded'
        capacity_basis = 'No team-size capacity is recorded for the matched barangay response unit.'
    elif unit.team_size < 5:
        capacity_points = 8
        capacity_value = f'{unit.team_size} team members'
        capacity_basis = 'Fewer than 5 recorded members adds 8 points.'
    elif unit.team_size < 10:
        capacity_points = 4
        capacity_value = f'{unit.team_size} team members'
        capacity_basis = '5–9 recorded members adds 4 points.'
    else:
        capacity_points = -5 if unit.equipment else 0
        capacity_value = f'{unit.team_size} team members; equipment {"listed" if unit.equipment else "not listed"}'
        capacity_basis = 'At least 10 members with listed equipment reduces the score by 5 points.'
    add_factor('Barangay response capacity', capacity_value, capacity_points, capacity_basis)

    competing = context['competing_incidents']
    competing_points = 10 if competing >= 3 else 5 if competing else 0
    add_factor('Competing active incidents', str(competing), competing_points,
               'Three or more competing active responses adds 10 points; one or two adds 5.')

    rainfall = context['rainfall']
    rainfall_mm = None
    rainfall_24h_mm = None
    if rainfall:
        rainfall_mm = rainfall.get('rainfall_mm')
        rainfall_24h_mm = rainfall.get('rainfall_24h_mm')
    if rainfall_mm is None:
        rainfall_mm = incident.rainfall_mm
    if rainfall_mm is None:
        rainfall_points = 0
        rainfall_value = 'Unavailable for this barangay'
        rainfall_basis = 'No barangay estimate or incident rainfall reading is available.'
    else:
        rainfall_mm = float(rainfall_mm)
        rainfall_points = 12 if rainfall_mm >= 30 else 9 if rainfall_mm >= 15 else 5 if rainfall_mm >= 7.5 else 2 if rainfall_mm > 0 else 0
        rainfall_value = f'{rainfall_mm:g} mm/h current estimate'
        if rainfall_24h_mm is not None:
            rainfall_value += f'; {float(rainfall_24h_mm):g} mm over 24 h'
        rainfall_basis = 'Current hourly rainfall adds points at 7.5, 15, and 30 mm/h thresholds.'
    add_factor('Barangay rainfall', rainfall_value, rainfall_points, rainfall_basis)

    reported_flood_landslide = any(word in hazard_text for word in ('flood', 'landslide'))
    add_factor(
        'Flood or landslide exposure',
        'Reported hazard matches flood/landslide' if reported_flood_landslide
        else 'No validated barangay exposure layer available',
        8 if reported_flood_landslide else 0,
        'Points reflect the reported incident type only; no local susceptibility map is configured.',
    )

    if not severity_known or distance_km is None:
        score = None
        outcome = None
        outcome_label = 'Insufficient information'
    else:
        score = min(100, max(0, sum(factor['points'] for factor in factors)))
        if score >= 60:
            outcome = 'CDRRMO_TAKEOVER'
        elif score >= 25:
            outcome = 'BARANGAY_SUPPORT'
        else:
            outcome = 'BARANGAY_MONITOR'
        outcome_label = OUTCOMES[outcome]
    return {
        'score': score,
        'outcome': outcome,
        'outcome_label': outcome_label,
        'factors': factors,
        'rules_version': DECISION_SUPPORT_VERSION,
        'barangay_name': context['barangay_name'],
        'barangay_unit_id': context['barangay_unit'].id if context['barangay_unit'] else None,
    }
