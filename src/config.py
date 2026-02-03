import os
from dotenv import load_dotenv

load_dotenv()

class Credentials:
    BIWENGER_USERNAME = os.getenv("BIWENGER_USERNAME")
    BIWENGER_PASSWORD = os.getenv("BIWENGER_PASSWORD")
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

class GeneralSettings:
    LANGUAGE = os.getenv("LANGUAGE")
    # Biwenger score ID (1: AS, 2: SofaScore, 5: Media, etc.)
    # Priority: SCORE_TYPE (common in .env) > SCORE_ID (alternative) > '1' (AS default)
    SCORE_TYPE = os.getenv("SCORE_TYPE")