from .market_actions import MarketActions
from .lineup_actions import LineupActions

class BiwengerActions:
    """
    Facade class that gathers all active interactions with the Biwenger API.
    """
    def __init__(self, session):
        self.market = MarketActions(session)
        self.lineup = LineupActions(session)
