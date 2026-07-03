import os
import sqlite3
import secrets
import shutil
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from models import db, User, Incident, IncidentResponse, Task, Resource, SituationReport
from ai.prediction import predict_hazard
from services.realtime_data import get_weather_data, get_earthquake_data

# Helper functions for authorization
def is_admin_or_coordinator():
    """Check if current user is admin or agency_coordinator"""
    return 'username' in session and session.get('role') in ['admin', 'agency_coordinator']

def is_incident_commander():
    """Check if current user is incident_commander"""
    return 'username' in session and session.get('role') == 'incident_commander'

def is_admin_coordinator_or_commander():
    """Check if current user is admin, agency_coordinator, or incident_commander"""
    return 'username' in session and session.get('role') in ['admin', 'agency_coordinator', 'incident_commander']

def is_field_responder():
    """Check if current user is a field responder"""
    return 'username' in session and session.get('role') == 'field_responder'

def is_eoc_staff():
    """Check if current user is EOC staff"""
    return 'username' in session and session.get('role') == 'eoc_staff'

app = Flask(__name__)
base_dir = os.path.abspath(os.path.dirname(__file__))
instance_dir = os.path.join(base_dir, 'instance')
os.makedirs(instance_dir, exist_ok=True)
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(instance_dir, 'database.db').replace('\\', '/') }"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'replace-this-with-a-secret'
app.config['TEMPLATES_AUTO_RELOAD'] = True

db.init_app(app)

@app.context_processor
def inject_alert_count():
    """Inject alert_count into every template so the sidebar badge
    shows consistently on all pages without per-route boilerplate."""
    alert_count = 0
    if session.get('username') and session.get('role') == 'user':
        try:
            user = User.query.filter_by(username=session['username']).first()
            if user:
                alert_count = Incident.query.filter_by(
                    user_id=user.id, alert=True
                ).count()
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
        
        # Migrate incident table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='incident'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(incident)")
            columns = [row[1] for row in cursor.fetchall()]
            if 'location' not in columns:
                cursor.execute("ALTER TABLE incident ADD COLUMN location VARCHAR(255)")
            conn.commit()


def migrate_incident_commander_tables():
    """Create incident commander related tables if they don't exist"""
    db_path = os.path.join(instance_dir, 'database.db')
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        
        # Create incident_response table
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
        
        # Create task table
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
        
        # Create resource table
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
        
        # Create situation_report table
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

# Flag to track if initialization has been attempted
_init_attempted = False

def lazy_init():
    """Initialize database tables once on first request"""
    global _init_attempted
    if _init_attempted:
        return
    
    _init_attempted = True
    try:
        with app.app_context():
            db.create_all()
            migrate_user_table()
            create_default_admin()
            app.logger.info("Database initialized successfully")
    except Exception as e:
        app.logger.error(f"Database initialization error: {e}")


# Initialize on first request
# TEMPORARILY DISABLED FOR DEBUGGING
# @app.before_request
# def init_on_first_request():
#    lazy_init()

def verify_password(user, password):
    if user is None:
        return False

    stored = user.password
    
    # Try checking as a hashed password first
    try:
        if check_password_hash(stored, password):
            return True
    except (ValueError, TypeError):
        # Not a valid hash format, continue to plain text check
        pass
    
    # Fallback for plain text passwords (for legacy data)
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
            flash(f'Registration successful! You can now log in.', 'success')
            return redirect(url_for('login'))
    return render_template('pages/register.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/admin')
def admin():
    if not is_admin_or_coordinator():
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard'))
    return redirect(url_for('admin_alerts'))

@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))
    
    # Redirect based on user role
    if user.role == 'incident_commander':
        return redirect(url_for('incident_commander_dashboard'))
    elif user.role == 'field_responder':
        return redirect(url_for('responder_dashboard'))
    elif user.role == 'eoc_staff':
        return redirect(url_for('eoc_dashboard'))
    elif user.role in ['admin', 'agency_coordinator']:
        return redirect(url_for('admin'))
        
    incidents = Incident.query.filter_by(user_id=user.id).order_by(Incident.created_at.desc()).limit(5).all()
    total_incidents = Incident.query.filter_by(user_id=user.id).count()
    alert_count = Incident.query.filter_by(user_id=user.id, alert=True).count()
    
    # Get latest risk score
    latest_incident = Incident.query.filter_by(user_id=user.id).order_by(Incident.created_at.desc()).first()
    latest_risk_score = latest_incident.score if latest_incident else 0
    
    # Get latest earthquake data
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

@app.route('/responder-dashboard')
def responder_dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user or user.role != 'field_responder':
        flash('Field responder access required.', 'danger')
        return redirect(url_for('dashboard'))

    session['agency'] = user.agency or 'FIELD UNIT'
    my_tasks = Task.query.filter_by(assigned_to_agency=user.agency or '').order_by(Task.created_at.desc()).all()
    my_reports = SituationReport.query.filter_by(reporter_id=user.id).order_by(SituationReport.created_at.desc()).limit(8).all()
    active_responses = IncidentResponse.query.filter_by(status='ACTIVE').order_by(IncidentResponse.started_at.desc()).all()

    pending_count = sum(1 for task in my_tasks if task.status in ['PENDING', 'IN_PROGRESS'])
    completed_count = sum(1 for task in my_tasks if task.status == 'COMPLETED')

    return render_template(
        'pages/field_responder_dashboard.html',
        user=user,
        my_tasks=my_tasks,
        my_reports=my_reports,
        active_responses=active_responses,
        pending_count=pending_count,
        completed_count=completed_count,
    )

@app.route('/responder-tasks')
def responder_tasks():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user or user.role != 'field_responder':
        flash('Field responder access required.', 'danger')
        return redirect(url_for('dashboard'))

    status_filter = request.args.get('status', '').upper()
    query = Task.query.filter_by(assigned_to_agency=user.agency or '')
    if status_filter:
        query = query.filter(Task.status == status_filter)

    tasks = query.order_by(Task.created_at.desc()).all()
    return render_template(
        'pages/field_responder_tasks.html',
        tasks=tasks,
        status_filter=status_filter,
        user=user,
    )

@app.route('/responder-checklist')
def responder_checklist():
    return redirect(url_for('responder_tasks'))

