"""File upload service for handling images with resizing and storage abstraction."""

import io
import os
import uuid
import aiofiles
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Protocol

from fastapi import HTTPException, UploadFile, status
from PIL import Image

# Allowed image types
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}

# Allowed video types
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".avi", ".mkv"}
ALLOWED_VIDEO_CONTENT_TYPES = {"video/mp4", "video/webm", "video/quicktime", "video/x-msvideo", "video/x-matroska"}

# Combined allowed types (for backward compatibility)
ALLOWED_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | ALLOWED_VIDEO_EXTENSIONS
ALLOWED_CONTENT_TYPES = ALLOWED_IMAGE_CONTENT_TYPES | ALLOWED_VIDEO_CONTENT_TYPES

# Maximum file sizes
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB for images
MAX_VIDEO_FILE_SIZE = 50 * 1024 * 1024  # 50MB for videos

# Upload directory (relative to backend root)
UPLOAD_DIR = Path("uploads")


# =============================================================================
# Category Dimensions Configuration (Mobile-First)
# =============================================================================

@dataclass
class ImageDimensions:
    """Image dimension configuration for a category."""
    max_width: int
    max_height: int
    quality: int = 85  # WebP quality (1-100)


CATEGORY_DIMENSIONS: dict[str, ImageDimensions] = {
    "profiles": ImageDimensions(max_width=400, max_height=400, quality=85),
    "clubs": ImageDimensions(max_width=800, max_height=400, quality=85),
    "sponsors": ImageDimensions(max_width=600, max_height=300, quality=85),
    "events": ImageDimensions(max_width=1200, max_height=630, quality=85),
    "fish": ImageDimensions(max_width=600, max_height=400, quality=85),
    "general": ImageDimensions(max_width=1024, max_height=1024, quality=80),
}


# =============================================================================
# Image Processor
# =============================================================================

class ImageProcessor:
    """Process and optimize images for web delivery."""

    @staticmethod
    def resize_image(
        contents: bytes,
        category: str,
        output_format: str = "WEBP",
    ) -> tuple[bytes, str]:
        """
        Resize and optimize an image based on category.

        Args:
            contents: Raw image bytes
            category: Upload category (determines max dimensions)
            output_format: Output format (default WEBP for optimization)

        Returns:
            Tuple of (processed_bytes, file_extension)
        """
        # Get dimensions for category (fallback to general)
        dims = CATEGORY_DIMENSIONS.get(category, CATEGORY_DIMENSIONS["general"])

        # Open image
        img = Image.open(io.BytesIO(contents))

        # Convert to RGB if necessary (for WebP compatibility)
        if img.mode in ("RGBA", "P"):
            # Create white background for transparency
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Resize using high-quality resampling (preserves aspect ratio)
        img.thumbnail((dims.max_width, dims.max_height), Image.Resampling.LANCZOS)

        # Save to buffer
        output = io.BytesIO()
        img.save(output, format=output_format, quality=dims.quality, optimize=True)

        # Determine extension
        ext = ".webp" if output_format == "WEBP" else f".{output_format.lower()}"

        return output.getvalue(), ext


# =============================================================================
# Storage Backend Abstraction
# =============================================================================

class StorageBackend(Protocol):
    """Protocol for storage backends (local, S3, DO Spaces, etc.)."""

    async def save(self, filename: str, contents: bytes) -> str:
        """Save file and return URL path."""
        ...

    async def delete(self, url_path: str) -> bool:
        """Delete file by URL path. Returns True if deleted."""
        ...


