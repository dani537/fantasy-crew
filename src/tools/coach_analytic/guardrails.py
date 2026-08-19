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
STARTER_PROTECTION_THRESHOLD = 0.70   # COMUNIATE_STARTER >= 70% -> do not sell / starter premium allowed
MIN_SALE_PRICE_RATIO = 0.70           # Never list below 70% of purchase price
MIN_SQUAD_SIZE = 11                   # Never drop below this many fit players
MAX_SINGLE_BID_BUDGET_RATIO = 1.00    # A single bid may use up to 100% of balance (allows signing top players/stars)
MIN_BALANCE_RESERVE = 0               # Balance must stay >= 0 (Biwenger rule)
MIN_LIQUIDITY_BUFFER_RATIO = 0.15     # Keep ~15% liquidity cushion during preseason
# Value caps: never pay more than MARKET_PRICE * (1 + cap) for a player.
MAX_OVERPAY_STARTER = 0.20            # Probable starters can carry a limited hype premium
MAX_OVERPAY_BACKUP = 0.08             # Rotational/backup players: barely any premium
# Negotiation floor vs a rival's asking price (direct offers can go below asking).
MIN_RIVAL_BID_FLOOR = 0.60

# --- Goalkeeper strategy (per user's manager logic) ---
GK_PLACEHOLDER_PRICE = 150_000        # absolute minimum bid to cover the GK position urgently
GK_BACKUP_MAX = 1_000_000             # a backup/insurance GK is only worth it if cheap
GK_STARTER_THRESHOLD = 0.80           # a "titular" GK must be a near-certain starter


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


def _value_cap(player_row) -> float:
    """
    Maximum justified bid for a player: market value adjusted by starter status.
    Paying beyond this means overpaying (especially dangerous for backups).
    """
    market_price = player_row.get("PLAYER_PRICE")
    if not pd.notna(market_price) or not market_price:
        return float("inf")
    starter = float(player_row.get("COMUNIATE_STARTER") or 0.0)
    cap_ratio = MAX_OVERPAY_STARTER if starter >= STARTER_PROTECTION_THRESHOLD else MAX_OVERPAY_BACKUP
    return float(market_price) * (1 + cap_ratio)


