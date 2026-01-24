"""Recommendations service for personalized event and angler suggestions."""

import logging
import math
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, func, and_, distinct
from sqlalchemy.exc import IntegrityError
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


def reason(key: str, **kwargs: Any) -> dict:
    """Create a reason item with translation key and optional arguments."""
    return {"key": key, "args": kwargs if kwargs else None}


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
                # Enhanced v2 stats
                "average_catch_length": float(stats.average_catch_length or 0),
                "consecutive_events": stats.consecutive_events,
                "max_consecutive_events": stats.max_consecutive_events,
                "total_events_this_year": stats.total_events_this_year,
                "last_event_date": stats.last_event_date,
            }
        return {
            "total_events": 0,
            "total_catches": 0,
            "unique_species_count": 0,
            "largest_catch_cm": 0,
            "total_wins": 0,
            "podium_finishes": 0,
            # Enhanced v2 stats
            "average_catch_length": 0,
            "consecutive_events": 0,
            "max_consecutive_events": 0,
            "total_events_this_year": 0,
            "last_event_date": None,
        }

    def _get_ml_insights(
        self,
        ml_score: float,
        user_stats: dict,
        user_event_types: set[int],
        event_type_id: int,
        enrollment_count: int,
    ) -> dict:
        """Generate ML insights and translatable factors."""
        # Confidence label (translation key)
        if ml_score >= 0.9:
            confidence_label = "confidence_very_high"
        elif ml_score >= 0.7:
            confidence_label = "confidence_high"
        elif ml_score >= 0.5:
            confidence_label = "confidence_moderate"
        elif ml_score >= 0.3:
            confidence_label = "confidence_low"
        else:
            confidence_label = "confidence_very_low"

        # Generate factor explanations (as reason items)
        factors = []

        # Experience factors
        total_events = user_stats.get("total_events", 0)
        total_catches = user_stats.get("total_catches", 0)
        total_wins = user_stats.get("total_wins", 0)

        if total_events >= 10:
            factors.append(reason("experienced_competitor"))
        elif total_events >= 5:
            factors.append(reason("active_participant"))
        elif total_events >= 1:
            factors.append(reason("getting_started"))

        if total_catches >= 50:
            factors.append(reason("prolific_angler"))
        elif total_catches >= 20:
            factors.append(reason("skilled_catcher"))

        if total_wins >= 3:
            factors.append(reason("multiple_event_winner"))
        elif total_wins >= 1:
            factors.append(reason("previous_winner"))

        # Event type match
        if event_type_id in user_event_types:
            factors.append(reason("matches_event_history"))

        # Popularity
        if enrollment_count >= 30:
            factors.append(reason("highly_popular_event"))
        elif enrollment_count >= 15:
            factors.append(reason("growing_interest"))

        # Ensure we have at least one factor
        if not factors:
            if ml_score >= 0.7:
                factors.append(reason("based_on_profile"))
            else:
                factors.append(reason("new_opportunity"))

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

        Automatically detects v1 vs v2 model based on loaded features.
        """
        try:
            # Check which model version is loaded by looking at features
            _, _, feature_names, _ = await self.ml_service.load_active_model("event_recommendations")

            # Prepare event data
            event_data = {
                "start_date": event.start_date,
                "event_type_id": event.event_type_id,
                "day_of_week": event.start_date.weekday() if event.start_date else 0,
                "month": event.start_date.month if event.start_date else 1,
                "enrollment_count": enrollment_count,
                "friends_enrolled": friends_count,
            }

            # Use v2 features if model has Hall of Fame features
            if "hof_entry_count" in feature_names:
                features = await self.ml_service.build_event_features_v2(
                    user_id=user.id,
                    user_stats=user_stats,
                    user_created_at=user.created_at,
                    event_data=event_data,
                )
            else:
                features = await self.ml_service.build_event_features(
                    user_stats=user_stats,
                    user_created_at=user.created_at,
                    event_data=event_data,
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
        """Get event type IDs the user has participated in (exclude test events)."""
        stmt = (
            select(distinct(Event.event_type_id))
            .join(EventEnrollment, EventEnrollment.event_id == Event.id)
            .where(
                EventEnrollment.user_id == user_id,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value,
                Event.is_test == False,
            )
        )
        result = await self.db.execute(stmt)
        return {row[0] for row in result.all()}

    async def get_user_caught_species(self, user_id: int) -> set[int]:
        """Get fish species IDs the user has caught (exclude test events)."""
        stmt = (
            select(distinct(Catch.fish_id))
            .join(Event, Catch.event_id == Event.id)
            .where(
                Catch.user_id == user_id,
                Catch.fish_id.isnot(None),
                Event.is_test == False,
            )
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
    ) -> tuple[float, list[dict], list[dict]]:
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
                    reasons.append(reason("near_you", distance=int(distance_km)))
                elif distance_km <= 25:
                    score += 25
                    reasons.append(reason("near_you", distance=int(distance_km)))
                elif distance_km <= 50:
                    score += 20
                    reasons.append(reason("distance_away", distance=int(distance_km)))
                elif distance_km <= 100:
                    score += 10

        # 2. Event type match (max 25 points)
        if event.event_type_id in user_event_types:
            score += 25
            type_name = await self.get_event_type_name(event.event_type_id)
            if type_name:
                reasons.append(reason("event_type_match", type_name=type_name))

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
                reasons.append(reason("species_match", species=", ".join(species_names)))

        # 4. Friends joining (max 15 points) - Pro feature
        if is_pro:
            friends_enrolled = await self.get_following_enrolled_in_event(
                user.id, event.id
            )
            if friends_enrolled:
                score += min(15, len(friends_enrolled) * 5)
                if len(friends_enrolled) == 1:
                    reasons.append(reason("friend_joining", name=friends_enrolled[0]['name']))
                else:
                    reasons.append(reason("friends_joining", count=len(friends_enrolled)))

        # 5. Popularity boost (max 10 points)
        enrollment_count = await self.get_event_enrollment_count(event.id)
        if enrollment_count >= 20:
            score += 10
            reasons.append(reason("popular_event"))
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

        # Get upcoming published events (exclude test events)
        now = datetime.now(timezone.utc)
        stmt = (
            select(Event)
            .options(joinedload(Event.location), joinedload(Event.event_type))
            .where(
                Event.status == "published",
                Event.start_date > now,
                Event.is_deleted.is_(False),
                Event.is_test.is_(False),
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
        """Get events that both users participated in (exclude test events)."""
        stmt = (
            select(Event)
            .join(EventEnrollment, EventEnrollment.event_id == Event.id)
            .where(
                EventEnrollment.user_id == user_id,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value,
                Event.is_test == False,
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
    ) -> tuple[float, list[dict], list[dict]]:
        """
        Calculate recommendation score for an angler.
        Returns (score, reasons, mutual_friends) tuple.
        Max score: 100 points
        """
        score = 0.0
        reasons = []
        mutual_friends = []

        # Skip if profile is private
        if not candidate_profile.is_profile_public:
            return 0, [], []

        # 1. Same events attended (max 30 points)
        shared_events = await self.get_shared_events(user.id, candidate.id)
        if shared_events:
            score += min(30, len(shared_events) * 10)
            if len(shared_events) == 1:
                reasons.append(reason("met_at_event", event_name=shared_events[0]['name']))
            else:
                reasons.append(reason("shared_events", count=len(shared_events)))

        # 2. Mutual follows (max 25 points) - Pro feature shows details
        mutual_friends = await self.get_mutual_follows(user.id, candidate.id)
        if mutual_friends:
            score += min(25, len(mutual_friends) * 8)
            if is_pro:
                if len(mutual_friends) == 1:
                    reasons.append(reason("followed_by", name=mutual_friends[0]['name']))
                else:
                    reasons.append(reason("mutual_friends", count=len(mutual_friends)))
            else:
                reasons.append(reason("mutual_connections"))

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
                reasons.append(reason("also_catches", species=", ".join(species_names)))

        # 4. Location proximity (max 15 points)
        # Compare cities if available
        if (
            user.profile
            and user.profile.city_id
            and candidate_profile.city_id
            and user.profile.city_id == candidate_profile.city_id
        ):
            score += 15
            reasons.append(reason("nearby_angler"))

        # 5. Similar experience level (max 10 points)
        candidate_stats = await self.get_user_catch_count(candidate.id)
        if abs(user_stats.get("catches", 0) - candidate_stats["catches"]) < 20:
            score += 10
            reasons.append(reason("similar_experience"))

        return score, reasons, mutual_friends if is_pro else []

    async def _batch_get_shared_events(
        self, user_id: int, candidate_ids: list[int]
    ) -> dict[int, list[dict]]:
        """Batch fetch shared events between user and all candidates."""
        if not candidate_ids:
            return {}

        # Get events the user participated in
        user_events_stmt = (
            select(EventEnrollment.event_id)
            .where(
                EventEnrollment.user_id == user_id,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value,
            )
        )

        # Get shared events for all candidates in one query
        stmt = (
            select(EventEnrollment.user_id, Event.id, Event.name)
            .join(Event, Event.id == EventEnrollment.event_id)
            .where(
                EventEnrollment.user_id.in_(candidate_ids),
                EventEnrollment.status == EnrollmentStatus.APPROVED.value,
                Event.is_test == False,
                Event.id.in_(user_events_stmt),
            )
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        # Group by candidate_id
        shared: dict[int, list[dict]] = {cid: [] for cid in candidate_ids}
        for row in rows:
            if len(shared[row.user_id]) < 5:  # Limit to 5 per candidate
                shared[row.user_id].append({"id": row.id, "name": row.name})
        return shared

    async def _batch_get_mutual_follows(
        self, user_id: int, candidate_ids: list[int]
    ) -> dict[int, list[dict]]:
        """Batch fetch mutual follows for all candidates."""
        if not candidate_ids:
            return {}

        # Users that I follow
        my_following_stmt = (
            select(UserFollow.following_id)
            .where(UserFollow.follower_id == user_id)
        )

        # Find users I follow who also follow the candidates
        stmt = (
            select(
                UserFollow.following_id.label("candidate_id"),
                UserAccount.id,
                UserProfile.first_name,
                UserProfile.last_name,
                UserProfile.profile_picture_url,
            )
            .join(UserAccount, UserAccount.id == UserFollow.follower_id)
            .join(UserProfile, UserProfile.user_id == UserAccount.id)
            .where(
                UserFollow.following_id.in_(candidate_ids),
                UserFollow.follower_id.in_(my_following_stmt),
            )
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        # Group by candidate_id
        mutual: dict[int, list[dict]] = {cid: [] for cid in candidate_ids}
        for row in rows:
            if len(mutual[row.candidate_id]) < 5:
                mutual[row.candidate_id].append({
                    "id": row.id,
                    "name": f"{row.first_name} {row.last_name}",
                    "profile_picture_url": row.profile_picture_url,
                })
        return mutual

    async def _batch_get_species(
        self, candidate_ids: list[int]
    ) -> dict[int, set[int]]:
        """Batch fetch caught species for all candidates."""
        if not candidate_ids:
            return {}

        stmt = (
            select(Catch.user_id, Catch.fish_id)
            .join(Event, Catch.event_id == Event.id)
            .where(
                Catch.user_id.in_(candidate_ids),
                Catch.fish_id.isnot(None),
                Event.is_test == False,
            )
            .distinct()
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        species: dict[int, set[int]] = {cid: set() for cid in candidate_ids}
        for row in rows:
            species[row.user_id].add(row.fish_id)
        return species

    async def _batch_get_catch_counts(
        self, candidate_ids: list[int]
    ) -> dict[int, int]:
        """Batch fetch catch counts for all candidates."""
        if not candidate_ids:
            return {}

        stmt = (
            select(Catch.user_id, func.count(Catch.id).label("count"))
            .where(Catch.user_id.in_(candidate_ids))
            .group_by(Catch.user_id)
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        counts: dict[int, int] = {cid: 0 for cid in candidate_ids}
        for row in rows:
            counts[row.user_id] = row.count
        return counts

    def _calculate_angler_score_from_batch(
        self,
        user_id: int,
        candidate_id: int,
        candidate_profile: UserProfile,
        user_species: set[int],
        user_catch_count: int,
        shared_events: list[dict],
        mutual_friends: list[dict],
        candidate_species: set[int],
        candidate_catch_count: int,
        user_city_id: Optional[int],
        is_pro: bool,
        species_names: dict[int, str],
    ) -> tuple[float, list[dict], list[dict]]:
        """
        Calculate recommendation score using pre-fetched batch data.
        No DB queries - pure computation.
        """
        score = 0.0
        reasons = []

        # Skip if profile is private
        if not candidate_profile.is_profile_public:
            return 0, [], []

        # 1. Same events attended (max 30 points)
        if shared_events:
            score += min(30, len(shared_events) * 10)
            if len(shared_events) == 1:
                reasons.append(reason("met_at_event", event_name=shared_events[0]['name']))
            else:
                reasons.append(reason("shared_events", count=len(shared_events)))

        # 2. Mutual follows (max 25 points)
        if mutual_friends:
            score += min(25, len(mutual_friends) * 8)
            if is_pro:
                if len(mutual_friends) == 1:
                    reasons.append(reason("followed_by", name=mutual_friends[0]['name']))
                else:
                    reasons.append(reason("mutual_friends", count=len(mutual_friends)))
            else:
                reasons.append(reason("mutual_connections"))

        # 3. Similar species caught (max 20 points)
        overlap = user_species & candidate_species
        if overlap:
            score += min(20, len(overlap) * 5)
            overlap_list = list(overlap)[:2]
            names = [species_names.get(sid, "") for sid in overlap_list if species_names.get(sid)]
            if names:
                reasons.append(reason("also_catches", species=", ".join(names)))

        # 4. Location proximity (max 15 points)
        if user_city_id and candidate_profile.city_id == user_city_id:
            score += 15
            reasons.append(reason("nearby_angler"))

        # 5. Similar experience level (max 10 points)
        if abs(user_catch_count - candidate_catch_count) < 20:
            score += 10
            reasons.append(reason("similar_experience"))

        return score, reasons, mutual_friends if is_pro else []

    async def get_angler_recommendations(
        self,
        user: UserAccount,
        is_pro: bool,
        limit: int = 10,
    ) -> list[dict]:
        """Get personalized angler recommendations for user (optimized - no N+1)."""
        # Get user's species history and catch count
        user_species = await self.get_user_caught_species(user.id)
        user_stats = await self.get_user_catch_count(user.id)
        user_catch_count = user_stats.get("catches", 0)
        user_city_id = user.profile.city_id if user.profile else None

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
                UserAccount.is_active.is_(True),
                UserProfile.is_profile_public.is_(True),
                UserProfile.is_deleted.is_(False),
                UserAccount.id.notin_(dismissed | already_following),
                # Users who have at least one catch
                UserAccount.id.in_(
                    select(distinct(Catch.user_id))
                ),
            )
            .limit(100)
        )
        result = await self.db.execute(stmt)
        candidates = result.all()

        if not candidates:
            return []

        # Extract candidate IDs for batch queries
        candidate_ids = [row.UserAccount.id for row in candidates]

        # BATCH FETCH ALL DATA (eliminates N+1 problem)
        shared_events_map = await self._batch_get_shared_events(user.id, candidate_ids)
        mutual_friends_map = await self._batch_get_mutual_follows(user.id, candidate_ids)
        species_map = await self._batch_get_species(candidate_ids)
        catch_counts_map = await self._batch_get_catch_counts(candidate_ids)

        # Pre-fetch species names for overlap display
        all_species_ids = set()
        for species_set in species_map.values():
            all_species_ids |= species_set
        all_species_ids |= user_species

        species_names: dict[int, str] = {}
        if all_species_ids:
            names_stmt = select(Fish.id, Fish.name).where(Fish.id.in_(all_species_ids))
            names_result = await self.db.execute(names_stmt)
            species_names = {row.id: row.name for row in names_result.all()}

        # Score each candidate (NO DB QUERIES - pure computation)
        scored_anglers = []
        for row in candidates:
            candidate = row.UserAccount
            candidate_profile = row.UserProfile
            cid = candidate.id

            score, reasons, mutual = self._calculate_angler_score_from_batch(
                user_id=user.id,
                candidate_id=cid,
                candidate_profile=candidate_profile,
                user_species=user_species,
                user_catch_count=user_catch_count,
                shared_events=shared_events_map.get(cid, []),
                mutual_friends=mutual_friends_map.get(cid, []),
                candidate_species=species_map.get(cid, set()),
                candidate_catch_count=catch_counts_map.get(cid, 0),
                user_city_id=user_city_id,
                is_pro=is_pro,
                species_names=species_names,
            )

            if score > 0:
                scored_anglers.append({
                    "user": {
                        "id": cid,
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
        try:
            await self.db.commit()
        except IntegrityError:
            # Already dismissed, ignore
            await self.db.rollback()

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

        # Get user's completed events (approved enrollments only, exclude test)
        stmt = (
            select(Event)
            .join(EventEnrollment, EventEnrollment.event_id == Event.id)
            .options(joinedload(Event.event_type))
            .where(
                EventEnrollment.user_id == user.id,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value,
                Event.status == "completed",
                Event.is_deleted.is_(False),
                Event.is_test.is_(False),
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

    # ---- User Search (Pro only) ----

    async def search_users(
        self,
        query: str,
        current_user_id: int,
        limit: int = 20,
    ) -> list[dict]:
        """
        Search for users by name (PRO feature).
        Returns public profiles matching the query.
        """
        if len(query) < 2:
            return []

        # Normalize query for case-insensitive search
        search_pattern = f"%{query.lower()}%"

        # Get users I'm already following
        following_stmt = select(UserFollow.following_id).where(
            UserFollow.follower_id == current_user_id
        )
        following_result = await self.db.execute(following_stmt)
        following_ids = {row[0] for row in following_result.all()}

        # Search users by name (case-insensitive) - search in first_name or last_name
        from sqlalchemy import or_
        stmt = (
            select(UserAccount, UserProfile)
            .join(UserProfile, UserProfile.user_id == UserAccount.id)
            .where(
                UserAccount.id != current_user_id,
                UserAccount.is_active.is_(True),
                UserProfile.is_profile_public.is_(True),
                UserProfile.is_deleted.is_(False),
                or_(
                    func.lower(UserProfile.first_name).like(search_pattern),
                    func.lower(UserProfile.last_name).like(search_pattern),
                    func.lower(func.concat(UserProfile.first_name, ' ', UserProfile.last_name)).like(search_pattern),
                ),
            )
            .order_by(UserProfile.first_name, UserProfile.last_name)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        users = result.all()

        if not users:
            return []

        # Batch fetch follower counts
        user_ids = [row.UserAccount.id for row in users]
        follower_counts_stmt = (
            select(UserFollow.following_id, func.count(UserFollow.id).label("count"))
            .where(UserFollow.following_id.in_(user_ids))
            .group_by(UserFollow.following_id)
        )
        follower_result = await self.db.execute(follower_counts_stmt)
        follower_counts = {row.following_id: row.count for row in follower_result.all()}

        # Build response
        results = []
        for row in users:
            user = row.UserAccount
            profile = row.UserProfile

            # Get city name for location
            location = None
            if profile.city:
                location = profile.city.name

            results.append({
                "id": user.id,
                "name": profile.full_name,
                "profile_picture_url": profile.profile_picture_url,
                "location": location,
                "follower_count": follower_counts.get(user.id, 0),
                "is_following": user.id in following_ids,
            })

        return results
