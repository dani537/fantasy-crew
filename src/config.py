import os
from dotenv import load_dotenv

load_dotenv()

# Biwenger score IDs: 1 = Picas AS, 2 = SofaScore, 5 = Media AS + SofaScore
DEFAULT_SCORE_TYPE = "5"


class Credentials:
    BIWENGER_USERNAME = os.getenv("BIWENGER_USERNAME")
    BIWENGER_PASSWORD = os.getenv("BIWENGER_PASSWORD")
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
    LLM_API_KEY = os.getenv("LLM_API_KEY")
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

    @classmethod
    def get_llm_api_key(cls) -> str:
        """Returns active LLM API Key based on LLM_PROVIDER selection."""
        provider = (os.getenv("LLM_PROVIDER") or "openrouter").strip().lower()
        if provider == "deepseek":
            return cls.DEEPSEEK_API_KEY or cls.LLM_API_KEY or cls.OPENROUTER_API_KEY or ""
        elif provider == "openrouter":
            return cls.OPENROUTER_API_KEY or cls.LLM_API_KEY or cls.DEEPSEEK_API_KEY or ""
        return cls.LLM_API_KEY or cls.OPENROUTER_API_KEY or cls.DEEPSEEK_API_KEY or ""

    @classmethod
    def validate(cls) -> list:
        """Returns a list of missing required credential names (empty = all OK)."""
        missing = []
        if not cls.BIWENGER_USERNAME:
            missing.append("BIWENGER_USERNAME")
        if not cls.BIWENGER_PASSWORD:
            missing.append("BIWENGER_PASSWORD")
        if not cls.get_llm_api_key():
            missing.append("OPENROUTER_API_KEY (or LLM_API_KEY / DEEPSEEK_API_KEY)")
        return missing


PROVIDER_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
}

DEFAULT_PROVIDER_MODELS = {
    "openrouter": "meta-llama/llama-3.3-70b-instruct",
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o-mini",
}


class GeneralSettings:
    # Email/report language: es, en, ca... (default: es)
    LANGUAGE = (os.getenv("LANGUAGE") or "es").strip().lower()
    # Priority: SCORE_TYPE env var > default (5: Media Picas AS + SofaScore)
    SCORE_TYPE = os.getenv("SCORE_TYPE") or DEFAULT_SCORE_TYPE
    # When True, no write operation is sent to the Biwenger API (safe testing)
    DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")

    # LLM Provider configuration (default: openrouter)
    LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or "openrouter").strip().lower()

    # Base URL: explicit env var > provider default
    LLM_BASE_URL = os.getenv("LLM_BASE_URL") or PROVIDER_BASE_URLS.get(LLM_PROVIDER, "https://openrouter.ai/api/v1")

    # Model resolution order: LLM_MODEL > OPENROUTER_MODEL > DEEPSEEK_MODEL > provider default
    LLM_MODEL = (
        os.getenv("LLM_MODEL")
        or os.getenv("OPENROUTER_MODEL")
        or os.getenv("DEEPSEEK_MODEL")
        or DEFAULT_PROVIDER_MODELS.get(LLM_PROVIDER, "deepseek/deepseek-chat")
    )

    # Google Sheets Tracker
    GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1V3lDapPrpGgLGVl-rvNi3Ishy70dAo22UEn24toa4kk")
    GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "./credentials_google.json")



LANGUAGE_NAMES = {
    "es": "Spanish",
    "en": "English",
    "ca": "Catalan",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
}


def get_language_name() -> str:
    """Returns the full language name for LLM prompts (e.g. 'Spanish')."""
    return LANGUAGE_NAMES.get(GeneralSettings.LANGUAGE, "Spanish")

