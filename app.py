import os
import sqlite3
from flask import Flask, current_app, render_template, request, redirect, url_for, session, flash, send_from_directory
from flask_wtf.csrf import CSRFProtect, generate_csrf
from werkzeug.security import generate_password_hash, check_password_hash
from flask_apscheduler import APScheduler

from models import db, User, Incident, IncidentResponse, Task, Resource, SituationReport, CitizenReport
from scheduler import monitor_hazards
from services.realtime_data import get_weather_data, get_earthquake_data
from ai.prediction import predict_hazard

from blueprints.admin import admin_bp
from blueprints.commander import (
    commander_bp,
    incident_commander_dashboard,
    incident_response_tasks,
    incident_response_resources,
    incident_response_reports,
    incident_response_timeline,
    incident_response_close_page,
    assign_task,
    allocate_resource,
    create_situation_report,
    update_task,
    update_resource,
)
from blueprints.coordinator import (
    coordinator_bp,
    coordinator_dashboard,
    coordinator_tasks,
    coordinator_team,
    coordinator_resources,
    coordinator_reports,
    coordinator_comms,
    coordinator_submit_report,
    coordinator_update_task,
    coordinator_update_resource,
    coordinator_allocate_resource,
    coordinator_quick_report,
    coordinator_response_detail,
)
from blueprints.responder import (
    responder_bp,
    responder_dashboard,
    responder_tasks,
    responder_checklist,
    responder_report,
    responder_update_task,
    responder_complete_task,
)
from blueprints.eoc import eoc_bp, eoc_dashboard, eoc_incident_monitoring, eoc_resource_monitoring
from blueprints.citizen import citizen_bp
from blueprints.ai import ai_bp


app = Flask(__name__)
base_dir = os.path.abspath(os.path.dirname(__file__))
instance_dir = os.path.join(base_dir, 'instance')
os.makedirs(instance_dir, exist_ok=True)
upload_dir = os.path.join(instance_dir, 'uploads', 'citizen_reports')
os.makedirs(upload_dir, exist_ok=True)
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(instance_dir, 'database.db').replace('\\', '/')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'replace-this-with-a-secret')
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['UPLOAD_FOLDER'] = upload_dir
app.config['INSTANCE_DIR'] = instance_dir
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['WTF_CSRF_ENABLED'] = True
app.config['SCHEDULER_API_ENABLED'] = True
app.config['SCHEDULER_TIMEZONE'] = 'UTC'

csrf = CSRFProtect(app)
db.init_app(app)

scheduler = APScheduler()
scheduler.init_app(app)
_scheduler_started = False

def start_scheduler():
    global _scheduler_started
    if _scheduler_started or app.config.get('TESTING'):
        return
    scheduler.add_job(id='monitor_hazards', func=monitor_hazards, trigger='interval', minutes=5)
    scheduler.start()
    _scheduler_started = True

@app.context_processor
def inject_csrf_token():
    return {'csrf_token': generate_csrf}

app.register_blueprint(admin_bp)
app.register_blueprint(commander_bp)
app.register_blueprint(coordinator_bp)
app.register_blueprint(responder_bp)
app.register_blueprint(eoc_bp)
app.register_blueprint(citizen_bp)
app.register_blueprint(ai_bp)