@app.route('/responder-report', methods=['GET', 'POST'])
def responder_report():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user or user.role != 'field_responder':
        flash('Field responder access required.', 'danger')
        return redirect(url_for('dashboard'))

    active_responses = IncidentResponse.query.filter_by(status='ACTIVE').order_by(IncidentResponse.started_at.desc()).all()

    if request.method == 'POST':
        incident_response_id = request.form.get('incident_response_id', type=int)
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        report_type = request.form.get('report_type', 'UPDATE').strip().upper()
        affected_areas = request.form.get('affected_areas', '').strip()
        casualties = request.form.get('casualties', 0, type=int)
        evacuated = request.form.get('evacuated', 0, type=int)
        gps_lat = request.form.get('gps_lat', '').strip()
        gps_lng = request.form.get('gps_lng', '').strip()

        if not incident_response_id or not title or not content:
            flash('Please complete the required fields before submitting your report.', 'danger')
            return redirect(url_for('responder_report'))

        if gps_lat and gps_lng:
            content = f"{content}\nGPS: {gps_lat}, {gps_lng}"

        report = SituationReport(
            incident_response_id=incident_response_id,
            reporter_id=user.id,
            title=title,
            content=content,
            report_type=report_type,
            affected_areas=affected_areas or None,
            casualties=casualties,
            evacuated=evacuated,
        )
        db.session.add(report)
        db.session.commit()

        upload_dir = os.path.join(instance_dir, 'uploads')
        os.makedirs(upload_dir, exist_ok=True)
        uploaded_files = request.files.getlist('media')
        for media_file in uploaded_files:
            if media_file and media_file.filename:
                filename = secure_filename(media_file.filename)
                timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
                saved_name = f"{user.id}_{timestamp}_{filename}"
                media_file.save(os.path.join(upload_dir, saved_name))

        flash('Field report submitted successfully.', 'success')
        return redirect(url_for('responder_dashboard'))

    return render_template('pages/field_responder_report.html', active_responses=active_responses)

@app.route('/responder-task/<int:task_id>/update', methods=['POST'])
def responder_update_task(task_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user or user.role != 'field_responder':
        flash('Field responder access required.', 'danger')
        return redirect(url_for('dashboard'))

    task = Task.query.get_or_404(task_id)
    if task.assigned_to_agency != (user.agency or ''):
        flash('You can only update tasks assigned to your unit.', 'danger')
        return redirect(url_for('responder_tasks'))

    new_status = request.form.get('status', '').upper()
    if new_status in {'PENDING', 'IN_PROGRESS', 'COMPLETED', 'FAILED'}:
        task.status = new_status
        if new_status == 'COMPLETED':
            task.completed_at = datetime.utcnow()
        else:
            task.completed_at = None
        db.session.commit()
        flash('Task status updated.', 'success')
    else:
        flash('A valid task status was not provided.', 'danger')

    return redirect(url_for('responder_tasks'))

@app.route('/responder-task/<int:task_id>/complete', methods=['POST'])
def responder_complete_task(task_id):
    return responder_update_task(task_id)

@app.route('/api/realtime-data')
def get_realtime_data():
    """API endpoint to fetch weather and earthquake data asynchronously"""
    if 'username' not in session:
        return {'error': 'Unauthorized'}, 401
    
    weather_data = get_weather_data("Cavite")
    earthquake_data = get_earthquake_data()
    
    return {
        'weather': weather_data,
        'earthquakes': earthquake_data,
    }

@app.route('/api/dashboard-stats')
def get_dashboard_stats():
    """API endpoint to fetch dashboard statistics"""
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
        'latest_earthquake_magnitude': latest_earthquake_magnitude
    }


@app.route('/live-prediction')
def live_prediction():
    # Generate a quick live flood prediction using current weather
    if not os.getenv('OPENWEATHER_API_KEY'):
        return {
            "error": "OPENWEATHER_API_KEY is not configured. Set the environment variable or add it to a .env file in the project root, then restart the app."
        }

    weather_data = get_weather_data("Cavite")
    if not weather_data:
        return {"error": "Could not fetch weather data. Verify the OpenWeatherMap API key is valid and the service is reachable."}

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

    active_responses = db.session.query(IncidentResponse).filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).count()

    active_alerts = db.session.query(Incident).filter(Incident.alert.is_(True)).count()

    # Real hazard type breakdown, same aggregation used on the EOC dashboard
    hazard_rows = db.session.query(
        Incident.hazard_type, db.func.count(Incident.id)
    ).group_by(Incident.hazard_type).order_by(db.func.count(Incident.id).desc()).all()
    hazard_labels = [row[0] for row in hazard_rows]
    hazard_counts = [row[1] for row in hazard_rows]

    return render_template('pages/analytics.html',
                         total_incidents=total_incidents,
                         avg_score=avg_score,
                         active_responses=active_responses,
                         active_alerts=active_alerts,
                         hazard_labels=hazard_labels,
                         hazard_counts=hazard_counts)

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

@app.route('/citizen-report', methods=['GET', 'POST'])
def citizen_report():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))
    
    if request.method == 'POST':
        hazard_type = request.form.get('hazard_type', '').strip()
        severity = request.form.get('severity', '').strip()
        location = request.form.get('location', '').strip()
        description = request.form.get('description', '').strip()
        affected_people = request.form.get('affected_people', 0)
        injuries = request.form.get('injuries', 0)
        contact = request.form.get('contact', '').strip()
        
        try:
            incident = Incident(
                user_id=user.id,
                hazard_type=hazard_type,
                location=location,
                message=description,
                level=severity,
                alert=False,
                rainfall_mm=0,
                river_level_m=0,
                soil_moisture_pct=0,
                population_density=0,
                score=0,
            )
            db.session.add(incident)
            db.session.commit()
            flash('Incident report submitted successfully. Authorities have been notified.', 'success')
            return redirect(url_for('citizen_status'))
        except Exception as e:
            flash(f'Error submitting report: {str(e)}', 'error')
    
    incidents = Incident.query.filter_by(user_id=user.id).order_by(Incident.created_at.desc()).all()
    total_incidents = len(incidents)
    pending_count = sum(1 for i in incidents if not i.alert)
    
    return render_template('pages/citizen_report.html', 
                         total_incidents=total_incidents, 
                         pending_count=pending_count)

