"""Celery tasks for AI-powered catch analysis.

Handles background processing of catch images for:
- Species detection
- Anomaly detection
- Metadata analysis

These tasks run asynchronously AFTER catch submission,
never blocking the user upload experience.
"""

import asyncio
import base64
import logging
from typing import Optional

from app.celery_app import celery_app
from app.database import create_celery_session_maker
from app.services.ai_analysis_service import ai_analysis_service

logger = logging.getLogger(__name__)


async def _run_catch_analysis(catch_id: int, image_bytes: Optional[bytes] = None) -> dict:
    """Run AI analysis for a catch asynchronously.

    Creates a fresh session maker to avoid event loop conflicts with Celery.

    Args:
        catch_id: The ID of the catch to analyze
        image_bytes: Optional pre-loaded image bytes (avoids re-download from S3)
    """
    # Create fresh session maker for this event loop
    session_maker = create_celery_session_maker()
    async with session_maker() as db:
        try:
            result = await ai_analysis_service.analyze_catch(db, catch_id, image_bytes=image_bytes)
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
def analyze_catch_with_ai(self, catch_id: int, image_bytes_b64: Optional[str] = None) -> dict:
    """
    Background task to analyze catch with AI.
    Runs after catch is saved - does not block upload.

    Args:
        catch_id: The ID of the catch to analyze
        image_bytes_b64: Optional base64-encoded image bytes (avoids re-download)

    Returns:
        Analysis results dict
    """
    logger.info(f"Starting AI analysis for catch {catch_id}")

    # Decode image bytes if provided
    image_bytes = None
    if image_bytes_b64:
        try:
            image_bytes = base64.b64decode(image_bytes_b64)
            logger.info(f"Using pre-loaded image bytes ({len(image_bytes)} bytes) for catch {catch_id}")
        except Exception as e:
            logger.warning(f"Failed to decode image bytes for catch {catch_id}: {e}")

    try:
        # Run async code in sync context - create new event loop for Celery worker
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_run_catch_analysis(catch_id, image_bytes))
            return result
        finally:
            loop.close()

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
        session_maker = create_celery_session_maker()
        async with session_maker() as db:
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

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_reanalyze())
    finally:
        loop.close()


def queue_catch_analysis(
    catch_id: int,
    delay_seconds: int = 0,
    image_bytes: Optional[bytes] = None
) -> None:
    """
    Queue a catch for AI analysis with optional delay.
    Called after catch submission.

    Args:
        catch_id: The ID of the catch to analyze
        delay_seconds: Delay before processing (default 0 if image_bytes provided)
        image_bytes: Optional image bytes to pass directly (avoids re-download from S3)
    """
    # Encode image bytes as base64 for JSON serialization
    image_bytes_b64 = None
    if image_bytes:
        image_bytes_b64 = base64.b64encode(image_bytes).decode('utf-8')
        logger.info(f"Passing {len(image_bytes)} bytes to AI analysis task for catch {catch_id}")

    analyze_catch_with_ai.apply_async(
        args=[catch_id, image_bytes_b64],
        countdown=delay_seconds,
    )
    logger.info(f"Queued AI analysis for catch {catch_id} with {delay_seconds}s delay")
