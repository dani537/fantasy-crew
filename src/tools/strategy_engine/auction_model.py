"""
Empirical Second-Price & First-Price Auction Bidding Model (`AuctionBidModel`)
==============================================================================
Trained causally (`fecha < current_date`) on `Pujas_Historico` (1,702 real league
bids) and rival liquidity (`Historico_Equipos_Diario`).

Key Empirical Insight from AZ Finance:
- #1 Birra München (+28.92M€ trading alpha) won 144 auctions with a median
  overbid of ONLY +0.44%, whereas bottom managers overbid +19% to +36%.
- Strategy:
  1. Speculative / Momentum Flips: Bid +0.35%..+1.2% (+10k..+45k € non-round jitter)
     to capture uncontested appreciations with zero Winner's Curse.
  2. Structural Starting-XI Upgrades: Optimize expected first-price surplus
     `max_b P(b > RivalMaxBid) * (IntrinsicValuation - b)` using empirical
     overbid quantiles conditioned on player tier, daily price surge, and
     league-wide rival purchasing power.
"""

from typing import Dict, Any, Optional
import numpy as np
import pandas as pd


class AuctionBidModel:
    """Causal auction competition & optimal bid shading estimator."""

    def __init__(self):
        # Default Bayesian priors (active on Day 1 before historical bids accumulate)
        self.tier_quantiles: Dict[str, Dict[str, float]] = {
            "micro_low_mom": {"p_contested": 0.18, "q50": 0.004, "q75": 0.025, "q90": 0.065},
            "micro_high_mom": {"p_contested": 0.45, "q50": 0.025, "q75": 0.075, "q90": 0.145},
            "mid_low_mom": {"p_contested": 0.22, "q50": 0.006, "q75": 0.030, "q90": 0.070},
            "mid_high_mom": {"p_contested": 0.52, "q50": 0.032, "q75": 0.080, "q90": 0.140},
            "star_low_mom": {"p_contested": 0.30, "q50": 0.010, "q75": 0.038, "q90": 0.075},
            "star_high_mom": {"p_contested": 0.62, "q50": 0.038, "q75": 0.085, "q90": 0.135},
        }
        self.baseline_league_max_bid: float = 30_000_000.0
        self.n_fitted_auctions: int = 0

    @staticmethod
    def _classify_bucket(price: float, subida_24h: float) -> str:
        high_mom = (subida_24h >= 60_000) or (price > 0 and (subida_24h / price) >= 0.035)
        if price < 2_000_000:
            tier = "micro"
        elif price < 7_000_000:
            tier = "mid"
        else:
            tier = "star"
        return f"{tier}_{'high_mom' if high_mom else 'low_mom'}"

    def fit_causal(
        self,
        df_bids_past: Optional[pd.DataFrame],
        df_managers_today: Optional[pd.DataFrame] = None,
    ) -> None:
        """Fits empirical winning overbid quantiles strictly on past auctions."""
        if df_bids_past is None or df_bids_past.empty:
            return

        df = df_bids_past.copy()
        wins = df[df["gano_subasta"].astype(str).str.upper().isin(["SÍ", "SI", "TRUE", "1"])].copy()
        if len(wins) < 12:
            return

        wins["precio_mercado_dia"] = pd.to_numeric(wins["precio_mercado_dia"], errors="coerce").fillna(0.0)
        wins["sobrepuja_pct"] = pd.to_numeric(wins["sobrepuja_pct"], errors="coerce").fillna(0.0) / 100.0
        wins["num_pujadores_subasta"] = pd.to_numeric(wins["num_pujadores_subasta"], errors="coerce").fillna(1.0)
        wins = wins[wins["precio_mercado_dia"] > 0]
        self.n_fitted_auctions = len(wins)

        for tier_name, (lo, hi) in [("micro", (0, 2_000_000)), ("mid", (2_000_000, 7_000_000)), ("star", (7_000_000, 1e12))]:
            sub = wins[(wins["precio_mercado_dia"] >= lo) & (wins["precio_mercado_dia"] < hi)]
            if len(sub) < 8:
                continue
            contested_mask = sub["num_pujadores_subasta"] >= 2
            uncontested_mask = ~contested_mask

            for mom_tag, mask_sub in [("low_mom", uncontested_mask), ("high_mom", contested_mask)]:
                key = f"{tier_name}_{mom_tag}"
                grp = sub[mask_sub] if mask_sub.sum() >= 4 else sub
                overbids = np.clip(grp["sobrepuja_pct"].values, -0.05, 0.35)
                prior = self.tier_quantiles[key]
                w_emp = min(0.75, len(grp) / 30.0)
                p_cont = float(contested_mask.mean()) if mom_tag == "high_mom" else float(contested_mask.mean() * 0.5)
                self.tier_quantiles[key] = {
                    "p_contested": round(w_emp * p_cont + (1 - w_emp) * prior["p_contested"], 4),
                    "q50": round(w_emp * float(np.quantile(overbids, 0.50)) + (1 - w_emp) * prior["q50"], 4),
                    "q75": round(w_emp * float(np.quantile(overbids, 0.75)) + (1 - w_emp) * prior["q75"], 4),
                    "q90": round(w_emp * float(np.quantile(overbids, 0.88)) + (1 - w_emp) * prior["q90"], 4),
                }

        if df_managers_today is not None and not df_managers_today.empty and "max_puja_disponible" in df_managers_today.columns:
            mp = pd.to_numeric(df_managers_today["max_puja_disponible"], errors="coerce").dropna()
            if len(mp) > 0:
                self.baseline_league_max_bid = float(mp.median())

    def compute_optimal_bid(
        self,
        player_id: int,
        market_price: float,
        asking_price: float,
        subida_24h: float,
        efr_72h: float,
        delta_xp_xi: float,
        p_start: float,
        seller_is_rival: bool = False,
        rival_seller_in_debt: bool = False,
        league_median_max_bid: Optional[float] = None,
        idle_cash_eur: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Computes the exact optimal bid in EUR, intrinsic valuation, and win probability.

        `idle_cash_eur`: surplus cash that would otherwise sit unused. Idle cash earns 0 points,
        so the EUR value of an extra starting-XI point scales up (x1.0 .. x2.5) as it grows.
        """
        vm = float(max(150_000.0, market_price))
        ask = float(max(150_000.0, asking_price if asking_price > 0 else vm))

        # 1. Compute Intrinsic Economic Valuation:
        # Capital appreciation over ~4.5 days + Fantasy points bonus value (50k/pt + leaderboard edge)
        expected_5d_gain = max(0.0, float(efr_72h) * 1.45)
        idle_boost = 1.0 + float(np.clip((float(idle_cash_eur) - 5_000_000.0) / 8_000_000.0, 0.0, 3.5))
        min_delta = 0.40 if idle_boost <= 1.0 else 0.25
        is_xi_upgrade = (delta_xp_xi >= min_delta) and (p_start >= 0.60)
        horizon_rounds = 7.5 if p_start >= 0.80 else (5.0 if p_start >= 0.70 else 2.5)
        points_eur_value = (
            max(0.0, float(delta_xp_xi)) * horizon_rounds * 60_000.0 * idle_boost if is_xi_upgrade else 0.0
        )

        intrinsic_value = vm + expected_5d_gain + points_eur_value

        # Anti-tie deterministic jitter (based on player_id so backtest is 100% deterministic!)
        jitter = 1_300 + (int(player_id) % 7) * 600  # +1,300 .. +4,900 €

        # 2. Rival Listing Negotiation Asymmetry
        if seller_is_rival:
            effective_floor = max(vm, ask)
            if effective_floor > intrinsic_value * 0.98:
                return {
                    "should_bid": False,
                    "bid_amount": 0,
                    "intrinsic_value": round(intrinsic_value),
                    "expected_surplus": 0.0,
                    "regime": "SKIP_OVERPRICED_RIVAL",
                    "win_prob_est": 0.0,
                }
            if rival_seller_in_debt:
                # Distressed rival needs cash before kickoff: bid near VM (>= VM, never below Biwenger floor)
                target_bid = max(vm, min(effective_floor, max(vm, ask * 0.95)) + jitter)
            else:
                target_bid = max(vm, min(effective_floor, intrinsic_value * 0.95))
            target_bid = max(int(round(vm)), int(round(target_bid)))
            surplus = intrinsic_value - target_bid
            return {
                "should_bid": surplus >= 45_000,
                "bid_amount": target_bid,
                "intrinsic_value": round(intrinsic_value),
                "expected_surplus": round(surplus),
                "regime": "RIVAL_DISTRESSED_ARB" if rival_seller_in_debt else "RIVAL_DIRECT",
                "win_prob_est": 0.65 if rival_seller_in_debt else 0.45,
            }

        # 3. Computer (`Mercado`) First-Price Sealed Auction
        bucket = self._classify_bucket(vm, subida_24h)
        q = self.tier_quantiles[bucket]

        # Liquidity inflation factor when rivals are flush with cash
        liq_ratio = 1.0
        if league_median_max_bid and self.baseline_league_max_bid > 0:
            liq_ratio = float(np.clip(league_median_max_bid / 25_000_000.0, 0.80, 1.25))

        if not is_xi_upgrade:
            # REGIME S: Pure Speculative / Momentum Flip ("Birra München +0.44% Doctrine")
            # Only bid if expected 72h gain clears safety hurdle (>= +45k € or >= +4.5% ROI)
            if efr_72h < 40_000 and (efr_72h / vm) < 0.04:
                return {
                    "should_bid": False,
                    "bid_amount": 0,
                    "intrinsic_value": round(intrinsic_value),
                    "expected_surplus": 0.0,
                    "regime": "SKIP_LOW_EDGE",
                    "win_prob_est": 0.0,
                }
            # Minimal disciplined overbid: capture 85-92% of the projected 72h gain!
            # Bid at VM + min(1.4% VM, 22% of EFR_72h), floored at ask + 10k + jitter
            spec_premium = min(vm * 0.015 * liq_ratio, max(10_000.0, float(efr_72h) * 0.22))
            bid_amt = int(round(max(ask + 5_000, vm + spec_premium) + jitter))
            max_allowed = int(round(vm + float(efr_72h) * 0.65))
            if bid_amt > max_allowed:
                return {
                    "should_bid": False,
                    "bid_amount": 0,
                    "intrinsic_value": round(intrinsic_value),
                    "expected_surplus": 0.0,
                    "regime": "SKIP_THIN_MARGIN",
                    "win_prob_est": 0.0,
                }
            win_p = 1.0 - q["p_contested"] * 0.72
            return {
                "should_bid": True,
                "bid_amount": bid_amt,
                "intrinsic_value": round(intrinsic_value),
                "expected_surplus": round((intrinsic_value - bid_amt) * win_p),
                "regime": "SPECULATIVE_FLIP",
                "win_prob_est": round(win_p, 3),
            }

        # REGIME C: Structural Starting-XI Upgrade -> Grid search optimal bid shading b*
        # Cap: never overpay blindly, but when cash is abundant and rival contention is high, allow up to +22%
        if delta_xp_xi >= 1.0 and p_start >= 0.80 and idle_boost >= 1.5:
            hard_cap_pct = 0.22
        elif (subida_24h >= 100_000 and p_start >= 0.80) or idle_boost >= 1.8:
            hard_cap_pct = 0.18
        else:
            hard_cap_pct = 0.14
        max_bid_cap = min(intrinsic_value - 40_000.0, vm * (1.0 + hard_cap_pct))
        if max_bid_cap < ask:
            return {
                "should_bid": False,
                "bid_amount": 0,
                "intrinsic_value": round(intrinsic_value),
                "expected_surplus": 0.0,
                "regime": "SKIP_CAP_EXCEEDED",
                "win_prob_est": 0.0,
            }

        # Piecewise smooth CDF of rival max bid from fitted quantiles
        q50 = max(0.003, q["q50"] * liq_ratio)
        q75 = max(q50 + 0.01, q["q75"] * liq_ratio)
        q90 = max(q75 + 0.02, q["q90"] * liq_ratio)
        p_uncontested = 1.0 - q["p_contested"]

        def _win_prob(over_pct: float) -> float:
            if over_pct < 0:
                return 0.0
            if over_pct <= q50:
                cdf_c = 0.50 * (over_pct / max(q50, 1e-4))
            elif over_pct <= q75:
                cdf_c = 0.50 + 0.25 * ((over_pct - q50) / max(q75 - q50, 1e-4))
            elif over_pct <= q90:
                cdf_c = 0.75 + 0.15 * ((over_pct - q75) / max(q90 - q75, 1e-4))
            else:
                cdf_c = min(0.98, 0.90 + 0.08 * (1.0 - np.exp(-(over_pct - q90) / 0.05)))
            return float(p_uncontested + (1.0 - p_uncontested) * cdf_c)

        grid_pcts = np.linspace(max(0.002, (ask - vm) / vm), (max_bid_cap - vm) / vm, 25)
        best_ev = -1.0
        best_b = ask + jitter
        best_wp = 0.5
        for gp in grid_pcts:
            cand_b = vm * (1.0 + gp) + jitter
            if cand_b < ask or cand_b > max_bid_cap + jitter:
                continue
            wp = _win_prob(gp)
            ev = wp * (intrinsic_value - cand_b)
            if ev > best_ev:
                best_ev = ev
                best_b = cand_b
                best_wp = wp

        bid_final = int(round(best_b))
        return {
            "should_bid": best_ev >= 40_000,
            "bid_amount": bid_final,
            "intrinsic_value": round(intrinsic_value),
            "expected_surplus": round(max(0.0, best_ev)),
            "regime": "CORE_XI_UPGRADE",
            "win_prob_est": round(best_wp, 3),
        }
