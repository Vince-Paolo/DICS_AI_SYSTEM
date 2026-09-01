import os
import glob
import sqlite3
import threading
import secrets
from datetime import datetime, timedelta, timezone
from flask import Flask, current_app, render_template, request, redirect, url_for, session, flash, send_from_directory
from flask_mail import Mail, Message
from sqlalchemy import text
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_migrate import Migrate
from werkzeug.security import check_password_hash, generate_password_hash
from flask_apscheduler import APScheduler
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from models import db, User, Incident, IncidentResponse, Task, Resource, CitizenReport, Agency, PostIncidentReport
from scheduler import monitor_hazards
from services.realtime_data import get_all_weather_data, get_weather_data, get_earthquake_data
from services import permissions as permission_service
from services.passwords import verify_and_migrate
from ai.decision_support import predict_hazard
from seed.demo_data import seed_geography_data

from blueprints.admin import admin_bp
from blueprints.commander import commander_bp
from blueprints.coordinator import coordinator_bp
from blueprints.responder import responder_bp
from blueprints.eoc import eoc_bp
from blueprints.citizen import citizen_bp
from blueprints.ai import ai_bp
from blueprints.facilities import facilities_bp


app = Flask(__name__)
base_dir = os.path.abspath(os.path.dirname(__file__))
instance_dir = os.path.join(base_dir, 'instance')
os.makedirs(instance_dir, exist_ok=True)
upload_dir = os.path.join(instance_dir, 'uploads', 'citizen_reports')
os.makedirs(upload_dir, exist_ok=True)

def _normalize_database_url():
    configured_url = os.environ.get('DATABASE_URL')
    if not configured_url:
        configured_url = f"sqlite:///{os.path.join(instance_dir, 'database.db').replace('\\', '/')}"

    # Railway (and Heroku-style) Postgres plugins hand out URLs starting with
    # 'postgres://', but SQLAlchemy 1.4+/2.x only accepts 'postgresql://'.
    if configured_url.startswith('postgres://'):
        configured_url = 'postgresql://' + configured_url[len('postgres://'):]

    # Flask-SQLAlchemy/SQLAlchemy can misinterpret relative sqlite paths like
    # 'sqlite:///instance/database.db' when the runtime CWD differs from the
    # project root. Resolve them against the project base directory instead.
    if configured_url.startswith('sqlite:///') and not configured_url.startswith('sqlite:////'):
        relative_path = configured_url[len('sqlite:///'):]
        if relative_path:
            normalized_path = os.path.normpath(os.path.join(base_dir, relative_path))
            configured_url = f"sqlite:///{normalized_path.replace('\\', '/')}"

    return configured_url

app.config['SQLALCHEMY_DATABASE_URI'] = _normalize_database_url()
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


def _get_limiter_storage_uri():
    """Flask-Limiter supports shared stores such as Redis/Postgres, but not
    SQLite-backed storage for multi-process deployments. Use the same
    Postgres DSN as the app when available; otherwise fall back to an
    in-memory store for local SQLite-only development."""
    database_url = app.config['SQLALCHEMY_DATABASE_URI']
    if database_url.startswith('postgresql:'):
        return database_url
    return 'memory://'


_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    import secrets as _secrets
    import warnings as _warnings
    _secret_key = _secrets.token_hex(32)
    _warnings.warn(
        'SECRET_KEY environment variable is not set. Generated a random '
        'temporary key for this process only; all sessions will be invalidated '
        'on restart and this is NOT safe for a multi-process/production deployment. '
        'Set the SECRET_KEY environment variable before deploying.',
        RuntimeWarning
    )
