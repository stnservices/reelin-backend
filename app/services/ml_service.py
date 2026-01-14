"""ML Model Service for loading and using trained models."""

import os
import time
import logging
from datetime import datetime, timezone
from typing import Optional

import joblib
import numpy as np
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ml_model import MLModel, MLPredictionLog
from app.models.hall_of_fame import HallOfFameEntry

logger = logging.getLogger(__name__)


class MLModelCache:
    """In-memory cache for loaded ML models."""

    def __init__(self):
        self._models: dict = {}
        self._scalers: dict = {}
        self._features: dict = {}
        self._model_ids: dict = {}

    def get(self, model_type: str) -> tuple[Optional[object], Optional[object], list[str], Optional[int]]:
        """Get cached model, scaler, features, and model_id."""
        return (
            self._models.get(model_type),
            self._scalers.get(model_type),
            self._features.get(model_type, []),
            self._model_ids.get(model_type),
        )

    def set(
        self,
        model_type: str,
        model: object,
        scaler: object,
        features: list[str],
        model_id: int,
    ) -> None:
        """Cache model, scaler, and features."""
        self._models[model_type] = model
        self._scalers[model_type] = scaler
        self._features[model_type] = features
        self._model_ids[model_type] = model_id

    def clear(self, model_type: str = None) -> None:
        """Clear cache for specific type or all."""
        if model_type:
            self._models.pop(model_type, None)
            self._scalers.pop(model_type, None)
            self._features.pop(model_type, None)
            self._model_ids.pop(model_type, None)
        else:
            self._models.clear()
            self._scalers.clear()
            self._features.clear()
            self._model_ids.clear()


# Global cache
_model_cache = MLModelCache()


