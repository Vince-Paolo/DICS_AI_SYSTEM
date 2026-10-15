from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import validates
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

db = SQLAlchemy()
MANILA_TIMEZONE = ZoneInfo('Asia/Manila')

control_number_sequence = db.Table(
    'control_number_sequence',
    db.metadata,
    db.Column('year', db.Integer, primary_key=True),
    db.Column('last_number', db.Integer, nullable=False),
)


def _next_control_number(connection, incident):
    if incident.created_at is None:
        incident.created_at = utcnow()
    created_at = incident.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    year = created_at.astimezone(MANILA_TIMEZONE).year
    if connection.dialect.name == 'postgresql':
        insert_statement = postgresql_insert(control_number_sequence)
    elif connection.dialect.name == 'sqlite':
        insert_statement = sqlite_insert(control_number_sequence)
    else:
        raise RuntimeError(
            f'Atomic incident control numbers are not supported for {connection.dialect.name}.'
        )

    statement = insert_statement.values(year=year, last_number=1).on_conflict_do_update(
        index_elements=[control_number_sequence.c.year],
        set_={'last_number': control_number_sequence.c.last_number + 1},
    ).returning(control_number_sequence.c.last_number)
    sequence = connection.execute(statement).scalar_one()
    return f'{year}-{sequence:05d}'