app.config['SECRET_KEY'] = _secret_key
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['UPLOAD_FOLDER'] = upload_dir
app.config['INSTANCE_DIR'] = instance_dir
app.config['MAX_UPLOAD_SIZE_BYTES'] = int(os.environ.get('MAX_UPLOAD_SIZE_BYTES', 16 * 1024 * 1024))
app.config['MAX_CONTENT_LENGTH'] = app.config['MAX_UPLOAD_SIZE_BYTES']
app.config['WTF_CSRF_ENABLED'] = True
app.config['SCHEDULER_API_ENABLED'] = True
app.config['SCHEDULER_TIMEZONE'] = 'UTC'
app.config['PROPAGATE_EXCEPTIONS'] = False
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['ENABLE_HSTS'] = os.environ.get('ENABLE_HSTS', 'false').lower() == 'true'
gmail_username = os.environ.get('MAIL_USERNAME') or os.environ.get('GMAIL_USERNAME')
mail_server = os.environ.get('MAIL_SERVER') or ('smtp.gmail.com' if gmail_username else 'localhost')
mail_port = os.environ.get('MAIL_PORT')
if mail_port is None:
    mail_port = '587' if mail_server == 'smtp.gmail.com' else '1025'
mail_use_tls = os.environ.get('MAIL_USE_TLS')
if mail_use_tls is None:
    mail_use_tls = 'true' if mail_server == 'smtp.gmail.com' else 'false'
mail_use_ssl = os.environ.get('MAIL_USE_SSL')
if mail_use_ssl is None:
    mail_use_ssl = 'false'

app.config['MAIL_SERVER'] = mail_server
app.config['MAIL_PORT'] = int(mail_port)
app.config['MAIL_USE_TLS'] = mail_use_tls.lower() == 'true'
app.config['MAIL_USE_SSL'] = mail_use_ssl.lower() == 'true'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME') or os.environ.get('GMAIL_USERNAME', '')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD') or os.environ.get('GMAIL_APP_PASSWORD', '')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER') or app.config['MAIL_USERNAME'] or 'noreply@dics.local'
app.config['MAIL_SUPPRESS_SEND'] = os.environ.get('MAIL_SUPPRESS_SEND', 'false').lower() == 'true'
app.config['MAIL_DEBUG'] = int(os.environ.get('MAIL_DEBUG', '0'))
mail = Mail()
mail.init_app(app)


@app.after_request
def add_security_headers(response):
    # Keep the inline exceptions only for the small set of legacy inline
    # bootstrap/js behaviors still in use. This is a defense-in-depth gap, not
    # an active vulnerability after the upload validation fix, but it should be
    # tightened later to nonce/hash-based CSP if the remaining inline usage is
    # ever refactored or bundled out of the template layer.
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "script-src 'self' 'unsafe-inline' "
        "https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' "
        "https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://unpkg.com; "
        "font-src 'self' https://cdn.jsdelivr.net data:; "
        "img-src 'self' data: blob: https://tile.openstreetmap.org https://*.tile.openstreetmap.org; "
        "connect-src 'self' https://nominatim.openstreetmap.org; "
        "media-src 'self' blob:; "
        "worker-src 'self' blob:"
    )
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    if app.config['ENABLE_HSTS'] and request.is_secure:
        response.headers.setdefault(
            'Strict-Transport-Security',
            'max-age=31536000; includeSubDomains'
        )
    return response

csrf = CSRFProtect(app)

# Postgres connections handed out by managed/pooled providers (Railway
# included) can occasionally be recycled from a session that left a failed
# transaction open. When that happens, the very first query SQLAlchemy runs
# against the "new" connection -- its own internal hstore/type probe -- dies
# with "current transaction is aborted", before any of our own code runs.
# pool_pre_ping discards stale pooled connections on checkout, and the
# connect listener below (registered with insert=True so it runs before
# SQLAlchemy's own on-connect probes) clears any leftover aborted
# transaction state the moment a physical connection is established.
app.config.setdefault('SQLALCHEMY_ENGINE_OPTIONS', {})
app.config['SQLALCHEMY_ENGINE_OPTIONS'].update({
    'pool_pre_ping': True,
    'pool_recycle': 280,
})

db.init_app(app)
migrate = Migrate(app, db, directory=os.path.join(base_dir, 'migrations'))

