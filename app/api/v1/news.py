"""News API endpoints for organizers and admins."""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.permissions import OrganizerOrAdmin
from app.database import get_db
from app.models.news import News
from app.models.user import UserAccount, UserProfile
from app.schemas.news import (
    NewsCreate,
    NewsListResponse,
    NewsResponse,
    NewsUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/news", tags=["News"])


def _get_author_name(user: UserAccount) -> str:
    """Get display name for news author."""
    if user.profile:
        name = f"{user.profile.first_name or ''} {user.profile.last_name or ''}".strip()
        if name:
            return name
    return user.email.split("@")[0]


def _news_to_response(news: News) -> NewsResponse:
    """Convert News model to response schema."""
    return NewsResponse(
        id=news.id,
        title=news.title,
        content=news.content,
        excerpt=news.excerpt,
        featured_image_url=news.featured_image_url,
        created_by_id=news.created_by_id,
        author_name=_get_author_name(news.created_by) if news.created_by else "Unknown",
        is_published=news.is_published,
        is_deleted=news.is_deleted,
        created_at=news.created_at,
        updated_at=news.updated_at,
        published_at=news.published_at,
    )


@router.get("", response_model=NewsListResponse)
async def list_news(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, max_length=100),
    include_deleted: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> NewsListResponse:
    """
    List news articles.

    - Admins see all articles
    - Organizers see only their own articles
    """
    is_admin = current_user.profile and current_user.profile.has_role("administrator")

    # Base query - load created_by and their profile for author name
    query = select(News).options(
        selectinload(News.created_by).selectinload(UserAccount.profile)
    )

    # Filter by owner for non-admins
    if not is_admin:
        query = query.where(News.created_by_id == current_user.id)

    # Exclude deleted by default
    if not include_deleted:
        query = query.where(News.is_deleted == False)  # noqa: E712

    # Search filter
    if search:
        search_filter = f"%{search}%"
        query = query.where(
            or_(
                News.title.ilike(search_filter),
                News.content.ilike(search_filter),
            )
        )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    query = query.order_by(News.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    news_list = result.scalars().all()

    return NewsListResponse(
        items=[_news_to_response(n) for n in news_list],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if total > 0 else 0,
    )


@router.post("", response_model=NewsResponse, status_code=status.HTTP_201_CREATED)
async def create_news(
    data: NewsCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> NewsResponse:
    """
    Create a new news article.

    Organizers and admins can create articles.
    Articles are created as drafts (is_published=False) by default.
    """
    news = News(
        title=data.title,
        content=data.content,
        excerpt=data.excerpt,
        featured_image_url=data.featured_image_url,
        created_by_id=current_user.id,
        is_published=False,
    )

    db.add(news)
    await db.commit()

    # Re-query with proper eager loading for the response
    query = (
        select(News)
        .options(selectinload(News.created_by).selectinload(UserAccount.profile))
        .where(News.id == news.id)
    )
    result = await db.execute(query)
    news = result.scalar_one()

    logger.info(f"News article created: id={news.id}, title={news.title}, by user={current_user.id}")

    return _news_to_response(news)


@router.get("/{news_id}", response_model=NewsResponse)
async def get_news(
    news_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> NewsResponse:
    """
    Get a specific news article.

    - Admins can view any article
    - Organizers can only view their own articles
    """
    query = (
        select(News)
        .options(selectinload(News.created_by).selectinload(UserAccount.profile))
        .where(News.id == news_id)
    )

    result = await db.execute(query)
    news = result.scalar_one_or_none()

    if not news:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="News article not found",
        )

    # Check ownership for non-admins
    is_admin = current_user.profile and current_user.profile.has_role("administrator")
    if not is_admin and news.created_by_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this article",
        )

    return _news_to_response(news)


@router.patch("/{news_id}", response_model=NewsResponse)
async def update_news(
    news_id: int,
    data: NewsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> NewsResponse:
    """
    Update a news article.

    - Admins can update any article
    - Organizers can only update their own articles
    """
    query = (
        select(News)
        .options(selectinload(News.created_by).selectinload(UserAccount.profile))
        .where(News.id == news_id)
        .where(News.is_deleted == False)  # noqa: E712
    )

    result = await db.execute(query)
    news = result.scalar_one_or_none()

    if not news:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="News article not found",
        )

    # Check ownership for non-admins
    is_admin = current_user.profile and current_user.profile.has_role("administrator")
    if not is_admin and news.created_by_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this article",
        )

    # Update fields
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(news, field, value)

    # Handle publish state change
    if data.is_published is not None:
        if data.is_published and not news.published_at:
            news.published_at = datetime.now(timezone.utc)
        elif not data.is_published:
            news.published_at = None

    await db.commit()

    # Re-query with proper eager loading for the response
    query = (
        select(News)
        .options(selectinload(News.created_by).selectinload(UserAccount.profile))
        .where(News.id == news.id)
    )
    result = await db.execute(query)
    news = result.scalar_one()

    logger.info(f"News article updated: id={news.id}, by user={current_user.id}")

    return _news_to_response(news)


@router.delete("/{news_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_news(
    news_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> None:
    """
    Soft delete a news article.

    - Admins can delete any article
    - Organizers can only delete their own articles
    """
    query = (
        select(News)
        .where(News.id == news_id)
        .where(News.is_deleted == False)  # noqa: E712
    )

    result = await db.execute(query)
    news = result.scalar_one_or_none()

    if not news:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="News article not found",
        )

    # Check ownership for non-admins
    is_admin = current_user.profile and current_user.profile.has_role("administrator")
    if not is_admin and news.created_by_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this article",
        )

    # Soft delete
    news.is_deleted = True
    news.deleted_at = datetime.now(timezone.utc)
    news.is_published = False  # Unpublish on delete

    await db.commit()

    logger.info(f"News article deleted: id={news.id}, by user={current_user.id}")


@router.post("/{news_id}/publish", response_model=NewsResponse)
async def toggle_publish_news(
    news_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> NewsResponse:
    """
    Toggle publish state of a news article.

    - If currently published, will unpublish
    - If currently unpublished, will publish
    """
    query = (
        select(News)
        .options(selectinload(News.created_by).selectinload(UserAccount.profile))
        .where(News.id == news_id)
        .where(News.is_deleted == False)  # noqa: E712
    )

    result = await db.execute(query)
    news = result.scalar_one_or_none()

    if not news:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="News article not found",
        )

    # Check ownership for non-admins
    is_admin = current_user.profile and current_user.profile.has_role("administrator")
    if not is_admin and news.created_by_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to publish/unpublish this article",
        )

    # Toggle publish state
    news.is_published = not news.is_published
    if news.is_published:
        news.published_at = datetime.now(timezone.utc)
    else:
        news.published_at = None

    await db.commit()

    # Re-query with proper eager loading for the response
    query = (
        select(News)
        .options(selectinload(News.created_by).selectinload(UserAccount.profile))
        .where(News.id == news.id)
    )
    result = await db.execute(query)
    news = result.scalar_one()

    action = "published" if news.is_published else "unpublished"
    logger.info(f"News article {action}: id={news.id}, by user={current_user.id}")

    return _news_to_response(news)
