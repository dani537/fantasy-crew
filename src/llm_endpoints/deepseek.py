from openai import OpenAI
from src.config import Credentials

class DeepseekClient:
    def __init__(self):
        self.api_key = Credentials.DEEPSEEK_API_KEY
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY not found in environment variables")
        
        self.client = OpenAI(
            api_key=self.api_key, 
            base_url="https://api.deepseek.com"
        )

    def generate_content(self, prompt: str, system_prompt: str = "You are a helpful assistant", model: str = "deepseek-chat"):
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                stream=False
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error generating content: {e}")
            return None

if __name__ == "__main__":
    # Simple test
    deepseek = DeepseekClient()
    response = deepseek.generate_content("Explain how AI works in a few words")
    print("Response from Deepseek:")
    print(response)
