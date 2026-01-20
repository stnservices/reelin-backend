"""Catch submission and validation endpoints."""

import logging
from datetime import datetime, timezone, timedelta
from math import ceil

logger = logging.getLogger(__name__)

# Time window for post-event actions (revalidation)
POST_EVENT_ACTION_HOURS = 72

from fastapi import APIRouter, Depends, HTTPException, Query, status, BackgroundTasks, UploadFile, File
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserAccount
from app.models.event import Event, EventStatus, EventFishScoring
from app.models.event_validator import EventValidator
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.catch import Catch, CatchStatus, EventScoreboard, RankingMovement
from app.models.fish import Fish
from app.models.team import Team, TeamMember
from app.models.club import Club, ClubMembership, MembershipStatus
from app.schemas.catch import (
    CatchCreate,
    CatchValidation,
    CatchRevalidation,
    CatchResponse,
    CatchDetailResponse,
    CatchListResponse,
    LeaderboardResponse,
    ScoreboardEntry,
    CatchSearchItem,
    CatchSearchResponse,
)
from app.schemas.common import MessageResponse
from app.core.permissions import ValidatorOrAdmin, check_is_event_validator
from app.core.storage import storage_service
from app.api.v1.live import live_scoring_service
from app.tasks.leaderboard import queue_leaderboard_recalculation
from app.tasks.notifications import send_catch_notification, send_catch_response_notification
from app.tasks.achievements import process_achievements_for_catch
from app.tasks.ai_analysis import queue_catch_analysis
from app.models.ai_analysis import CatchAiAnalysis
from app.services.statistics_service import statistics_service

router = APIRouter()


def is_within_post_event_window(event: Event) -> bool:
    """
    Check if we're within the allowed time window for post-event actions.
    Returns True if:
    - Event is ongoing (always allowed)
    - Event is finished AND within POST_EVENT_ACTION_HOURS of end_date
    """
    if event.status == EventStatus.ONGOING.value:
        return True

    if event.status == EventStatus.COMPLETED.value:
        if not event.end_date:
            # No end date set, allow action
            return True
        now = datetime.now(timezone.utc)
        end_time = event.end_date
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        deadline = end_time + timedelta(hours=POST_EVENT_ACTION_HOURS)
        return now <= deadline

    return False


def get_post_event_deadline(event: Event) -> datetime | None:
    """Get the deadline for post-event actions."""
    if event.end_date:
        end_time = event.end_date
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        return end_time + timedelta(hours=POST_EVENT_ACTION_HOURS)
    return None


