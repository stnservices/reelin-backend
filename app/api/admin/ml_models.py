"""Admin ML Model Management API."""

import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.core.permissions import AdminOnly
from app.models.user import UserAccount
from app.models.ml_model import MLModel, MLPredictionLog

router = APIRouter()


class MLModelResponse(BaseModel):
    """Response schema for ML model."""

    id: int
    name: str
    model_type: str
    file_path: str
    file_size_bytes: Optional[int]
    is_active: bool
    trained_at: datetime
    training_samples: Optional[int]
    positive_rate: Optional[float]
    roc_auc: Optional[float]
    cv_roc_auc: Optional[float]
    predictions_count: int
    last_prediction_at: Optional[datetime]
    avg_prediction_ms: Optional[float]
    uploaded_by_email: Optional[str]
    created_at: datetime
    notes: Optional[str]
    performance_grade: str

    class Config:
        from_attributes = True


class MLModelStatsResponse(BaseModel):
    """Response schema for model statistics."""

    model_id: int
    period_days: int
    total_predictions: int
    avg_latency_ms: Optional[float]
    actual_positive_count: int
    actual_negative_count: int
    avg_score_when_positive: Optional[float]
    avg_score_when_negative: Optional[float]


class MLSettingsResponse(BaseModel):
    """Response schema for ML settings."""

    ml_enabled: bool
    ml_min_confidence: float
    ml_log_predictions: bool
    active_models: dict[str, Optional[str]]


