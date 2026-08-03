"""
Dynamic model selector - tries the OpenRouter API, falls back to a proven list.
Caches the chosen model for 7 days.
"""

import json
import time
import requests
from pathlib import Path
from typing import List, Dict, Optional

from src.utils.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, LLM_MODEL

CACHE_FILE = "model_cache.json"
CACHE_EXPIRY_SECONDS = 7 * 86400  # 7 days

# Hardcoded fallback models (chat + function calling optional, but we just need chat)
FALLBACK_MODELS = [
    "openai/gpt-3.5-turbo",                  # cheap, widely available
    "meta-llama/llama-3.1-8b-instruct:free", # free, always available
    "google/gemini-2.0-flash-001",           # another cheap option
]

def _fetch_openrouter_models() -> List[Dict]:
    """Fetch model list from OpenRouter. Returns empty list on failure."""
    url = f"{OPENROUTER_BASE_URL}/models"
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # OpenRouter returns a list under "data"
        return data.get("data", [])
    except Exception as e:
        print(f"⚠️ Model list fetch failed: {e}")
        return []

def _is_chat_model(model: Dict) -> bool:
    """Check if model is a chat model and not deprecated."""
    if model.get("type") != "chat":
        return False
    # Skip deprecated models
    if model.get("status") == "deprecated":
        return False
    return True

def _select_best_model() -> Optional[str]:
    """Return the best model from the API, or None if we can't decide."""
    models = _fetch_openrouter_models()
    if not models:
        return None

    # Filter to chat models only
    chat_models = [m for m in models if _is_chat_model(m)]
    if not chat_models:
        return None

    # Sort by cost (lowest total price per 1k tokens)
    def price(model):
        p = model.get("pricing", {})
        return float(p.get("prompt", 0)) + float(p.get("completion", 0))

    chat_models.sort(key=price)
    best = chat_models[0]
    print(f"✅ Dynamic model: {best['id']} (${price(best):.6f} per 1k tokens)")
    return best["id"]

def get_best_model() -> str:
    """
    Return the model ID to use.
    1. Try cached model if not expired.
    2. Try dynamic selection from OpenRouter.
    3. Fall back to static list if both fail.
    4. Ultimate fallback: LLM_MODEL from config.
    """
    cache_path = Path(CACHE_FILE)
    # Check cache
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
            if time.time() - cache["timestamp"] < CACHE_EXPIRY_SECONDS:
                return cache["model_id"]
        except:
            pass

    # Try dynamic selection
    dynamic = _select_best_model()
    if dynamic:
        # Save to cache
        with open(cache_path, "w") as f:
            json.dump({"model_id": dynamic, "timestamp": time.time()}, f)
        return dynamic

    # Fallback to static list – try to use the first available (we just assume they exist)
    for model_id in FALLBACK_MODELS:
        # For simplicity, we assume these always work; no API check needed.
        # In a production system, we could verify with a lightweight call.
        print(f"Using fallback model: {model_id}")
        # Save to cache
        with open(cache_path, "w") as f:
            json.dump({"model_id": model_id, "timestamp": time.time()}, f)
        return model_id

    # Ultimate fallback – the config model (which you've already set to a working one)
    print(f" Using config fallback: {LLM_MODEL}")
    return LLM_MODEL