# Backward-compatible aliases for legacy endpoint names used by older templates.
app.add_url_rule('/incident-commander-dashboard', endpoint='incident_commander_dashboard', view_func=incident_commander_dashboard)
app.add_url_rule('/incident-response/<int:response_id>/tasks', endpoint='incident_response_tasks', view_func=incident_response_tasks)
app.add_url_rule('/incident-response/<int:response_id>/resources', endpoint='incident_response_resources', view_func=incident_response_resources)
app.add_url_rule('/incident-response/<int:response_id>/reports', endpoint='incident_response_reports', view_func=incident_response_reports)
app.add_url_rule('/incident-response/<int:response_id>/timeline', endpoint='incident_response_timeline', view_func=incident_response_timeline)
app.add_url_rule('/incident-response/<int:response_id>/close', endpoint='incident_response_close_page', view_func=incident_response_close_page)
app.add_url_rule('/incident-response/<int:response_id>/assign-task', endpoint='assign_task', view_func=assign_task)
app.add_url_rule('/incident-response/<int:response_id>/allocate-resource', endpoint='allocate_resource', view_func=allocate_resource)
app.add_url_rule('/incident-response/<int:response_id>/create-report', endpoint='create_situation_report', view_func=create_situation_report)
app.add_url_rule('/incident-response/<int:response_id>/update-task/<int:task_id>', endpoint='update_task', view_func=update_task)
app.add_url_rule('/incident-response/<int:response_id>/update-resource/<int:resource_id>', endpoint='update_resource', view_func=update_resource)
app.add_url_rule('/coordinator', endpoint='coordinator_dashboard', view_func=coordinator_dashboard)
app.add_url_rule('/coordinator/tasks', endpoint='coordinator_tasks', view_func=coordinator_tasks)
app.add_url_rule('/coordinator/team', endpoint='coordinator_team', view_func=coordinator_team)
app.add_url_rule('/coordinator/resources', endpoint='coordinator_resources', view_func=coordinator_resources)
app.add_url_rule('/coordinator/reports', endpoint='coordinator_reports', view_func=coordinator_reports)
app.add_url_rule('/coordinator/comms', endpoint='coordinator_comms', view_func=coordinator_comms)
app.add_url_rule('/coordinator/reports/submit', endpoint='coordinator_submit_report', view_func=coordinator_submit_report, methods=['POST'])
app.add_url_rule('/coordinator/tasks/<int:task_id>/update', endpoint='coordinator_update_task', view_func=coordinator_update_task, methods=['POST'])
app.add_url_rule('/coordinator/resources/<int:resource_id>/update', endpoint='coordinator_update_resource', view_func=coordinator_update_resource, methods=['POST'])
app.add_url_rule('/coordinator/resources/allocate', endpoint='coordinator_allocate_resource', view_func=coordinator_allocate_resource, methods=['POST'])
app.add_url_rule('/coordinator/quick-report', endpoint='coordinator_quick_report', view_func=coordinator_quick_report, methods=['POST'])
app.add_url_rule('/coordinator/response/<int:response_id>', endpoint='coordinator_response_detail', view_func=coordinator_response_detail)
app.add_url_rule('/responder-dashboard', endpoint='responder_dashboard', view_func=responder_dashboard)
app.add_url_rule('/responder-tasks', endpoint='responder_tasks', view_func=responder_tasks)
app.add_url_rule('/responder-checklist', endpoint='responder_checklist', view_func=responder_checklist)
app.add_url_rule('/responder-report', endpoint='responder_report', view_func=responder_report, methods=['GET', 'POST'])
app.add_url_rule('/responder-task/<int:task_id>/update', endpoint='responder_update_task', view_func=responder_update_task, methods=['POST'])
app.add_url_rule('/responder-task/<int:task_id>/complete', endpoint='responder_complete_task', view_func=responder_complete_task, methods=['POST'])
app.add_url_rule('/eoc-dashboard', endpoint='eoc_dashboard', view_func=eoc_dashboard)
app.add_url_rule('/eoc/incidents', endpoint='eoc_incident_monitoring', view_func=eoc_incident_monitoring)
app.add_url_rule('/eoc/resources', endpoint='eoc_resource_monitoring', view_func=eoc_resource_monitoring)


@app.context_processor
def inject_alert_count():
    alert_count = 0
    if session.get('username') and session.get('role') == 'user':
        try:
            user = User.query.filter_by(username=session['username']).first()
            if user:
                alert_count = Incident.query.filter_by(user_id=user.id, alert=True).count()
        except Exception:
            alert_count = 0
    return {'alert_count': alert_count}


