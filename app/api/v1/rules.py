"""Organizer rules API endpoints."""

from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query, status, UploadFile, File
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user_id_cached
from app.core.storage import storage_service
from app.models.event import Event, EventType
from app.models.rules import OrganizerRule, OrganizerRuleDefault
from app.schemas.rules import (
    RuleCreate,
    RuleUpdate,
    RuleResponse,
    RuleListResponse,
    RuleDefaultSet,
    RuleDefaultResponse,
    RuleDefaultsListResponse,
)
from app.schemas.common import MessageResponse

router = APIRouter()


def rule_to_response(rule: OrganizerRule, usage_count: int = 0) -> RuleResponse:
    """Convert OrganizerRule model to response schema."""
    return RuleResponse(
        id=rule.id,
        name=rule.name,
        description=rule.description,
        content=rule.content,
        external_url=rule.external_url,
        document_url=rule.document_url,
        is_active=rule.is_active,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
        usage_count=usage_count,
    )


@router.get("", response_model=RuleListResponse)
async def list_rules(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    List organizer's rules.
    Only returns rules owned by the current user.
    """
    # Base query - only own rules
    query = select(OrganizerRule).where(OrganizerRule.owner_id == user_id)

    if not include_inactive:
        query = query.where(OrganizerRule.is_active == True)

    # Count total
    count_query = select(func.count(OrganizerRule.id)).where(
        OrganizerRule.owner_id == user_id
    )
    if not include_inactive:
        count_query = count_query.where(OrganizerRule.is_active == True)

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply pagination
    query = query.order_by(OrganizerRule.name).offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    rules = result.scalars().all()

    # Batch-load usage counts for all rules on this page
    rule_ids = [r.id for r in rules]
    counts_by_rule_id = {}
    if rule_ids:
        usage_query = (
            select(Event.rule_id, func.count(Event.id))
            .where(Event.rule_id.in_(rule_ids))
            .group_by(Event.rule_id)
        )
        usage_result = await db.execute(usage_query)
        counts_by_rule_id = dict(usage_result.all())

    items = []
    for rule in rules:
        usage_count = counts_by_rule_id.get(rule.id, 0)
        items.append(rule_to_response(rule, usage_count))

    return RuleListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if total > 0 else 1,
    )


@router.post("", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    rule_data: RuleCreate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Create a new rule.
    Rule must have at least one of: content, external_url, or document_url.
    For file uploads, use the /rules/upload endpoint or upload after creation.
    """
    # Validate that at least one content source is provided
    if not rule_data.content and not rule_data.external_url and not rule_data.document_url:
        raise HTTPException(
            status_code=400,
            detail="Rule must have content, external URL, or uploaded document",
        )

    # Check for duplicate name
    existing_query = select(OrganizerRule).where(
        OrganizerRule.owner_id == user_id,
        OrganizerRule.name == rule_data.name,
        OrganizerRule.is_active == True,
    )
    existing_result = await db.execute(existing_query)
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="A rule with this name already exists",
        )

    rule = OrganizerRule(
        owner_id=user_id,
        name=rule_data.name,
        description=rule_data.description,
        content=rule_data.content,
        external_url=rule_data.external_url,
        document_url=rule_data.document_url,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)

    return rule_to_response(rule)


