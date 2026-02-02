import requests
import random
from dataclasses import dataclass

BASE_URL = "https://biwenger.as.com/"
LOGIN_URL = BASE_URL + 'api/v2/auth/login'
USER_INFO_URL = BASE_URL + 'api/v2/account'


# NOTA: Aquest codi només està preparat per quan l'usuari té una sola lliga (com és el meu cas), resta pendent
#       per a gestionar múltiples ligues a get_user_info, ara mateix només retorna la primera lliga

@dataclass
class PlayerInfo:
    user_id: int
    user_name: str
    league_id: int
    league_name: str
    team_id: int
    team_name: str
    balance: int

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
    languages = [
        "es",
        "ca"
    ]
    return random.choice(languages)


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
    Handles authentication with Biwenger using a persistent session.
    """
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.session = requests.Session()
        self._setup_headers()
        self.token = None
        self.player_info = None

    def _setup_headers(self):
        """Sets up the base headers for the session to mimic a real browser."""
        self.base_headers = random_headers()
        self.session.headers.update(self.base_headers)

    def login(self) -> str:
        """
        Performs the login process and returns the bearer token.
        
        Returns:
            str: The bearer token if login is successful.
            
        Raises:
            Exception: If login fails or token is not found.
        """
        # 1. Visit home to initialize cookies (optional but recommended)
        try:
            self.session.get(BASE_URL)
        except requests.RequestException as e:
            print(f"Warning: Error connecting to home page: {e}")

        # 2. Login
        login_payload = {
            "email": self.email,
            "password": self.password
        }
    
        try:
            response = self.session.post(LOGIN_URL, json=login_payload)
            
            if response.status_code == 200:
                self.token = response.json().get("token")
                if self.token:
                    # Update session headers with the token
                    self.session.headers.update({"Authorization": f"Bearer {self.token}"})
                    return self.token
                else:
                    raise Exception("Login successful but no token found in response.")
            else:
                raise Exception(f"Login failed with status code: {response.status_code}. Response: {response.text}")

        except requests.RequestException as e:
            raise Exception(f"Error during login request: {e}")

    def get_session(self) -> requests.Session:
        """Returns the active requests.Session object."""
        return self.session

    def get_user_info(self):
        # Añadimos el token y el referer al header base
        extra_headers = {
            "Authorization": f"Bearer {self.token}",
            "Referer": "https://biwenger.as.com/"
        }
        response = self.session.get(USER_INFO_URL, headers=extra_headers)
        
        if response.status_code == 200:
            response_json = response.json()
            user_id = response_json.get('data').get('account').get('id')
            user_name = response_json.get('data').get('account').get('name')

            league_id = response_json.get('data').get('leagues')[0].get('id')
            league_name = response_json.get('data').get('leagues')[0].get('name')
            team_id = response_json.get('data').get('leagues')[0].get('user').get('id')
            team_name = response_json.get('data').get('leagues')[0].get('user').get('name')
            balance = response_json.get('data').get('leagues')[0].get('user').get('balance')

            self.player_info = PlayerInfo(
                user_id=user_id,
                user_name=user_name,
                league_id=league_id,
                league_name=league_name,
                team_id=team_id,
                team_name=team_name,
                balance=balance
            )

            return self.player_info

        else:
            raise Exception(f"Failed to get user info: {response.text}")

    def run(self):
        """
        Ejecuta login y obtiene la información del usuario.
        
        Returns:
            dict: Diccionario con 'token' y 'player_info'
        """
        token = self.login()
        print(f"Token obtenido: {token[:20]}...")
        player_info = self.get_user_info()
        print(f"Usuario: {player_info.user_name}")
        print(f"Liga: {player_info.league_name}")
        print(f"Equipo: {player_info.team_name}")
        print(f"Balance: {player_info.balance:,}€")