class LocalStorageBackend:
    """Local filesystem storage backend."""

    def __init__(self, base_dir: Path = UPLOAD_DIR, url_prefix: str = "/uploads"):
        self.base_dir = base_dir
        self.url_prefix = url_prefix

    def _get_file_path(self, filename: str) -> Path:
        """Get full file path, creating directories as needed."""
        file_path = self.base_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        return file_path

    async def save(self, filename: str, contents: bytes) -> str:
        """Save file to local filesystem."""
        file_path = self._get_file_path(filename)
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(contents)
        return f"{self.url_prefix}/{filename}"

    async def save_with_content_type(
        self,
        key: str,
        contents: bytes,
        content_type: str,
    ) -> str:
        """Save file to local filesystem (content_type ignored for local storage)."""
        file_path = self._get_file_path(key)
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(contents)
        return f"{self.url_prefix}/{key}"

    async def save_file_with_content_type(
        self,
        key: str,
        file_path: str,
        content_type: str,
    ) -> str:
        """Copy file to local storage (content_type ignored for local storage)."""
        import shutil
        dest_path = self._get_file_path(key)
        shutil.copy2(file_path, dest_path)
        return f"{self.url_prefix}/{key}"

    async def delete(self, url_path: str) -> bool:
        """Delete file from local filesystem."""
        if not url_path or not url_path.startswith(self.url_prefix):
            return False

        filename = url_path.replace(f"{self.url_prefix}/", "")
        file_path = self.base_dir / filename

        if file_path.exists():
            os.remove(file_path)
            return True
        return False


class DOSpacesBackend:
    """
    Digital Ocean Spaces storage backend (S3-compatible).

    Configuration via environment variables:
    - DO_SPACES_KEY: Access key ID
    - DO_SPACES_SECRET: Secret access key
    - DO_SPACES_BUCKET: Bucket name
    - DO_SPACES_REGION: Region (e.g., fra1, nyc3)
    - DO_SPACES_ENDPOINT: Endpoint URL (e.g., https://fra1.digitaloceanspaces.com)
    """

    def __init__(self):
        import boto3
        from botocore.config import Config

        self.key = os.environ.get("DO_SPACES_KEY")
        self.secret = os.environ.get("DO_SPACES_SECRET")
        self.bucket = os.environ.get("DO_SPACES_BUCKET")
        self.region = os.environ.get("DO_SPACES_REGION", "fra1")
        self.endpoint = os.environ.get("DO_SPACES_ENDPOINT", f"https://{self.region}.digitaloceanspaces.com")

        if not all([self.key, self.secret, self.bucket]):
            raise ValueError(
                "DO Spaces configuration incomplete. Required: "
                "DO_SPACES_KEY, DO_SPACES_SECRET, DO_SPACES_BUCKET"
            )

        # Create S3 client for DO Spaces
        self.client = boto3.client(
            "s3",
            region_name=self.region,
            endpoint_url=self.endpoint,
            aws_access_key_id=self.key,
            aws_secret_access_key=self.secret,
            config=Config(signature_version="s3v4"),
        )

        # Public URL base (CDN or direct)
        self.public_url_base = os.environ.get("STORAGE_PUBLIC_URL_BASE", f"https://{self.bucket}.{self.region}.digitaloceanspaces.com")

    async def save(self, filename: str, contents: bytes) -> str:
        """Save file to DO Spaces and return public URL."""
        import asyncio

        # Upload to S3 (boto3 is sync, run in executor)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self.client.put_object(
                Bucket=self.bucket,
                Key=f"uploads/{filename}",
                Body=contents,
                ContentType="image/webp",
                ACL="public-read",
            )
        )

        # Return public URL
        return f"{self.public_url_base}/uploads/{filename}"

    async def save_with_content_type(
        self,
        key: str,
        contents: bytes,
        content_type: str,
    ) -> str:
        """
        Save file to DO Spaces with specific content type and return public URL.

        Args:
            key: Storage key (path within bucket)
            contents: File contents
            content_type: MIME type (e.g., "image/webp", "video/mp4")

        Returns:
            Public URL to the uploaded file
        """
        import asyncio

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self.client.put_object(
                Bucket=self.bucket,
                Key=f"uploads/{key}",
                Body=contents,
                ContentType=content_type,
                ACL="public-read",
            )
        )

        return f"{self.public_url_base}/uploads/{key}"

    async def save_file_with_content_type(
        self,
        key: str,
        file_path: str,
        content_type: str,
    ) -> str:
        """
        Upload a file from disk to DO Spaces with specific content type.

        Args:
            key: Storage key (path within bucket)
            file_path: Local file path to upload
            content_type: MIME type (e.g., "image/webp", "video/mp4")

        Returns:
            Public URL to the uploaded file
        """
        import asyncio

        def _upload():
            with open(file_path, 'rb') as f:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=f"uploads/{key}",
                    Body=f,
                    ContentType=content_type,
                    ACL="public-read",
                )

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _upload)

        return f"{self.public_url_base}/uploads/{key}"

    async def delete(self, url_path: str) -> bool:
        """Delete file from DO Spaces."""
        import asyncio

        # Extract key from URL
        if self.public_url_base in url_path:
            key = url_path.replace(f"{self.public_url_base}/", "")
        elif url_path.startswith("/uploads/"):
            key = url_path.lstrip("/")
        else:
            return False

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.client.delete_object(Bucket=self.bucket, Key=key)
            )
            return True
        except Exception:
            return False