@router.post("/upload", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule_with_document(
    name: str = Query(..., min_length=1, max_length=100),
    file: UploadFile = File(...),
    description: str = Query(None, max_length=255),
    content: str = Query(None),
    external_url: str = Query(None, max_length=500),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Create a new rule with a document upload.
    This endpoint accepts multipart/form-data with the document file.
    """
    # Check for duplicate name
    existing_query = select(OrganizerRule).where(
        OrganizerRule.owner_id == user_id,
        OrganizerRule.name == name,
        OrganizerRule.is_active == True,
    )
    existing_result = await db.execute(existing_query)
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="A rule with this name already exists",
        )

    # Upload the document
    document_url = await storage_service.upload_rule_document(file, user_id)

    # Create the rule
    rule = OrganizerRule(
        owner_id=user_id,
        name=name,
        description=description,
        content=content,
        external_url=external_url,
        document_url=document_url,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)

    return rule_to_response(rule)


# =============================================================================
# Rule Defaults Management
# IMPORTANT: These routes must be defined BEFORE /{rule_id} routes to avoid
# FastAPI matching "/defaults" as a rule_id parameter
# =============================================================================


@router.get("/defaults", response_model=RuleDefaultsListResponse)
async def list_rule_defaults(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Get default rules for all Event Types.
    Returns all active Event Types with assigned default rule (if any).
    """
    # Get all active event types
    event_types_query = select(EventType).where(EventType.is_active == True).order_by(EventType.name)
    event_types_result = await db.execute(event_types_query)
    event_types = event_types_result.scalars().all()

    # Get user's defaults
    defaults_query = (
        select(OrganizerRuleDefault)
        .options(selectinload(OrganizerRuleDefault.rule))
        .where(OrganizerRuleDefault.owner_id == user_id)
    )
    defaults_result = await db.execute(defaults_query)
    defaults = {d.event_type_id: d for d in defaults_result.scalars().all()}

    # Build response
    items = []
    for et in event_types:
        default = defaults.get(et.id)
        items.append(RuleDefaultResponse(
            event_type_id=et.id,
            event_type_name=et.name,
            event_type_code=et.code,
            rule_id=default.rule_id if default else None,
            rule_name=default.rule.name if default and default.rule else None,
        ))

    return RuleDefaultsListResponse(defaults=items)


@router.post("/defaults", response_model=RuleDefaultResponse)
async def set_rule_default(
    default_data: RuleDefaultSet,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Set or update the default rule for an Event Type.
    Pass rule_id=null to remove the default.
    """
    # Verify event type exists
    et_query = select(EventType).where(EventType.id == default_data.event_type_id)
    et_result = await db.execute(et_query)
    event_type = et_result.scalar_one_or_none()

    if not event_type:
        raise HTTPException(status_code=404, detail="Event type not found")

    # If rule_id provided, verify it exists and belongs to user
    if default_data.rule_id:
        rule_query = select(OrganizerRule).where(
            OrganizerRule.id == default_data.rule_id,
            OrganizerRule.owner_id == user_id,
            OrganizerRule.is_active == True,
        )
        rule_result = await db.execute(rule_query)
        rule = rule_result.scalar_one_or_none()

        if not rule:
            raise HTTPException(status_code=404, detail="Rule not found")

    # Check for existing default
    existing_query = select(OrganizerRuleDefault).where(
        OrganizerRuleDefault.owner_id == user_id,
        OrganizerRuleDefault.event_type_id == default_data.event_type_id,
    )
    existing_result = await db.execute(existing_query)
    existing = existing_result.scalar_one_or_none()

    if default_data.rule_id:
        # Set or update default
        if existing:
            existing.rule_id = default_data.rule_id
        else:
            new_default = OrganizerRuleDefault(
                owner_id=user_id,
                event_type_id=default_data.event_type_id,
                rule_id=default_data.rule_id,
            )
            db.add(new_default)

        await db.commit()

        # Reload rule for response
        rule_query = select(OrganizerRule).where(OrganizerRule.id == default_data.rule_id)
        rule_result = await db.execute(rule_query)
        rule = rule_result.scalar_one()

        return RuleDefaultResponse(
            event_type_id=event_type.id,
            event_type_name=event_type.name,
            event_type_code=event_type.code,
            rule_id=rule.id,
            rule_name=rule.name,
        )
    else:
        # Remove default
        if existing:
            await db.delete(existing)
            await db.commit()

        return RuleDefaultResponse(
            event_type_id=event_type.id,
            event_type_name=event_type.name,
            event_type_code=event_type.code,
            rule_id=None,
            rule_name=None,
        )


@router.delete("/defaults/{event_type_id}", response_model=MessageResponse)
async def remove_rule_default(
    event_type_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Remove the default rule for an Event Type.
    """
    query = select(OrganizerRuleDefault).where(
        OrganizerRuleDefault.owner_id == user_id,
        OrganizerRuleDefault.event_type_id == event_type_id,
    )
    result = await db.execute(query)
    default = result.scalar_one_or_none()

    if not default:
        raise HTTPException(status_code=404, detail="Default not found")

    await db.delete(default)
    await db.commit()

    return {"message": "Default removed successfully"}


# =============================================================================
# Individual Rule Operations (must be AFTER /defaults routes)
# =============================================================================


@router.get("/{rule_id}", response_model=RuleResponse)
async def get_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Get a specific rule.
    Only the owner can view their rules.
    """
    query = select(OrganizerRule).where(OrganizerRule.id == rule_id)
    result = await db.execute(query)
    rule = result.scalar_one_or_none()

    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    if rule.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this rule")

    # Get usage count
    usage_query = select(func.count(Event.id)).where(Event.rule_id == rule.id)
    usage_result = await db.execute(usage_query)
    usage_count = usage_result.scalar() or 0

    return rule_to_response(rule, usage_count)


@router.patch("/{rule_id}", response_model=RuleResponse)
async def update_rule(
    rule_id: int,
    rule_data: RuleUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Update a rule.
    Only the owner can update their rules.
    """
    query = select(OrganizerRule).where(OrganizerRule.id == rule_id)
    result = await db.execute(query)
    rule = result.scalar_one_or_none()

    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    if rule.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to update this rule")

    # Check for duplicate name if name is being changed
    if rule_data.name and rule_data.name != rule.name:
        existing_query = select(OrganizerRule).where(
            OrganizerRule.owner_id == user_id,
            OrganizerRule.name == rule_data.name,
            OrganizerRule.is_active == True,
            OrganizerRule.id != rule_id,
        )
        existing_result = await db.execute(existing_query)
        if existing_result.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail="A rule with this name already exists",
            )

    # Update fields
    if rule_data.name is not None:
        rule.name = rule_data.name
    if rule_data.description is not None:
        rule.description = rule_data.description
    if rule_data.content is not None:
        rule.content = rule_data.content
    if rule_data.external_url is not None:
        rule.external_url = rule_data.external_url
    if rule_data.document_url is not None:
        rule.document_url = rule_data.document_url
    if rule_data.is_active is not None:
        rule.is_active = rule_data.is_active

    # Validate that at least one content source remains
    if not rule.content and not rule.external_url and not rule.document_url:
        raise HTTPException(
            status_code=400,
            detail="Rule must have content, external URL, or uploaded document",
        )

    await db.commit()
    await db.refresh(rule)

    # Get usage count
    usage_query = select(func.count(Event.id)).where(Event.rule_id == rule.id)
    usage_result = await db.execute(usage_query)
    usage_count = usage_result.scalar() or 0

    return rule_to_response(rule, usage_count)


@router.delete("/{rule_id}", response_model=MessageResponse)
async def delete_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Delete a rule (soft delete - sets is_active=False).
    Only the owner can delete their rules.
    """
    query = select(OrganizerRule).where(OrganizerRule.id == rule_id)
    result = await db.execute(query)
    rule = result.scalar_one_or_none()

    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    if rule.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this rule")

    # Soft delete
    rule.is_active = False
    await db.commit()

    return {"message": "Rule deleted successfully"}


@router.post("/{rule_id}/document", response_model=RuleResponse)
async def upload_rule_document(
    rule_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Upload a document (PDF/DOC/DOCX) for a rule.
    Replaces any existing document.
    """
    # Get the rule
    query = select(OrganizerRule).where(OrganizerRule.id == rule_id)
    result = await db.execute(query)
    rule = result.scalar_one_or_none()

    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    if rule.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to update this rule")

    # Delete old document if exists
    if rule.document_url:
        await storage_service.delete_file(rule.document_url)

    # Upload new document
    document_url = await storage_service.upload_rule_document(file, user_id)

    # Update rule
    rule.document_url = document_url
    await db.commit()
    await db.refresh(rule)

    # Get usage count
    usage_query = select(func.count(Event.id)).where(Event.rule_id == rule.id)
    usage_result = await db.execute(usage_query)
    usage_count = usage_result.scalar() or 0

    return rule_to_response(rule, usage_count)


@router.delete("/{rule_id}/document", response_model=RuleResponse)
async def delete_rule_document(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Delete the document from a rule.
    """
    # Get the rule
    query = select(OrganizerRule).where(OrganizerRule.id == rule_id)
    result = await db.execute(query)
    rule = result.scalar_one_or_none()

    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    if rule.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to update this rule")

    if not rule.document_url:
        raise HTTPException(status_code=400, detail="Rule has no document")

    # Validate that at least one content source remains after deletion
    if not rule.content and not rule.external_url:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete document: rule must have content, external URL, or document",
        )

    # Delete document from storage
    await storage_service.delete_file(rule.document_url)

    # Update rule
    rule.document_url = None
    await db.commit()
    await db.refresh(rule)

    # Get usage count
    usage_query = select(func.count(Event.id)).where(Event.rule_id == rule.id)
    usage_result = await db.execute(usage_query)
    usage_count = usage_result.scalar() or 0

    return rule_to_response(rule, usage_count)
