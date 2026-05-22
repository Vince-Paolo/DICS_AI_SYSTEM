import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Incident
from ai.prediction import predict_hazard
from services.realtime_data import get_weather_data, get_earthquake_data

app = Flask(__name__)
base_dir = os.path.abspath(os.path.dirname(__file__))
instance_dir = os.path.join(base_dir, 'instance')
os.makedirs(instance_dir, exist_ok=True)
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(instance_dir, 'database.db').replace('\\', '/') }"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'replace-this-with-a-secret'

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
            conn.commit()


def create_default_admin():
    admin = User.query.filter_by(role='admin').first()
    if admin is None:
        admin = User(
            username='admin',
            password=generate_password_hash('Admin123!'),
            role='admin',
        )
        db.session.add(admin)
        db.session.commit()


def create_tables():
    with app.app_context():
        db.create_all()
        migrate_user_table()
        create_default_admin()

create_tables()

def verify_password(user, password):
    if user is None:
        return False

    stored = user.password
    if stored.startswith('pbkdf2:') or stored.startswith('argon2:'):
        return check_password_hash(stored, password)

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
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and verify_password(user, password):
            session['username'] = user.username
            session['role'] = user.role
            flash('Welcome back, ' + user.username + '!', 'success')
            if user.role == 'admin':
                return redirect(url_for('admin'))
            return redirect(url_for('dashboard'))
        error = 'Invalid username or password.'
    return render_template('login.html', error=error)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'username' in session:
        return redirect(url_for('dashboard'))

    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            error = 'Username and password are required.'
        elif User.query.filter_by(username=username).first():
            error = 'Username already exists.'
        else:
            new_user = User(
                username=username,
                password=generate_password_hash(password),
                role='user',
            )
            db.session.add(new_user)
            db.session.commit()
            flash('Registration successful. Please log in.', 'success')
            return redirect(url_for('login'))
    return render_template('register.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/admin')
def admin():
    if 'username' not in session or session.get('role') != 'admin':
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

    # fetch live external data (may return None/empty on failure)
    weather_data = get_weather_data("Cavite")
    earthquake_data = get_earthquake_data()

    return render_template(
        'dashboard.html',
        username=user.username,
        user_role=user.role,
        incidents=incidents,
        total_incidents=total_incidents,
        alert_count=alert_count,
        weather_data=weather_data,
        earthquake_data=earthquake_data,
    )


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
    return render_template('hazard_map.html')

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
    if 'username' not in session or session.get('role') != 'admin':
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard'))

    incidents = Incident.query.order_by(Incident.created_at.desc()).all()
    return render_template('admin_alerts.html', incidents=incidents)

@app.route('/admin/alerts/<int:incident_id>/toggle', methods=['POST'])
def toggle_alert(incident_id):
    if 'username' not in session or session.get('role') != 'admin':
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

    return render_template('ai_prediction.html', prediction=prediction)

if __name__ == '__main__':
    app.run(debug=True)
