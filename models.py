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
