"""Content moderation service for profile picture safety.

Primary: Google Cloud Vision Safe Search API (1,000 free/month)
Fallback: Azure AI Content Safety (5,000 free/month)

Enhanced detection includes:
- Safe Search (adult, violence, racy)
- Label Detection (offensive gestures like middle finger)
- Text Detection (hate speech, offensive words)
"""

import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, List, Set

import httpx
import requests

from app.config import get_settings

logger = logging.getLogger(__name__)

# Offensive labels to reject (detected via Label Detection)
OFFENSIVE_LABELS = {
    "middle finger",
    "obscene gesture",
    "rude gesture",
    "offensive gesture",
    "fuck",
    "flipping off",
    "the finger",
    "bird gesture",
}

# Offensive text patterns to reject (detected via OCR)
# Case-insensitive matching
OFFENSIVE_TEXT_PATTERNS = [
    # English offensive words
    r"\bracis[tm]\b",  # racism, racist
    r"\bnigger\b",
    r"\bnigga\b",
    r"\bfaggot\b",
    r"\bretard\b",
    r"\bkike\b",
    r"\bspic\b",
    r"\bchink\b",
    r"\bgook\b",
    r"\bwetback\b",
    r"\bcunt\b",
    r"\bslut\b",
    r"\bwhore\b",
    r"\bnazi\b",
    r"\bhitler\b",
    r"\bswastika\b",
    r"\bkkk\b",
    r"\bwhite\s*power\b",
    r"\bheil\b",
    r"\bsieg\s*heil\b",
    r"\bfuck\s*(you|off|this)\b",
    r"\bdie\b",
    r"\bkill\s*(yourself|jews|blacks|whites)\b",
    # Romanian offensive words
    r"\bmuie\b",
    r"\bmui3\b",  # leet speak variation
    r"\bpula\b",
    r"\bpwla\b",  # leet speak variation
    r"\bsugi\b",
    r"\bcur\b",
    r"\bfut\b",
    r"\blingi\b",
    r"\bcacat\b",
    r"\bpizdă\b",
    r"\bpizda\b",
    r"\bcurva\b",
    r"\bcoaie\b",
    r"\blabă\b",
    r"\blaba\b",
    r"\bfutut\b",
    r"\bsugeti\b",
]


class SafeSearchLikelihood(IntEnum):
    """Google Vision Safe Search likelihood levels."""

    UNKNOWN = 0
    VERY_UNLIKELY = 1
    UNLIKELY = 2
    POSSIBLE = 3
    LIKELY = 4
    VERY_LIKELY = 5


# Rejection thresholds - reject if score >= threshold
REJECTION_THRESHOLDS = {
    "adult": SafeSearchLikelihood.LIKELY,  # 4+ = reject
    "violence": SafeSearchLikelihood.LIKELY,  # 4+ = reject
    "racy": SafeSearchLikelihood.VERY_LIKELY,  # 5 only = reject
}

# Azure Content Safety severity levels (0, 2, 4, 6)
# We reject at severity >= 4 (Medium or High)
AZURE_REJECTION_THRESHOLD = 4


@dataclass
class ModerationResult:
    """Result of content moderation check."""

    approved: bool
    rejection_reason: Optional[str] = None
    adult_score: Optional[int] = None
    violence_score: Optional[int] = None
    racy_score: Optional[int] = None
    raw_response: Optional[dict] = None
    processing_time_ms: int = 0
    provider: str = "unknown"
    error_message: Optional[str] = None
    detected_labels: List[str] = field(default_factory=list)
    detected_text: Optional[str] = None
    offensive_labels_found: List[str] = field(default_factory=list)
    offensive_text_found: List[str] = field(default_factory=list)


