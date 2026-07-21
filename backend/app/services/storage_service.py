"""MinIO/S3 storage service — upload, download, delete, and URL generation."""

import io
import json
import logging
import uuid
from datetime import timedelta
from typing import Optional

from minio import Minio
from minio.error import S3Error

from app.config import settings

logger = logging.getLogger(__name__)


class StorageService:
    """MinIO client wrapper for asset storage.

    Provides convenience methods for common operations with proper
    error handling and bucket auto-creation.
    """

    def __init__(self):
        self._client: Optional[Minio] = None
        self._bucket: Optional[str] = None

    @property
    def client(self) -> Minio:
        if self._client is None:
            self._client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_use_ssl,
            )
        return self._client

    @property
    def bucket(self) -> str:
        if self._bucket is None:
            self._bucket = settings.minio_bucket
        return self._bucket

    def ensure_bucket(self):
        """Create the bucket if it doesn't exist, with public read policy."""
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
                logger.info("Created MinIO bucket: %s", self.bucket)

            # Always ensure public read policy (for both new and existing buckets)
            public_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"AWS": ["*"]},
                        "Action": ["s3:GetObject"],
                        "Resource": [f"arn:aws:s3:::{self.bucket}/*"],
                    }
                ],
            }
            self.client.set_bucket_policy(self.bucket, json.dumps(public_policy))
            logger.info("Public read policy ensured on bucket: %s", self.bucket)
        except S3Error as exc:
            logger.warning("Failed to ensure bucket %s: %s", self.bucket, exc)

    def upload(self, object_name: str, file_path: str,
               content_type: str = "application/octet-stream") -> str:
        """Upload a local file to MinIO. Returns the object name."""
        self.ensure_bucket()
        self.client.fput_object(
            self.bucket, object_name, file_path, content_type,
        )
        logger.info("Uploaded file to MinIO: %s", object_name)
        return object_name

    def upload_bytes(self, object_name: str, data: bytes,
                     content_type: str = "application/octet-stream",
                     length: Optional[int] = None) -> str:
        """Upload bytes to MinIO. Returns the object name."""
        self.ensure_bucket()
        self.client.put_object(
            self.bucket,
            object_name,
            io.BytesIO(data),
            length or len(data),
            content_type,
        )
        logger.info("Uploaded %d bytes to MinIO: %s", len(data), object_name)
        return object_name

    def download(self, object_name: str, file_path: str):
        """Download a file from MinIO to local path."""
        self.client.fget_object(self.bucket, object_name, file_path)
        logger.info("Downloaded from MinIO: %s -> %s", object_name, file_path)

    def download_bytes(self, object_name: str) -> bytes:
        """Download an object from MinIO and return as bytes."""
        response = self.client.get_object(self.bucket, object_name)
        try:
            return response.read()
        finally:
            response.close()
            if hasattr(response, 'release_connection'):
                response.release_connection()

    def delete(self, object_name: str) -> bool:
        """Delete an object from MinIO. Returns True if successful."""
        try:
            self.client.remove_object(self.bucket, object_name)
            logger.info("Deleted from MinIO: %s", object_name)
            return True
        except S3Error as exc:
            logger.warning("Failed to delete %s from MinIO: %s", object_name, exc)
            return False

    def get_url(self, object_name: str) -> str:
        """Get the public URL for an object (no auth required)."""
        return f"{settings.minio_public_endpoint}/{self.bucket}/{object_name}"

    def get_presigned_url(self, object_name: str,
                          expires: timedelta = timedelta(hours=1)) -> str:
        """Get a presigned URL with temporary access."""
        try:
            url = self.client.presigned_get_object(
                self.bucket, object_name, expires=expires,
            )
            return url
        except S3Error as exc:
            logger.warning("Failed to generate presigned URL: %s", exc)
            return self.get_url(object_name)

    def exists(self, object_name: str) -> bool:
        """Check if an object exists in the bucket."""
        try:
            self.client.stat_object(self.bucket, object_name)
            return True
        except S3Error:
            return False


# Singleton
storage_service = StorageService()


def generate_object_key(tenant_id: int, filename: str, prefix: str = "assets") -> str:
    """Generate a unique object key for MinIO storage.

    Format: ``{prefix}/{tenant_id}/{uuid}.{ext}``
    """
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
    unique_id = uuid.uuid4().hex
    return f"{prefix}/{tenant_id}/{unique_id}.{ext}"
