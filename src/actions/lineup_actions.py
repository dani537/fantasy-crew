import requests
from typing import List

class LineupActions:
    """
    Handles all operations regarding lineups and formations.
    Requires an authenticated requests.Session from BiwengerAuth.
    """
    def __init__(self, session: requests.Session):
        self.session = session
        self.base_url = "https://biwenger.as.com/api/v2"

    def set_lineup(self, formation: str, player_ids: List[int]) -> bool:
        """
        Updates the starting eleven.
        
        :param formation: String representing the formation (e.g., "4-4-2", "3-4-3", "4-3-3")
        :param player_ids: List of player IDs to line up. Normally exactly 11 players.
                           Order MUST be: GK, then DFs, then MFs, then FWs matching the formation.
        """
        payload = {
            "lineup": {
                "type": formation,
                "playersID": player_ids,
                "reservesID": []
            }
        }
        url = f"{self.base_url}/user?fields=*,lineup(date)"
        response = self.session.put(url, json=payload)
        
        if response.status_code == 200:
            print(f"✅ Lineup updated successfully using formation {formation}")
            return True
        else:
            print(f"❌ Failed to update lineup. Status: {response.status_code}, Response: {response.text}")
            return False
