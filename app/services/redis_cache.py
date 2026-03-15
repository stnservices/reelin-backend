"""Redis cache service for chat and live tracking."""

import json
from datetime import datetime
from typing import Any, Optional

import redis.asyncio as redis

from app.config import get_settings


def _json_default(obj):
    """JSON serializer for cache values. Converts Decimal to float, datetime to ISO string."""
    from decimal import Decimal
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class RedisCache:
    """Async Redis cache for chat and live tracking data."""

    # Default TTL: 1 hour
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
                socket_timeout=5.0,  # 5 second timeout for operations
                socket_connect_timeout=5.0,  # 5 second timeout for connection
            )
            self._event_loop_id = current_loop_id
        return self._redis

    async def close(self):
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None

    # === Generic cache operations ===

    async def get(self, key: str) -> Optional[Any]:
        """Get a value from cache by key."""
        client = await self.get_client()
        data = await client.get(key)
        if data is not None:
            try:
                return json.loads(data)
            except (json.JSONDecodeError, TypeError):
                return data
        return None

    async def set(self, key: str, value: Any, ttl: int = DEFAULT_TTL):
        """Set a value in cache with TTL."""
        client = await self.get_client()
        await client.setex(key, ttl, json.dumps(value, default=_json_default))

    async def delete(self, key: str):
        """Delete a key from cache."""
        client = await self.get_client()
        await client.delete(key)

    # === Chat Pub/Sub (Event Chat feature) ===

    CHAT_CHANNEL_PREFIX = "chat"

    def chat_channel(self, event_id: int) -> str:
        """Get the Pub/Sub channel name for chat broadcasts."""
        return f"{self.CHAT_CHANNEL_PREFIX}:event_{event_id}"

    async def publish_chat_message(self, event_id: int, data: dict):
        """Publish a chat event to Redis Pub/Sub."""
        client = await self.get_client()
        channel = self.chat_channel(event_id)
        await client.publish(channel, json.dumps(data, default=str))

    async def subscribe_chat_channel(self, event_id: int):
        """Subscribe to chat events for a specific event."""
        client = await self.get_client()
        pubsub = client.pubsub()
        await pubsub.subscribe(self.chat_channel(event_id))
        return pubsub

    async def get_chat_pubsub(self):
        """Get a PubSub client for chat pattern subscriptions."""
        client = await self.get_client()
        return client.pubsub()

    # === Chat Message Caching ===

    CHAT_MESSAGES_PREFIX = "chat_messages"
    CHAT_CACHE_TTL = 3600  # 1 hour

    def chat_messages_key(self, event_id: int) -> str:
        """Key for cached chat messages (sorted set by message ID)."""
        return f"{self.CHAT_MESSAGES_PREFIX}:{event_id}"

    async def cache_chat_message(self, event_id: int, message: dict):
        """Add a message to the cache and publish to Pub/Sub."""
        client = await self.get_client()
        key = self.chat_messages_key(event_id)

        # Add to sorted set (score = message_id for ordering)
        await client.zadd(key, {json.dumps(message, default=str): message["id"]})

        # Keep only last 100 messages in cache
        await client.zremrangebyrank(key, 0, -101)

        # Refresh TTL
        await client.expire(key, self.CHAT_CACHE_TTL)

        # Publish to Pub/Sub for real-time subscribers
        await self.publish_chat_message(event_id, {
            "type": "new_message",
            "message": message,
        })

    async def get_cached_chat_messages(
        self, event_id: int, limit: int = 50, before_id: Optional[int] = None
    ) -> Optional[list[dict]]:
        """Get cached messages. Returns None if cache miss."""
        client = await self.get_client()
        key = self.chat_messages_key(event_id)

        # Check if cache exists
        if not await client.exists(key):
            return None

        if before_id:
            # Get messages with ID less than before_id
            data = await client.zrevrangebyscore(
                key, f"({before_id}", "-inf", start=0, num=limit
            )
        else:
            # Get latest messages
            data = await client.zrevrange(key, 0, limit - 1)

        if not data:
            return None

        # Parse and reverse to get chronological order
        messages = [json.loads(item) for item in data]
        messages.reverse()
        return messages

    async def delete_cached_chat_message(self, event_id: int, message_id: int):
        """Remove a message from cache and publish deletion event."""
        client = await self.get_client()
        key = self.chat_messages_key(event_id)

        # Find and remove the message
        messages = await client.zrangebyscore(key, message_id, message_id)
        if messages:
            await client.zrem(key, *messages)

        # Publish deletion event
        await self.publish_chat_message(event_id, {
            "type": "message_deleted",
            "message_id": message_id,
        })

    async def update_cached_chat_message(self, event_id: int, message_id: int, updates: dict):
        """Update a message in cache (e.g., pin/unpin)."""
        client = await self.get_client()
        key = self.chat_messages_key(event_id)

        # Find the message
        messages = await client.zrangebyscore(key, message_id, message_id)
        if messages:
            old_data = json.loads(messages[0])
            # Remove old entry
            await client.zrem(key, messages[0])
            # Update and re-add
            old_data.update(updates)
            await client.zadd(key, {json.dumps(old_data, default=str): message_id})

        # Publish update event
        event_type = "message_pinned" if updates.get("is_pinned") else "message_unpinned"
        await self.publish_chat_message(event_id, {
            "type": event_type,
            "message_id": message_id,
            "is_pinned": updates.get("is_pinned", False),
        })

    async def warm_chat_cache(self, event_id: int, messages: list[dict]):
        """Pre-populate cache with messages from database."""
        client = await self.get_client()
        key = self.chat_messages_key(event_id)

        if not messages:
            return

        # Add all messages to sorted set
        mapping = {json.dumps(m, default=str): m["id"] for m in messages}
        await client.zadd(key, mapping)
        await client.expire(key, self.CHAT_CACHE_TTL)

    # === Live Tracking ===

    TRACKING_CHANNEL_PREFIX = "tracking"
    TRACKING_POSITIONS_PREFIX = "tracking_positions"
    TRACKING_STATS_PREFIX = "tracking_stats"
    TRACKING_TTL = 7200  # 2 hours - positions expire after event

    def tracking_channel(self, event_id: int) -> str:
        """Get Pub/Sub channel for live tracking broadcasts."""
        return f"{self.TRACKING_CHANNEL_PREFIX}:event_{event_id}"

    def tracking_positions_key(self, event_id: int) -> str:
        """Key for hash storing all participant positions."""
        return f"{self.TRACKING_POSITIONS_PREFIX}:{event_id}"

    def tracking_stats_key(self, event_id: int) -> str:
        """Key for tracking statistics (participant count, etc)."""
        return f"{self.TRACKING_STATS_PREFIX}:{event_id}"

    async def update_participant_position(
        self,
        event_id: int,
        user_id: int,
        position_data: dict,
    ):
        """Update participant's live position in Redis hash and publish update."""
        client = await self.get_client()
        key = self.tracking_positions_key(event_id)

        # Store position in hash (field = user_id, value = position JSON)
        await client.hset(key, str(user_id), json.dumps(position_data, default=str))
        await client.expire(key, self.TRACKING_TTL)

        # Publish position update to subscribers
        await client.publish(
            self.tracking_channel(event_id),
            json.dumps({
                "type": "position_update",
                "user_id": user_id,
                "position": position_data,
            }, default=str),
        )

    async def get_all_participant_positions(self, event_id: int) -> dict[int, dict]:
        """Get all participant positions for an event."""
        client = await self.get_client()
        key = self.tracking_positions_key(event_id)

        data = await client.hgetall(key)
        result = {}
        for user_id_str, position_json in data.items():
            try:
                result[int(user_id_str)] = json.loads(position_json)
            except (json.JSONDecodeError, ValueError):
                continue
        return result

    async def get_participant_position(self, event_id: int, user_id: int) -> dict | None:
        """Get a single participant's position."""
        client = await self.get_client()
        key = self.tracking_positions_key(event_id)

        data = await client.hget(key, str(user_id))
        if data:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                return None
        return None

    async def remove_participant_position(self, event_id: int, user_id: int):
        """Remove participant from tracking (when they stop)."""
        client = await self.get_client()
        key = self.tracking_positions_key(event_id)

        await client.hdel(key, str(user_id))

        # Publish removal event
        await client.publish(
            self.tracking_channel(event_id),
            json.dumps({
                "type": "participant_removed",
                "user_id": user_id,
            }),
        )

    async def set_participant_offline(self, event_id: int, user_id: int):
        """Mark participant as offline without removing them."""
        client = await self.get_client()
        key = self.tracking_positions_key(event_id)

        # Get current position and update online status
        data = await client.hget(key, str(user_id))
        if data:
            try:
                position = json.loads(data)
                position["is_online"] = False
                position["disconnected_at"] = datetime.utcnow().isoformat()
                await client.hset(key, str(user_id), json.dumps(position, default=str))

                # Publish offline event
                await client.publish(
                    self.tracking_channel(event_id),
                    json.dumps({
                        "type": "participant_offline",
                        "user_id": user_id,
                    }),
                )
            except json.JSONDecodeError:
                pass

    async def get_tracking_participant_count(self, event_id: int) -> int:
        """Get number of participants currently tracking."""
        client = await self.get_client()
        key = self.tracking_positions_key(event_id)
        return await client.hlen(key)

    async def subscribe_tracking_channel(self, event_id: int):
        """Subscribe to tracking updates for an event."""
        client = await self.get_client()
        pubsub = client.pubsub()
        await pubsub.subscribe(self.tracking_channel(event_id))
        return pubsub

    async def publish_tracking_event(self, event_id: int, data: dict):
        """Publish a generic tracking event."""
        client = await self.get_client()
        await client.publish(
            self.tracking_channel(event_id),
            json.dumps(data, default=str),
        )


# Global singleton
redis_cache = RedisCache()


async def invalidate_event_caches(event_id: int, user_ids: list[int] | None = None):
    """Invalidate all cached data for an event. Call on match/catch completion."""
    keys = [
        f"ta:rankings:{event_id}",
        f"ta:public:standings:{event_id}",
        f"ta:public:status:{event_id}",
        f"ta:statistics:{event_id}",
        f"ta:public:bracket:{event_id}",
        f"sf:leaderboard:{event_id}",
    ]
    if user_ids:
        for uid in user_ids:
            if uid is not None:
                keys.append(f"ta:gamecards:{event_id}:{uid}")
                keys.append(f"ta:mymatches:{event_id}:{uid}")
    for key in keys:
        await redis_cache.delete(key)
