"""Keep tests isolated from local configuration and persistent data."""
import os

os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'test-secret-key'
os.environ.setdefault('FILE_STORAGE_BACKEND', 'local')
os.environ.setdefault('FILE_STORAGE_BUCKET', '')
os.environ.setdefault('FILE_STORAGE_REGION', '')
os.environ.setdefault('FILE_STORAGE_ENDPOINT_URL', '')
os.environ.setdefault('FILE_STORAGE_ACCESS_KEY_ID', '')
os.environ.setdefault('FILE_STORAGE_SECRET_ACCESS_KEY', '')