def filter_bids(bids: list, market: pd.DataFrame, balance: float, squad: pd.DataFrame):
    """
    Validates bid operations proposed by the LLM.

    Rules:
    - Player must be on the market and not injured.
    - Computer (Mercado) auctions: bid must be >= minimum sale price.
      Rival direct offers: bid must be >= MIN_RIVAL_BID_FLOOR * sale price
      (i.e. we can negotiate downwards instead of paying the asking price).
    - Bid must never exceed the justified value cap (market price adjusted by
      starter status) -> stops overpaying for backups/rotational players.
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

        is_gk = (player.get("PLAYER_POSITION") == "GK")

        seller_id = player.get("MARKET_SALE_USER_ID")
        seller_name = player.get("MARKET_SALE_USER_NAME")
        to_user_id = None
        is_rival = bool(
            pd.notna(seller_id) and seller_name not in (None, "Mercado")
        )
        if is_rival:
            to_user_id = int(seller_id)

        sale_price = player.get("MARKET_SALE_PRICE")
        if pd.notna(sale_price):
            sale_price = int(sale_price)
            if is_rival:
                # Negotiate DOWN: never bid more than a rival asks, only ever less.
                floor = int(sale_price * MIN_RIVAL_BID_FLOOR)
                if amount < floor:
                    blocked.append({
                        **bid,
                        "blocked_reason": f"Bid {amount:,}€ too low to negotiate vs asking {sale_price:,}€ (floor {floor:,}€)",
                    })
                    continue
                if amount > sale_price:
                    blocked.append({
                        **bid,
                        "blocked_reason": f"Bid {amount:,}€ exceeds rival's asking price {sale_price:,}€ — negotiate DOWN, never overbid a rival",
                    })
                    continue
            elif amount < sale_price:
                blocked.append({**bid, "blocked_reason": f"Bid {amount:,}€ below sale price {sale_price:,}€"})
                continue

        # Goalkeeper strategy: the GK must be a STARTER (or a cheap cover/insurance).
        if is_gk:
            starter = float(player.get("COMUNIATE_STARTER") or 0.0)
            has_gk = "GK" not in needed_positions
            if not has_gk:
                # We have NO starting GK: only accept clear starters, or a cheap
                # placeholder to cover the position. NEVER burn money on a backup
                # GK when we have no starter yet (wait for a starter to appear).
                if starter < GK_STARTER_THRESHOLD and amount > GK_PLACEHOLDER_PRICE:
                    blocked.append({
                        **bid,
                        "blocked_reason": (f"{name} no es portero titular (start {starter:.0%}); "
                                           f"no gastamos en suplentes. Si no hay titular, puja mínima de cubrimiento ({GK_PLACEHOLDER_PRICE:,}€)"),
                    })
                    continue
                if sale_price and starter < GK_STARTER_THRESHOLD and sale_price > GK_PLACEHOLDER_PRICE:
                    blocked.append({
                        **bid,
                        "blocked_reason": f"{name} es portero suplente caro; esperamos a un titular",
                    })
                    continue
            else:
                # We already own a starter: a cheap SUB goalkeeper is only worth
                # it as injury insurance (same-team backup).
                if starter < STARTER_PROTECTION_THRESHOLD and amount > GK_BACKUP_MAX:
                    blocked.append({
                        **bid,
                        "blocked_reason": f"{name} es portero suplente por {amount:,}€; solo interesa como seguro barato (<={GK_BACKUP_MAX:,}€)",
                    })
                    continue

        cap = _value_cap(player)
        if cap != float("inf") and amount > cap:
            blocked.append({
                **bid,
                "blocked_reason": f"Bid {amount:,}€ exceeds justified value {cap:,.0f}€ "
                                  f"(overpaying for this player's profile)",
            })
            continue

        if amount > balance * MAX_SINGLE_BID_BUDGET_RATIO:
            blocked.append({
                **bid,
                "blocked_reason": f"Bid {amount:,}€ exceeds {MAX_SINGLE_BID_BUDGET_RATIO:.0%} of balance",
            })
            continue

        enriched.append({
            "player_id": pid,
            "amount": amount,
            "nombre": name,
            "position": player.get("PLAYER_POSITION"),
            "to_user_id": to_user_id,
            "seller_is_rival": is_rival,
            "sale_price": sale_price,
            "market_price": player.get("PLAYER_PRICE"),
            "starter": float(player.get("COMUNIATE_STARTER") or 0.0),
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
    Computes structural squad needs based on effective starter probabilities
    and multiposition coverage, rather than raw position counts.
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

    # Count natural primary positions
    if "PLAYER_POSITION" in fit.columns:
        for pos in result["counts"]:
            # Handle both string pos and comma-separated merged pos
            result["counts"][pos] = int(fit["PLAYER_POSITION"].apply(
                lambda p: pos in [x.strip() for x in str(p).split(',')] if pd.notna(p) else False
            ).sum())

    missing = []
    notes = []

    # 1. Goalkeeper Policy:
    # Check starter GKs (COMUNIATE_STARTER >= 0.70).
    gk_players = fit[fit["PLAYER_POSITION"].astype(str).str.contains("GK", na=False)] if "GK" in fit["PLAYER_POSITION"].to_string() else pd.DataFrame()
    starter_gks = 0
    if not gk_players.empty and "COMUNIATE_STARTER" in gk_players.columns:
        starter_gks = int((gk_players["COMUNIATE_STARTER"] >= 0.70).sum())

    if starter_gks == 0:
        missing.append("GK")
        notes.append("GK: No hay portero titular confirmado. Prioridad ALTA de fichaje.")
    elif starter_gks >= 2:
        notes.append(f"GK: Sobrecubierta ({starter_gks} porteros titulares caros). Política óptima: 1 titular + su suplente barato de club para liberar capital vendiendo a uno.")
    else:
        notes.append("GK: 1 portero titular cubierto.")

    # 2. Defense Policy: Check effective DF coverage (including multiposition DF with starter >= 0.5)
    effective_df = 0
    df_non_starters = []
    for _, row in fit.iterrows():
        pos_str = str(row.get("PLAYER_POSITION") or "")
        alt_str = str(row.get("PLAYER_ALT_POSITIONS") or "")
        combined = f"{pos_str}, {alt_str}"
        if "DF" in combined:
            starter = float(row.get("COMUNIATE_STARTER") or 0.0)
            if starter >= 0.5:
                effective_df += 1
            else:
                df_non_starters.append(str(row.get("PLAYER_NAME", "DF")))

    if effective_df < 3:
        missing.append("DF")
        non_start_str = ", ".join(df_non_starters) if df_non_starters else "ninguno"
        notes.append(f"DF: Solo {effective_df} defensas titulares fiables. Suplentes con 0% ({non_start_str}) son punto flaco. Fichar 1 DF titular.")
    else:
        notes.append(f"DF: {effective_df} defensas titulares disponibles.")

    # 3. Midfield Policy:
    mf_count = 0
    for _, row in fit.iterrows():
        pos_str = str(row.get("PLAYER_POSITION") or "")
        if "MF" in pos_str:
            starter = float(row.get("COMUNIATE_STARTER") or 0.0)
            if starter >= 0.5:
                mf_count += 1
    notes.append(f"MF: Línea mejor cubierta ({mf_count} medios fiables). Los no titulares (ej. Camavinga) son transferibles para liquidez.")

    # 4. Forward Policy: Check confirmed top strikers
    pure_fw_starters = 0
    pure_fw_subs = []
    for _, row in fit.iterrows():
        pos_str = str(row.get("PLAYER_POSITION") or "")
        # Check players whose primary position is FW
        if pos_str.strip().startswith("FW"):
            starter = float(row.get("COMUNIATE_STARTER") or 0.0)
            if starter >= 0.70:
                pure_fw_starters += 1
            else:
                pure_fw_subs.append(str(row.get("PLAYER_NAME", "FW")))

    if pure_fw_starters == 0:
        missing.insert(0, "FW")  # Top priority
        sub_names = ", ".join(pure_fw_subs) if pure_fw_subs else "Bisiwu"
        notes.append(f"FW: Mayor debilidad estructural. 0 delanteros centro titulares (solo suplente en revalorización: {sub_names}). Prioridad ALTA urgente: Fichar un delantero centro (FW) titular.")

    if result["fit_players"] < MIN_SQUAD_SIZE:
        notes.append(f"Solo {result['fit_players']} jugadores disponibles (< {MIN_SQUAD_SIZE}): plantilla corta.")

    result["missing_positions"] = missing
    result["summary"] = " | ".join(notes) if notes else "Estructura de plantilla completa."
    return result


def get_speculative_trading_targets(df_master: pd.DataFrame, available_budget: float, max_targets: int = 3) -> list:
    """
    Identifies players with high daily price increments (speculative market hype)
    that can be signed to generate capital gains (trading/flipping).
    """
    if df_master is None or df_master.empty or available_budget <= 0:
        return []
        
    if 'MARKET_SALE_PRICE' not in df_master.columns or 'PLAYER_PRICE_INCREMENT' not in df_master.columns:
        return []
        
    status = df_master.get('PLAYER_STATUS', pd.Series('ok', index=df_master.index)).fillna('ok')
    on_market = df_master[
        (df_master['MARKET_SALE_PRICE'] > 0) & 
        (status != 'injured') &
        (df_master['PLAYER_PRICE_INCREMENT'] > 30000)
    ].copy()
    
    if on_market.empty:
        return []
        
    on_market['SPEC_SCORE'] = on_market['PLAYER_PRICE_INCREMENT'] / on_market['MARKET_SALE_PRICE']
    targets = on_market.sort_values(by=['SPEC_SCORE', 'PLAYER_PRICE_INCREMENT'], ascending=[False, False])
    
    proposed_bids = []
    spent = 0.0
    for _, r in targets.head(10).iterrows():
        sale_price = float(r['MARKET_SALE_PRICE'])
        bid_amount = int(sale_price * 1.02)
        if spent + bid_amount <= available_budget:
            proposed_bids.append({
                "player_id": int(r['PLAYER_ID']),
                "nombre": r['PLAYER_NAME'],
                "amount": bid_amount,
                "reason": f"Speculative flip: rising +{int(r['PLAYER_PRICE_INCREMENT']):,}€/day",
            })
            spent += bid_amount
            if len(proposed_bids) >= max_targets:
                break
                
    return proposed_bids
