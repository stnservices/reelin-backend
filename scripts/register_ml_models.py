"""
Register trained ML models in the database.

This script reads the model metadata and registers/updates models in the database,
making them available for activation via the admin UI.

Usage:
    cd reelin-backend
    python scripts/register_ml_models.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


# Model configurations to register
MODELS_TO_REGISTER = [
    {
        "name": "Event Recommendations v2",
        "model_type": "event_recommendations",
        "model_file": "models/event_recommendations/event_recommendation_v2_model.joblib",
        "scaler_file": "models/event_recommendations/event_recommendation_v2_scaler.joblib",
        "features_file": "models/event_recommendations/event_recommendation_v2_features.txt",
        "metadata_file": "models/event_recommendations/event_recommendation_v2_metadata.json",
        "notes": "Enhanced model with Hall of Fame integration, 26 features",
    },
    {
        "name": "Catch Time Prediction",
        "model_type": "catch_time",
        "model_file": "models/catch_time/catch_time_model.joblib",
        "scaler_file": "models/catch_time/catch_time_scaler.joblib",
        "features_file": "models/catch_time/catch_time_features.txt",
        "metadata_file": "models/catch_time/catch_time_metadata.json",
        "notes": "Predicts optimal fishing hours based on user patterns",
    },
    {
        "name": "Species Forecast",
        "model_type": "species_forecast",
        "model_file": "models/species_forecast/species_forecast_model.joblib",
        "scaler_file": "models/species_forecast/species_forecast_scaler.joblib",
        "features_file": "models/species_forecast/species_forecast_features.txt",
        "metadata_file": "models/species_forecast/species_forecast_metadata.json",
        "metric_field": "accuracy",  # Use accuracy for multi-class classification
        "notes": "Multi-class species prediction based on location and season",
    },
    {
        "name": "Analytics - Attendance",
        "model_type": "analytics_predictions",
        "model_file": "models/analytics_predictions/attendance_model.joblib",
        "scaler_file": "models/analytics_predictions/attendance_scaler.joblib",
        "features_file": "models/analytics_predictions/attendance_features.txt",
        "metadata_file": "models/analytics_predictions/analytics_metadata.json",
        "metadata_key": "attendance",  # Nested key in metadata
        "metric_field": "r2",  # Use R2 as quality metric (will be converted to percentage)
        "notes": "Predicts event attendance for planning (R² score)",
    },
    {
        "name": "Analytics - Performance",
        "model_type": "analytics_performance",
        "model_file": "models/analytics_predictions/performance_model.joblib",
        "scaler_file": "models/analytics_predictions/performance_scaler.joblib",
        "features_file": "models/analytics_predictions/performance_features.txt",
        "metadata_file": "models/analytics_predictions/analytics_metadata.json",
        "metadata_key": "performance",  # Nested key in metadata
        "metric_field": "accuracy",  # Use accuracy as quality metric
        "notes": "Predicts user finish bracket (winner/podium/top10/other)",
    },
]


async def get_db_session():
    """Create async database session."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return async_session()


async def register_model(db: AsyncSession, config: dict) -> bool:
    """Register a single model in the database."""
    from app.models.ml_model import MLModel

    # Check if model file exists
    if not os.path.exists(config["model_file"]):
        print(f"  ✗ Model file not found: {config['model_file']}")
        return False

    # Get file size
    file_size = os.path.getsize(config["model_file"])

    # Load metadata if available
    metadata = {}
    if os.path.exists(config.get("metadata_file", "")):
        with open(config["metadata_file"]) as f:
            metadata = json.load(f)

    # Handle nested metadata (for analytics models)
    model_metadata = metadata
    if config.get("metadata_key") and "models" in metadata:
        model_metadata = metadata.get("models", {}).get(config["metadata_key"], {})

    # Get the appropriate metric (roc_auc, accuracy, or custom field)
    metric_field = config.get("metric_field", "roc_auc")
    roc_auc_value = model_metadata.get(metric_field) or metadata.get("roc_auc")
    cv_roc_auc_value = metadata.get("cv_roc_auc")

    # Validate metric value
    def clean_metric(val):
        if val is None:
            return None
        try:
            val = float(val)
            if str(val).lower() == 'nan' or val != val:  # NaN check
                return None
            # For negative R2 values, treat as 0 (poor model)
            if val < 0:
                return 0.0
            return val
        except (ValueError, TypeError):
            return None

    roc_auc_value = clean_metric(roc_auc_value)
    cv_roc_auc_value = clean_metric(cv_roc_auc_value)

    # Check for existing model with same name
    stmt = select(MLModel).where(MLModel.name == config["name"])
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    trained_at = datetime.now()
    if metadata.get("trained_at"):
        try:
            trained_at = datetime.fromisoformat(metadata["trained_at"])
        except ValueError:
            pass

    num_samples = model_metadata.get("num_samples") or metadata.get("num_samples")

    if existing:
        # Update existing model
        existing.file_path = config["model_file"]
        existing.file_size_bytes = file_size
        existing.trained_at = trained_at
        existing.training_samples = num_samples
        existing.roc_auc = roc_auc_value
        existing.cv_roc_auc = cv_roc_auc_value
        existing.notes = config.get("notes", "")
        print(f"  ✓ Updated: {config['name']} (metric: {roc_auc_value})")
    else:
        # Create new model
        model = MLModel(
            name=config["name"],
            model_type=config["model_type"],
            file_path=config["model_file"],
            file_size_bytes=file_size,
            trained_at=trained_at,
            training_samples=num_samples,
            roc_auc=roc_auc_value,
            cv_roc_auc=cv_roc_auc_value,
            notes=config.get("notes", ""),
            is_active=False,  # Don't auto-activate
        )
        db.add(model)
        print(f"  ✓ Created: {config['name']} (metric: {roc_auc_value})")

    return True


async def main():
    """Main function to register all models."""
    print("="*60)
    print("Registering ML Models in Database")
    print("="*60)
    print()

    db = await get_db_session()

    try:
        success_count = 0
        for config in MODELS_TO_REGISTER:
            print(f"Processing: {config['name']}")
            if await register_model(db, config):
                success_count += 1

        await db.commit()

        print()
        print("="*60)
        print(f"Registered {success_count}/{len(MODELS_TO_REGISTER)} models")
        print("="*60)
        print()
        print("Next steps:")
        print("1. Go to Admin UI -> ML Models")
        print("2. Click 'Activate' on the models you want to use")
        print("3. The system will use activated models for predictions")

    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
