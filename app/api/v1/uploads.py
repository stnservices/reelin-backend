"""File upload endpoints."""

from enum import Enum
from typing import Optional

from fastapi import APIRouter, Depends, File, UploadFile, Query
from pydantic import BaseModel

from app.core.permissions import get_current_user
from app.models.user import UserAccount
from app.services.uploads import save_upload, delete_upload

router = APIRouter()


class UploadCategory(str, Enum):
    """Upload category for organizing files."""

    FISH = "fish"
    SPONSORS = "sponsors"
    EVENTS = "events"
    CLUBS = "clubs"
    PROFILES = "profiles"
    GENERAL = "general"


class UploadResponse(BaseModel):
    """Response schema for file upload."""

    url: str
    filename: str
    message: str


@router.post("", response_model=UploadResponse)
async def upload_image(
    file: UploadFile = File(...),
    category: UploadCategory = Query(default=UploadCategory.GENERAL),
    entity_id: Optional[int] = Query(
        default=None,
        description="Entity ID for path organization (club_id, sponsor_id, event_id). For profiles, uses current user ID automatically.",
    ),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Upload an image file.

    - **file**: Image file (JPG, PNG, or WebP, max 5MB)
    - **category**: Category for organizing uploads (profiles, clubs, sponsors, events, fish, general)
    - **entity_id**: Optional entity ID for path organization. For profiles category, automatically uses current user ID.

    Returns the URL path to access the uploaded file.
    File paths follow the pattern: {category}/{entity_id}/{timestamp}_{uuid}.webp
    """
    # For profiles, always use current user's ID
    if category == UploadCategory.PROFILES:
        resolved_entity_id = current_user.id
    else:
        resolved_entity_id = entity_id

    url = await save_upload(file, category.value, resolved_entity_id)

    return UploadResponse(
        url=url,
        filename=file.filename or "unknown",
        message="Image uploaded successfully",
    )


@router.delete("")
async def remove_upload(
    url: str = Query(..., description="The URL path of the file to delete"),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Delete an uploaded file.

    - **url**: The URL path returned from the upload endpoint

    Only admins and the file owner can delete uploads.
    """
    deleted = await delete_upload(url)

    if deleted:
        return {"message": "File deleted successfully"}
    else:
        return {"message": "File not found or already deleted"}
