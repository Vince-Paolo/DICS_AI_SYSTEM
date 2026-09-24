import importlib.util
import os
from pathlib import Path


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
    monkeypatch.delenv('SECRET_KEY', raising=False)

    module = _load_app_module()

    assert hasattr(module, '_load_dotenv_file')
    module._load_dotenv_file(env_file)
    assert os.environ['FLASK_DEBUG'] == '0'
    assert os.environ['SECRET_KEY'] == 'runtime-secret'
