"""Schemas for news articles."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class NewsBase(BaseModel):
    """Base schema for news data."""

    title: str = Field(..., min_length=1, max_length=255, description="Article title")
    content: str = Field(..., min_length=10, description="Article content (Markdown)")
    excerpt: Optional[str] = Field(None, max_length=500, description="Summary for landing page cards")
    featured_image_url: Optional[str] = Field(None, max_length=500, description="Featured image URL")


class NewsCreate(NewsBase):
    """Schema for creating a news article."""

    pass


class NewsUpdate(BaseModel):
    """Schema for updating a news article."""

    title: Optional[str] = Field(None, min_length=1, max_length=255)
    content: Optional[str] = Field(None, min_length=10)
    excerpt: Optional[str] = Field(None, max_length=500)
    featured_image_url: Optional[str] = Field(None, max_length=500)
    is_published: Optional[bool] = None


class NewsResponse(NewsBase):
    """Response schema for news article (admin view)."""

    id: int
    created_by_id: int
    author_name: str = Field(..., description="Author display name")
    is_published: bool
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class NewsPublicResponse(BaseModel):
    """Public response schema for news article (landing page list)."""

    id: int
    title: str
    excerpt: Optional[str] = None
    featured_image_url: Optional[str] = None
    author_name: str = Field(..., description="Author display name")
    published_at: datetime

    model_config = {"from_attributes": True}


class NewsPublicDetailResponse(BaseModel):
    """Public response schema for news article detail (full content)."""

    id: int
    title: str
    content: str = Field(..., description="Full article content (Markdown)")
    excerpt: Optional[str] = None
    featured_image_url: Optional[str] = None
    author_name: str = Field(..., description="Author display name")
    published_at: datetime

    model_config = {"from_attributes": True}


class NewsListResponse(BaseModel):
    """Paginated list response for news articles."""

    items: list[NewsResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class NewsPublicListResponse(BaseModel):
    """Paginated list response for public news."""

    items: list[NewsPublicResponse]
    total: int
    page: int
    page_size: int
