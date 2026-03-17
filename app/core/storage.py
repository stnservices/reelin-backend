"""Digital Ocean Spaces / S3 storage service."""

import uuid
from datetime import datetime
from io import BytesIO
from typing import Optional
import mimetypes

import boto3
from botocore.exceptions import ClientError
from fastapi import UploadFile, HTTPException

from app.config import get_settings


class StorageService:
    """Service for handling file uploads to Digital Ocean Spaces (S3-compatible)."""

    def __init__(self):
        self.settings = get_settings()
        self._client = None

    @property
    def client(self):
        """Lazy initialization of S3 client."""
        if self._client is None:
            if not self.settings.storage_access_key or not self.settings.storage_secret_key:
                raise HTTPException(
                    status_code=500,
                    detail="Storage service not configured"
                )
            self._client = boto3.client(
                "s3",
                endpoint_url=self.settings.storage_endpoint_url,
                aws_access_key_id=self.settings.storage_access_key,
                aws_secret_access_key=self.settings.storage_secret_key,
                region_name=self.settings.storage_region,
            )
        return self._client

    @property
    def bucket_name(self) -> str:
        return self.settings.storage_bucket_name

    @property
    def cdn_base_url(self) -> str:
        """Get the CDN base URL for serving files."""
        import os
        custom_base = os.environ.get("STORAGE_PUBLIC_URL_BASE")
        if custom_base:
            return custom_base
        # Digital Ocean Spaces CDN format (legacy fallback)
        endpoint = self.settings.storage_endpoint_url
        if endpoint and "digitaloceanspaces.com" in endpoint:
            endpoint_host = endpoint.replace("https://", "")
            region = endpoint_host.split(".")[0]
            return f"https://{self.bucket_name}.{region}.cdn.digitaloceanspaces.com"
        return f"https://{self.bucket_name}.s3.amazonaws.com"

    def _generate_key(self, folder: str, filename: str) -> str:
        """Generate a unique key for the file."""
        # Get file extension
        ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
        # Generate unique filename with timestamp and UUID
        timestamp = datetime.utcnow().strftime("%Y%m%d")
        unique_id = str(uuid.uuid4())[:8]
        new_filename = f"{timestamp}_{unique_id}.{ext}" if ext else f"{timestamp}_{unique_id}"
        return f"{folder}/{new_filename}"

    def _get_content_type(self, filename: str) -> str:
        """Get content type from filename."""
        content_type, _ = mimetypes.guess_type(filename)
        return content_type or "application/octet-stream"

    async def upload_file(
        self,
        file: UploadFile,
        folder: str,
        allowed_types: Optional[list[str]] = None,
        max_size_mb: int = 10,
    ) -> str:
        """
        Upload a file to storage.

        Args:
            file: FastAPI UploadFile
            folder: Folder/prefix for the file (e.g., 'events', 'catches')
            allowed_types: List of allowed MIME types
            max_size_mb: Maximum file size in MB

        Returns:
            Public URL of the uploaded file
        """
        # Validate file type
        if allowed_types and file.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"File type not allowed. Allowed types: {', '.join(allowed_types)}"
            )

        # Read file content
        content = await file.read()

        # Validate file size
        if len(content) > max_size_mb * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail=f"File too large. Maximum size: {max_size_mb}MB"
            )

        # Generate key and upload
        key = self._generate_key(folder, file.filename or "file")
        content_type = file.content_type or self._get_content_type(file.filename or "")

        try:
            self.client.upload_fileobj(
                BytesIO(content),
                self.bucket_name,
                key,
                ExtraArgs={
                    "ContentType": content_type,
                    "ACL": "public-read",
                },
            )
        except ClientError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload file: {str(e)}"
            )

        # Return public URL
        return f"{self.cdn_base_url}/{key}"

    async def delete_file(self, url: str) -> bool:
        """
        Delete a file from storage by URL.

        Args:
            url: Public URL of the file

        Returns:
            True if deleted successfully
        """
        # Extract key from URL
        if not url.startswith(self.cdn_base_url):
            return False

        key = url.replace(f"{self.cdn_base_url}/", "")

        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=key)
            return True
        except ClientError:
            return False

    async def upload_event_image(self, file: UploadFile, event_id: int) -> str:
        """Upload an event image (logo/banner)."""
        return await self.upload_file(
            file=file,
            folder=f"events/{event_id}",
            allowed_types=["image/jpeg", "image/png", "image/webp", "image/gif"],
            max_size_mb=5,
        )

    async def upload_catch_photo(self, file: UploadFile, event_id: int, user_id: int) -> str:
        """Upload a catch photo or video."""
        return await self.upload_file(
            file=file,
            folder=f"catches/{event_id}/{user_id}",
            allowed_types=[
                "image/jpeg", "image/png", "image/webp",
                "video/mp4", "video/webm", "video/quicktime", "video/x-msvideo", "video/x-matroska"
            ],
            max_size_mb=50,  # Allow larger files for videos
        )

    async def upload_profile_picture(self, file: UploadFile, user_id: int) -> str:
        """Upload a user profile picture."""
        return await self.upload_file(
            file=file,
            folder=f"profiles/{user_id}",
            allowed_types=["image/jpeg", "image/png", "image/webp"],
            max_size_mb=2,
        )

    async def upload_rule_document(self, file: UploadFile, user_id: int) -> str:
        """Upload a competition rules document (PDF/DOC/DOCX)."""
        return await self.upload_file(
            file=file,
            folder=f"rules/{user_id}",
            allowed_types=[
                "application/pdf",
                "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ],
            max_size_mb=10,
        )

    async def upload_club_image(self, file: UploadFile, club_id: int) -> str:
        """Upload a club logo/image."""
        return await self.upload_file(
            file=file,
            folder=f"clubs/{club_id}",
            allowed_types=["image/jpeg", "image/png", "image/webp"],
            max_size_mb=5,
        )

    async def upload_sponsor_image(self, file: UploadFile, sponsor_id: int) -> str:
        """Upload a sponsor logo/image."""
        return await self.upload_file(
            file=file,
            folder=f"sponsors/{sponsor_id}",
            allowed_types=["image/jpeg", "image/png", "image/webp"],
            max_size_mb=5,
        )


# Singleton instance
storage_service = StorageService()
