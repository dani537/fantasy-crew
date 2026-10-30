"""
Configuration and Credentials Manager (Fantasy Champion Edition)
================================================================
Centralized, dynamic loading of environment variables, Streamlit Cloud secrets,
Multi-Tier LLM Routing (Google AI Pro / Gemini API + OpenRouter 2026 Tiered Models),
and Competitive Autopilot policy settings.
"""

import os
from typing import List
from dotenv import load_dotenv

# Load local .env
load_dotenv()


def _get_config_var(key: str, default: str = "") -> str:
    """
    Dynamically retrieves a configuration variable:
    1. Checks os.environ (case-insensitive)
    2. Checks streamlit.secrets (case-insensitive, top level or nested sections)
    3. Returns default
    """
    # 1. os.environ check
    for k in (key, key.upper(), key.lower()):
        val = os.getenv(k)
        if val is not None and str(val).strip() != "":
            return str(val).strip()

    # 2. streamlit.secrets check
    try:
        import streamlit as st
        if hasattr(st, "secrets") and st.secrets is not None:
            # Direct key match
            for k in (key, key.upper(), key.lower()):
                if k in st.secrets:
                    val = st.secrets[k]
                    if val is not None and str(val).strip() != "":
                        return str(val).strip()

            # Nested sections check (e.g. [biwenger], [openrouter], etc.)
            for sec_name, sec_dict in st.secrets.items():
                if isinstance(sec_dict, dict) or hasattr(sec_dict, "items"):
                    for sk, sv in sec_dict.items():
                        if sk.lower() == key.lower():
                            if sv is not None and str(sv).strip() != "":
                                return str(sv).strip()
    except Exception:
        pass

    return default


class _CredentialsMeta(type):
    """Metaclass allowing class-level property access (e.g. Credentials.BIWENGER_USERNAME)."""
    @property
    def BIWENGER_TOKEN(cls) -> str:
        return _get_config_var("BIWENGER_TOKEN")

    @property
    def BIWENGER_USERNAME(cls) -> str:
        return _get_config_var("BIWENGER_USERNAME")

    @property
    def BIWENGER_PASSWORD(cls) -> str:
        return _get_config_var("BIWENGER_PASSWORD")

    @property
    def GEMINI_API_KEY(cls) -> str:
        return _get_config_var("GEMINI_API_KEY") or _get_config_var("GOOGLE_API_KEY")

    @property
    def OPENROUTER_API_KEY(cls) -> str:
        return _get_config_var("OPENROUTER_API_KEY")

    @property
    def LLM_API_KEY(cls) -> str:
        return _get_config_var("LLM_API_KEY")

    @property
    def DEEPSEEK_API_KEY(cls) -> str:
        return _get_config_var("DEEPSEEK_API_KEY")


class Credentials(metaclass=_CredentialsMeta):
    @classmethod
    def get_llm_api_key(cls, provider: str = "") -> str:
        """Returns active LLM API Key based on LLM_PROVIDER selection (prioritizing Gemini $0 tier if available)."""
        prov = (provider or GeneralSettings.LLM_PROVIDER).lower()
        if prov == "gemini":
            return cls.GEMINI_API_KEY or cls.OPENROUTER_API_KEY or cls.LLM_API_KEY or ""
        if prov == "deepseek":
            return cls.DEEPSEEK_API_KEY or cls.LLM_API_KEY or cls.OPENROUTER_API_KEY or ""
        if prov == "openrouter":
            return cls.OPENROUTER_API_KEY or cls.LLM_API_KEY or cls.GEMINI_API_KEY or cls.DEEPSEEK_API_KEY or ""
        return cls.GEMINI_API_KEY or cls.OPENROUTER_API_KEY or cls.LLM_API_KEY or cls.DEEPSEEK_API_KEY or ""

    @classmethod
    def validate(cls, require_llm: bool = True) -> list:
        """Returns a list of missing required credential names (empty = all OK)."""
        missing = []
        has_token = bool(_get_config_var("BIWENGER_TOKEN"))
        has_creds = bool(_get_config_var("BIWENGER_USERNAME") and _get_config_var("BIWENGER_PASSWORD"))

        if not (has_token or has_creds):
            missing.append("BIWENGER_TOKEN (o BIWENGER_USERNAME y BIWENGER_PASSWORD)")
        if require_llm and not cls.get_llm_api_key():
            missing.append("GEMINI_API_KEY o OPENROUTER_API_KEY (o LLM_API_KEY / DEEPSEEK_API_KEY)")
        return missing


PROVIDER_BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
}

DEFAULT_PROVIDER_MODELS = {
    "gemini": "gemini-3.8-flash",
    "openrouter": "openai/gpt-6-luna-pro",
    "deepseek": "deepseek-chat",
    "openai": "gpt-6-luna-pro",
}


