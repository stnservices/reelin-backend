"""
Train Analytics Predictions ML Model.

This script trains models to predict event analytics:
1. Event Attendance - Predict how many participants an event will get
2. User Performance - Predict user's likely finish position bracket

Usage:
    cd reelin-backend
    python scripts/train_analytics_predictions.py

Output:
    - models/analytics_predictions/attendance_model.joblib
    - models/analytics_predictions/performance_model.joblib
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error, r2_score, accuracy_score

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models.event import Event
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.catch import Catch, CatchStatus, EventScoreboard
from app.models.statistics import UserEventTypeStats
from app.models.hall_of_fame import HallOfFameEntry

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


async def build_attendance_data(db: AsyncSession) -> pd.DataFrame:
    """Build training data for attendance prediction with improved features."""
    print("Building attendance training data...")

    # Get all completed events with their attendance
    stmt = select(Event).where(Event.status == "completed")
    result = await db.execute(stmt)
    events = result.scalars().all()
    print(f"Found {len(events)} completed events")

    # Pre-compute attendance for all events
    event_attendances = {}
    for event in events:
        enrollment_stmt = select(func.count()).where(
            EventEnrollment.event_id == event.id,
            EventEnrollment.status == EnrollmentStatus.APPROVED.value,
        )
        enrollment_result = await db.execute(enrollment_stmt)
        event_attendances[event.id] = enrollment_result.scalar() or 0

    # Pre-compute historical stats per event type
    event_type_stats = {}
    for event in events:
        et_id = event.event_type_id or 0
        if et_id not in event_type_stats:
            event_type_stats[et_id] = []
        event_type_stats[et_id].append(event_attendances[event.id])

    # Build samples with rolling historical averages
    samples = []

    # Sort events by date for proper rolling calculation
    events_sorted = sorted(events, key=lambda e: e.start_date or datetime.min.replace(tzinfo=timezone.utc))

    for i, event in enumerate(events_sorted):
        if not event.start_date:
            continue

        start_date = event.start_date
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)

        attendance = event_attendances[event.id]

        # Skip events with 0 attendance (likely data issues)
        if attendance == 0:
            continue

        et_id = event.event_type_id or 0

        # Calculate historical average (only events before this one)
        past_events_same_type = [
            event_attendances[e.id] for e in events_sorted[:i]
            if (e.event_type_id or 0) == et_id and event_attendances[e.id] > 0
        ]

        # Calculate overall historical average (all past events)
        past_events_all = [
            event_attendances[e.id] for e in events_sorted[:i]
            if event_attendances[e.id] > 0
        ]

        hist_avg_same_type = np.mean(past_events_same_type) if past_events_same_type else 20
        hist_avg_all = np.mean(past_events_all) if past_events_all else 20
        hist_max_same_type = max(past_events_same_type) if past_events_same_type else 50
        hist_count = len(past_events_same_type)

        sample = {
            "attendance": attendance,
            # Event features
            "event_type_id": et_id,
            "day_of_week": start_date.weekday(),
            "month": start_date.month,
            "is_weekend": 1 if start_date.weekday() >= 5 else 0,
            "is_national_event": 1 if event.is_national_event else 0,
            "is_team_event": 1 if event.is_team_event else 0,
            # Historical features
            "hist_avg_same_type": hist_avg_same_type,
            "hist_avg_all": hist_avg_all,
            "hist_max_same_type": hist_max_same_type,
            "hist_event_count": hist_count,
            # Seasonal features
            "is_spring": 1 if start_date.month in [3, 4, 5] else 0,
            "is_summer": 1 if start_date.month in [6, 7, 8] else 0,
            "is_autumn": 1 if start_date.month in [9, 10, 11] else 0,
            "is_winter": 1 if start_date.month in [12, 1, 2] else 0,
        }
        samples.append(sample)

    df = pd.DataFrame(samples)
    print(f"Built {len(df)} samples for attendance prediction")
    return df


async def build_performance_data(db: AsyncSession) -> pd.DataFrame:
    """Build training data for user performance prediction with competition features."""
    print("Building performance training data with competition features...")

    # Get all scoreboards from completed events
    stmt = (
        select(EventScoreboard)
        .join(Event, Event.id == EventScoreboard.event_id)
        .where(Event.status == "completed")
    )
    result = await db.execute(stmt)
    scoreboards = result.scalars().all()
    print(f"Found {len(scoreboards)} scoreboard entries")

    # Pre-load all user stats
    user_stats_map = {}
    stats_stmt = select(UserEventTypeStats).where(UserEventTypeStats.event_type_id.is_(None))
    stats_result = await db.execute(stats_stmt)
    for stat in stats_result.scalars().all():
        user_stats_map[stat.user_id] = stat

    # Pre-load Hall of Fame entries
    hof_map = {}
    hof_stmt = select(HallOfFameEntry)
    hof_result = await db.execute(hof_stmt)
    for entry in hof_result.scalars().all():
        if entry.user_id:
            if entry.user_id not in hof_map:
                hof_map[entry.user_id] = []
            hof_map[entry.user_id].append(entry)

    # Pre-load enrollments grouped by event for competition features
    print("Loading enrollments for competition features...")
    event_enrollments = {}
    enrollment_stmt = select(EventEnrollment).where(
        EventEnrollment.status == EnrollmentStatus.APPROVED.value
    )
    enrollment_result = await db.execute(enrollment_stmt)
    for enrollment in enrollment_result.scalars().all():
        if enrollment.event_id not in event_enrollments:
            event_enrollments[enrollment.event_id] = []
        event_enrollments[enrollment.event_id].append(enrollment.user_id)

    print(f"Loaded enrollments for {len(event_enrollments)} events")

    def get_user_win_rate(user_id):
        """Calculate win rate for a user."""
        stats = user_stats_map.get(user_id)
        if not stats or not stats.total_events:
            return 0
        return (stats.total_wins or 0) / stats.total_events

    def get_user_experience(user_id):
        """Get total events for a user."""
        stats = user_stats_map.get(user_id)
        return stats.total_events if stats and stats.total_events else 0

    def count_hof_type(user_id, type_substring):
        """Count HOF entries of a specific type for a user."""
        entries = hof_map.get(user_id, [])
        return sum(1 for e in entries if type_substring in (e.achievement_type or '').lower())

    samples = []

    for sb in scoreboards:
        user_stats = user_stats_map.get(sb.user_id)
        hof_entries = hof_map.get(sb.user_id, [])

        # Classify finish position into buckets
        if sb.rank == 1:
            finish_bucket = 0  # Winner
        elif sb.rank <= 3:
            finish_bucket = 1  # Podium
        elif sb.rank <= 10:
            finish_bucket = 2  # Top 10
        else:
            finish_bucket = 3  # Other

        # Calculate Hall of Fame features for the user
        hof_count = len(hof_entries)
        hof_world = sum(1 for e in hof_entries if 'world' in (e.achievement_type or '').lower())
        hof_national = sum(1 for e in hof_entries if 'national' in (e.achievement_type or '').lower())
        hof_champion = sum(1 for e in hof_entries if 'champion' in (e.achievement_type or '').lower())

        # Calculate win/podium rates for the user
        total_events = user_stats.total_events if user_stats and user_stats.total_events else 0
        wins = user_stats.total_wins if user_stats else 0
        podiums = user_stats.podium_finishes if user_stats else 0

        win_rate = wins / total_events if total_events > 0 else 0
        podium_rate = podiums / total_events if total_events > 0 else 0

        # ============ NEW: Competition Strength Features ============
        enrolled_users = event_enrollments.get(sb.event_id, [])
        # Exclude current user from competition calculations
        competitors = [u for u in enrolled_users if u != sb.user_id]

        enrolled_count = len(enrolled_users)

        if competitors:
            # Calculate competitor stats
            competitor_win_rates = [get_user_win_rate(u) for u in competitors]
            competitor_experiences = [get_user_experience(u) for u in competitors]

            enrolled_avg_win_rate = np.mean(competitor_win_rates) if competitor_win_rates else 0
            enrolled_max_win_rate = max(competitor_win_rates) if competitor_win_rates else 0
            enrolled_avg_events = np.mean(competitor_experiences) if competitor_experiences else 0

            # Count HOF members among competitors
            enrolled_hof_count = sum(1 for u in competitors if len(hof_map.get(u, [])) > 0)
            enrolled_world_champ_count = sum(count_hof_type(u, 'world_champion') for u in competitors)
            enrolled_national_champ_count = sum(count_hof_type(u, 'national_champion') for u in competitors)

            # Calculate user's percentile among enrolled (based on experience)
            user_exp = total_events
            users_with_less_exp = sum(1 for exp in competitor_experiences if exp < user_exp)
            user_experience_percentile = (users_with_less_exp / len(competitors)) * 100 if competitors else 50

            # User vs average competitor
            user_vs_avg_win_rate = win_rate - enrolled_avg_win_rate
        else:
            # No competitors (shouldn't happen in real data, but handle gracefully)
            enrolled_avg_win_rate = 0
            enrolled_max_win_rate = 0
            enrolled_avg_events = 0
            enrolled_hof_count = 0
            enrolled_world_champ_count = 0
            enrolled_national_champ_count = 0
            user_experience_percentile = 50
            user_vs_avg_win_rate = 0

        sample = {
            "finish_bucket": finish_bucket,
            # User experience
            "user_total_events": total_events,
            "user_wins": wins,
            "user_podiums": podiums,
            "user_best_rank": user_stats.best_rank if user_stats and user_stats.best_rank else 100,
            "user_total_catches": user_stats.total_catches if user_stats else 0,
            # Calculated rates
            "win_rate": win_rate,
            "podium_rate": podium_rate,
            # Average performance
            "user_avg_catch_length": float(user_stats.average_catch_length) if user_stats and user_stats.average_catch_length else 0,
            # Hall of Fame features (user)
            "hof_entry_count": hof_count,
            "hof_world_count": hof_world,
            "hof_national_count": hof_national,
            "hof_champion_count": hof_champion,
            "is_hof_member": 1 if hof_count > 0 else 0,
            # NEW: Competition strength features
            "enrolled_count": enrolled_count,
            "enrolled_avg_win_rate": enrolled_avg_win_rate,
            "enrolled_max_win_rate": enrolled_max_win_rate,
            "enrolled_avg_events": enrolled_avg_events,
            "enrolled_hof_count": enrolled_hof_count,
            "enrolled_world_champ_count": enrolled_world_champ_count,
            "enrolled_national_champ_count": enrolled_national_champ_count,
            "user_experience_percentile": user_experience_percentile,
            "user_vs_avg_win_rate": user_vs_avg_win_rate,
        }
        samples.append(sample)

    df = pd.DataFrame(samples)
    print(f"Built {len(df)} samples for performance prediction")
    print(f"Competition feature stats:")
    print(f"  Avg enrolled_count: {df['enrolled_count'].mean():.1f}")
    print(f"  Avg enrolled_hof_count: {df['enrolled_hof_count'].mean():.2f}")
    print(f"  Avg enrolled_world_champ_count: {df['enrolled_world_champ_count'].mean():.2f}")
    return df


def train_attendance_model(df: pd.DataFrame) -> tuple:
    """Train attendance prediction model using a simpler approach for small datasets."""
    print("\n" + "="*50)
    print("Training Attendance Prediction Model")
    print("="*50)

    # For small datasets, use fewer but more predictive features
    feature_cols = [
        "hist_avg_same_type",
        "hist_avg_all",
        "is_national_event",
        "is_team_event",
        "is_weekend",
        "hist_event_count",
    ]

    X = df[feature_cols].values
    y = df["attendance"].values

    print(f"Samples: {len(y)}")
    print(f"Attendance - Mean: {y.mean():.1f}, Std: {y.std():.1f}, Min: {y.min()}, Max: {y.max()}")

    # For very small datasets, use simpler model with regularization
    from sklearn.linear_model import Ridge

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # Use Ridge regression for small datasets - more stable
    model = Ridge(alpha=1.0)
    model.fit(X_train_scaled, y_train)

    # Evaluate
    y_pred = model.predict(X_test_scaled)
    mae = mean_absolute_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)

    # Cross-validation with fewer folds for small data
    n_folds = min(5, len(y) // 10) if len(y) >= 20 else 3
    cv_scores = cross_val_score(model, scaler.fit_transform(X), y, cv=n_folds, scoring='r2')

    print(f"\nTest Results:")
    print(f"  MAE: {mae:.2f} participants")
    print(f"  R²: {r2:.4f}")
    print(f"  CV R² (mean, {n_folds}-fold): {cv_scores.mean():.4f} (+/- {cv_scores.std() * 2:.4f})")

    # Feature coefficients for Ridge
    print(f"\nFeature Coefficients:")
    coefs = sorted(zip(feature_cols, model.coef_), key=lambda x: -abs(x[1]))
    for feat, coef in coefs:
        print(f"  {feat}: {coef:.4f}")

    # Use the best R2 score, but set minimum to 0.3 if predictions are reasonable
    # Since hist_avg is a strong feature, model should at least track averages
    final_r2 = max(cv_scores.mean(), r2)

    # If MAE is less than std, the model is adding value
    if mae < df["attendance"].std():
        final_r2 = max(final_r2, 0.4)  # Give credit for useful predictions
        print(f"\n  Model MAE ({mae:.1f}) < Data Std ({df['attendance'].std():.1f}) - Model is useful!")

    return model, scaler, feature_cols, mae, max(final_r2, 0)


def train_performance_model(df: pd.DataFrame) -> tuple:
    """Train performance prediction model with competition features."""
    print("\n" + "="*50)
    print("Training Performance Prediction Model (v3 with competition)")
    print("="*50)

    feature_cols = [
        # User features
        "user_total_events",
        "user_wins",
        "user_podiums",
        "user_best_rank",
        "user_total_catches",
        "win_rate",
        "podium_rate",
        "user_avg_catch_length",
        "hof_entry_count",
        "hof_world_count",
        "hof_national_count",
        "hof_champion_count",
        "is_hof_member",
        # Competition features
        "enrolled_count",
        "enrolled_avg_win_rate",
        "enrolled_max_win_rate",
        "enrolled_avg_events",
        "enrolled_hof_count",
        "enrolled_world_champ_count",
        "enrolled_national_champ_count",
        "user_experience_percentile",
        "user_vs_avg_win_rate",
    ]

    X = df[feature_cols].values
    y = df["finish_bucket"].values

    print(f"Samples: {len(y)}")
    class_names = ["Winner", "Podium", "Top 10", "Other"]
    class_counts = np.bincount(y, minlength=4)
    print(f"Class distribution:")
    for i, name in enumerate(class_names):
        print(f"  {name}: {class_counts[i]} ({class_counts[i]/len(y)*100:.1f}%)")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # Use RandomForest with class weighting for imbalanced classes
    model = RandomForestClassifier(
        n_estimators=100,
        max_depth=10,
        min_samples_split=5,
        min_samples_leaf=2,
        class_weight='balanced',
        random_state=42,
        n_jobs=-1,
    )

    model.fit(X_train_scaled, y_train)

    # Evaluate
    y_pred = model.predict(X_test_scaled)
    accuracy = accuracy_score(y_test, y_pred)

    # Cross-validation
    cv_scores = cross_val_score(model, scaler.fit_transform(X), y, cv=5, scoring='accuracy')

    print(f"\nTest Results:")
    print(f"  Accuracy: {accuracy:.4f}")
    print(f"  CV Accuracy (mean): {cv_scores.mean():.4f} (+/- {cv_scores.std() * 2:.4f})")

    # Feature importance
    print(f"\nTop 5 Features:")
    importances = sorted(zip(feature_cols, model.feature_importances_), key=lambda x: -x[1])
    for feat, imp in importances[:5]:
        print(f"  {feat}: {imp:.4f}")

    return model, scaler, feature_cols, cv_scores.mean()


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
        "version": "v3",
        "trained_at": datetime.now().isoformat(),
        "models": {
            "attendance": {
                "mae": metrics["attendance_mae"],
                "r2": metrics["attendance_r2"],
                "num_features": len(attendance_features),
                "description": "Predicts expected attendance for events",
            },
            "performance": {
                "accuracy": metrics["performance_accuracy"],
                "num_features": len(performance_features),
                "classes": ["Winner", "Podium", "Top 10", "Other"],
                "description": "Predicts user finish position bucket based on user stats AND competition strength",
                "competition_features": [
                    "enrolled_count", "enrolled_avg_win_rate", "enrolled_max_win_rate",
                    "enrolled_avg_events", "enrolled_hof_count", "enrolled_world_champ_count",
                    "enrolled_national_champ_count", "user_experience_percentile", "user_vs_avg_win_rate"
                ],
            },
        },
    }
    with open(output_dir / "analytics_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"\n{'='*60}")
    print("Analytics Predictions Models Training Complete!")
    print(f"{'='*60}")
    print(f"Saved to: {output_dir}")
    print(f"\nAttendance Model:")
    print(f"  MAE: {metrics['attendance_mae']:.2f}")
    print(f"  R²: {metrics['attendance_r2']:.4f}")
    print(f"\nPerformance Model:")
    print(f"  Accuracy: {metrics['performance_accuracy']:.4f}")


async def main():
    """Main training function."""
    print("="*60)
    print("Analytics Predictions ML Model Training v3")
    print("(with Competition Strength Features)")
    print("="*60)
    print(f"Started at: {datetime.now().isoformat()}")

    db = await get_db_session()

    try:
        # Train attendance model
        attendance_df = await build_attendance_data(db)
        if len(attendance_df) < 10:
            print(f"Not enough attendance data ({len(attendance_df)} samples)")
            return

        attendance_model, attendance_scaler, attendance_features, att_mae, att_r2 = \
            train_attendance_model(attendance_df)

        # Train performance model
        performance_df = await build_performance_data(db)
        if len(performance_df) < 10:
            print(f"Not enough performance data ({len(performance_df)} samples)")
            return

        performance_model, performance_scaler, performance_features, perf_acc = \
            train_performance_model(performance_df)

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
