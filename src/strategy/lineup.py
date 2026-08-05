"""
Deterministic Lineup Selector
==============================
Computes the optimal starting XI for a squad using only data — no LLM involved.

Used in two ways:
1. VALIDATION: checks that the Coach's proposed lineup is legal (11 players,
   valid formation, exactly 1 GK, correct positions, all from our squad).
2. FALLBACK: if the LLM lineup is illegal (it happens), the deterministic
   best XI is used instead, so we never leave the lineup unset or broken.

Scoring:
- During the season: EXPECTED_POINTS (xP) is the primary signal.
- Pre-season (all xP == 0): starter probability + market price as quality proxy.
"""

import pandas as pd

# Valid Biwenger formations: name -> (n_defenders, n_midfielders, n_forwards)
FORMATIONS = {
    "3-4-3": (3, 4, 3),
    "3-5-2": (3, 5, 2),
    "4-3-3": (4, 3, 3),
    "4-4-2": (4, 4, 2),
    "4-5-1": (4, 5, 1),
    "5-3-2": (5, 3, 2),
    "5-4-1": (5, 4, 1),
}

_UNAVAILABLE_STATUS = {"injured", "suspended", "sanctioned"}


def _player_positions(row) -> set:
    """Returns the set of lines a player can occupy: {'GK','DF','MF','FW'}."""
    positions = set()
    pos = row.get("PLAYER_POSITION")
    if isinstance(pos, str) and pos:
        positions.add(pos)
    alt = row.get("PLAYER_ALT_POSITIONS")
    if isinstance(alt, str) and alt:
        for p in alt.split(","):
            p = p.strip()
            if p in ("GK", "DF", "MF", "FW"):
                positions.add(p)
    return positions


def _score_players(squad: pd.DataFrame) -> pd.Series:
    """
    Scores each player for lineup purposes.
    Primary: EXPECTED_POINTS. Fallback (pre-season, all xP = 0):
    starter probability dominates, price breaks ties.
    Doubtful players are penalized; unavailable ones are excluded upstream.
    """
    xp = squad.get("EXPECTED_POINTS", pd.Series(0.0, index=squad.index)).fillna(0.0)

    if xp.sum() > 0:
        score = xp.astype(float)
    else:
        starter = squad.get("COMUNIATE_STARTER", pd.Series(0.0, index=squad.index)).fillna(0.0).astype(float)
        price = squad.get("PLAYER_PRICE", pd.Series(0, index=squad.index)).fillna(0).astype(float)
        score = starter * 10.0 + price / 1_000_000.0

    status = squad.get("PLAYER_STATUS", pd.Series("ok", index=squad.index)).fillna("ok")
    score = score.where(status != "doubt", score * 0.7)
    return score


