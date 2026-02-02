from google import genai

# Config
from src.config import Credentials

class GeminiClient:
    def __init__(self):
        api_key = Credentials.GEMINI_API_KEY
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found in environment variables")
        self.client = genai.Client(api_key=api_key)

    def generate_content(self, prompt: str, model: str = "gemini-2.0-flash"):
        try:
            response = self.client.models.generate_content(
                model=model,
                contents=prompt,
            )
            return response.text
        except Exception as e:
            print(f"Error generating content: {e}")
            return None

    def list_models(self):
        try:
            return list(self.client.models.list())
        except Exception as e:
            print(f"Error listing models: {e}")
            return []

if __name__ == "__main__":
    # Simple test
    gemini = GeminiClient()
    response = gemini.generate_content("Explain how AI works in a few words")
    print(response)
