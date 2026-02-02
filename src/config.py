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