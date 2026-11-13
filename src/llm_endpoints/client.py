"""
Provider-Agnostic OpenAI-Compatible LLM Client & Multi-Tier Failover Integration
================================================================================
Supports Google AI Studio / Google AI Pro (Gemini OpenAI-compatible API at $0 cost),
OpenRouter (gpt-6-luna-pro, claude-haiku-5.5, xiaomi/mimo-v2.6-flash, gpt-6.1-terra),
DeepSeek direct, or any OpenAI-compatible endpoint.
"""

import requests
from typing import Optional, Dict, Any, List
from src.config import Credentials, GeneralSettings


# Verified October 2026 pricing (USD per 1M tokens) for live cost telemetry
MODEL_PRICING_PER_1M: Dict[str, tuple] = {
    # Google AI Studio Free Tier / Google AI Pro ($10/mo Cloud Credit included -> $0 out of pocket)
    "gemini-4-hydrogen": (0.0, 0.0),
    "gemini-3.8-flash": (0.0, 0.0),
    "gemini-3.5-flash-lite": (0.0, 0.0),
    "gemini-2.5-flash": (0.0, 0.0),
    "gemini-2.5-flash-lite": (0.0, 0.0),
    "gemini-4-argon": (0.0, 0.0),
    # OpenRouter Verified Catalog (2026-10-30 / 2026-11-12)
    "google/gemini-4-hydrogen": (0.40, 1.80),
    "openai/gpt-6-luna-pro": (0.10, 0.50),
    "openai/gpt-6-luna": (0.10, 0.50),
    "openai/gpt-5.6-luna": (0.20, 1.20),
    "openai/gpt-6.1-terra": (1.00, 5.00),
    "openai/gpt-6.1-terra-pro": (1.00, 5.00),
    "anthropic/claude-haiku-5.5": (0.10, 0.50),
    "xiaomi/mimo-v2.6-flash": (0.14, 0.28),
    "xiaomi/mimo-v2.6-pro": (0.435, 0.87),
    "deepseek/deepseek-v4.1-flash": (0.045, 0.50),
    "deepseek/deepseek-v4-flash": (0.091, 0.182),
    "deepseek-chat": (0.257, 1.029),
    "google/gemini-3.8-flash": (0.825, 4.125),
    "google/gemini-2.5-flash-lite": (0.10, 0.40),
}


