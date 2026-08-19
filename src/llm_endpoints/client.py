import requests
from src.config import Credentials, GeneralSettings


class LLMClient:
    """
    Provider-agnostic OpenAI-compatible LLM client.
    Supports OpenRouter, DeepSeek direct, OpenAI, Groq, Ollama, or any OpenAI-compatible provider.
    Inspired by FollowFit's LLM architecture.
    """

    def __init__(self, api_key: str = None, base_url: str = None, default_model: str = None):
        self.api_key = api_key or Credentials.get_llm_api_key()
        if not self.api_key:
            raise ValueError(
                "No LLM API Key found. Please set OPENROUTER_API_KEY, LLM_API_KEY, or DEEPSEEK_API_KEY in .env"
            )

        self.base_url = (base_url or GeneralSettings.LLM_BASE_URL).rstrip("/")
        self.default_model = default_model or GeneralSettings.LLM_MODEL

    def chat_completion(
        self,
        messages: list,
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 16000,
        timeout: int = 90,
        response_format: dict = None,
    ) -> dict:
        """
        Executes a chat completion call against an OpenAI-compatible endpoint.
        Returns a dict with {"success": True/False, "content": str, "usage": dict, "error": str}
        """
        target_model = model or self.default_model
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        # OpenRouter-recommended headers
        if "openrouter.ai" in self.base_url:
            headers["HTTP-Referer"] = "https://github.com/dani537/fantasy-crew"
            headers["X-Title"] = "Biwenger Agent"

        payload = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Enable JSON mode by default if not explicitly disabled or overridden
        if response_format is not None:
            payload["response_format"] = response_format
        elif "openrouter.ai" in self.base_url or "deepseek" in self.base_url or "openai" in self.base_url:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if response.status_code == 200:
                resp_json = response.json()
                choices = resp_json.get("choices", [])
                if not choices:
                    return {"success": False, "error": "Empty choices in API response"}
                msg = choices[0].get("message", {})
                # Content extraction with fallback for reasoning models (DeepSeek R1, etc.)
                content = (
                    msg.get("content")
                    or msg.get("reasoning_content")
                    or msg.get("reasoning")
                    or ""
                )
                return {
                    "success": True,
                    "content": content,
                    "usage": resp_json.get("usage", {}),
                }
            else:
                return {
                    "success": False,
                    "error": f"API Error (Status {response.status_code}): {response.text}",
                }
        except Exception as e:
            return {
                "success": False,
                "error": f"Connection error: {str(e)}",
            }

    def generate_content(
        self,
        prompt: str,
        system_prompt: str = "You are a helpful assistant",
        model: str = None,
        temperature: float = 0.7,
    ) -> str:
        """
        High-level wrapper compatible with the legacy agent interface.
        Returns response string on success, or None on failure.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        res = self.chat_completion(
            messages=messages, model=model, temperature=temperature
        )
        if res["success"]:
            return res["content"]
        else:
            print(
                f"❌ Error generating content with model '{model or self.default_model}': {res['error']}"
            )
            return None

    def call(
        self,
        prompt: str,
        system_role: str = "You are a helpful assistant",
        model: str = None,
        temperature: float = 0.7,
    ) -> str:
        """Alias for generate_content."""
        return self.generate_content(
            prompt=prompt,
            system_prompt=system_role,
            model=model,
            temperature=temperature
        )


# Alias for compatibility with FollowFit client naming
OpenAICompatibleClient = LLMClient
