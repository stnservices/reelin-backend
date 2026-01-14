"""
Train Species Forecast ML Model.

This script trains a multi-class classifier to predict which fish species
a user is likely to catch based on:
- Location (approximate area/zone)
- Time of year (month/season)
- Time of day
- User's historical species catches
- Event location species availability

The model predicts probability distribution across known species.

Usage:
    cd reelin-backend
    python scripts/train_species_forecast.py

Output:
    - models/species_forecast/species_forecast_model.joblib
    - models/species_forecast/species_forecast_scaler.joblib
    - models/species_forecast/species_forecast_features.txt
    - models/species_forecast/species_forecast_classes.txt
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.metrics import accuracy_score, classification_report, top_k_accuracy_score

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, func, distinct
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models.catch import Catch, CatchStatus
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


def discretize_location(lat: float, lng: float, precision: int = 1) -> tuple:
    """
    Discretize location to zones for privacy and generalization.
    Precision 1 = ~11km zones, 2 = ~1.1km zones
    """
    if lat is None or lng is None:
        return (0, 0)
    return (round(lat, precision), round(lng, precision))


async def get_user_species_history(db: AsyncSession, user_id: int) -> dict:
    """Get user's historical species catch distribution."""
    stmt = (
        select(Catch.fish_id, func.count().label("count"))
        .where(
            Catch.user_id == user_id,
            Catch.status == CatchStatus.APPROVED.value,
            Catch.fish_id.isnot(None),
        )
        .group_by(Catch.fish_id)
    )
    result = await db.execute(stmt)
    rows = result.all()

    species_counts = {row.fish_id: row.count for row in rows}
    total = sum(species_counts.values())

    return {
        "species_counts": species_counts,
        "total_catches": total,
        "unique_species": len(species_counts),
        "top_species": max(species_counts.items(), key=lambda x: x[1])[0] if species_counts else 0,
    }


async def build_training_data(db: AsyncSession) -> pd.DataFrame:
    """Build training dataset from historical catches."""
    print("Building training data...")

    # Get all species
    species_stmt = select(Fish).where(Fish.is_active == True)
    species_result = await db.execute(species_stmt)
    all_species = {fish.id: fish.name for fish in species_result.scalars().all()}
    print(f"Found {len(all_species)} active species")

    # Get all approved catches with species
    stmt = (
        select(Catch)
        .where(
            Catch.status == CatchStatus.APPROVED.value,
            Catch.fish_id.isnot(None),
        )
    )
    result = await db.execute(stmt)
    catches = result.scalars().all()
    print(f"Found {len(catches)} approved catches with species")

    if len(catches) < 100:
        print("Warning: Limited training data. Model may not be reliable.")

    samples = []

    for catch in catches:
        # Get catch time
        catch_time = catch.catch_time or catch.submitted_at
        if catch_time.tzinfo is None:
            catch_time = catch_time.replace(tzinfo=timezone.utc)

        # Discretize location
        lat_zone, lng_zone = discretize_location(catch.location_lat, catch.location_lng)

        # Get user's species history
        user_history = await get_user_species_history(db, catch.user_id)

        # Has user caught this species before?
        user_caught_before = 1 if catch.fish_id in user_history["species_counts"] else 0

        sample = {
            "fish_id": catch.fish_id,
            "hour": catch_time.hour,
            "day_of_week": catch_time.weekday(),
            "month": catch_time.month,
            "lat_zone": lat_zone,
            "lng_zone": lng_zone,
            "has_location": 1 if (catch.location_lat and catch.location_lng) else 0,
            "length": catch.length or 0,
            "user_total_catches": user_history["total_catches"],
            "user_unique_species": user_history["unique_species"],
            "user_caught_before": user_caught_before,
            "user_top_species": user_history["top_species"],
            # Season features
            "is_spring": 1 if catch_time.month in [3, 4, 5] else 0,
            "is_summer": 1 if catch_time.month in [6, 7, 8] else 0,
            "is_autumn": 1 if catch_time.month in [9, 10, 11] else 0,
            "is_winter": 1 if catch_time.month in [12, 1, 2] else 0,
            # Time of day
            "is_morning": 1 if 5 <= catch_time.hour < 10 else 0,
            "is_evening": 1 if 17 <= catch_time.hour < 21 else 0,
        }
        samples.append(sample)

    print(f"Built {len(samples)} training samples")
    return pd.DataFrame(samples), all_species


