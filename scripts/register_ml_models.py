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
        "notes": "Multi-class species prediction based on location and season",
    },
    {
        "name": "Analytics - Attendance",
        "model_type": "analytics_predictions",
        "model_file": "models/analytics_predictions/attendance_model.joblib",
        "scaler_file": "models/analytics_predictions/attendance_scaler.joblib",
        "features_file": "models/analytics_predictions/attendance_features.txt",
        "metadata_file": "models/analytics_predictions/analytics_metadata.json",
        "notes": "Predicts event attendance for planning",
    },
    {
        "name": "Analytics - Performance",
        "model_type": "analytics_performance",
        "model_file": "models/analytics_predictions/performance_model.joblib",
        "scaler_file": "models/analytics_predictions/performance_scaler.joblib",
        "features_file": "models/analytics_predictions/performance_features.txt",
        "metadata_file": "models/analytics_predictions/analytics_metadata.json",
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

    if existing:
        # Update existing model
        existing.file_path = config["model_file"]
        existing.file_size_bytes = file_size
        existing.trained_at = trained_at
        existing.training_samples = metadata.get("num_samples")
        existing.roc_auc = metadata.get("roc_auc") if metadata.get("roc_auc") and not str(metadata.get("roc_auc")).lower() == 'nan' else None
        existing.cv_roc_auc = metadata.get("cv_roc_auc") if metadata.get("cv_roc_auc") and not str(metadata.get("cv_roc_auc")).lower() == 'nan' else None
        existing.notes = config.get("notes", "")
        print(f"  ✓ Updated: {config['name']}")
    else:
        # Create new model
        model = MLModel(
            name=config["name"],
            model_type=config["model_type"],
            file_path=config["model_file"],
            file_size_bytes=file_size,
            trained_at=trained_at,
            training_samples=metadata.get("num_samples"),
            roc_auc=metadata.get("roc_auc") if metadata.get("roc_auc") and not str(metadata.get("roc_auc")).lower() == 'nan' else None,
            cv_roc_auc=metadata.get("cv_roc_auc") if metadata.get("cv_roc_auc") and not str(metadata.get("cv_roc_auc")).lower() == 'nan' else None,
            notes=config.get("notes", ""),
            is_active=False,  # Don't auto-activate
        )
        db.add(model)
        print(f"  ✓ Created: {config['name']}")

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