@router.get("", response_model=CatchListResponse)
async def list_catches(
    event_id: int,
    status_filter: CatchStatus | None = Query(None, alias="status"),
    user_id: int | None = Query(None),
    user_search: str | None = Query(None, description="Search by user name or email"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    List catches for an event.
    Validators see all catches.
    Regular users see only approved catches (or their own).
    Supports searching by user name or email (validators only).
    """
    # Check event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check permissions
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_validator = bool(user_roles.intersection({"administrator", "validator", "organizer"}))

    # Build base query - include AI analysis for validators
    query = (
        select(Catch)
        .options(
            selectinload(Catch.user).selectinload(UserAccount.profile),
            selectinload(Catch.fish),
            selectinload(Catch.validated_by),
            selectinload(Catch.ai_analysis).selectinload(CatchAiAnalysis.detected_species),
        )
        .where(Catch.event_id == event_id)
    )

    # Non-validators only see approved catches (or their own)
    if not is_validator:
        query = query.where(
            (Catch.status == CatchStatus.APPROVED.value) |
            (Catch.user_id == current_user.id)
        )

    # Filter by status (validators only)
    if status_filter:
        if is_validator:
            query = query.where(Catch.status == status_filter.value)
        elif status_filter != CatchStatus.APPROVED:
            # Non-validators can only filter their own non-approved catches
            query = query.where(Catch.user_id == current_user.id)

    # Filter by user ID
    if user_id:
        query = query.where(Catch.user_id == user_id)

    # Filter by user name/email search (validators only)
    # This is done in-memory after fetching due to joined table complexity
    user_search_filter = None
    if user_search and is_validator:
        user_search_filter = user_search.lower()

    # Get total count
    count_query = select(func.count(Catch.id)).where(Catch.event_id == event_id)
    if not is_validator:
        count_query = count_query.where(
            (Catch.status == CatchStatus.APPROVED.value) |
            (Catch.user_id == current_user.id)
        )
    if status_filter and is_validator:
        count_query = count_query.where(Catch.status == status_filter.value)
    if user_id:
        count_query = count_query.where(Catch.user_id == user_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Pagination - when using user_search, we need to fetch more and filter in-memory
    if user_search_filter:
        # Fetch all matching catches for in-memory filtering
        query = query.order_by(Catch.submitted_at.desc())
        result = await db.execute(query)
        all_catches = result.scalars().all()

        # Filter by user name/email
        filtered_catches = []
        for c in all_catches:
            user_name = ""
            user_email = c.user.email.lower() if c.user else ""
            if c.user and c.user.profile:
                user_name = f"{c.user.profile.first_name or ''} {c.user.profile.last_name or ''}".lower()
            if user_search_filter in user_name or user_search_filter in user_email:
                filtered_catches.append(c)

        # Apply pagination to filtered results
        total = len(filtered_catches)
        offset = (page - 1) * page_size
        catches = filtered_catches[offset : offset + page_size]
    else:
        # Standard pagination
        offset = (page - 1) * page_size
        query = query.order_by(Catch.submitted_at.desc()).offset(offset).limit(page_size)
        result = await db.execute(query)
        catches = result.scalars().all()

    # Fetch enrollment info for all users in the catch list (for validators)
    user_enrollment_map: dict[int, tuple[int | None, int | None]] = {}  # user_id -> (enrollment_id, draw_number)
    if is_validator and catches:
        user_ids = list(set(c.user_id for c in catches))
        enrollment_query = select(EventEnrollment).where(
            EventEnrollment.event_id == event_id,
            EventEnrollment.user_id.in_(user_ids),
        )
        enrollment_result = await db.execute(enrollment_query)
        enrollments = enrollment_result.scalars().all()
        for e in enrollments:
            # Map user to their enrollment_number and draw_number
            user_enrollment_map[e.user_id] = (e.enrollment_number, e.draw_number)

    # Build response items with enrollment info and AI analysis for validators
    items = []
    for c in catches:
        enrollment_info = user_enrollment_map.get(c.user_id, (None, None))
        items.append(CatchDetailResponse.from_catch(
            c,
            enrollment_number=enrollment_info[0],
            draw_number=enrollment_info[1],
            include_ai_analysis=is_validator,
        ))

    return CatchListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if total > 0 else 1,
    )


@router.post("", response_model=CatchResponse, status_code=status.HTTP_201_CREATED)
async def submit_catch(
    catch_data: CatchCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Submit a new catch for validation.

    Normal flow: User must be enrolled in the event.
    Proxy flow: Organizer/validator can upload on behalf of an angler using on_behalf_of_user_id.
    """
    # Check event exists and is ongoing
    event_query = select(Event).where(Event.id == catch_data.event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if event.status != EventStatus.ONGOING.value:
        raise HTTPException(
            status_code=400,
            detail="Event is not currently active for catch submissions",
        )

    # Determine if this is a proxy upload
    is_proxy_upload = catch_data.on_behalf_of_user_id is not None
    target_user_id = catch_data.on_behalf_of_user_id if is_proxy_upload else current_user.id

    if is_proxy_upload:
        # Proxy upload: validate permissions
        user_roles = current_user.profile.roles if current_user.profile else []
        is_admin = "administrator" in user_roles
        is_owner = event.created_by_id == current_user.id

        is_assigned_validator = False
        if "validator" in user_roles:
            is_assigned_validator = await check_is_event_validator(
                catch_data.event_id, current_user.id, db
            )

        if not (is_admin or is_owner or is_assigned_validator):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only event owner, assigned validator, or administrator can upload on behalf of an angler",
            )

        # Check target user is enrolled and approved
        target_enrollment_query = select(EventEnrollment).where(
            EventEnrollment.event_id == catch_data.event_id,
            EventEnrollment.user_id == target_user_id,
            EventEnrollment.status == EnrollmentStatus.APPROVED.value,
        )
        target_enrollment_result = await db.execute(target_enrollment_query)
        if not target_enrollment_result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Target angler is not enrolled or enrollment not approved for this event",
            )
    else:
        # Normal upload: check current user is enrolled
        enrollment_query = select(EventEnrollment).where(
            EventEnrollment.event_id == catch_data.event_id,
            EventEnrollment.user_id == current_user.id,
            EventEnrollment.status == EnrollmentStatus.APPROVED.value,
        )
        enrollment_result = await db.execute(enrollment_query)
        if not enrollment_result.scalar_one_or_none():
            raise HTTPException(
                status_code=403,
                detail="Not enrolled or enrollment not approved for this event",
            )

    # Check fish exists and is valid
    fish_query = select(Fish).where(Fish.id == catch_data.fish_id, Fish.is_active == True)
    fish_result = await db.execute(fish_query)
    fish = fish_result.scalar_one_or_none()

    if not fish:
        raise HTTPException(status_code=404, detail="Fish species not found")

    # Validate length against fish limits
    if fish.min_length and catch_data.length < fish.min_length:
        raise HTTPException(
            status_code=400,
            detail=f"Catch length ({catch_data.length}cm) is below minimum ({fish.min_length}cm) for {fish.name}",
        )

    # Create catch
    catch = Catch(
        event_id=catch_data.event_id,
        user_id=target_user_id,  # The angler who caught the fish
        uploaded_by_id=current_user.id if is_proxy_upload else None,  # Who uploaded (only for proxy)
        fish_id=catch_data.fish_id,
        length=catch_data.length,
        weight=catch_data.weight,
        photo_url=catch_data.photo_url,
        location_lat=catch_data.location_lat,
        location_lng=catch_data.location_lng,
        catch_time=catch_data.catch_time or datetime.now(timezone.utc),
        status=CatchStatus.PENDING.value,
    )

    db.add(catch)
    await db.commit()
    await db.refresh(catch)

    # Queue AI analysis if enabled for this event (non-blocking, runs in background)
    if event.use_ai_analysis:
        try:
            queue_catch_analysis(catch.id, delay_seconds=5)
        except Exception as e:
            logger.warning(f"Failed to queue AI analysis for catch {catch.id}: {e}")

    # Broadcast to validators that a new catch was submitted
    background_tasks.add_task(
        broadcast_catch_submitted,
        catch.event_id,
        catch.id,
        catch.user_id,
    )

    return catch


@router.post("/upload", response_model=CatchResponse, status_code=status.HTTP_201_CREATED)
async def submit_catch_with_image(
    event_id: int = Query(..., description="Event ID"),
    fish_id: int = Query(..., description="Fish species ID"),
    length: float = Query(..., gt=0, le=400, description="Catch length in cm (max 400)"),
    weight: float | None = Query(None, gt=0, le=500, description="Catch weight in kg (max 500)"),
    location_lat: float | None = Query(None, description="Latitude (optional)"),
    location_lng: float | None = Query(None, description="Longitude (optional)"),
    location_accuracy: float | None = Query(None, description="GPS accuracy in meters (optional)"),
    client_hash: str | None = Query(None, description="SHA-256 hash from client (optional)"),
    on_behalf_of_user_id: int | None = Query(None, description="User ID of the angler (for proxy uploads by organizers/validators)"),
    photo: UploadFile = File(..., description="Photo or video of the catch"),
    background_tasks: BackgroundTasks = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Submit a new catch with media upload (form-data).
    Use this endpoint from mobile apps or Postman with form-data.

    Normal flow: User must be enrolled in the event.
    Proxy flow: Organizer/validator can upload on behalf of an angler using on_behalf_of_user_id.

    Features:
    - Duplicate detection via SHA-256 hash (returns 409 if duplicate)
    - Video duration validation (returns 400 if > max_video_duration)
    - Automatic media conversion (images -> WebP, videos -> MP4)
    - Hash-based storage keys for deduplication
    """
    from app.services.media_processor import MediaProcessor, MediaProcessingError
    from app.services.uploads import (
        get_storage,
        generate_hash_based_key,
        generate_poster_key,
    )
    import os

    # Check event exists and is ongoing
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if event.status != EventStatus.ONGOING.value:
        raise HTTPException(
            status_code=400,
            detail="Event is not currently active for catch submissions",
        )

    # Determine if this is a proxy upload
    is_proxy_upload = on_behalf_of_user_id is not None
    target_user_id = on_behalf_of_user_id if is_proxy_upload else current_user.id

    if is_proxy_upload:
        # Proxy upload: validate permissions
        user_roles = current_user.profile.roles if current_user.profile else []
        is_admin = "administrator" in user_roles
        is_owner = event.created_by_id == current_user.id

        is_assigned_validator = False
        if "validator" in user_roles:
            is_assigned_validator = await check_is_event_validator(
                event_id, current_user.id, db
            )

        if not (is_admin or is_owner or is_assigned_validator):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only event owner, assigned validator, or administrator can upload on behalf of an angler",
            )

        # Check target user is enrolled and approved
        target_enrollment_query = select(EventEnrollment).where(
            EventEnrollment.event_id == event_id,
            EventEnrollment.user_id == target_user_id,
            EventEnrollment.status == EnrollmentStatus.APPROVED.value,
        )
        target_enrollment_result = await db.execute(target_enrollment_query)
        if not target_enrollment_result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Target angler is not enrolled or enrollment not approved for this event",
            )
    else:
        # Normal upload: check current user is enrolled
        enrollment_query = select(EventEnrollment).where(
            EventEnrollment.event_id == event_id,
            EventEnrollment.user_id == current_user.id,
            EventEnrollment.status == EnrollmentStatus.APPROVED.value,
        )
        enrollment_result = await db.execute(enrollment_query)
        if not enrollment_result.scalar_one_or_none():
            raise HTTPException(
                status_code=403,
                detail="Not enrolled or enrollment not approved for this event",
            )

    # Check fish exists and is valid
    fish_query = select(Fish).where(Fish.id == fish_id, Fish.is_active == True)
    fish_result = await db.execute(fish_query)
    fish = fish_result.scalar_one_or_none()

    if not fish:
        raise HTTPException(status_code=404, detail="Fish species not found")

    # Validate media type against event constraints
    if photo.content_type:
        is_video = photo.content_type.startswith("video/")
        is_image = photo.content_type.startswith("image/")

        if is_video and event.allowed_media_type == "image":
            raise HTTPException(
                status_code=400,
                detail="This event only accepts image uploads, not videos",
            )
        if is_image and event.allowed_media_type == "video":
            raise HTTPException(
                status_code=400,
                detail="This event only accepts video uploads, not images",
            )

    # Get max video duration from event settings (default 5 seconds)
    max_video_duration = event.max_video_duration or 5

    try:
        # Stream upload with hash computation
        temp_path, metadata = await MediaProcessor.stream_upload_with_hash(photo)

        try:
            # Check for duplicate BEFORE expensive processing
            if metadata.sha256_hash:
                duplicate_query = select(Catch).where(
                    Catch.event_id == event_id,
                    Catch.user_id == target_user_id,
                    Catch.sha256_original == metadata.sha256_hash,
                )
                duplicate_result = await db.execute(duplicate_query)
                existing_catch = duplicate_result.scalar_one_or_none()

                if existing_catch:
                    # Clean up temp file
                    os.unlink(temp_path)
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "DUPLICATE_MEDIA",
                            "message": "This media has already been submitted for this event.",
                            "existing_catch_id": existing_catch.id,
                        }
                    )

            # Validate video duration if applicable
            if metadata.is_video:
                duration = MediaProcessor.get_video_duration(temp_path)
                MediaProcessor.validate_video_duration(duration, max_video_duration)
                metadata.video_duration_seconds = duration

            # Process media (convert to WebP/MP4)
            if metadata.is_video:
                processed = await MediaProcessor.process_video(temp_path)
                output_path = processed.processed_path
                poster_path = processed.poster_path
                output_extension = ".mp4"
                output_mime_type = "video/mp4"
            else:
                output_path = await MediaProcessor.process_image(temp_path)
                poster_path = None
                output_extension = ".webp"
                output_mime_type = "image/webp"

            # Clean up original temp file
            try:
                os.unlink(temp_path)
            except:
                pass

            # Upload with hash-based key (includes user_id for organization)
            storage = get_storage()
            media_type = "videos" if metadata.is_video else "images"
            storage_key = generate_hash_based_key(
                event_id,
                target_user_id,  # The angler who caught the fish
                metadata.sha256_hash,
                output_extension,
                media_type,
            )

            # Upload processed media
            photo_url = await storage.save_file_with_content_type(
                storage_key,
                output_path,
                output_mime_type,
            )

            # Upload poster frame if video
            poster_url = None
            if poster_path:
                poster_key = generate_poster_key(event_id, target_user_id, metadata.sha256_hash)
                poster_url = await storage.save_file_with_content_type(
                    poster_key,
                    poster_path,
                    "image/jpeg",
                )

            # Clean up poster temp file
            try:
                if poster_path:
                    os.unlink(poster_path)
            except:
                pass

            # Clean up processed temp file
            try:
                os.unlink(output_path)
            except:
                pass

            # Create catch with media metadata
            catch = Catch(
                event_id=event_id,
                user_id=target_user_id,  # The angler who caught the fish
                uploaded_by_id=current_user.id if is_proxy_upload else None,  # Who uploaded (only for proxy)
                fish_id=fish_id,
                length=length,
                weight=weight,
                photo_url=photo_url,
                poster_url=poster_url,
                sha256_original=metadata.sha256_hash,
                original_mime_type=metadata.mime_type,
                original_size_bytes=metadata.size_bytes,
                video_duration_seconds=metadata.video_duration_seconds,
                location_lat=location_lat,
                location_lng=location_lng,
                location_accuracy=location_accuracy,
                catch_time=datetime.now(timezone.utc),
                status=CatchStatus.PENDING.value,
            )

            db.add(catch)
            await db.commit()
            await db.refresh(catch)

            # Queue AI analysis if enabled for this event (non-blocking, runs in background)
            if event.use_ai_analysis:
                try:
                    queue_catch_analysis(catch.id, delay_seconds=5)
                except Exception as e:
                    logger.warning(f"Failed to queue AI analysis for catch {catch.id}: {e}")

            # Broadcast to validators that a new catch was submitted
            if background_tasks:
                background_tasks.add_task(
                    broadcast_catch_submitted,
                    catch.event_id,
                    catch.id,
                    catch.user_id,
                )

            return catch

        except HTTPException:
            # Re-raise HTTP exceptions (validation errors, duplicates)
            raise
        except MediaProcessingError as e:
            # Clean up temp file on processing error
            try:
                os.unlink(temp_path)
            except:
                pass
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": e.code, "message": e.message}
            )
        except Exception as e:
            # Clean up temp file on unexpected error
            try:
                os.unlink(temp_path)
            except:
                pass
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Media processing failed: {str(e)}"
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Upload failed: {str(e)}"
        )


# ============== Search Endpoint (MUST be before /{catch_id} routes) ==============


@router.get("/search", response_model=CatchSearchResponse)
async def search_catches(
    event_id: int = Query(..., description="Event ID (required)"),
    user_search: str | None = Query(None, description="Search by user name or email"),
    team_search: str | None = Query(None, description="Search by team name"),
    status_filter: CatchStatus | None = Query(None, alias="status"),
    fish_id: int | None = Query(None, description="Filter by fish species"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Search catches for revalidation.
    Validators can search by user name/email or team name.
    Returns catches with team info for team events.
    """
    # Check event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Permission check: must be admin, event owner, or assigned validator
    user_roles = current_user.profile.roles if current_user.profile else []

    is_admin = "administrator" in user_roles
    is_owner = event.created_by_id == current_user.id

    is_assigned_validator = False
    if "validator" in user_roles:
        is_assigned_validator = await check_is_event_validator(
            event_id, current_user.id, db
        )

    if not (is_admin or is_owner or is_assigned_validator):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the event owner, assigned validator, or administrator can search catches"
        )

    # Build user to team mapping for team events
    user_to_team: dict[int, tuple[int, str]] = {}  # user_id -> (team_id, team_name)
    if event.is_team_event:
        teams_query = (
            select(Team)
            .options(
                selectinload(Team.members).selectinload(TeamMember.enrollment),
            )
            .where(Team.event_id == event_id, Team.is_active == True)
        )
        teams_result = await db.execute(teams_query)
        teams = teams_result.scalars().all()

        for team in teams:
            for member in team.members:
                if member.is_active and member.enrollment:
                    user_to_team[member.enrollment.user_id] = (team.id, team.name)

    # Base query for catches
    query = (
        select(Catch)
        .options(
            selectinload(Catch.user).selectinload(UserAccount.profile),
            selectinload(Catch.fish),
            selectinload(Catch.validated_by),
            selectinload(Catch.revalidated_by),
        )
        .where(Catch.event_id == event_id)
    )

    # Apply filters
    if status_filter:
        query = query.where(Catch.status == status_filter.value)

    if fish_id:
        query = query.where(Catch.fish_id == fish_id)

    # Get all matching catches first for user/team filtering
    result = await db.execute(query.order_by(Catch.submitted_at.desc()))
    all_catches = result.scalars().all()

    # Filter by user search if provided
    filtered_catches = []
    for catch in all_catches:
        # User search filter
        if user_search:
            user_search_lower = user_search.lower()
            user_name = ""
            if catch.user and catch.user.profile:
                user_name = f"{catch.user.profile.first_name or ''} {catch.user.profile.last_name or ''}".lower()
            user_email = catch.user.email.lower() if catch.user else ""

            if user_search_lower not in user_name and user_search_lower not in user_email:
                continue

        # Team search filter (only for team events)
        if team_search and event.is_team_event:
            team_info = user_to_team.get(catch.user_id)
            if not team_info or team_search.lower() not in team_info[1].lower():
                continue

        filtered_catches.append(catch)

    # Pagination
    total = len(filtered_catches)
    pages = ceil(total / page_size) if total > 0 else 1
    offset = (page - 1) * page_size
    paginated_catches = filtered_catches[offset:offset + page_size]

    # Build response items
    items = []
    for catch in paginated_catches:
        user_name = ""
        if catch.user and catch.user.profile:
            user_name = f"{catch.user.profile.first_name or ''} {catch.user.profile.last_name or ''}".strip()

        team_info = user_to_team.get(catch.user_id) if event.is_team_event else None

        items.append(CatchSearchItem(
            id=catch.id,
            user_id=catch.user_id,
            user_name=user_name or f"User {catch.user_id}",
            user_email=catch.user.email if catch.user else "",
            team_id=team_info[0] if team_info else None,
            team_name=team_info[1] if team_info else None,
            fish_id=catch.fish_id,
            fish_name=catch.fish.name if catch.fish else "Unknown",
            length=catch.length,
            weight=catch.weight,
            photo_url=catch.photo_url,
            status=catch.status,
            validated_at=catch.validated_at,
            validated_by=catch.validated_by.email if catch.validated_by else None,
            revalidated_at=catch.revalidated_at,
            revalidated_by=catch.revalidated_by.email if catch.revalidated_by else None,
            revalidation_reason=catch.revalidation_reason,
        ))

    return CatchSearchResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


# ============== Catch ID-based Routes ==============


@router.get("/{catch_id}", response_model=CatchDetailResponse)
async def get_catch(
    catch_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get a specific catch.
    Validators also receive AI analysis hints.
    """
    query = (
        select(Catch)
        .options(
            selectinload(Catch.user).selectinload(UserAccount.profile),
            selectinload(Catch.fish),
            selectinload(Catch.validated_by),
            selectinload(Catch.ai_analysis).selectinload(CatchAiAnalysis.detected_species),
        )
        .where(Catch.id == catch_id)
    )
    result = await db.execute(query)
    catch = result.scalar_one_or_none()

    if not catch:
        raise HTTPException(status_code=404, detail="Catch not found")

    # Check permissions - validators see all, users see approved or their own
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_validator = bool(user_roles.intersection({"administrator", "validator", "organizer"}))
    is_own_catch = catch.user_id == current_user.id

    if not is_validator and not is_own_catch and catch.status != CatchStatus.APPROVED.value:
        raise HTTPException(status_code=403, detail="Not authorized to view this catch")

    # Include AI analysis for validators
    return CatchDetailResponse.from_catch(catch, include_ai_analysis=is_validator)


@router.post("/{catch_id}/validate", response_model=CatchDetailResponse)
async def validate_catch(
    catch_id: int,
    validation_data: CatchValidation,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Validate a catch (approve or reject).
    Only the event owner, assigned validators, or administrators can validate catches.
    """
    query = (
        select(Catch)
        .options(
            selectinload(Catch.user).selectinload(UserAccount.profile),
            selectinload(Catch.fish),
            selectinload(Catch.event),
        )
        .where(Catch.id == catch_id)
    )
    result = await db.execute(query)
    catch = result.scalar_one_or_none()

    if not catch:
        raise HTTPException(status_code=404, detail="Catch not found")

    # Permission check: must be admin, event owner, or assigned validator
    event = catch.event
    user_roles = current_user.profile.roles if current_user.profile else []

    is_admin = "administrator" in user_roles
    is_owner = event.created_by_id == current_user.id

    # Check if user is an assigned validator for this specific event
    is_assigned_validator = False
    if "validator" in user_roles:
        is_assigned_validator = await check_is_event_validator(
            event.id, current_user.id, db
        )

    if not (is_admin or is_owner or is_assigned_validator):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the event owner, assigned validator, or administrator can validate catches"
        )

    if catch.status != CatchStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail="Catch has already been validated",
        )

    # Validate new fish species if provided
    if validation_data.new_fish_id:
        fish_query = select(Fish).where(Fish.id == validation_data.new_fish_id)
        fish_result = await db.execute(fish_query)
        new_fish = fish_result.scalar_one_or_none()
        if not new_fish:
            raise HTTPException(status_code=400, detail="Invalid fish species ID")

    # Update catch status
    catch.status = validation_data.status.value
    catch.validated_by_id = current_user.id
    catch.validated_at = datetime.now(timezone.utc)

    if validation_data.status == CatchStatus.REJECTED:
        catch.rejection_reason = validation_data.rejection_reason
    else:
        # Apply fish species change if provided
        if validation_data.new_fish_id:
            catch.fish_id = validation_data.new_fish_id

        # Apply adjustments if provided
        if validation_data.adjusted_length:
            catch.length = validation_data.adjusted_length
        if validation_data.adjusted_weight:
            catch.weight = validation_data.adjusted_weight

        # Calculate points based on fish scoring config
        # Uses accountable_min_length and under_min_length_points
        catch.points = await calculate_catch_points(
            db, catch.event_id, catch.fish_id, catch.length
        )

        # Update scoreboard
        await update_scoreboard(db, catch)

    await db.commit()
    await db.refresh(catch)

    # Broadcast live update if approved
    if validation_data.status == CatchStatus.APPROVED:
        catch_user_name = f"{catch.user.profile.first_name} {catch.user.profile.last_name}" if catch.user.profile else f"User {catch.user_id}"
        background_tasks.add_task(
            broadcast_catch_validated,
            catch.event_id,
            catch.id,
            catch.user_id,
            catch.fish.name if catch.fish else "Unknown",
            float(catch.length),
            float(catch.points) if catch.points else float(catch.length),
            catch.photo_url,
            catch_user_name,
            catch.validated_at.isoformat() if catch.validated_at else None,
        )
        # Send push notification to event participants
        send_catch_notification.delay(
            event_id=catch.event_id,
            catch_user_id=catch.user_id,
            catch_user_name=catch_user_name,
            fish_species=catch.fish.name,
            fish_weight=float(catch.weight) if catch.weight else None,
            fish_length=float(catch.length) if catch.length else None,
            event_name=event.name,
        )
        # Process achievements for the approved catch
        process_achievements_for_catch.delay(
            catch_id=catch.id,
            event_id=catch.event_id,
            user_id=catch.user_id,
        )
        # Trigger stats recalculation for user
        await statistics_service.update_user_stats_for_event(db, catch.user_id, catch.event_id)

    # Queue leaderboard recalculation
    queue_leaderboard_recalculation(catch.event_id, "catch_validated")

    # Send notification to catch owner about the result
    send_catch_response_notification.delay(
        catch_owner_id=catch.user_id,
        fish_species=catch.fish.name if catch.fish else "Unknown",
        fish_length=float(catch.length),
        status="approved" if validation_data.status == CatchStatus.APPROVED else "rejected",
        event_name=event.name,
        event_id=catch.event_id,
        rejection_reason=catch.rejection_reason if validation_data.status == CatchStatus.REJECTED else None,
    )

    # Reload relationships for response
    query = (
        select(Catch)
        .options(
            selectinload(Catch.user).selectinload(UserAccount.profile),
            selectinload(Catch.fish),
            selectinload(Catch.validated_by),
        )
        .where(Catch.id == catch_id)
    )
    result = await db.execute(query)
    catch = result.scalar_one()

    return CatchDetailResponse.from_catch(catch)


