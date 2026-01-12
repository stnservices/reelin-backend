"""AI Analysis Service for catch validation assistance.

Provides:
- Species detection (via Google Cloud Vision)
- Anomaly detection (GPS, timing, similarity)
- Metadata analysis (EXIF parsing)
- Auto-validation with confidence scoring
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from math import radians, cos, sin, asin, sqrt
from typing import Optional, Tuple

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from app.models.catch import Catch
from app.models.ai_analysis import CatchAiAnalysis, AiAnalysisStatus
from app.models.fish import Fish
from app.config import get_settings

logger = logging.getLogger(__name__)

# AI Moderator account email
AI_MODERATOR_EMAIL = "ai_moderator@reelin.ro"


class AiAnalysisService:
    """Service for AI-powered catch analysis with auto-validation support."""

    # Species label mappings from Cloud Vision to our fish species
    SPECIES_LABEL_MAP = {
        "biban": ["perch", "european perch", "perca", "perca fluviatilis", "bass", "biban"],
        "clean": ["asp", "leuciscus aspius", "aspius aspius", "rapfen", "clean"],
        "salau": ["zander", "pike-perch", "sander", "sander lucioperca", "pikeperch", "salau"],
        "avat": ["asp", "chub", "squalius", "leuciscus", "avat"],
        "stiuca": ["pike", "northern pike", "esox", "esox lucius", "stiuca"],
        "somn": ["catfish", "wels catfish", "silurus", "silurus glanis", "wels", "sheatfish", "somn"],
        "pike": ["pike", "northern pike", "esox", "esox lucius"],
        "carp": ["carp", "common carp", "cyprinus", "cyprinus carpio", "mirror carp"],
        "catfish": ["catfish", "wels catfish", "silurus", "silurus glanis", "wels", "sheatfish"],
        "perch": ["perch", "european perch", "perca", "perca fluviatilis"],
        "zander": ["zander", "pike-perch", "sander", "sander lucioperca", "pikeperch"],
        "bass": ["bass", "largemouth bass", "smallmouth bass", "micropterus"],
        "trout": ["trout", "brown trout", "rainbow trout", "salmo", "oncorhynchus"],
        "bream": ["bream", "common bream", "abramis", "abramis brama"],
        "roach": ["roach", "rutilus", "rutilus rutilus"],
        "tench": ["tench", "tinca", "tinca tinca"],
        "asp": ["asp", "leuciscus aspius", "aspius aspius", "rapfen"],
    }

    GENERIC_FISH_LABELS = ["fish", "ray-finned fish", "freshwater fish", "bony fish", "animal"]

    def __init__(self):
        """Initialize the AI analysis service."""
        self._vision_client = None
        settings = get_settings()

        if settings.google_cloud_credentials_path:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.google_cloud_credentials_path
        elif settings.google_cloud_credentials_json:
            import tempfile
            import json
            try:
                creds_dict = json.loads(settings.google_cloud_credentials_json)
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                    json.dump(creds_dict, f)
                    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = f.name
            except json.JSONDecodeError as e:
                logger.error(f"Invalid Google Cloud credentials JSON: {e}")

    def _get_vision_client(self):
        """Lazy initialization of Vision client."""
        if self._vision_client is None:
            try:
                from google.cloud import vision
                self._vision_client = vision.ImageAnnotatorClient()
                logger.info("Google Cloud Vision client initialized")
            except Exception as e:
                logger.error(f"Failed to initialize Vision client: {e}")
        return self._vision_client

    @staticmethod
    def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two GPS coordinates in kilometers."""
        R = 6371
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

    async def analyze_catch(
        self, db: AsyncSession, catch_id: int
    ) -> dict:
        """Perform AI analysis on a catch using Google Vision."""
        start_time = datetime.utcnow()

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

        catch_stmt = select(Catch).options(selectinload(Catch.event)).where(Catch.id == catch_id)
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
            # 1. Species Detection via Google Vision
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

            # 4. Calculate validation confidence
            validation_confidence, recommendation = self.calculate_validation_confidence(
                species_result=results["species"],
                anomaly_score=analysis.anomaly_score,
                metadata_warnings=metadata,
            )
            analysis.validation_confidence = validation_confidence
            analysis.validation_recommendation = recommendation

            # 5. Simple insights (no verbose report)
            analysis.ai_insights = self._generate_simple_insights(
                species_result, anomalies, validation_confidence, recommendation
            )

            processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            analysis.status = AiAnalysisStatus.COMPLETE.value
            analysis.processed_at = datetime.utcnow()
            analysis.processing_time_ms = int(processing_time)

            results["validation"] = {
                "confidence": validation_confidence,
                "recommendation": recommendation,
            }

            await db.commit()

            # 6. Auto-validation if eligible
            auto_validated = await self.perform_auto_validation(db, catch_id, analysis)
            results["auto_validated"] = auto_validated

        except Exception as e:
            logger.error(f"AI analysis failed for catch {catch_id}: {e}")
            analysis.status = AiAnalysisStatus.FAILED.value
            analysis.error_message = str(e)
            await db.commit()

        return results

    async def _detect_species(
        self, db: AsyncSession, catch: Catch
    ) -> Optional[dict]:
        """Detect fish species using Google Cloud Vision."""
        image_url = catch.photo_url

        # For video catches, use poster frame
        if catch.original_mime_type and 'video' in catch.original_mime_type:
            if catch.poster_url:
                image_url = catch.poster_url
            elif catch.thumbnail_url:
                image_url = catch.thumbnail_url
            else:
                logger.warning(f"Video catch {catch.id} has no poster for classification")
                return None

        if not image_url:
            return None

        client = self._get_vision_client()
        if not client:
            return None

        try:
            from google.cloud import vision

            image = vision.Image()
            image.source.image_uri = image_url

            response = client.label_detection(image=image, max_results=20)

            if response.error.message:
                logger.error(f"Vision API error for catch {catch.id}: {response.error.message}")
                return None

            labels = response.label_annotations
            detected_labels = [(label.description.lower(), label.score) for label in labels]

            logger.info(f"Vision labels for catch {catch.id}: {detected_labels[:10]}")

            claimed_species_name = catch.fish.name.lower() if catch.fish else None
            if not claimed_species_name:
                return None

            species_confidence = 0.0
            alternatives = []

            expected_labels = self.SPECIES_LABEL_MAP.get(claimed_species_name, [])

            for label, score in detected_labels:
                for expected in expected_labels:
                    if expected in label or label in expected:
                        species_confidence = max(species_confidence, score)
                        break

            fish_present = any(
                any(generic in label for generic in self.GENERIC_FISH_LABELS)
                for label, _ in detected_labels
            )

            for species_name, species_labels in self.SPECIES_LABEL_MAP.items():
                if species_name == claimed_species_name:
                    continue
                alt_confidence = 0.0
                for label, score in detected_labels:
                    for expected in species_labels:
                        if expected in label or label in expected:
                            alt_confidence = max(alt_confidence, score)
                            break
                if alt_confidence > 0.3:
                    stmt = select(Fish).where(Fish.name.ilike(f"%{species_name}%"))
                    result = await db.execute(stmt)
                    alt_fish = result.scalar_one_or_none()
                    alternatives.append({
                        "species_name": species_name,
                        "species_id": alt_fish.id if alt_fish else None,
                        "confidence": round(alt_confidence, 3),
                    })

            alternatives.sort(key=lambda x: x["confidence"], reverse=True)

            return {
                "detected_species_id": catch.fish_id if species_confidence > 0.3 else None,
                "confidence": round(species_confidence, 3),
                "alternatives": alternatives[:5],
                "fish_present": fish_present,
                "raw_labels": [{"label": l, "score": round(s, 3)} for l, s in detected_labels[:10]],
            }

        except Exception as e:
            logger.error(f"Vision species detection failed for catch {catch.id}: {e}")
            return None

    async def _detect_anomalies(
        self, db: AsyncSession, catch: Catch
    ) -> list[dict]:
        """Detect suspicious patterns in catch data."""
        flags = []

        if catch.event:
            event = catch.event

            # GPS Check
            if catch.location_lat and catch.location_lng and event.location:
                event_loc = event.location
                if event_loc.latitude and event_loc.longitude:
                    distance = self.haversine_distance(
                        catch.location_lat, catch.location_lng,
                        event_loc.latitude, event_loc.longitude,
                    )
                    if distance > 0.5:
                        flags.append({
                            "code": "gps_outside_event",
                            "message": f"GPS location {distance:.1f}km from event",
                            "severity": "warning" if distance < 2 else "high",
                            "details": {"distance_km": round(distance, 2)},
                        })

            # Time Check
            if catch.submitted_at and event.start_date:
                if catch.submitted_at < event.start_date:
                    flags.append({
                        "code": "time_before_event",
                        "message": "Submitted before event started",
                        "severity": "high",
                        "details": {},
                    })
                if event.end_date and catch.submitted_at > event.end_date:
                    flags.append({
                        "code": "time_after_event",
                        "message": "Submitted after event ended",
                        "severity": "warning",
                        "details": {},
                    })

            # Rapid Submission Check
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

        # Size Check
        if catch.fish and catch.length and catch.length > 150:
            flags.append({
                "code": "unusual_size",
                "message": f"Very large size ({catch.length}cm)",
                "severity": "info",
                "details": {"length_cm": catch.length},
            })

        return flags

    async def _analyze_metadata(self, catch: Catch) -> list[dict]:
        """Analyze photo metadata for suspicious patterns."""
        warnings = []

        if not catch.catch_time and catch.photo_url:
            warnings.append({
                "code": "no_exif_time",
                "message": "No timestamp in photo EXIF",
                "severity": "info",
                "details": {},
            })

        if catch.original_size_bytes and catch.original_size_bytes < 50000:
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
        severity_weights = {"high": 0.4, "warning": 0.2, "info": 0.05}

        for flag in flags:
            severity = flag.get("severity", "info")
            score += severity_weights.get(severity, 0.05)

        return min(score, 1.0)

    def calculate_validation_confidence(
        self,
        species_result: Optional[dict],
        anomaly_score: float,
        metadata_warnings: list[dict],
    ) -> Tuple[float, str]:
        """Calculate validation confidence and recommendation."""
        confidence = 0.5

        if species_result:
            species_confidence = species_result.get("confidence", 0)
            fish_present = species_result.get("fish_present", False)

            if species_confidence >= 0.7:
                confidence += 0.3
            elif species_confidence >= 0.4:
                confidence += 0.2
            elif fish_present:
                confidence += 0.1
            else:
                confidence -= 0.2

        confidence -= anomaly_score * 0.3
        confidence -= min(len(metadata_warnings) * 0.02, 0.1)
        confidence = max(0.0, min(1.0, confidence))

        if confidence >= 0.85:
            recommendation = "approve"
        elif confidence <= 0.3:
            recommendation = "reject"
        else:
            recommendation = "review"

        return round(confidence, 3), recommendation

    def _generate_simple_insights(
        self,
        species_result: Optional[dict],
        anomaly_flags: list[dict],
        confidence: float,
        recommendation: str,
    ) -> str:
        """Generate simple insights summary."""
        parts = []
        parts.append(f"Confidence: {confidence * 100:.0f}% - {recommendation.upper()}")

        if species_result:
            fish_present = species_result.get("fish_present", False)
            species_conf = species_result.get("confidence", 0)
            if species_conf >= 0.5:
                parts.append(f"Species match: {species_conf * 100:.0f}%")
            elif fish_present:
                parts.append("Fish detected, species uncertain")

        if anomaly_flags:
            flags_summary = [f.get("code", "unknown") for f in anomaly_flags]
            parts.append(f"Flags: {', '.join(flags_summary)}")

        return " | ".join(parts)

    async def perform_auto_validation(
        self,
        db: AsyncSession,
        catch_id: int,
        analysis: CatchAiAnalysis,
    ) -> bool:
        """Attempt to auto-validate a catch if conditions are met."""
        from app.models.user import UserAccount
        from app.models.catch import CatchStatus

        catch_stmt = select(Catch).options(selectinload(Catch.event)).where(Catch.id == catch_id)
        catch_result = await db.execute(catch_stmt)
        catch = catch_result.scalar_one_or_none()

        if not catch or not catch.event:
            return False

        event = catch.event

        if not event.use_ml_auto_validation:
            return False

        if catch.status != CatchStatus.PENDING.value:
            return False

        threshold = event.ml_confidence_threshold
        if not analysis.meets_threshold(threshold):
            return False

        if analysis.validation_recommendation != "approve":
            return False

        moderator_stmt = select(UserAccount).where(UserAccount.email == AI_MODERATOR_EMAIL)
        moderator_result = await db.execute(moderator_stmt)
        ai_moderator = moderator_result.scalar_one_or_none()

        if not ai_moderator:
            logger.error("AI Moderator account not found!")
            return False

        catch.status = CatchStatus.APPROVED.value
        catch.validated_by_id = ai_moderator.id
        catch.validated_at = datetime.now(timezone.utc)

        analysis.auto_validated = True
        analysis.auto_validated_at = datetime.now(timezone.utc)

        await db.commit()

        logger.info(f"Auto-validated catch {catch_id} with confidence {analysis.validation_confidence}")
        return True


# Singleton instance
ai_analysis_service = AiAnalysisService()
