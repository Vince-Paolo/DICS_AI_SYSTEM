import subprocess
from unittest.mock import patch

import pytest
from flask import Flask

from blueprints.admin import admin_bp


@pytest.mark.parametrize(
    ('stderr', 'expected_message'),
    [
        (
            'pg_dump: error: aborting because of server version mismatch\n'
            'pg_dump: detail: server version: 18.6; pg_dump version: 17.11',
            'pg_dump is older than the database server',
        ),
        (
            'pg_dump: error: connection to server failed: Connection refused',
            'Backup could not be created. Please try again.',
        ),
        (None, 'Backup could not be created. Please try again.'),
    ],
)
def test_backup_process_failure_logs_stderr_and_flashes_message(stderr, expected_message, caplog):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='backup-test', WTF_CSRF_ENABLED=False)
    app.register_blueprint(admin_bp)
    failure = subprocess.CalledProcessError(1, ['pg_dump'], stderr=stderr)

    with (
        patch('blueprints.admin.permission_service.is_admin', return_value=True),
        patch('blueprints.admin.verify_admin_password', return_value=True),
        patch('blueprints.admin.User') as user_model,
        patch('blueprints.admin.db') as database,
        patch('blueprints.admin._build_pg_dump_command', return_value=(['pg_dump'], {})),
        patch('blueprints.admin.subprocess.run', side_effect=failure) as run,
    ):
        user_model.query.filter_by.return_value.first.return_value.id = 1
        database.engine.dialect.name = 'postgresql'
        database.engine.url.render_as_string.return_value = 'postgresql://localhost/test'
        client = app.test_client()
        with client.session_transaction() as session:
            session['username'] = 'admin'

        response = client.post('/admin/backup', data={'password': 'password'})

        assert response.status_code == 302
        assert response.location.endswith('/admin/users')
        with client.session_transaction() as session:
            assert len(session['_flashes']) == 1
            category, message = session['_flashes'][0]
            assert category == 'error'
            assert expected_message in message
        assert 'Failed to create backup: pg_dump stderr:' in caplog.text
        if stderr:
            assert stderr in caplog.text
        run.assert_called_once_with(['pg_dump'], check=True, capture_output=True, text=True, env={})
        database.session.add.assert_not_called()
        database.session.commit.assert_not_called()
