import os
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent.parent
PLATFORM_ROOT = PACKAGE_DIR.parent
load_dotenv(PLATFORM_ROOT / ".env")

DATA_DIR = Path(os.getenv("PDF_EXTRACT_DATA_DIR") or PLATFORM_ROOT / "instance" / "pdf_extract")
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "app.db"
FRONTEND_DIR = PACKAGE_DIR / "frontend"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()

def _shipped_own_companies() -> str:
    """The company names that ship with the code (pdf_extract/own_companies.txt); the settings page can override them."""
    try:
        return (PACKAGE_DIR / "own_companies.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


DEFAULT_SETTINGS = {
    "default_provider": "claude",
    "claude_model": "claude-sonnet-5-5",
    "claude_fast_model": "claude-haiku-5-5",
    "deepseek_model": "deepseek-flash",
    # Per vendor: the models fetched from its API (newest first) and the ids the administrator switched on.
    # Only switched-on models are offered in the model drop-downs.
    "model_catalog": {},
    "enabled_models": {},
    # Model for "AI field mapping": a preset key from extractor.model_presets() (haiku / deepseek / sonnet / custom),
    # or "default" to follow the default extraction model.
    "mapping_model": "sonnet",
    # Our own company names (one per line or comma-separated). They are payer / recipient, never the vendor, when
    # batch auto-templating works out who issued a document.
    "own_companies": _shipped_own_companies(),
    "custom_base_url": "http://localhost:11434/v1",
    "custom_api_key": "ollama",
    "custom_model": "qwen2.5vl",
    "custom_vision": True,
    "render_dpi": 150,
    "image_grayscale": True,
    # 0 = automatic: as many documents at once as are waiting or running, between 3 and max_workers_cap.
    # A number above 0 fixes the parallelism to that value.
    "max_workers": 0,
    "max_workers_cap": 16,
    "few_shot_examples": 2,
    # USD per 1M tokens, from the providers' official pricing pages (checked 2026-10-08).
    # long_*: higher rates when one prompt exceeds long_threshold tokens (cache rates scale with it).
    # peak_multiplier: DeepSeek charges 2x during peak hours (see backend/usage.py).
    "prices": {
        "claude-sonnet-5-5": {"input": 2, "output": 10, "cache_read": 0.10, "cache_write": 2.5},
        "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125,
                             "long_threshold": 100000, "long_input": 0.50, "long_output": 2.50},
        "claude-opus-5-5": {"input": 4, "output": 20, "cache_read": 0.20, "cache_write": 5},
        "claude-sonnet-5": {"input": 2, "output": 10, "cache_read": 0.20, "cache_write": 2.5},
        "claude-sonnet-4-6": {"input": 3, "output": 15, "cache_read": 0.30, "cache_write": 3.75},
        "claude-haiku-4-5": {"input": 1, "output": 5, "cache_read": 0.10, "cache_write": 1.25},
        "deepseek-flash": {"input": 0.15, "output": 0.60, "cache_read": 0.003, "cache_write": 0,
                           "peak_multiplier": 2},
        "deepseek-v4-pro": {"input": 0.66, "output": 1.98, "cache_read": 0.022, "cache_write": 0,
                            "peak_multiplier": 2},
    },
}
