"""Personal analytics service for Pro users."""

from datetime import date, datetime, timedelta
from typing import Optional
from sqlalchemy import select, func, distinct, and_, extract, case, cast
from sqlalchemy.types import Numeric
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.catch import Catch, CatchStatus
from app.models.event import Event, EventStatus
from app.models.fish import Fish
from app.models.location import FishingSpot
from app.services.redis_cache import redis_cache


class AnalyticsService:
    """Service for computing personal catch analytics."""

    CACHE_TTL = 300  # 5 minutes

    @staticmethod
    def _get_date_range(period: str, start_date: Optional[date] = None, end_date: Optional[date] = None) -> tuple:
        """Get start and end dates based on period filter."""
        today = date.today()

        if period == "custom" and start_date and end_date:
            return start_date, end_date
        elif period == "week":
            return today - timedelta(days=7), today
        elif period == "month":
            return today.replace(day=1), today
        elif period == "year":
            return today.replace(month=1, day=1), today
        else:  # "all"
            return None, None

    async def get_basic_stats(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> dict:
        """
        Get basic stats for free users.
        Returns total catches, events, species + last 10 catches.
        """
        # Total catches (exclude test events)
        catches_stmt = (
            select(func.count(Catch.id))
            .join(Event, Catch.event_id == Event.id)
            .where(Catch.user_id == user_id)
            .where(Catch.status == CatchStatus.APPROVED.value)
            .where(Event.is_test == False)
        )
        result = await db.execute(catches_stmt)
        total_catches = result.scalar() or 0

        # Total events (exclude test events)
        events_stmt = (
            select(func.count(distinct(Catch.event_id)))
            .join(Event, Catch.event_id == Event.id)
            .where(Catch.user_id == user_id)
            .where(Catch.status == CatchStatus.APPROVED.value)
            .where(Event.is_test == False)
        )
        result = await db.execute(events_stmt)
        total_events = result.scalar() or 0

        # Total species (exclude test events)
        species_stmt = (
            select(func.count(distinct(Catch.fish_id)))
            .join(Event, Catch.event_id == Event.id)
            .where(Catch.user_id == user_id)
            .where(Catch.status == CatchStatus.APPROVED.value)
            .where(Event.is_test == False)
        )
        result = await db.execute(species_stmt)
        total_species = result.scalar() or 0

        # Total and average length (exclude test events)
        length_stmt = (
            select(func.sum(Catch.length), func.avg(Catch.length))
            .join(Event, Catch.event_id == Event.id)
            .where(Catch.user_id == user_id)
            .where(Catch.status == CatchStatus.APPROVED.value)
            .where(Event.is_test == False)
        )
        result = await db.execute(length_stmt)
        row = result.first()
        total_length = float(row[0]) if row and row[0] else 0.0
        avg_length = float(row[1]) if row and row[1] else 0.0

        # Last 10 catches (exclude test events)
        last_catches_stmt = (
            select(Catch)
            .join(Event, Catch.event_id == Event.id)
            .options(selectinload(Catch.fish), selectinload(Catch.event))
            .where(Catch.user_id == user_id)
            .where(Catch.status == CatchStatus.APPROVED.value)
            .where(Event.is_test == False)
            .order_by(Catch.submitted_at.desc())
            .limit(10)
        )
        result = await db.execute(last_catches_stmt)
        last_catches = result.scalars().all()

        return {
            "total_catches": total_catches,
            "total_events": total_events,
            "total_species": total_species,
            "total_length_cm": total_length,
            "average_length_cm": round(avg_length, 1),
            "biggest_catch": None,
            "personal_bests": [],
            "species_counts": [],
            "catches_by_hour": None,
            "catches_by_day_of_week": None,
            "catches_by_month": None,
            "best_time_of_day": None,
            "best_day_of_week": None,
            "top_locations": None,
            "catch_heatmap": None,
            "monthly_trend": None,
            "improvement_rate": None,
            "last_catches": [
                {
                    "id": c.id,
                    "species_name": c.fish.name if c.fish else "Unknown",
                    "length_cm": c.length,
                    "catch_date": c.submitted_at.date().isoformat(),
                    "event_name": c.event.name if c.event else None,
                }
                for c in last_catches
            ],
        }

    async def get_full_analytics(
        self,
        db: AsyncSession,
        user_id: int,
        period: str = "all",
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> dict:
        """
        Get full analytics for Pro users.
        """
        # Check cache first
        cache_key = f"analytics:{user_id}:{period}:{start_date}:{end_date}"
        cached = await redis_cache.get(cache_key)
        if cached:
            return cached

        date_start, date_end = self._get_date_range(period, start_date, end_date)

        # Build base filter (exclude test events)
        base_filter = [
            Catch.user_id == user_id,
            Catch.status == CatchStatus.APPROVED.value,
            Event.is_test == False,
        ]
        if date_start:
            base_filter.append(Catch.submitted_at >= datetime.combine(date_start, datetime.min.time()))
        if date_end:
            base_filter.append(Catch.submitted_at <= datetime.combine(date_end, datetime.max.time()))

        # === Overview stats ===
        overview = await self._get_overview_stats(db, base_filter)

        # === Species breakdown ===
        species_counts = await self._get_species_breakdown(db, base_filter)

        # === Personal bests ===
        personal_bests = await self._get_personal_bests(db, user_id)

        # === Time analysis ===
        catches_by_hour = await self._get_catches_by_hour(db, base_filter)
        catches_by_day = await self._get_catches_by_day_of_week(db, base_filter)
        catches_by_month = await self._get_catches_by_month(db, base_filter)

        # === Location insights ===
        top_locations = await self._get_top_locations(db, base_filter)
        catch_heatmap = await self._get_catch_heatmap(db, base_filter)

        # === Trends ===
        monthly_trend = await self._get_monthly_trend(db, user_id)

        # Calculate best times
        best_hour = max(catches_by_hour.items(), key=lambda x: x[1])[0] if catches_by_hour else None
        best_day = max(catches_by_day.items(), key=lambda x: x[1])[0] if catches_by_day else None

        result = {
            **overview,
            "personal_bests": personal_bests,
            "species_counts": species_counts,
            "catches_by_hour": catches_by_hour,
            "catches_by_day_of_week": catches_by_day,
            "catches_by_month": catches_by_month,
            "best_time_of_day": self._hour_to_period(best_hour) if best_hour is not None else None,
            "best_day_of_week": best_day,
            "top_locations": top_locations,
            "catch_heatmap": catch_heatmap,
            "monthly_trend": monthly_trend,
            "improvement_rate": self._calculate_improvement(monthly_trend),
            "last_catches": None,  # Not needed for Pro
        }

        # Cache the result
        await redis_cache.set(cache_key, result, ttl=self.CACHE_TTL)

        return result

    async def _get_overview_stats(self, db: AsyncSession, base_filter: list) -> dict:
        """Get overview statistics."""
        # Total catches (join with Event for is_test filter)
        stmt = (
            select(func.count(Catch.id))
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*base_filter))
        )
        result = await db.execute(stmt)
        total_catches = result.scalar() or 0

        # Total events
        stmt = (
            select(func.count(distinct(Catch.event_id)))
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*base_filter))
        )
        result = await db.execute(stmt)
        total_events = result.scalar() or 0

        # Total species
        stmt = (
            select(func.count(distinct(Catch.fish_id)))
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*base_filter))
        )
        result = await db.execute(stmt)
        total_species = result.scalar() or 0

        # Total and average length
        stmt = (
            select(func.sum(Catch.length), func.avg(Catch.length))
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*base_filter))
        )
        result = await db.execute(stmt)
        row = result.first()
        total_length = float(row[0]) if row[0] else 0.0
        avg_length = float(row[1]) if row[1] else 0.0

        # Biggest catch
        stmt = (
            select(Catch)
            .join(Event, Catch.event_id == Event.id)
            .options(selectinload(Catch.fish), selectinload(Catch.event))
            .where(and_(*base_filter))
            .order_by(Catch.length.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        biggest = result.scalar_one_or_none()

        biggest_catch = None
        if biggest:
            biggest_catch = {
                "id": biggest.id,
                "species_id": biggest.fish_id,
                "species_name": biggest.fish.name if biggest.fish else "Unknown",
                "length_cm": biggest.length,
                "weight_kg": biggest.weight,
                "catch_date": biggest.submitted_at.date().isoformat(),
                "event_name": biggest.event.name if biggest.event else None,
                "photo_url": biggest.photo_url,
            }

        return {
            "total_catches": total_catches,
            "total_events": total_events,
            "total_species": total_species,
            "total_length_cm": total_length,
            "average_length_cm": round(avg_length, 1),
            "biggest_catch": biggest_catch,
        }

    async def _get_species_breakdown(self, db: AsyncSession, base_filter: list) -> list:
        """Get catches breakdown by species."""
        stmt = (
            select(
                Catch.fish_id,
                Fish.name,
                func.count(Catch.id).label("count"),
                func.avg(Catch.length).label("avg_length"),
                func.max(Catch.length).label("max_length"),
            )
            .join(Event, Catch.event_id == Event.id)
            .join(Fish, Catch.fish_id == Fish.id)
            .where(and_(*base_filter))
            .group_by(Catch.fish_id, Fish.name)
            .order_by(func.count(Catch.id).desc())
        )
        result = await db.execute(stmt)
        rows = result.fetchall()

        total = sum(r.count for r in rows)
        return [
            {
                "species_id": r.fish_id,
                "species_name": r.name,
                "count": r.count,
                "percentage": round(r.count * 100 / total, 1) if total > 0 else 0,
                "average_length": round(float(r.avg_length), 1) if r.avg_length else 0,
                "max_length": float(r.max_length) if r.max_length else 0,
            }
            for r in rows
        ]

    async def _get_personal_bests(self, db: AsyncSession, user_id: int) -> list:
        """Get personal best catch for each species (exclude test events)."""
        # Using a subquery to get the max length per species
        subquery = (
            select(Catch.fish_id, func.max(Catch.length).label("max_length"))
            .join(Event, Catch.event_id == Event.id)
            .where(Catch.user_id == user_id)
            .where(Catch.status == CatchStatus.APPROVED.value)
            .where(Event.is_test == False)
            .group_by(Catch.fish_id)
        ).subquery()

        stmt = (
            select(Catch)
            .join(Event, Catch.event_id == Event.id)
            .options(selectinload(Catch.fish), selectinload(Catch.event))
            .join(subquery, and_(
                Catch.fish_id == subquery.c.fish_id,
                Catch.length == subquery.c.max_length,
            ))
            .where(Catch.user_id == user_id)
            .where(Catch.status == CatchStatus.APPROVED.value)
            .where(Event.is_test == False)
            .order_by(Catch.length.desc())
        )
        result = await db.execute(stmt)
        catches = result.scalars().all()

        # Deduplicate by species (in case of ties)
        seen_species = set()
        personal_bests = []
        for c in catches:
            if c.fish_id not in seen_species:
                seen_species.add(c.fish_id)
                personal_bests.append({
                    "species_id": c.fish_id,
                    "species_name": c.fish.name if c.fish else "Unknown",
                    "length_cm": c.length,
                    "weight_kg": c.weight,
                    "catch_date": c.submitted_at.date().isoformat(),
                    "event_name": c.event.name if c.event else None,
                    "photo_url": c.photo_url,
                })

        return personal_bests

    async def _get_catches_by_hour(self, db: AsyncSession, base_filter: list) -> dict:
        """Get catch count by hour of day."""
        hour_expr = extract("hour", Catch.submitted_at)
        stmt = (
            select(
                hour_expr.label("hour"),
                func.count(Catch.id).label("count"),
            )
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*base_filter))
            .group_by(hour_expr)
            .order_by(hour_expr)
        )
        result = await db.execute(stmt)
        rows = result.fetchall()

        return {str(int(r.hour)): r.count for r in rows}

    async def _get_catches_by_day_of_week(self, db: AsyncSession, base_filter: list) -> dict:
        """Get catch count by day of week."""
        # PostgreSQL: 0 = Sunday, 6 = Saturday
        days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

        dow_expr = extract("dow", Catch.submitted_at)
        stmt = (
            select(
                dow_expr.label("dow"),
                func.count(Catch.id).label("count"),
            )
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*base_filter))
            .group_by(dow_expr)
            .order_by(dow_expr)
        )
        result = await db.execute(stmt)
        rows = result.fetchall()

        return {days[int(r.dow)]: r.count for r in rows}

    async def _get_catches_by_month(self, db: AsyncSession, base_filter: list) -> dict:
        """Get catch count by month."""
        month_expr = func.to_char(Catch.submitted_at, "YYYY-MM")
        stmt = (
            select(
                month_expr.label("month"),
                func.count(Catch.id).label("count"),
            )
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*base_filter))
            .group_by(month_expr)
            .order_by(month_expr)
        )
        result = await db.execute(stmt)
        rows = result.fetchall()

        return {r.month: r.count for r in rows}

    async def _get_top_locations(self, db: AsyncSession, base_filter: list) -> list:
        """Get top fishing locations."""
        stmt = (
            select(
                Event.name,
                FishingSpot.latitude,
                FishingSpot.longitude,
                func.count(Catch.id).label("catch_count"),
                func.max(Catch.submitted_at).label("last_catch"),
            )
            .join(Event, Catch.event_id == Event.id)
            .join(FishingSpot, Event.location_id == FishingSpot.id, isouter=True)
            .where(and_(*base_filter))
            .where(FishingSpot.latitude.isnot(None))
            .group_by(Event.id, Event.name, FishingSpot.latitude, FishingSpot.longitude)
            .order_by(func.count(Catch.id).desc())
            .limit(10)
        )
        result = await db.execute(stmt)
        rows = result.fetchall()

        return [
            {
                "name": r.name,
                "latitude": float(r.latitude) if r.latitude else 0.0,
                "longitude": float(r.longitude) if r.longitude else 0.0,
                "catch_count": r.catch_count,
                "last_catch_date": r.last_catch.date().isoformat() if r.last_catch else None,
            }
            for r in rows
        ]

    async def _get_catch_heatmap(self, db: AsyncSession, base_filter: list) -> list:
        """Get catch heatmap data (aggregated by grid)."""
        # Round coordinates to 2 decimal places for grid aggregation
        # Cast to Numeric for PostgreSQL round() function
        lat_grid = func.round(cast(FishingSpot.latitude, Numeric), 2)
        lng_grid = func.round(cast(FishingSpot.longitude, Numeric), 2)
        stmt = (
            select(
                lat_grid.label("lat"),
                lng_grid.label("lng"),
                func.count(Catch.id).label("count"),
            )
            .join(Event, Catch.event_id == Event.id)
            .join(FishingSpot, Event.location_id == FishingSpot.id, isouter=True)
            .where(and_(*base_filter))
            .where(FishingSpot.latitude.isnot(None))
            .group_by(lat_grid, lng_grid)
        )
        result = await db.execute(stmt)
        rows = result.fetchall()

        if not rows:
            return []

        max_count = max(r.count for r in rows)
        return [
            {
                "latitude": float(r.lat) if r.lat else 0.0,
                "longitude": float(r.lng) if r.lng else 0.0,
                "count": r.count,
                "intensity": round(r.count / max_count, 2) if max_count > 0 else 0,
            }
            for r in rows
        ]

    async def _get_monthly_trend(self, db: AsyncSession, user_id: int) -> list:
        """Get monthly catch trend for the last 12 months (exclude test events)."""
        twelve_months_ago = datetime.now() - timedelta(days=365)

        month_expr = func.to_char(Catch.submitted_at, "YYYY-MM")
        stmt = (
            select(
                month_expr.label("month"),
                func.count(Catch.id).label("catch_count"),
                func.avg(Catch.length).label("avg_length"),
                func.max(Catch.length).label("best_length"),
            )
            .join(Event, Catch.event_id == Event.id)
            .where(Catch.user_id == user_id)
            .where(Catch.status == CatchStatus.APPROVED.value)
            .where(Catch.submitted_at >= twelve_months_ago)
            .where(Event.is_test == False)
            .group_by(month_expr)
            .order_by(month_expr)
        )
        result = await db.execute(stmt)
        rows = result.fetchall()

        return [
            {
                "month": r.month,
                "catch_count": r.catch_count,
                "average_length": round(float(r.avg_length), 1) if r.avg_length else 0,
                "best_catch_length": float(r.best_length) if r.best_length else 0,
            }
            for r in rows
        ]

    def _calculate_improvement(self, monthly_trend: list) -> Optional[float]:
        """Calculate improvement rate comparing recent vs earlier period."""
        if len(monthly_trend) < 4:
            return None

        mid = len(monthly_trend) // 2
        earlier = sum(m["catch_count"] for m in monthly_trend[:mid])
        recent = sum(m["catch_count"] for m in monthly_trend[mid:])

        if earlier == 0:
            return None

        return round((recent - earlier) / earlier * 100, 1)

    def _hour_to_period(self, hour: int | str) -> str:
        """Convert hour to time period name."""
        # Handle string hours (from dict keys)
        if isinstance(hour, str):
            hour = int(hour)
        if 5 <= hour < 12:
            return "Morning (5-12)"
        elif 12 <= hour < 17:
            return "Afternoon (12-17)"
        elif 17 <= hour < 21:
            return "Evening (17-21)"
        else:
            return "Night (21-5)"


# Singleton instance
analytics_service = AnalyticsService()
