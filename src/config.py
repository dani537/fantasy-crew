"""
Configuration and Credentials Manager
=====================================
Centralized, dynamic loading of environment variables and Streamlit Cloud secrets.
"""

import os
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
    def get_llm_api_key(cls) -> str:
        """Returns active LLM API Key based on LLM_PROVIDER selection."""
        provider = _get_config_var("LLM_PROVIDER", "openrouter").lower()
        if provider == "deepseek":
            return cls.DEEPSEEK_API_KEY or cls.LLM_API_KEY or cls.OPENROUTER_API_KEY or ""
        elif provider == "openrouter":
            return cls.OPENROUTER_API_KEY or cls.LLM_API_KEY or cls.DEEPSEEK_API_KEY or ""
        return cls.LLM_API_KEY or cls.OPENROUTER_API_KEY or cls.DEEPSEEK_API_KEY or ""

    @classmethod
    def validate(cls) -> list:
        """Returns a list of missing required credential names (empty = all OK)."""
        missing = []
        has_token = bool(_get_config_var("BIWENGER_TOKEN"))
        has_creds = bool(_get_config_var("BIWENGER_USERNAME") and _get_config_var("BIWENGER_PASSWORD"))
        
        if not (has_token or has_creds):
            missing.append("BIWENGER_TOKEN (o BIWENGER_USERNAME y BIWENGER_PASSWORD)")
        if not cls.get_llm_api_key():
            missing.append("OPENROUTER_API_KEY (or LLM_API_KEY / DEEPSEEK_API_KEY)")
        return missing


PROVIDER_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
}

DEFAULT_PROVIDER_MODELS = {
    "openrouter": "openai/gpt-5.6-luna",
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o-mini",
}


class _GeneralSettingsMeta(type):
    """Metaclass allowing dynamic access to general settings."""
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
        return _get_config_var("LLM_PROVIDER", "openrouter").lower()

    @property
    def LLM_MODEL(cls) -> str:
        return _get_config_var("LLM_MODEL", "openai/gpt-5.6-luna")

    @property
    def LLM_BASE_URL(cls) -> str:
        return PROVIDER_BASE_URLS.get(cls.LLM_PROVIDER, "https://openrouter.ai/api/v1")


class GeneralSettings(metaclass=_GeneralSettingsMeta):
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