def train_model(df: pd.DataFrame, all_species: dict) -> tuple:
    """Train the multi-class species prediction model."""
    print("\nTraining model...")

    feature_cols = [
        "hour",
        "day_of_week",
        "month",
        "lat_zone",
        "lng_zone",
        "has_location",
        "user_total_catches",
        "user_unique_species",
        "user_caught_before",
        "user_top_species",
        "is_spring",
        "is_summer",
        "is_autumn",
        "is_winter",
        "is_morning",
        "is_evening",
    ]

    X = df[feature_cols].values
    y = df["fish_id"].values

    # Get unique species in training data
    unique_species = sorted(set(y))
    print(f"Training on {len(unique_species)} species")

    # Encode labels
    label_encoder = LabelEncoder()
    y_encoded = label_encoder.fit_transform(y)

    print(f"Features: {len(feature_cols)}")
    print(f"Samples: {len(y)}")

    # Split data
    X_train, X_test, y_train, y_test = train_test_split(
        X, y_encoded, test_size=0.2, random_state=42, stratify=y_encoded
    )

    # Scale features
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # Train model - RandomForest works well for multi-class
    model = RandomForestClassifier(
        n_estimators=100,
        max_depth=10,
        min_samples_split=5,
        random_state=42,
        n_jobs=-1,
    )

    print("Fitting model...")
    model.fit(X_train_scaled, y_train)

    # Evaluate
    y_pred = model.predict(X_test_scaled)
    y_pred_proba = model.predict_proba(X_test_scaled)

    accuracy = accuracy_score(y_test, y_pred)
    print(f"\nTest Accuracy: {accuracy:.4f}")

    # Top-3 accuracy (did the correct species appear in top 3 predictions?)
    if len(unique_species) >= 3:
        top3_acc = top_k_accuracy_score(y_test, y_pred_proba, k=3)
        print(f"Top-3 Accuracy: {top3_acc:.4f}")
    else:
        top3_acc = accuracy

    # Cross-validation
    cv_scores = cross_val_score(model, X_train_scaled, y_train, cv=5, scoring="accuracy")
    print(f"CV Accuracy: {cv_scores.mean():.4f} (+/- {cv_scores.std() * 2:.4f})")

    # Feature importance
    print("\nTop 10 Feature Importances:")
    importance_df = pd.DataFrame({
        "feature": feature_cols,
        "importance": model.feature_importances_
    }).sort_values("importance", ascending=False)
    print(importance_df.head(10).to_string(index=False))

    return model, scaler, label_encoder, feature_cols, accuracy, top3_acc, cv_scores.mean()


def save_model(
    model, scaler, label_encoder, feature_cols,
    accuracy, top3_acc, cv_accuracy, num_samples, all_species
):
    """Save the trained model."""
    output_dir = Path("models/species_forecast")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save model
    model_path = output_dir / "species_forecast_model.joblib"
    joblib.dump(model, model_path)
    print(f"\nSaved model: {model_path}")

    # Save scaler
    scaler_path = output_dir / "species_forecast_scaler.joblib"
    joblib.dump(scaler, scaler_path)
    print(f"Saved scaler: {scaler_path}")

    # Save label encoder
    encoder_path = output_dir / "species_forecast_encoder.joblib"
    joblib.dump(label_encoder, encoder_path)
    print(f"Saved encoder: {encoder_path}")

    # Save features
    features_path = output_dir / "species_forecast_features.txt"
    with open(features_path, "w") as f:
        for feat in feature_cols:
            f.write(f"{feat}\n")
    print(f"Saved features: {features_path}")

    # Save class labels (species IDs)
    classes_path = output_dir / "species_forecast_classes.txt"
    with open(classes_path, "w") as f:
        for species_id in label_encoder.classes_:
            name = all_species.get(species_id, f"species_{species_id}")
            f.write(f"{species_id}:{name}\n")
    print(f"Saved classes: {classes_path}")

    # Save metadata
    import json
    metadata = {
        "version": "v1",
        "trained_at": datetime.now().isoformat(),
        "num_samples": num_samples,
        "num_features": len(feature_cols),
        "num_species": len(label_encoder.classes_),
        "accuracy": accuracy,
        "top3_accuracy": top3_acc,
        "cv_accuracy": cv_accuracy,
        "description": "Multi-class species prediction based on location, time, and user history",
    }
    metadata_path = output_dir / "species_forecast_metadata.json"
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"\n{'='*60}")
    print("Species Forecast Model Training Complete!")
    print(f"{'='*60}")
    print(f"Species: {len(label_encoder.classes_)}")
    print(f"Accuracy: {accuracy:.4f}")
    print(f"Top-3 Accuracy: {top3_acc:.4f}")


async def main():
    """Main training function."""
    print("="*60)
    print("Species Forecast ML Model Training")
    print("="*60)
    print(f"Started at: {datetime.now().isoformat()}")

    db = await get_db_session()

    try:
        df, all_species = await build_training_data(db)

        if len(df) == 0:
            print("\nError: No training data found.")
            return

        model, scaler, label_encoder, feature_cols, accuracy, top3_acc, cv_acc = train_model(df, all_species)
        save_model(
            model, scaler, label_encoder, feature_cols,
            accuracy, top3_acc, cv_acc, len(df), all_species
        )

    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
