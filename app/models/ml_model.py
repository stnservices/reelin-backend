"""ML Model registry and prediction logging models."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, Boolean, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MLModel(Base):
    """
    ML Model registry.
    Tracks uploaded models, their performance metrics, and activation status.
    """

    __tablename__ = "ml_models"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # event_recommendations, analytics_predictions, etc.
    file_path: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False, index=True
    )

    # Training metadata
    trained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    training_samples: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    positive_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    roc_auc: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cv_roc_auc: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    feature_columns: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    feature_importance: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    # Runtime stats
    predictions_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    last_prediction_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    avg_prediction_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Admin
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    uploaded_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", lazy="joined"
    )
    prediction_logs: Mapped[list["MLPredictionLog"]] = relationship(
        "MLPredictionLog", back_populates="model", cascade="all, delete-orphan"
    )

    @property
    def performance_grade(self) -> str:
        """Get a human-readable performance grade based on ROC AUC."""
        if not self.roc_auc:
            return "Unknown"
        if self.roc_auc >= 0.9:
            return "Excellent"
        if self.roc_auc >= 0.8:
            return "Good"
        if self.roc_auc >= 0.7:
            return "Fair"
        return "Poor"

    def __repr__(self) -> str:
        return f"<MLModel(id={self.id}, name={self.name}, type={self.model_type}, active={self.is_active})>"


class MLPredictionLog(Base):
    """
    Log of ML predictions for monitoring and performance tracking.
    actual_outcome is filled later when we know the real result.
    """

    __tablename__ = "ml_prediction_logs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("ml_models.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    entity_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # event, angler
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    prediction_score: Mapped[float] = mapped_column(Float, nullable=False)
    actual_outcome: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    prediction_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    model: Mapped["MLModel"] = relationship("MLModel", back_populates="prediction_logs")
    user: Mapped[Optional["UserAccount"]] = relationship("UserAccount", lazy="joined")

    def __repr__(self) -> str:
        return f"<MLPredictionLog(id={self.id}, model_id={self.model_id}, score={self.prediction_score})>"


# Import for type hints
from app.models.user import UserAccount