if app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgresql:'):
    from sqlalchemy import event as _sa_event

    with app.app_context():
        @_sa_event.listens_for(db.engine, 'connect', insert=True)
        def _clear_stale_transaction_on_connect(dbapi_connection, connection_record):
            try:
                dbapi_connection.rollback()
            except Exception:
                pass

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    storage_uri=_get_limiter_storage_uri(),
    default_limits=['200 per day', '50 per hour'],
)

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
app.register_blueprint(facilities_bp)

# emergency_sos lives in blueprints/citizen.py, which is imported before
# `limiter` exists (it's imported at the top of this file, before app =
# Flask(__name__) itself) -- so it can't use @limiter.limit as a decorator
# without a circular import. Applying the limit here, after both the
# blueprint is registered and limiter exists, is Flask-Limiter's documented
# pattern for this exact situation. 5/minute is deliberately more generous
# than the 10/minute on login/register: a citizen retrying a failed SOS
# during a real emergency shouldn't get throttled, but this still meaningfully
# caps a scripted flood -- worth having now that a successful SOS triggers a
# real-time alert on the EOC dashboard (see /eoc/sos-incidents/pending),
# which repeated fake submissions would actively disrupt.
app.view_functions['citizen.emergency_sos'] = limiter.limit("5 per minute")(app.view_functions['citizen.emergency_sos'])

@app.errorhandler(404)
def handle_not_found(error):
    return render_template('pages/error_404.html', error=error), 404


@app.errorhandler(500)
def handle_server_error(error):
    return render_template('pages/error_500.html', error=error), 500


@app.context_processor
def inject_alert_count():
    alert_count = 0
    try:
        username = session.get('username')
        role = session.get('role')
    except RuntimeError:
        username = None
        role = None

    if username and role in ('user', 'citizen'):
        try:
            alert_count = Incident.query.filter(Incident.alert == True).count()
        except Exception:
            alert_count = 0
    return {'alert_count': alert_count}


def migrate_user_table():
    # This function edits the schema via a raw sqlite3 connection, which only
    # makes sense when the app is actually running on SQLite. On Postgres
    # (e.g. Railway's Postgres plugin) db.create_all() already builds the
    # full schema from the current models, and opening a stray local SQLite
    # file here would be misleading dead weight -- so skip it entirely.
    if not app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite:'):
        return
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
            if 'must_change_password' not in columns:
                cursor.execute("ALTER TABLE user ADD COLUMN must_change_password BOOLEAN DEFAULT 0")
            if 'reset_token' not in columns:
                cursor.execute("ALTER TABLE user ADD COLUMN reset_token VARCHAR(500)")
            if 'reset_token_expires_at' not in columns:
                cursor.execute("ALTER TABLE user ADD COLUMN reset_token_expires_at DATETIME")
            conn.commit()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='incident'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(incident)")
            columns = [row[1] for row in cursor.fetchall()]
            if 'location' not in columns:
                cursor.execute("ALTER TABLE incident ADD COLUMN location VARCHAR(255)")
            if 'citizen_report_id' not in columns:
                cursor.execute("ALTER TABLE incident ADD COLUMN citizen_report_id INTEGER")
            if 'external_event_id' not in columns:
                cursor.execute("ALTER TABLE incident ADD COLUMN external_event_id VARCHAR(120)")
            if 'event_time' not in columns:
                cursor.execute("ALTER TABLE incident ADD COLUMN event_time DATETIME")
            conn.commit()


