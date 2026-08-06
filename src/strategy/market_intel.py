"""
Market Intelligence
===================
Deterministic analysis of the league board to estimate how competitive each
auction is and how much above market value a player typically goes for.

Uses the historical `board_bids.csv` (losing bids + winning amounts recorded
per resolved auction) to answer the question: "if there are N bidders, how much
over market value do I probably need to pay?". This feeds both the Sporting
Director's rationale and the hard value caps in the guardrails.

Since live rival bid counts are not exposed by the market endpoint, this is an
empirical estimate based on what actually happened in this league recently.
"""

import pandas as pd


_PREMIUM_RANGE = (0.5, 3.0)


def _premium_over_price(pay: float, price: float):
    if not pay or not price:
        return None
    ratio = pay / price
    if _PREMIUM_RANGE[0] <= ratio <= _PREMIUM_RANGE[1]:
        return ratio - 1.0
    return None


def _round(x, nd=2):
    return round(x, nd) if x is not None else None


def compute_overbid_stats(board, master) -> dict:
    """
    Computes competition stats from the board's resolved auctions.

    Args:
        board:  DataFrame from `board_bids.csv` (date, player_id, bid_amount,
                winning_amount).
        master: df_master for current market prices and positions.

    Returns:
        A dict with aggregate figures, or an empty dict when there is not
        enough data to draw any conclusion.
    """
    if board is None or board.empty:
        return {}
    needed = {"player_id", "winning_amount"}
    if not needed.issubset(board.columns):
        return {}

    price_map = {}
    pos_map = {}
    if master is not None and "PLAYER_ID" in master.columns:
        if "PLAYER_PRICE" in master.columns:
            price_map = dict(zip(master["PLAYER_ID"], master["PLAYER_PRICE"].fillna(0)))
        if "PLAYER_POSITION" in master.columns:
            pos_map = dict(zip(master["PLAYER_ID"], master["PLAYER_POSITION"]))

    winning = board.groupby("player_id")["winning_amount"].first()
    n_bids = board.groupby("player_id").size().add(1)  # +1 for the winner

    samples = []
    for pid, win in winning.items():
        prem = _premium_over_price(float(win), float(price_map.get(pid, 0) or 0))
        if prem is None:
            continue
        samples.append({
            "n": int(n_bids.get(pid, 1)),
            "prem": prem,
            "pos": pos_map.get(pid),
            "win": float(win),
        })

    if len(samples) < 2:
        return {}

    def _avg(rows):
        if not rows:
            return None
        return sum(r["prem"] for r in rows) / len(rows)

    overall = _avg(samples)

    def _bucket(n):
        if n <= 1:
            return "1"
        if n <= 2:
            return "2"
        if n <= 3:
            return "3"
        return "4+"

    buckets = {}
    for label in ["1", "2", "3", "4+"]:
        rows = [s for s in samples if _bucket(s["n"]) == label]
        buckets[label] = _avg(rows)

    by_pos = {}
    for pos in ["GK", "DF", "MF", "FW"]:
        rows = [s for s in samples if s["pos"] == pos]
        if rows:
            by_pos[pos] = _avg(rows)

    return {
        "samples": len(samples),
        "overall_premium": _round(overall),
        "by_bids": {k: _round(v) for k, v in buckets.items() if v is not None},
        "by_position": {k: _round(v) for k, v in by_pos.items() if v is not None},
    }


def build_market_intel_summary(board, master) -> str:
    """
    Renders a compact, human-readable summary of competition stats to embed in
    the Sporting Director prompt. Falls back to a neutral note with no data.
    """
    stats = compute_overbid_stats(board, master)
    if not stats:
        return (
            "No hay datos suficientes del board para estimar el sobreprecio típico. "
            "Sé conservador: no superes el valor de mercado de forma evidente."
        )

    parts = [
        f"Basado en {stats['samples']} subastas resueltas recientes, el mercado de esta "
        f"liga paga de media un {stats['overall_premium']*100:.0f}% por encima del valor del jugador."
    ]

    if stats["by_bids"]:
        by_bids = ", ".join(
            f"{n} pujas → +{v*100:.0f}%" for n, v in stats["by_bids"].items()
        )
        parts.append(f"Según competencia: {by_bids}.")

    if stats["by_position"]:
        by_pos = ", ".join(
            f"{p}: +{v*100:.0f}%" for p, v in stats["by_position"].items()
        )
        parts.append(f"Por posición: {by_pos}.")

    parts.append(
        "REGLA DE VALOR: puja cerca de VALOR + % estimado, NUNCA muy por encima. "
        "Si el precio del vendedor ya supera el valor con holgura, NO pujes o plantéalo como negociación a la baja."
    )
    return " ".join(parts)


def expected_overbid_for(board, master, position=None, n_bids_hint=None) -> float:
    """
    Returns a sensible default estimate of the overbid premium to expect.

    Ties together overall, position-specific and competition-level averages so
    the SD has a single number to anchor its bid sizing to market value.
    """
    stats = compute_overbid_stats(board, master)
    if not stats:
        return 0.06

    position_prem = stats.get("by_position", {}).get(position)
    if n_bids_hint and stats.get("by_bids"):
        bucket = str(min(n_bids_hint, 4)) if n_bids_hint else "2"
        compet_prem = stats["by_bids"].get(bucket)
    else:
        compet_prem = None

    if position_prem is not None and compet_prem is not None:
        return (position_prem + compet_prem) / 2
    if position_prem is not None:
        return position_prem
    if compet_prem is not None:
        return compet_prem
    return stats.get("overall_premium") or 0.06


def competition_multiplier(board, master, position=None) -> float:
    """
    Multiplier (>= 1.0) to apply on top of a player's CURRENT market price so that
    our bid stays competitive instead of underbidding and losing every auction.

    Sources, in order of preference:
      1. Position-specific observed overbid in this league (board history).
      2. Overall observed overbid.
      3. Conservative default (small bump to beat the asking price).

    This is what stops us "ofreciendo demasiado poco" — if this league pays +X%
    over the price for a given line, we bid at least that.
    """
    stats = compute_overbid_stats(board, master)
    if stats:
        if position and stats.get("by_position", {}).get(position) is not None:
            return 1.0 + stats["by_position"][position]
        if stats.get("overall_premium") is not None:
            return 1.0 + stats["overall_premium"]
    # No reliable history: still beat the current price to stay in the running.
    return 1.03


def adjust_bids_to_competitive(bids: list, board, master, auction=True) -> list:
    """
    Radjustes the amount of each MARKET bid to a competitive level based on the
    league's observed overbid for that line. Never lowers a bid; never overrides
    a rival negotiation (auction=False keeps rival amounts untouched, and they are
    already capped below the asking price by the guardrails).

    Args:
        bids: list of enriched bid dicts (with position, sale_price, seller_is_rival).
        auction: True for the pre-7:00 market auction phase (raise to compete).
                 False for post-auction rival offers (leave the negotiated amount).
    """
    if not bids or not auction:
        return bids

    for bid in bids:
        if bid.get("seller_is_rival"):
            continue  # rival negotiations stay as negotiated (never raised)
        pos = bid.get("position")
        mult = competition_multiplier(board, master, position=pos)
        base = bid.get("sale_price") or bid.get("market_price")
        if not base:
            continue
        try:
            competitive = int(float(base) * mult)
        except (TypeError, ValueError):
            continue
        if competitive > bid["amount"]:
            # Cap the raise at +40% of the original to avoid reckless spending.
            ceiling = int(bid["amount"] * 1.40)
            bid["amount"] = min(competitive, ceiling)
            bid["competitive_raise"] = True
    return bids