def migrate_user_table():
    db_path = os.path.join(instance_dir, 'database.db')
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(user)")
            columns = [row[1] for row in cursor.fetchall()]
            if 'created_at' not in columns:
                cursor.execute("ALTER TABLE user ADD COLUMN created_at DATETIME")
            if 'role' not in columns:
                cursor.execute("ALTER TABLE user ADD COLUMN role VARCHAR(20) DEFAULT 'user'")
            if 'full_name' not in columns:
                cursor.execute("ALTER TABLE user ADD COLUMN full_name VARCHAR(150)")
            if 'contact_number' not in columns:
                cursor.execute("ALTER TABLE user ADD COLUMN contact_number VARCHAR(20)")
            if 'email' not in columns:
                cursor.execute("ALTER TABLE user ADD COLUMN email VARCHAR(150)")
            if 'is_disabled' not in columns:
                cursor.execute("ALTER TABLE user ADD COLUMN is_disabled BOOLEAN DEFAULT 0")
            conn.commit()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='incident'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(incident)")
            columns = [row[1] for row in cursor.fetchall()]
            if 'location' not in columns:
                cursor.execute("ALTER TABLE incident ADD COLUMN location VARCHAR(255)")
            conn.commit()


def migrate_incident_commander_tables():
    db_path = os.path.join(instance_dir, 'database.db')
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS incident_response (
                id INTEGER PRIMARY KEY,
                incident_id INTEGER NOT NULL UNIQUE,
                commander_id INTEGER NOT NULL,
                status VARCHAR(20) DEFAULT 'ACTIVE',
                situation_summary TEXT,
                priority_level VARCHAR(20) DEFAULT 'MEDIUM',
                affected_population INTEGER,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                resolved_at DATETIME,
                closed_at DATETIME,
                FOREIGN KEY (incident_id) REFERENCES incident(id),
                FOREIGN KEY (commander_id) REFERENCES user(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task (
                id INTEGER PRIMARY KEY,
                incident_response_id INTEGER NOT NULL,
                assigned_to_agency VARCHAR(150) NOT NULL,
                assigned_by_id INTEGER NOT NULL,
                title VARCHAR(200) NOT NULL,
                description TEXT NOT NULL,
                status VARCHAR(20) DEFAULT 'PENDING',
                priority VARCHAR(20) DEFAULT 'MEDIUM',
                estimated_completion DATETIME,
                completed_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (incident_response_id) REFERENCES incident_response(id),
                FOREIGN KEY (assigned_by_id) REFERENCES user(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS resource (
                id INTEGER PRIMARY KEY,
                incident_response_id INTEGER NOT NULL,
                resource_type VARCHAR(100) NOT NULL,
                agency VARCHAR(150) NOT NULL,
                quantity INTEGER NOT NULL,
                status VARCHAR(20) DEFAULT 'AVAILABLE',
                location VARCHAR(255),
                notes TEXT,
                allocated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                deployed_at DATETIME,
                FOREIGN KEY (incident_response_id) REFERENCES incident_response(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS situation_report (
                id INTEGER PRIMARY KEY,
                incident_response_id INTEGER NOT NULL,
                reporter_id INTEGER NOT NULL,
                title VARCHAR(200) NOT NULL,
                content TEXT NOT NULL,
                report_type VARCHAR(50) DEFAULT 'UPDATE',
                affected_areas VARCHAR(500),
                casualties INTEGER,
                evacuated INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (incident_response_id) REFERENCES incident_response(id),
                FOREIGN KEY (reporter_id) REFERENCES user(id)
            )
        """)
        conn.commit()


def create_default_admin():
    admin = User.query.filter_by(role='admin').first()
    if admin is None:
        admin = User(
            username='admin',
            email='admin@dics-ai.local',
            password=generate_password_hash('Admin123!'),
            email_verified=True,
            role='admin',
        )
        db.session.add(admin)
        db.session.commit()


def create_tables():
    with app.app_context():
        db.create_all()
        migrate_user_table()
        migrate_incident_commander_tables()
        create_default_admin()


_init_attempted = False


def lazy_init():
    global _init_attempted
    if _init_attempted:
        return
    _init_attempted = True
    try:
        with app.app_context():
            db.create_all()
            migrate_user_table()
            create_default_admin()
            app.logger.info('Database initialized successfully')
    except Exception as e:
        app.logger.error(f'Database initialization error: {e}')


@app.before_request
def init_on_first_request():
    lazy_init()
    start_scheduler()


def verify_password(user, password):
    if user is None:
        return False

    stored = user.password
    try:
        if check_password_hash(stored, password):
            return True
    except (ValueError, TypeError):
        pass

    if stored == password:
        user.password = generate_password_hash(password)
        db.session.commit()
        return True

    return False


@app.route('/', methods=['GET', 'POST'])
def login():
    if 'username' in session:
        role = session.get('role')
        if role == 'incident_commander':
            return redirect(url_for('incident_commander_dashboard'))
        elif role == 'agency_coordinator':
            return redirect(url_for('coordinator_dashboard'))
        elif role == 'field_responder':
            return redirect(url_for('responder_dashboard'))
        elif role == 'eoc_staff':
            return redirect(url_for('eoc_dashboard'))
        else:
            return redirect(url_for('dashboard'))

    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        user = User.query.filter_by(username=username).first()
        if user and user.is_disabled:
            error = 'This account has been disabled. Contact an administrator.'
        elif user and verify_password(user, password):
            session['username'] = user.username
            session['role'] = user.role
            session['agency'] = user.agency or 'FIELD UNIT'
            flash('Welcome back, ' + user.username + '!', 'success')
            if user.role == 'incident_commander':
                return redirect(url_for('incident_commander_dashboard'))
            elif user.role == 'agency_coordinator':
                return redirect(url_for('coordinator_dashboard'))
            elif user.role == 'field_responder':
                return redirect(url_for('responder_dashboard'))
            elif user.role == 'eoc_staff':
                return redirect(url_for('eoc_dashboard'))
            else:
                return redirect(url_for('dashboard'))
        else:
            error = 'Invalid username or password.'
    return render_template('pages/login.html', error=error)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'username' in session:
        return redirect(url_for('dashboard'))

    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        full_name = request.form.get('full_name', '').strip()
        contact_number = request.form.get('contact_number', '').strip()
        if not username or not password or not full_name or not contact_number or not email:
            error = 'All fields are required.'
        elif User.query.filter_by(username=username).first():
            error = 'Username already exists.'
        elif User.query.filter_by(email=email).first():
            error = 'Email already registered.'
        else:
            new_user = User(
                username=username,
                email=email,
                password=generate_password_hash(password),
                full_name=full_name,
                contact_number=contact_number,
                role='user',
            )
            db.session.add(new_user)
            db.session.commit()
            flash('Registration successful! You can now log in.', 'success')
            return redirect(url_for('login'))
    return render_template('pages/register.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))

    if user.role == 'incident_commander':
        return redirect(url_for('incident_commander_dashboard'))
    elif user.role == 'field_responder':
        return redirect(url_for('responder_dashboard'))
    elif user.role == 'eoc_staff':
        return redirect(url_for('eoc_dashboard'))
    elif user.role in ['admin', 'agency_coordinator']:
        return redirect(url_for('admin.admin'))

    incidents = Incident.query.filter_by(user_id=user.id).order_by(Incident.created_at.desc()).limit(5).all()
    total_incidents = Incident.query.filter_by(user_id=user.id).count()
    alert_count = Incident.query.filter_by(user_id=user.id, alert=True).count()
    latest_incident = Incident.query.filter_by(user_id=user.id).order_by(Incident.created_at.desc()).first()
    latest_risk_score = latest_incident.score if latest_incident else 0

    earthquake_data = get_earthquake_data()
    latest_earthquake_magnitude = 0
    if earthquake_data and len(earthquake_data) > 0:
        latest_earthquake_magnitude = earthquake_data[0].get('magnitude', 0)

    return render_template(
        'pages/dashboard.html',
        username=user.username,
        user_role=user.role,
        incidents=incidents,
        total_incidents=total_incidents,
        alert_count=alert_count,
        latest_risk_score=latest_risk_score,
        latest_earthquake_magnitude=latest_earthquake_magnitude,
        weather_data=None,
        earthquake_data=None,
    )


@app.route('/api/map-pins')
def get_map_pins():
    incidents = Incident.query.order_by(Incident.created_at.desc()).all()
    pins = []

    for incident in incidents:
        is_active = incident.alert or incident.status in {'ACTIVE', 'NEW', 'MONITORING', 'VERIFIED', 'PENDING'}
        if not is_active:
            continue

        report = None
        if incident.user_id is not None:
            report = CitizenReport.query.filter(
                CitizenReport.user_id == incident.user_id,
                CitizenReport.location == incident.location,
                CitizenReport.hazard_type == incident.hazard_type,
            ).order_by(CitizenReport.created_at.desc()).first()

        if report is None:
            report = CitizenReport.query.filter(
                CitizenReport.location == incident.location,
                CitizenReport.hazard_type == incident.hazard_type,
            ).order_by(CitizenReport.created_at.desc()).first()

        if report is None or report.gps_latitude is None or report.gps_longitude is None:
            continue

        level = 'High' if incident.alert else str(incident.level or 'Moderate')
        pins.append({
            'id': incident.id,
            'hazard_type': incident.hazard_type,
            'label': incident.location or incident.hazard_type,
            'location': incident.location,
            'message': incident.message,
            'level': level.capitalize(),
            'lat': report.gps_latitude,
            'lng': report.gps_longitude,
            'status': incident.status,
            'reported_by': incident.reported_by,
        })

    return pins


@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    upload_dir = current_app.config['UPLOAD_FOLDER']
    safe_path = os.path.join(upload_dir, filename)
    if not os.path.commonpath([os.path.abspath(upload_dir), os.path.abspath(safe_path)]) == os.path.abspath(upload_dir):
        return {'error': 'Invalid file path'}, 400
    if not os.path.exists(safe_path):
        return {'error': 'File not found'}, 404
    return send_from_directory(upload_dir, filename)


@app.route('/api/realtime-data')
def get_realtime_data():
    if 'username' not in session:
        return {'error': 'Unauthorized'}, 401
    weather_data = get_weather_data('Cavite')
    earthquake_data = get_earthquake_data()
    return {'weather': weather_data, 'earthquakes': earthquake_data}


@app.route('/api/dashboard-stats')
def get_dashboard_stats():
    if 'username' not in session:
        return {'error': 'Unauthorized'}, 401
    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return {'error': 'User not found'}, 404
    alert_count = Incident.query.filter_by(user_id=user.id, alert=True).count()
    total_incidents = Incident.query.filter_by(user_id=user.id).count()
    latest_incident = Incident.query.filter_by(user_id=user.id).order_by(Incident.created_at.desc()).first()
    latest_risk_score = latest_incident.score if latest_incident else 0
    earthquake_data = get_earthquake_data()
    latest_earthquake_magnitude = earthquake_data[0].get('magnitude', 0) if earthquake_data and len(earthquake_data) > 0 else 0
    return {
        'alert_count': alert_count,
        'total_incidents': total_incidents,
        'latest_risk_score': latest_risk_score,
        'latest_earthquake_magnitude': latest_earthquake_magnitude,
    }


@app.route('/api/analytics-data')
def get_analytics_data():
    if 'username' not in session:
        return {'error': 'Unauthorized'}, 401

    incident_rows = db.session.query(
        Incident.hazard_type,
        db.func.count(Incident.id)
    ).group_by(Incident.hazard_type).all()
    incident_counts = {row[0] or 'Unknown': row[1] for row in incident_rows}

    resolved_responses = db.session.query(IncidentResponse).filter(IncidentResponse.resolved_at.isnot(None)).all()
    response_durations = []
    for response in resolved_responses:
        if response.started_at and response.resolved_at:
            duration = (response.resolved_at - response.started_at).total_seconds() / 60.0
            if duration >= 0:
                response_durations.append(duration)

    avg_response_time = round(sum(response_durations) / len(response_durations), 1) if response_durations else 0
    response_buckets = {
        '< 30 min': 0,
        '30-60 min': 0,
        '60-120 min': 0,
        '> 120 min': 0,
    }
    for minutes in response_durations:
        if minutes < 30:
            response_buckets['< 30 min'] += 1
        elif minutes < 60:
            response_buckets['30-60 min'] += 1
        elif minutes < 120:
            response_buckets['60-120 min'] += 1
        else:
            response_buckets['> 120 min'] += 1

    resource_status_rows = db.session.query(
        Resource.status,
        db.func.sum(Resource.quantity)
    ).group_by(Resource.status).all()
    resources_by_status = {row[0]: int(row[1] or 0) for row in resource_status_rows}

    resource_type_rows = db.session.query(
        Resource.resource_type,
        db.func.sum(Resource.quantity)
    ).group_by(Resource.resource_type).all()
    resources_by_type = {row[0]: int(row[1] or 0) for row in resource_type_rows}

    return {
        'incident_counts': incident_counts,
        'response_time': {
            'average_minutes': avg_response_time,
            'buckets': response_buckets,
            'total_resolved': len(response_durations),
        },
        'resource_utilization': {
            'status_counts': resources_by_status,
            'type_counts': resources_by_type,
        },
    }


@app.route('/live-prediction')
def live_prediction():
    if not os.getenv('OPENWEATHER_API_KEY'):
        return {'error': 'OPENWEATHER_API_KEY is not configured.'}
    weather_data = get_weather_data('Cavite')
    if not weather_data:
        return {'error': 'Could not fetch weather data.'}
    rainfall = weather_data.get('rainfall', 0) or 0
    river_level = rainfall / 10.0
    soil_moisture = weather_data.get('humidity', 0) or 0
    population_density = 1200
    prediction = predict_hazard(
        hazard_type='flood',
        rainfall_mm=rainfall,
        river_level_m=river_level,
        soil_moisture_pct=soil_moisture,
        population_density=population_density,
    )
    return prediction


@app.route('/analytics')
def analytics():
    if 'username' not in session:
        return redirect(url_for('login'))
    total_incidents = db.session.query(Incident).count()
    avg_score = db.session.query(db.func.avg(Incident.score)).scalar() or 0
    active_responses = db.session.query(IncidentResponse).filter(IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])).count()
    active_alerts = db.session.query(Incident).filter(Incident.alert.is_(True)).count()
    hazard_rows = db.session.query(Incident.hazard_type, db.func.count(Incident.id)).group_by(Incident.hazard_type).order_by(db.func.count(Incident.id).desc()).all()
    hazard_labels = [row[0] for row in hazard_rows]
    hazard_counts = [row[1] for row in hazard_rows]
    return render_template('pages/analytics.html', total_incidents=total_incidents, avg_score=avg_score, active_responses=active_responses, active_alerts=active_alerts, hazard_labels=hazard_labels, hazard_counts=hazard_counts)


@app.route('/hazard-map')
def hazard_map():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('pages/hazard_map.html', sidebar_variant='hazard')


@app.route('/ics')
def ics_page():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('pages/ics.html')


@app.route('/protocols')
def protocols():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('pages/protocols.html')


if __name__ == '__main__':
    create_tables()
    app.run(debug=True, use_reloader=False, host='127.0.0.1', port=5000)
