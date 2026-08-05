"""
Deterministic Guardrails
=========================
Hard safety rules applied to the LLM's transfer decisions BEFORE anything is
sent to the Biwenger API. The LLM advises; these rules protect the team from
catastrophic decisions (observed in real runs):

- Selling 5 of 10 players at minimum price (squad left unable to field an XI).
- Selling a player bought via a 5M clause for 2M (realizing a 3M loss).
- Bidding on injured players or overpaying beyond the budget.
- Bidding on a rival's player without addressing the offer to the seller.
"""

import pandas as pd

# --- Tunable strategy parameters ---
STARTER_PROTECTION_THRESHOLD = 0.70   # COMUNIATE_STARTER >= 70% -> do not sell
MIN_SALE_PRICE_RATIO = 0.70           # Never list below 70% of purchase price
MIN_SQUAD_SIZE = 11                   # Never drop below this many fit players
MAX_SINGLE_BID_BUDGET_RATIO = 0.50    # One bid may not exceed 50% of balance
MIN_BALANCE_RESERVE = 0               # Balance must stay >= 0 (Biwenger rule)


def _squad_index(squad: pd.DataFrame) -> dict:
    """Maps PLAYER_ID -> row (as dict) for fast lookups."""
    if squad is None or squad.empty:
        return {}
    return {int(r["PLAYER_ID"]): r.to_dict() for _, r in squad.iterrows()}


def filter_sales(sales: list, squad: pd.DataFrame):
    """
    Validates sale operations proposed by the LLM.

    Returns:
        (approved_sales, blocked_sales) where blocked items carry a 'blocked_reason'.
    """
    approved, blocked = [], []
    if not sales:
        return approved, blocked

    players = _squad_index(squad)
    squad_size = len(players)
    fit_count = 0
    if squad is not None and not squad.empty and "PLAYER_STATUS" in squad.columns:
        fit_count = int((squad["PLAYER_STATUS"].fillna("ok") != "injured").sum())
    else:
        fit_count = squad_size

    n_gks = 0
    if squad is not None and not squad.empty and "PLAYER_POSITION" in squad.columns:
        n_gks = int((squad["PLAYER_POSITION"] == "GK").sum())

    # How many players can we afford to list without going below MIN_SQUAD_SIZE?
    sales_budget = max(0, fit_count - MIN_SQUAD_SIZE)

    for sale in sales:
        pid = sale.get("player_id") or sale.get("id_jugador")
        price = sale.get("price") or sale.get("precio_minimo_esperado") or sale.get("precio") or 0
        name = sale.get("nombre", "?")

        def _block(reason):
            blocked.append({**sale, "blocked_reason": reason})

        try:
            pid = int(pid)
        except (TypeError, ValueError):
            _block("Invalid player id")
            continue

        player = players.get(pid)
        if player is None:
            _block(f"{name} is not in our squad")
            continue

        status = str(player.get("PLAYER_STATUS") or "ok")
        starter = float(player.get("COMUNIATE_STARTER") or 0.0)
        purchase = float(player.get("BIWPLAYER_PURCHASE_PRICE") or 0.0)
        position = player.get("PLAYER_POSITION")

        # 1. Squad size protection
        if sales_budget <= 0 and status != "injured":
            _block(f"Squad too thin ({fit_count} fit players, min {MIN_SQUAD_SIZE}). Sale forbidden.")
            continue

        # 2. Only GK protection
        if position == "GK" and n_gks <= 1:
            _block("Cannot sell our only goalkeeper")
            continue

        # 3. Starter protection (healthy probable starters are not for sale)
        if starter >= STARTER_PROTECTION_THRESHOLD and status == "ok":
            _block(f"Starter protection: {name} has {starter:.0%} starter probability")
            continue

        # 4. Clause/value protection: never realize a big loss on a fit player
        if purchase > 0 and price and int(price) < purchase * MIN_SALE_PRICE_RATIO and status == "ok":
            _block(
                f"Value protection: listing {name} at {int(price):,}€ "
                f"realizes a loss vs {purchase:,.0f}€ paid"
            )
            continue

        approved.append(sale)
        sales_budget -= 1

    return approved, blocked