class ContentModerationService:
    """
    Service for moderating profile pictures using AI.

    Primary: Google Cloud Vision Safe Search API (service account auth)
    Fallback: Azure AI Content Safety (5,000 free/month)
    """

    def __init__(self):
        settings = get_settings()
        self.enabled = settings.content_moderation_enabled
        self._vision_client = None
        self._credentials_configured = False

        # Azure Content Safety settings
        self.azure_endpoint = getattr(settings, 'azure_content_safety_endpoint', '')
        self.azure_key = getattr(settings, 'azure_content_safety_key', '')

        # Configure Google Cloud credentials
        if settings.google_cloud_credentials_path:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.google_cloud_credentials_path
            self._credentials_configured = True
            logger.info(f"Google Cloud credentials configured from file: {settings.google_cloud_credentials_path}")
        elif settings.google_cloud_credentials_json:
            import tempfile
            try:
                creds_dict = json.loads(settings.google_cloud_credentials_json)
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                    json.dump(creds_dict, f)
                    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = f.name
                    self._credentials_configured = True
                    logger.info("Google Cloud credentials configured from JSON string")
            except json.JSONDecodeError as e:
                logger.error(f"Invalid Google Cloud credentials JSON: {e}")

    def _get_vision_client(self):
        """Lazy initialization of Vision client."""
        if self._vision_client is None and self._credentials_configured:
            try:
                from google.cloud import vision
                self._vision_client = vision.ImageAnnotatorClient()
                logger.info("Google Cloud Vision client initialized")
            except Exception as e:
                logger.error(f"Failed to initialize Vision client: {e}")
        return self._vision_client

    def _check_offensive_labels(self, labels: List[str]) -> List[str]:
        """Check if any labels match offensive patterns."""
        offensive_found = []
        for label in labels:
            label_lower = label.lower()
            for offensive in OFFENSIVE_LABELS:
                if offensive in label_lower:
                    offensive_found.append(label)
                    break
        return offensive_found

    def _check_offensive_text(self, text: str) -> List[str]:
        """Check if detected text contains offensive content."""
        offensive_found = []
        text_lower = text.lower()
        for pattern in OFFENSIVE_TEXT_PATTERNS:
            matches = re.findall(pattern, text_lower, re.IGNORECASE)
            if matches:
                offensive_found.extend(matches)
        return list(set(offensive_found))  # Remove duplicates

    def _google_vision_moderate_sync(self, image_url: str) -> ModerationResult:
        """
        Use Google Cloud Vision API with service account (synchronous).

        Performs three checks in a single API call:
        1. Safe Search - adult, violence, racy content
        2. Label Detection - offensive gestures (middle finger, etc.)
        3. Text Detection - hate speech, offensive words
        """
        from google.cloud import vision

        # Create fresh client each time to avoid event loop issues in Celery
        client = vision.ImageAnnotatorClient()

        image = vision.Image()
        image.source.image_uri = image_url

        # Request multiple features in a single API call
        features = [
            vision.Feature(type_=vision.Feature.Type.SAFE_SEARCH_DETECTION),
            vision.Feature(type_=vision.Feature.Type.LABEL_DETECTION, max_results=20),
            vision.Feature(type_=vision.Feature.Type.TEXT_DETECTION),
        ]

        request = vision.AnnotateImageRequest(image=image, features=features)
        response = client.annotate_image(request=request)

        if response.error.message:
            raise Exception(response.error.message)

        # 1. Process Safe Search results
        safe_search = response.safe_search_annotation
        adult_score = int(safe_search.adult)
        violence_score = int(safe_search.violence)
        racy_score = int(safe_search.racy)

        # 2. Process Label Detection results
        detected_labels = [label.description for label in response.label_annotations]
        offensive_labels = self._check_offensive_labels(detected_labels)

        # 3. Process Text Detection results
        detected_text = ""
        offensive_text = []
        if response.text_annotations:
            # First annotation contains the full text
            detected_text = response.text_annotations[0].description
            offensive_text = self._check_offensive_text(detected_text)

        # Determine rejection reason
        rejection_reason = None
        if adult_score >= REJECTION_THRESHOLDS["adult"]:
            rejection_reason = "adult_content"
        elif violence_score >= REJECTION_THRESHOLDS["violence"]:
            rejection_reason = "violent_content"
        elif racy_score >= REJECTION_THRESHOLDS["racy"]:
            rejection_reason = "inappropriate_content"
        elif offensive_labels:
            rejection_reason = "offensive_gesture"
        elif offensive_text:
            rejection_reason = "offensive_text"

        logger.info(
            f"Google Vision result: adult={adult_score}, violence={violence_score}, "
            f"racy={racy_score}, labels={len(detected_labels)}, "
            f"offensive_labels={offensive_labels}, offensive_text={offensive_text}, "
            f"rejected={rejection_reason is not None}"
        )

        return ModerationResult(
            approved=rejection_reason is None,
            rejection_reason=rejection_reason,
            adult_score=adult_score,
            violence_score=violence_score,
            racy_score=racy_score,
            raw_response={
                "adult": adult_score,
                "violence": violence_score,
                "racy": racy_score,
                "spoof": int(safe_search.spoof),
                "medical": int(safe_search.medical),
                "labels": detected_labels[:10],  # Store top 10 labels
                "text_detected": bool(detected_text),
            },
            provider="google_vision",
            detected_labels=detected_labels,
            detected_text=detected_text[:500] if detected_text else None,  # Limit text length
            offensive_labels_found=offensive_labels,
            offensive_text_found=offensive_text,
        )

    def _azure_content_safety_sync(self, image_url: str) -> ModerationResult:
        """
        Use Azure AI Content Safety API as fallback (synchronous).

        Free tier: 5,000 images/month
        API: POST <endpoint>/contentsafety/image:analyze?api-version=2024-09-01

        Note: Azure requires base64 encoded image content, not URLs.
        """
        # Download and base64 encode the image
        image_response = requests.get(image_url, timeout=30)
        image_response.raise_for_status()
        image_base64 = base64.b64encode(image_response.content).decode('utf-8')

        url = f"{self.azure_endpoint.rstrip('/')}/contentsafety/image:analyze"
        params = {"api-version": "2024-09-01"}
        headers = {
            "Ocp-Apim-Subscription-Key": self.azure_key,
            "Content-Type": "application/json",
        }

        # Azure requires base64 encoded image content
        payload = {
            "image": {"content": image_base64},
        }

        response = requests.post(url, params=params, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()

        # Parse response
        # Response format: {"categoriesAnalysis": [{"category": "Sexual", "severity": 0}, ...]}
        categories = {item["category"]: item["severity"] for item in data.get("categoriesAnalysis", [])}

        sexual_severity = categories.get("Sexual", 0)
        violence_severity = categories.get("Violence", 0)

        # Map Azure severity (0,2,4,6) to our score format (0-5)
        # 0=Safe -> 1, 2=Low -> 2, 4=Medium -> 4, 6=High -> 5
        def map_severity(sev):
            if sev == 0:
                return 1
            elif sev == 2:
                return 2
            elif sev == 4:
                return 4
            else:  # 6
                return 5

        adult_score = map_severity(sexual_severity)
        violence_score = map_severity(violence_severity)

        # Check rejection thresholds (reject if Azure severity >= 4)
        rejection_reason = None
        if sexual_severity >= AZURE_REJECTION_THRESHOLD:
            rejection_reason = "adult_content"
        elif violence_severity >= AZURE_REJECTION_THRESHOLD:
            rejection_reason = "violent_content"

        logger.info(
            f"Azure Content Safety result: sexual={sexual_severity}, violence={violence_severity}, "
            f"rejected={rejection_reason is not None}"
        )

        return ModerationResult(
            approved=rejection_reason is None,
            rejection_reason=rejection_reason,
            adult_score=adult_score,
            violence_score=violence_score,
            racy_score=0,  # Azure doesn't have a separate "racy" category
            raw_response=data,
            provider="azure_content_safety",
        )

    def moderate_image_sync(self, image_url: str) -> ModerationResult:
        """
        Moderate an image for inappropriate content (synchronous version for Celery).

        Tries Google Vision first, falls back to Azure Content Safety.
        """
        if not self.enabled:
            logger.info("Content moderation disabled")
            return ModerationResult(approved=True, provider="disabled")

        start_time = time.time()

        # Try Google Vision first
        if self._credentials_configured:
            try:
                result = self._google_vision_moderate_sync(image_url)
                result.processing_time_ms = int((time.time() - start_time) * 1000)
                return result
            except Exception as e:
                logger.warning(f"Google Vision failed: {e} (url: {image_url[:100]})")

        # Fallback to Azure Content Safety
        if self.azure_endpoint and self.azure_key:
            try:
                result = self._azure_content_safety_sync(image_url)
                result.processing_time_ms = int((time.time() - start_time) * 1000)
                return result
            except Exception as e:
                logger.warning(f"Azure Content Safety failed: {e} (url: {image_url[:100]})")

        # No providers available - fail open (approve)
        logger.error(f"No moderation providers available (url: {image_url[:100]})")
        return ModerationResult(
            approved=True,
            provider="none",
            error_message="No moderation providers configured",
            processing_time_ms=int((time.time() - start_time) * 1000),
        )

    async def moderate_image(self, image_url: str) -> ModerationResult:
        """
        Moderate an image for inappropriate content (async version).

        Tries Google Vision first, falls back to Azure Content Safety.
        """
        if not self.enabled:
            logger.info("Content moderation disabled")
            return ModerationResult(approved=True, provider="disabled")

        start_time = time.time()

        # Try Google Vision first
        if self._credentials_configured:
            try:
                result = await self._google_vision_moderate(image_url)
                result.processing_time_ms = int((time.time() - start_time) * 1000)
                return result
            except Exception as e:
                logger.warning(f"Google Vision failed: {e} (url: {image_url[:100]})")

        # Fallback to Azure Content Safety
        if self.azure_endpoint and self.azure_key:
            try:
                result = await self._azure_content_safety_moderate(image_url)
                result.processing_time_ms = int((time.time() - start_time) * 1000)
                return result
            except Exception as e:
                logger.warning(f"Azure Content Safety failed: {e} (url: {image_url[:100]})")

        # No providers available - fail open (approve)
        logger.error(f"No moderation providers available (url: {image_url[:100]})")
        return ModerationResult(
            approved=True,
            provider="none",
            error_message="No moderation providers configured",
            processing_time_ms=int((time.time() - start_time) * 1000),
        )

    async def _google_vision_moderate(self, image_url: str) -> ModerationResult:
        """
        Use Google Cloud Vision Safe Search API with service account (async wrapper).
        """
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._google_vision_moderate_sync, image_url)

    async def _azure_content_safety_moderate(self, image_url: str) -> ModerationResult:
        """
        Use Azure AI Content Safety API as fallback (async version).

        Note: Azure requires base64 encoded image content, not URLs.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Download and base64 encode the image
            image_response = await client.get(image_url)
            image_response.raise_for_status()
            image_base64 = base64.b64encode(image_response.content).decode('utf-8')

            url = f"{self.azure_endpoint.rstrip('/')}/contentsafety/image:analyze"
            params = {"api-version": "2024-09-01"}
            headers = {
                "Ocp-Apim-Subscription-Key": self.azure_key,
                "Content-Type": "application/json",
            }

            # Azure requires base64 encoded image content
            payload = {
                "image": {"content": image_base64},
            }

            response = await client.post(url, params=params, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        # Parse response
        categories = {item["category"]: item["severity"] for item in data.get("categoriesAnalysis", [])}

        sexual_severity = categories.get("Sexual", 0)
        violence_severity = categories.get("Violence", 0)

        # Map Azure severity (0,2,4,6) to our score format (0-5)
        def map_severity(sev):
            if sev == 0:
                return 1
            elif sev == 2:
                return 2
            elif sev == 4:
                return 4
            else:
                return 5

        adult_score = map_severity(sexual_severity)
        violence_score = map_severity(violence_severity)

        rejection_reason = None
        if sexual_severity >= AZURE_REJECTION_THRESHOLD:
            rejection_reason = "adult_content"
        elif violence_severity >= AZURE_REJECTION_THRESHOLD:
            rejection_reason = "violent_content"

        logger.info(
            f"Azure Content Safety result: sexual={sexual_severity}, violence={violence_severity}, "
            f"rejected={rejection_reason is not None}"
        )

        return ModerationResult(
            approved=rejection_reason is None,
            rejection_reason=rejection_reason,
            adult_score=adult_score,
            violence_score=violence_score,
            racy_score=0,
            raw_response=data,
            provider="azure_content_safety",
        )


# Singleton instance
content_moderation_service = ContentModerationService()
