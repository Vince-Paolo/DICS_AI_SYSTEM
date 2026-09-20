"""Shared pytest setup.

This runs before any test module in this directory is imported. Its job is
to make the test suite hermetic with respect to the developer's real .env --
ai/decision_support.py and services/realtime_data.py both load .env into
os.environ on import (using a setdefault-style loader that never overrides
an already-set key), and app.py constructs a FileStorage(app) unconditionally
at import time. Without this, a completely normal local .env -- e.g. one with
FILE_STORAGE_BACKEND=s3 and placeholder bucket/region values while S3 isn't
set up yet -- makes boto3 raise at Flask app-construction time, which fails
collection for every single test file that imports `app`, not just the ones
that touch file storage.

Individual test files already do this for DATABASE_URL/SECRET_KEY with
os.environ.setdefault(...) before `from app import app`; this centralizes
that same protection for every env var app.py reads at import time, so no
test file has to remember to repeat it, and it can't be silently skipped by
a new test file added later.
"""
import os

os.environ.setdefault('FILE_STORAGE_BACKEND', 'local')
os.environ.setdefault('FILE_STORAGE_BUCKET', '')
os.environ.setdefault('FILE_STORAGE_REGION', '')
os.environ.setdefault('FILE_STORAGE_ENDPOINT_URL', '')
os.environ.setdefault('FILE_STORAGE_ACCESS_KEY_ID', '')
os.environ.setdefault('FILE_STORAGE_SECRET_ACCESS_KEY', '')