# =============================================================================
# Storage Backend Instance (configurable)
# =============================================================================

def get_storage_backend() -> StorageBackend:
    """
    Get the configured storage backend based on STORAGE_BACKEND env var.

    Options:
    - 'local' or empty: LocalStorageBackend (default)
    - 'do_spaces': DOSpacesBackend (DigitalOcean Spaces)
    """
    backend_type = os.environ.get("STORAGE_BACKEND", "local").lower()

    if backend_type == "do_spaces":
        return DOSpacesBackend()
    else:
        return LocalStorageBackend()


# Global storage backend instance
_storage_backend: Optional[StorageBackend] = None


def get_storage() -> StorageBackend:
    """Get or create the storage backend singleton."""
    global _storage_backend
    if _storage_backend is None:
        _storage_backend = get_storage_backend()
    return _storage_backend


# =============================================================================
# Public API (backward compatible)
# =============================================================================

def get_upload_dir() -> Path:
    """Get the upload directory, creating it if needed."""
    upload_path = UPLOAD_DIR
    upload_path.mkdir(parents=True, exist_ok=True)
    return upload_path


def validate_image_file(file: UploadFile) -> None:
    """Validate an uploaded image file."""
    if file.content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Please upload a JPG, PNG, or WebP image. Got: {file.content_type}",
        )

    if file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid file extension. Allowed: {', '.join(ALLOWED_IMAGE_EXTENSIONS)}",
            )


def validate_media_file(file: UploadFile) -> bool:
    """
    Validate an uploaded media file (image or video).

    Returns:
        True if the file is a video, False if it's an image
    """
    is_video = file.content_type in ALLOWED_VIDEO_CONTENT_TYPES
    is_image = file.content_type in ALLOWED_IMAGE_CONTENT_TYPES

    if not is_video and not is_image:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Please upload an image (JPG, PNG, WebP) or video (MP4, WebM, MOV). Got: {file.content_type}",
        )

    if file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid file extension. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
            )

    return is_video


async def validate_file_size(file: UploadFile, max_size: int = MAX_FILE_SIZE, file_type: str = "Image") -> bytes:
    """Validate file size and return contents."""
    contents = await file.read()

    if len(contents) > max_size:
        size_mb = len(contents) / (1024 * 1024)
        max_mb = max_size / (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{file_type} must be under {max_mb:.0f}MB. Your file is {size_mb:.1f}MB.",
        )

    return contents


def generate_filename(
    original_filename: str,
    category: str = "general",
    ext: str = ".webp",
    entity_id: Optional[int] = None,
) -> str:
    """
    Generate a unique filename with the given extension.

    Args:
        original_filename: Original uploaded filename
        category: Category subdirectory (profiles, clubs, sponsors, etc.)
        ext: File extension
        entity_id: Optional entity ID (user_id, club_id, sponsor_id) for path organization

    Returns:
        Path like "category/entity_id/timestamp_uuid.ext" or "category/timestamp_uuid.ext"
    """
    timestamp = datetime.utcnow().strftime("%Y%m%d")
    unique_id = uuid.uuid4().hex[:8]

    if entity_id is not None:
        return f"{category}/{entity_id}/{timestamp}_{unique_id}{ext}"
    return f"{category}/{timestamp}_{unique_id}{ext}"


