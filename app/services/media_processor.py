"""Media processing service for handling uploads with hashing, conversion, and optimization."""

import asyncio
import hashlib
import io
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import aiofiles
import ffmpeg
from fastapi import HTTPException, UploadFile, status
from PIL import Image

from app.services.uploads import (
    ALLOWED_IMAGE_CONTENT_TYPES,
    ALLOWED_VIDEO_CONTENT_TYPES,
    MAX_FILE_SIZE,
    MAX_VIDEO_FILE_SIZE,
)


@dataclass
class MediaMetadata:
    """Metadata collected during media processing."""
    sha256_hash: str
    mime_type: str
    size_bytes: int
    is_video: bool
    video_duration_seconds: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass
class ProcessedMedia:
    """Result of media processing."""
    processed_path: str
    poster_path: Optional[str]  # For videos only
    output_mime_type: str
    output_extension: str
    metadata: MediaMetadata


class MediaProcessingError(Exception):
    """Custom exception for media processing errors."""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class MediaProcessor:
    """
    Process uploaded media files with hashing, validation, and conversion.

    - Streams upload to temp file while computing SHA-256
    - Validates video duration
    - Converts images to WebP
    - Transcodes videos to MP4 (H.264/AAC)
    - Extracts video poster frames
    """

    # Processing configuration
    IMAGE_MAX_DIMENSION = 1200
    IMAGE_QUALITY = 85
    VIDEO_MAX_DURATION_SECONDS = 5
    VIDEO_MAX_WIDTH = 720
    VIDEO_FRAMERATE = 30

    @staticmethod
    async def stream_upload_with_hash(
        file: UploadFile,
        max_image_size: int = MAX_FILE_SIZE,
        max_video_size: int = MAX_VIDEO_FILE_SIZE,
    ) -> Tuple[str, MediaMetadata]:
        """
        Stream uploaded file to temp file while computing SHA-256 hash.

        Args:
            file: The uploaded file
            max_image_size: Maximum size for images in bytes
            max_video_size: Maximum size for videos in bytes

        Returns:
            Tuple of (temp_file_path, metadata)

        Raises:
            HTTPException: If file type is invalid or too large
        """
        # Determine if video or image
        is_video = file.content_type in ALLOWED_VIDEO_CONTENT_TYPES
        is_image = file.content_type in ALLOWED_IMAGE_CONTENT_TYPES

        if not is_video and not is_image:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_MEDIA_TYPE",
                    "message": f"Invalid file type: {file.content_type}. Allowed: images (JPG, PNG, WebP) or videos (MP4, WebM, MOV).",
                }
            )

        max_size = max_video_size if is_video else max_image_size

        # Create temp file with appropriate extension
        ext = Path(file.filename or "file").suffix.lower() or (".mp4" if is_video else ".jpg")
        temp_fd, temp_path = tempfile.mkstemp(suffix=ext)

        try:
            # Stream to temp file while computing hash
            sha256 = hashlib.sha256()
            total_bytes = 0
            chunk_size = 64 * 1024  # 64KB chunks

            async with aiofiles.open(temp_path, 'wb') as temp_file:
                while True:
                    chunk = await file.read(chunk_size)
                    if not chunk:
                        break

                    total_bytes += len(chunk)
                    if total_bytes > max_size:
                        os.close(temp_fd)
                        os.unlink(temp_path)
                        size_mb = max_size / (1024 * 1024)
                        media_type = "Video" if is_video else "Image"
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail={
                                "code": "FILE_TOO_LARGE",
                                "message": f"{media_type} must be under {size_mb:.0f}MB.",
                            }
                        )

                    sha256.update(chunk)
                    await temp_file.write(chunk)

            os.close(temp_fd)

            metadata = MediaMetadata(
                sha256_hash=sha256.hexdigest(),
                mime_type=file.content_type,
                size_bytes=total_bytes,
                is_video=is_video,
            )

            return temp_path, metadata

        except Exception as e:
            # Clean up on error
            try:
                os.close(temp_fd)
            except:
                pass
            try:
                os.unlink(temp_path)
            except:
                pass
            raise e

    @staticmethod
    def get_video_duration(file_path: str) -> float:
        """
        Get video duration in seconds using FFmpeg probe.

        Args:
            file_path: Path to the video file

        Returns:
            Duration in seconds
        """
        try:
            probe = ffmpeg.probe(file_path)
            duration = float(probe['format']['duration'])
            return duration
        except Exception as e:
            raise MediaProcessingError(
                code="PROBE_FAILED",
                message=f"Failed to read video metadata: {str(e)}"
            )

    @staticmethod
    def validate_video_duration(duration: float, max_duration: int = 5) -> None:
        """
        Validate that video duration is within limits.

        Args:
            duration: Video duration in seconds
            max_duration: Maximum allowed duration in seconds

        Raises:
            HTTPException: If video is too long

        Note:
            Uses 2x multiplier for safety buffer to account for:
            - Mobile app's +1 second recording limit (timing compensation)
            - Timer precision variance (up to ~0.5s before first tick)
            - Start/stop recording delays (~0.5s)
            - Video encoding/compression variations
        """
        # Use 2x multiplier for robust validation (e.g., 5s limit accepts up to 10s)
        max_duration_with_buffer = max_duration * 2
        if duration > max_duration_with_buffer:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "VIDEO_TOO_LONG",
                    "message": f"Video must be {max_duration_with_buffer} seconds or less. Your video is {duration:.1f} seconds.",
                }
            )

    @staticmethod
    def _is_image_already_optimized(input_path: str, max_dimension: int = 1200, max_size_kb: int = 500) -> bool:
        """
        Check if image is already optimized (JPEG, within size limits).

        Skip processing if:
        - Already JPEG format
        - Dimensions within max_dimension
        - File size under max_size_kb
        """
        try:
            # Check file size first (fast)
            file_size_kb = os.path.getsize(input_path) / 1024
            if file_size_kb > max_size_kb:
                return False

            # Check format and dimensions
            img = Image.open(input_path)
            width, height = img.size

            # Must be JPEG and within dimension limits
            is_jpeg = img.format in ('JPEG', 'JPG')
            within_dimensions = width <= max_dimension and height <= max_dimension

            img.close()
            return is_jpeg and within_dimensions
        except Exception:
            return False

    @staticmethod
    async def process_image(
        input_path: str,
        max_dimension: int = 1200,
        quality: int = 85,
    ) -> str:
        """
        Resize and optimize image to JPEG.

        Using JPEG instead of WebP for maximum Android compatibility.
        Some Android devices have issues decoding WebP images.

        Skips processing if image is already optimized (JPEG, ≤1200px, ≤500KB).

        Args:
            input_path: Path to the input image
            max_dimension: Maximum width or height
            quality: JPEG quality (1-100)

        Returns:
            Path to the processed JPEG image (or original if already optimized)
        """
        # Skip processing if already optimized (mobile pre-compressed)
        if MediaProcessor._is_image_already_optimized(input_path, max_dimension):
            # Just copy to new temp file with .jpg extension
            output_fd, output_path = tempfile.mkstemp(suffix=".jpg")
            os.close(output_fd)
            shutil.copy2(input_path, output_path)
            return output_path

        def _process():
            # Open and process image
            img = Image.open(input_path)
            original_img = img  # Keep reference for cleanup

            try:
                # Convert to RGB if necessary (required for JPEG)
                if img.mode in ("RGBA", "P"):
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                    img = background
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                # Resize to fit within max dimensions (preserves aspect ratio)
                img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

                # Save as JPEG for maximum compatibility
                output_fd, output_path = tempfile.mkstemp(suffix=".jpg")
                os.close(output_fd)

                img.save(output_path, format="JPEG", quality=quality, optimize=True)

                return output_path
            finally:
                # Explicitly release memory immediately
                if img is not original_img:
                    img.close()
                original_img.close()

        # Run in executor to avoid blocking
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _process)

    @staticmethod
    def _is_already_optimized(probe: dict, max_width: int = 720) -> bool:
        """Check if video is already in optimal format (H.264/AAC, reasonable size)."""
        try:
            video_stream = next(
                (s for s in probe['streams'] if s['codec_type'] == 'video'),
                None
            )
            audio_stream = next(
                (s for s in probe['streams'] if s['codec_type'] == 'audio'),
                None
            )

            if not video_stream:
                return False

            # Check video codec (h264)
            video_codec = video_stream.get('codec_name', '').lower()
            if video_codec not in ('h264', 'avc1'):
                return False

            # Check dimensions
            width = int(video_stream.get('width', 9999))
            if width > max_width:
                return False

            # Check audio codec if present (aac preferred, but accept others)
            if audio_stream:
                audio_codec = audio_stream.get('codec_name', '').lower()
                # Accept aac, mp3, or no audio
                if audio_codec not in ('aac', 'mp3', ''):
                    return False

            return True
        except Exception:
            return False

    @staticmethod
    async def process_video(
        input_path: str,
        max_width: int = 720,
        framerate: int = 30,
    ) -> ProcessedMedia:
        """
        Process video - skip transcoding if already optimized, otherwise transcode.
        Always extracts poster frame.

        Args:
            input_path: Path to the input video
            max_width: Maximum video width (height scaled proportionally)
            framerate: Output framerate

        Returns:
            ProcessedMedia with output paths and metadata
        """
        def _process():
            # Probe input to get metadata
            probe = ffmpeg.probe(input_path)
            video_stream = next(
                (s for s in probe['streams'] if s['codec_type'] == 'video'),
                None
            )

            if not video_stream:
                raise MediaProcessingError(
                    code="NO_VIDEO_STREAM",
                    message="No video stream found in file"
                )

            duration = float(probe['format']['duration'])
            width = int(video_stream['width'])
            height = int(video_stream['height'])

            # Check if video is already optimized (compressed on client)
            already_optimized = MediaProcessor._is_already_optimized(probe, max_width)

            # Create poster path (always needed)
            poster_fd, poster_path = tempfile.mkstemp(suffix=".jpg")
            os.close(poster_fd)

            try:
                if already_optimized:
                    # Skip transcoding - just copy the file and extract poster
                    output_fd, output_path = tempfile.mkstemp(suffix=".mp4")
                    os.close(output_fd)

                    # Copy file (fast, no re-encoding)
                    import shutil
                    shutil.copy2(input_path, output_path)
                else:
                    # Need to transcode
                    # Calculate output dimensions (scale down if needed, preserve aspect ratio)
                    if width > max_width:
                        scale = f"scale={max_width}:-2"  # -2 ensures height is even (required for H.264)
                    else:
                        scale = "scale=trunc(iw/2)*2:trunc(ih/2)*2"  # Just ensure even dimensions

                    output_fd, output_path = tempfile.mkstemp(suffix=".mp4")
                    os.close(output_fd)

                    # Transcode video
                    (
                        ffmpeg
                        .input(input_path)
                        .output(
                            output_path,
                            vcodec='libx264',
                            acodec='aac',
                            vf=scale,
                            r=framerate,
                            preset='fast',
                            crf=24,  # Balanced quality/size for mobile uploads
                            movflags='+faststart',  # Enable streaming
                            y=None,  # Overwrite output
                        )
                        .overwrite_output()
                        .run(capture_stdout=True, capture_stderr=True)
                    )

                # Extract poster frame at 0.5 seconds (or middle of short videos)
                poster_time = min(0.5, duration / 2)
                (
                    ffmpeg
                    .input(input_path, ss=poster_time)
                    .output(poster_path, vframes=1, q=2)
                    .overwrite_output()
                    .run(capture_stdout=True, capture_stderr=True)
                )

                # Get hash of original file
                sha256 = hashlib.sha256()
                with open(input_path, 'rb') as f:
                    for chunk in iter(lambda: f.read(65536), b''):
                        sha256.update(chunk)

                metadata = MediaMetadata(
                    sha256_hash=sha256.hexdigest(),
                    mime_type="video/mp4",
                    size_bytes=os.path.getsize(input_path),
                    is_video=True,
                    video_duration_seconds=duration,
                    width=width,
                    height=height,
                )

                return ProcessedMedia(
                    processed_path=output_path,
                    poster_path=poster_path,
                    output_mime_type="video/mp4",
                    output_extension=".mp4",
                    metadata=metadata,
                )

            except ffmpeg.Error as e:
                # Clean up on error
                try:
                    os.unlink(output_path)
                except:
                    pass
                try:
                    os.unlink(poster_path)
                except:
                    pass
                raise MediaProcessingError(
                    code="TRANSCODE_FAILED",
                    message=f"Video transcoding failed: {e.stderr.decode() if e.stderr else str(e)}"
                )

        # Run in executor to avoid blocking
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _process)

    @classmethod
    async def process_upload(
        cls,
        file: UploadFile,
        max_video_duration: int = 5,
    ) -> ProcessedMedia:
        """
        Full processing pipeline for uploaded media.

        1. Stream to temp file with hash computation
        2. Validate video duration if applicable
        3. Process/convert media (WebP for images, MP4 for videos)
        4. Extract poster frame for videos

        Args:
            file: The uploaded file
            max_video_duration: Maximum video duration in seconds

        Returns:
            ProcessedMedia with all output paths and metadata
        """
        # Stream upload with hash
        temp_path, metadata = await cls.stream_upload_with_hash(file)

        try:
            if metadata.is_video:
                # Get and validate video duration
                duration = cls.get_video_duration(temp_path)
                cls.validate_video_duration(duration, max_video_duration)

                # Update metadata with duration
                metadata.video_duration_seconds = duration

                # Process video
                result = await cls.process_video(temp_path)

                # Clean up original temp file
                os.unlink(temp_path)

                return result
            else:
                # Process image
                processed_path = await cls.process_image(temp_path)

                # Clean up original temp file
                os.unlink(temp_path)

                return ProcessedMedia(
                    processed_path=processed_path,
                    poster_path=None,
                    output_mime_type="image/jpeg",
                    output_extension=".jpg",
                    metadata=metadata,
                )

        except Exception as e:
            # Clean up on error
            try:
                os.unlink(temp_path)
            except:
                pass
            raise e

    @staticmethod
    def cleanup_temp_files(*paths: str) -> None:
        """Clean up temporary files."""
        for path in paths:
            if path:
                try:
                    os.unlink(path)
                except:
                    pass
