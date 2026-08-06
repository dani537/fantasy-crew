import os
from dotenv import load_dotenv

load_dotenv()

# Biwenger score IDs: 1 = Picas AS, 2 = SofaScore, 5 = Media AS + SofaScore
DEFAULT_SCORE_TYPE = "5"


class Credentials:
    BIWENGER_USERNAME = os.getenv("BIWENGER_USERNAME")
    BIWENGER_PASSWORD = os.getenv("BIWENGER_PASSWORD")
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

    @classmethod
    def validate(cls) -> list:
        """Returns a list of missing required credential names (empty = all OK)."""
        missing = []
        if not cls.BIWENGER_USERNAME:
            missing.append("BIWENGER_USERNAME")
        if not cls.BIWENGER_PASSWORD:
            missing.append("BIWENGER_PASSWORD")
        if not cls.DEEPSEEK_API_KEY:
            missing.append("DEEPSEEK_API_KEY")
        return missing


class GeneralSettings:
    # Email/report language: es, en, ca... (default: es)
    LANGUAGE = (os.getenv("LANGUAGE") or "es").strip().lower()
    # Priority: SCORE_TYPE env var > default (5: Media Picas AS + SofaScore)
    SCORE_TYPE = os.getenv("SCORE_TYPE") or DEFAULT_SCORE_TYPE
    # DeepSeek LLM Model (default: deepseek-v4-flash)
    DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL") or "deepseek-v4-flash"
    # When True, no write operation is sent to the Biwenger API (safe testing)
    DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")


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