def filter_bids(bids: list, market: pd.DataFrame, balance: float, squad: pd.DataFrame):
    """
    Validates bid operations proposed by the LLM.

    Rules:
    - Player must be on the market and not injured.
    - Bid must be >= minimum sale price.
    - Bid must be <= MAX_SINGLE_BID_BUDGET_RATIO * balance.
    - Total bids must fit within (balance - MIN_BALANCE_RESERVE);
      bids are prioritized by squad needs (missing positions first).

    Returns:
        (approved_bids, blocked_bids). Approved bids include 'to_user_id'
        when the seller is a rival manager (required by the Biwenger API).
    """
    approved, blocked = [], []
    if not bids:
        return approved, blocked

    market_idx = _squad_index(market)
    needs = compute_squad_needs(squad)
    needed_positions = set(needs.get("missing_positions", []))

    def need_rank(player_pos):
        # Bids covering a missing position go first
        return 0 if player_pos in needed_positions else 1

    enriched = []
    for bid in bids:
        pid = bid.get("player_id") or bid.get("id_jugador_mercado")
        amount = bid.get("amount") or bid.get("importe_oferta") or 0
        name = bid.get("nombre", "?")
        try:
            pid = int(pid)
            amount = int(amount)
        except (TypeError, ValueError):
            blocked.append({**bid, "blocked_reason": "Invalid player id or amount"})
            continue

        player = market_idx.get(pid)
        if player is None:
            blocked.append({**bid, "blocked_reason": f"{name} is not on the market"})
            continue

        if str(player.get("PLAYER_STATUS") or "ok") == "injured":
            blocked.append({**bid, "blocked_reason": f"{name} is injured"})
            continue

        sale_price = player.get("MARKET_SALE_PRICE")
        if pd.notna(sale_price) and amount < int(sale_price):
            blocked.append({**bid, "blocked_reason": f"Bid {amount:,}€ below sale price {int(sale_price):,}€"})
            continue

        if amount > balance * MAX_SINGLE_BID_BUDGET_RATIO:
            blocked.append({
                **bid,
                "blocked_reason": f"Bid {amount:,}€ exceeds {MAX_SINGLE_BID_BUDGET_RATIO:.0%} of balance",
            })
            continue

        seller_id = player.get("MARKET_SALE_USER_ID")
        seller_name = player.get("MARKET_SALE_USER_NAME")
        to_user_id = None
        if pd.notna(seller_id) and seller_name not in (None, "Mercado"):
            to_user_id = int(seller_id)

        enriched.append({
            "player_id": pid,
            "amount": amount,
            "nombre": name,
            "position": player.get("PLAYER_POSITION"),
            "to_user_id": to_user_id,
        })

    # Prioritize needs, then keep insertion order (stable sort)
    enriched.sort(key=lambda b: need_rank(b["position"]))

    remaining = balance - MIN_BALANCE_RESERVE
    for bid in enriched:
        if bid["amount"] <= remaining:
            approved.append(bid)
            remaining -= bid["amount"]
        else:
            blocked.append({**bid, "blocked_reason": "Insufficient remaining budget"})

    return approved, blocked


def compute_squad_needs(squad: pd.DataFrame) -> dict:
    """
    Computes structural squad needs: how many players per line are missing
    to field a competitive XI plus minimal depth.
    """
    result = {
        "squad_size": 0,
        "fit_players": 0,
        "counts": {"GK": 0, "DF": 0, "MF": 0, "FW": 0},
        "missing_positions": [],
        "summary": "",
    }
    if squad is None or squad.empty:
        result["missing_positions"] = ["GK", "DF", "MF", "FW"]
        result["summary"] = "Empty squad."
        return result

    result["squad_size"] = len(squad)
    fit = squad
    if "PLAYER_STATUS" in squad.columns:
        fit = squad[squad["PLAYER_STATUS"].fillna("ok") != "injured"]
    result["fit_players"] = len(fit)

    if "PLAYER_POSITION" in fit.columns:
        for pos in result["counts"]:
            result["counts"][pos] = int((fit["PLAYER_POSITION"] == pos).sum())

    # Minimums to field a classic XI with a bit of depth
    minimums = {"GK": 1, "DF": 4, "MF": 4, "FW": 2}
    missing = []
    notes = []
    for pos, minimum in minimums.items():
        deficit = minimum - result["counts"][pos]
        if deficit > 0:
            missing.append(pos)
            notes.append(f"{pos}: need {deficit} more (have {result['counts'][pos]})")
    if result["fit_players"] < MIN_SQUAD_SIZE:
        notes.append(f"Only {result['fit_players']} fit players (< {MIN_SQUAD_SIZE}): signings are the top priority, sales are blocked.")

    result["missing_positions"] = missing
    result["summary"] = "; ".join(notes) if notes else "Squad structure is complete."
    return result