class _GeneralSettingsMeta(type):
    """Metaclass allowing dynamic access to general and multi-LLM settings."""
    @property
    def LANGUAGE(cls) -> str:
        return _get_config_var("LANGUAGE", "es").lower()

    @property
    def SCORE_TYPE(cls) -> str:
        return _get_config_var("SCORE_TYPE", "5")

    @property
    def DRY_RUN(cls) -> bool:
        return _get_config_var("DRY_RUN", "false").lower() in ("1", "true", "yes")

    @property
    def LLM_PROVIDER(cls) -> str:
        explicit = _get_config_var("LLM_PROVIDER", "").lower()
        if explicit in PROVIDER_BASE_URLS:
            # If user explicitly set openrouter/gemini, honor it (so long as its key exists)
            if explicit == "gemini" and not Credentials.GEMINI_API_KEY and Credentials.OPENROUTER_API_KEY:
                return "openrouter"
            return explicit
        # Auto-prioritize Google AI Pro / AI Studio ($0 cost) when GEMINI_API_KEY is present
        if Credentials.GEMINI_API_KEY:
            return "gemini"
        if Credentials.OPENROUTER_API_KEY:
            return "openrouter"
        if Credentials.DEEPSEEK_API_KEY:
            return "deepseek"
        return "openrouter"

    @property
    def LLM_MODEL(cls) -> str:
        prov = cls.LLM_PROVIDER
        raw_model = _get_config_var("LLM_MODEL", "")
        # Upgrade legacy gpt-5.6-luna automatically to newer & 50% cheaper gpt-6-luna-pro
        if raw_model == "openai/gpt-5.6-luna":
            raw_model = "openai/gpt-6-luna-pro"
        if prov == "gemini":
            if raw_model and raw_model.startswith("gemini-"):
                return raw_model
            return _get_config_var("GEMINI_MODEL_DAILY", DEFAULT_PROVIDER_MODELS["gemini"])
        return raw_model or DEFAULT_PROVIDER_MODELS.get(prov, "openai/gpt-6-luna-pro")

    @property
    def LLM_MODEL_DAILY(cls) -> str:
        if cls.LLM_PROVIDER == "gemini":
            return _get_config_var("GEMINI_MODEL_DAILY", "gemini-3.8-flash")
        return _get_config_var("LLM_MODEL_DAILY", cls.LLM_MODEL)

    @property
    def LLM_MODEL_HIGH_STAKES(cls) -> str:
        if cls.LLM_PROVIDER == "gemini":
            return _get_config_var("GEMINI_MODEL_HIGH_STAKES", "gemini-4-argon")
        return _get_config_var("LLM_MODEL_HIGH_STAKES", "openai/gpt-6.1-terra")

    @property
    def LLM_FALLBACK_MODELS(cls) -> List[str]:
        raw = _get_config_var(
            "LLM_FALLBACK_MODELS",
            "openai/gpt-6-luna-pro,anthropic/claude-haiku-5.5,xiaomi/mimo-v2.6-flash,deepseek/deepseek-v4.1-flash",
        )
        return [m.strip() for m in raw.split(",") if m.strip()]

    @property
    def LLM_BASE_URL(cls) -> str:
        return PROVIDER_BASE_URLS.get(cls.LLM_PROVIDER, "https://openrouter.ai/api/v1")


class GeneralSettings(metaclass=_GeneralSettingsMeta):
    pass


class _StrategySettingsMeta(type):
    """Competitive strategy & Autopilot execution policies."""
    @property
    def AUTOPILOT_MODE(cls) -> str:
        """One of: DRY_RUN, SEMI_AUTO, FULL_AUTO."""
        mode = _get_config_var("AUTOPILOT_MODE", "SEMI_AUTO").upper()
        return mode if mode in ("DRY_RUN", "SEMI_AUTO", "FULL_AUTO") else "SEMI_AUTO"

    @property
    def FRIDAY_EMERGENCY_SOLVENCY(cls) -> bool:
        """Automatically accepts optimal Computer offers before kickoff if balance < 0 in SEMI_AUTO."""
        return _get_config_var("FRIDAY_EMERGENCY_SOLVENCY", "true").lower() in ("1", "true", "yes")

    @property
    def CLAUSE_POLICY(cls) -> str:
        """AGGRESSIVE (24/7 snipe + defensive shield) or DISABLED."""
        return _get_config_var("CLAUSE_POLICY", "AGGRESSIVE").upper()

    @property
    def PREVENTIVE_MARKET_LISTING(cls) -> bool:
        """Lists transferable players daily at 0€ cost to harvest +-5% Computer offers."""
        return _get_config_var("PREVENTIVE_MARKET_LISTING", "true").lower() in ("1", "true", "yes")

    @property
    def CREDIT_LIMIT_RATIO(cls) -> float:
        """Official Biwenger AZ Finance max debt allowance (25% of squad market value)."""
        try:
            return float(_get_config_var("CREDIT_LIMIT_RATIO", "0.25"))
        except ValueError:
            return 0.25

    @property
    def MAX_SQUAD_SIZE(cls) -> int:
        try:
            return int(_get_config_var("MAX_SQUAD_SIZE", "22"))
        except ValueError:
            return 22


class StrategySettings(metaclass=_StrategySettingsMeta):
    pass


class GoogleSheetsConfig:
    @classmethod
    @property
    def SHEET_ID(cls) -> str:
        return _get_config_var("GOOGLE_SHEET_ID")

    @classmethod
    @property
    def SERVICE_ACCOUNT_FILE(cls) -> str:
        return _get_config_var("GOOGLE_SERVICE_ACCOUNT_FILE", "./credentials_google.json")
