from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    full_name = db.Column(db.String(150), nullable=True)
    contact_number = db.Column(db.String(20), nullable=True)
    agency = db.Column(db.String(150), nullable=True)
    email_verified = db.Column(db.Boolean, default=False)
    verification_token = db.Column(db.String(500), nullable=True)
    role = db.Column(db.String(20), default='user')
    is_disabled = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    incidents = db.relationship('Incident', backref='user', lazy=True)

    @property
    def password_hash(self):
        return self.password

class Incident(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    hazard_type = db.Column(db.String(50), nullable=False)
    location = db.Column(db.String(255), nullable=True)
    rainfall_mm = db.Column(db.Float, nullable=False)
    river_level_m = db.Column(db.Float, nullable=False)
    soil_moisture_pct = db.Column(db.Float, nullable=False)
    population_density = db.Column(db.Float, nullable=False)
    score = db.Column(db.Float, nullable=False)
    level = db.Column(db.String(20), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    alert = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    response = db.relationship('IncidentResponse', backref='incident', lazy=True, uselist=False)


class IncidentResponse(db.Model):
    """Active incident response coordination"""
    id = db.Column(db.Integer, primary_key=True)
    incident_id = db.Column(db.Integer, db.ForeignKey('incident.id'), nullable=False)
    commander_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='ACTIVE')  # ACTIVE, MONITORING, RESOLVED, CLOSED
    situation_summary = db.Column(db.Text, nullable=True)
    priority_level = db.Column(db.String(20), default='MEDIUM')  # LOW, MEDIUM, HIGH, CRITICAL
    affected_population = db.Column(db.Integer, nullable=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)
    closed_at = db.Column(db.DateTime, nullable=True)
    
    commander = db.relationship('User', backref='incident_responses')
    tasks = db.relationship('Task', backref='incident_response', lazy=True, cascade='all, delete-orphan')
    resources = db.relationship('Resource', backref='incident_response', lazy=True, cascade='all, delete-orphan')
    reports = db.relationship('SituationReport', backref='incident_response', lazy=True, cascade='all, delete-orphan')


class Task(db.Model):
    """Incident response tasks assigned to agencies"""
    id = db.Column(db.Integer, primary_key=True)
    incident_response_id = db.Column(db.Integer, db.ForeignKey('incident_response.id'), nullable=False)
    assigned_to_agency = db.Column(db.String(150), nullable=False)
    assigned_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='PENDING')  # PENDING, IN_PROGRESS, COMPLETED, FAILED
    priority = db.Column(db.String(20), default='MEDIUM')  # LOW, MEDIUM, HIGH, CRITICAL
    estimated_completion = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    assigned_by = db.relationship('User', backref='assigned_tasks', foreign_keys=[assigned_by_id])


class Resource(db.Model):
    """Resource allocation tracking"""
    id = db.Column(db.Integer, primary_key=True)
    incident_response_id = db.Column(db.Integer, db.ForeignKey('incident_response.id'), nullable=False)
    resource_type = db.Column(db.String(100), nullable=False)  # Personnel, Equipment, Vehicles, Supplies, etc.
    agency = db.Column(db.String(150), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='AVAILABLE')  # AVAILABLE, DEPLOYED, RETURNING, UNAVAILABLE
    location = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    allocated_at = db.Column(db.DateTime, default=datetime.utcnow)
    deployed_at = db.Column(db.DateTime, nullable=True)


class SituationReport(db.Model):
    """Situation updates and incident timeline"""
    id = db.Column(db.Integer, primary_key=True)
    incident_response_id = db.Column(db.Integer, db.ForeignKey('incident_response.id'), nullable=False)
    reporter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    report_type = db.Column(db.String(50), default='UPDATE')  # UPDATE, ALERT, MILESTONE, CLOSURE
    affected_areas = db.Column(db.String(500), nullable=True)
    casualties = db.Column(db.Integer, nullable=True)
    evacuated = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    reporter = db.relationship('User', backref='situation_reports', foreign_keys=[reporter_id])
