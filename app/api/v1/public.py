"""Public API endpoints - no authentication required."""

import logging
from datetime import datetime, timedelta, timezone
from typing import List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.admin_message import AdminMessage
from app.models.event import Event, EventStatus, EventType
from app.models.news import News
from app.models.partner import Partner
from app.schemas.admin_message import AdminMessageSendResponse, PublicContactCreate
from app.schemas.news import NewsPublicDetailResponse, NewsPublicListResponse, NewsPublicResponse
from app.schemas.partner import PartnerPublicResponse
from app.services.email import get_email_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/public", tags=["Public"])


class LiveEventResponse(BaseModel):
    """Public live event info."""

    id: int
    name: str
    slug: str
    event_type: str
    status: str


class LiveEventsListResponse(BaseModel):
    """Response for list of live events."""

    events: List[LiveEventResponse]
    count: int


class EventCountsByType(BaseModel):
    """Event counts by type."""

    street_fishing: int = 0
    trout_area: int = 0
    boat_fishing: int = 0
    predator_cup: int = 0
    aquachallenge: int = 0


class PublicStatsResponse(BaseModel):
    """Public platform statistics response."""

    event_counts: EventCountsByType
    active_events: int
    total_events: int


@router.get("/stats", response_model=PublicStatsResponse)
async def get_public_stats(
    db: AsyncSession = Depends(get_db),
) -> PublicStatsResponse:
    """
    Get public platform statistics.

    Returns event counts by type and active event count.
    No authentication required.
    """
    # Get event counts by type (excluding draft and cancelled)
    visible_statuses = [
        EventStatus.PUBLISHED.value,
        EventStatus.ONGOING.value,
        EventStatus.COMPLETED.value,
    ]

    # Query events grouped by event type code
    counts_query = (
        select(EventType.code, func.count(Event.id).label("count"))
        .join(Event, Event.event_type_id == EventType.id)
        .where(Event.status.in_(visible_statuses))
        .where(Event.is_deleted == False)  # noqa: E712
        .group_by(EventType.code)
    )

    result = await db.execute(counts_query)
    counts_by_type = {row.code: row.count for row in result.fetchall()}

    # Get active/ongoing events count
    active_query = (
        select(func.count(Event.id))
        .where(Event.status == EventStatus.ONGOING.value)
        .where(Event.is_deleted == False)  # noqa: E712
    )
    active_result = await db.execute(active_query)
    active_count = active_result.scalar() or 0

    # Get total visible events
    total_query = (
        select(func.count(Event.id))
        .where(Event.status.in_(visible_statuses))
        .where(Event.is_deleted == False)  # noqa: E712
    )
    total_result = await db.execute(total_query)
    total_count = total_result.scalar() or 0

    return PublicStatsResponse(
        event_counts=EventCountsByType(
            street_fishing=counts_by_type.get("street_fishing", 0),
            trout_area=counts_by_type.get("trout_area", 0),
            boat_fishing=counts_by_type.get("boat_fishing", 0),
            predator_cup=counts_by_type.get("predator_cup", 0),
            aquachallenge=counts_by_type.get("aquachallenge", 0),
        ),
        active_events=active_count,
        total_events=total_count,
    )


@router.get("/events/live", response_model=LiveEventsListResponse)
async def get_live_events(
    db: AsyncSession = Depends(get_db),
) -> LiveEventsListResponse:
    """
    Get list of ongoing (live) events.

    Returns events that are currently in progress.
    No authentication required.
    """
    from sqlalchemy.orm import selectinload

    query = (
        select(Event)
        .options(selectinload(Event.event_type))
        .where(Event.status == EventStatus.ONGOING.value)
        .where(Event.is_deleted == False)  # noqa: E712
        .order_by(Event.start_date.desc())
    )

    result = await db.execute(query)
    events = result.scalars().all()

    return LiveEventsListResponse(
        events=[
            LiveEventResponse(
                id=event.id,
                name=event.name,
                slug=event.slug,
                event_type=event.event_type.code if event.event_type else "street_fishing",
                status=event.status,
            )
            for event in events
        ],
        count=len(events),
    )


@router.get("/partners", response_model=List[PartnerPublicResponse])
async def get_public_partners(
    db: AsyncSession = Depends(get_db),
) -> List[Partner]:
    """
    Get active partners for landing page.

    Returns partners ordered by display_order.
    No authentication required.
    """
    query = (
        select(Partner)
        .where(Partner.is_active == True)  # noqa: E712
        .order_by(Partner.display_order, Partner.name)
    )

    result = await db.execute(query)
    return list(result.scalars().all())


def _get_author_name_from_news(news: News) -> str:
    """Get display name for news author."""
    if news.created_by and news.created_by.profile:
        name = f"{news.created_by.profile.first_name or ''} {news.created_by.profile.last_name or ''}".strip()
        if name:
            return name
    if news.created_by:
        return news.created_by.email.split("@")[0]
    return "Unknown"


