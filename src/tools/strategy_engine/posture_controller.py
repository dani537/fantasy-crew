"""
Causal Standings & Regime Posture Controller.
Translates league rank, point gap to leader/podium, remaining rounds, and scarcity
into strictly causal, mathematically bounded strategy modifiers (`PROTECT_LEAD`,
`TACTICAL_PRESS`, `ASYMMETRIC_CHASER`).

Zero look-ahead: only settled payouts strictly on or before `current_date` may
be fed into `compute_modifiers`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


POSTURE_EARLY = "EARLY_SEASON_CALIBRATION"
POSTURE_PROTECT = "PROTECT_LEAD"
POSTURE_PRESS = "TACTICAL_PRESS"
POSTURE_CHASER = "ASYMMETRIC_CHASER"


def _clip(val: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, float(val))))


@dataclass
class PostureModifiers:
    """
    Bounded multiplicative/additive overrides consumed by `PortfolioOptimizer`,
    `AuctionBidModel`, `ClauseSniper`, and `AutopilotRunner`.
    """
    posture: str = POSTURE_EARLY
    my_rank: int = 1
    total_managers: int = 15
    rounds_settled: int = 0
    rounds_remaining: int = 38
    gap_to_leader: float = 0.0
    lead_over_second: float = 0.0
    urgency_index: float = 0.0  # [0, 1] chase/defense urgency

    # Core strategic multipliers (1.0 = neutral baseline)
    bid_aggression_mult: float = 1.0
    spec_bid_aggression_mult: float = 1.0
    overbid_cap_mult: float = 1.0
    clause_alpha_floor_delta_eur: int = 0
    shield_threshold_mult: float = 1.0
    credit_usage_mult: float = 1.0
    points_weight_mult: float = 1.0
    speculation_weight_mult: float = 1.0
    p_start_floor_delta: float = 0.0
    clause_timing_preference: str = "AUTO"
    rationale: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class StandingsPostureController:
    """
    Computes deterministic causal strategic posture from live or historical standings.
    Adapts cleanly across all league phases without overfitting or future leakage.
    """

    def __init__(self, total_season_rounds: int = 38, enabled: bool = True):
        self.total_season_rounds = max(1, int(total_season_rounds))
        self.enabled = bool(enabled)

    def compute_modifiers(
        self,
        my_rank: int = 1,
        total_managers: int = 15,
        my_points: float = 0.0,
        leader_points: float = 0.0,
        second_points: float = 0.0,
        podium_points: float = 0.0,
        rounds_settled: int = 0,
        my_net_worth: float = 0.0,
        leader_net_worth: float = 0.0,
        llm_overrides: Optional[Dict[str, Any]] = None,
    ) -> PostureModifiers:
        rounds_s = max(0, int(rounds_settled))
        rounds_rem = max(1, self.total_season_rounds - rounds_s)
        n_mgr = max(2, int(total_managers))
        rank_i = max(1, min(n_mgr, int(my_rank)))

        gap_to_leader = float(my_points - leader_points)
        lead_over_second = float(my_points - second_points) if rank_i == 1 else 0.0

        if not self.enabled or rounds_s < 2:
            mods = PostureModifiers(
                posture=POSTURE_EARLY,
                my_rank=rank_i,
                total_managers=n_mgr,
                rounds_settled=rounds_s,
                rounds_remaining=rounds_rem,
                gap_to_leader=round(gap_to_leader, 1),
                lead_over_second=round(lead_over_second, 1),
                urgency_index=0.0,
                rationale=(
                    f"Calibración inicial (J{rounds_s} completadas): neutralidad 1.0x maximizando "
                    "compounding de patrimonio + xP."
                ),
            )
            return self.apply_llm_overrides(mods, llm_overrides)

        deficit = max(0.0, leader_points - my_points)
        deficit_per_round = deficit / float(rounds_rem)

        # 1) Leader defense posture (#1 in league)
        if rank_i == 1:
            comfort = _clip(lead_over_second / 28.0, 0.15, 1.0)
            # Thin lead keeps healthy aggressiveness; big lead locks down variance & shields core XI
            mods = PostureModifiers(
                posture=POSTURE_PROTECT,
                my_rank=rank_i,
                total_managers=n_mgr,
                rounds_settled=rounds_s,
                rounds_remaining=rounds_rem,
                gap_to_leader=round(lead_over_second, 1),
                lead_over_second=round(lead_over_second, 1),
                urgency_index=round(comfort, 3),
                bid_aggression_mult=round(0.95 - 0.06 * comfort, 4),
                spec_bid_aggression_mult=round(0.94 - 0.07 * comfort, 4),
                overbid_cap_mult=round(0.92 - 0.08 * comfort, 4),
                clause_alpha_floor_delta_eur=int(450_000 + 450_000 * comfort),
                shield_threshold_mult=round(0.90 - 0.10 * comfort, 4),  # Lower threshold => more shielding
                credit_usage_mult=round(0.85 - 0.12 * comfort, 4),
                points_weight_mult=round(1.05 + 0.06 * comfort, 4),
                speculation_weight_mult=round(0.94 - 0.06 * comfort, 4),
                p_start_floor_delta=round(0.02 + 0.03 * comfort, 4),
                clause_timing_preference="PRE_LOCK_SURGICAL",
                rationale=(
                    f"PROTECT_LEAD (#1 con +{lead_over_second:.0f} pts sobre #2, {rounds_rem}J restantes): "
                    "compresión de varianza, mayor blindaje XI y disciplina contra sobrepujas."
                ),
            )
        # 2) Asymmetric Chaser (deep deficit or back-half standings requiring accelerated catch-up)
        elif (deficit >= 65.0 and rank_i >= 5) or (rank_i >= max(7, int(round(n_mgr * 0.50))) and rounds_s >= 3) or deficit_per_round >= 3.0:
            intensity = _clip(max(deficit / 170.0, deficit_per_round / 7.5), 0.35, 1.0)
            nw_edge = 1.0 if (my_net_worth > leader_net_worth and leader_net_worth > 0) else 0.0
            mods = PostureModifiers(
                posture=POSTURE_CHASER,
                my_rank=rank_i,
                total_managers=n_mgr,
                rounds_settled=rounds_s,
                rounds_remaining=rounds_rem,
                gap_to_leader=round(gap_to_leader, 1),
                lead_over_second=0.0,
                urgency_index=round(intensity, 3),
                bid_aggression_mult=round(1.08 + 0.10 * intensity, 4),
                spec_bid_aggression_mult=round(1.06 + 0.08 * intensity, 4),
                overbid_cap_mult=round(1.12 + 0.14 * intensity, 4),
                clause_alpha_floor_delta_eur=int(-450_000 - 650_000 * intensity),
                shield_threshold_mult=round(1.06 + 0.08 * intensity, 4),  # Prioritize offense over defensive hoarding
                credit_usage_mult=round(1.08 + 0.10 * intensity, 4),
                points_weight_mult=round(1.00 + 0.04 * nw_edge, 4),
                speculation_weight_mult=round(1.08 + 0.06 * (1.0 - 0.5 * nw_edge), 4),
                p_start_floor_delta=round(-0.02 * intensity, 4),
                clause_timing_preference="HYBRID",
                rationale=(
                    f"ASYMMETRIC_CHASER (#{rank_i}/{n_mgr}, {gap_to_leader:+.0f} pts vs líder, intensidad {intensity:.2f}): "
                    "compounding agresivo de patrimonio/EFR, sniping de alto VORP y techo de sobrepuja ampliado."
                ),
            )
        # 3) Tactical Press (podium / upper-table contention within striking distance)
        else:
            press = _clip(max(deficit / 85.0, (podium_points - my_points) / 40.0), 0.10, 1.0)
            mods = PostureModifiers(
                posture=POSTURE_PRESS,
                my_rank=rank_i,
                total_managers=n_mgr,
                rounds_settled=rounds_s,
                rounds_remaining=rounds_rem,
                gap_to_leader=round(gap_to_leader, 1),
                lead_over_second=0.0,
                urgency_index=round(press, 3),
                bid_aggression_mult=round(1.01 + 0.07 * press, 4),
                spec_bid_aggression_mult=round(1.01 + 0.05 * press, 4),
                overbid_cap_mult=round(1.02 + 0.09 * press, 4),
                clause_alpha_floor_delta_eur=int(-150_000 - 300_000 * press),
                shield_threshold_mult=round(0.98 if rank_i <= 3 else 1.02, 4),
                credit_usage_mult=round(1.02 + 0.09 * press, 4),
                points_weight_mult=round(1.03 + 0.04 * press, 4),
                speculation_weight_mult=round(1.01 + 0.03 * press, 4),
                p_start_floor_delta=0.0,
                clause_timing_preference="HYBRID",
                rationale=(
                    f"TACTICAL_PRESS (#{rank_i}/{n_mgr}, {gap_to_leader:+.0f} pts vs líder, presión {press:.2f}): "
                    "presión quirúrgica sobre rivales directos en subastas y cláusulas de mejora neta."
                ),
            )

        return self.apply_llm_overrides(mods, llm_overrides)

    def apply_llm_overrides(
        self,
        mods: PostureModifiers,
        llm_overrides: Optional[Dict[str, Any]],
    ) -> PostureModifiers:
        """
        Safely applies strictly clamped bounded deltas generated by `LLMStrategicSupervisor`.
        """
        if not llm_overrides:
            return mods

        req_posture = str(llm_overrides.get("posture_override", "AUTO") or "AUTO").upper()
        if req_posture in {POSTURE_PROTECT, POSTURE_PRESS, POSTURE_CHASER}:
            mods.posture = req_posture

        bid_d = _clip(float(llm_overrides.get("bid_aggression_delta", 0.0)), -0.12, 0.15)
        overbid_d = _clip(float(llm_overrides.get("overbid_cap_delta", 0.0)), -0.10, 0.18)
        cl_alpha_d = int(_clip(float(llm_overrides.get("clause_alpha_delta_eur", 0)), -1_000_000, 1_500_000))
        tilt = _clip(float(llm_overrides.get("speculation_vs_points_tilt", 0.0)), -0.15, 0.15)
        shield_d = _clip(float(llm_overrides.get("shield_aggression_delta", 0.0)), -0.15, 0.15)
        credit_d = _clip(float(llm_overrides.get("credit_aggression_delta", 0.0)), -0.15, 0.15)

        mods.bid_aggression_mult = round(_clip(mods.bid_aggression_mult + bid_d, 0.78, 1.30), 4)
        mods.spec_bid_aggression_mult = round(_clip(mods.spec_bid_aggression_mult + bid_d - 0.3 * tilt, 0.78, 1.30), 4)
        mods.overbid_cap_mult = round(_clip(mods.overbid_cap_mult + overbid_d, 0.75, 1.38), 4)
        mods.clause_alpha_floor_delta_eur = int(
            _clip(mods.clause_alpha_floor_delta_eur + cl_alpha_d, -1_600_000, 2_000_000)
        )
        # Positive `shield_aggression_delta` means more aggressive shielding -> lower threshold mult
        mods.shield_threshold_mult = round(_clip(mods.shield_threshold_mult - shield_d, 0.72, 1.25), 4)
        mods.credit_usage_mult = round(_clip(mods.credit_usage_mult + credit_d, 0.65, 1.25), 4)
        mods.points_weight_mult = round(_clip(mods.points_weight_mult + tilt, 0.85, 1.25), 4)
        mods.speculation_weight_mult = round(_clip(mods.speculation_weight_mult - tilt, 0.82, 1.22), 4)

        timing = str(llm_overrides.get("clause_timing_preference", "AUTO") or "AUTO").upper()
        if timing in {"IMMEDIATE", "PRE_LOCK_SURGICAL", "HYBRID"}:
            mods.clause_timing_preference = timing

        priority_pos = llm_overrides.get("priority_positions")
        if isinstance(priority_pos, (list, tuple)):
            valid_pos = [str(p).upper() for p in priority_pos if str(p).upper() in {"GK", "DF", "MF", "FW"}][:2]
            if valid_pos:
                mods.extra["priority_positions"] = valid_pos

        llm_note = str(llm_overrides.get("strategic_rationale", "") or "").strip()
        if llm_note:
            mods.extra["llm_rationale"] = llm_note
            mods.rationale = f"{mods.rationale} | Supervisor Gemini: {llm_note}"

        return mods
