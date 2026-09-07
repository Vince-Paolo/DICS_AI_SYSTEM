import unittest
from unittest.mock import MagicMock, patch

from app import app
from services.file_storage import FileStorage


def _base_config(tmp_path, backend='local'):
    return {
        'FILE_STORAGE_BACKEND': backend,
        'UPLOAD_FOLDER': str(tmp_path),
        'FILE_STORAGE_BUCKET': 'test-bucket',
        'FILE_STORAGE_PREFIX': 'uploads',
        'FILE_STORAGE_REGION': 'us-east-1',
        'FILE_STORAGE_ENDPOINT_URL': '',
        'FILE_STORAGE_ACCESS_KEY_ID': '',
        'FILE_STORAGE_SECRET_ACCESS_KEY': '',
    }


class FakeApp:
    """Minimal stand-in so FileStorage doesn't need a real Flask app.config."""
    def __init__(self, config):
        self.config = config


class LocalFileStorageTestCase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.mkdtemp()

    def test_save_then_read_roundtrip(self):
        storage = FileStorage(FakeApp(_base_config(self.tmp_dir)))
        storage.save('photo.jpg', b'fake-image-bytes')
        self.assertEqual(storage.read('photo.jpg'), b'fake-image-bytes')

    def test_read_missing_file_returns_none(self):
        storage = FileStorage(FakeApp(_base_config(self.tmp_dir)))
        self.assertIsNone(storage.read('does-not-exist.jpg'))


class S3FileStorageTestCase(unittest.TestCase):
    """S3 backend is mocked rather than hitting real AWS -- these test that
    FileStorage calls boto3 correctly, not that S3 itself works."""

    def test_requires_bucket_when_backend_is_s3(self):
        config = _base_config('/tmp', backend='s3')
        config['FILE_STORAGE_BUCKET'] = ''
        with self.assertRaises(RuntimeError):
            FileStorage(FakeApp(config))

    @patch('services.file_storage.boto3.client')
    def test_save_uploads_to_configured_bucket_and_key(self, mock_boto_client):
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client

        storage = FileStorage(FakeApp(_base_config('/tmp', backend='s3')))
        storage.save('photo.jpg', b'fake-image-bytes')

        mock_client.upload_fileobj.assert_called_once()
        args, kwargs = mock_client.upload_fileobj.call_args
        self.assertEqual(args[1], 'test-bucket')
        self.assertEqual(args[2], 'uploads/photo.jpg')
        self.assertEqual(kwargs['ExtraArgs']['ContentType'], 'image/jpeg')

    @patch('services.file_storage.boto3.client')
    def test_read_returns_none_when_key_missing(self, mock_boto_client):
        mock_client = MagicMock()
        mock_client.exceptions.NoSuchKey = type('NoSuchKey', (Exception,), {})
        mock_client.get_object.side_effect = mock_client.exceptions.NoSuchKey()
        mock_boto_client.return_value = mock_client

        storage = FileStorage(FakeApp(_base_config('/tmp', backend='s3')))
        self.assertIsNone(storage.read('missing.jpg'))


if __name__ == '__main__':
    unittest.main()