@router.post("/{catch_id}/revalidate", response_model=CatchDetailResponse)
async def revalidate_catch(
    catch_id: int,
    revalidation_data: CatchRevalidation,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Revalidate an already validated catch (change approved to rejected or vice versa).
    Only the event owner, assigned validators, or administrators can revalidate catches.
    Cannot revalidate pending catches - use the validate endpoint instead.
    Scores are automatically recalculated after revalidation.
    """
    query = (
        select(Catch)
        .options(
            selectinload(Catch.user).selectinload(UserAccount.profile),
            selectinload(Catch.fish),
            selectinload(Catch.event),
        )
        .where(Catch.id == catch_id)
    )
    result = await db.execute(query)
    catch = result.scalar_one_or_none()

    if not catch:
        raise HTTPException(status_code=404, detail="Catch not found")

    # Permission check: must be admin, event owner, or assigned validator
    event = catch.event
    user_roles = current_user.profile.roles if current_user.profile else []

    is_admin = "administrator" in user_roles
    is_owner = event.created_by_id == current_user.id

    # Check if user is an assigned validator for this specific event
    is_assigned_validator = False
    if "validator" in user_roles:
        is_assigned_validator = await check_is_event_validator(
            event.id, current_user.id, db
        )

    if not (is_admin or is_owner or is_assigned_validator):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the event owner, assigned validator, or administrator can revalidate catches"
        )

    # Check 72-hour window for finished events
    if not is_within_post_event_window(event):
        deadline = get_post_event_deadline(event)
        raise HTTPException(
            status_code=400,
            detail=f"Revalidation window has expired. Actions are only allowed within {POST_EVENT_ACTION_HOURS} hours after event ends."
            + (f" Deadline was: {deadline.isoformat()}" if deadline else ""),
        )

    # Can only revalidate already validated catches
    if catch.status == CatchStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail="Cannot revalidate a pending catch. Use the validate endpoint instead.",
        )

    # Check if any changes are being made (status, fish, length, or weight)
    has_status_change = catch.status != revalidation_data.new_status.value
    has_fish_change = revalidation_data.new_fish_id and revalidation_data.new_fish_id != catch.fish_id
    has_length_change = revalidation_data.adjusted_length and revalidation_data.adjusted_length != catch.length
    has_weight_change = revalidation_data.adjusted_weight and revalidation_data.adjusted_weight != catch.weight

    if not has_status_change and not has_fish_change and not has_length_change and not has_weight_change:
        raise HTTPException(
            status_code=400,
            detail="No changes detected. Please modify the status, species, length, or weight.",
        )

    # Validate new fish species if provided
    if revalidation_data.new_fish_id:
        fish_query = select(Fish).where(Fish.id == revalidation_data.new_fish_id)
        fish_result = await db.execute(fish_query)
        new_fish = fish_result.scalar_one_or_none()
        if not new_fish:
            raise HTTPException(status_code=400, detail="Invalid fish species ID")

    old_status = catch.status
    old_fish_id = catch.fish_id

    # Update catch status and fish if changed
    catch.status = revalidation_data.new_status.value
    catch.revalidated_by_id = current_user.id
    catch.revalidated_at = datetime.now(timezone.utc)
    catch.revalidation_reason = revalidation_data.reason

    # Update fish species if changed
    if revalidation_data.new_fish_id:
        catch.fish_id = revalidation_data.new_fish_id

    if revalidation_data.new_status == CatchStatus.REJECTED:
        # Rejecting a previously approved catch
        catch.rejection_reason = revalidation_data.reason
        catch.points = None  # Remove points

        # Need to recalculate scoreboard (remove this catch's contribution)
        await recalculate_user_score(db, catch.event_id, catch.user_id)

    else:
        # Approving (or re-approving with corrections)
        catch.rejection_reason = None

        # Apply adjustments if provided
        if revalidation_data.adjusted_length:
            catch.length = revalidation_data.adjusted_length
        if revalidation_data.adjusted_weight:
            catch.weight = revalidation_data.adjusted_weight

        # Calculate points based on fish scoring config
        # Uses accountable_min_length and under_min_length_points
        catch.points = await calculate_catch_points(
            db, catch.event_id, catch.fish_id, catch.length
        )

        # Update scoreboard
        await update_scoreboard(db, catch)

    await db.commit()
    await db.refresh(catch)

    # Broadcast live update
    background_tasks.add_task(
        broadcast_catch_revalidated,
        catch.event_id,
        catch.id,
        catch.user_id,
        old_status,
        revalidation_data.new_status.value,
    )

    # Send push notification if catch was approved (rejected -> approved)
    if revalidation_data.new_status == CatchStatus.APPROVED:
        catch_user_name = f"{catch.user.profile.first_name} {catch.user.profile.last_name}"
        send_catch_notification.delay(
            event_id=catch.event_id,
            catch_user_id=catch.user_id,
            catch_user_name=catch_user_name,
            fish_species=catch.fish.name,
            fish_weight=float(catch.weight) if catch.weight else None,
            fish_length=float(catch.length) if catch.length else None,
            event_name=event.name,
        )
        # Process achievements for the revalidated catch
        process_achievements_for_catch.delay(
            catch_id=catch.id,
            event_id=catch.event_id,
            user_id=catch.user_id,
        )
        # Trigger stats recalculation for user
        await statistics_service.update_user_stats_for_event(db, catch.user_id, catch.event_id)

    # Queue leaderboard recalculation
    queue_leaderboard_recalculation(catch.event_id, "catch_revalidated")

    # Send notification to catch owner about the revalidation result
    send_catch_response_notification.delay(
        catch_owner_id=catch.user_id,
        fish_species=catch.fish.name if catch.fish else "Unknown",
        fish_length=float(catch.length),
        status="approved" if revalidation_data.new_status == CatchStatus.APPROVED else "rejected",
        event_name=event.name,
        event_id=catch.event_id,
        rejection_reason=revalidation_data.reason if revalidation_data.new_status == CatchStatus.REJECTED else None,
    )

    # Reload relationships for response
    query = (
        select(Catch)
        .options(
            selectinload(Catch.user).selectinload(UserAccount.profile),
            selectinload(Catch.fish),
            selectinload(Catch.validated_by),
            selectinload(Catch.revalidated_by),
        )
        .where(Catch.id == catch_id)
    )
    result = await db.execute(query)
    catch = result.scalar_one()

    return CatchDetailResponse.from_catch(catch)


@router.delete("/{catch_id}", response_model=MessageResponse)
async def delete_catch(
    catch_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Delete a pending catch.
    Users can only delete their own pending catches.
    """
    query = select(Catch).where(Catch.id == catch_id)
    result = await db.execute(query)
    catch = result.scalar_one_or_none()

    if not catch:
        raise HTTPException(status_code=404, detail="Catch not found")

    # Check ownership
    if catch.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to delete this catch",
        )

    # Only pending catches can be deleted
    if catch.status != CatchStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail="Can only delete pending catches",
        )

    await db.delete(catch)
    await db.commit()

    return {"message": "Catch deleted successfully"}


