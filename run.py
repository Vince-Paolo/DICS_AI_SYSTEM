from pathlib import Path
import importlib.util


ROOT = Path(__file__).resolve().parent
APP_MODULE_PATH = ROOT / "app.py"

spec = importlib.util.spec_from_file_location("app_entry", APP_MODULE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load application entrypoint from {APP_MODULE_PATH}")

module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

app = module.app


if __name__ == "__main__":
    debug_mode = str(__import__('os').environ.get('FLASK_DEBUG', '0')).strip().lower() in {'1', 'true', 'yes', 'on'}
    app.run(
        debug=debug_mode,
        host=__import__('os').environ.get('HOST', '127.0.0.1'),
        port=int(__import__('os').environ.get('PORT', 5000))
    )
