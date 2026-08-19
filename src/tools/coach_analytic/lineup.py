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
    "4-5-1": (4, 5, 1),
    "3-5-2": (3, 5, 2),
    "4-4-2": (4, 4, 2),
    "4-3-3": (4, 3, 3),
    "3-4-3": (3, 4, 3),
    "5-4-1": (5, 4, 1),
    "5-3-2": (5, 3, 2),
}
OFFICIAL_FORMATIONS = FORMATIONS

# Offensive preference: formations with more midfielders are preferred (a MF goal
# outscores a FW goal, so we want more of our scorers sitting one line back).
_FORMATION_PREF = {name: n_mf for name, (_, n_mf, _) in FORMATIONS.items()}

# Line rank: lower = further back (defensive). A multi-position player earns more
# per goal when fielded in the lowest (most-back) line they can play.
_LINE_RANK = {"GK": 0, "DF": 1, "MF": 2, "FW": 3}

_UNAVAILABLE_STATUS = {"injured", "suspended", "sanctioned"}
_LINE_ORDER = ("GK", "DF", "MF", "FW")


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
    # Deepest (most-defensive) valid line for each player, for the multi-position
    # "play him as far back as possible" rule (more points per goal).
    available["_deepest"] = available["_positions"].apply(
        lambda p: min((_LINE_RANK.get(x, 9) for x in p), default=9)
    )

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
            line_rank = _LINE_RANK[line]
            pool = field[
                field["_positions"].apply(lambda p: line in p) & ~field["PLAYER_ID"].isin(used)
            ].copy()
            if len(pool) < count:
                ok = False
                break
            # Multi-position rule: prefer fielding a player in their most-back valid
            # line (deduct depth so lower rank = further back = preferred), then score.
            pool["_pref"] = (pool["_deepest"] == line_rank).astype(int)
            pool = pool.sort_values(["_pref", "_score"], ascending=[False, False])
            picked = pool.head(count)
            chosen.extend([(line, r["PLAYER_ID"], r["_score"]) for _, r in picked.iterrows()])
            used.update(picked["PLAYER_ID"].tolist())
        if not ok:
            continue

        total = best_gk["_score"] + sum(s for _, _, s in chosen)
        candidate = {
            "formation": formation,
            "player_ids": [int(best_gk["PLAYER_ID"])]
            + [int(pid) for line, pid, _ in sorted(chosen, key=lambda c: _LINE_RANK[c[0]])],
            "slots": [{"player_id": int(best_gk["PLAYER_ID"]), "linea": "GK"}]
            + [
                {"player_id": int(pid), "linea": line}
                for line, pid, _ in sorted(chosen, key=lambda c: _LINE_RANK[c[0]])
            ],
            "_total": total,
        }
        if best_lineup is None:
            best_lineup = candidate
            continue
        # Prefer more fantasy goals; break ties toward a more offensive formation
        # (more midfielders / attackers), so our FW/MF scorers slot one line back.
        cur_pref = _FORMATION_PREF.get(best_lineup["formation"], 0)
        new_pref = _FORMATION_PREF.get(formation, 0)
        if (total, new_pref) > (best_lineup["_total"], cur_pref):
            best_lineup = candidate

    if best_lineup:
        best_lineup.pop("_total", None)
    return best_lineup


def _normalise_slots(lineup: dict) -> list | None:
    """Returns explicit lineup slots, accepting the legacy ordered-ID format.

    The explicit format is the source of truth because a player's primary
    position may differ from the line in which Biwenger fields them.
    """
    formation = lineup.get("formation") or lineup.get("formacion")
    if formation not in FORMATIONS:
        return None

    raw_slots = lineup.get("slots") or lineup.get("titulares")
    if raw_slots:
        normalised = []
        for slot in raw_slots:
            if not isinstance(slot, dict):
                return None
            player_id = slot.get("player_id") or slot.get("id_jugador")
            line = slot.get("linea") or slot.get("assigned_position")
            try:
                normalised.append({"player_id": int(player_id), "linea": str(line).upper()})
            except (TypeError, ValueError):
                return None
        return normalised

    # Compatibility for older SD/Coach reports: their IDs were explicitly
    # required to be ordered GK -> DF -> MF -> FW, so their line can be
    # recovered without reverting to primary-position validation.
    player_ids = lineup.get("player_ids") or lineup.get("jugadores_id") or lineup.get("id_jugadores_titulares")
    if not player_ids or len(player_ids) != 11:
        return None
    n_df, n_mf, n_fw = FORMATIONS[formation]
    lines = ["GK"] + ["DF"] * n_df + ["MF"] * n_mf + ["FW"] * n_fw
    try:
        return [{"player_id": int(pid), "linea": line} for pid, line in zip(player_ids, lines)]
    except (TypeError, ValueError):
        return None


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
    slots = _normalise_slots(lineup)
    if formation not in FORMATIONS or not slots or len(slots) != 11:
        return False
    player_ids = [slot["player_id"] for slot in slots]
    if len(set(player_ids)) != 11:
        return False

    squad_ids = set(squad["PLAYER_ID"].astype(int).tolist())
    try:
        player_ids = [int(p) for p in player_ids]
    except (TypeError, ValueError):
        return False
    if not set(player_ids).issubset(squad_ids):
        return False

    n_df, n_mf, n_fw = FORMATIONS[formation]
    expected_counts = {"GK": 1, "DF": n_df, "MF": n_mf, "FW": n_fw}
    actual_counts = {line: sum(slot["linea"] == line for slot in slots) for line in _LINE_ORDER}
    if actual_counts != expected_counts:
        return False

    players_by_id = squad.assign(_pid=squad["PLAYER_ID"].astype(int)).set_index("_pid")
    for slot in slots:
        line = slot["linea"]
        if line not in _LINE_ORDER:
            return False
        if line not in _player_positions(players_by_id.loc[slot["player_id"]]):
            return False
    return True


def order_lineup_for_api(lineup: dict | list, squad: pd.DataFrame) -> list:
    """
    Orders player ids as the Biwenger API expects: GK -> DF -> MF -> FW.
    """
    if isinstance(lineup, dict):
        slots = _normalise_slots(lineup)
        if slots:
            return [slot["player_id"] for slot in sorted(slots, key=lambda slot: _LINE_RANK[slot["linea"]])]
        return []
    # Legacy callers already provide the documented API order. Do not reorder
    # by primary position: that would corrupt valid multiposition assignments.
    return [int(player_id) for player_id in lineup]
