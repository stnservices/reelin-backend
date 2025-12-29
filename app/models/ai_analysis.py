"""AI Analysis model for catch validation assistance."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AiAnalysisStatus(str, Enum):
    """AI analysis processing status."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED = "skipped"


class CatchAiAnalysis(Base):
    """
    AI-powered analysis results for catch validation.
    Runs asynchronously after catch upload to provide hints to validators.
    """

    __tablename__ = "catch_ai_analysis"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    catch_id: Mapped[int] = mapped_column(
        ForeignKey("catches.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Species detection
    detected_species_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("fish.id", ondelete="SET NULL"), nullable=True
    )
    species_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    species_alternatives: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    # [{species_id: 2, name: "Zander", confidence: 0.04}, ...]

    # Anomaly detection
    anomaly_score: Mapped[float] = mapped_column(
        Float, default=0.0, server_default="0", nullable=False
    )  # 0.0-1.0 (higher = more suspicious)
    anomaly_flags: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    # ["gps_outside_event", "similar_to_catch_123", ...]

    # Metadata analysis
    metadata_warnings: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    # ["exif_stripped", "editing_software_detected", ...]

    # Raw data for debugging
    raw_vision_response: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    raw_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Perceptual hash for image similarity detection
    perceptual_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Processing info
    status: Mapped[str] = mapped_column(
        String(20),
        default=AiAnalysisStatus.PENDING.value,
        server_default="pending",
        nullable=False,
        index=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processing_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    catch: Mapped["Catch"] = relationship("Catch", back_populates="ai_analysis")
    detected_species: Mapped[Optional["Fish"]] = relationship("Fish", lazy="joined")

    @property
    def overall_risk(self) -> str:
        """Calculate overall risk level based on anomaly score and flags."""
        if self.anomaly_score >= 0.7 or any(
            f.get("severity") == "high" for f in self.anomaly_flags
        ):
            return "high"
        elif self.anomaly_score >= 0.4 or any(
            f.get("severity") == "warning" for f in self.anomaly_flags
        ):
            return "medium"
        return "low"

    @property
    def species_matches_claim(self) -> bool:
        """Check if detected species matches what user claimed."""
        if not self.detected_species_id:
            return True  # No detection, assume match
        return self.detected_species_id == self.catch.fish_id

    @property
    def is_complete(self) -> bool:
        return self.status == AiAnalysisStatus.COMPLETE.value

    @property
    def is_failed(self) -> bool:
        return self.status == AiAnalysisStatus.FAILED.value

    def __repr__(self) -> str:
        return f"<CatchAiAnalysis(id={self.id}, catch_id={self.catch_id}, status={self.status})>"


# Import for type hints
from app.models.catch import Catch
from app.models.fish import Fish
