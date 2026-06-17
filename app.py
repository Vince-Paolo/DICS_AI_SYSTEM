import os
import sqlite3
import secrets
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Incident
from ai.prediction import predict_hazard
from services.realtime_data import get_weather_data, get_earthquake_data

# Helper function for authorization
def is_admin_or_coordinator():
    """Check if current user is admin or agency_coordinator"""
    return 'username' in session and session.get('role') in ['admin', 'agency_coordinator']

app = Flask(__name__)
base_dir = os.path.abspath(os.path.dirname(__file__))
instance_dir = os.path.join(base_dir, 'instance')
os.makedirs(instance_dir, exist_ok=True)
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(instance_dir, 'database.db').replace('\\', '/') }"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'replace-this-with-a-secret'
app.config['TEMPLATES_AUTO_RELOAD'] = True

db.init_app(app)

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
            flash('Welcome back, ' + user.username + '!', 'success')
            if user.role in ['admin', 'agency_coordinator']:
                return redirect(url_for('admin'))
            return redirect(url_for('dashboard'))
        else:
            error = 'Invalid username or password.'
    return render_template('login.html', error=error)

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
    return render_template('register.html', error=error)

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
        'dashboard.html',
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
    return render_template('analytics.html')

@app.route('/hazard-map')
def hazard_map():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('hazard_map.html', sidebar_variant='hazard')

@app.route('/ics')
def ics_page():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('ics.html')

@app.route('/protocols')
def protocols():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('protocols.html')

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
    
    return render_template('citizen_report.html', 
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
    
    return render_template('citizen_alerts.html', 
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
    
    return render_template('citizen_status.html', 
                         incidents=incidents, 
                         total_incidents=total_incidents, 
                         pending_count=pending_count)

@app.route('/citizen-resources')
def citizen_resources():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('citizen_resources.html')

@app.route('/incidents')
def incident_history():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))

    incidents = Incident.query.filter_by(user_id=user.id).order_by(Incident.created_at.desc()).all()
    return render_template('incidents.html', incidents=incidents)

@app.route('/alerts')
def alerts():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))

    alerts = Incident.query.filter_by(user_id=user.id, alert=True).order_by(Incident.created_at.desc()).all()
    return render_template('alerts.html', alerts=alerts)

@app.route('/admin/alerts')
def admin_alerts():
    if not is_admin_or_coordinator():
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard'))

    incidents = Incident.query.order_by(Incident.created_at.desc()).all()
    return render_template('admin_alerts.html', incidents=incidents)

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

    return render_template('ai_prediction.html', 
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
    return render_template('user_management.html', users=users, roles=roles)

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

if __name__ == '__main__':
    create_tables()
    app.run(debug=True, use_reloader=False, host='127.0.0.1', port=5000)
