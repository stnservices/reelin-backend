"""Recommendations service for personalized event and angler suggestions."""

import logging
import math
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func, and_, distinct
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.catch import Catch
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.event import Event, EventType
from app.models.fish import Fish
from app.models.follow import UserFollow
from app.models.location import FishingSpot
from app.models.recommendation import RecommendationDismissal
from app.models.statistics import UserEventTypeStats
from app.models.user import UserAccount, UserProfile

logger = logging.getLogger(__name__)


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great-circle distance between two points on Earth.
    Returns distance in kilometers.
    """
    R = 6371  # Earth's radius in km

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


class RecommendationsService:
    """Service for generating personalized recommendations."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._ml_service = None

    @property
    def ml_service(self):
        """Lazy load ML service to avoid circular imports."""
        if self._ml_service is None:
            from app.services.ml_service import MLService
            self._ml_service = MLService(self.db)
        return self._ml_service

    async def get_user_stats(self, user_id: int) -> dict:
        """Get user overall stats from user_event_type_stats."""
        stmt = select(UserEventTypeStats).where(
            UserEventTypeStats.user_id == user_id,
            UserEventTypeStats.event_type_id.is_(None),  # Overall stats
        )
        result = await self.db.execute(stmt)
        stats = result.scalar_one_or_none()

        if stats:
            return {
                "total_events": stats.total_events,
                "total_catches": stats.total_catches,
                "unique_species_count": stats.unique_species_count,
                "largest_catch_cm": float(stats.largest_catch_cm or 0),
                "total_wins": stats.total_wins,
                "podium_finishes": stats.podium_finishes,
            }
        return {
            "total_events": 0,
            "total_catches": 0,
            "unique_species_count": 0,
            "largest_catch_cm": 0,
            "total_wins": 0,
            "podium_finishes": 0,
        }

    def _get_ml_insights(
        self,
        ml_score: float,
        user_stats: dict,
        user_event_types: set[int],
        event_type_id: int,
        enrollment_count: int,
    ) -> dict:
        """Generate ML insights and human-readable factors."""
        # Confidence label
        if ml_score >= 0.9:
            confidence_label = "Very High"
        elif ml_score >= 0.7:
            confidence_label = "High"
        elif ml_score >= 0.5:
            confidence_label = "Moderate"
        elif ml_score >= 0.3:
            confidence_label = "Low"
        else:
            confidence_label = "Very Low"

        # Generate factor explanations
        factors = []

        # Experience factors
        total_events = user_stats.get("total_events", 0)
        total_catches = user_stats.get("total_catches", 0)
        total_wins = user_stats.get("total_wins", 0)

        if total_events >= 10:
            factors.append("Experienced competitor")
        elif total_events >= 5:
            factors.append("Active participant")
        elif total_events >= 1:
            factors.append("Getting started")

        if total_catches >= 50:
            factors.append("Prolific angler")
        elif total_catches >= 20:
            factors.append("Skilled catcher")

        if total_wins >= 3:
            factors.append("Multiple event winner")
        elif total_wins >= 1:
            factors.append("Previous winner")

        # Event type match
        if event_type_id in user_event_types:
            factors.append("Matches your event history")

        # Popularity
        if enrollment_count >= 30:
            factors.append("Highly popular event")
        elif enrollment_count >= 15:
            factors.append("Growing interest")

        # Ensure we have at least one factor
        if not factors:
            if ml_score >= 0.7:
                factors.append("Based on your profile")
            else:
                factors.append("New opportunity")

        return {
            "confidence": ml_score,
            "confidence_label": confidence_label,
            "factors": factors[:4],  # Max 4 factors
        }

    async def get_ml_event_score(
        self,
        user: UserAccount,
        event: Event,
        user_stats: dict,
        user_event_types: set[int],
        enrollment_count: int,
        friends_count: int,
    ) -> tuple[Optional[float], Optional[dict]]:
        """
        Get ML model prediction for event enrollment probability.
        Returns (score, insights) tuple. Both None if ML model is not available.
        """
        try:
            features = await self.ml_service.build_event_features(
                user_stats=user_stats,
                user_created_at=user.created_at,
                event_data={
                    "start_date": event.start_date,
                    "event_type_id": event.event_type_id,
                    "day_of_week": event.start_date.weekday() if event.start_date else 0,
                    "month": event.start_date.month if event.start_date else 1,
                    "enrollment_count": enrollment_count,
                    "friends_enrolled": friends_count,
                },
            )
            features["has_done_event_type"] = 1 if event.event_type_id in user_event_types else 0

            score = await self.ml_service.predict_event_enrollment(
                user_id=user.id,
                event_id=event.id,
                features=features,
                log_prediction=True,
            )

            if score is not None:
                insights = self._get_ml_insights(
                    score, user_stats, user_event_types,
                    event.event_type_id, enrollment_count
                )
                return score, insights

            return None, None
        except Exception as e:
            logger.warning(f"ML prediction failed: {e}")
            return None, None

    async def get_user_participated_event_types(self, user_id: int) -> set[int]:
        """Get event type IDs the user has participated in."""
        stmt = (
            select(distinct(Event.event_type_id))
            .join(EventEnrollment, EventEnrollment.event_id == Event.id)
            .where(
                EventEnrollment.user_id == user_id,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value,
            )
        )
        result = await self.db.execute(stmt)
        return {row[0] for row in result.all()}

    async def get_user_caught_species(self, user_id: int) -> set[int]:
        """Get fish species IDs the user has caught."""
        stmt = select(distinct(Catch.fish_id)).where(
            Catch.user_id == user_id,
            Catch.fish_id.isnot(None),
        )
        result = await self.db.execute(stmt)
        return {row[0] for row in result.all()}

    async def get_following_enrolled_in_event(
        self, user_id: int, event_id: int
    ) -> list[dict]:
        """Get users that current user follows who are enrolled in event."""
        stmt = (
            select(UserAccount, UserProfile)
            .join(UserFollow, UserFollow.following_id == UserAccount.id)
            .join(EventEnrollment, EventEnrollment.user_id == UserAccount.id)
            .join(UserProfile, UserProfile.user_id == UserAccount.id)
            .where(
                UserFollow.follower_id == user_id,
                EventEnrollment.event_id == event_id,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value,
            )
            .limit(5)
        )
        result = await self.db.execute(stmt)
        rows = result.all()
        return [
            {
                "id": row.UserAccount.id,
                "name": row.UserProfile.full_name,
                "profile_picture_url": row.UserProfile.profile_picture_url,
            }
            for row in rows
        ]

    async def get_event_enrollment_count(self, event_id: int) -> int:
        """Get number of approved enrollments for an event."""
        stmt = select(func.count()).where(
            EventEnrollment.event_id == event_id,
            EventEnrollment.status == EnrollmentStatus.APPROVED.value,
        )
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    async def get_species_name(self, species_id: int) -> Optional[str]:
        """Get species name by ID."""
        stmt = select(Fish.name).where(Fish.id == species_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_event_type_name(self, event_type_id: int) -> Optional[str]:
        """Get event type name by ID."""
        stmt = select(EventType.name).where(EventType.id == event_type_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_dismissed_items(
        self, user_id: int, item_type: str
    ) -> set[int]:
        """Get IDs of dismissed recommendations."""
        stmt = select(RecommendationDismissal.item_id).where(
            RecommendationDismissal.user_id == user_id,
            RecommendationDismissal.item_type == item_type,
        )
        result = await self.db.execute(stmt)
        return {row[0] for row in result.all()}

    async def calculate_event_score(
        self,
        user: UserAccount,
        event: Event,
        user_lat: Optional[float],
        user_lng: Optional[float],
        user_event_types: set[int],
        user_species: set[int],
        is_pro: bool,
    ) -> tuple[float, list[str], list[dict]]:
        """
        Calculate recommendation score for an event.
        Returns (score, reasons, friends_enrolled) tuple.
        Max score: 100 points
        """
        score = 0.0
        reasons = []
        friends_enrolled = []

        # 1. Location proximity (max 30 points)
        if user_lat and user_lng and event.location:
            event_lat = event.location.latitude
            event_lng = event.location.longitude
            if event_lat and event_lng:
                distance_km = haversine_distance(user_lat, user_lng, event_lat, event_lng)
                if distance_km <= 10:
                    score += 30
                    reasons.append(f"Near you ({distance_km:.0f}km)")
                elif distance_km <= 25:
                    score += 25
                    reasons.append(f"Near you ({distance_km:.0f}km)")
                elif distance_km <= 50:
                    score += 20
                    reasons.append(f"{distance_km:.0f}km away")
                elif distance_km <= 100:
                    score += 10

        # 2. Event type match (max 25 points)
        if event.event_type_id in user_event_types:
            score += 25
            type_name = await self.get_event_type_name(event.event_type_id)
            if type_name:
                reasons.append(f"You've done {type_name} before")

        # 3. Species match (max 20 points)
        # Get species from fish_scoring relationship if available
        event_species = set()
        if hasattr(event, 'fish_scoring') and event.fish_scoring:
            for fs in event.fish_scoring:
                if fs.fish_id:
                    event_species.add(fs.fish_id)
        overlap = user_species & event_species
        if overlap:
            score += min(20, len(overlap) * 7)
            overlap_list = list(overlap)[:2]
            species_names = []
            for sid in overlap_list:
                name = await self.get_species_name(sid)
                if name:
                    species_names.append(name)
            if species_names:
                reasons.append(f"You've caught {', '.join(species_names)}")

        # 4. Friends joining (max 15 points) - Pro feature
        if is_pro:
            friends_enrolled = await self.get_following_enrolled_in_event(
                user.id, event.id
            )
            if friends_enrolled:
                score += min(15, len(friends_enrolled) * 5)
                if len(friends_enrolled) == 1:
                    reasons.append(f"{friends_enrolled[0]['name']} is joining")
                else:
                    reasons.append(f"{len(friends_enrolled)} friends joining")

        # 5. Popularity boost (max 10 points)
        enrollment_count = await self.get_event_enrollment_count(event.id)
        if enrollment_count >= 20:
            score += 10
            reasons.append("Popular event")
        elif enrollment_count >= 10:
            score += 5

        return score, reasons, friends_enrolled

    async def get_event_recommendations(
        self,
        user: UserAccount,
        is_pro: bool,
        limit: int = 10,
        use_ml: bool = True,
    ) -> list[dict]:
        """Get personalized event recommendations for user."""
        # Get user location from profile
        user_lat = None
        user_lng = None
        if user.profile and user.profile.city:
            # Use city coordinates as user location
            city = user.profile.city
            if hasattr(city, 'latitude') and hasattr(city, 'longitude'):
                user_lat = city.latitude
                user_lng = city.longitude

        # Get user's event type and species history
        user_event_types = await self.get_user_participated_event_types(user.id)
        user_species = await self.get_user_caught_species(user.id)

        # Get user stats for ML
        user_stats = await self.get_user_stats(user.id) if use_ml else {}

        # Get dismissed events
        dismissed = await self.get_dismissed_items(user.id, "event")

        # Get upcoming published events
        now = datetime.now(timezone.utc)
        stmt = (
            select(Event)
            .options(joinedload(Event.location), joinedload(Event.event_type))
            .where(
                Event.status == "published",
                Event.start_date > now,
                Event.is_deleted == False,
            )
            .order_by(Event.start_date)
            .limit(100)
        )
        result = await self.db.execute(stmt)
        events = result.unique().scalars().all()

        # Score each event
        scored_events = []
        for event in events:
            # Skip dismissed
            if event.id in dismissed:
                continue

            score, reasons, friends = await self.calculate_event_score(
                user, event, user_lat, user_lng,
                user_event_types, user_species, is_pro
            )

            # Add ML-enhanced scoring if available
            ml_score = None
            ml_insights = None
            if use_ml:
                enrollment_count = await self.get_event_enrollment_count(event.id)
                friends_count = len(friends) if friends else 0
                ml_score, ml_insights = await self.get_ml_event_score(
                    user, event, user_stats, user_event_types,
                    enrollment_count, friends_count
                )
                if ml_score is not None:
                    # Combine: 60% rule-based + 40% ML (scaled to 0-100)
                    score = score * 0.6 + (ml_score * 100) * 0.4
                    # Add ML-based reasons from insights
                    if ml_insights and ml_insights.get("factors"):
                        for factor in ml_insights["factors"][:2]:
                            if factor not in reasons:
                                reasons.append(factor)

            if score > 0:
                scored_events.append({
                    "event": event,
                    "score": score,
                    "ml_score": ml_score,
                    "ml_insights": ml_insights,
                    "reasons": reasons,
                    "friends_enrolled": friends if is_pro else None,
                })

        # Sort by score descending
        scored_events.sort(key=lambda x: x["score"], reverse=True)

        # Limit for free users
        if not is_pro:
            scored_events = scored_events[:3]
        else:
            scored_events = scored_events[:limit]

        return scored_events

    # ---- Angler Recommendations ----

    async def get_shared_events(self, user_id: int, candidate_id: int) -> list[dict]:
        """Get events that both users participated in."""
        stmt = (
            select(Event)
            .join(EventEnrollment, EventEnrollment.event_id == Event.id)
            .where(
                EventEnrollment.user_id == user_id,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value,
                Event.id.in_(
                    select(EventEnrollment.event_id).where(
                        EventEnrollment.user_id == candidate_id,
                        EventEnrollment.status == EnrollmentStatus.APPROVED.value,
                    )
                ),
            )
            .limit(5)
        )
        result = await self.db.execute(stmt)
        events = result.scalars().all()
        return [{"id": e.id, "name": e.name} for e in events]

    async def get_mutual_follows(
        self, user_id: int, candidate_id: int
    ) -> list[dict]:
        """Get users who follow both user and candidate (mutual connections)."""
        # Users that I follow who also follow the candidate
        stmt = (
            select(UserAccount, UserProfile)
            .join(UserFollow, UserFollow.following_id == UserAccount.id)
            .join(UserProfile, UserProfile.user_id == UserAccount.id)
            .where(
                UserFollow.follower_id == user_id,
                UserAccount.id.in_(
                    select(UserFollow.follower_id).where(
                        UserFollow.following_id == candidate_id
                    )
                ),
            )
            .limit(5)
        )
        result = await self.db.execute(stmt)
        rows = result.all()
        return [
            {
                "id": row.UserAccount.id,
                "name": row.UserProfile.full_name,
                "profile_picture_url": row.UserProfile.profile_picture_url,
            }
            for row in rows
        ]

    async def is_following(self, follower_id: int, following_id: int) -> bool:
        """Check if user is following another user."""
        stmt = select(UserFollow.id).where(
            UserFollow.follower_id == follower_id,
            UserFollow.following_id == following_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_user_catch_count(self, user_id: int) -> dict:
        """Get basic user catch count for comparison."""
        catch_count = await self.db.execute(
            select(func.count()).where(Catch.user_id == user_id)
        )
        return {"catches": catch_count.scalar() or 0}

    async def calculate_angler_score(
        self,
        user: UserAccount,
        candidate: UserAccount,
        candidate_profile: UserProfile,
        user_species: set[int],
        user_stats: dict,
        is_pro: bool,
    ) -> tuple[float, list[str], list[dict]]:
        """
        Calculate recommendation score for an angler.
        Returns (score, reasons, mutual_friends) tuple.
        Max score: 100 points
        """
        score = 0.0
        reasons = []
        mutual_friends = []

        # Skip if already following
        if await self.is_following(user.id, candidate.id):
            return 0, [], []

        # Skip if profile is private
        if not candidate_profile.is_profile_public:
            return 0, [], []

        # 1. Same events attended (max 30 points)
        shared_events = await self.get_shared_events(user.id, candidate.id)
        if shared_events:
            score += min(30, len(shared_events) * 10)
            if len(shared_events) == 1:
                reasons.append(f"Met at {shared_events[0]['name']}")
            else:
                reasons.append(f"Attended {len(shared_events)} same events")

        # 2. Mutual follows (max 25 points) - Pro feature shows details
        mutual_friends = await self.get_mutual_follows(user.id, candidate.id)
        if mutual_friends:
            score += min(25, len(mutual_friends) * 8)
            if is_pro:
                if len(mutual_friends) == 1:
                    reasons.append(f"Followed by {mutual_friends[0]['name']}")
                else:
                    reasons.append(f"{len(mutual_friends)} mutual friends")
            else:
                reasons.append("Mutual connections")

        # 3. Similar species caught (max 20 points)
        candidate_species = await self.get_user_caught_species(candidate.id)
        overlap = user_species & candidate_species
        if overlap:
            score += min(20, len(overlap) * 5)
            overlap_list = list(overlap)[:2]
            species_names = []
            for sid in overlap_list:
                name = await self.get_species_name(sid)
                if name:
                    species_names.append(name)
            if species_names:
                reasons.append(f"Also catches {', '.join(species_names)}")

        # 4. Location proximity (max 15 points)
        # Compare cities if available
        if (
            user.profile
            and user.profile.city_id
            and candidate_profile.city_id
            and user.profile.city_id == candidate_profile.city_id
        ):
            score += 15
            reasons.append("Nearby angler")

        # 5. Similar experience level (max 10 points)
        candidate_stats = await self.get_user_catch_count(candidate.id)
        if abs(user_stats.get("catches", 0) - candidate_stats["catches"]) < 20:
            score += 10
            reasons.append("Similar experience level")

        return score, reasons, mutual_friends if is_pro else []

    async def get_angler_recommendations(
        self,
        user: UserAccount,
        is_pro: bool,
        limit: int = 10,
    ) -> list[dict]:
        """Get personalized angler recommendations for user."""
        # Get user's species history and catch count
        user_species = await self.get_user_caught_species(user.id)
        user_stats = await self.get_user_catch_count(user.id)

        # Get dismissed anglers
        dismissed = await self.get_dismissed_items(user.id, "angler")

        # Get users I'm already following
        following_stmt = select(UserFollow.following_id).where(
            UserFollow.follower_id == user.id
        )
        following_result = await self.db.execute(following_stmt)
        already_following = {row[0] for row in following_result.all()}

        # Get candidate anglers - users with public profiles who have catches
        stmt = (
            select(UserAccount, UserProfile)
            .join(UserProfile, UserProfile.user_id == UserAccount.id)
            .where(
                UserAccount.id != user.id,
                UserAccount.is_active == True,
                UserProfile.is_profile_public == True,
                UserProfile.is_deleted == False,
                # Users who have at least one catch
                UserAccount.id.in_(
                    select(distinct(Catch.user_id))
                ),
            )
            .limit(200)
        )
        result = await self.db.execute(stmt)
        candidates = result.all()

        # Score each candidate
        scored_anglers = []
        for row in candidates:
            candidate = row.UserAccount
            candidate_profile = row.UserProfile

            # Skip dismissed or already following
            if candidate.id in dismissed or candidate.id in already_following:
                continue

            score, reasons, mutual = await self.calculate_angler_score(
                user, candidate, candidate_profile,
                user_species, user_stats, is_pro
            )

            if score > 0:
                scored_anglers.append({
                    "user": {
                        "id": candidate.id,
                        "name": candidate_profile.full_name,
                        "profile_picture_url": candidate_profile.profile_picture_url,
                    },
                    "score": score,
                    "reasons": reasons,
                    "mutual_friends": mutual,
                })

        # Sort by score descending
        scored_anglers.sort(key=lambda x: x["score"], reverse=True)

        # Limit for free users
        if not is_pro:
            scored_anglers = scored_anglers[:3]
        else:
            scored_anglers = scored_anglers[:limit]

        return scored_anglers

    async def dismiss_recommendation(
        self, user_id: int, item_type: str, item_id: int
    ) -> None:
        """Dismiss a recommendation so it won't show again."""
        dismissal = RecommendationDismissal(
            user_id=user_id,
            item_type=item_type,
            item_id=item_id,
        )
        self.db.add(dismissal)
        await self.db.commit()

    # ---- Completed Event Insights ----

    async def get_completed_event_insights(
        self,
        user: UserAccount,
        limit: int = 20,
    ) -> list[dict]:
        """
        Get ML match insights for user's completed events.
        Shows how well the user matched each event they participated in.
        """
        from app.models.enrollment import EventEnrollment, EnrollmentStatus

        # Get user stats and event type history
        user_stats = await self.get_user_stats(user.id)
        user_event_types = await self.get_user_participated_event_types(user.id)

        # Get user's completed events (approved enrollments only)
        stmt = (
            select(Event)
            .join(EventEnrollment, EventEnrollment.event_id == Event.id)
            .options(joinedload(Event.event_type))
            .where(
                EventEnrollment.user_id == user.id,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value,
                Event.status == "completed",
                Event.is_deleted == False,
            )
            .order_by(Event.end_date.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        events = result.unique().scalars().all()

        insights = []
        for event in events:
            # Get enrollment count for this event
            enrollment_count = await self.get_event_enrollment_count(event.id)

            # Get friends who were enrolled
            friends_count = len(
                await self.get_following_enrolled_in_event(user.id, event.id)
            )

            # Get ML score and insights
            ml_score, ml_insights = await self.get_ml_event_score(
                user, event, user_stats, user_event_types,
                enrollment_count, friends_count
            )

            if ml_score is not None:
                insights.append({
                    "event": {
                        "id": event.id,
                        "name": event.name,
                        "slug": event.slug,
                        "start_date": event.start_date,
                        "end_date": event.end_date,
                        "event_type_name": event.event_type.name if event.event_type else None,
                    },
                    "match_score": round(ml_score * 100),  # 0-100 percentage
                    "match_label": ml_insights["confidence_label"],
                    "factors": ml_insights["factors"],
                })

        return insights
