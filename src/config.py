"""
Configuration and Credentials Manager (Fantasy Champion v3 — Multi-User Plug & Play)
====================================================================================
Centralized, zero-hardcoding configuration loader supporting:
- Environment variables (`.env` / GitHub Actions Secrets & Variables)
- Streamlit Cloud `st.secrets`
- Auto-discovery of Biwenger User/League IDs & Official League Rules via API
- Multi-Tier LLM Routing (Google AI Pro $0 tier prioritized + OpenRouter failover)
- Dual Notifications (HTML Email SMTP + Bidirectional Interactive Telegram Bot)
- Granular `SEMI_AUTO` Autonomy Governance
"""

import os
import json
from typing import List, Optional, Dict, Any
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
    for k in (key, key.upper(), key.lower()):
        val = os.getenv(k)
        if val is not None and str(val).strip() != "":
            return str(val).strip()

    try:
        import streamlit as st
        if hasattr(st, "secrets") and st.secrets is not None:
            for k in (key, key.upper(), key.lower()):
                if k in st.secrets:
                    val = st.secrets[k]
                    if val is not None and str(val).strip() != "":
                        return str(val).strip()

            for _, sec_dict in st.secrets.items():
                if isinstance(sec_dict, dict) or hasattr(sec_dict, "items"):
                    for sk, sv in sec_dict.items():
                        if sk.lower() == key.lower():
                            if sv is not None and str(sv).strip() != "":
                                return str(sv).strip()
    except Exception:
        pass

    return default


def resolve_active_user_identity() -> Dict[str, Any]:
    """
    Resolves active manager & league identity dynamically without any hardcoded names/IDs:
    1. Explicit `.env` overrides (`BIWENGER_USER_ID`, `BIWENGER_LEAGUE_ID`, `BIWENGER_TEAM_NAME`, `BIWENGER_LEAGUE_NAME`)
    2. Cached live profile (`./data/raw/live_league_profile.json`)
    3. Extracted `./data/user_info.csv`
    """
    info: Dict[str, Any] = {
        "user_id": 0,
        "team_id": 0,
        "team_name": "",
        "league_id": 0,
        "league_name": "",
        "score_id": int(_get_config_var("SCORE_TYPE", "5") or 5),
        "balance": None,
    }

    profile_path = "./data/raw/live_league_profile.json"
    if os.path.exists(profile_path):
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                prof = json.load(f)
            info.update({k: v for k, v in prof.items() if v not in (None, "", 0)})
        except Exception:
            pass

    user_csv = "./data/user_info.csv"
    if os.path.exists(user_csv):
        try:
            import pandas as pd
            df_u = pd.read_csv(user_csv)
            if not df_u.empty:
                r = df_u.iloc[0]
                if not info["team_id"] and pd.notna(r.get("team_id")):
                    info["team_id"] = int(r["team_id"])
                if not info["user_id"] and pd.notna(r.get("user_id")):
                    info["user_id"] = int(r["user_id"])
                if not info["team_name"] and pd.notna(r.get("team_name")):
                    info["team_name"] = str(r["team_name"]).strip()
                if not info["league_id"] and pd.notna(r.get("league_id")):
                    info["league_id"] = int(r["league_id"])
                if not info["league_name"] and pd.notna(r.get("league_name")):
                    info["league_name"] = str(r["league_name"]).strip()
                if info["balance"] is None and pd.notna(r.get("balance")):
                    info["balance"] = float(r["balance"])
        except Exception:
            pass

    # Explicit environment overrides take highest precedence
    env_lid = _get_config_var("BIWENGER_LEAGUE_ID")
    env_uid = _get_config_var("BIWENGER_USER_ID")
    env_tname = _get_config_var("BIWENGER_TEAM_NAME")
    env_lname = _get_config_var("BIWENGER_LEAGUE_NAME")

    if env_lid.isdigit():
        info["league_id"] = int(env_lid)
    if env_uid.isdigit():
        info["team_id"] = int(env_uid)
    if env_tname:
        info["team_name"] = env_tname
    if env_lname:
        info["league_name"] = env_lname

    if not info["team_name"]:
        info["team_name"] = "Mi Equipo"
    if not info["league_name"]:
        info["league_name"] = "Liga Biwenger"

    return info