@app.route('/citizen-alerts')
def citizen_alerts():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))
    
    alerts = Incident.query.filter_by(user_id=user.id, alert=True).order_by(Incident.created_at.desc()).all()
    alert_count = len(alerts)
    
    return render_template('pages/citizen_alerts.html', 
                         alerts=alerts, 
                         alert_count=alert_count)

@app.route('/citizen-status')
def citizen_status():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))
    
    incidents = Incident.query.filter_by(user_id=user.id).order_by(Incident.created_at.desc()).all()
    total_incidents = len(incidents)
    pending_count = sum(1 for i in incidents if not i.alert)
    
    return render_template('pages/citizen_status.html', 
                         incidents=incidents, 
                         total_incidents=total_incidents, 
                         pending_count=pending_count)

@app.route('/citizen-resources')
def citizen_resources():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('pages/citizen_resources.html')

@app.route('/incidents')
def incident_history():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))

    incidents = Incident.query.filter_by(user_id=user.id).order_by(Incident.created_at.desc()).all()
    return render_template('pages/incidents.html', incidents=incidents)

@app.route('/alerts')
def alerts():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))

    alerts = Incident.query.filter_by(user_id=user.id, alert=True).order_by(Incident.created_at.desc()).all()
    return render_template('pages/alerts.html', alerts=alerts)

@app.route('/admin/alerts')
def admin_alerts():
    if not is_admin_or_coordinator():
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard'))

    incidents = Incident.query.order_by(Incident.created_at.desc()).all()
    return render_template('pages/admin_alerts.html', incidents=incidents)

@app.route('/admin/alerts/<int:incident_id>/toggle', methods=['POST'])
def toggle_alert(incident_id):
    if not is_admin_or_coordinator():
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard'))

    incident = Incident.query.get_or_404(incident_id)
    incident.alert = not incident.alert
    db.session.commit()
    flash('Alert status updated.', 'success')
    return redirect(url_for('admin_alerts'))

@app.route('/ai-prediction', methods=['GET', 'POST'])
def ai_prediction():
    if 'username' not in session:
        return redirect(url_for('login'))

    prediction = None
    if request.method == 'POST':
        hazard_type = request.form.get('hazard_type')
        rainfall = float(request.form.get('rainfall') or 0)
        river_level = float(request.form.get('river_level') or 0)
        soil_moisture = float(request.form.get('soil_moisture') or 0)
        population_density = float(request.form.get('population_density') or 0)

        prediction = predict_hazard(
            hazard_type=hazard_type,
            rainfall_mm=rainfall,
            river_level_m=river_level,
            soil_moisture_pct=soil_moisture,
            population_density=population_density,
        )

        user = User.query.filter_by(username=session['username']).first()
        if user:
            incident = Incident(
                user_id=user.id,
                hazard_type=hazard_type,
                rainfall_mm=rainfall,
                river_level_m=river_level,
                soil_moisture_pct=soil_moisture,
                population_density=population_density,
                score=prediction['score'],
                level=prediction['level'],
                message=prediction['message'],
                alert=prediction['alert'],
            )
            db.session.add(incident)
            db.session.commit()

    # Gather statistics
    total_active_alerts = Incident.query.filter_by(alert=True).count()
    total_incidents = Incident.query.count()
    latest_incident = Incident.query.order_by(Incident.created_at.desc()).first()
    latest_risk_score = latest_incident.score if latest_incident else 0
    
    # Get latest earthquake data
    earthquake_data = get_earthquake_data()
    latest_earthquake_magnitude = 0
    if earthquake_data and len(earthquake_data) > 0:
        latest_earthquake_magnitude = earthquake_data[0].get('magnitude', 0)

    return render_template('pages/ai_prediction.html', 
                         prediction=prediction,
                         total_active_alerts=total_active_alerts,
                         total_incidents=total_incidents,
                         latest_risk_score=latest_risk_score,
                         latest_earthquake_magnitude=latest_earthquake_magnitude)

@app.route('/admin/users')
def manage_users():
    """User management page for admins and coordinators"""
    if not is_admin_or_coordinator():
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard'))
    
    users = User.query.order_by(User.created_at.desc()).all()
    roles = ['user', 'agency_coordinator', 'admin']
    return render_template('pages/user_management.html', users=users, roles=roles)

@app.route('/admin/users/add', methods=['POST'])
def add_user():
    """Add a new user"""
    if not is_admin_or_coordinator():
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard'))
    
    try:
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        full_name = request.form.get('full_name', '').strip()
        contact_number = request.form.get('contact_number', '').strip()
        agency = request.form.get('agency', '').strip()
        role = request.form.get('role', 'user')
        
        if not username or not password or not email:
            flash('Username, email, and password are required.', 'error')
            return redirect(url_for('manage_users'))
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'error')
            return redirect(url_for('manage_users'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
            return redirect(url_for('manage_users'))
        
        new_user = User(
            username=username,
            email=email,
            password=generate_password_hash(password),
            full_name=full_name,
            contact_number=contact_number,
            agency=agency,
            role=role,
            email_verified=True,
        )
        db.session.add(new_user)
        db.session.commit()
        flash(f'User "{username}" created successfully.', 'success')
    except Exception as e:
        flash(f'Error creating user: {str(e)}', 'error')
    
    return redirect(url_for('manage_users'))

@app.route('/admin/users/<int:user_id>/update', methods=['POST'])
def update_user(user_id):
    """Update user details and role"""
    if not is_admin_or_coordinator():
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard'))
    
    user = User.query.get_or_404(user_id)
    
    try:
        # Prevent self-demotion if last admin
        if user.role == 'admin' and request.form.get('role') != 'admin':
            admin_count = User.query.filter_by(role='admin').count()
            if admin_count <= 1:
                flash('Cannot remove last admin account.', 'error')
                return redirect(url_for('manage_users'))
        
        user.full_name = request.form.get('full_name', user.full_name).strip()
        user.contact_number = request.form.get('contact_number', user.contact_number).strip()
        user.agency = request.form.get('agency', user.agency).strip()
        user.role = request.form.get('role', user.role)
        
        db.session.commit()
        flash(f'User "{user.username}" updated successfully.', 'success')
    except Exception as e:
        flash(f'Error updating user: {str(e)}', 'error')
    
    return redirect(url_for('manage_users'))

@app.route('/admin/users/<int:user_id>/toggle', methods=['POST'])
def toggle_user_status(user_id):
    """Disable/enable user account"""
    if not is_admin_or_coordinator():
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard'))
    
    user = User.query.get_or_404(user_id)
    
    try:
        # Prevent disabling last admin
        if user.role == 'admin' and not user.is_disabled:
            admin_count = User.query.filter_by(role='admin', is_disabled=False).count()
            if admin_count <= 1:
                flash('Cannot disable last active admin account.', 'error')
                return redirect(url_for('manage_users'))
        
        user.is_disabled = not user.is_disabled
        db.session.commit()
        status = 'disabled' if user.is_disabled else 'enabled'
        flash(f'User "{user.username}" {status} successfully.', 'success')
    except Exception as e:
        flash(f'Error toggling user status: {str(e)}', 'error')
    
    return redirect(url_for('manage_users'))

@app.route('/admin/backup')
def export_backup():
    """Export SQLite database as a downloadable backup file"""
    if not session.get('role') == 'admin':
        flash('Admin access required.', 'danger')
        return redirect(url_for('manage_users'))

    try:
        db_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'instance', 'database.db')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f'dics_ai_backup_{timestamp}.db'
        backup_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'instance', backup_filename)

        # Use SQLite backup API for a safe consistent copy
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(backup_path)
        src.backup(dst)
        dst.close()
        src.close()

        return send_file(
            backup_path,
            as_attachment=True,
            download_name=backup_filename,
            mimetype='application/octet-stream'
        )
    except Exception as e:
        flash(f'Backup failed: {str(e)}', 'error')
        return redirect(url_for('manage_users'))


