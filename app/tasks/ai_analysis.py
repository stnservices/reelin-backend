"""Celery tasks for AI-powered catch analysis.

Handles background processing of catch images for:
- Species detection (Google Vision)
- Anomaly detection
- Metadata analysis

These tasks run asynchronously AFTER catch submission,
never blocking the user upload experience.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.celery_app import celery_app
from app.database import SyncSessionLocal
from app.services.ai_analysis_service import ai_analysis_service
from app.services.firebase_leaderboard_service import sync_validator_event

logger = logging.getLogger(__name__)


def _sync_run_catch_analysis(catch_id: int) -> dict:
    """Run AI analysis for a catch using sync DB session."""
    with SyncSessionLocal() as db:
        try:
            result = ai_analysis_service.analyze_catch_sync(db, catch_id)
            logger.info(f"AI analysis completed for catch {catch_id}")

            # Broadcast AI analysis completion via Firebase
            _sync_broadcast_ai_analysis_complete(db, catch_id)

            return result
        except Exception as e:
            logger.error(f"AI analysis failed for catch {catch_id}: {e}")
            raise


def _sync_broadcast_ai_analysis_complete(db, catch_id: int) -> None:
    """Broadcast AI analysis completion to validators via Firebase."""
    from app.models.catch import Catch
    from app.models.ai_analysis import CatchAiAnalysis

    try:
        # Get catch with event_id
        catch_stmt = select(Catch).where(Catch.id == catch_id)
        catch_result = db.execute(catch_stmt)
        catch = catch_result.scalar_one_or_none()

        if not catch or not catch.event_id:
            logger.warning(f"Cannot broadcast AI analysis: catch {catch_id} not found or has no event")
            return

        # Get the AI analysis record
        analysis_stmt = (
            select(CatchAiAnalysis)
            .options(selectinload(CatchAiAnalysis.detected_species))
            .where(CatchAiAnalysis.catch_id == catch_id)
        )
        analysis_result = db.execute(analysis_stmt)
        analysis = analysis_result.scalar_one_or_none()

        if not analysis:
            logger.warning(f"Cannot broadcast AI analysis: no analysis record for catch {catch_id}")
            return

        # Build the AI analysis data for SSE
        ai_analysis_data = {
            "status": analysis.status,
            "species_matches_claim": (
                analysis.detected_species_id == catch.fish_id
                if analysis.detected_species_id else False
            ),
            "anomaly_score": analysis.anomaly_score or 0,
            "anomaly_flags": analysis.anomaly_flags or [],
            "metadata_warnings": analysis.metadata_warnings or [],
            "overall_risk": (
                "high" if (analysis.anomaly_score or 0) > 0.7
                else "medium" if (analysis.anomaly_score or 0) > 0.3
                else "low"
            ),
            "processed_at": analysis.processed_at.isoformat() if analysis.processed_at else None,
            "error_message": analysis.error_message,
            "validation_confidence": analysis.validation_confidence,
            "validation_recommendation": analysis.validation_recommendation,
            "ai_insights": analysis.ai_insights,
            "auto_validated": analysis.auto_validated,
            "auto_validated_at": analysis.auto_validated_at.isoformat() if analysis.auto_validated_at else None,
        }

        # Add detected species info
        if analysis.detected_species_id and analysis.detected_species:
            ai_analysis_data["detected_species"] = {
                "species_id": analysis.detected_species_id,
                "species_name": analysis.detected_species.name,
                "confidence": analysis.species_confidence or 0,
            }

        # Add species alternatives
        if analysis.species_alternatives:
            ai_analysis_data["species_alternatives"] = analysis.species_alternatives

        # Firebase sync for real-time updates
        sync_validator_event(catch.event_id, "ai_analysis_complete", {
            "catchId": catch_id,
            "aiAnalysis": ai_analysis_data,
        })

        logger.info(f"Broadcasted AI analysis completion for catch {catch_id} to event {catch.event_id}")

    except Exception as e:
        logger.error(f"Failed to broadcast AI analysis completion for catch {catch_id}: {e}")


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def analyze_catch_with_ai(self, catch_id: int) -> dict:
    """
    Background task to analyze catch with AI (Google Vision).
    Runs after catch is saved - does not block upload.

    Args:
        catch_id: The ID of the catch to analyze

    Returns:
        Analysis results dict
    """
    logger.info(f"Starting AI analysis for catch {catch_id}")

    try:
        return _sync_run_catch_analysis(catch_id)
    except Exception as e:
        logger.error(f"AI analysis task failed for catch {catch_id}: {e}")
        raise self.retry(exc=e)


@celery_app.task
def reanalyze_pending_catches(limit: int = 100) -> dict:
    """
    Periodic task to retry failed/pending AI analyses.
    Run via: celery -A app.celery_app beat

    Args:
        limit: Maximum number of catches to process

    Returns:
        Summary of processed catches
    """
    from app.models.ai_analysis import CatchAiAnalysis, AiAnalysisStatus

    with SyncSessionLocal() as db:
        stmt = (
            select(CatchAiAnalysis)
            .where(
                CatchAiAnalysis.status.in_([
                    AiAnalysisStatus.PENDING.value,
                    AiAnalysisStatus.FAILED.value,
                ])
            )
            .limit(limit)
        )
        result = db.execute(stmt)
        analyses = result.scalars().all()

        processed = 0
        for analysis in analyses:
            try:
                ai_analysis_service.analyze_catch_sync(db, analysis.catch_id)
                processed += 1
            except Exception as e:
                logger.error(f"Reanalysis failed for catch {analysis.catch_id}: {e}")

        return {"processed": processed, "total_found": len(analyses)}


def queue_catch_analysis(catch_id: int, delay_seconds: int = 5) -> None:
    """
    Queue a catch for AI analysis with optional delay.
    Called after catch submission.

    Args:
        catch_id: The ID of the catch to analyze
        delay_seconds: Delay before processing (default 5s to allow S3 propagation)
    """
    analyze_catch_with_ai.apply_async(
        args=[catch_id],
        countdown=delay_seconds,
    )
    logger.info(f"Queued AI analysis for catch {catch_id} with {delay_seconds}s delay")