def select_best_lineup(squad: pd.DataFrame):
    """
    Selects the best legal XI from the squad.

    Args:
        squad: DataFrame with our players (must contain PLAYER_ID,
               PLAYER_POSITION and ideally scoring columns).

    Returns:
        dict with 'formation' (str) and 'player_ids' (list of 11 ints ordered
        GK -> DF -> MF -> FW, as required by the Biwenger API),
        or None if no legal lineup can be fielded.
    """
    if squad is None or squad.empty:
        return None

    squad = squad.copy()
    status = squad.get("PLAYER_STATUS", pd.Series("ok", index=squad.index)).fillna("ok")
    available = squad[~status.isin(_UNAVAILABLE_STATUS)].copy()

    if available.empty:
        available = squad.copy()  # desperate: field whoever exists

    available["_score"] = _score_players(available)
    available["_positions"] = available.apply(_player_positions, axis=1)

    # --- Goalkeeper (mandatory, exactly 1) ---
    gks = available[available["_positions"].apply(lambda p: "GK" in p)]
    if gks.empty:
        return None  # No legal lineup possible without a GK
    best_gk = gks.sort_values("_score", ascending=False).iloc[0]

    field = available.drop(index=best_gk.name)

    best_lineup = None
    for formation, (n_df, n_mf, n_fw) in FORMATIONS.items():
        slots = [("DF", n_df), ("MF", n_mf), ("FW", n_fw)]
        chosen = []
        used = set()
        ok = True
        # Fill scarcest pools first to avoid greedy dead-ends
        for line, count in sorted(slots, key=lambda s: len(field[field["_positions"].apply(lambda p: s[0] in p)])):
            pool = field[
                field["_positions"].apply(lambda p: line in p) & ~field["PLAYER_ID"].isin(used)
            ].sort_values("_score", ascending=False)
            if len(pool) < count:
                ok = False
                break
            picked = pool.head(count)
            chosen.extend([(line, r["PLAYER_ID"], r["_score"]) for _, r in picked.iterrows()])
            used.update(picked["PLAYER_ID"].tolist())
        if not ok:
            continue

        total = best_gk["_score"] + sum(s for _, _, s in chosen)
        if best_lineup is None or total > best_lineup["_total"]:
            best_lineup = {
                "formation": formation,
                "player_ids": [int(best_gk["PLAYER_ID"])]
                + [int(pid) for line, pid, _ in sorted(chosen, key=lambda c: ("DF", "MF", "FW").index(c[0]))],
                "_total": total,
            }

    if best_lineup:
        best_lineup.pop("_total", None)
    return best_lineup


def validate_lineup(lineup: dict, squad: pd.DataFrame) -> bool:
    """
    Checks that an LLM-proposed lineup is legal:
    - dict with formation + exactly 11 player ids
    - formation is known
    - exactly 1 GK, and DF/MF/FW counts match the formation
    - every player belongs to our squad and can play in an assigned line
    """
    if not isinstance(lineup, dict) or squad is None or squad.empty:
        return False

    formation = lineup.get("formation") or lineup.get("formacion")
    player_ids = lineup.get("player_ids") or lineup.get("jugadores_id") or lineup.get("id_jugadores_titulares")

    if formation not in FORMATIONS or not player_ids or len(player_ids) != 11:
        return False
    if len(set(player_ids)) != 11:
        return False

    squad_ids = set(squad["PLAYER_ID"].astype(int).tolist())
    try:
        player_ids = [int(p) for p in player_ids]
    except (TypeError, ValueError):
        return False
    if not set(player_ids).issubset(squad_ids):
        return False

    selected = squad[squad["PLAYER_ID"].astype(int).isin(player_ids)].copy()
    selected["_positions"] = selected.apply(_player_positions, axis=1)

    n_df, n_mf, n_fw = FORMATIONS[formation]
    counts = {"GK": 0, "DF": 0, "MF": 0, "FW": 0}
    # Count each player in their primary position only (order GK, DF, MF, FW)
    for _, row in selected.iterrows():
        primary = row.get("PLAYER_POSITION")
        if primary in counts:
            counts[primary] += 1

    # Strict check on primary positions: 1 GK and matching lines.
    # Alt-position juggling is allowed by Biwenger but keeping it strict
    # protects us from LLM hallucinations.
    return counts["GK"] == 1 and counts["DF"] == n_df and counts["MF"] == n_mf and counts["FW"] == n_fw


def order_lineup_for_api(player_ids: list, squad: pd.DataFrame) -> list:
    """
    Orders player ids as the Biwenger API expects: GK -> DF -> MF -> FW.
    """
    if squad is None or squad.empty:
        return player_ids
    pos_rank = {"GK": 0, "DF": 1, "MF": 2, "FW": 3}
    pos_map = (
        squad.assign(_pid=squad["PLAYER_ID"].astype(int))
        .set_index("_pid")["PLAYER_POSITION"]
        .to_dict()
    )
    return sorted(player_ids, key=lambda pid: pos_rank.get(pos_map.get(int(pid)), 9))
