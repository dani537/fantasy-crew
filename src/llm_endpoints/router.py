"""
Multi-Tier LLM Router (Google AI Pro $0 Priority + OpenRouter 2026 Failover)
============================================================================
Routes LLM requests across two operational tiers:
  - `daily`: Routine Mon-Thu coaching, news NLP classification, and daily checks.
             Prioritizes GEMINI_API_KEY (`gemini-3.8-flash` -> `gemini-2.5-flash`) at $0/mo,
             then OpenRouter (`openai/gpt-6-luna-pro` -> `anthropic/claude-haiku-5.5`
             -> `xiaomi/mimo-v2.6-flash` -> `deepseek/deepseek-v4.1-flash`).
  - `high_stakes`: Pre-deadline Friday solvency & complex squad restructuring.
             Prioritizes GEMINI_API_KEY (`gemini-4-argon` -> `gemini-3.8-flash`),
             then OpenRouter (`openai/gpt-6.1-terra` -> `openai/gpt-6-luna-pro`
             -> `anthropic/claude-haiku-5.5`).
"""

import os
from typing import List, Dict
from src.config import Credentials, GeneralSettings, PROVIDER_BASE_URLS
from src.llm_endpoints.client import LLMClient, MODEL_PRICING_PER_1M


class TieredLLMRouter:
    """Resolves ordered failover chains across Gemini API, OpenRouter, and DeepSeek."""

    @classmethod
    def resolve_candidates(cls, tier: str = "daily") -> List[Dict[str, str]]:
        candidates: List[Dict[str, str]] = []
        seen = set()

        def _add(provider: str, model: str, api_key: str):
            if not api_key or not model:
                return
            key = (provider, model)
            if key not in seen:
                seen.add(key)
                candidates.append({
                    "provider": provider,
                    "base_url": PROVIDER_BASE_URLS[provider],
                    "model": model,
                    "api_key": api_key,
                })

        gem_key = Credentials.GEMINI_API_KEY
        or_key = Credentials.OPENROUTER_API_KEY
        ds_key = Credentials.DEEPSEEK_API_KEY

        # 1. Priority #1: Google AI Pro / Google AI Studio ($0 out-of-pocket tier)
        if gem_key:
            if tier == "high_stakes":
                _add("gemini", GeneralSettings.LLM_MODEL_HIGH_STAKES if GeneralSettings.LLM_PROVIDER == "gemini" else "gemini-4-argon", gem_key)
                _add("gemini", "gemini-4-hydrogen", gem_key)
                _add("gemini", "gemini-3.8-flash", gem_key)
                _add("gemini", "gemini-2.5-flash", gem_key)
            else:
                _add("gemini", GeneralSettings.LLM_MODEL_DAILY if GeneralSettings.LLM_PROVIDER == "gemini" else "gemini-4-hydrogen", gem_key)
                _add("gemini", "gemini-3.8-flash", gem_key)
                _add("gemini", "gemini-3.5-flash-lite", gem_key)
                _add("gemini", "gemini-2.5-flash", gem_key)

        # 2. Priority #2: OpenRouter 2026 Ultra-Efficient Catalog
        if or_key:
            if tier == "high_stakes":
                hs_or = GeneralSettings.LLM_MODEL_HIGH_STAKES
                if not hs_or.startswith("gemini-"):
                    _add("openrouter", hs_or, or_key)
                else:
                    _add("openrouter", "google/gemini-4-hydrogen", or_key)
                    _add("openrouter", "openai/gpt-6.1-terra", or_key)
            else:
                daily_or = GeneralSettings.LLM_MODEL_DAILY
                if not daily_or.startswith("gemini-"):
                    _add("openrouter", daily_or, or_key)
                else:
                    _add("openrouter", "google/gemini-4-hydrogen", or_key)

            for fb_model in GeneralSettings.LLM_FALLBACK_MODELS:
                _add("openrouter", fb_model, or_key)

        # 3. Priority #3: Direct DeepSeek API fallback
        if ds_key:
            _add("deepseek", "deepseek-chat", ds_key)

        return candidates

    @classmethod
    def get_pydantic_ai_model(cls, tier: str = "daily") -> str:
        """
        Configures environment variables and returns the Pydantic AI model string
        matching the highest-priority available candidate for the requested tier.
        """
        candidates = cls.resolve_candidates(tier=tier)
        if not candidates:
            return "openrouter:openai/gpt-6-luna-pro"

        top = candidates[0]
        prov = top["provider"]
        model = top["model"]
        if prov == "gemini":
            os.environ["GEMINI_API_KEY"] = top["api_key"]
            os.environ["GOOGLE_API_KEY"] = top["api_key"]
            return f"google-gla:{model}"
        if prov == "openrouter":
            os.environ["OPENROUTER_API_KEY"] = top["api_key"]
            return model if model.startswith("openrouter:") else f"openrouter:{model}"
        if prov == "deepseek":
            os.environ["DEEPSEEK_API_KEY"] = top["api_key"]
            return f"deepseek:{model}"
        return f"openrouter:{model}"

    @classmethod
    def get_pydantic_ai_model_object(cls, tier: str = "daily"):
        """
        Builds a native Pydantic AI v2 `Model` (or `FallbackModel` chain) compatible
        with custom Gemini / OpenRouter / DeepSeek model names (including `gemini-4-hydrogen`).
        Falls back to `get_pydantic_ai_model(tier)` if model classes are unavailable.
        """
        candidates = cls.resolve_candidates(tier=tier)
        if not candidates:
            return cls.get_pydantic_ai_model(tier=tier)

        try:
            from pydantic_ai.models.google import GoogleModel
            from pydantic_ai.providers.google import GoogleProvider
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider
            from pydantic_ai.models.fallback import FallbackModel
        except Exception:
            return cls.get_pydantic_ai_model(tier=tier)

        models = []
        for cand in candidates:
            prov = cand["provider"]
            model_name = cand["model"]
            api_key = cand["api_key"]
            base_url = cand["base_url"]
            if prov == "gemini":
                models.append(GoogleModel(model_name, provider=GoogleProvider(api_key=api_key)))
            else:
                models.append(OpenAIChatModel(model_name, provider=OpenAIProvider(base_url=base_url, api_key=api_key)))

        if len(models) == 1:
            return models[0]
        return FallbackModel(*models)

    @classmethod
    def get_client(cls, tier: str = "daily") -> LLMClient:
        """Returns an LLMClient bound to the requested tier with automatic failover."""
        return LLMClient(tier=tier, enable_failover=True)

    @classmethod
    def summary_table(cls) -> List[Dict[str, object]]:
        """Returns active routing chains and pricing metadata for UI / CLI inspection."""
        rows = []
        for tier in ("daily", "high_stakes"):
            for idx, c in enumerate(cls.resolve_candidates(tier=tier), start=1):
                in_p, out_p = MODEL_PRICING_PER_1M.get(c["model"], (0.15, 0.60))
                rows.append({
                    "tier": tier,
                    "priority": idx,
                    "provider": c["provider"],
                    "model": c["model"],
                    "input_usd_1m": in_p,
                    "output_usd_1m": out_p,
                })
        return rows