# ============= INCIDENT COMMANDER ROUTES =============

@app.route('/incident-commander-dashboard')
def incident_commander_dashboard():
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))
    
    commander = User.query.filter_by(username=session['username']).first()
    
    # Get all active incident responses assigned to this commander
    active_responses = db.session.query(IncidentResponse).filter(
        IncidentResponse.commander_id == commander.id,
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).all()
    
    # Get all critical incidents
    critical_incidents = db.session.query(Incident).filter(
        Incident.level.in_(['CRITICAL', 'HIGH'])
    ).order_by(Incident.created_at.desc()).all()
    
    # Get statistics
    total_active = len(active_responses)
    total_critical = len(critical_incidents)
    total_tasks = db.session.query(Task).join(IncidentResponse).filter(
        IncidentResponse.commander_id == commander.id,
        Task.status != 'COMPLETED'
    ).count()
    total_resources = db.session.query(Resource).join(IncidentResponse).filter(
        IncidentResponse.commander_id == commander.id,
        Resource.status == 'DEPLOYED'
    ).count()
    
    return render_template('pages/incident_commander_dashboard.html',
                         active_responses=active_responses,
                         critical_incidents=critical_incidents,
                         total_active=total_active,
                         total_critical=total_critical,
                         total_tasks=total_tasks,
                         total_resources=total_resources)


@app.route('/incident/<int:incident_id>/activate-response', methods=['POST'])
def activate_incident_response(incident_id):
    if not is_incident_commander():
        return jsonify({'error': 'Unauthorized'}), 403
    
    incident = Incident.query.get_or_404(incident_id)
    commander = User.query.filter_by(username=session['username']).first()
    
    # Check if response already exists
    existing = IncidentResponse.query.filter_by(incident_id=incident_id).first()
    if existing:
        return jsonify({'error': 'Incident response already active'}), 400
    
    response = IncidentResponse(
        incident_id=incident_id,
        commander_id=commander.id,
        status='ACTIVE',
        situation_summary=f"Incident Response initiated for {incident.hazard_type} at {incident.location}",
        priority_level='CRITICAL' if incident.level == 'CRITICAL' else 'HIGH' if incident.level == 'HIGH' else 'MEDIUM'
    )
    
    db.session.add(response)
    db.session.commit()
    
    flash(f'Incident response activated for incident {incident_id}', 'success')
    return redirect(url_for('incident_commander_dashboard'))


def compile_incident_timeline(response):
    """Compiles a chronological list of events for the incident response"""
    events = []
    
    # 1. Incident response started
    events.append({
        'timestamp': response.started_at,
        'title': 'Response Initiated',
        'type': 'system',
        'icon': 'bi-play-fill',
        'badge_class': 'bg-success',
        'details': f"Commander initiated emergency operations."
    })
    
    # 2. Tasks assigned and completed
    for task in response.tasks:
        events.append({
            'timestamp': task.created_at,
            'title': f"Task Assigned: {task.title}",
            'type': 'task_assign',
            'icon': 'bi-list-task',
            'badge_class': 'bg-primary',
            'details': f"Assigned to {task.assigned_to_agency} (Priority: {task.priority})."
        })
        if task.completed_at:
            events.append({
                'timestamp': task.completed_at,
                'title': f"Task Completed: {task.title}",
                'type': 'task_complete',
                'icon': 'bi-check-circle-fill',
                'badge_class': 'bg-success',
                'details': f"Completed by {task.assigned_to_agency}."
            })
            
    # 3. Resources allocated and deployed
    for resource in response.resources:
        events.append({
            'timestamp': resource.allocated_at,
            'title': f"Resource Allocated: {resource.quantity}x {resource.resource_type}",
            'type': 'resource_allocate',
            'icon': 'bi-boxes',
            'badge_class': 'bg-info',
            'details': f"Allocated from {resource.agency}."
        })
        if resource.deployed_at:
            events.append({
                'timestamp': resource.deployed_at,
                'title': f"Resource Deployed: {resource.resource_type}",
                'type': 'resource_deploy',
                'icon': 'bi-truck',
                'badge_class': 'bg-success',
                'details': f"Deployed to {resource.location or 'assigned sectors'}."
            })
            
    # 4. Situation reports
    for report in response.reports:
        if report.report_type == 'CLOSURE':
            continue
        events.append({
            'timestamp': report.created_at,
            'title': f"Situation Report: {report.title}",
            'type': 'report',
            'icon': 'bi-file-text',
            'badge_class': 'bg-warning text-dark' if report.report_type == 'UPDATE' else 'bg-danger' if report.report_type == 'ALERT' else 'bg-success',
            'details': f"{report.content} (Reporter: {report.reporter.username})"
        })
        
    # 5. Incident closed
    if response.closed_at:
        events.append({
            'timestamp': response.closed_at,
            'title': 'Response Closed',
            'type': 'closure',
            'icon': 'bi-archive-fill',
            'badge_class': 'bg-secondary',
            'details': f"Response closed and operations archived."
        })
        
    events.sort(key=lambda x: x['timestamp'])
    return events


