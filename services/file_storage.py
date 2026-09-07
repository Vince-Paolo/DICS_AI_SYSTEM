import io
import os

import boto3
from botocore.exceptions import ClientError


class FileStorage:
    """Store uploaded files locally or in an S3-compatible object store."""

    def __init__(self, app):
        self.backend = app.config.get('FILE_STORAGE_BACKEND', 'local').lower()
        self.upload_dir = app.config['UPLOAD_FOLDER']
        self.bucket = app.config.get('FILE_STORAGE_BUCKET')
        self.prefix = app.config.get('FILE_STORAGE_PREFIX', 'uploads').strip('/')
        self.client = None

        if self.backend == 's3':
            if not self.bucket:
                raise RuntimeError('FILE_STORAGE_BUCKET is required when FILE_STORAGE_BACKEND=s3')
            self.client = boto3.client(
                's3',
                region_name=app.config.get('FILE_STORAGE_REGION') or None,
                endpoint_url=app.config.get('FILE_STORAGE_ENDPOINT_URL') or None,
                aws_access_key_id=app.config.get('FILE_STORAGE_ACCESS_KEY_ID') or None,
                aws_secret_access_key=app.config.get('FILE_STORAGE_SECRET_ACCESS_KEY') or None,
            )

    def _key(self, filename):
        return f'{self.prefix}/{filename}' if self.prefix else filename

    def save(self, filename, content):
        if self.backend == 's3':
            self.client.upload_fileobj(
                io.BytesIO(content),
                self.bucket,
                self._key(filename),
                ExtraArgs={'ContentType': self._content_type(filename)},
            )
            return

        os.makedirs(self.upload_dir, exist_ok=True)
        with open(os.path.join(self.upload_dir, filename), 'wb') as output_file:
            output_file.write(content)

    def read(self, filename):
        if self.backend == 's3':
            try:
                response = self.client.get_object(Bucket=self.bucket, Key=self._key(filename))
            except self.client.exceptions.NoSuchKey:
                return None
            except ClientError as error:
                if error.response.get('Error', {}).get('Code') in {'404', 'NoSuchKey'}:
                    return None
                raise
            return response['Body'].read()

        path = os.path.join(self.upload_dir, filename)
        if not os.path.isfile(path):
            return None
        with open(path, 'rb') as input_file:
            return input_file.read()

    @staticmethod
    def _content_type(filename):
        return {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.webp': 'image/webp',
        }.get(os.path.splitext(filename)[1].lower(), 'application/octet-stream')