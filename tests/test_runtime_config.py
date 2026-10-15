import importlib.util
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / 'app.py'


def _load_app_module():
    spec = importlib.util.spec_from_file_location('app_module_for_config_test', APP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_dotenv_file_sets_environment(monkeypatch, tmp_path):
    env_file = tmp_path / '.env'
    env_file.write_text('FLASK_DEBUG=0\nSECRET_KEY=runtime-secret\n', encoding='utf-8')

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('FLASK_DEBUG', raising=False)
    monkeypatch.setenv('SECRET_KEY', 'test-secret-key')

    module = _load_app_module()

    monkeypatch.delenv('SECRET_KEY', raising=False)

    assert hasattr(module, '_load_dotenv_file')
    module._load_dotenv_file(env_file)
    assert os.environ['FLASK_DEBUG'] == '0'
    assert os.environ['SECRET_KEY'] == 'runtime-secret'


def test_start_scheduler_is_safe_for_concurrent_first_requests():
    module = _load_app_module()
    module._scheduler_started = False
    ready = threading.Barrier(3)

    def start_from_request():
        ready.wait()
        module.start_scheduler()

    def add_job(*args, **kwargs):
        time.sleep(0.05)

    with patch.object(module.scheduler, 'add_job', side_effect=add_job) as add_job_mock, \
         patch.object(module.scheduler, 'start') as start_mock:
        with ThreadPoolExecutor(max_workers=2) as pool:
            requests = [pool.submit(start_from_request) for _ in range(2)]
            ready.wait()
            for request in requests:
                request.result()

    assert add_job_mock.call_count == 1
    assert add_job_mock.call_args.kwargs['replace_existing'] is True
    start_mock.assert_called_once()
