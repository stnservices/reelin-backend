"""AI Analysis Service for catch validation assistance.

Provides:
- Species detection (via Google Cloud Vision)
- Anomaly detection (GPS, timing, similarity)
- Metadata analysis (EXIF parsing)
"""

import logging
from datetime import datetime, timedelta
from math import radians, cos, sin, asin, sqrt
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catch import Catch
from app.models.ai_analysis import CatchAiAnalysis, AiAnalysisStatus
from app.models.fish import Fish

logger = logging.getLogger(__name__)


class AiAnalysisService:
    """Service for AI-powered catch analysis."""

    # Species label mappings from Cloud Vision to our fish species
    SPECIES_LABEL_MAP = {
        "pike": ["pike", "northern pike", "esox", "esox lucius"],
        "carp": ["carp", "common carp", "cyprinus", "cyprinus carpio", "mirror carp"],
        "catfish": [
            "catfish",
            "wels catfish",
            "silurus",
            "silurus glanis",
            "wels",
            "sheatfish",
        ],
        "perch": ["perch", "european perch", "perca", "perca fluviatilis"],
        "zander": ["zander", "pike-perch", "sander", "sander lucioperca", "pikeperch"],
        "bass": ["bass", "largemouth bass", "smallmouth bass", "micropterus"],
        "trout": ["trout", "brown trout", "rainbow trout", "salmo", "oncorhynchus"],
        "bream": ["bream", "common bream", "abramis", "abramis brama"],
        "roach": ["roach", "rutilus", "rutilus rutilus"],
        "tench": ["tench", "tinca", "tinca tinca"],
    }

    @staticmethod
    def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two GPS coordinates in kilometers."""
        R = 6371  # Earth's radius in km

        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1

        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        c = 2 * asin(sqrt(a))

        return R * c

    async def create_pending_analysis(
        self, db: AsyncSession, catch_id: int
    ) -> CatchAiAnalysis:
        """Create a pending AI analysis record for a catch."""
        analysis = CatchAiAnalysis(
            catch_id=catch_id,
            status=AiAnalysisStatus.PENDING.value,
        )
        db.add(analysis)
        await db.commit()
        await db.refresh(analysis)
        return analysis

    async def analyze_catch(self, db: AsyncSession, catch_id: int) -> dict:
        """
        Perform full AI analysis on a catch.
        Returns analysis results dict.
        """
        start_time = datetime.utcnow()

        # Get or create analysis record
        stmt = select(CatchAiAnalysis).where(CatchAiAnalysis.catch_id == catch_id)
        result = await db.execute(stmt)
        analysis = result.scalar_one_or_none()

        if not analysis:
            analysis = CatchAiAnalysis(
                catch_id=catch_id,
                status=AiAnalysisStatus.PROCESSING.value,
            )
            db.add(analysis)
            await db.flush()
        else:
            analysis.status = AiAnalysisStatus.PROCESSING.value
            await db.flush()

        # Get catch with event info
        catch_stmt = select(Catch).where(Catch.id == catch_id)
        catch_result = await db.execute(catch_stmt)
        catch = catch_result.scalar_one_or_none()

        if not catch:
            analysis.status = AiAnalysisStatus.FAILED.value
            analysis.error_message = "Catch not found"
            await db.commit()
            return {"error": "Catch not found"}

        results = {
            "species": None,
            "anomalies": [],
            "metadata": [],
        }

        try:
            # 1. Species Detection (placeholder - requires Cloud Vision API key)
            species_result = await self._detect_species(db, catch)
            if species_result:
                results["species"] = species_result
                analysis.detected_species_id = species_result.get("detected_species_id")
                analysis.species_confidence = species_result.get("confidence")
                analysis.species_alternatives = species_result.get("alternatives", [])

            # 2. Anomaly Detection
            anomalies = await self._detect_anomalies(db, catch)
            results["anomalies"] = anomalies
            analysis.anomaly_flags = anomalies
            analysis.anomaly_score = self._calculate_anomaly_score(anomalies)

            # 3. Metadata Analysis
            metadata = await self._analyze_metadata(catch)
            results["metadata"] = metadata
            analysis.metadata_warnings = metadata

            # Calculate processing time
            processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000

            # Update analysis record
            analysis.status = AiAnalysisStatus.COMPLETE.value
            analysis.processed_at = datetime.utcnow()
            analysis.processing_time_ms = int(processing_time)

        except Exception as e:
            logger.error(f"AI analysis failed for catch {catch_id}: {e}")
            analysis.status = AiAnalysisStatus.FAILED.value
            analysis.error_message = str(e)

        await db.commit()
        return results

    async def _detect_species(
        self, db: AsyncSession, catch: Catch
    ) -> Optional[dict]:
        """
        Detect fish species from image using Cloud Vision.
        Returns None if detection not available.
        """
        # NOTE: Cloud Vision integration requires API key setup
        # For now, return None (no detection available)
        # In production, integrate with:
        # from google.cloud import vision
        # client = vision.ImageAnnotatorClient()

        # Placeholder: Log that we would analyze the image
        logger.info(f"Species detection placeholder for catch {catch.id}, image: {catch.photo_url}")

        # Return None to indicate no detection (requires Cloud Vision API)
        return None

    async def _detect_anomalies(
        self, db: AsyncSession, catch: Catch
    ) -> list[dict]:
        """Detect suspicious patterns in catch data."""
        flags = []

        # Get event for location/time checks
        if catch.event:
            event = catch.event

            # 1. GPS Check - is catch location within event area?
            if catch.location_lat and catch.location_lng and event.location:
                event_loc = event.location
                if event_loc.latitude and event_loc.longitude:
                    distance = self.haversine_distance(
                        catch.location_lat,
                        catch.location_lng,
                        event_loc.latitude,
                        event_loc.longitude,
                    )
                    if distance > 0.5:  # More than 500m from event
                        flags.append({
                            "code": "gps_outside_event",
                            "message": f"GPS location {distance:.1f}km from event",
                            "severity": "warning" if distance < 2 else "high",
                            "details": {"distance_km": round(distance, 2)},
                        })

            # 2. Time Check - was catch submitted during event hours?
            if catch.submitted_at and event.start_datetime:
                if catch.submitted_at < event.start_datetime:
                    flags.append({
                        "code": "time_before_event",
                        "message": "Submitted before event started",
                        "severity": "high",
                        "details": {},
                    })
                if event.end_datetime and catch.submitted_at > event.end_datetime:
                    flags.append({
                        "code": "time_after_event",
                        "message": "Submitted after event ended",
                        "severity": "warning",
                        "details": {},
                    })

            # 3. Rapid Submission Check
            rapid_stmt = select(Catch).where(
                and_(
                    Catch.event_id == catch.event_id,
                    Catch.user_id == catch.user_id,
                    Catch.id != catch.id,
                    Catch.submitted_at >= catch.submitted_at - timedelta(minutes=5),
                    Catch.submitted_at <= catch.submitted_at,
                )
            )
            rapid_result = await db.execute(rapid_stmt)
            rapid_catches = rapid_result.scalars().all()
            if len(rapid_catches) >= 3:
                flags.append({
                    "code": "rapid_submissions",
                    "message": f"{len(rapid_catches) + 1} catches in 5 minutes",
                    "severity": "info",
                    "details": {"count": len(rapid_catches) + 1},
                })

            # 4. Similar Image Check (placeholder - requires perceptual hashing)
            # Would check: find_similar_images(catch.perceptual_hash, catch.event_id)

        # 5. Size Check - is the size unusual for this species?
        if catch.fish and catch.length:
            # Basic size range check (would be more sophisticated in production)
            if catch.length > 150:  # Very large fish (150cm+)
                flags.append({
                    "code": "unusual_size_for_species",
                    "message": f"Very large size ({catch.length}cm) - verify carefully",
                    "severity": "info",
                    "details": {"length_cm": catch.length},
                })

        return flags

    async def _analyze_metadata(self, catch: Catch) -> list[dict]:
        """Analyze photo/video metadata for suspicious patterns."""
        warnings = []

        # NOTE: Full EXIF analysis requires downloading the image
        # For now, check what metadata we have stored

        # Check if original metadata indicates missing EXIF
        if not catch.catch_time and catch.photo_url:
            # No EXIF time extracted - could indicate stripped metadata
            warnings.append({
                "code": "no_exif_time",
                "message": "No timestamp extracted from photo EXIF",
                "severity": "info",
                "details": {},
            })

        # Check for unusually small file size (could indicate heavy editing/compression)
        if catch.original_size_bytes and catch.original_size_bytes < 50000:  # Less than 50KB
            warnings.append({
                "code": "very_small_file",
                "message": f"File size very small ({catch.original_size_bytes / 1000:.1f}KB)",
                "severity": "info",
                "details": {"size_bytes": catch.original_size_bytes},
            })

        return warnings

    def _calculate_anomaly_score(self, flags: list[dict]) -> float:
        """Calculate overall anomaly score from flags."""
        if not flags:
            return 0.0

        score = 0.0
        severity_weights = {
            "high": 0.4,
            "warning": 0.2,
            "info": 0.05,
        }

        for flag in flags:
            severity = flag.get("severity", "info")
            score += severity_weights.get(severity, 0.05)

        return min(score, 1.0)  # Cap at 1.0


# Singleton instance
ai_analysis_service = AiAnalysisService()
