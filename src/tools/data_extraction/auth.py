import requests
import random
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pydantic import BaseModel

BASE_URL = "https://biwenger.as.com/"
LOGIN_URL = BASE_URL + 'api/v2/auth/login'
USER_INFO_URL = BASE_URL + 'api/v2/account'


class PlayerInfo(BaseModel):
    user_id: int
    user_name: str
    league_id: int
    league_name: str
    team_id: int
    team_name: str
    balance: int
    competition_slug: str


def get_random_user_agent() -> str:
    """Returns a random User-Agent string."""
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/118.0"
    ]
    return random.choice(user_agents)


def get_random_language() -> str:
    """Returns a random Accept-Language string."""
    return random.choice(["es", "ca"])


def random_headers() -> dict:
    """Returns a dictionary of random headers."""
    return {
        "User-Agent": get_random_user_agent(),
        "Accept": "application/json, text/plain, */*",
        "X-Version": "630",
        "X-Lang": get_random_language()
    }


class BiwengerAuth:
    """
    Handles authentication with Biwenger using a persistent session or direct Bearer Token.
    """
    def __init__(self, email: str = None, password: str = None, token: str = None):
        self.email = email
        self.password = password
        self.token = token
        self.session = requests.Session()
        self._setup_headers()
        self.player_info = None

    def _setup_headers(self):
        """Sets up the base headers for the session to mimic a real browser."""
        self.base_headers = random_headers()
        self.session.headers.update(self.base_headers)

    def login(self) -> str:
        """
        Performs the login process and returns the bearer token.
        """
        try:
            self.session.get(BASE_URL, timeout=15)
        except Exception:
            pass

        login_payload = {
            "email": self.email,
            "password": self.password
        }
    
        try:
            response = self.session.post(LOGIN_URL, json=login_payload, timeout=20)
            
            if response.status_code == 200:
                self.token = response.json().get("token")
                if self.token:
                    self.session.headers.update({"Authorization": f"Bearer {self.token}"})
                    return self.token
                else:
                    raise Exception("Login successful but no token found in response.")
            elif response.status_code == 429:
                raise Exception(
                    "Biwenger ha limitado temporalmente el endpoint de login (429 Rate Limit). "
                    "Usa tu BIWENGER_TOKEN directamente en Secrets para evitar el login por contraseña."
                )
            else:
                raise Exception(f"Login failed with status code: {response.status_code}. Response: {response.text}")

        except requests.RequestException as e:
            raise Exception(f"Error during login request: {e}")

    def get_session(self) -> requests.Session:
        """Returns the active requests.Session object."""
        return self.session

    def get_user_info(self):
        """
        Retrieves user information from Biwenger using the bearer token.
        """
        extra_headers = {
            "Authorization": f"Bearer {self.token}",
            "Referer": "https://biwenger.as.com/"
        }
        response = self.session.get(USER_INFO_URL, headers=extra_headers, timeout=20)
        
        if response.status_code == 200:
            response_json = response.json()
            user_id = response_json.get('data', {}).get('account', {}).get('id')
            user_name = response_json.get('data', {}).get('account', {}).get('name')

            leagues = response_json.get('data', {}).get('leagues', [])
            if not leagues:
                raise Exception("No leagues found in user account.")

            league_id = leagues[0].get('id')
            league_name = leagues[0].get('name')
            competition_slug = leagues[0].get('competition')
            team_id = leagues[0].get('user', {}).get('id')
            team_name = leagues[0].get('user', {}).get('name')
            balance = leagues[0].get('user', {}).get('balance', 0)

            self.player_info = PlayerInfo(
                user_id=user_id,
                user_name=user_name,
                league_id=league_id,
                league_name=league_name,
                team_id=team_id,
                team_name=team_name,
                balance=balance,
                competition_slug=competition_slug
            )
            return self.player_info
        elif response.status_code == 429:
            raise Exception("Rate limit (429) en /api/v2/account.")
        else:
            raise Exception(f"Failed to get user info: HTTP {response.status_code}")

    def run(self) -> requests.Session:
        """
        Runs authentication flow. If token exists, uses it directly; otherwise performs login.
        """
        if self.token:
            self.session.headers.update({"Authorization": f"Bearer {self.token}"})
            self.get_user_info()
            return self.session
        elif self.email and self.password:
            self.login()
            self.get_user_info()
            return self.session
        else:
            raise ValueError("Debes proporcionar BIWENGER_TOKEN o BIWENGER_USERNAME y BIWENGER_PASSWORD.")