@app.route('/incident-response/<int:response_id>')
def incident_response_detail(response_id):
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))
    
    response = IncidentResponse.query.get_or_404(response_id)
    incident = response.incident
    
    tasks = Task.query.filter_by(incident_response_id=response_id).all()
    resources = Resource.query.filter_by(incident_response_id=response_id).all()
    reports = SituationReport.query.filter_by(incident_response_id=response_id).order_by(SituationReport.created_at.desc()).all()
    
    # Calculate statistics
    total_tasks = len(tasks)
    completed_tasks = sum(1 for t in tasks if t.status == 'COMPLETED')
    task_completion_pct = int(completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
    deployed_resources = sum(1 for r in resources if r.status == 'DEPLOYED')
    
    total_casualties = db.session.query(db.func.sum(SituationReport.casualties)).filter(SituationReport.incident_response_id == response_id).scalar() or 0
    total_evacuated = db.session.query(db.func.sum(SituationReport.evacuated)).filter(SituationReport.incident_response_id == response_id).scalar() or 0
    
    return render_template('pages/incident_response_detail.html',
                         response=response,
                         incident=incident,
                         tasks=tasks,
                         resources=resources,
                         reports=reports,
                         active_tab='dashboard',
                         total_tasks=total_tasks,
                         completed_tasks=completed_tasks,
                         task_completion_pct=task_completion_pct,
                         deployed_resources=deployed_resources,
                         total_casualties=total_casualties,
                         total_evacuated=total_evacuated)


@app.route('/incident-response/<int:response_id>/tasks')
def incident_response_tasks(response_id):
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))
    
    response = IncidentResponse.query.get_or_404(response_id)
    tasks = Task.query.filter_by(incident_response_id=response_id).all()
    
    return render_template('pages/incident_response_tasks.html',
                         response=response,
                         tasks=tasks,
                         active_tab='tasks')


@app.route('/incident-response/<int:response_id>/resources')
def incident_response_resources(response_id):
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))
    
    response = IncidentResponse.query.get_or_404(response_id)
    resources = Resource.query.filter_by(incident_response_id=response_id).all()
    
    return render_template('pages/incident_response_resources.html',
                         response=response,
                         resources=resources,
                         active_tab='resources')


@app.route('/incident-response/<int:response_id>/reports')
def incident_response_reports(response_id):
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))
    
    response = IncidentResponse.query.get_or_404(response_id)
    reports = SituationReport.query.filter_by(incident_response_id=response_id).order_by(SituationReport.created_at.desc()).all()
    
    return render_template('pages/incident_response_reports.html',
                         response=response,
                         reports=reports,
                         active_tab='reports')


@app.route('/incident-response/<int:response_id>/timeline')
def incident_response_timeline(response_id):
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))
    
    response = IncidentResponse.query.get_or_404(response_id)
    events = compile_incident_timeline(response)
    
    return render_template('pages/incident_response_timeline.html',
                         response=response,
                         events=events,
                         active_tab='timeline')


@app.route('/incident-response/<int:response_id>/close', methods=['GET', 'POST'])
def incident_response_close_page(response_id):
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))
    
    response = IncidentResponse.query.get_or_404(response_id)
    commander = User.query.filter_by(username=session['username']).first()
    
    if request.method == 'POST':
        # Perform closure
        summary = request.form.get('notes', '').strip()
        casualties = int(request.form.get('casualties') or 0)
        evacuated = int(request.form.get('evacuated') or 0)
        
        # Save as a closure SituationReport
        closure_report = SituationReport(
            incident_response_id=response_id,
            reporter_id=commander.id,
            title='Operational Closure Summary',
            content=summary or 'Incident response operations closed by commander.',
            report_type='CLOSURE',
            casualties=casualties,
            evacuated=evacuated,
            affected_areas='All Areas Closed'
        )
        
        response.status = 'CLOSED'
        response.closed_at = datetime.utcnow()
        response.resolved_at = datetime.utcnow()
        response.situation_summary = f"Response Closed. Total Casualties: {casualties}, Total Evacuated: {evacuated}"
        
        # Mark the underlying incident as resolved (alert=False)
        response.incident.alert = False
        
        db.session.add(closure_report)
        db.session.commit()
        
        flash('Incident response closed successfully and incident marked resolved.', 'success')
        return redirect(url_for('incident_commander_dashboard'))
        
    return render_template('pages/incident_response_close.html',
                         response=response,
                         active_tab='close')


@app.route('/incident-response/<int:response_id>/assign-task', methods=['GET', 'POST'])
def assign_task(response_id):
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))
    
    response = IncidentResponse.query.get_or_404(response_id)
    commander = User.query.filter_by(username=session['username']).first()
    
    if request.method == 'POST':
        agency = request.form.get('agency')
        title = request.form.get('title')
        description = request.form.get('description')
        priority = request.form.get('priority', 'MEDIUM')
        estimated_completion = request.form.get('estimated_completion')
        
        task = Task(
            incident_response_id=response_id,
            assigned_to_agency=agency,
            assigned_by_id=commander.id,
            title=title,
            description=description,
            priority=priority,
            status='PENDING',
            estimated_completion=datetime.fromisoformat(estimated_completion) if estimated_completion else None
        )
        
        db.session.add(task)
        db.session.commit()
        
        flash(f'Task "{title}" assigned to {agency}', 'success')
        
    return redirect(url_for('incident_response_tasks', response_id=response_id))


@app.route('/incident-response/<int:response_id>/allocate-resource', methods=['GET', 'POST'])
def allocate_resource(response_id):
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))
    
    response = IncidentResponse.query.get_or_404(response_id)
    
    if request.method == 'POST':
        resource_type = request.form.get('resource_type')
        agency = request.form.get('agency')
        quantity = int(request.form.get('quantity', 1))
        location = request.form.get('location')
        notes = request.form.get('notes')
        
        resource = Resource(
            incident_response_id=response_id,
            resource_type=resource_type,
            agency=agency,
            quantity=quantity,
            location=location,
            notes=notes,
            status='AVAILABLE'
        )
        
        db.session.add(resource)
        db.session.commit()
        
        flash(f'Resource allocated: {quantity} x {resource_type} from {agency}', 'success')
        
    return redirect(url_for('incident_response_resources', response_id=response_id))


