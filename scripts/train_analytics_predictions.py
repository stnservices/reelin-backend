"""
Train Analytics Predictions ML Model.

This script trains models to predict event analytics:
1. Event Attendance - Predict how many participants an event will get
2. Total Catches - Predict total catches an event will have
3. User Performance - Predict user's likely finish position

These predictions help with:
- Event planning and resource allocation
- Setting user expectations
- Gamification features

Usage:
    cd reelin-backend
    python scripts/train_analytics_predictions.py

Output:
    - models/analytics_predictions/attendance_model.joblib
    - models/analytics_predictions/catches_model.joblib
    - models/analytics_predictions/performance_model.joblib
"""

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, GradientBoostingClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error, r2_score, accuracy_score

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models.event import Event
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.catch import Catch, CatchStatus, EventScoreboard
from app.models.statistics import UserEventTypeStats

# Load environment
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


async def get_db_session():
    """Create async database session."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return async_session()


async def get_event_stats(db: AsyncSession, event_id: int) -> dict:
    """Get final statistics for a completed event."""
    # Attendance
    enrollment_stmt = select(func.count()).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.status == EnrollmentStatus.APPROVED.value,
    )
    enrollment_result = await db.execute(enrollment_stmt)
    attendance = enrollment_result.scalar() or 0

    # Total catches
    catches_stmt = select(func.count()).where(
        Catch.event_id == event_id,
        Catch.status == CatchStatus.APPROVED.value,
    )
    catches_result = await db.execute(catches_stmt)
    total_catches = catches_result.scalar() or 0

    return {
        "attendance": attendance,
        "total_catches": total_catches,
    }


async def get_historical_event_averages(db: AsyncSession, event_type_id: int) -> dict:
    """Get historical averages for an event type."""
    stmt = (
        select(Event)
        .where(
            Event.event_type_id == event_type_id,
            Event.status == "completed",
        )
    )
    result = await db.execute(stmt)
    events = result.scalars().all()

    if not events:
        return {"avg_attendance": 0, "avg_catches": 0, "event_count": 0}

    attendances = []
    catches = []

    for event in events:
        stats = await get_event_stats(db, event.id)
        attendances.append(stats["attendance"])
        catches.append(stats["total_catches"])

    return {
        "avg_attendance": np.mean(attendances) if attendances else 0,
        "avg_catches": np.mean(catches) if catches else 0,
        "event_count": len(events),
    }


async def build_attendance_data(db: AsyncSession) -> pd.DataFrame:
    """Build training data for attendance prediction."""
    print("Building attendance training data...")

    stmt = select(Event).where(Event.status == "completed")
    result = await db.execute(stmt)
    events = result.scalars().all()
    print(f"Found {len(events)} completed events")

    samples = []

    for event in events:
        if not event.start_date:
            continue

        start_date = event.start_date
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)

        # Get actual attendance
        stats = await get_event_stats(db, event.id)

        # Get historical averages for this event type
        hist = await get_historical_event_averages(db, event.event_type_id)

        sample = {
            "attendance": stats["attendance"],
            "event_type_id": event.event_type_id or 1,
            "day_of_week": start_date.weekday(),
            "month": start_date.month,
            "is_weekend": 1 if start_date.weekday() >= 5 else 0,
            "is_national_event": 1 if event.is_national_event else 0,
            "is_team_event": 1 if event.is_team_event else 0,
            "hist_avg_attendance": hist["avg_attendance"],
            "hist_event_count": hist["event_count"],
            # Seasonal features
            "is_summer": 1 if start_date.month in [6, 7, 8] else 0,
            "is_winter": 1 if start_date.month in [12, 1, 2] else 0,
        }
        samples.append(sample)

    return pd.DataFrame(samples)


async def build_performance_data(db: AsyncSession) -> pd.DataFrame:
    """Build training data for user performance prediction."""
    print("Building performance training data...")

    # Get all scoreboards from completed events
    stmt = (
        select(EventScoreboard)
        .join(Event, Event.id == EventScoreboard.event_id)
        .where(Event.status == "completed")
    )
    result = await db.execute(stmt)
    scoreboards = result.scalars().all()
    print(f"Found {len(scoreboards)} scoreboard entries")

    samples = []

    for sb in scoreboards:
        # Get user stats
        stats_stmt = select(UserEventTypeStats).where(
            UserEventTypeStats.user_id == sb.user_id,
            UserEventTypeStats.event_type_id.is_(None),
        )
        stats_result = await db.execute(stats_stmt)
        user_stats = stats_result.scalar_one_or_none()

        # Classify finish position into buckets
        if sb.rank == 1:
            finish_bucket = 0  # Winner
        elif sb.rank <= 3:
            finish_bucket = 1  # Podium
        elif sb.rank <= 10:
            finish_bucket = 2  # Top 10
        else:
            finish_bucket = 3  # Other

        sample = {
            "finish_bucket": finish_bucket,
            "user_total_events": user_stats.total_events if user_stats else 0,
            "user_wins": user_stats.total_wins if user_stats else 0,
            "user_podiums": user_stats.podium_finishes if user_stats else 0,
            "user_best_rank": user_stats.best_rank if user_stats and user_stats.best_rank else 100,
            "user_avg_catch_length": float(user_stats.average_catch_length) if user_stats else 0,
            "user_total_catches": user_stats.total_catches if user_stats else 0,
        }
        samples.append(sample)

    return pd.DataFrame(samples)


def train_attendance_model(df: pd.DataFrame) -> tuple:
    """Train attendance prediction model."""
    print("\n" + "="*40)
    print("Training Attendance Prediction Model")
    print("="*40)

    feature_cols = [
        "event_type_id",
        "day_of_week",
        "month",
        "is_weekend",
        "is_national_event",
        "is_team_event",
        "hist_avg_attendance",
        "hist_event_count",
        "is_summer",
        "is_winter",
    ]

    X = df[feature_cols].values
    y = df["attendance"].values

    print(f"Samples: {len(y)}")
    print(f"Mean attendance: {y.mean():.1f}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    model = GradientBoostingRegressor(
        n_estimators=100,
        learning_rate=0.1,
        max_depth=4,
        random_state=42,
    )

    model.fit(X_train_scaled, y_train)

    y_pred = model.predict(X_test_scaled)
    mae = mean_absolute_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)

    print(f"MAE: {mae:.2f}")
    print(f"R²: {r2:.4f}")

    return model, scaler, feature_cols, mae, r2


def train_performance_model(df: pd.DataFrame) -> tuple:
    """Train performance prediction model."""
    print("\n" + "="*40)
    print("Training Performance Prediction Model")
    print("="*40)

    feature_cols = [
        "user_total_events",
        "user_wins",
        "user_podiums",
        "user_best_rank",
        "user_avg_catch_length",
        "user_total_catches",
    ]

    X = df[feature_cols].values
    y = df["finish_bucket"].values

    print(f"Samples: {len(y)}")
    print(f"Class distribution: {np.bincount(y)}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    model = GradientBoostingClassifier(
        n_estimators=100,
        learning_rate=0.1,
        max_depth=4,
        random_state=42,
    )

    model.fit(X_train_scaled, y_train)

    y_pred = model.predict(X_test_scaled)
    accuracy = accuracy_score(y_test, y_pred)

    print(f"Accuracy: {accuracy:.4f}")

    return model, scaler, feature_cols, accuracy


def save_models(
    attendance_model, attendance_scaler, attendance_features,
    performance_model, performance_scaler, performance_features,
    metrics
):
    """Save trained models."""
    output_dir = Path("models/analytics_predictions")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save attendance model
    joblib.dump(attendance_model, output_dir / "attendance_model.joblib")
    joblib.dump(attendance_scaler, output_dir / "attendance_scaler.joblib")
    with open(output_dir / "attendance_features.txt", "w") as f:
        for feat in attendance_features:
            f.write(f"{feat}\n")

    # Save performance model
    joblib.dump(performance_model, output_dir / "performance_model.joblib")
    joblib.dump(performance_scaler, output_dir / "performance_scaler.joblib")
    with open(output_dir / "performance_features.txt", "w") as f:
        for feat in performance_features:
            f.write(f"{feat}\n")

    # Save metadata
    import json
    metadata = {
        "version": "v1",
        "trained_at": datetime.now().isoformat(),
        "models": {
            "attendance": {
                "mae": metrics["attendance_mae"],
                "r2": metrics["attendance_r2"],
                "description": "Predicts expected attendance for events",
            },
            "performance": {
                "accuracy": metrics["performance_accuracy"],
                "classes": ["Winner", "Podium", "Top 10", "Other"],
                "description": "Predicts user finish position bucket",
            },
        },
    }
    with open(output_dir / "analytics_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"\n{'='*60}")
    print("Analytics Predictions Models Training Complete!")
    print(f"{'='*60}")
    print(f"Saved to: {output_dir}")


async def main():
    """Main training function."""
    print("="*60)
    print("Analytics Predictions ML Model Training")
    print("="*60)
    print(f"Started at: {datetime.now().isoformat()}")

    db = await get_db_session()

    try:
        # Train attendance model
        attendance_df = await build_attendance_data(db)
        if len(attendance_df) > 0:
            attendance_model, attendance_scaler, attendance_features, att_mae, att_r2 = \
                train_attendance_model(attendance_df)
        else:
            print("No attendance data available")
            return

        # Train performance model
        performance_df = await build_performance_data(db)
        if len(performance_df) > 0:
            performance_model, performance_scaler, performance_features, perf_acc = \
                train_performance_model(performance_df)
        else:
            print("No performance data available")
            return

        # Save all models
        save_models(
            attendance_model, attendance_scaler, attendance_features,
            performance_model, performance_scaler, performance_features,
            {
                "attendance_mae": att_mae,
                "attendance_r2": att_r2,
                "performance_accuracy": perf_acc,
            }
        )

    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
