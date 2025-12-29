"""Celery tasks for AI-powered catch analysis.

Handles background processing of catch images for:
- Species detection
- Anomaly detection
- Metadata analysis

These tasks run asynchronously AFTER catch submission,
never blocking the user upload experience.
"""

import asyncio
import logging
from typing import Optional

from app.celery_app import celery_app
from app.database import async_session_maker
from app.services.ai_analysis_service import ai_analysis_service

logger = logging.getLogger(__name__)


async def _run_catch_analysis(catch_id: int) -> dict:
    """Run AI analysis for a catch asynchronously."""
    async with async_session_maker() as db:
        try:
            result = await ai_analysis_service.analyze_catch(db, catch_id)
            logger.info(f"AI analysis completed for catch {catch_id}")
            return result
        except Exception as e:
            logger.error(f"AI analysis failed for catch {catch_id}: {e}")
            raise


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def analyze_catch_with_ai(self, catch_id: int) -> dict:
    """
    Background task to analyze catch with AI.
    Runs after catch is saved - does not block upload.

    Args:
        catch_id: The ID of the catch to analyze

    Returns:
        Analysis results dict
    """
    logger.info(f"Starting AI analysis for catch {catch_id}")

    try:
        # Run async code in sync context
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If running in async context, create new loop
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        result = loop.run_until_complete(_run_catch_analysis(catch_id))
        return result

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
    from sqlalchemy import select
    from app.models.ai_analysis import CatchAiAnalysis, AiAnalysisStatus

    async def _reanalyze():
        async with async_session_maker() as db:
            # Find pending or failed analyses
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
            result = await db.execute(stmt)
            analyses = result.scalars().all()

            processed = 0
            for analysis in analyses:
                try:
                    await ai_analysis_service.analyze_catch(db, analysis.catch_id)
                    processed += 1
                except Exception as e:
                    logger.error(f"Reanalysis failed for catch {analysis.catch_id}: {e}")

            return {"processed": processed, "total_found": len(analyses)}

    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(_reanalyze())


def queue_catch_analysis(catch_id: int, delay_seconds: int = 5) -> None:
    """
    Queue a catch for AI analysis with optional delay.
    Called after catch submission.

    Args:
        catch_id: The ID of the catch to analyze
        delay_seconds: Delay before processing (allows image to finish uploading)
    """
    analyze_catch_with_ai.apply_async(
        args=[catch_id],
        countdown=delay_seconds,
    )
    logger.info(f"Queued AI analysis for catch {catch_id} with {delay_seconds}s delay")