@router.get("/news", response_model=NewsPublicListResponse)
async def get_public_news(
    page: int = Query(1, ge=1),
    page_size: int = Query(6, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> NewsPublicListResponse:
    """
    Get published news articles for landing page.

    Returns articles ordered by published_at (newest first).
    No authentication required.
    """
    from sqlalchemy.orm import selectinload

    # Base query - only published, not deleted
    query = (
        select(News)
        .options(selectinload(News.created_by))
        .where(News.is_published == True)  # noqa: E712
        .where(News.is_deleted == False)  # noqa: E712
    )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination and ordering
    query = query.order_by(News.published_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    news_list = result.scalars().all()

    return NewsPublicListResponse(
        items=[
            NewsPublicResponse(
                id=n.id,
                title=n.title,
                excerpt=n.excerpt,
                featured_image_url=n.featured_image_url,
                author_name=_get_author_name_from_news(n),
                published_at=n.published_at,
            )
            for n in news_list
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/news/{news_id}", response_model=NewsPublicDetailResponse)
async def get_public_news_detail(
    news_id: int,
    db: AsyncSession = Depends(get_db),
) -> NewsPublicDetailResponse:
    """
    Get a single published news article with full content.

    No authentication required.
    """
    from sqlalchemy.orm import selectinload

    query = (
        select(News)
        .options(selectinload(News.created_by))
        .where(News.id == news_id)
        .where(News.is_published == True)  # noqa: E712
        .where(News.is_deleted == False)  # noqa: E712
    )

    result = await db.execute(query)
    news = result.scalar_one_or_none()

    if not news:
        raise HTTPException(
            status_code=404,
            detail="News article not found",
        )

    return NewsPublicDetailResponse(
        id=news.id,
        title=news.title,
        content=news.content,
        excerpt=news.excerpt,
        featured_image_url=news.featured_image_url,
        author_name=_get_author_name_from_news(news),
        published_at=news.published_at,
    )


async def verify_recaptcha(token: str) -> tuple[bool, float]:
    """
    Verify reCAPTCHA v3 token with Google.

    Returns:
        Tuple of (is_valid, score)
    """
    settings = get_settings()

    # Allow dev token in debug mode (for localhost testing)
    if settings.debug and token == "dev-mode-token":
        logger.warning("Dev mode: skipping reCAPTCHA verification")
        return True, 1.0

    if not settings.recaptcha_secret_key:
        logger.warning("reCAPTCHA secret key not configured, skipping verification")
        return True, 1.0  # Allow in dev mode

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://www.google.com/recaptcha/api/siteverify",
                data={
                    "secret": settings.recaptcha_secret_key,
                    "response": token,
                },
                timeout=10.0,
            )
            data = response.json()

            if data.get("success"):
                score = data.get("score", 0.0)
                return score >= settings.recaptcha_min_score, score

            logger.warning(f"reCAPTCHA verification failed: {data.get('error-codes', [])}")
            return False, 0.0

    except Exception as e:
        logger.error(f"reCAPTCHA verification error: {e}")
        return False, 0.0


@router.post("/contact", response_model=AdminMessageSendResponse)
async def submit_public_contact(
    contact: PublicContactCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AdminMessageSendResponse:
    """
    Submit a contact message from non-authenticated visitor.

    - Verifies reCAPTCHA v3 token
    - Rate limited to 1 message per email per day
    - Sends email notification to admin
    """
    settings = get_settings()

    # 1. Verify reCAPTCHA
    is_valid, score = await verify_recaptcha(contact.recaptcha_token)
    if not is_valid:
        logger.warning(f"reCAPTCHA failed for {contact.email}, score: {score}")
        raise HTTPException(
            status_code=400,
            detail="Security verification failed. Please try again.",
        )

    # 2. Rate limit: 1 message per email per day
    one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)
    existing_message = await db.execute(
        select(AdminMessage)
        .where(AdminMessage.sender_email == contact.email)
        .where(AdminMessage.created_at >= one_day_ago)
        .limit(1)
    )
    if existing_message.scalar_one_or_none():
        raise HTTPException(
            status_code=429,
            detail="You have already sent a message today. Please try again tomorrow.",
        )

    # 3. Create admin message
    message = AdminMessage(
        sender_id=None,  # Non-authenticated
        sender_name=contact.name,
        sender_email=contact.email,
        sender_phone=contact.phone,
        subject=contact.subject,
        message=contact.message,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)

    logger.info(f"Contact message created: id={message.id}, email={contact.email}")

    # 4. Send email notification to admin
    email_service = get_email_service()
    if email_service.is_configured():
        html_content = f"""
        <h2>New Contact Form Submission</h2>
        <p><strong>From:</strong> {contact.name} ({contact.email})</p>
        {f'<p><strong>Phone:</strong> {contact.phone}</p>' if contact.phone else ''}
        <p><strong>Subject:</strong> {contact.subject}</p>
        <hr>
        <p><strong>Message:</strong></p>
        <p>{contact.message.replace(chr(10), '<br>')}</p>
        <hr>
        <p><em>Submitted via public contact form</em></p>
        """

        email_service.send_email(
            to_email=settings.contact_admin_email,
            subject=f"[ReelIn Contact] {contact.subject}",
            html_content=html_content,
            text_content=f"From: {contact.name} ({contact.email})\n\n{contact.message}",
        )

    return AdminMessageSendResponse(
        success=True,
        message="Your message has been sent successfully. We'll get back to you soon!",
    )