@router.get("/models", response_model=list[MLModelResponse])
async def list_ml_models(
    model_type: Optional[str] = Query(None),
    include_inactive: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """List all ML models, optionally filtered by type."""
    query = (
        select(MLModel)
        .options(selectinload(MLModel.uploaded_by))
        .order_by(MLModel.created_at.desc())
    )

    if model_type:
        query = query.where(MLModel.model_type == model_type)

    if not include_inactive:
        query = query.where(MLModel.is_active == True)

    result = await db.execute(query)
    models = result.scalars().all()

    return [
        MLModelResponse(
            id=m.id,
            name=m.name,
            model_type=m.model_type,
            file_path=m.file_path,
            file_size_bytes=m.file_size_bytes,
            is_active=m.is_active,
            trained_at=m.trained_at,
            training_samples=m.training_samples,
            positive_rate=m.positive_rate,
            roc_auc=m.roc_auc,
            cv_roc_auc=m.cv_roc_auc,
            predictions_count=m.predictions_count,
            last_prediction_at=m.last_prediction_at,
            avg_prediction_ms=m.avg_prediction_ms,
            uploaded_by_email=m.uploaded_by.email if m.uploaded_by else None,
            created_at=m.created_at,
            notes=m.notes,
            performance_grade=m.performance_grade,
        )
        for m in models
    ]


@router.post("/models/upload")
async def upload_ml_model(
    file: UploadFile = File(...),
    name: str = Form(...),
    model_type: str = Form(...),
    trained_at: Optional[str] = Form(None),
    training_samples: Optional[int] = Form(None),
    roc_auc: Optional[float] = Form(None),
    cv_roc_auc: Optional[float] = Form(None),
    notes: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """
    Upload a new ML model file.
    Accepts .joblib files for sklearn models.
    """
    if not file.filename.endswith(".joblib"):
        raise HTTPException(400, "Only .joblib files are supported")
    file_ext = ".joblib"

    # Create models directory if it doesn't exist
    models_dir = f"models/{model_type}"
    os.makedirs(models_dir, exist_ok=True)

    # Generate unique file path
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = f"{models_dir}/{name}_{timestamp}{file_ext}"

    # Save file
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    # Parse trained_at
    parsed_trained_at = datetime.now()
    if trained_at:
        try:
            parsed_trained_at = datetime.fromisoformat(trained_at)
        except ValueError:
            pass

    # Create DB record
    ml_model = MLModel(
        name=name,
        model_type=model_type,
        file_path=file_path,
        file_size_bytes=len(content),
        trained_at=parsed_trained_at,
        training_samples=training_samples,
        roc_auc=roc_auc,
        cv_roc_auc=cv_roc_auc,
        uploaded_by_id=current_user.id,
        notes=notes,
    )
    db.add(ml_model)
    await db.commit()
    await db.refresh(ml_model)

    return {"id": ml_model.id, "message": "Model uploaded successfully"}


@router.get("/models/{model_id}", response_model=MLModelResponse)
async def get_ml_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Get a specific ML model by ID."""
    query = (
        select(MLModel)
        .options(selectinload(MLModel.uploaded_by))
        .where(MLModel.id == model_id)
    )
    result = await db.execute(query)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(404, "Model not found")

    return MLModelResponse(
        id=model.id,
        name=model.name,
        model_type=model.model_type,
        file_path=model.file_path,
        file_size_bytes=model.file_size_bytes,
        is_active=model.is_active,
        trained_at=model.trained_at,
        training_samples=model.training_samples,
        positive_rate=model.positive_rate,
        roc_auc=model.roc_auc,
        cv_roc_auc=model.cv_roc_auc,
        predictions_count=model.predictions_count,
        last_prediction_at=model.last_prediction_at,
        avg_prediction_ms=model.avg_prediction_ms,
        uploaded_by_email=model.uploaded_by.email if model.uploaded_by else None,
        created_at=model.created_at,
        notes=model.notes,
        performance_grade=model.performance_grade,
    )


@router.post("/models/{model_id}/activate")
async def activate_ml_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """
    Set a model as the active model for its type.
    Deactivates any previously active model of same type.
    """
    model = await db.get(MLModel, model_id)
    if not model:
        raise HTTPException(404, "Model not found")

    # Deactivate other models of same type
    await db.execute(
        update(MLModel)
        .where(MLModel.model_type == model.model_type)
        .values(is_active=False)
    )

    # Activate this model
    model.is_active = True
    await db.commit()

    return {"message": f"Model '{model.name}' is now active"}


@router.post("/models/{model_id}/deactivate")
async def deactivate_ml_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Deactivate a model (will fall back to rule-based)."""
    model = await db.get(MLModel, model_id)
    if not model:
        raise HTTPException(404, "Model not found")

    model.is_active = False
    await db.commit()

    return {"message": f"Model '{model.name}' is now inactive"}


@router.delete("/models/{model_id}")
async def delete_ml_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Delete a model (cannot delete active model)."""
    model = await db.get(MLModel, model_id)
    if not model:
        raise HTTPException(404, "Model not found")

    if model.is_active:
        raise HTTPException(400, "Cannot delete active model. Deactivate it first.")

    # Delete file
    if os.path.exists(model.file_path):
        os.remove(model.file_path)

    # Delete record
    await db.delete(model)
    await db.commit()

    return {"message": "Model deleted"}


@router.get("/models/{model_id}/stats", response_model=MLModelStatsResponse)
async def get_model_stats(
    model_id: int,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Get prediction statistics for a model."""
    model = await db.get(MLModel, model_id)
    if not model:
        raise HTTPException(404, "Model not found")

    since = datetime.now() - timedelta(days=days)

    # Aggregate stats
    result = await db.execute(
        select(
            func.count(MLPredictionLog.id).label("total_predictions"),
            func.avg(MLPredictionLog.prediction_ms).label("avg_latency_ms"),
            func.count(MLPredictionLog.id)
            .filter(MLPredictionLog.actual_outcome == True)
            .label("actual_positive"),
            func.count(MLPredictionLog.id)
            .filter(MLPredictionLog.actual_outcome == False)
            .label("actual_negative"),
            func.avg(MLPredictionLog.prediction_score)
            .filter(MLPredictionLog.actual_outcome == True)
            .label("avg_score_when_positive"),
            func.avg(MLPredictionLog.prediction_score)
            .filter(MLPredictionLog.actual_outcome == False)
            .label("avg_score_when_negative"),
        )
        .where(MLPredictionLog.model_id == model_id)
        .where(MLPredictionLog.created_at >= since)
    )

    row = result.first()

    return MLModelStatsResponse(
        model_id=model_id,
        period_days=days,
        total_predictions=row.total_predictions or 0,
        avg_latency_ms=float(row.avg_latency_ms) if row.avg_latency_ms else None,
        actual_positive_count=row.actual_positive or 0,
        actual_negative_count=row.actual_negative or 0,
        avg_score_when_positive=(
            float(row.avg_score_when_positive)
            if row.avg_score_when_positive
            else None
        ),
        avg_score_when_negative=(
            float(row.avg_score_when_negative)
            if row.avg_score_when_negative
            else None
        ),
    )


@router.get("/models/types/summary")
async def get_model_types_summary(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Get summary of active models by type."""
    # Get all active models grouped by type
    result = await db.execute(
        select(MLModel)
        .where(MLModel.is_active == True)
    )
    active_models = result.scalars().all()

    # Build summary
    summary = {}
    for model in active_models:
        summary[model.model_type] = {
            "model_id": model.id,
            "model_name": model.name,
            "roc_auc": model.roc_auc,
            "predictions_count": model.predictions_count,
        }

    # Add model types that don't have active models (fish_classifier is handled separately)
    all_types = ["event_recommendations", "analytics_predictions", "catch_time", "species_forecast"]
    for model_type in all_types:
        if model_type not in summary:
            summary[model_type] = None

    return {"active_models": summary}


