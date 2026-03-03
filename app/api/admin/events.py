"""Admin event management endpoints."""

import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.permissions import AdminOnly
from app.models.event import Event, EventFishScoring, EventSpeciesBonusPoints, EventPrize, EventScoringRule
from app.models.trout_area import TAEventSettings, TAEventPointConfig
from app.models.user import UserAccount

router = APIRouter(tags=["Admin Events"])

# Keys written into additional_rules during lineup generation — strip on clone
# so they are recalculated fresh when generate_lineups is called on the new event.
DRAW_TRACKING_KEYS = {"draw_completed", "total_legs", "matches_per_leg", "pairing_algorithm"}


def _make_slug(name: str, suffix: int) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"{base}-copy-{suffix}"


class EventCloneRequest(BaseModel):
    name: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    registration_deadline: Optional[datetime] = None


class EventCloneResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    status: str
    format_code: str
    start_date: datetime
    end_date: datetime
    registration_deadline: Optional[datetime]


@router.post("/{event_id}/clone", response_model=EventCloneResponse, status_code=201)
async def clone_event(
    event_id: int,
    data: EventCloneRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> EventCloneResponse:
    """Clone an existing event into a new draft event.

    Copies config and game setup — but NOT enrollments, catches, lineups, matches,
    game cards, standings, bracket, contestations, scoreboards, or teams.
    """
    now = datetime.now(timezone.utc)
    tomorrow = now + timedelta(days=1)

    start = data.start_date or tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
    end = data.end_date or tomorrow.replace(hour=18, minute=0, second=0, microsecond=0)
    deadline = data.registration_deadline or now.replace(hour=23, minute=59, second=0, microsecond=0)

    # ── 1. Load source event ──────────────────────────────────────────────────
    result = await db.execute(
        select(Event).where(Event.id == event_id, Event.is_deleted == False)
    )
    source = result.scalar_one_or_none()

    if not source:
        raise HTTPException(status_code=404, detail="Event not found or deleted")

    format_code = source.event_type.format_code  # "ta" or "sf"

    # Load related config via separate queries (avoids async lazy-load issues).
    ta_settings = None
    ta_point_config = None
    fish_scoring = []
    species_bonus = []

    if format_code == "ta":
        ts_res = await db.execute(
            select(TAEventSettings).where(TAEventSettings.event_id == event_id)
        )
        ta_settings = ts_res.scalar_one_or_none()

        pc_res = await db.execute(
            select(TAEventPointConfig).where(TAEventPointConfig.event_id == event_id)
        )
        ta_point_config = pc_res.scalar_one_or_none()
    else:
        fs_res = await db.execute(
            select(EventFishScoring).where(EventFishScoring.event_id == event_id)
        )
        fish_scoring = fs_res.scalars().all()

        sb_res = await db.execute(
            select(EventSpeciesBonusPoints).where(EventSpeciesBonusPoints.event_id == event_id)
        )
        species_bonus = sb_res.scalars().all()

    prizes_res = await db.execute(
        select(EventPrize).where(EventPrize.event_id == event_id)
    )
    prizes = prizes_res.scalars().all()

    rules_res = await db.execute(
        select(EventScoringRule).where(EventScoringRule.event_id == event_id)
    )
    scoring_rules = rules_res.scalars().all()

    # ── 2. Create new Event ───────────────────────────────────────────────────
    new_name = data.name or f"{source.name} (copy)"
    new_slug = _make_slug(new_name, int(now.timestamp()))

    new_event = Event(
        name=new_name,
        slug=new_slug,
        description=source.description,
        event_type_id=source.event_type_id,
        scoring_config_id=source.scoring_config_id,
        start_date=start,
        end_date=end,
        registration_deadline=deadline,
        location_id=source.location_id,
        location_name=source.location_name,
        meeting_point_lat=source.meeting_point_lat,
        meeting_point_lng=source.meeting_point_lng,
        meeting_point_address=source.meeting_point_address,
        created_by_id=source.created_by_id,
        billing_profile_id=source.billing_profile_id,
        status="draft",
        max_participants=source.max_participants,
        requires_approval=source.requires_approval,
        top_x_overall=source.top_x_overall,
        has_bonus_points=source.has_bonus_points,
        is_team_event=source.is_team_event,
        is_national_event=source.is_national_event,
        is_tournament_event=source.is_tournament_event,
        min_team_size=source.min_team_size,
        max_team_size=source.max_team_size,
        rule_id=source.rule_id,
        rules=source.rules,
        image_url=source.image_url,
        allow_gallery_upload=source.allow_gallery_upload,
        allowed_media_type=source.allowed_media_type,
        max_video_duration=source.max_video_duration,
        use_ai_analysis=source.use_ai_analysis,
        use_ml_auto_validation=source.use_ml_auto_validation,
        ml_confidence_threshold=source.ml_confidence_threshold,
        participation_fee=source.participation_fee,
        participation_fee_currency_id=source.participation_fee_currency_id,
        is_test=source.is_test,
        created_at=now,
        updated_at=now,
        published_at=None,
        completed_at=None,
        is_deleted=False,
        deleted_at=None,
        deleted_by_id=None,
    )
    db.add(new_event)
    await db.flush()  # populate new_event.id

    # ── 3a. TA: copy TAEventSettings + TAEventPointConfig ─────────────────────
    if format_code == "ta":
        if ta_settings:
            clean_rules = {
                k: v
                for k, v in (ta_settings.additional_rules or {}).items()
                if k not in DRAW_TRACKING_KEYS
            }
            db.add(TAEventSettings(
                event_id=new_event.id,
                number_of_legs=ta_settings.number_of_legs,
                max_rounds_per_leg=ta_settings.max_rounds_per_leg,
                has_knockout_stage=ta_settings.has_knockout_stage,
                knockout_qualifiers=ta_settings.knockout_qualifiers,
                has_requalification=ta_settings.has_requalification,
                requalification_slots=ta_settings.requalification_slots,
                direct_to_semifinal=ta_settings.direct_to_semifinal,
                direct_placement_from=ta_settings.direct_placement_from,
                is_team_event=ta_settings.is_team_event,
                team_size=ta_settings.team_size,
                team_scoring_method=ta_settings.team_scoring_method,
                require_both_validation=ta_settings.require_both_validation,
                auto_validate_ghost=ta_settings.auto_validate_ghost,
                dispute_resolution_timeout_hours=ta_settings.dispute_resolution_timeout_hours,
                match_duration_minutes=ta_settings.match_duration_minutes,
                break_between_legs_minutes=ta_settings.break_between_legs_minutes,
                additional_rules=clean_rules,
            ))

        if ta_point_config:
            db.add(TAEventPointConfig(
                event_id=new_event.id,
                victory_points=ta_point_config.victory_points,
                tie_points=ta_point_config.tie_points,
                tie_zero_points=ta_point_config.tie_zero_points,
                loss_points=ta_point_config.loss_points,
                loss_zero_points=ta_point_config.loss_zero_points,
            ))

    # ── 3b. SF: copy EventFishScoring + EventSpeciesBonusPoints ───────────────
    else:
        for fs in fish_scoring:
            db.add(EventFishScoring(
                event_id=new_event.id,
                fish_id=fs.fish_id,
                accountable_catch_slots=fs.accountable_catch_slots,
                accountable_min_length=fs.accountable_min_length,
                under_min_length_points=fs.under_min_length_points,
                top_x_catches=fs.top_x_catches,
                display_order=fs.display_order,
            ))

        for sb in species_bonus:
            db.add(EventSpeciesBonusPoints(
                event_id=new_event.id,
                species_count=sb.species_count,
                bonus_points=sb.bonus_points,
            ))

    # ── 4. Always copy EventPrize + EventScoringRule ──────────────────────────
    for prize in prizes:
        db.add(EventPrize(
            event_id=new_event.id,
            place=prize.place,
            title=prize.title,
            description=prize.description,
            value=prize.value,
            image_url=prize.image_url,
        ))

    for rule in scoring_rules:
        db.add(EventScoringRule(
            event_id=new_event.id,
            fish_id=rule.fish_id,
            min_length=rule.min_length,
            max_length=rule.max_length,
            points_per_cm=rule.points_per_cm,
            bonus_points=rule.bonus_points,
            points_formula=rule.points_formula,
        ))

    await db.commit()
    await db.refresh(new_event)

    return EventCloneResponse(
        id=new_event.id,
        name=new_event.name,
        slug=new_event.slug,
        status=new_event.status,
        format_code=format_code,
        start_date=new_event.start_date,
        end_date=new_event.end_date,
        registration_deadline=new_event.registration_deadline,
    )