class MLService:
    """Service for ML model predictions."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def load_active_model(self, model_type: str) -> tuple[Optional[object], Optional[object], list[str], Optional[int]]:
        """
        Load the active model for a given type.
        Returns (model, scaler, features, model_id) tuple.
        Uses cache when available.
        """
        # Check cache first
        model, scaler, features, model_id = _model_cache.get(model_type)
        if model is not None:
            return model, scaler, features, model_id

        # Get active model from DB
        stmt = select(MLModel).where(
            MLModel.model_type == model_type,
            MLModel.is_active == True,
        )
        result = await self.db.execute(stmt)
        ml_model = result.scalar_one_or_none()

        if not ml_model:
            logger.debug(f"No active model found for type: {model_type}")
            return None, None, [], None

        # Load model from file
        try:
            model = joblib.load(ml_model.file_path)

            # Try to load scaler
            scaler_path = ml_model.file_path.replace("_model.joblib", "_scaler.joblib")
            scaler = None
            if os.path.exists(scaler_path):
                scaler = joblib.load(scaler_path)

            # Try to load feature list
            features_path = ml_model.file_path.replace("_model.joblib", "_features.txt")
            features = []
            if os.path.exists(features_path):
                with open(features_path) as f:
                    features = [line.strip() for line in f if line.strip()]
            elif ml_model.feature_columns:
                features = ml_model.feature_columns

            # Cache the loaded model
            _model_cache.set(model_type, model, scaler, features, ml_model.id)

            logger.info(f"Loaded ML model: {ml_model.name} (id={ml_model.id})")
            return model, scaler, features, ml_model.id

        except Exception as e:
            logger.error(f"Failed to load model {ml_model.name}: {e}")
            return None, None, [], None

    async def predict_event_enrollment(
        self,
        user_id: int,
        event_id: int,
        features: dict,
        log_prediction: bool = True,
    ) -> Optional[float]:
        """
        Predict probability of user enrolling in event.
        Returns probability between 0 and 1, or None if model unavailable.
        """
        model, scaler, feature_names, model_id = await self.load_active_model("event_recommendations")

        if model is None:
            return None

        start_time = time.time()

        try:
            # Build feature vector in correct order
            feature_vector = []
            for fname in feature_names:
                feature_vector.append(features.get(fname, 0))

            X = np.array([feature_vector])

            # Scale if scaler available
            if scaler is not None:
                X = scaler.transform(X)

            # Predict probability
            proba = model.predict_proba(X)[0][1]  # Probability of class 1 (enrollment)

            elapsed_ms = (time.time() - start_time) * 1000

            # Log prediction
            if log_prediction and model_id:
                await self._log_prediction(
                    model_id=model_id,
                    user_id=user_id,
                    entity_type="event",
                    entity_id=event_id,
                    score=float(proba),
                    elapsed_ms=elapsed_ms,
                )

            return float(proba)

        except Exception as e:
            logger.error(f"Prediction error: {e}")
            return None

    async def _log_prediction(
        self,
        model_id: int,
        user_id: int,
        entity_type: str,
        entity_id: int,
        score: float,
        elapsed_ms: float,
    ) -> None:
        """Log a prediction for monitoring."""
        try:
            log = MLPredictionLog(
                model_id=model_id,
                user_id=user_id,
                entity_type=entity_type,
                entity_id=entity_id,
                prediction_score=score,
                prediction_ms=elapsed_ms,
            )
            self.db.add(log)

            # Update model stats
            await self.db.execute(
                update(MLModel)
                .where(MLModel.id == model_id)
                .values(
                    predictions_count=MLModel.predictions_count + 1,
                    last_prediction_at=datetime.now(timezone.utc),
                )
            )

            await self.db.commit()
        except Exception as e:
            logger.error(f"Failed to log prediction: {e}")

    async def get_user_hall_of_fame_stats(self, user_id: int) -> dict:
        """Get Hall of Fame statistics for a user."""
        stmt = select(HallOfFameEntry).where(HallOfFameEntry.user_id == user_id)
        result = await self.db.execute(stmt)
        entries = result.scalars().all()

        # Achievement type to tier mapping
        ACHIEVEMENT_TIER = {
            "world_champion": 1,
            "national_champion": 2,
            "world_podium": 3,
            "national_podium": 4,
        }

        stats = {
            "hof_entry_count": 0,
            "hof_world_champion": 0,
            "hof_national_champion": 0,
            "hof_world_podium": 0,
            "hof_national_podium": 0,
            "hof_best_tier": 5,  # 5 = no hall of fame entries
        }

        if not entries:
            return stats

        stats["hof_entry_count"] = len(entries)

        for entry in entries:
            if entry.achievement_type == "world_champion":
                stats["hof_world_champion"] += 1
            elif entry.achievement_type == "national_champion":
                stats["hof_national_champion"] += 1
            elif entry.achievement_type == "world_podium":
                stats["hof_world_podium"] += 1
            elif entry.achievement_type == "national_podium":
                stats["hof_national_podium"] += 1

            # Track best tier
            tier = ACHIEVEMENT_TIER.get(entry.achievement_type, 5)
            stats["hof_best_tier"] = min(stats["hof_best_tier"], tier)

        return stats

    async def build_event_features(
        self,
        user_stats: dict,
        user_created_at: datetime,
        event_data: dict,
    ) -> dict:
        """Build feature dict for event recommendation prediction."""
        # Calculate user age at event
        user_age_at_event = 0
        event_start = event_data.get("start_date")
        if event_start and user_created_at:
            if isinstance(event_start, datetime):
                delta = event_start - user_created_at
                user_age_at_event = delta.total_seconds() / 86400

        return {
            "user_event_count": user_stats.get("total_events", 0),
            "user_catch_count": user_stats.get("total_catches", 0),
            "user_species_count": user_stats.get("unique_species_count", 0),
            "user_max_catch_cm": user_stats.get("largest_catch_cm", 0),
            "user_wins": user_stats.get("total_wins", 0),
            "user_podiums": user_stats.get("podium_finishes", 0),
            "user_age_at_event": user_age_at_event,
            "event_type_id": event_data.get("event_type_id", 1),
            "event_day_of_week": event_data.get("day_of_week", 0),
            "event_month": event_data.get("month", 1),
            "event_enrollment_count": event_data.get("enrollment_count", 0),
            "has_done_event_type": 1 if user_stats.get("has_done_event_type") else 0,
            "friends_enrolled_count": event_data.get("friends_enrolled", 0),
        }

    async def build_event_features_v2(
        self,
        user_id: int,
        user_stats: dict,
        user_created_at: datetime,
        event_data: dict,
    ) -> dict:
        """Build enhanced feature dict for v2 event recommendation prediction."""
        # Get Hall of Fame stats
        hof_stats = await self.get_user_hall_of_fame_stats(user_id)

        # Calculate user age at event
        user_age_at_event = 0
        event_start = event_data.get("start_date")
        if event_start and user_created_at:
            if isinstance(event_start, datetime):
                delta = event_start - user_created_at
                user_age_at_event = max(0, delta.total_seconds() / 86400)

        # Calculate derived features
        total_events = user_stats.get("total_events", 0)
        win_rate = user_stats.get("total_wins", 0) / total_events if total_events > 0 else 0
        podium_rate = user_stats.get("podium_finishes", 0) / total_events if total_events > 0 else 0

        # Days since last event
        days_since_last = 365  # default if never participated
        last_event_date = user_stats.get("last_event_date")
        if last_event_date and event_start:
            if isinstance(event_start, datetime) and isinstance(last_event_date, datetime):
                delta = event_start - last_event_date
                days_since_last = max(0, delta.total_seconds() / 86400)

        return {
            # User basic stats
            "user_event_count": user_stats.get("total_events", 0),
            "user_catch_count": user_stats.get("total_catches", 0),
            "user_species_count": user_stats.get("unique_species_count", 0),
            "user_max_catch_cm": user_stats.get("largest_catch_cm", 0),
            "user_wins": user_stats.get("total_wins", 0),
            "user_podiums": user_stats.get("podium_finishes", 0),

            # Hall of Fame features
            "hof_entry_count": hof_stats["hof_entry_count"],
            "hof_world_champion": hof_stats["hof_world_champion"],
            "hof_national_champion": hof_stats["hof_national_champion"],
            "hof_world_podium": hof_stats["hof_world_podium"],
            "hof_national_podium": hof_stats["hof_national_podium"],
            "hof_best_tier": hof_stats["hof_best_tier"],

            # Performance features
            "win_rate": win_rate,
            "podium_rate": podium_rate,
            "avg_catch_length": user_stats.get("average_catch_length", 0),
            "consecutive_events": user_stats.get("consecutive_events", 0),
            "max_consecutive_events": user_stats.get("max_consecutive_events", 0),

            # Activity features
            "events_this_year": user_stats.get("total_events_this_year", 0),
            "days_since_last_event": days_since_last,

            # Context features
            "user_age_at_event": user_age_at_event,
            "event_type_id": event_data.get("event_type_id", 1),
            "event_day_of_week": event_data.get("day_of_week", 0),
            "event_month": event_data.get("month", 1),
            "event_enrollment_count": event_data.get("enrollment_count", 0),
            "has_done_event_type": 1 if user_stats.get("has_done_event_type") else 0,
            "friends_enrolled_count": event_data.get("friends_enrolled", 0),
        }


def get_ml_service(db: AsyncSession) -> MLService:
    """Factory function for ML service."""
    return MLService(db)


def clear_model_cache(model_type: str = None) -> None:
    """Clear the model cache (call after model update)."""
    _model_cache.clear(model_type)
