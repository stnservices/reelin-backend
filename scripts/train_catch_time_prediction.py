"""
Train Catch Time Prediction ML Model.

This script trains a model to predict optimal catch times based on:
- Time patterns (hour, day of week, month)
- User historical catch patterns
- Location patterns
- Event characteristics
- Species being targeted

The model predicts probability of successful catch for each hour of the day.

Usage:
    cd reelin-backend
    python scripts/train_catch_time_prediction.py

Output:
    - models/catch_time/catch_time_model.joblib
    - models/catch_time/catch_time_scaler.joblib
    - models/catch_time/catch_time_features.txt
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, classification_report

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models.catch import Catch, CatchStatus
from app.models.event import Event
from app.models.fish import Fish

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


async def get_user_catch_patterns(db: AsyncSession, user_id: int) -> dict:
    """Get user's historical catch patterns by hour."""
    # Get user's approved catches with catch_time
    stmt = select(Catch).where(
        Catch.user_id == user_id,
        Catch.status == CatchStatus.APPROVED.value,
        Catch.catch_time.isnot(None),
    )
    result = await db.execute(stmt)
    catches = result.scalars().all()

    if not catches:
        return {
            "preferred_hours": [],
            "total_catches": 0,
            "catches_by_hour": {},
        }

    # Count catches by hour
    hour_counts = {}
    for catch in catches:
        hour = catch.catch_time.hour
        hour_counts[hour] = hour_counts.get(hour, 0) + 1

    # Find top 3 preferred hours
    sorted_hours = sorted(hour_counts.items(), key=lambda x: x[1], reverse=True)
    preferred_hours = [h[0] for h in sorted_hours[:3]]

    return {
        "preferred_hours": preferred_hours,
        "total_catches": len(catches),
        "catches_by_hour": hour_counts,
    }


async def build_training_data(db: AsyncSession) -> pd.DataFrame:
    """Build training dataset from historical catches."""
    print("Building training data...")

    # Get all approved catches with catch_time
    stmt = (
        select(Catch)
        .where(
            Catch.status == CatchStatus.APPROVED.value,
            Catch.catch_time.isnot(None),
        )
    )
    result = await db.execute(stmt)
    catches = result.scalars().all()
    print(f"Found {len(catches)} approved catches with catch_time")

    if len(catches) < 50:
        print("Warning: Very few catches with timing data. Model may not be reliable.")

    samples = []

    for catch in catches:
        catch_time = catch.catch_time
        if catch_time.tzinfo is None:
            catch_time = catch_time.replace(tzinfo=timezone.utc)

        # Get user's catch patterns
        user_patterns = await get_user_catch_patterns(db, catch.user_id)

        # For positive sample (actual catch)
        sample = {
            "is_catch": 1,
            "hour": catch_time.hour,
            "day_of_week": catch_time.weekday(),
            "month": catch_time.month,
            "fish_id": catch.fish_id or 0,
            "length": catch.length or 0,
            "has_location": 1 if (catch.location_lat and catch.location_lng) else 0,
            "user_total_catches": user_patterns["total_catches"],
            "is_user_preferred_hour": 1 if catch_time.hour in user_patterns["preferred_hours"] else 0,
            "morning": 1 if 5 <= catch_time.hour < 10 else 0,
            "midday": 1 if 10 <= catch_time.hour < 14 else 0,
            "afternoon": 1 if 14 <= catch_time.hour < 18 else 0,
            "evening": 1 if 18 <= catch_time.hour < 22 else 0,
            "night": 1 if catch_time.hour >= 22 or catch_time.hour < 5 else 0,
        }
        samples.append(sample)

        # Create negative samples (hours when user didn't catch)
        # Sample a few hours where the user was active (same day) but didn't catch
        for offset in [3, 6, 9, 12]:
            neg_hour = (catch_time.hour + offset) % 24
            if neg_hour not in user_patterns.get("catches_by_hour", {}):
                neg_sample = sample.copy()
                neg_sample["is_catch"] = 0
                neg_sample["hour"] = neg_hour
                neg_sample["is_user_preferred_hour"] = 1 if neg_hour in user_patterns["preferred_hours"] else 0
                neg_sample["morning"] = 1 if 5 <= neg_hour < 10 else 0
                neg_sample["midday"] = 1 if 10 <= neg_hour < 14 else 0
                neg_sample["afternoon"] = 1 if 14 <= neg_hour < 18 else 0
                neg_sample["evening"] = 1 if 18 <= neg_hour < 22 else 0
                neg_sample["night"] = 1 if neg_hour >= 22 or neg_hour < 5 else 0
                samples.append(neg_sample)

    print(f"Built {len(samples)} training samples")
    return pd.DataFrame(samples)