@router.get("/leaderboard/{event_id}", response_model=LeaderboardResponse)
async def get_leaderboard(
    event_id: int,
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """
    Get event leaderboard (public endpoint).
    """
    # Check event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Get scoreboard entries
    query = (
        select(EventScoreboard)
        .options(selectinload(EventScoreboard.user).selectinload(UserAccount.profile))
        .where(EventScoreboard.event_id == event_id)
        .order_by(EventScoreboard.rank)
        .limit(limit)
    )
    result = await db.execute(query)
    scoreboards = result.scalars().all()

    # Get total participants
    count_query = select(func.count(EventScoreboard.id)).where(
        EventScoreboard.event_id == event_id
    )
    count_result = await db.execute(count_query)
    total = count_result.scalar()

    # Get last update time
    last_updated = None
    if scoreboards:
        last_updated = max(s.updated_at for s in scoreboards)

    return LeaderboardResponse(
        event_id=event_id,
        entries=[ScoreboardEntry.from_scoreboard(s) for s in scoreboards],
        total_participants=total,
        last_updated=last_updated,
    )


async def calculate_catch_points(
    db: AsyncSession, event_id: int, fish_id: int, length: float
) -> float:
    """
    Calculate points for a catch based on event fish scoring configuration.

    Scoring rules:
    - If catch length >= accountable_min_length: points = length (1 point per cm)
    - If catch length < accountable_min_length: points = under_min_length_points (fixed value)
    - If no fish scoring config exists: points = length (fallback)

    Args:
        db: Database session
        event_id: Event ID
        fish_id: Fish species ID
        length: Catch length in cm

    Returns:
        Calculated points for this catch
    """
    # Get fish scoring config for this event and fish
    scoring_query = select(EventFishScoring).where(
        EventFishScoring.event_id == event_id,
        EventFishScoring.fish_id == fish_id,
    )
    result = await db.execute(scoring_query)
    fish_scoring = result.scalar_one_or_none()

    if not fish_scoring:
        # No scoring config - default to length as points
        return length

    if length >= fish_scoring.accountable_min_length:
        # Catch meets minimum length - full points (length in cm)
        return length
    else:
        # Catch is under minimum length - fixed points value
        return float(fish_scoring.under_min_length_points)


async def get_user_team_id(db: AsyncSession, event_id: int, user_id: int) -> int | None:
    """Get user's team_id for a team event."""
    query = (
        select(Team.id)
        .join(TeamMember, Team.id == TeamMember.team_id)
        .join(EventEnrollment, TeamMember.enrollment_id == EventEnrollment.id)
        .where(
            Team.event_id == event_id,
            Team.is_active == True,
            TeamMember.is_active == True,
            EventEnrollment.user_id == user_id,
        )
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_user_active_club_id(db: AsyncSession, user_id: int) -> int | None:
    """Get user's active club_id (first active membership)."""
    query = (
        select(ClubMembership.club_id)
        .where(
            ClubMembership.user_id == user_id,
            ClubMembership.status == MembershipStatus.ACTIVE.value,
        )
        .order_by(ClubMembership.joined_at.desc())
        .limit(1)
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def update_scoreboard(db: AsyncSession, catch: Catch) -> None:
    """Update scoreboard after a catch is approved."""
    # Get or create scoreboard entry
    scoreboard_query = select(EventScoreboard).where(
        EventScoreboard.event_id == catch.event_id,
        EventScoreboard.user_id == catch.user_id,
    )
    result = await db.execute(scoreboard_query)
    scoreboard = result.scalar_one_or_none()

    if not scoreboard:
        # Get team_id and club_id for new scoreboard entry
        team_id = await get_user_team_id(db, catch.event_id, catch.user_id)
        club_id = await get_user_active_club_id(db, catch.user_id)

        scoreboard = EventScoreboard(
            event_id=catch.event_id,
            user_id=catch.user_id,
            team_id=team_id,
            club_id=club_id,
            total_catches=0,
            total_length=0.0,
            total_points=0.0,
            species_count=0,
            average_length=0.0,
            first_catch_time=None,
            rank=0,
        )
        db.add(scoreboard)

    # Update aggregates
    scoreboard.total_catches += 1
    scoreboard.total_length += catch.length
    scoreboard.total_points += catch.points or catch.length

    if catch.weight:
        scoreboard.total_weight = (scoreboard.total_weight or 0) + catch.weight

    # Update best catch if this is better
    if not scoreboard.best_catch_length or catch.length > scoreboard.best_catch_length:
        scoreboard.best_catch_length = catch.length
        scoreboard.best_catch_id = catch.id

    # Update average_length
    scoreboard.average_length = round(scoreboard.total_length / scoreboard.total_catches, 2)

    # Update first_catch_time (earliest catch wins in tiebreaker)
    catch_time = catch.catch_time or catch.submitted_at
    if not scoreboard.first_catch_time or catch_time < scoreboard.first_catch_time:
        scoreboard.first_catch_time = catch_time

    # Update species_count - need to query all approved catches to get distinct species
    species_query = (
        select(func.count(func.distinct(Catch.fish_id)))
        .where(
            Catch.event_id == catch.event_id,
            Catch.user_id == catch.user_id,
            Catch.status == CatchStatus.APPROVED.value,
        )
    )
    species_result = await db.execute(species_query)
    scoreboard.species_count = species_result.scalar() or 0

    # Save previous rank for movement tracking
    scoreboard.previous_rank = scoreboard.rank

    await db.flush()

    # Recalculate all ranks for the event
    await recalculate_ranks(db, catch.event_id)


async def recalculate_ranks(db: AsyncSession, event_id: int) -> None:
    """Recalculate all ranks for an event."""
    # Check if this is a team event
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()
    is_team_event = event.is_team_event if event else False

    # Build user_id to team_id mapping for team events
    user_to_team: dict[int, int] = {}
    if is_team_event:
        teams_query = (
            select(Team)
            .options(selectinload(Team.members).selectinload(TeamMember.enrollment))
            .where(Team.event_id == event_id, Team.is_active == True)
        )
        teams_result = await db.execute(teams_query)
        teams = teams_result.scalars().all()
        for team in teams:
            for member in team.members:
                if member.is_active and member.enrollment:
                    user_to_team[member.enrollment.user_id] = team.id

    query = (
        select(EventScoreboard)
        .where(EventScoreboard.event_id == event_id)
        .order_by(EventScoreboard.total_points.desc(), EventScoreboard.total_length.desc())
    )
    result = await db.execute(query)
    scoreboards = result.scalars().all()

    for i, scoreboard in enumerate(scoreboards, start=1):
        old_rank = scoreboard.rank
        scoreboard.rank = i

        # Track significant rank changes
        if old_rank != 0 and old_rank != i:
            movement = RankingMovement(
                event_id=event_id,
                user_id=scoreboard.user_id,
                team_id=user_to_team.get(scoreboard.user_id) if is_team_event else None,
                old_rank=old_rank,
                new_rank=i,
            )
            db.add(movement)


async def broadcast_catch_submitted(event_id: int, catch_id: int, user_id: int) -> None:
    """Broadcast new catch submission to live scoring subscribers (for validators)."""
    await live_scoring_service.broadcast(event_id, {
        "type": "catch_submitted",
        "catch_id": catch_id,
        "user_id": user_id,
    })


async def broadcast_catch_validated(
    event_id: int,
    catch_id: int,
    user_id: int,
    fish_name: str,
    length: float,
    points: float,
    photo_url: str | None,
    angler_name: str,
    validated_at: str | None,
) -> None:
    """Broadcast catch validation to live scoring subscribers with full catch data."""
    logger.info(f"Broadcasting catch_validated for event {event_id}, catch {catch_id}")
    await live_scoring_service.broadcast(event_id, {
        "type": "catch_validated",
        "catch_id": catch_id,
        "user_id": user_id,
        "catch": {
            "catch_id": catch_id,
            "fish_name": fish_name,
            "length": length,
            "points": points,
            "photo_url": photo_url,
            "angler_name": angler_name,
            "validated_at": validated_at,
            "is_scored": True,
        }
    })


async def broadcast_catch_revalidated(
    event_id: int,
    catch_id: int,
    user_id: int,
    old_status: str,
    new_status: str,
) -> None:
    """Broadcast catch revalidation to live scoring subscribers."""
    await live_scoring_service.broadcast(event_id, {
        "type": "catch_revalidated",
        "catch_id": catch_id,
        "user_id": user_id,
        "old_status": old_status,
        "new_status": new_status,
    })


async def recalculate_user_score(db: AsyncSession, event_id: int, user_id: int) -> None:
    """
    Recalculate a user's scoreboard entry from their approved catches.
    Used after revalidation to ensure scores are accurate.
    Includes all tiebreaker fields: species_count, average_length, first_catch_time.
    Also checks enrollment status - disqualified users are removed from scoreboard.
    """
    from app.models.enrollment import EventEnrollment, EnrollmentStatus

    # Check if user is disqualified
    enrollment_query = select(EventEnrollment).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.user_id == user_id,
    )
    enrollment_result = await db.execute(enrollment_query)
    enrollment = enrollment_result.scalar_one_or_none()

    is_disqualified = enrollment and enrollment.status == EnrollmentStatus.DISQUALIFIED.value

    # Get or create scoreboard entry
    scoreboard_query = select(EventScoreboard).where(
        EventScoreboard.event_id == event_id,
        EventScoreboard.user_id == user_id,
    )
    result = await db.execute(scoreboard_query)
    scoreboard = result.scalar_one_or_none()

    # If user is disqualified, remove their scoreboard entry
    if is_disqualified:
        if scoreboard:
            await db.delete(scoreboard)
            await db.flush()
        await recalculate_ranks(db, event_id)
        return

    # Get all approved catches for this user in this event
    catches_query = (
        select(Catch)
        .where(
            Catch.event_id == event_id,
            Catch.user_id == user_id,
            Catch.status == CatchStatus.APPROVED.value,
        )
    )
    result = await db.execute(catches_query)
    catches = result.scalars().all()

    if not catches:
        # No approved catches - remove scoreboard entry if exists
        if scoreboard:
            await db.delete(scoreboard)
            await db.flush()
        await recalculate_ranks(db, event_id)
        return

    # Get team_id and club_id (always update to ensure accuracy)
    team_id = await get_user_team_id(db, event_id, user_id)
    club_id = await get_user_active_club_id(db, user_id)

    if not scoreboard:
        scoreboard = EventScoreboard(
            event_id=event_id,
            user_id=user_id,
            team_id=team_id,
            club_id=club_id,
            rank=0,
        )
        db.add(scoreboard)
    else:
        # Update team_id and club_id for existing entries
        scoreboard.team_id = team_id
        scoreboard.club_id = club_id

    # Recalculate all aggregates
    scoreboard.total_catches = len(catches)
    scoreboard.total_length = sum(c.length for c in catches)
    scoreboard.total_points = sum(c.points or c.length for c in catches)
    scoreboard.total_weight = sum(c.weight for c in catches if c.weight) or None

    # Find best catch
    best = max(catches, key=lambda c: c.length)
    scoreboard.best_catch_length = best.length
    scoreboard.best_catch_id = best.id

    # Calculate tiebreaker fields
    scoreboard.species_count = len(set(c.fish_id for c in catches))
    scoreboard.average_length = round(scoreboard.total_length / scoreboard.total_catches, 2)

    # First catch time (earliest catch_time or submitted_at)
    catch_times = [c.catch_time or c.submitted_at for c in catches]
    scoreboard.first_catch_time = min(catch_times) if catch_times else None

    # Save previous rank for movement tracking
    scoreboard.previous_rank = scoreboard.rank

    await db.flush()

    # Recalculate all ranks for the event
    await recalculate_ranks(db, event_id)


# ============== Catch Photo Upload ==============


@router.post("/upload-photo")
async def upload_catch_photo(
    event_id: int = Query(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
) -> dict:
    """
    Upload a catch photo before submitting the catch.
    Returns the URL to use when creating the catch.
    User must be enrolled in the event.
    """
    # Check event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check user is enrolled and approved
    enrollment_query = select(EventEnrollment).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.user_id == current_user.id,
        EventEnrollment.status == EnrollmentStatus.APPROVED.value,
    )
    enrollment_result = await db.execute(enrollment_query)
    if not enrollment_result.scalar_one_or_none():
        raise HTTPException(
            status_code=403,
            detail="Not enrolled or enrollment not approved for this event",
        )

    # Upload to storage
    photo_url = await storage_service.upload_catch_photo(file, event_id, current_user.id)

    return {"photo_url": photo_url}
