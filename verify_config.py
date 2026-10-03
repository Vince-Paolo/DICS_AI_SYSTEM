import os
import tempfile
import importlib.util
from pathlib import Path

os.environ['FLASK_DEBUG'] = '0'
os.environ.pop('SECRET_KEY', None)
root = Path('c:/Users/Admin/OneDrive/Paolo/Projects/DICS_AI_SYSTEM-main')
spec = importlib.util.spec_from_file_location('app_module_verification', root / 'app.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.app.config['DEBUG'] is False

with tempfile.TemporaryDirectory() as td:
    env_path = Path(td) / '.env'
    env_path.write_text('FLASK_DEBUG=0\nSECRET_KEY=runtime-secret\n', encoding='utf-8')
    os.environ.pop('FLASK_DEBUG', None)
    os.environ.pop('SECRET_KEY', None)
    module._load_dotenv_file(env_path)
    assert os.environ['FLASK_DEBUG'] == '0'
    assert os.environ['SECRET_KEY'] == 'runtime-secret'

print('config_ok')