def train_model(df: pd.DataFrame) -> tuple:
    """Train the ML model."""
    print("\nTraining model...")

    feature_cols = [
        "hour",
        "day_of_week",
        "month",
        "fish_id",
        "has_location",
        "user_total_catches",
        "is_user_preferred_hour",
        "morning",
        "midday",
        "afternoon",
        "evening",
        "night",
    ]

    X = df[feature_cols].values
    y = df["is_catch"].values

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

    # Train model
    model = GradientBoostingClassifier(
        n_estimators=100,
        learning_rate=0.1,
        max_depth=4,
        random_state=42,
    )

    print("Fitting model...")
    model.fit(X_train_scaled, y_train)

    # Evaluate
    y_pred_proba = model.predict_proba(X_test_scaled)[:, 1]
    roc_auc = roc_auc_score(y_test, y_pred_proba)
    print(f"\nTest ROC-AUC: {roc_auc:.4f}")

    # Cross-validation
    cv_scores = cross_val_score(model, X_train_scaled, y_train, cv=5, scoring="roc_auc")
    print(f"CV ROC-AUC: {cv_scores.mean():.4f} (+/- {cv_scores.std() * 2:.4f})")

    # Feature importance
    print("\nFeature Importances:")
    importance_df = pd.DataFrame({
        "feature": feature_cols,
        "importance": model.feature_importances_
    }).sort_values("importance", ascending=False)
    print(importance_df.to_string(index=False))

    return model, scaler, feature_cols, roc_auc, cv_scores.mean()


def save_model(model, scaler, feature_cols, roc_auc, cv_roc_auc, num_samples):
    """Save the trained model."""
    output_dir = Path("models/catch_time")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save model
    model_path = output_dir / "catch_time_model.joblib"
    joblib.dump(model, model_path)
    print(f"\nSaved model: {model_path}")

    # Save scaler
    scaler_path = output_dir / "catch_time_scaler.joblib"
    joblib.dump(scaler, scaler_path)
    print(f"Saved scaler: {scaler_path}")

    # Save features
    features_path = output_dir / "catch_time_features.txt"
    with open(features_path, "w") as f:
        for feat in feature_cols:
            f.write(f"{feat}\n")
    print(f"Saved features: {features_path}")

    # Save metadata
    import json
    metadata = {
        "version": "v1",
        "trained_at": datetime.now().isoformat(),
        "num_samples": num_samples,
        "num_features": len(feature_cols),
        "roc_auc": roc_auc,
        "cv_roc_auc": cv_roc_auc,
        "description": "Predicts optimal catch times based on historical patterns",
    }
    metadata_path = output_dir / "catch_time_metadata.json"
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"\n{'='*60}")
    print("Catch Time Prediction Model Training Complete!")
    print(f"{'='*60}")


async def main():
    """Main training function."""
    print("="*60)
    print("Catch Time Prediction ML Model Training")
    print("="*60)
    print(f"Started at: {datetime.now().isoformat()}")

    db = await get_db_session()

    try:
        df = await build_training_data(db)

        if len(df) == 0:
            print("\nError: No training data found.")
            return

        model, scaler, feature_cols, roc_auc, cv_roc_auc = train_model(df)
        save_model(model, scaler, feature_cols, roc_auc, cv_roc_auc, len(df))

    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
