"""Redis cache service for leaderboard data."""

import json
from datetime import datetime
from typing import Any, Optional

import redis.asyncio as redis

from app.config import get_settings


class RedisCache:
    """Async Redis cache for leaderboard and scoring data."""

    # Cache key prefixes
    LEADERBOARD = "leaderboard"
    USER_DETAILS = "user"
    TEAM_DETAILS = "team"
    MOVEMENTS = "movements"
    UPDATED_AT = "updated_at"
    VIEWER_COUNT = "viewers"

    # Default TTL: 1 hour (leaderboard recalculated on each change anyway)
    DEFAULT_TTL = 3600

    def __init__(self):
        self._redis: Optional[redis.Redis] = None
        self._event_loop_id: Optional[int] = None

    async def get_client(self) -> redis.Redis:
        """Get or create Redis client.

        Recreates the client if the event loop has changed (e.g., in Celery workers
        where each task may have a fresh event loop).
        """
        import asyncio
        current_loop_id = id(asyncio.get_event_loop())

        # Recreate client if event loop has changed or client doesn't exist
        if self._redis is None or self._event_loop_id != current_loop_id:
            if self._redis is not None:
                try:
                    await self._redis.close()
                except Exception:
                    pass  # Ignore errors when closing stale connection

            settings = get_settings()
            self._redis = redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            self._event_loop_id = current_loop_id
        return self._redis

    async def close(self):
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None

    # === Key builders ===

    def _key(self, *parts: str) -> str:
        """Build a cache key from parts."""
        return ":".join(parts)

    def leaderboard_key(self, event_id: int) -> str:
        """Key for full leaderboard JSON."""
        return self._key(self.LEADERBOARD, str(event_id))

    def user_details_key(self, event_id: int, user_id: int) -> str:
        """Key for user catch details."""
        return self._key(self.LEADERBOARD, str(event_id), self.USER_DETAILS, str(user_id))

    def team_details_key(self, event_id: int, team_id: int) -> str:
        """Key for team catch details."""
        return self._key(self.LEADERBOARD, str(event_id), self.TEAM_DETAILS, str(team_id))

    def movements_key(self, event_id: int) -> str:
        """Key for recent ranking movements."""
        return self._key(self.LEADERBOARD, str(event_id), self.MOVEMENTS)

    def updated_at_key(self, event_id: int) -> str:
        """Key for last calculation timestamp."""
        return self._key(self.LEADERBOARD, str(event_id), self.UPDATED_AT)

    def viewer_count_key(self, event_id: int) -> str:
        """Key for live viewer count."""
        return self._key(self.LEADERBOARD, str(event_id), self.VIEWER_COUNT)

    # === Generic cache operations ===

    async def get(self, key: str) -> Optional[Any]:
        """Get a value from cache by key."""
        client = await self.get_client()
        data = await client.get(key)
        if data:
            try:
                return json.loads(data)
            except (json.JSONDecodeError, TypeError):
                return data
        return None

    async def set(self, key: str, value: Any, ttl: int = DEFAULT_TTL):
        """Set a value in cache with TTL."""
        client = await self.get_client()
        await client.setex(key, ttl, json.dumps(value, default=str))

    async def delete(self, key: str):
        """Delete a key from cache."""
        client = await self.get_client()
        await client.delete(key)

    # === Leaderboard operations ===

    async def get_leaderboard(self, event_id: int) -> Optional[dict]:
        """Get cached leaderboard data."""
        client = await self.get_client()
        data = await client.get(self.leaderboard_key(event_id))
        if data:
            return json.loads(data)
        return None

    async def set_leaderboard(self, event_id: int, data: dict, ttl: int = DEFAULT_TTL):
        """Cache leaderboard data."""
        client = await self.get_client()
        await client.setex(
            self.leaderboard_key(event_id),
            ttl,
            json.dumps(data, default=str),
        )
        # Update timestamp
        await client.set(
            self.updated_at_key(event_id),
            datetime.utcnow().isoformat(),
        )

    async def get_updated_at(self, event_id: int) -> Optional[datetime]:
        """Get last calculation timestamp."""
        client = await self.get_client()
        data = await client.get(self.updated_at_key(event_id))
        if data:
            return datetime.fromisoformat(data)
        return None

    # === User/Team details ===

    async def get_user_details(self, event_id: int, user_id: int) -> Optional[dict]:
        """Get cached user catch details."""
        client = await self.get_client()
        data = await client.get(self.user_details_key(event_id, user_id))
        if data:
            return json.loads(data)
        return None

    async def set_user_details(
        self, event_id: int, user_id: int, data: dict, ttl: int = DEFAULT_TTL
    ):
        """Cache user catch details."""
        client = await self.get_client()
        await client.setex(
            self.user_details_key(event_id, user_id),
            ttl,
            json.dumps(data, default=str),
        )

    async def get_team_details(self, event_id: int, team_id: int) -> Optional[dict]:
        """Get cached team catch details."""
        client = await self.get_client()
        data = await client.get(self.team_details_key(event_id, team_id))
        if data:
            return json.loads(data)
        return None

    async def set_team_details(
        self, event_id: int, team_id: int, data: dict, ttl: int = DEFAULT_TTL
    ):
        """Cache team catch details."""
        client = await self.get_client()
        await client.setex(
            self.team_details_key(event_id, team_id),
            ttl,
            json.dumps(data, default=str),
        )

    # === Batch operations ===

    async def set_all_user_details(
        self, event_id: int, user_details: dict[int, dict], ttl: int = DEFAULT_TTL
    ):
        """Cache all user details at once (pipeline for efficiency)."""
        client = await self.get_client()
        async with client.pipeline() as pipe:
            for user_id, data in user_details.items():
                pipe.setex(
                    self.user_details_key(event_id, user_id),
                    ttl,
                    json.dumps(data, default=str),
                )
            await pipe.execute()

    async def set_all_team_details(
        self, event_id: int, team_details: dict[int, dict], ttl: int = DEFAULT_TTL
    ):
        """Cache all team details at once (pipeline for efficiency)."""
        client = await self.get_client()
        async with client.pipeline() as pipe:
            for team_id, data in team_details.items():
                pipe.setex(
                    self.team_details_key(event_id, team_id),
                    ttl,
                    json.dumps(data, default=str),
                )
            await pipe.execute()

    # === Movements ===

    async def get_movements(self, event_id: int, limit: int = 20) -> list[dict]:
        """Get recent ranking movements."""
        client = await self.get_client()
        data = await client.lrange(self.movements_key(event_id), 0, limit - 1)
        return [json.loads(item) for item in data]

    async def add_movement(self, event_id: int, movement: dict, max_size: int = 100):
        """Add a ranking movement and trim list."""
        client = await self.get_client()
        key = self.movements_key(event_id)
        await client.lpush(key, json.dumps(movement, default=str))
        await client.ltrim(key, 0, max_size - 1)

    async def set_movements(self, event_id: int, movements: list[dict]):
        """Replace all movements (used during recalculation)."""
        client = await self.get_client()
        key = self.movements_key(event_id)
        await client.delete(key)
        if movements:
            async with client.pipeline() as pipe:
                for m in movements:
                    pipe.rpush(key, json.dumps(m, default=str))
                await pipe.execute()

    # === Viewer count ===

    async def increment_viewers(self, event_id: int) -> int:
        """Increment viewer count when SSE client connects."""
        client = await self.get_client()
        return await client.incr(self.viewer_count_key(event_id))

    async def decrement_viewers(self, event_id: int) -> int:
        """Decrement viewer count when SSE client disconnects."""
        client = await self.get_client()
        key = self.viewer_count_key(event_id)
        count = await client.decr(key)
        # Don't go below 0
        if count < 0:
            await client.set(key, 0)
            return 0
        return count

    async def get_viewer_count(self, event_id: int) -> int:
        """Get current viewer count."""
        client = await self.get_client()
        count = await client.get(self.viewer_count_key(event_id))
        return int(count) if count else 0

    # === Cache invalidation ===

    async def invalidate_event(self, event_id: int):
        """Invalidate all cached data for an event."""
        client = await self.get_client()
        # Find all keys for this event
        pattern = f"{self.LEADERBOARD}:{event_id}:*"
        keys = []
        async for key in client.scan_iter(match=pattern):
            keys.append(key)
        # Also include the main leaderboard key
        keys.append(self.leaderboard_key(event_id))

        if keys:
            await client.delete(*keys)

    # === Pub/Sub for SSE bridge (Celery -> FastAPI) ===

    SSE_CHANNEL_PREFIX = "sse_broadcast"

    def sse_channel(self, event_id: int) -> str:
        """Get the Pub/Sub channel name for SSE broadcasts."""
        return f"{self.SSE_CHANNEL_PREFIX}:event_{event_id}"

    async def publish_sse_event(self, event_id: int, data: dict):
        """
        Publish an SSE event to Redis Pub/Sub.
        Used by Celery tasks to notify FastAPI to broadcast to SSE clients.
        """
        client = await self.get_client()
        channel = self.sse_channel(event_id)
        await client.publish(channel, json.dumps(data, default=str))

    async def subscribe_sse_channel(self, event_id: int):
        """
        Subscribe to SSE events for a specific event.
        Returns a PubSub object that can be iterated for messages.
        """
        client = await self.get_client()
        pubsub = client.pubsub()
        await pubsub.subscribe(self.sse_channel(event_id))
        return pubsub

    async def get_pubsub(self):
        """Get a PubSub client for pattern subscriptions."""
        client = await self.get_client()
        return client.pubsub()


# Global singleton
redis_cache = RedisCache()