def estimate_call_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Computes exact estimated USD cost for a single LLM call."""
    in_rate, out_rate = MODEL_PRICING_PER_1M.get(model, (0.15, 0.60))
    return (prompt_tokens * in_rate + completion_tokens * out_rate) / 1_000_000.0


class LLMClient:
    """
    Provider-agnostic OpenAI-compatible LLM client with token & cost telemetry
    and optional multi-provider failover via TieredLLMRouter.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
        tier: str = "daily",
        enable_failover: bool = True,
    ):
        self.tier = tier
        self.enable_failover = enable_failover
        self._explicit_provider = bool(api_key or base_url or default_model)
        self.api_key = api_key or Credentials.get_llm_api_key()
        if not self.api_key:
            raise ValueError(
                "No LLM API Key found. Set GEMINI_API_KEY or OPENROUTER_API_KEY in .env"
            )

        self.base_url = (base_url or GeneralSettings.LLM_BASE_URL).rstrip("/")
        self.default_model = default_model or (
            GeneralSettings.LLM_MODEL_HIGH_STAKES if tier == "high_stakes" else GeneralSettings.LLM_MODEL_DAILY
        )
        self._usage: Dict[str, Any] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
            "calls": 0,
            "last_model": None,
        }

    def _single_completion(
        self,
        api_key: str,
        base_url: str,
        target_model: str,
        messages: List[dict],
        temperature: float,
        max_tokens: int,
        timeout: int,
        response_format: Optional[dict],
    ) -> dict:
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        if "openrouter.ai" in base_url:
            headers["HTTP-Referer"] = "https://github.com/dani537/fantasy-champion"
            headers["X-Title"] = "Fantasy Champion"

        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if response_format is not None:
            if response_format:  # allow passing False/empty dict to disable JSON mode
                payload["response_format"] = response_format
        elif any(p in base_url for p in ("openrouter.ai", "deepseek", "openai")):
            payload["response_format"] = {"type": "json_object"}

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if response.status_code == 200:
                resp_json = response.json()
                choices = resp_json.get("choices", [])
                if not choices:
                    return {"success": False, "error": "Empty choices in API response", "status_code": 200}
                msg = choices[0].get("message", {})
                content = (
                    msg.get("content")
                    or msg.get("reasoning_content")
                    or msg.get("reasoning")
                    or ""
                )
                usage = resp_json.get("usage", {}) or {}
                p_tok = int(usage.get("prompt_tokens", 0) or 0)
                c_tok = int(usage.get("completion_tokens", 0) or 0)
                t_tok = int(usage.get("total_tokens", p_tok + c_tok) or (p_tok + c_tok))
                cost = estimate_call_cost_usd(target_model, p_tok, c_tok)

                self._usage["prompt_tokens"] += p_tok
                self._usage["completion_tokens"] += c_tok
                self._usage["total_tokens"] += t_tok
                self._usage["estimated_cost_usd"] += cost
                self._usage["calls"] += 1
                self._usage["last_model"] = target_model

                return {
                    "success": True,
                    "content": content,
                    "usage": {**usage, "estimated_cost_usd": cost},
                    "model_used": target_model,
                }
            return {
                "success": False,
                "status_code": response.status_code,
                "error": f"API Error (Status {response.status_code}): {response.text[:400]}",
            }
        except Exception as e:
            return {
                "success": False,
                "status_code": 0,
                "error": f"Connection error: {str(e)}",
            }

    def chat_completion(
        self,
        messages: list,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 16000,
        timeout: int = 90,
        response_format: Optional[dict] = None,
    ) -> dict:
        """
        Executes a chat completion call with transparent multi-provider failover
        when enable_failover=True and no single model override was pinned.
        """
        if model or self._explicit_provider or not self.enable_failover:
            return self._single_completion(
                api_key=self.api_key,
                base_url=self.base_url,
                target_model=model or self.default_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                response_format=response_format,
            )

        from src.llm_endpoints.router import TieredLLMRouter
        candidates = TieredLLMRouter.resolve_candidates(tier=self.tier)
        last_err = {"success": False, "error": "No LLM candidates configured"}
        for cand in candidates:
            res = self._single_completion(
                api_key=cand["api_key"],
                base_url=cand["base_url"],
                target_model=cand["model"],
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                response_format=response_format,
            )
            if res["success"]:
                return res
            last_err = res
        return last_err

    def generate_content(
        self,
        prompt: str,
        system_prompt: str = "You are a helpful assistant",
        model: Optional[str] = None,
        temperature: float = 0.7,
        response_format: Optional[dict] = None,
    ) -> Optional[str]:
        """High-level wrapper compatible with CoachAnalytic and legacy callers."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        res = self.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            response_format=response_format,
        )
        if res["success"]:
            return res["content"]
        print(f"❌ Error generating content with model '{model or self.default_model}': {res['error']}")
        return None

    def call(
        self,
        prompt: str,
        system_role: str = "You are a helpful assistant",
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
    ) -> Optional[str]:
        """Alias for generate_content."""
        effective_sys = system_prompt if system_prompt is not None else system_role
        return self.generate_content(
            prompt=prompt,
            system_prompt=effective_sys,
            model=model,
            temperature=temperature,
        )

    def get_token_usage(self) -> dict:
        """Returns the accumulated token & USD cost telemetry."""
        return self._usage.copy()


def get_llm_client(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    default_model: Optional[str] = None,
    tier: str = "daily",
) -> LLMClient:
    """Factory helper to obtain an instance of LLMClient."""
    return LLMClient(api_key=api_key, base_url=base_url, default_model=default_model, tier=tier)


OpenAICompatibleClient = LLMClient