@app.route('/incident-response/<int:response_id>/create-report', methods=['GET', 'POST'])
def create_situation_report(response_id):
    if not is_incident_commander() and session.get('role') != 'agency_coordinator':
        flash('Access required.', 'danger')
        return redirect(url_for('dashboard'))
    
    response = IncidentResponse.query.get_or_404(response_id)
    reporter = User.query.filter_by(username=session['username']).first()
    
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        report_type = request.form.get('report_type', 'UPDATE')
        affected_areas = request.form.get('affected_areas')
        casualties = request.form.get('casualties', type=int) or 0
        evacuated = request.form.get('evacuated', type=int) or 0
        
        report = SituationReport(
            incident_response_id=response_id,
            reporter_id=reporter.id,
            title=title,
            content=content,
            report_type=report_type,
            affected_areas=affected_areas,
            casualties=casualties,
            evacuated=evacuated
        )
        
        db.session.add(report)
        response.situation_summary = f"Latest Report: {title}"
        db.session.commit()
        
        flash(f'Situation report "{title}" created successfully', 'success')
        
    return redirect(url_for('incident_response_reports', response_id=response_id))


@app.route('/incident-response/<int:response_id>/update-task/<int:task_id>', methods=['POST'])
def update_task(response_id, task_id):
    if not is_incident_commander():
        return jsonify({'error': 'Unauthorized'}), 403
    
    task = Task.query.get_or_404(task_id)
    status = request.form.get('status')
    
    task.status = status
    if status == 'COMPLETED':
        task.completed_at = datetime.utcnow()
    
    db.session.commit()
    
    flash(f'Task status updated to {status}', 'success')
    return redirect(url_for('incident_response_tasks', response_id=response_id))


@app.route('/incident-response/<int:response_id>/update-resource/<int:resource_id>', methods=['POST'])
def update_resource(response_id, resource_id):
    if not is_incident_commander():
        return jsonify({'error': 'Unauthorized'}), 403
    
    resource = Resource.query.get_or_404(resource_id)
    status = request.form.get('status')
    location = request.form.get('location')
    
    resource.status = status
    if location:
        resource.location = location
    if status == 'DEPLOYED':
        resource.deployed_at = datetime.utcnow()
    
    db.session.commit()
    
    flash(f'Resource status updated to {status}', 'success')
    return redirect(url_for('incident_response_resources', response_id=response_id))


@app.route('/api/incident-response-stats')
def get_incident_response_stats():
    """Get statistics for incident command API"""
    if not is_incident_commander():
        return jsonify({'error': 'Unauthorized'}), 403
    
    commander = User.query.filter_by(username=session['username']).first()
    
    # Get active responses
    active_responses = db.session.query(IncidentResponse).filter(
        IncidentResponse.commander_id == commander.id,
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).count()
    
    # Get pending tasks
    pending_tasks = db.session.query(Task).join(IncidentResponse).filter(
        IncidentResponse.commander_id == commander.id,
        Task.status.in_(['PENDING', 'IN_PROGRESS'])
    ).count()
    
    # Get deployed resources
    deployed_resources = db.session.query(Resource).join(IncidentResponse).filter(
        IncidentResponse.commander_id == commander.id,
        Resource.status == 'DEPLOYED'
    ).count()
    
    return jsonify({
        'active_responses': active_responses,
        'pending_tasks': pending_tasks,
        'deployed_resources': deployed_resources
    })



# ============================================================
# EOC STAFF ROUTES
# ============================================================

@app.route('/eoc-dashboard')
def eoc_dashboard():
    if not is_eoc_staff():
        flash('EOC Staff access required.', 'danger')
        return redirect(url_for('dashboard'))

    # System-wide active incident responses (not scoped to a single commander)
    active_responses = db.session.query(IncidentResponse).filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).order_by(IncidentResponse.started_at.desc()).all()

    # System-wide critical/high incidents without an active response yet
    critical_incidents = db.session.query(Incident).filter(
        Incident.level.in_(['CRITICAL', 'HIGH'])
    ).order_by(Incident.created_at.desc()).limit(10).all()

    # Most recent incidents overall, for the live feed
    recent_incidents = db.session.query(Incident).order_by(
        Incident.created_at.desc()
    ).limit(8).all()

    # Headline stats
    total_active = len(active_responses)
    total_critical = db.session.query(Incident).filter(
        Incident.level.in_(['CRITICAL', 'HIGH'])
    ).count()
    total_tasks = db.session.query(Task).filter(
        Task.status.in_(['PENDING', 'IN_PROGRESS'])
    ).count()
    total_resources = db.session.query(Resource).filter(
        Resource.status == 'DEPLOYED'
    ).count()
    total_incidents_all = db.session.query(Incident).count()

    # Hazard type breakdown, for a quick at-a-glance analytics widget
    hazard_breakdown = db.session.query(
        Incident.hazard_type, db.func.count(Incident.id)
    ).group_by(Incident.hazard_type).order_by(db.func.count(Incident.id).desc()).all()

    return render_template('pages/eoc_dashboard.html',
                         active_responses=active_responses,
                         critical_incidents=critical_incidents,
                         recent_incidents=recent_incidents,
                         total_active=total_active,
                         total_critical=total_critical,
                         total_tasks=total_tasks,
                         total_resources=total_resources,
                         total_incidents_all=total_incidents_all,
                         hazard_breakdown=hazard_breakdown)


