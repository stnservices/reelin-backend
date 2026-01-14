#!/usr/bin/env python3
"""
Master ML Training Script - Train All Models

This script trains all ML models for the ReelIn platform:
1. Event Recommendations v2 - With Hall of Fame integration
2. Catch Time Prediction - Optimal fishing times
3. Species Forecast - Predict which species to catch
4. Analytics Predictions - Event attendance & performance

Usage:
    # Train all models
    python scripts/train_all_ml_models.py

    # Train specific models
    python scripts/train_all_ml_models.py --models recommendations,species

    # List available models
    python scripts/train_all_ml_models.py --list

Requirements:
    pip install numpy pandas scikit-learn joblib
"""

import argparse
import asyncio
import importlib
import sys
from datetime import datetime
from pathlib import Path

# Available training modules
AVAILABLE_MODELS = {
    "recommendations": {
        "module": "train_event_recommendations_v2",
        "description": "Event Recommendations v2 (with Hall of Fame)",
    },
    "catch_time": {
        "module": "train_catch_time_prediction",
        "description": "Catch Time Prediction (optimal fishing hours)",
    },
    "species": {
        "module": "train_species_forecast",
        "description": "Species Forecast (multi-class prediction)",
    },
    "analytics": {
        "module": "train_analytics_predictions",
        "description": "Analytics Predictions (attendance & performance)",
    },
}


def list_models():
    """Print available models."""
    print("\nAvailable ML Models:")
    print("=" * 60)
    for name, info in AVAILABLE_MODELS.items():
        print(f"  {name:15} - {info['description']}")
    print()


async def train_model(model_name: str) -> bool:
    """Train a specific model."""
    if model_name not in AVAILABLE_MODELS:
        print(f"Error: Unknown model '{model_name}'")
        return False

    module_name = AVAILABLE_MODELS[model_name]["module"]
    print(f"\n{'='*60}")
    print(f"Training: {AVAILABLE_MODELS[model_name]['description']}")
    print(f"{'='*60}")

    try:
        # Import the module dynamically
        module = importlib.import_module(module_name)

        # Run the main async function
        if hasattr(module, "main"):
            await module.main()
            return True
        else:
            print(f"Error: Module {module_name} has no main() function")
            return False

    except Exception as e:
        print(f"Error training {model_name}: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Train ReelIn ML Models",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--models",
        type=str,
        help="Comma-separated list of models to train (default: all)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available models",
    )

    args = parser.parse_args()

    if args.list:
        list_models()
        return

    # Determine which models to train
    if args.models:
        models_to_train = [m.strip() for m in args.models.split(",")]
        # Validate model names
        for model in models_to_train:
            if model not in AVAILABLE_MODELS:
                print(f"Error: Unknown model '{model}'")
                list_models()
                sys.exit(1)
    else:
        models_to_train = list(AVAILABLE_MODELS.keys())

    print("\n" + "="*60)
    print("ReelIn ML Model Training")
    print("="*60)
    print(f"Started at: {datetime.now().isoformat()}")
    print(f"Models to train: {', '.join(models_to_train)}")

    # Train each model
    results = {}
    for model in models_to_train:
        success = await train_model(model)
        results[model] = "Success" if success else "Failed"

    # Print summary
    print("\n" + "="*60)
    print("Training Summary")
    print("="*60)
    for model, status in results.items():
        status_emoji = "✓" if status == "Success" else "✗"
        print(f"  {status_emoji} {model:15} - {status}")

    print(f"\nCompleted at: {datetime.now().isoformat()}")

    # Exit with error if any model failed
    if "Failed" in results.values():
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
