"""Schemas for AI Analysis responses."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SpeciesSuggestion(BaseModel):
    """AI-detected species suggestion."""

    species_id: int
    species_name: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class AnomalyFlag(BaseModel):
    """An anomaly flag detected by AI analysis."""

    code: str
    message: str
    severity: str  # info, warning, high
    details: Optional[dict] = None


class MetadataWarning(BaseModel):
    """A metadata warning from analysis."""

    code: str
    message: str
    details: Optional[dict] = None


class AiAnalysisResponse(BaseModel):
    """AI Analysis results for validators."""

    status: str  # pending, processing, complete, failed

    # Species detection
    detected_species: Optional[SpeciesSuggestion] = None
    species_alternatives: list[SpeciesSuggestion] = []
    species_matches_claim: bool = True

    # Anomalies
    anomaly_score: float = Field(0.0, ge=0.0, le=1.0)
    anomaly_flags: list[AnomalyFlag] = []

    # Metadata
    metadata_warnings: list[MetadataWarning] = []

    # Summary
    overall_risk: str = "low"  # low, medium, high

    # Validation (for ML auto-validation)
    validation_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    validation_recommendation: Optional[str] = None  # approve, reject, review
    ai_insights: Optional[str] = None  # Human-readable insights for validators
    auto_validated: bool = False
    auto_validated_at: Optional[datetime] = None

    # Processing info
    processed_at: Optional[datetime] = None
    processing_time_ms: Optional[int] = None
    error_message: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "status": "complete",
                "detected_species": {
                    "species_id": 1,
                    "species_name": "Pike",
                    "confidence": 0.94,
                },
                "species_alternatives": [
                    {"species_id": 5, "species_name": "Zander", "confidence": 0.04},
                ],
                "species_matches_claim": True,
                "anomaly_score": 0.2,
                "anomaly_flags": [
                    {
                        "code": "rapid_submissions",
                        "message": "3 catches in 5 minutes",
                        "severity": "info",
                        "details": {"count": 3},
                    }
                ],
                "metadata_warnings": [],
                "overall_risk": "low",
                "processed_at": "2025-12-28T14:32:00Z",
                "processing_time_ms": 1250,
            }
        }


def build_ai_analysis_response(analysis) -> Optional[AiAnalysisResponse]:
    """Build AI analysis response from model."""
    if not analysis:
        return None

    # Build detected species
    detected_species = None
    if analysis.detected_species_id and analysis.species_confidence:
        detected_species = SpeciesSuggestion(
            species_id=analysis.detected_species_id,
            species_name=analysis.detected_species.name if analysis.detected_species else "Unknown",
            confidence=analysis.species_confidence,
        )

    # Build alternatives
    alternatives = [
        SpeciesSuggestion(
            species_id=alt.get("species_id", 0),
            species_name=alt.get("species_name", "Unknown"),
            confidence=alt.get("confidence", 0.0),
        )
        for alt in (analysis.species_alternatives or [])
    ]

    # Build anomaly flags
    anomaly_flags = [
        AnomalyFlag(
            code=flag.get("code", ""),
            message=flag.get("message", ""),
            severity=flag.get("severity", "info"),
            details=flag.get("details"),
        )
        for flag in (analysis.anomaly_flags or [])
    ]

    # Build metadata warnings
    metadata_warnings = [
        MetadataWarning(
            code=warning.get("code", ""),
            message=warning.get("message", ""),
            details=warning.get("details"),
        )
        for warning in (analysis.metadata_warnings or [])
    ]

    return AiAnalysisResponse(
        status=analysis.status,
        detected_species=detected_species,
        species_alternatives=alternatives,
        species_matches_claim=analysis.species_matches_claim,
        anomaly_score=analysis.anomaly_score,
        anomaly_flags=anomaly_flags,
        metadata_warnings=metadata_warnings,
        overall_risk=analysis.overall_risk,
        validation_confidence=analysis.validation_confidence,
        validation_recommendation=analysis.validation_recommendation,
        ai_insights=analysis.ai_insights,
        auto_validated=analysis.auto_validated,
        auto_validated_at=analysis.auto_validated_at,
        processed_at=analysis.processed_at,
        processing_time_ms=analysis.processing_time_ms,
        error_message=analysis.error_message,
    )