@app.route('/eoc/incidents')
def eoc_incident_monitoring():
    if not is_eoc_staff():
        flash('EOC Staff access required.', 'danger')
        return redirect(url_for('dashboard'))

    level_filter = request.args.get('level', '').strip()
    hazard_filter = request.args.get('hazard', '').strip()
    status_filter = request.args.get('status', '').strip()

    query = db.session.query(Incident)
    if level_filter:
        query = query.filter(Incident.level == level_filter)
    if hazard_filter:
        query = query.filter(Incident.hazard_type == hazard_filter)
    if status_filter == 'responded':
        query = query.join(IncidentResponse, isouter=False)
    elif status_filter == 'unresponded':
        query = query.outerjoin(IncidentResponse).filter(IncidentResponse.id.is_(None))
    elif status_filter == 'alert':
        query = query.filter(Incident.alert.is_(True))

    incidents = query.order_by(Incident.created_at.desc()).all()

    # Distinct hazard types for the filter dropdown
    hazard_types = [row[0] for row in db.session.query(Incident.hazard_type).distinct().order_by(Incident.hazard_type).all()]

    total_incidents = db.session.query(Incident).count()
    total_alerts = db.session.query(Incident).filter(Incident.alert.is_(True)).count()
    total_critical = db.session.query(Incident).filter(Incident.level == 'CRITICAL').count()
    total_unresponded = db.session.query(Incident).filter(
        Incident.level.in_(['CRITICAL', 'HIGH'])
    ).outerjoin(IncidentResponse).filter(IncidentResponse.id.is_(None)).count()

    return render_template('pages/eoc_incident_monitoring.html',
                         incidents=incidents,
                         hazard_types=hazard_types,
                         level_filter=level_filter,
                         hazard_filter=hazard_filter,
                         status_filter=status_filter,
                         total_incidents=total_incidents,
                         total_alerts=total_alerts,
                         total_critical=total_critical,
                         total_unresponded=total_unresponded)


@app.route('/eoc/resources')
def eoc_resource_monitoring():
    if not is_eoc_staff():
        flash('EOC Staff access required.', 'danger')
        return redirect(url_for('dashboard'))

    status_filter = request.args.get('status', '').strip()
    agency_filter = request.args.get('agency', '').strip()

    query = db.session.query(Resource).join(IncidentResponse).join(Incident)
    if status_filter:
        query = query.filter(Resource.status == status_filter)
    if agency_filter:
        query = query.filter(Resource.agency == agency_filter)

    resources = query.order_by(Resource.allocated_at.desc()).all()

    agencies = [row[0] for row in db.session.query(Resource.agency).distinct().order_by(Resource.agency).all()]

    total_deployed = db.session.query(Resource).filter(Resource.status == 'DEPLOYED').count()
    total_available = db.session.query(Resource).filter(Resource.status == 'AVAILABLE').count()
    total_returning = db.session.query(Resource).filter(Resource.status == 'RETURNING').count()
    total_units = db.session.query(db.func.coalesce(db.func.sum(Resource.quantity), 0)).filter(
        Resource.status == 'DEPLOYED'
    ).scalar()

    # Deployed resource breakdown by agency, for a quick allocation snapshot
    agency_breakdown = db.session.query(
        Resource.agency, db.func.count(Resource.id)
    ).filter(Resource.status == 'DEPLOYED').group_by(Resource.agency).order_by(db.func.count(Resource.id).desc()).all()

    return render_template('pages/eoc_resource_monitoring.html',
                         resources=resources,
                         agencies=agencies,
                         status_filter=status_filter,
                         agency_filter=agency_filter,
                         total_deployed=total_deployed,
                         total_available=total_available,
                         total_returning=total_returning,
                         total_units=total_units,
                         agency_breakdown=agency_breakdown)


# ============================================================
# AGENCY COORDINATOR ROUTES
# ============================================================

def is_coordinator():
    """Check if current user is agency_coordinator (not admin)"""
    return 'username' in session and session.get('role') == 'agency_coordinator'

def get_coordinator_agency():
    """Get current coordinator's agency name"""
    user = User.query.filter_by(username=session.get('username')).first()
    return user.agency if user else None


@app.route('/coordinator')
def coordinator_dashboard():
    """Agency Coordinator Hub – main landing page"""
    if not is_admin_or_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    agency = get_coordinator_agency()

    # Tasks assigned to this coordinator's agency
    my_tasks = Task.query.join(IncidentResponse).filter(
        Task.assigned_to_agency == agency,
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).order_by(Task.created_at.desc()).all() if agency else []

    pending_count = sum(1 for t in my_tasks if t.status in ('PENDING', 'IN_PROGRESS'))

    # Active responses
    active_responses = IncidentResponse.query.filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).order_by(IncidentResponse.started_at.desc()).all()

    # Resources deployed (all agencies, coordinator sees all)
    deployed_resources = Resource.query.filter_by(status='DEPLOYED').count()

    # Total active incidents with a response
    active_incidents = IncidentResponse.query.filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).count()

    return render_template('pages/coordinator_dashboard.html',
        my_tasks=my_tasks,
        pending_count=pending_count,
        active_responses=active_responses,
        deployed_resources=deployed_resources,
        active_incidents=active_incidents
    )


@app.route('/coordinator/tasks')
def coordinator_tasks():
    """Agency task management – all tasks assigned to coordinator's agency"""
    if not is_admin_or_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    agency = get_coordinator_agency()
    status_filter = request.args.get('status')
    priority_filter = request.args.get('priority')

    query = Task.query.filter_by(assigned_to_agency=agency) if agency else Task.query

    if status_filter:
        query = query.filter(Task.status == status_filter)
    if priority_filter:
        query = query.filter(Task.priority == priority_filter)

    tasks = query.order_by(Task.created_at.desc()).all()

    return render_template('pages/coordinator_tasks.html', tasks=tasks)


@app.route('/coordinator/tasks/<int:task_id>/update', methods=['POST'])
def coordinator_update_task(task_id):
    """Agency coordinator updates their own task status"""
    if not is_admin_or_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    task = Task.query.get_or_404(task_id)
    new_status = request.form.get('status')

    if new_status in ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'FAILED'):
        task.status = new_status
        if new_status == 'COMPLETED':
            task.completed_at = datetime.utcnow()
        db.session.commit()
        flash(f'Task "{task.title}" updated to {new_status}.', 'success')

    # Redirect back to where the user came from
    referrer = request.referrer
    if referrer:
        return redirect(referrer)
    return redirect(url_for('coordinator_tasks'))


@app.route('/coordinator/team')
def coordinator_team():
    """Team tracking – resources across agencies"""
    if not is_admin_or_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    # All resources for active responses
    resources = Resource.query.join(IncidentResponse).filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).order_by(Resource.agency, Resource.allocated_at.desc()).all()

    # Count resources per agency
    team_counts = {}
    for r in resources:
        team_counts[r.agency] = team_counts.get(r.agency, 0) + r.quantity

    # Task completion summary per agency
    all_tasks = Task.query.join(IncidentResponse).filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).all()

    task_summary = {}
    for t in all_tasks:
        agency_name = t.assigned_to_agency
        if agency_name not in task_summary:
            task_summary[agency_name] = {'total': 0, 'completed': 0}
        task_summary[agency_name]['total'] += 1
        if t.status == 'COMPLETED':
            task_summary[agency_name]['completed'] += 1

    return render_template('pages/coordinator_team.html',
        resources=resources,
        team_counts=team_counts,
        task_summary=task_summary
    )