def migrate_incident_commander_tables():
    # Same reasoning as migrate_user_table(): raw sqlite3 only, skip on Postgres.
    if not app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite:'):
        return
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
            CREATE TABLE IF NOT EXISTS incident_message (
                id INTEGER PRIMARY KEY,
                incident_response_id INTEGER NOT NULL,
                reporter_id INTEGER NOT NULL,
                title VARCHAR(200) NOT NULL,
                content TEXT NOT NULL,
                report_type VARCHAR(50) DEFAULT 'UPDATE',
                source VARCHAR(20) DEFAULT 'coordinator',
                affected_areas VARCHAR(500),
                casualties INTEGER,
                evacuated INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (incident_response_id) REFERENCES incident_response(id),
                FOREIGN KEY (reporter_id) REFERENCES user(id)
            )
        """)
        if cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='situation_report'").fetchone():
            cursor.execute("SELECT COUNT(*) FROM situation_report")
            if cursor.fetchone()[0] > 0:
                cursor.execute("""
                    INSERT OR IGNORE INTO incident_message (
                        id, incident_response_id, reporter_id, title, content, report_type,
                        source, affected_areas, casualties, evacuated, created_at
                    )
                    SELECT id, incident_response_id, reporter_id, title, content, report_type,
                           'commander' AS source, affected_areas, casualties, evacuated, created_at
                    FROM situation_report
                """)
        if cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='message'").fetchone():
            cursor.execute("SELECT COUNT(*) FROM message")
            if cursor.fetchone()[0] > 0:
                cursor.execute("""
                    INSERT OR IGNORE INTO incident_message (
                        id, incident_response_id, reporter_id, title, content, report_type,
                        source, affected_areas, casualties, evacuated, created_at
                    )
                    SELECT id, incident_response_id, sender_id, title, content, report_type,
                           'coordinator' AS source, affected_areas, casualties, evacuated, created_at
                    FROM message
                """)
        conn.commit()


def migrate_external_event_id_constraint():
    """Add the external-event deduplication guarantee to existing databases."""
    if db.engine.dialect.name not in {'sqlite', 'postgresql'}:
        return

    db.session.execute(text(
        'CREATE UNIQUE INDEX IF NOT EXISTS uq_incident_external_event_id '
        'ON incident (external_event_id) '
        'WHERE external_event_id IS NOT NULL'
    ))
    db.session.commit()


def create_default_admin():
    admin_password = os.environ.get('ADMIN_PASSWORD')
    if not admin_password:
        raise RuntimeError(
            'ADMIN_PASSWORD must be set before startup. The application cannot create or update the admin account without an environment-provided password.'
        )

    # Only these two lookups identify "the" bootstrap admin by a stable,
    # unambiguous identifier. A bare role='admin' match (removed below) is
    # NOT safe to use here: if it matches more than one admin-role user,
    # .first() picks an arbitrary one; and even with exactly one match, it
    # could be an admin who was never the bootstrap account at all, or one
    # who has since legitimately changed their password through the
    # change-password flow. Silently overwriting that password back to
    # whatever ADMIN_PASSWORD currently holds (e.g. a stale value in an
    # ops .env file that was never updated after the in-app change) would
    # revert a real admin's deliberately-chosen password on every restart
    # and force them to change it again -- confusing, not a recovery.
    admin = User.query.filter_by(username='admin').first()
    if admin is None:
        admin = User.query.filter_by(email='admin@dics-ai.local').first()

    created_new = False
    if admin is None:
        admin = User(
            username='admin',
            email='admin@dics-ai.local',
            password=generate_password_hash(admin_password),
            email_verified=True,
            role='admin',
            must_change_password=True,
        )
        db.session.add(admin)
        created_new = True
    else:
        admin.role = 'admin'
        admin.email_verified = True
        if not admin.email:
            admin.email = 'admin@dics-ai.local'
        if admin.username != 'admin' and admin.username in {None, ''}:
            admin.username = 'admin'
        if not admin.password or not check_password_hash(admin.password, admin_password):
            # The env-provided password is either brand new or was just
            # reset by whoever operates the deployment -- either way, it's
            # a value known outside the account holder, so treat it the
            # same as a fresh admin: force an in-app change before it can
            # be used for anything else.
            admin.password = generate_password_hash(admin_password)
            admin.must_change_password = True

    try:
        db.session.commit()
        if created_new:
            app.logger.warning('Default admin created. Change the password immediately.')
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Unable to create default admin: {e}')
        raise


def seed_agencies():
    canonical_agencies = [
        'BFP',
        'PNP',
        'DOH',
        'DILG',
        'MDRRMO',
        'PAGASA',
        'PHIVOLCS',
        'CIVIL DEFENSE',
        'RED CROSS',
        'LOCAL GOVERNMENT',
    ]
    with app.app_context():
        for name in canonical_agencies:
            existing = Agency.query.filter_by(name=name).first()
            if existing is None:
                db.session.add(Agency(name=name))
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            app.logger.error(f'Unable to seed agencies: {exc}')


def create_tables():
    with app.app_context():
        try:
            db.create_all()
            migrate_user_table()
            migrate_incident_commander_tables()
            migrate_external_event_id_constraint()
            create_default_admin()
            seed_agencies()
            seed_geography_data()
        except Exception:
            db.session.rollback()
            raise


_init_attempted = False
_init_lock = threading.Lock()

# Arbitrary fixed key for the Postgres advisory lock used to serialize
# startup DB initialization across gunicorn worker *processes* (a Python
# Lock only protects threads within one process). On SQLite this lock is
# skipped since there's no cross-process DB to race over in the same way.
_INIT_ADVISORY_LOCK_KEY = 918_273_645


def lazy_init():
    global _init_attempted
    # Fast path: once this process has confirmed init is done, skip the lock
    # entirely on every subsequent request.
    if _init_attempted:
        return

    # Guards against multiple *threads* in this process racing in here at
    # once (e.g. gunicorn --threads > 1 with several requests arriving on
    # first boot, as with a browser loading /, /static/*.js, /static/*.css,
    # and /favicon.ico nearly simultaneously).
    with _init_lock:
        if _init_attempted:
            return

        is_postgres = app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgresql:')
        pg_lock_acquired = False
        try:
            with app.app_context():
                if is_postgres:
                    # Guards against multiple gunicorn *worker processes*
                    # racing in here at once -- a threading.Lock only
                    # protects one process, but there are several separate
                    # OS processes hitting the same Postgres database.
                    # This blocks until any other process's init finishes,
                    # so it's safe even if we end up waiting a few seconds.
                    db.session.execute(
                        text('SELECT pg_advisory_lock(:key)'),
                        {'key': _INIT_ADVISORY_LOCK_KEY},
                    )
                    pg_lock_acquired = True

                db.create_all()
                migrate_user_table()
                migrate_external_event_id_constraint()
                create_default_admin()
                seed_agencies()
                seed_geography_data()
                app.logger.info('Database initialized successfully')
                _init_attempted = True
        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Database initialization error: {e}')
            raise
        finally:
            if pg_lock_acquired:
                try:
                    db.session.execute(
                        text('SELECT pg_advisory_unlock(:key)'),
                        {'key': _INIT_ADVISORY_LOCK_KEY},
                    )
                    db.session.commit()
                except Exception:
                    db.session.rollback()


@app.before_request
def init_on_first_request():
    lazy_init()
    start_scheduler()


@app.before_request
def enforce_password_change():
    """Defense-in-depth alongside the redirect already in login(): even if
    a session ends up with must_change_password set (e.g. an admin whose
    env-provided password was just reset, mid-session), they can't
    navigate around it by requesting a different URL directly."""
    if not session.get('must_change_password'):
        return None
    if request.endpoint in {'change_password', 'logout', 'static'}:
        return None
    return redirect(url_for('change_password'))


def verify_password(user, password):
    return verify_and_migrate(
        user,
        password,
        commit=db.session.commit,
        rollback=db.session.rollback,
        log=app.logger,
    )


@app.route('/', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if 'username' in session:
        role = session.get('role')
        if role == 'incident_commander':
            return redirect(url_for('commander.incident_commander_dashboard'))
        elif role == 'agency_coordinator':
            return redirect(url_for('coordinator.coordinator_dashboard'))
        elif role == 'field_responder':
            return redirect(url_for('responder.responder_dashboard'))
        elif role == 'eoc_staff':
            return redirect(url_for('eoc.eoc_dashboard'))
        elif role == 'citizen':
            return redirect(url_for('citizen.citizen_dashboard'))
        else:
            return redirect(url_for('dashboard'))

    error = None
    if request.method == 'POST':
        try:
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '').strip()
            user = User.query.filter_by(username=username).first()
            if user and user.is_disabled:
                error = 'This account has been disabled. Contact an administrator.'
            elif user and verify_password(user, password):
                session['username'] = user.username
                session['role'] = user.role
                session['agency'] = user.agency or 'FIELD UNIT'
                session['must_change_password'] = bool(user.must_change_password)
                flash('Welcome back, ' + user.username + '!', 'success')
                if user.must_change_password:
                    return redirect(url_for('change_password'))
                if user.role == 'incident_commander':
                    return redirect(url_for('commander.incident_commander_dashboard'))
                elif user.role == 'agency_coordinator':
                    return redirect(url_for('coordinator.coordinator_dashboard'))
                elif user.role == 'field_responder':
                    return redirect(url_for('responder.responder_dashboard'))
                elif user.role == 'eoc_staff':
                    return redirect(url_for('eoc.eoc_dashboard'))
                elif user.role == 'citizen':
                    return redirect(url_for('citizen.citizen_dashboard'))
                else:
                    return redirect(url_for('dashboard'))
            else:
                error = 'Invalid username or password.'
        except Exception as e:
            app.logger.error(f'Login error for user {username}: {str(e)}', exc_info=True)
            error = 'An error occurred during login. Please try again.'
    return render_template('pages/login.html', error=error)


@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("5 per hour")
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
        elif len(password) < 8:
            error = 'Password must be at least 8 characters.'
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
                role='citizen',
            )
            db.session.add(new_user)
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                app.logger.exception('Failed to create account')
                flash('Unable to create account. Please try again.', 'error')
                return render_template('pages/register.html', error=None)
            flash('Registration successful! You can now log in.', 'success')
            return redirect(url_for('login'))
    return render_template('pages/register.html', error=error)


def send_password_reset_email(user, token):
    reset_url = url_for('reset_password', token=token, _external=True)
    subject = 'Reset your password | DICS AI'
    body = (
        'You requested a password reset for your DICS AI account.\n\n'
        f'Use this link to reset your password: {reset_url}\n\n'
        'This link will expire in 1 hour. If you did not request this reset, '
        'you can safely ignore this email.'
    )
    message = Message(subject=subject, recipients=[user.email], body=body, sender=app.config['MAIL_DEFAULT_SENDER'])
    try:
        mail.send(message)
        app.logger.info('Password reset email sent to %s', user.email)
        return True
    except Exception:
        app.logger.exception('Failed to send password reset email to %s', user.email)
        return False


@app.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def forgot_password():
    if 'username' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = User.query.filter_by(email=email).first() if email else None
        if user:
            token = secrets.token_urlsafe(32)
            user.reset_token = token
            user.reset_token_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
            try:
                db.session.commit()
                send_password_reset_email(user, token)
            except Exception as exc:
                db.session.rollback()
                app.logger.exception('Failed to create password reset token')
        flash('If an account exists for that email, a reset link has been sent.', 'success')
        return redirect(url_for('login'))

    return render_template('pages/forgot_password.html')


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def reset_password(token):
    user = User.query.filter_by(reset_token=token).first()
    if not user or not user.reset_token_expires_at or user.reset_token_expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
        flash('This password reset link is invalid or has expired.', 'error')
        return redirect(url_for('login'))

    error = None
    if request.method == 'POST':
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')
        if len(new_password) < 8:
            error = 'New password must be at least 8 characters.'
        elif new_password != confirm_password:
            error = 'New password and confirmation do not match.'
        else:
            user.password = generate_password_hash(new_password)
            user.reset_token = None
            user.reset_token_expires_at = None
            user.must_change_password = False
            try:
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                app.logger.exception('Failed to update password from reset link')
                flash('Unable to update password. Please try again.', 'error')
                return render_template('pages/reset_password.html', token=token, error=None)
            flash('Password updated successfully. Please sign in with your new password.', 'success')
            return redirect(url_for('login'))

    return render_template('pages/reset_password.html', token=token, error=error)


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


@app.route('/change-password', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def change_password():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))

    forced = bool(session.get('must_change_password'))
    error = None
    if request.method == 'POST':
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not verify_password(user, current_password):
            error = 'Current password is incorrect.'
        elif len(new_password) < 8:
            error = 'New password must be at least 8 characters.'
        elif new_password != confirm_password:
            error = 'New password and confirmation do not match.'
        elif new_password == current_password:
            error = 'New password must be different from your current password.'
        else:
            user.password = generate_password_hash(new_password)
            user.must_change_password = False
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                app.logger.exception('Failed to update password')
                flash('Unable to update password. Please try again.', 'error')
                return render_template('pages/change_password.html', error=None, forced=forced)
            session['must_change_password'] = False
            flash('Password updated successfully.', 'success')
            return redirect(url_for('dashboard'))

    return render_template('pages/change_password.html', error=error, forced=forced)


@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))

    if user.role == 'incident_commander':
        return redirect(url_for('commander.incident_commander_dashboard'))
    elif user.role == 'field_responder':
        return redirect(url_for('responder.responder_dashboard'))
    elif user.role == 'eoc_staff':
        return redirect(url_for('eoc.eoc_dashboard'))
    elif user.role == 'citizen':
        return redirect(url_for('citizen.citizen_dashboard'))
    elif user.role == 'agency_coordinator':
        return redirect(url_for('coordinator.coordinator_dashboard'))

    # Only admins reach this point; every other role is redirected above.
    # Administration-focused view: account health and backup status only.
    # Incident verification, commander assignment, and operations monitoring
    # are owned by EOC staff; hazard/incident field operations live with
    # EOC staff, incident commander, and coordinator roles.
    total_users = User.query.count()
    disabled_users = User.query.filter_by(is_disabled=True).count()
    active_users = total_users - disabled_users

    role_counts = {}
    for role in ['admin', 'agency_coordinator', 'incident_commander', 'eoc_staff', 'field_responder', 'citizen']:
        role_counts[role] = User.query.filter_by(role=role).count()

    recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()

    backup_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'instance')
    backup_files = sorted(
        glob.glob(os.path.join(backup_dir, 'dics_ai_backup_*.db'))
        + glob.glob(os.path.join(backup_dir, 'dics_ai_backup_*.dump')),
        reverse=True,
    )
    last_backup_time = None
    if backup_files:
        last_backup_time = datetime.fromtimestamp(os.path.getmtime(backup_files[0]))

    return render_template(
        'pages/dashboard.html',
        username=user.username,
        user_role=user.role,
        total_users=total_users,
        active_users=active_users,
        disabled_users=disabled_users,
        role_counts=role_counts,
        recent_users=recent_users,
        last_backup_time=last_backup_time,
        backup_count=len(backup_files),
        weather_data=None,
        earthquake_data=None,
    )


@app.route('/api/map-pins')
def get_map_pins():
    if 'username' not in session:
        return {'error': 'Unauthorized'}, 401

    incidents = Incident.query.order_by(Incident.created_at.desc()).all()
    pins = []

    for incident in incidents:
        is_active = incident.alert or incident.status in {'ACTIVE', 'NEW', 'MONITORING', 'VERIFIED', 'PENDING'}
        if not is_active:
            continue

        report = incident.citizen_report
        if report is None:
            report = CitizenReport.query.filter(
                CitizenReport.user_id == incident.user_id,
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
    extension = os.path.splitext(filename)[1].lower()
    response = send_from_directory(
        upload_dir,
        filename,
        as_attachment=extension not in {'.jpg', '.jpeg', '.png', '.webp'},
        download_name=os.path.basename(filename),
    )
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.route('/api/realtime-data')
def get_realtime_data():
    if 'username' not in session:
        return {'error': 'Unauthorized'}, 401
    weather_data = get_all_weather_data()
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
    user = User.query.filter_by(username=session['username']).first()
    if not permission_service.can_view_analytics(user):
        return {'error': 'Forbidden'}, 403

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
    if 'username' not in session:
        return {'error': 'Unauthorized'}, 401
    if not os.getenv('OPENWEATHER_API_KEY'):
        return {'error': 'OPENWEATHER_API_KEY is not configured.'}

    city = request.args.get('city', 'Cavite')
    weather_data = get_weather_data(city)
    if not weather_data:
        return {'error': f'Could not fetch weather data for {city}.'}, 404

    rainfall = float(weather_data.get('rainfall', 0) or 0)
    river_level = round(min(15.0, max(0.0, rainfall / 10.0)), 2)
    humidity_pct = float(weather_data.get('humidity', 0) or 0)
    population_density = 1200
    prediction = predict_hazard(
        hazard_type='flood',
        rainfall_mm=rainfall,
        river_level_m=river_level,
        humidity_pct=humidity_pct,
        population_density=population_density,
    )
    return prediction


@app.route('/analytics')
def analytics():
    if 'username' not in session:
        return redirect(url_for('login'))

    if not permission_service.can_view_analytics(User.query.filter_by(username=session['username']).first()):
        flash('You do not have permission to access analytics. Only admins, coordinators, commanders, and EOC staff can view system analytics.', 'danger')
        return redirect(url_for('dashboard'))

    total_incidents = db.session.query(Incident).count()
    avg_score = db.session.query(db.func.avg(Incident.score)).scalar() or 0
    active_responses = db.session.query(IncidentResponse).filter(IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])).count()
    active_alerts = db.session.query(Incident).filter(Incident.alert.is_(True)).count()
    hazard_rows = db.session.query(Incident.hazard_type, db.func.count(Incident.id)).group_by(Incident.hazard_type).order_by(db.func.count(Incident.id).desc()).all()
    hazard_labels = [row[0] for row in hazard_rows]
    hazard_counts = [row[1] for row in hazard_rows]
    post_incident_reports = db.session.query(PostIncidentReport).join(IncidentResponse).order_by(PostIncidentReport.created_at.desc()).all()
    average_rating = db.session.query(db.func.avg(PostIncidentReport.response_rating)).scalar() or 0
    return render_template('pages/analytics.html', total_incidents=total_incidents, avg_score=avg_score, active_responses=active_responses, active_alerts=active_alerts, hazard_labels=hazard_labels, hazard_counts=hazard_counts, post_incident_reports=post_incident_reports, average_rating=average_rating)


@app.route('/hazard-map')
def hazard_map():
    if 'username' not in session:
        return redirect(url_for('login'))

    if not permission_service.has_any_role('ADMIN', 'COORDINATOR', 'COMMANDER', 'EOC', 'RESPONDER', 'CITIZEN'):
        flash('You do not have permission to view the hazard map.', 'danger')
        return redirect(url_for('dashboard'))

    return render_template('pages/hazard_map.html', sidebar_variant='hazard')


@app.route('/ics')
def ics_page():
    if 'username' not in session:
        return redirect(url_for('login'))

    if not permission_service.has_any_role('ADMIN', 'COMMANDER'):
        flash('You do not have permission to access the Incident Command System. Only admins and incident commanders can view this.', 'danger')
        return redirect(url_for('dashboard'))

    return render_template('pages/ics.html')


@app.route('/protocols')
def protocols():
    if 'username' not in session:
        return redirect(url_for('login'))

    if not permission_service.has_any_role('ADMIN', 'COMMANDER'):
        flash('You do not have permission to access ICS protocols. Only admins and incident commanders can view this.', 'danger')
        return redirect(url_for('dashboard'))

    return render_template('pages/protocols.html')


if __name__ == '__main__':
    create_tables()
    app.run(debug=os.environ.get('FLASK_DEBUG', '0') == '1', use_reloader=False, host='127.0.0.1', port=5000)
