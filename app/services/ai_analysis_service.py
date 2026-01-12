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
    # Maps species name (lowercase) to possible Vision API labels
    SPECIES_LABEL_MAP = {
        # Romanian species names (from database)
        "biban": ["perch", "european perch", "perca", "perca fluviatilis", "bass", "biban"],
        "clean": ["asp", "leuciscus aspius", "aspius aspius", "rapfen", "clean"],
        "salau": ["zander", "pike-perch", "sander", "sander lucioperca", "pikeperch", "salau"],
        "avat": ["asp", "chub", "squalius", "leuciscus", "avat"],
        "stiuca": ["pike", "northern pike", "esox", "esox lucius", "stiuca"],
        "somn": ["catfish", "wels catfish", "silurus", "silurus glanis", "wels", "sheatfish", "somn"],
        "șalău vărgat": ["zander", "pike-perch", "striped zander"],
        # English names (for international events)
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

    # Generic fish labels that indicate a fish is present but species is uncertain
    GENERIC_FISH_LABELS = ["fish", "ray-finned fish", "freshwater fish", "bony fish", "animal"]

    def __init__(self):
        """Initialize the AI analysis service."""
        self._vision_client = None
        settings = get_settings()

        # Configure Google Cloud credentials
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
                logger.info("Google Cloud Vision client initialized for AI analysis")
            except Exception as e:
                logger.error(f"Failed to initialize Vision client: {e}")
        return self._vision_client

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

    async def analyze_catch(
        self, db: AsyncSession, catch_id: int, image_bytes: Optional[bytes] = None
    ) -> dict:
        """
        Perform full AI analysis on a catch.
        Returns analysis results dict.

        Args:
            db: Database session
            catch_id: ID of the catch to analyze
            image_bytes: Optional pre-loaded image bytes (avoids re-download from S3)
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

        # Get catch with event info (eager load to avoid lazy loading in async)
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
            # 1. Species Detection
            species_result = await self._detect_species(db, catch, image_bytes=image_bytes)
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

            # 4. Calculate validation confidence and recommendation
            validation_confidence, recommendation = self.calculate_validation_confidence(
                species_result=results["species"],
                anomaly_score=analysis.anomaly_score,
                metadata_warnings=metadata,
            )
            analysis.validation_confidence = validation_confidence
            analysis.validation_recommendation = recommendation

            # 5. Generate human-readable insights
            insights = self.generate_ai_insights(
                catch=catch,
                species_result=results["species"],
                anomaly_flags=results["anomalies"],
                metadata_warnings=metadata,
                validation_confidence=validation_confidence,
                recommendation=recommendation,
            )
            analysis.ai_insights = insights

            # Calculate processing time
            processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000

            # Update analysis record
            analysis.status = AiAnalysisStatus.COMPLETE.value
            analysis.processed_at = datetime.utcnow()
            analysis.processing_time_ms = int(processing_time)

            # Store results for return
            results["validation"] = {
                "confidence": validation_confidence,
                "recommendation": recommendation,
                "insights": insights,
            }

            await db.commit()

            # 6. Attempt auto-validation if eligible
            auto_validated = await self.perform_auto_validation(db, catch_id, analysis)
            results["auto_validated"] = auto_validated

        except Exception as e:
            logger.error(f"AI analysis failed for catch {catch_id}: {e}")
            analysis.status = AiAnalysisStatus.FAILED.value
            analysis.error_message = str(e)
            await db.commit()

        return results

    async def _detect_species(
        self, db: AsyncSession, catch: Catch, image_bytes: Optional[bytes] = None
    ) -> Optional[dict]:
        """
        Detect fish species from image using custom PyTorch model or Google Vision fallback.
        Returns dict with detected_species_id, confidence, alternatives.

        Args:
            db: Database session
            catch: The catch to analyze
            image_bytes: Optional pre-loaded image bytes (avoids re-download from S3)

        For video catches, uses the poster_url (extracted frame) for classification.
        """
        # Determine which image URL to use (fallback if no bytes provided)
        image_url = catch.photo_url

        # For video catches, use the poster frame (extracted thumbnail)
        # Note: image_bytes from upload already contains poster frame for videos
        if catch.original_mime_type and 'video' in catch.original_mime_type:
            if image_bytes:
                # We already have poster frame bytes from upload - use them
                logger.info(f"Using pre-loaded poster bytes for video catch {catch.id}")
            elif catch.poster_url:
                image_url = catch.poster_url
                logger.info(f"Using poster frame URL for video catch {catch.id}")
            elif catch.thumbnail_url:
                image_url = catch.thumbnail_url
                logger.info(f"Using thumbnail for video catch {catch.id}")
            else:
                logger.warning(f"Video catch {catch.id} has no poster/thumbnail for classification")
                return None

        if not image_url and not image_bytes:
            logger.warning(f"No image URL or bytes for catch {catch.id}")
            return None

        # Try custom PyTorch model first
        custom_result = await self._detect_species_custom_model(db, catch, image_url, image_bytes)
        if custom_result:
            logger.info(f"Used custom model for catch {catch.id}: {custom_result.get('detected_species_id')}")
            return custom_result

        # Fallback to Google Vision API
        return await self._detect_species_google_vision(db, catch, image_url)

    async def _detect_species_custom_model(
        self, db: AsyncSession, catch: Catch, image_url: str, image_bytes: Optional[bytes] = None
    ) -> Optional[dict]:
        """
        Detect fish species using custom trained PyTorch model.
        Returns dict with detected_species_id, confidence, alternatives.

        Args:
            db: Database session
            catch: The catch to analyze
            image_url: URL to the image (fallback if bytes not provided)
            image_bytes: Optional pre-loaded image bytes (avoids re-download from S3)
        """
        try:
            from app.services.fish_classifier_service import fish_classifier_service, init_fish_classifier

            # Initialize model if not already loaded (needed for Celery workers)
            if not fish_classifier_service.is_available:
                logger.info("Initializing fish classifier model...")
                init_fish_classifier()

            if not fish_classifier_service.is_available:
                logger.debug("Custom fish classifier not available after init attempt")
                return None

            result = await fish_classifier_service.classify_image(image_url, image_bytes=image_bytes)

            if not result:
                return None

            # Map slug to species ID
            species_slug = result.get("detected_species_slug")
            detected_fish = None

            if species_slug:
                stmt = select(Fish).where(Fish.slug == species_slug)
                species_result = await db.execute(stmt)
                detected_fish = species_result.scalar_one_or_none()

                # Try name match if slug doesn't work
                if not detected_fish:
                    stmt = select(Fish).where(Fish.name.ilike(f"%{species_slug}%"))
                    species_result = await db.execute(stmt)
                    detected_fish = species_result.scalar_one_or_none()

            # Build alternatives with species IDs
            alternatives = []
            for alt in result.get("alternatives", []):
                alt_slug = alt.get("species_slug")
                stmt = select(Fish).where(Fish.slug == alt_slug)
                alt_result = await db.execute(stmt)
                alt_fish = alt_result.scalar_one_or_none()

                alternatives.append({
                    "species_name": alt_slug,
                    "species_id": alt_fish.id if alt_fish else None,
                    "confidence": round(alt.get("confidence", 0), 3),
                })

            confidence = result.get("confidence", 0)

            return {
                "detected_species_id": detected_fish.id if detected_fish else None,
                "confidence": round(confidence, 3),
                "alternatives": alternatives[:5],
                "fish_present": confidence > 0.3,
                "model_type": "custom_pytorch",
            }

        except Exception as e:
            logger.error(f"Custom model species detection failed for catch {catch.id}: {e}")
            return None

    async def _detect_species_google_vision(
        self, db: AsyncSession, catch: Catch, image_url: str
    ) -> Optional[dict]:
        """
        Detect fish species from image using Google Cloud Vision (fallback).
        Returns dict with detected_species_id, confidence, alternatives.
        """
        client = self._get_vision_client()
        if not client:
            logger.warning(f"Vision client not available for catch {catch.id}")
            return None

        try:
            from google.cloud import vision

            image = vision.Image()
            image.source.image_uri = image_url

            # Request label detection
            response = client.label_detection(image=image, max_results=20)

            if response.error.message:
                logger.error(f"Vision API error for catch {catch.id}: {response.error.message}")
                return None

            labels = response.label_annotations
            detected_labels = [(label.description.lower(), label.score) for label in labels]

            logger.info(f"Detected labels for catch {catch.id}: {detected_labels[:10]}")

            # Get the claimed species name
            claimed_species_name = catch.fish.name.lower() if catch.fish else None

            if not claimed_species_name:
                return None

            # Check if claimed species is detected
            species_confidence = 0.0
            alternatives = []

            # Get the expected labels for the claimed species
            expected_labels = self.SPECIES_LABEL_MAP.get(claimed_species_name, [])

            # Calculate match confidence
            for label, score in detected_labels:
                # Direct match with expected labels
                for expected in expected_labels:
                    if expected in label or label in expected:
                        species_confidence = max(species_confidence, score)
                        break

            # Check for fish presence (generic fish labels)
            fish_present = any(
                any(generic in label for generic in self.GENERIC_FISH_LABELS)
                for label, _ in detected_labels
            )

            # Find alternative species matches
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
                    # Look up species ID
                    stmt = select(Fish).where(Fish.name.ilike(f"%{species_name}%"))
                    result = await db.execute(stmt)
                    alt_fish = result.scalar_one_or_none()
                    alternatives.append({
                        "species_name": species_name,
                        "species_id": alt_fish.id if alt_fish else None,
                        "confidence": round(alt_confidence, 3),
                    })

            # Sort alternatives by confidence
            alternatives.sort(key=lambda x: x["confidence"], reverse=True)

            return {
                "detected_species_id": catch.fish_id if species_confidence > 0.3 else None,
                "confidence": round(species_confidence, 3),
                "alternatives": alternatives[:5],
                "fish_present": fish_present,
                "raw_labels": [{"label": l, "score": round(s, 3)} for l, s in detected_labels[:15]],
                "model_type": "google_vision",
            }

        except Exception as e:
            logger.error(f"Google Vision species detection failed for catch {catch.id}: {e}")
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

    def calculate_validation_confidence(
        self,
        species_result: Optional[dict],
        anomaly_score: float,
        metadata_warnings: list[dict],
    ) -> Tuple[float, str]:
        """
        Calculate overall validation confidence score and recommendation.

        Returns:
            Tuple of (confidence_score, recommendation)
            - confidence_score: 0.0 to 1.0
            - recommendation: "approve", "reject", or "review"
        """
        # Base confidence starts at 0.5 (neutral)
        confidence = 0.5

        # Species detection contributes up to 0.3
        if species_result:
            species_confidence = species_result.get("confidence", 0)
            fish_present = species_result.get("fish_present", False)

            if species_confidence >= 0.7:
                confidence += 0.3  # High species match
            elif species_confidence >= 0.4:
                confidence += 0.2  # Moderate species match
            elif fish_present:
                confidence += 0.1  # At least a fish is visible
            else:
                confidence -= 0.2  # No fish detected

        # Anomaly score reduces confidence (up to -0.3)
        confidence -= anomaly_score * 0.3

        # Metadata warnings reduce confidence slightly (up to -0.1)
        warning_penalty = len(metadata_warnings) * 0.02
        confidence -= min(warning_penalty, 0.1)

        # Clamp confidence to 0.0-1.0
        confidence = max(0.0, min(1.0, confidence))

        # Determine recommendation
        if confidence >= 0.85:
            recommendation = "approve"
        elif confidence <= 0.3:
            recommendation = "reject"
        else:
            recommendation = "review"

        return round(confidence, 3), recommendation

    def generate_ai_insights(
        self,
        catch: Catch,
        species_result: Optional[dict],
        anomaly_flags: list[dict],
        metadata_warnings: list[dict],
        validation_confidence: float,
        recommendation: str,
    ) -> str:
        """
        Generate human-readable insights for validators.

        Returns a formatted string with AI findings.
        """
        insights = []
        insights.append(f"🤖 Fane AI Analysis Report")
        insights.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━")
        insights.append(f"")
        insights.append(f"📊 Confidence: {validation_confidence * 100:.1f}%")
        insights.append(f"📋 Recommendation: {recommendation.upper()}")
        insights.append(f"")

        # Species analysis
        insights.append(f"🐟 Species Analysis:")
        if species_result:
            species_conf = species_result.get("confidence", 0)
            fish_present = species_result.get("fish_present", False)

            if species_conf >= 0.7:
                insights.append(f"   ✅ High confidence species match ({species_conf * 100:.0f}%)")
            elif species_conf >= 0.4:
                insights.append(f"   ⚠️ Moderate species match ({species_conf * 100:.0f}%)")
            elif fish_present:
                insights.append(f"   ❓ Fish detected but species uncertain")
            else:
                insights.append(f"   ❌ No clear fish detected in image")

            # Alternative species
            alternatives = species_result.get("alternatives", [])
            if alternatives:
                insights.append(f"   Alternative matches:")
                for alt in alternatives[:3]:
                    insights.append(f"      - {alt['species_name']}: {alt['confidence'] * 100:.0f}%")
        else:
            insights.append(f"   ⏳ Species detection not available")

        insights.append(f"")

        # Anomaly findings
        if anomaly_flags:
            insights.append(f"⚠️ Anomaly Flags ({len(anomaly_flags)}):")
            for flag in anomaly_flags:
                severity_icon = {"high": "🔴", "warning": "🟡", "info": "🔵"}.get(flag.get("severity"), "⚪")
                insights.append(f"   {severity_icon} {flag.get('message', flag.get('code'))}")
        else:
            insights.append(f"✅ No anomalies detected")

        insights.append(f"")

        # Metadata warnings
        if metadata_warnings:
            insights.append(f"📝 Metadata Notes:")
            for warning in metadata_warnings:
                insights.append(f"   • {warning.get('message', warning.get('code'))}")

        # Size check
        if catch.length:
            insights.append(f"")
            insights.append(f"📏 Reported Length: {catch.length} cm")
            if catch.fish and catch.fish.max_length:
                if catch.length > catch.fish.max_length * 0.9:
                    insights.append(f"   ⚠️ Near maximum size for species")

        return "\n".join(insights)

    async def perform_auto_validation(
        self,
        db: AsyncSession,
        catch_id: int,
        analysis: CatchAiAnalysis,
    ) -> bool:
        """
        Attempt to auto-validate a catch if conditions are met.

        Returns True if auto-validated, False otherwise.
        """
        from app.models.user import UserAccount
        from app.models.catch import CatchStatus

        # Get the catch with event (eager load to avoid lazy loading in async)
        catch_stmt = select(Catch).options(selectinload(Catch.event)).where(Catch.id == catch_id)
        catch_result = await db.execute(catch_stmt)
        catch = catch_result.scalar_one_or_none()

        if not catch or not catch.event:
            return False

        event = catch.event

        # Check if auto-validation is enabled for this event
        if not event.use_ml_auto_validation:
            logger.info(f"Auto-validation disabled for event {event.id}")
            return False

        # Check if catch is still pending
        if catch.status != CatchStatus.PENDING.value:
            logger.info(f"Catch {catch_id} already validated: {catch.status}")
            return False

        # Check confidence threshold
        threshold = event.ml_confidence_threshold
        if not analysis.meets_threshold(threshold):
            logger.info(
                f"Catch {catch_id} confidence {analysis.validation_confidence} "
                f"below threshold {threshold}"
            )
            return False

        # Must have "approve" recommendation
        if analysis.validation_recommendation != "approve":
            logger.info(f"Catch {catch_id} recommendation is {analysis.validation_recommendation}")
            return False

        # Get AI Moderator account
        moderator_stmt = select(UserAccount).where(UserAccount.email == AI_MODERATOR_EMAIL)
        moderator_result = await db.execute(moderator_stmt)
        ai_moderator = moderator_result.scalar_one_or_none()

        if not ai_moderator:
            logger.error("AI Moderator account not found! Run migrations.")
            return False

        # Perform auto-validation
        catch.status = CatchStatus.APPROVED.value
        catch.validated_by_id = ai_moderator.id
        catch.validated_at = datetime.now(timezone.utc)

        analysis.auto_validated = True
        analysis.auto_validated_at = datetime.now(timezone.utc)

        await db.commit()

        logger.info(
            f"✅ Auto-validated catch {catch_id} with confidence {analysis.validation_confidence}"
        )
        return True


# Singleton instance
ai_analysis_service = AiAnalysisService()