def generate_hash_based_key(
    event_id: int,
    user_id: int,
    sha256_hash: str,
    extension: str,
    media_type: str = "images"
) -> str:
    """
    Generate a deterministic storage key based on SHA-256 hash.

    Format: events/{event_id}/{user_id}/{media_type}/{sha256}.{ext}

    Args:
        event_id: The event ID
        user_id: The user ID (the angler who caught the fish)
        sha256_hash: The SHA-256 hash of the original file
        extension: File extension (e.g., ".webp", ".mp4")
        media_type: "images" or "videos"

    Returns:
        Storage key path
    """
    ext = extension.lstrip(".")
    return f"events/{event_id}/{user_id}/{media_type}/{sha256_hash}.{ext}"


def generate_poster_key(event_id: int, user_id: int, sha256_hash: str) -> str:
    """
    Generate storage key for video poster frame.

    Format: events/{event_id}/{user_id}/images/{sha256}_poster.jpg

    Args:
        event_id: The event ID
        user_id: The user ID (the angler who caught the fish)
        sha256_hash: The SHA-256 hash of the original video

    Returns:
        Storage key path
    """
    return f"events/{event_id}/{user_id}/images/{sha256_hash}_poster.jpg"


async def save_upload(
    file: UploadFile,
    category: str = "general",
    entity_id: Optional[int] = None,
) -> str:
    """
    Save an uploaded file with automatic resizing and optimization.

    Args:
        file: The uploaded file
        category: Category subdirectory (profiles, sponsors, clubs, events, fish, general)
        entity_id: Optional entity ID for path organization (user_id, club_id, sponsor_id, etc.)

    Returns:
        The URL path to access the file (e.g., /uploads/clubs/123/20251218_abc123.webp)
    """
    # Validate file
    validate_image_file(file)
    contents = await validate_file_size(file)

    # Process image (resize and convert to WebP)
    processed_contents, ext = ImageProcessor.resize_image(contents, category)

    # Generate unique filename with optional entity_id
    filename = generate_filename(file.filename or "image.jpg", category, ext, entity_id)

    # Save using storage backend
    storage = get_storage()
    url = await storage.save(filename, processed_contents)

    return url


async def save_media_upload(
    file: UploadFile,
    category: str = "general",
    entity_id: Optional[int] = None,
) -> str:
    """
    Save an uploaded media file (image or video).

    Images are resized and converted to WebP.
    Videos are saved as-is with original format.

    Args:
        file: The uploaded file
        category: Category subdirectory (profiles, sponsors, clubs, catches, events, etc.)
        entity_id: Optional entity ID for path organization (user_id, club_id, sponsor_id, etc.)

    Returns:
        The URL path to access the file
    """
    # Validate file type (returns True if video)
    is_video = validate_media_file(file)

    if is_video:
        # For videos, validate size and save directly
        contents = await validate_file_size(file, MAX_VIDEO_FILE_SIZE, "Video")

        # Keep original extension for videos
        original_ext = Path(file.filename or "video.mp4").suffix.lower()
        filename = generate_filename(file.filename or "video.mp4", category, original_ext, entity_id)

        # Save using storage backend
        storage = get_storage()
        url = await storage.save(filename, contents)

        return url
    else:
        # For images, use the existing image processing pipeline
        contents = await validate_file_size(file, MAX_FILE_SIZE, "Image")

        # Process image (resize and convert to WebP)
        processed_contents, ext = ImageProcessor.resize_image(contents, category)

        # Generate unique filename with optional entity_id
        filename = generate_filename(file.filename or "image.jpg", category, ext, entity_id)

        # Save using storage backend
        storage = get_storage()
        url = await storage.save(filename, processed_contents)

        return url


async def delete_upload(url_path: str) -> bool:
    """
    Delete an uploaded file by its URL path.

    Args:
        url_path: The URL path (e.g., /uploads/fish/20251216_abc123.webp)

    Returns:
        True if deleted, False if file didn't exist
    """
    storage = get_storage()
    return await storage.delete(url_path)