def utcnow():
    """Naive UTC 'now', for use as a SQLAlchemy column default/onupdate and
    anywhere else the app needs the current UTC time.

    datetime.utcnow() is deprecated (Python 3.12+) in favor of
    datetime.now(timezone.utc), but that returns a timezone-*aware*
    datetime -- swapping it in directly would break every comparison
    against the naive datetimes SQLite/SQLAlchemy already store for every
    existing created_at/updated_at/etc. column and value in this database.
    This gives the exact same naive-UTC value datetime.utcnow() always did,
    via the non-deprecated API, so nothing else in the app has to change."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    """SQLite does not enforce foreign key constraints by default. Without
    this, deleting a row (including via raw SQL or a database tool) can
    silently leave dependent rows pointing at nothing -- e.g. an
    IncidentResponse whose incident_id no longer matches any Incident. That
    orphaned state is what caused 500s/404s when a commander opened an
    incident response tied to a missing incident. Turning this on makes the
    ondelete='CASCADE' rules below actually apply."""
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    except Exception:
        # Non-SQLite engines (e.g. Postgres in production) don't need this
        # and don't support this pragma; ignore quietly.
        pass


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    full_name = db.Column(db.String(150), nullable=True)
    contact_number = db.Column(db.String(20), nullable=True)
    email_verified = db.Column(db.Boolean, default=False)
    verification_token = db.Column(db.String(500), nullable=True)
    verification_expires_at = db.Column(db.DateTime, nullable=True)
    verification_attempts = db.Column(db.Integer, default=0)
    verification_sent_at = db.Column(db.DateTime, nullable=True)
    reset_token = db.Column(db.String(500), nullable=True)
    reset_token_expires_at = db.Column(db.DateTime, nullable=True)
    role = db.Column(db.String(20), default='user')
    is_disabled = db.Column(db.Boolean, default=False)
    must_change_password = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    incidents = db.relationship('Incident', foreign_keys='[Incident.user_id]', backref='user', lazy=True)
    citizen_reports = db.relationship('CitizenReport', backref='user', lazy=True)
    alerts = db.relationship('Alert', backref='user', lazy=True, cascade='all, delete-orphan')
    reports = db.relationship('Report', backref='user', lazy=True, cascade='all, delete-orphan')
    audit_events = db.relationship('AuditEvent', backref='user', lazy=True, cascade='all, delete-orphan')
    ai_recommendations = db.relationship(
        'AIRecommendation',
        foreign_keys='[AIRecommendation.user_id]',
        backref='user',
        lazy=True,
        cascade='all, delete-orphan',
    )

    @property
    def password_hash(self):
        return self.password

    @validates('email')
    def normalize_email(self, key, value):
        return value.strip().lower() if isinstance(value, str) else value


db.Index('uq_user_email_lower', db.func.lower(User.email), unique=True)


class Province(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=True)
    name = db.Column(db.String(150), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    municipalities = db.relationship('Municipality', backref='province', lazy=True, cascade='all, delete-orphan')


class Municipality(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    province_id = db.Column(db.Integer, db.ForeignKey('province.id'), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=True)
    name = db.Column(db.String(150), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    barangays = db.relationship('Barangay', backref='municipality', lazy=True, cascade='all, delete-orphan')


class Barangay(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    municipality_id = db.Column(db.Integer, db.ForeignKey('municipality.id'), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=True)
    name = db.Column(db.String(150), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    response_unit = db.relationship(
        'BarangayResponseUnit',
        backref='barangay',
        uselist=False,
        cascade='all, delete-orphan',
    )


response_team_members = db.Table(
    'response_team_members',
    db.metadata,
    db.Column('team_id', db.Integer, db.ForeignKey('response_team.id', ondelete='CASCADE'), primary_key=True),
    db.Column('user_id', db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True),
)


class ResponseTeam(db.Model):
    __tablename__ = 'response_team'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    members = db.relationship('User', secondary=response_team_members, backref='response_teams')


class BarangayResponseUnit(db.Model):
    __tablename__ = 'barangay_response_unit'

    id = db.Column(db.Integer, primary_key=True)
    barangay_id = db.Column(db.Integer, db.ForeignKey('barangay.id', ondelete='CASCADE'), nullable=False, unique=True)
    contact_person = db.Column(db.String(150), nullable=True)
    contact_number = db.Column(db.String(30), nullable=True)
    team_size = db.Column(db.Integer, nullable=True)
    equipment = db.Column(db.Text, nullable=True)
    location = db.Column(db.String(255), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    updated_by = db.relationship('User', foreign_keys=[updated_by_id])


class BarangayContactLog(db.Model):
    __tablename__ = 'barangay_contact_log'

    id = db.Column(db.Integer, primary_key=True)
    incident_id = db.Column(db.Integer, db.ForeignKey('incident.id', ondelete='CASCADE'), nullable=False, index=True)
    response_unit_id = db.Column(db.Integer, db.ForeignKey('barangay_response_unit.id'), nullable=False, index=True)
    contacted_person = db.Column(db.String(150), nullable=True)
    contact_method = db.Column(db.String(20), nullable=False, default='PHONE')
    outcome = db.Column(db.String(40), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    contacted_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    logged_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    response_unit = db.relationship('BarangayResponseUnit', backref='contact_logs')
    logged_by = db.relationship('User', foreign_keys=[logged_by_id])


class CitizenReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    hazard_type = db.Column(db.String(50), nullable=False)
    severity = db.Column(db.String(20), nullable=False)
    location = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)
    affected_people = db.Column(db.Integer, nullable=True)
    injuries = db.Column(db.Integer, nullable=True)
    contact = db.Column(db.String(30), nullable=True)
    gps_latitude = db.Column(db.Float, nullable=True)
    gps_longitude = db.Column(db.Float, nullable=True)
    # Radius in meters reported by the browser's Geolocation API
    # (position.coords.accuracy) for a device GPS fix, or null when the
    # point instead came from an address search / manual pin placement --
    # those aren't a device-measured accuracy, so storing one would be
    # misleading to whoever triages the report.
    gps_accuracy = db.Column(db.Float, nullable=True)
    province_id = db.Column(db.Integer, db.ForeignKey('province.id'), nullable=True)
    municipality_id = db.Column(db.Integer, db.ForeignKey('municipality.id'), nullable=True)
    barangay_id = db.Column(db.Integer, db.ForeignKey('barangay.id'), nullable=True)
    anonymous = db.Column(db.Boolean, default=False)
    photo_filename = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class Incident(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    control_number = db.Column(db.String(20), nullable=False, unique=True)
    patient_count = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    hazard_type = db.Column(db.String(50), nullable=False)
    location = db.Column(db.String(255), nullable=True)
    rainfall_mm = db.Column(db.Float, nullable=True)
    river_level_m = db.Column(db.Float, nullable=True)
    humidity_pct = db.Column(db.Float, nullable=True)
    population_density = db.Column(db.Float, nullable=True)
    score = db.Column(db.Float, nullable=True)
    level = db.Column(db.String(20), nullable=True)
    message = db.Column(db.String(255), nullable=False)
    alert = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(20), default='NEW')
    verified_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    citizen_report_id = db.Column(db.Integer, db.ForeignKey('citizen_report.id'), nullable=True)
    # Stable identity of the source event from an external feed (e.g.
    # "usgs:us7000abcd", "gdacs:1234567", "eonet:EONET_1111"), used to
    # de-duplicate hazard-monitor incidents by the physical event itself
    # rather than by location text + a wall-clock window. Null for
    # citizen-reported and AI-weather-predicted incidents, which have no
    # external event identity.
    external_event_id = db.Column(db.String(120), nullable=True, unique=True)
    # When the hazard actually occurred/was observed by the source feed
    # (e.g. the USGS quake time, GDACS flood from_date, EONET event date).
    # Distinct from created_at, which is only when *we* logged the row --
    # for a hazard-monitor incident those can be days apart if the source
    # feed was slow to update or our own dedup delayed ingestion. Null for
    # citizen-reported and AI-weather-predicted incidents, where "when we
    # logged it" and "when it happened" are effectively the same moment.
    event_time = db.Column(db.DateTime, nullable=True)
    reported_by = db.Column(db.String(50), nullable=True)
    province_id = db.Column(db.Integer, db.ForeignKey('province.id'), nullable=True)
    municipality_id = db.Column(db.Integer, db.ForeignKey('municipality.id'), nullable=True)
    barangay_id = db.Column(db.Integer, db.ForeignKey('barangay.id'), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    citizen_report = db.relationship('CitizenReport', backref=db.backref('incident', uselist=False), foreign_keys=[citizen_report_id])
    response = db.relationship('IncidentResponse', backref='incident', lazy=True, uselist=False, cascade='all, delete-orphan')
    verifier = db.relationship('User', foreign_keys=[verified_by_id], backref='verified_incidents')
    reports = db.relationship('Report', backref='incident', lazy=True, cascade='all, delete-orphan')
    alerts = db.relationship('Alert', backref='incident', lazy=True, cascade='all, delete-orphan')
    ai_recommendations = db.relationship('AIRecommendation', backref='incident', lazy=True, cascade='all, delete-orphan')
    patient_records = db.relationship(
        'PatientRecord',
        backref='incident',
        lazy=True,
        cascade='all, delete-orphan',
        order_by='PatientRecord.id',
    )
    contact_logs = db.relationship(
        'BarangayContactLog',
        backref='incident',
        lazy=True,
        cascade='all, delete-orphan',
        order_by='BarangayContactLog.contacted_at.desc()',
    )

    @property
    def display_time(self):
        """The best available timestamp for 'when did this actually happen',
        for UI display. Falls back to created_at (when we logged it) for
        incidents with no independently-known event_time -- citizen reports
        and AI-weather-predicted incidents, where that distinction doesn't
        apply."""
        return self.event_time or self.created_at


@event.listens_for(Incident, 'before_insert')
def assign_incident_control_number(mapper, connection, incident):
    if not incident.control_number:
        incident.control_number = _next_control_number(connection, incident)


class PatientRecord(db.Model):
    """Minimal, non-identifying patient tracking for an emergency incident."""
    id = db.Column(db.Integer, primary_key=True)
    incident_id = db.Column(db.Integer, db.ForeignKey('incident.id', ondelete='CASCADE'), nullable=False, index=True)
    condition = db.Column(db.String(255), nullable=False)
    destination = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(50), nullable=False, default='RECEIVING_CARE')
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class IncidentResponse(db.Model):
    """Active incident response coordination"""
    id = db.Column(db.Integer, primary_key=True)
    incident_id = db.Column(db.Integer, db.ForeignKey('incident.id', ondelete='CASCADE'), nullable=False)
    commander_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    dispatched_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    status = db.Column(db.String(20), default='ACTIVE', index=True)  # ACTIVE, MONITORING, RESOLVED, CLOSED
    situation_summary = db.Column(db.Text, nullable=True)
    priority_level = db.Column(db.String(20), default='MEDIUM')  # LOW, MEDIUM, HIGH, CRITICAL
    affected_population = db.Column(db.Integer, nullable=True)
    started_at = db.Column(db.DateTime, default=utcnow, index=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    closed_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    commander = db.relationship('User', foreign_keys=[commander_id], backref='incident_responses')
    dispatched_by = db.relationship('User', foreign_keys=[dispatched_by_id], backref='dispatched_responses')
    tasks = db.relationship('Task', backref='incident_response', lazy=True, cascade='all, delete-orphan')
    resources = db.relationship('Resource', backref='incident_response', lazy=True, cascade='all, delete-orphan')
    messages = db.relationship('IncidentMessage', backref='incident_response', lazy=True, cascade='all, delete-orphan')


class Task(db.Model):
    """Incident response tasks assigned to personnel or teams."""
    version_id = db.Column(db.Integer, nullable=False, default=1)
    __mapper_args__ = {'version_id_col': version_id}
    id = db.Column(db.Integer, primary_key=True)
    incident_response_id = db.Column(db.Integer, db.ForeignKey('incident_response.id', ondelete='CASCADE'), nullable=False)
    assigned_to_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    assigned_to_team_id = db.Column(db.Integer, db.ForeignKey('response_team.id'), nullable=True, index=True)
    assigned_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='PENDING')  # PENDING, IN_PROGRESS, COMPLETED
    remarks = db.Column(db.Text, nullable=True)
    priority = db.Column(db.String(20), default='MEDIUM')  # LOW, MEDIUM, HIGH, CRITICAL
    estimated_completion = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    assigned_by = db.relationship('User', backref='assigned_tasks', foreign_keys=[assigned_by_id])
    assigned_to_user = db.relationship('User', foreign_keys=[assigned_to_user_id], backref='received_tasks')
    assigned_to_team = db.relationship('ResponseTeam', backref='tasks')

    @property
    def assignee_label(self):
        if self.assigned_to_user:
            return self.assigned_to_user.full_name or self.assigned_to_user.username
        if self.assigned_to_team:
            return self.assigned_to_team.name
        return 'Unassigned'


class IncidentMessage(db.Model):
    """Unified inter-role incident communications log."""
    id = db.Column(db.Integer, primary_key=True)
    incident_response_id = db.Column(db.Integer, db.ForeignKey('incident_response.id', ondelete='CASCADE'), nullable=False, index=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    report_type = db.Column(db.String(50), default='UPDATE')
    source = db.Column(db.String(20), default='coordinator')
    affected_areas = db.Column(db.String(500), nullable=True)
    casualties = db.Column(db.Integer, nullable=True)
    evacuated = db.Column(db.Integer, nullable=True)
    gps_latitude = db.Column(db.Float, nullable=True)
    gps_longitude = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)

    reporter = db.relationship('User', backref='incident_messages', foreign_keys=[reporter_id])


class PostIncidentReport(db.Model):
    """Structured lessons learned and feedback after an incident response closes."""
    id = db.Column(db.Integer, primary_key=True)
    incident_response_id = db.Column(db.Integer, db.ForeignKey('incident_response.id', ondelete='CASCADE'), nullable=False, unique=True)
    lessons_learned = db.Column(db.Text, nullable=True)
    response_rating = db.Column(db.Integer, nullable=True)
    recommendations = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    incident_response = db.relationship('IncidentResponse', backref=db.backref('post_incident_report', cascade='all, delete-orphan'), uselist=False)


class Resource(db.Model):
    """Resource allocation tracking"""
    version_id = db.Column(db.Integer, nullable=False, default=1)
    __mapper_args__ = {'version_id_col': version_id}
    id = db.Column(db.Integer, primary_key=True)
    incident_response_id = db.Column(db.Integer, db.ForeignKey('incident_response.id', ondelete='CASCADE'), nullable=False)
    resource_type = db.Column(db.String(100), nullable=False)  # Personnel, Equipment, Vehicles, Supplies, etc.
    quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='AVAILABLE')  # AVAILABLE, DEPLOYED, RETURNING, UNAVAILABLE
    location = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    allocated_at = db.Column(db.DateTime, default=utcnow, index=True)
    deployed_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

class Facility(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    facility_type = db.Column(db.String(100), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    province_id = db.Column(db.Integer, db.ForeignKey('province.id'), nullable=True)
    municipality_id = db.Column(db.Integer, db.ForeignKey('municipality.id'), nullable=True)
    barangay_id = db.Column(db.Integer, db.ForeignKey('barangay.id'), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    province = db.relationship('Province')
    municipality = db.relationship('Municipality')
    barangay = db.relationship('Barangay')


class EvacuationCenter(db.Model):
    version_id = db.Column(db.Integer, nullable=False, default=1)
    __mapper_args__ = {'version_id_col': version_id}
    __table_args__ = (
        CheckConstraint('capacity IS NULL OR capacity >= 0', name='ck_evacuation_center_capacity_nonnegative'),
        CheckConstraint('occupancy IS NULL OR occupancy >= 0', name='ck_evacuation_center_occupancy_nonnegative'),
        CheckConstraint(
            'capacity IS NULL OR occupancy IS NULL OR occupancy <= capacity',
            name='ck_evacuation_center_occupancy_within_capacity',
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    facility_id = db.Column(db.Integer, db.ForeignKey('facility.id'), nullable=False)
    capacity = db.Column(db.Integer, nullable=True)
    occupancy = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), default='OPEN')
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    facility = db.relationship('Facility', backref=db.backref('evacuation_center', uselist=False))


class EvacuationRecord(db.Model):
    """Tracks people moved into an evacuation center as part of an active incident response.

    This is what actually links EvacuationCenter.occupancy to a response's
    lifecycle: occupancy is updated exclusively through records created here,
    so the running total on the center and the audit trail for a response
    always agree.
    """
    __table_args__ = (
        CheckConstraint('people_count > 0', name='ck_evacuation_record_people_positive'),
    )
    id = db.Column(db.Integer, primary_key=True)
    incident_response_id = db.Column(db.Integer, db.ForeignKey('incident_response.id', ondelete='CASCADE'), nullable=False, index=True)
    evacuation_center_id = db.Column(db.Integer, db.ForeignKey('evacuation_center.id', ondelete='CASCADE'), nullable=False, index=True)
    people_count = db.Column(db.Integer, nullable=False)
    recorded_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    recorded_at = db.Column(db.DateTime, default=utcnow, index=True)

    incident_response = db.relationship('IncidentResponse', backref=db.backref('evacuation_records', cascade='all, delete-orphan'))
    evacuation_center = db.relationship('EvacuationCenter', backref='evacuation_records')
    recorded_by = db.relationship('User')


class AIRecommendation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    incident_id = db.Column(db.Integer, db.ForeignKey('incident.id', ondelete='CASCADE'), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    provider = db.Column(db.String(100), nullable=True)
    model = db.Column(db.String(100), nullable=True)
    recommendation_type = db.Column(db.String(100), nullable=False)
    summary = db.Column(db.Text, nullable=False)
    confidence_score = db.Column(db.Float, nullable=True)
    recommended_resources = db.Column(db.Text, nullable=True)
    primary_factors = db.Column(db.Text, nullable=True)
    decision = db.Column(db.String(20), nullable=True)
    decision_reason = db.Column(db.Text, nullable=True)
    decided_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    decided_by = db.relationship('User', foreign_keys=[decided_by_id])


class AuditEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    entity_type = db.Column(db.String(100), nullable=False)
    entity_id = db.Column(db.Integer, nullable=True)
    action = db.Column(db.String(100), nullable=False)
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)


class Alert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    incident_id = db.Column(db.Integer, db.ForeignKey('incident.id', ondelete='CASCADE'), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    audience = db.Column(db.String(20), nullable=False, default='PUBLIC')
    external_event_id = db.Column(db.String(255), nullable=True, unique=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    severity = db.Column(db.String(20), default='MEDIUM')
    status = db.Column(db.String(20), default='ACTIVE')
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    incident_id = db.Column(db.Integer, db.ForeignKey('incident.id', ondelete='CASCADE'), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    report_type = db.Column(db.String(50), default='GENERAL')
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)