class _CredentialsMeta(type):
    """Metaclass allowing class-level dynamic property access."""
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
    def BIWENGER_LEAGUE_ID(cls) -> Optional[int]:
        v = _get_config_var("BIWENGER_LEAGUE_ID")
        return int(v) if v.isdigit() else None

    @property
    def BIWENGER_USER_ID(cls) -> Optional[int]:
        v = _get_config_var("BIWENGER_USER_ID")
        return int(v) if v.isdigit() else None

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

    @property
    def GMAIL_ADDRESS(cls) -> str:
        return _get_config_var("GMAIL_ADDRESS") or _get_config_var("GMAIL_ADRESS")

    @property
    def GMAIL_PASSWORD(cls) -> str:
        return _get_config_var("GMAIL_PASSWORD")

    @property
    def NOTIFY_EMAIL_TO(cls) -> str:
        return _get_config_var("NOTIFY_EMAIL_TO") or cls.GMAIL_ADDRESS or cls.BIWENGER_USERNAME

    @property
    def TELEGRAM_BOT_TOKEN(cls) -> str:
        return _get_config_var("TELEGRAM_BOT_TOKEN")

    @property
    def TELEGRAM_ALLOWED_CHAT_ID(cls) -> str:
        return _get_config_var("TELEGRAM_ALLOWED_CHAT_ID") or _get_config_var("TELEGRAM_CHAT_ID")


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
    def TIMEZONE(cls) -> str:
        return _get_config_var("TIMEZONE", "Europe/Madrid")

    @property
    def DRY_RUN(cls) -> bool:
        return _get_config_var("DRY_RUN", "false").lower() in ("1", "true", "yes")

    @property
    def LLM_PROVIDER(cls) -> str:
        explicit = _get_config_var("LLM_PROVIDER", "").lower()
        if explicit in PROVIDER_BASE_URLS:
            if explicit == "gemini" and not Credentials.GEMINI_API_KEY and Credentials.OPENROUTER_API_KEY:
                return "openrouter"
            return explicit
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
    """Competitive strategy & Autopilot v3 execution policies (100% parameterizable via .env)."""
    @property
    def AUTOPILOT_MODE(cls) -> str:
        """One of: DRY_RUN, SEMI_AUTO, FULL_AUTO (default: FULL_AUTO)."""
        mode = _get_config_var("AUTOPILOT_MODE", "FULL_AUTO").upper()
        return mode if mode in ("DRY_RUN", "SEMI_AUTO", "FULL_AUTO") else "FULL_AUTO"

    @property
    def RISK_PROFILE(cls) -> str:
        """CONSERVATIVE, BALANCED (default), or AGGRESSIVE."""
        rp = _get_config_var("RISK_PROFILE", "BALANCED").upper()
        return rp if rp in ("CONSERVATIVE", "BALANCED", "AGGRESSIVE") else "BALANCED"

    @property
    def AUTONOMOUS_BIDS_IN_SEMI_AUTO(cls) -> bool:
        """In SEMI_AUTO, autonomously place optimal market bids while notifying user via Email/Telegram."""
        return _get_config_var("AUTONOMOUS_BIDS_IN_SEMI_AUTO", "true").lower() in ("1", "true", "yes")

    @property
    def AUTONOMOUS_ROUTINE_SALES_IN_SEMI_AUTO(cls) -> bool:
        """In SEMI_AUTO, autonomously accept offers on minor flips/patches while gating important sales for human OK."""
        return _get_config_var("AUTONOMOUS_ROUTINE_SALES_IN_SEMI_AUTO", "true").lower() in ("1", "true", "yes")

    @property
    def IMPORTANT_SALE_MIN_EUR(cls) -> int:
        """Sales above this price (or FRANCHISE_CORE / STRUCTURAL_STARTER roles) require human confirmation in SEMI_AUTO."""
        try:
            return int(float(_get_config_var("IMPORTANT_SALE_MIN_EUR", "4000000")))
        except ValueError:
            return 4_000_000

    @property
    def FRIDAY_EMERGENCY_SOLVENCY(cls) -> bool:
        """Automatically clears negative balance pre-kickoff using non-core collateral in SEMI_AUTO."""
        return _get_config_var("PRE_KICKOFF_EMERGENCY_SOLVENCY", _get_config_var("FRIDAY_EMERGENCY_SOLVENCY", "true")).lower() in ("1", "true", "yes")

    @property
    def CLAUSE_POLICY(cls) -> str:
        """SURGICAL_SCARCITY (recommended), AGGRESSIVE, or DISABLED."""
        pol = _get_config_var("CLAUSE_POLICY", "SURGICAL_SCARCITY").upper()
        return pol if pol in ("SURGICAL_SCARCITY", "AGGRESSIVE", "DISABLED") else "SURGICAL_SCARCITY"

    @property
    def PREVENTIVE_MARKET_LISTING(cls) -> bool:
        """Lists up to `marketMaxUserSales` transferable non-core players daily at +5% VM."""
        return _get_config_var("PREVENTIVE_MARKET_LISTING", "true").lower() in ("1", "true", "yes")

    @property
    def CREDIT_LIMIT_RATIO(cls) -> float:
        """Optional override for league debt allowance (defaults to API auto-discovered rule or 0.25)."""
        raw = _get_config_var("CREDIT_LIMIT_RATIO", "")
        if raw:
            try:
                return float(raw)
            except ValueError:
                pass
        return 0.25

    @property
    def MAX_SQUAD_SIZE(cls) -> int:
        try:
            return int(_get_config_var("MAX_SQUAD_SIZE", "22"))
        except ValueError:
            return 22

    @property
    def INITIAL_LEAGUE_BUDGET_EUR(cls) -> int:
        """Default initial total budget (Cash + Day-1 Squad Value) for Biwenger leagues (standard = 40M €)."""
        try:
            return int(float(_get_config_var("INITIAL_LEAGUE_BUDGET_EUR", "40000000")))
        except ValueError:
            return 40_000_000


class StrategySettings(metaclass=_StrategySettingsMeta):
    pass


class _GoogleSheetsConfigMeta(type):
    @property
    def SHEET_ID(cls) -> str:
        return _get_config_var("GOOGLE_SHEET_ID") or _get_config_var("GOOGLE_SHEET_ID_MARKET")

    @property
    def SERVICE_ACCOUNT_FILE(cls) -> str:
        return _get_config_var("GOOGLE_SERVICE_ACCOUNT_FILE", "./credentials_google.json")

    @property
    def SERVICE_ACCOUNT_JSON(cls) -> str:
        return _get_config_var("GOOGLE_SERVICE_ACCOUNT_JSON")

    @property
    def ENABLED(cls) -> bool:
        mode = _get_config_var("GOOGLE_SHEETS_ENABLED", "auto").lower()
        if mode in ("0", "false", "no", "off"):
            return False
        has_creds = bool(cls.SERVICE_ACCOUNT_JSON or (cls.SERVICE_ACCOUNT_FILE and os.path.exists(cls.SERVICE_ACCOUNT_FILE)))
        return bool(cls.SHEET_ID and has_creds)


class GoogleSheetsConfig(metaclass=_GoogleSheetsConfigMeta):
    pass