@app.route('/coordinator/resources')
def coordinator_resources():
    """Resource request and tracking page"""
    if not is_admin_or_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    resources = Resource.query.join(IncidentResponse).filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).order_by(Resource.allocated_at.desc()).all()

    active_responses = IncidentResponse.query.filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).order_by(IncidentResponse.started_at.desc()).all()

    deployed_count = sum(1 for r in resources if r.status == 'DEPLOYED')
    available_count = sum(1 for r in resources if r.status == 'AVAILABLE')
    returning_count = sum(1 for r in resources if r.status == 'RETURNING')
    unavail_count = sum(1 for r in resources if r.status == 'UNAVAILABLE')

    return render_template('pages/coordinator_resources.html',
        resources=resources,
        active_responses=active_responses,
        deployed_count=deployed_count,
        available_count=available_count,
        returning_count=returning_count,
        unavail_count=unavail_count
    )


@app.route('/coordinator/resources/allocate', methods=['POST'])
def coordinator_allocate_resource():
    """Coordinator allocates a resource to a response"""
    if not is_admin_or_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    response_id = request.form.get('response_id', type=int)
    agency = request.form.get('agency', '').strip()
    resource_type = request.form.get('resource_type', '').strip()
    quantity = request.form.get('quantity', type=int, default=1)
    status = request.form.get('status', 'AVAILABLE')
    location = request.form.get('location', '').strip()
    notes = request.form.get('notes', '').strip()

    if not all([response_id, agency, resource_type, quantity]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('coordinator_resources'))

    response = IncidentResponse.query.get_or_404(response_id)

    resource = Resource(
        incident_response_id=response.id,
        resource_type=resource_type,
        agency=agency,
        quantity=quantity,
        status=status,
        location=location or None,
        notes=notes or None,
        deployed_at=datetime.utcnow() if status == 'DEPLOYED' else None
    )
    db.session.add(resource)
    db.session.commit()
    flash(f'{quantity}x {resource_type} ({agency}) allocated to Response #{response.incident_id}.', 'success')
    return redirect(url_for('coordinator_resources'))


@app.route('/coordinator/resources/<int:resource_id>/update', methods=['POST'])
def coordinator_update_resource(resource_id):
    """Update resource status"""
    if not is_admin_or_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    resource = Resource.query.get_or_404(resource_id)
    new_status = request.form.get('status')

    if new_status in ('AVAILABLE', 'DEPLOYED', 'RETURNING', 'UNAVAILABLE'):
        resource.status = new_status
        if new_status == 'DEPLOYED' and not resource.deployed_at:
            resource.deployed_at = datetime.utcnow()
        db.session.commit()
        flash(f'Resource status updated to {new_status}.', 'success')

    referrer = request.referrer
    if referrer:
        return redirect(referrer)
    return redirect(url_for('coordinator_resources'))


@app.route('/coordinator/reports')
def coordinator_reports():
    """Situation reports page"""
    if not is_admin_or_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    reports = SituationReport.query.join(IncidentResponse).filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).order_by(SituationReport.created_at.desc()).all()

    active_responses = IncidentResponse.query.filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).order_by(IncidentResponse.started_at.desc()).all()

    return render_template('pages/coordinator_reports.html',
        reports=reports,
        active_responses=active_responses
    )


@app.route('/coordinator/reports/submit', methods=['POST'])
def coordinator_submit_report():
    """Submit a situation report"""
    if not is_admin_or_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    response_id = request.form.get('response_id', type=int)
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()
    report_type = request.form.get('report_type', 'UPDATE')
    affected_areas = request.form.get('affected_areas', '').strip()
    evacuated = request.form.get('evacuated', type=int)
    casualties = request.form.get('casualties', type=int)

    if not all([response_id, title, content]):
        flash('Please fill in all required fields.', 'error')
        referrer = request.referrer
        return redirect(referrer or url_for('coordinator_reports'))

    user = User.query.filter_by(username=session['username']).first()
    response = IncidentResponse.query.get_or_404(response_id)

    report = SituationReport(
        incident_response_id=response.id,
        reporter_id=user.id,
        title=title,
        content=content,
        report_type=report_type if report_type in ('UPDATE', 'ALERT', 'MILESTONE', 'CLOSURE') else 'UPDATE',
        affected_areas=affected_areas or None,
        evacuated=evacuated,
        casualties=casualties
    )
    db.session.add(report)
    db.session.commit()
    flash(f'Situation report "{title}" submitted successfully.', 'success')

    referrer = request.referrer
    if referrer:
        return redirect(referrer)
    return redirect(url_for('coordinator_reports'))


@app.route('/coordinator/comms')
def coordinator_comms():
    """Communication Center – inter-agency coordination feed"""
    if not is_admin_or_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    # All situation reports from active responses, recent first
    comm_logs = SituationReport.query.join(IncidentResponse).filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).order_by(SituationReport.created_at.desc()).limit(50).all()

    active_responses = IncidentResponse.query.filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).order_by(IncidentResponse.started_at.desc()).all()

    return render_template('pages/coordinator_comms.html',
        comm_logs=comm_logs,
        active_responses=active_responses
    )


@app.route('/coordinator/response/<int:response_id>')
def coordinator_response_detail(response_id):
    """Coordinator view of a specific incident response"""
    if not is_admin_or_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    response = IncidentResponse.query.get_or_404(response_id)
    agency = get_coordinator_agency()

    # Tasks assigned specifically to this coordinator's agency
    agency_tasks = [t for t in response.tasks if t.assigned_to_agency == agency] if agency else []

    return render_template('pages/coordinator_response_detail.html',
        response=response,
        agency_tasks=agency_tasks
    )


@app.route('/coordinator/quick-report', methods=['POST'])
def coordinator_quick_report():
    """Quick status report shortcut from dashboard"""
    return coordinator_submit_report()


if __name__ == '__main__':
    create_tables()
    app.run(debug=True, use_reloader=False, host='127.0.0.1', port=5000)