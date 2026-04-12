import requests

class MarketActions:
    """
    Handles all active market operations (bidding, selling, accepting offers).
    Requires an authenticated requests.Session from BiwengerAuth.
    """
    def __init__(self, session: requests.Session):
        self.session = session
        self.base_url = "https://biwenger.as.com/api/v2"

    def place_offer(self, amount: int, player_id: int, to_user_id: int = None) -> bool:
        """
        Places a bid on a player. If 'to_user_id' is None, it bids to the market (computer).
        If the amount is higher or equal to the clause, it executes a 'clausulazo' immediately.
        """
        payload = {
            "amount": amount,
            "requestedPlayers": [player_id],
            "to": to_user_id,
            "type": "purchase"
        }
        url = f"{self.base_url}/offers/"
        response = self.session.post(url, json=payload)
        
        if response.status_code == 200:
            print(f"✅ Offer of {amount}€ placed successfully for player {player_id}")
            return True
        else:
            print(f"❌ Failed to place offer. Status: {response.status_code}, Response: {response.text}")
            return False

    def accept_offer(self, offer_id: int) -> bool:
        """
        Accepts a received offer given its ID.
        """
        payload = {
            "status": "accepted"
        }
        url = f"{self.base_url}/offers/{offer_id}"
        response = self.session.put(url, json=payload)
        
        if response.status_code == 200:
            print(f"✅ Offer {offer_id} accepted successfully.")
            return True
        else:
            print(f"❌ Failed to accept offer. Status: {response.status_code}, Response: {response.text}")
            return False

    def place_player_on_market(self, player_id: int, price: int) -> bool:
        """
        Puts one of your own players on the market for an initial selling price.
        """
        payload = {
            "type": "sell",
            "player": player_id,
            "price": price
        }
        url = f"{self.base_url}/market"
        response = self.session.post(url, json=payload)
        
        if response.status_code in [200, 204]:
            print(f"✅ Player {player_id} placed on the market for {price}€.")
            return True
        else:
            print(f"❌ Failed to place player on market. Status: {response.status_code}, Response: {response.text}")
            return False
