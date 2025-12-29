"""Schemas for organizer rules."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class RuleCreate(BaseModel):
    """Schema for creating a new rule."""

    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=255)
    content: Optional[str] = None
    external_url: Optional[str] = Field(None, max_length=500)
    document_url: Optional[str] = Field(None, max_length=500)


class RuleUpdate(BaseModel):
    """Schema for updating a rule."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=255)
    content: Optional[str] = None
    external_url: Optional[str] = Field(None, max_length=500)
    document_url: Optional[str] = Field(None, max_length=500)
    is_active: Optional[bool] = None


class RuleResponse(BaseModel):
    """Schema for rule response."""

    id: int
    name: str
    description: Optional[str] = None
    content: Optional[str] = None
    external_url: Optional[str] = None
    document_url: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    usage_count: int = 0  # Number of events using this rule

    model_config = ConfigDict(from_attributes=True)


class RuleListResponse(BaseModel):
    """Paginated rule list response."""

    items: list[RuleResponse]
    total: int
    page: int
    page_size: int
    pages: int


class RuleDefaultSet(BaseModel):
    """Schema for setting a default rule for an Event Type."""

    event_type_id: int
    rule_id: Optional[int] = None  # None to remove the default


class RuleDefaultResponse(BaseModel):
    """Schema for rule default response."""

    event_type_id: int
    event_type_name: str
    event_type_code: str
    rule_id: Optional[int] = None
    rule_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class RuleDefaultsListResponse(BaseModel):
    """Schema for list of rule defaults."""

    defaults: list[RuleDefaultResponse]


class RuleBriefResponse(BaseModel):
    """Brief rule info for embedding in other responses."""

    id: int
    name: str
    content: Optional[str] = None
    external_url: Optional[str] = None
    document_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
