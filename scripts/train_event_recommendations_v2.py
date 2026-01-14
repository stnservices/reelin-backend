"""
Train Enhanced Event Recommendations ML Model v2.

This script trains a machine learning model to predict user enrollment probability
for events, incorporating:
- User statistics (events, catches, wins, podiums)
- Hall of Fame data (world/national championships)
- Performance metrics (win rate, podium rate, streaks)
- Activity patterns (recency, frequency)
- Event context (type, day, month, popularity)

Usage:
    cd reelin-backend
    python scripts/train_event_recommendations_v2.py

The script will output:
    - models/event_recommendations/event_recommendation_v2_model.joblib
    - models/event_recommendations/event_recommendation_v2_scaler.joblib
    - models/event_recommendations/event_recommendation_v2_features.txt
"""

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, classification_report, confusion_matrix

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, func, and_, distinct
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models.user import UserAccount
from app.models.event import Event, EventType
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.statistics import UserEventTypeStats
from app.models.hall_of_fame import HallOfFameEntry
from app.models.follow import UserFollow

# Load environment
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


# Achievement type to tier mapping
ACHIEVEMENT_TIER = {
    "world_champion": 1,
    "national_champion": 2,
    "world_podium": 3,
    "national_podium": 4,
}


async def get_db_session():
    """Create async database session."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return async_session()


async def get_user_stats(db: AsyncSession, user_id: int) -> dict:
    """Get user overall stats from user_event_type_stats."""
    stmt = select(UserEventTypeStats).where(
        UserEventTypeStats.user_id == user_id,
        UserEventTypeStats.event_type_id.is_(None),  # Overall stats
    )
    result = await db.execute(stmt)
    stats = result.scalar_one_or_none()

    if stats:
        return {
            "total_events": stats.total_events,
            "total_catches": stats.total_catches,
            "unique_species_count": stats.unique_species_count,
            "largest_catch_cm": float(stats.largest_catch_cm or 0),
            "total_wins": stats.total_wins,
            "podium_finishes": stats.podium_finishes,
            "avg_catch_length": float(stats.average_catch_length or 0),
            "consecutive_events": stats.consecutive_events,
            "max_consecutive_events": stats.max_consecutive_events,
            "events_this_year": stats.total_events_this_year,
            "last_event_date": stats.last_event_date,
        }
    return {
        "total_events": 0,
        "total_catches": 0,
        "unique_species_count": 0,
        "largest_catch_cm": 0,
        "total_wins": 0,
        "podium_finishes": 0,
        "avg_catch_length": 0,
        "consecutive_events": 0,
        "max_consecutive_events": 0,
        "events_this_year": 0,
        "last_event_date": None,
    }


async def get_user_hall_of_fame_stats(db: AsyncSession, user_id: int) -> dict:
    """Get Hall of Fame statistics for a user."""
    stmt = select(HallOfFameEntry).where(HallOfFameEntry.user_id == user_id)
    result = await db.execute(stmt)
    entries = result.scalars().all()

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


async def get_user_event_types(db: AsyncSession, user_id: int) -> set:
    """Get event type IDs the user has participated in."""
    stmt = (
        select(distinct(Event.event_type_id))
        .join(EventEnrollment, EventEnrollment.event_id == Event.id)
        .where(
            EventEnrollment.user_id == user_id,
            EventEnrollment.status == EnrollmentStatus.APPROVED.value,
        )
    )
    result = await db.execute(stmt)
    return {row[0] for row in result.all()}


async def get_friends_enrolled_count(db: AsyncSession, user_id: int, event_id: int) -> int:
    """Get count of friends (following) enrolled in event."""
    stmt = (
        select(func.count())
        .select_from(UserFollow)
        .join(EventEnrollment, EventEnrollment.user_id == UserFollow.following_id)
        .where(
            UserFollow.follower_id == user_id,
            EventEnrollment.event_id == event_id,
            EventEnrollment.status == EnrollmentStatus.APPROVED.value,
        )
    )
    result = await db.execute(stmt)
    return result.scalar() or 0


async def get_event_enrollment_count(db: AsyncSession, event_id: int) -> int:
    """Get number of approved enrollments for an event at a point in time."""
    stmt = select(func.count()).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.status == EnrollmentStatus.APPROVED.value,
    )
    result = await db.execute(stmt)
    return result.scalar() or 0


async def build_training_data(db: AsyncSession) -> pd.DataFrame:
    """Build training dataset from historical enrollments."""
    print("Building training data...")

    # Get all completed events
    stmt = (
        select(Event)
        .where(
            Event.status == "completed",
            Event.is_deleted.is_(False),
        )
        .order_by(Event.start_date)
    )
    result = await db.execute(stmt)
    events = result.scalars().all()
    print(f"Found {len(events)} completed events")

    # Get all active users
    user_stmt = select(UserAccount).where(UserAccount.is_active.is_(True))
    user_result = await db.execute(user_stmt)
    users = user_result.scalars().all()
    print(f"Found {len(users)} active users")

    # Build user lookups
    user_created_at = {u.id: u.created_at for u in users}

    # Build samples
    samples = []
    now = datetime.now(timezone.utc)

    for event in events:
        if not event.start_date:
            continue

        event_start = event.start_date
        if event_start.tzinfo is None:
            event_start = event_start.replace(tzinfo=timezone.utc)

        # Get all approved enrollments for this event
        enrolled_stmt = select(EventEnrollment.user_id).where(
            EventEnrollment.event_id == event.id,
            EventEnrollment.status == EnrollmentStatus.APPROVED.value,
        )
        enrolled_result = await db.execute(enrolled_stmt)
        enrolled_users = {row[0] for row in enrolled_result.all()}

        enrollment_count = len(enrolled_users)

        # For each enrolled user, create a positive sample
        for user_id in enrolled_users:
            if user_id not in user_created_at:
                continue

            user_created = user_created_at[user_id]
            if user_created and user_created.tzinfo is None:
                user_created = user_created.replace(tzinfo=timezone.utc)

            # Get user stats
            user_stats = await get_user_stats(db, user_id)
            hof_stats = await get_user_hall_of_fame_stats(db, user_id)
            user_event_types = await get_user_event_types(db, user_id)
            friends_enrolled = await get_friends_enrolled_count(db, user_id, event.id)

            # Calculate derived features
            total_events = user_stats["total_events"]
            win_rate = user_stats["total_wins"] / total_events if total_events > 0 else 0
            podium_rate = user_stats["podium_finishes"] / total_events if total_events > 0 else 0

            # User age at event
            user_age_days = 0
            if user_created and event_start:
                delta = event_start - user_created
                user_age_days = max(0, delta.total_seconds() / 86400)

            # Days since last event
            days_since_last = 365  # default if never participated
            if user_stats["last_event_date"]:
                last_event_date = user_stats["last_event_date"]
                if last_event_date.tzinfo is None:
                    last_event_date = last_event_date.replace(tzinfo=timezone.utc)
                delta = event_start - last_event_date
                days_since_last = max(0, delta.total_seconds() / 86400)

            sample = {
                # Target
                "enrolled": 1,

                # User basic stats
                "user_event_count": user_stats["total_events"],
                "user_catch_count": user_stats["total_catches"],
                "user_species_count": user_stats["unique_species_count"],
                "user_max_catch_cm": user_stats["largest_catch_cm"],
                "user_wins": user_stats["total_wins"],
                "user_podiums": user_stats["podium_finishes"],

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
                "avg_catch_length": user_stats["avg_catch_length"],
                "consecutive_events": user_stats["consecutive_events"],
                "max_consecutive_events": user_stats["max_consecutive_events"],

                # Activity features
                "events_this_year": user_stats["events_this_year"],
                "days_since_last_event": days_since_last,

                # Context features
                "user_age_at_event": user_age_days,
                "event_type_id": event.event_type_id or 1,
                "event_day_of_week": event_start.weekday(),
                "event_month": event_start.month,
                "event_enrollment_count": enrollment_count,
                "has_done_event_type": 1 if event.event_type_id in user_event_types else 0,
                "friends_enrolled_count": friends_enrolled,
            }
            samples.append(sample)

        # Create negative samples from users who didn't enroll
        # Sample a subset of non-enrolled users for balance
        non_enrolled_candidates = [
            uid for uid in user_created_at.keys()
            if uid not in enrolled_users
        ]

        # Sample approximately same number of negatives as positives
        num_negatives = min(len(non_enrolled_candidates), len(enrolled_users))
        if num_negatives > 0:
            negative_user_ids = np.random.choice(
                non_enrolled_candidates, size=num_negatives, replace=False
            )

            for user_id in negative_user_ids:
                user_created = user_created_at[user_id]
                if user_created and user_created.tzinfo is None:
                    user_created = user_created.replace(tzinfo=timezone.utc)

                # Skip if user didn't exist yet
                if user_created and event_start and user_created > event_start:
                    continue

                # Get user stats
                user_stats = await get_user_stats(db, user_id)
                hof_stats = await get_user_hall_of_fame_stats(db, user_id)
                user_event_types = await get_user_event_types(db, user_id)
                friends_enrolled = await get_friends_enrolled_count(db, user_id, event.id)

                # Calculate derived features
                total_events = user_stats["total_events"]
                win_rate = user_stats["total_wins"] / total_events if total_events > 0 else 0
                podium_rate = user_stats["podium_finishes"] / total_events if total_events > 0 else 0

                # User age at event
                user_age_days = 0
                if user_created and event_start:
                    delta = event_start - user_created
                    user_age_days = max(0, delta.total_seconds() / 86400)

                # Days since last event
                days_since_last = 365
                if user_stats["last_event_date"]:
                    last_event_date = user_stats["last_event_date"]
                    if last_event_date.tzinfo is None:
                        last_event_date = last_event_date.replace(tzinfo=timezone.utc)
                    delta = event_start - last_event_date
                    days_since_last = max(0, delta.total_seconds() / 86400)

                sample = {
                    # Target
                    "enrolled": 0,

                    # User basic stats
                    "user_event_count": user_stats["total_events"],
                    "user_catch_count": user_stats["total_catches"],
                    "user_species_count": user_stats["unique_species_count"],
                    "user_max_catch_cm": user_stats["largest_catch_cm"],
                    "user_wins": user_stats["total_wins"],
                    "user_podiums": user_stats["podium_finishes"],

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
                    "avg_catch_length": user_stats["avg_catch_length"],
                    "consecutive_events": user_stats["consecutive_events"],
                    "max_consecutive_events": user_stats["max_consecutive_events"],

                    # Activity features
                    "events_this_year": user_stats["events_this_year"],
                    "days_since_last_event": days_since_last,

                    # Context features
                    "user_age_at_event": user_age_days,
                    "event_type_id": event.event_type_id or 1,
                    "event_day_of_week": event_start.weekday(),
                    "event_month": event_start.month,
                    "event_enrollment_count": enrollment_count,
                    "has_done_event_type": 1 if event.event_type_id in user_event_types else 0,
                    "friends_enrolled_count": friends_enrolled,
                }
                samples.append(sample)

    print(f"Built {len(samples)} training samples")
    return pd.DataFrame(samples)


def train_model(df: pd.DataFrame) -> tuple:
    """Train the ML model and return model, scaler, and feature names."""
    print("\nTraining model...")

    # Define feature columns (all except 'enrolled')
    feature_cols = [
        # User basic stats
        "user_event_count",
        "user_catch_count",
        "user_species_count",
        "user_max_catch_cm",
        "user_wins",
        "user_podiums",

        # Hall of Fame features
        "hof_entry_count",
        "hof_world_champion",
        "hof_national_champion",
        "hof_world_podium",
        "hof_national_podium",
        "hof_best_tier",

        # Performance features
        "win_rate",
        "podium_rate",
        "avg_catch_length",
        "consecutive_events",
        "max_consecutive_events",

        # Activity features
        "events_this_year",
        "days_since_last_event",

        # Context features
        "user_age_at_event",
        "event_type_id",
        "event_day_of_week",
        "event_month",
        "event_enrollment_count",
        "has_done_event_type",
        "friends_enrolled_count",
    ]

    X = df[feature_cols].values
    y = df["enrolled"].values

    print(f"Features: {len(feature_cols)}")
    print(f"Samples: {len(y)}")
    print(f"Positive rate: {y.mean():.2%}")

    # Split data
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # Scale features
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # Train model - GradientBoosting for better performance
    model = GradientBoostingClassifier(
        n_estimators=100,
        learning_rate=0.1,
        max_depth=5,
        min_samples_split=10,
        min_samples_leaf=5,
        random_state=42,
    )

    print("Fitting model...")
    model.fit(X_train_scaled, y_train)

    # Evaluate
    y_pred = model.predict(X_test_scaled)
    y_pred_proba = model.predict_proba(X_test_scaled)[:, 1]

    roc_auc = roc_auc_score(y_test, y_pred_proba)
    print(f"\nTest ROC-AUC: {roc_auc:.4f}")

    # Cross-validation
    cv_scores = cross_val_score(
        model, X_train_scaled, y_train, cv=5, scoring="roc_auc"
    )
    print(f"CV ROC-AUC: {cv_scores.mean():.4f} (+/- {cv_scores.std() * 2:.4f})")

    # Classification report
    print("\nClassification Report:")
    print(classification_report(y_test, y_pred, target_names=["Not Enrolled", "Enrolled"]))

    # Feature importance
    print("\nTop 10 Feature Importances:")
    importance_df = pd.DataFrame({
        "feature": feature_cols,
        "importance": model.feature_importances_
    }).sort_values("importance", ascending=False)
    print(importance_df.head(10).to_string(index=False))

    return model, scaler, feature_cols, roc_auc, cv_scores.mean()


def save_model(
    model,
    scaler,
    feature_cols: list,
    roc_auc: float,
    cv_roc_auc: float,
    num_samples: int,
):
    """Save the trained model and associated files."""
    output_dir = Path("models/event_recommendations")
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save model
    model_path = output_dir / f"event_recommendation_v2_model.joblib"
    joblib.dump(model, model_path)
    print(f"\nSaved model: {model_path}")

    # Save scaler
    scaler_path = output_dir / f"event_recommendation_v2_scaler.joblib"
    joblib.dump(scaler, scaler_path)
    print(f"Saved scaler: {scaler_path}")

    # Save feature list
    features_path = output_dir / f"event_recommendation_v2_features.txt"
    with open(features_path, "w") as f:
        for feat in feature_cols:
            f.write(f"{feat}\n")
    print(f"Saved features: {features_path}")

    # Save metadata
    metadata = {
        "version": "v2",
        "trained_at": datetime.now().isoformat(),
        "num_samples": num_samples,
        "num_features": len(feature_cols),
        "roc_auc": roc_auc,
        "cv_roc_auc": cv_roc_auc,
        "feature_groups": {
            "user_basic": ["user_event_count", "user_catch_count", "user_species_count",
                          "user_max_catch_cm", "user_wins", "user_podiums"],
            "hall_of_fame": ["hof_entry_count", "hof_world_champion", "hof_national_champion",
                            "hof_world_podium", "hof_national_podium", "hof_best_tier"],
            "performance": ["win_rate", "podium_rate", "avg_catch_length",
                           "consecutive_events", "max_consecutive_events"],
            "activity": ["events_this_year", "days_since_last_event"],
            "context": ["user_age_at_event", "event_type_id", "event_day_of_week",
                       "event_month", "event_enrollment_count", "has_done_event_type",
                       "friends_enrolled_count"],
        }
    }

    metadata_path = output_dir / "event_recommendation_v2_metadata.json"
    import json
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Saved metadata: {metadata_path}")

    print(f"\n{'='*60}")
    print("Model Training Complete!")
    print(f"{'='*60}")
    print(f"Model: {model_path}")
    print(f"Features: {len(feature_cols)}")
    print(f"Samples: {num_samples}")
    print(f"ROC-AUC: {roc_auc:.4f}")
    print(f"CV ROC-AUC: {cv_roc_auc:.4f}")
    print(f"\nTo activate this model, upload via admin UI or update ML model record.")


async def main():
    """Main training function."""
    print("="*60)
    print("Event Recommendations ML Model Training - v2")
    print("="*60)
    print(f"Started at: {datetime.now().isoformat()}")
    print()

    # Connect to database
    db = await get_db_session()

    try:
        # Build training data
        df = await build_training_data(db)

        if len(df) < 100:
            print("\nWarning: Very small dataset. Model may not be reliable.")

        if len(df) == 0:
            print("\nError: No training data found. Cannot train model.")
            return

        # Train model
        model, scaler, feature_cols, roc_auc, cv_roc_auc = train_model(df)

        # Save model
        save_model(
            model=model,
            scaler=scaler,
            feature_cols=feature_cols,
            roc_auc=roc_auc,
            cv_roc_auc=cv_roc_auc,
            num_samples=len(df),
        )

    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
