"""Fish species classification service using trained PyTorch model.

This service provides inference for fish species classification,
replacing Google Vision API when a trained model is available.
"""

import logging
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from PIL import Image

logger = logging.getLogger(__name__)

# Check if PyTorch is available
try:
    import torch
    from torchvision import transforms
    PYTORCH_AVAILABLE = True
except ImportError:
    PYTORCH_AVAILABLE = False
    logger.warning("PyTorch not installed. Fish classifier service disabled.")


# ImageNet normalization constants
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]


class FishClassifierService:
    """Service for fish species classification using trained PyTorch model.

    Usage:
        service = FishClassifierService()
        service.load_model("models/fish_classifier/best_model.pt")
        result = await service.classify_image("https://example.com/fish.jpg")
    """

    def __init__(self):
        self.model = None
        self.class_mapping: Optional[Dict[str, int]] = None
        self.idx_to_class: Optional[Dict[int, str]] = None
        self.device = None
        self.transform = None
        self._is_loaded = False

    @property
    def is_available(self) -> bool:
        """Check if the service is available (model loaded)."""
        return self._is_loaded and PYTORCH_AVAILABLE

    def load_model(self, model_path: str = "models/fish_classifier/best_model.pt") -> bool:
        """Load trained model from checkpoint.

        Args:
            model_path: Path to the model checkpoint file.

        Returns:
            True if model loaded successfully, False otherwise.
        """
        if not PYTORCH_AVAILABLE:
            logger.error("PyTorch not available. Cannot load model.")
            return False

        model_path = Path(model_path)
        if not model_path.exists():
            logger.warning(f"Model not found at {model_path}. Fish classifier disabled.")
            return False

        try:
            # Setup device
            if torch.cuda.is_available():
                self.device = torch.device("cuda")
                logger.info("Using CUDA for fish classification")
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                self.device = torch.device("mps")
                logger.info("Using Apple MPS for fish classification")
            else:
                self.device = torch.device("cpu")
                logger.info("Using CPU for fish classification")

            # Load checkpoint
            checkpoint = torch.load(model_path, map_location=self.device)

            self.class_mapping = checkpoint["class_mapping"]
            self.idx_to_class = {v: k for k, v in self.class_mapping.items()}
            num_classes = len(self.class_mapping)
            model_name = checkpoint.get("model_name", "efficientnet_b0")

            # Create model (import here to avoid circular imports)
            from torchvision import models
            import torch.nn as nn

            if model_name == "efficientnet_b0":
                self.model = models.efficientnet_b0(weights=None)
                in_features = self.model.classifier[1].in_features
                self.model.classifier = nn.Sequential(
                    nn.Dropout(p=0.3),
                    nn.Linear(in_features, 512),
                    nn.ReLU(inplace=True),
                    nn.Dropout(p=0.15),
                    nn.Linear(512, num_classes),
                )
            elif model_name == "efficientnet_b2":
                self.model = models.efficientnet_b2(weights=None)
                in_features = self.model.classifier[1].in_features
                self.model.classifier = nn.Sequential(
                    nn.Dropout(p=0.3),
                    nn.Linear(in_features, 512),
                    nn.ReLU(inplace=True),
                    nn.Dropout(p=0.15),
                    nn.Linear(512, num_classes),
                )
            elif model_name == "resnet50":
                self.model = models.resnet50(weights=None)
                in_features = self.model.fc.in_features
                self.model.fc = nn.Sequential(
                    nn.Dropout(p=0.3),
                    nn.Linear(in_features, 512),
                    nn.ReLU(inplace=True),
                    nn.Dropout(p=0.15),
                    nn.Linear(512, num_classes),
                )
            else:
                raise ValueError(f"Unknown model architecture: {model_name}")

            # Load state dict, stripping 'backbone.' prefix if present
            state_dict = checkpoint["model_state_dict"]
            # Check if state dict has 'backbone.' prefix (from FishClassifier wrapper)
            if any(k.startswith("backbone.") for k in state_dict.keys()):
                state_dict = {k.replace("backbone.", ""): v for k, v in state_dict.items()}
            self.model.load_state_dict(state_dict)
            self.model.to(self.device)
            self.model.eval()

            # Setup inference transforms
            self.transform = transforms.Compose([
                transforms.Resize(256),
                transforms.CenterCrop(224),
                transforms.ToTensor(),
                transforms.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
            ])

            self._is_loaded = True
            logger.info(f"Fish classifier loaded: {num_classes} species, model={model_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to load fish classifier model: {e}")
            self._is_loaded = False
            return False

    def _download_image_from_s3(self, image_url: str) -> Optional[bytes]:
        """Download image from S3/Spaces using boto3.

        Extracts S3 key from CDN URL and downloads directly.
        """
        try:
            from app.core.storage import StorageService

            storage = StorageService()

            # Extract S3 key from CDN URL
            # URL format: https://bucket.region.cdn.digitaloceanspaces.com/path/to/file
            # We need: path/to/file
            cdn_base = storage.cdn_base_url
            if image_url.startswith(cdn_base):
                s3_key = image_url[len(cdn_base):].lstrip("/")
            else:
                # Try to extract from other URL formats
                # https://region.digitaloceanspaces.com/bucket/path
                parts = image_url.replace("https://", "").split("/", 1)
                if len(parts) > 1:
                    s3_key = parts[1]
                else:
                    return None

            # Download from S3
            response = storage.client.get_object(
                Bucket=storage.bucket_name,
                Key=s3_key
            )
            return response["Body"].read()

        except Exception as e:
            logger.warning(f"S3 download failed for {image_url}: {e}")
            return None

    async def classify_image(
        self,
        image_url: str,
        top_k: int = 5,
        timeout: float = 30.0,
        image_bytes: Optional[bytes] = None,
    ) -> Optional[Dict]:
        """Classify fish species from image URL or bytes.

        Args:
            image_url: URL to the fish image (fallback if bytes not provided).
            top_k: Number of top predictions to return.
            timeout: Request timeout in seconds.
            image_bytes: Optional pre-loaded image bytes (avoids re-download).

        Returns:
            Dictionary with classification results, or None if failed.
        """
        if not self.is_available:
            logger.warning("Fish classifier not available")
            return None

        try:
            # Use provided bytes if available, otherwise download
            if image_bytes:
                image_data = image_bytes
                logger.info(f"Using pre-loaded image bytes ({len(image_data)} bytes)")
            else:
                # Try S3 direct download first (bypasses CDN restrictions)
                image_data = self._download_image_from_s3(image_url)

                if not image_data:
                    # Fall back to direct HTTP download
                    async with httpx.AsyncClient() as client:
                        response = await client.get(image_url, timeout=timeout)
                        response.raise_for_status()
                        image_data = response.content

            # Load and preprocess image
            image = Image.open(BytesIO(image_data)).convert("RGB")
            input_tensor = self.transform(image).unsqueeze(0).to(self.device)

            # Run inference
            with torch.no_grad():
                outputs = self.model(input_tensor)
                probs = torch.softmax(outputs, dim=1)[0]

            # Get top-k predictions
            top_probs, top_indices = probs.topk(min(top_k, len(self.class_mapping)))

            predictions = []
            for prob, idx in zip(top_probs, top_indices):
                predictions.append({
                    "species_slug": self.idx_to_class[idx.item()],
                    "confidence": prob.item(),
                })

            # Primary prediction
            primary = predictions[0]

            # Get alternatives (skip primary, only include if > 5% confidence)
            alternatives = [
                p for p in predictions[1:]
                if p["confidence"] > 0.05
            ]

            return {
                "detected_species_slug": primary["species_slug"],
                "confidence": primary["confidence"],
                "alternatives": alternatives,
                "all_probabilities": {
                    self.idx_to_class[i]: p.item()
                    for i, p in enumerate(probs)
                },
                "model_type": "custom_pytorch",
            }

        except httpx.HTTPError as e:
            logger.error(f"Failed to download image {image_url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Fish classification failed for {image_url}: {e}")
            return None

    def classify_image_sync(
        self,
        image_path: str,
        top_k: int = 5,
    ) -> Optional[Dict]:
        """Classify fish species from local image (synchronous).

        Args:
            image_path: Path to the fish image.
            top_k: Number of top predictions to return.

        Returns:
            Dictionary with classification results, or None if failed.
        """
        if not self.is_available:
            logger.warning("Fish classifier not available")
            return None

        try:
            # Load and preprocess image
            image = Image.open(image_path).convert("RGB")
            input_tensor = self.transform(image).unsqueeze(0).to(self.device)

            # Run inference
            with torch.no_grad():
                outputs = self.model(input_tensor)
                probs = torch.softmax(outputs, dim=1)[0]

            # Get top-k predictions
            top_probs, top_indices = probs.topk(min(top_k, len(self.class_mapping)))

            predictions = []
            for prob, idx in zip(top_probs, top_indices):
                predictions.append({
                    "species_slug": self.idx_to_class[idx.item()],
                    "confidence": prob.item(),
                })

            primary = predictions[0]
            alternatives = [p for p in predictions[1:] if p["confidence"] > 0.05]

            return {
                "detected_species_slug": primary["species_slug"],
                "confidence": primary["confidence"],
                "alternatives": alternatives,
                "model_type": "custom_pytorch",
            }

        except Exception as e:
            logger.error(f"Fish classification failed for {image_path}: {e}")
            return None


# Singleton instance
fish_classifier_service = FishClassifierService()


def init_fish_classifier(model_path: str = "models/fish_classifier/best_model.pt"):
    """Initialize fish classifier service at startup.

    Call this from your application startup to load the model.
    """
    if fish_classifier_service.load_model(model_path):
        logger.info("Fish classifier service initialized successfully")
    else:
        logger.warning("Fish classifier service not available (model not found or PyTorch not installed)")
