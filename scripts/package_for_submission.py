"""
Build a clean distribution zip of the project, excluding everything that
should never leave this machine: the SQLite database and its backups,
citizen-submitted photos, the local virtualenv, and caches.

`.gitignore` already lists these paths, but that only protects `git push` --
it does nothing for a zip built by hand (e.g. right-click > Compress, or
`zip -r` over the whole folder), which is how `instance/database.db` and
citizen report photos have ended up inside submitted/shared archives before.

Usage:
    python scripts/package_for_submission.py [output_path]

Defaults to writing dics_submission_<timestamp>.zip in the project root.
"""

import os
import sys
import zipfile
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
PROJECT_FOLDER_NAME = os.path.basename(PROJECT_ROOT)

EXCLUDE_DIR_NAMES = {'.venv', '__pycache__', '.pytest_cache', '.git', 'instance'}
EXCLUDE_FILE_SUFFIXES = ('.pyc', '.db', '.bak')
EXCLUDE_EXACT_FILENAMES = {'.env'}  # real local secrets; .env.example is fine to ship


def _should_skip_dir(dirname):
    return dirname in EXCLUDE_DIR_NAMES


def _should_skip_file(filename):
    return filename in EXCLUDE_EXACT_FILENAMES or filename.endswith(EXCLUDE_FILE_SUFFIXES)


def build_zip(output_path):
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs if not _should_skip_dir(d)]
            for filename in files:
                if _should_skip_file(filename):
                    continue
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, PROJECT_ROOT)
                arcname = os.path.join(PROJECT_FOLDER_NAME, rel_path)
                zf.write(full_path, arcname)
    return output_path


if __name__ == '__main__':
    if len(sys.argv) > 1:
        out = sys.argv[1]
    else:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        out = os.path.join(PROJECT_ROOT, f'dics_submission_{timestamp}.zip')

    result = build_zip(out)
    print(f'Wrote {result}')
    print('Excluded: instance/ (database, backups, citizen photos), .venv/, caches, .db/.bak/.pyc files.')
