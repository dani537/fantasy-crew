"""
Coherent Quantitative Strategy Archetypes (`archetypes.py`)
==========================================================
Defines high-level, mathematically coherent strategy philosophies grounded
in sports analytics, market microstructure, and game theory. Every field is
consumed directly by `PortfolioOptimizer`, `AuctionBidModel`, `ClauseSniper`,
and `AZFinanceMarketEnv`.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass
class StrategyArchetype:
    archetype_id: str
    name: str
    description: str
    lifecycle_driven: bool              # Dynamically adjusts behavior across early/mature/endgame phases
    target_roster_size: int             # Target squad depth
    clause_ratio_cap: float             # Maximum buyout multiplier willing to pay
    clause_min_avg_pts: float           # Minimum average points of victim player
    clause_min_delta_xp: float          # Minimum expected point delta over starting-XI floor
    clause_cash_reserve_pct: float      # % of working surplus cash allocated to clauses vs auctions
    protect_pillars_absolute: bool      # Strict prohibition against selling core superstars for arbitrage
    shield_rival_threat_pct: float      # % of cash budget allocated to defensive clause shields
    auction_overbid_factor: float       # Overbid scaling factor on computer auctions
    speculative_hold_days_max: int      # Maximum days to hold a speculative flip
    max_simultaneous_bids: int = 5      # Maximum daily computer market bids
    # Extended causal strategy dimensions (zero dead parameters!)
    clause_policy: str = "COMPETITIVE_VORP"          # COMPETITIVE_VORP | VALUE_COMPOUND_VORP | SURGICAL_SCARCITY | AGGRESSIVE | DISABLED
    rank_adaptive: bool = True                       # Modulates via StandingsPostureController
    clause_timing_mode: str = "HYBRID"               # IMMEDIATE | PRE_LOCK_SURGICAL | HYBRID
    credit_utilization_aggression: float = 1.0       # Multiplier on trading-day leverage willingness
    hold_decay_penalty_eur: int = 80_000             # Daily penalty after speculative_hold_days_max
    efr_weight: float = 1.0                          # Scaling factor on 72h EFR capital appreciation
    min_efr_trigger: int = 40_000                    # Minimum 72h EFR EUR threshold for speculative flips
    core_bid_aggression: float = 1.0                 # Scaling factor on starting-XI upgrade intrinsic edge
    spec_bid_aggression: float = 1.0                 # Scaling factor on speculative flip bid aggressiveness
    xp_horizon_rounds: float = 7.5                   # Amortization horizon (rounds) for core XI points value
    reserve_buffer_eur: int = 0                      # Soft protective cash buffer
    priority_mode: str = "BALANCED"                  # BALANCED | WEALTH_COMPOUND | XI_MAX | VORP_SNIPER


STRATEGY_ARCHETYPES: Dict[str, StrategyArchetype] = {
    # 1. Adaptativa Integral por Ciclo de Vida (Estrategia Principal Clásica)
    "ADAPTIVE_LIFECYCLE": StrategyArchetype(
        archetype_id="ADAPTIVE_LIFECYCLE",
        name="Adaptativa Integral por Ciclo de Vida",
        description="Fase 1 Trader -> Fase 2 Depredador VORP con Clausulazos -> Fase 3 Cosecha Once + Parones FIFA + Gestión Táctica de Lesiones",
        lifecycle_driven=True,
        target_roster_size=15,
        clause_ratio_cap=2.20,
        clause_min_avg_pts=4.8,
        clause_min_delta_xp=0.75,
        clause_cash_reserve_pct=0.80,
        protect_pillars_absolute=True,
        shield_rival_threat_pct=0.20,
        auction_overbid_factor=1.015,
        speculative_hold_days_max=3,
        max_simultaneous_bids=4,
        clause_policy="COMPETITIVE_VORP",
        clause_timing_mode="HYBRID",
    ),

    # 2. Concentración de Galácticos (Doctrina Superestrellas)
    "GALACTICOS_CORE": StrategyArchetype(
        archetype_id="GALACTICOS_CORE",
        name="Concentración de Superestrellas (Galácticos)",
        description="Plantilla ultracorta (13-14). Ahorra caja masiva para clausular solo a los mejores de la liga (media >= 5.4). Cero cromos medianos.",
        lifecycle_driven=False,
        target_roster_size=13,
        clause_ratio_cap=2.50,
        clause_min_avg_pts=5.4,
        clause_min_delta_xp=1.20,
        clause_cash_reserve_pct=0.90,
        protect_pillars_absolute=True,
        shield_rival_threat_pct=0.30,
        auction_overbid_factor=1.030,
        speculative_hold_days_max=2,
        max_simultaneous_bids=2,
        clause_policy="COMPETITIVE_VORP",
        clause_timing_mode="PRE_LOCK_SURGICAL",
        core_bid_aggression=1.12,
        xp_horizon_rounds=8.5,
        priority_mode="XI_MAX",
    ),

    # 3. Moneyball Cuantitativo (Eficiencia Coste / Rendimiento)
    "MONEYBALL_QUANT": StrategyArchetype(
        archetype_id="MONEYBALL_QUANT",
        name="Moneyball Cuantitativo (Eficiencia Pts / M€)",
        description="Busca futbolistas con alto xP infravalorados por precio (ratio <=1.90x). Rota activos en pico de valor y reinvierte en perlas.",
        lifecycle_driven=False,
        target_roster_size=18,
        clause_ratio_cap=1.90,
        clause_min_avg_pts=4.2,
        clause_min_delta_xp=0.50,
        clause_cash_reserve_pct=0.45,
        protect_pillars_absolute=False,
        shield_rival_threat_pct=0.10,
        auction_overbid_factor=1.005,
        speculative_hold_days_max=4,
        max_simultaneous_bids=5,
        clause_policy="VALUE_COMPOUND_VORP",
        efr_weight=1.18,
        min_efr_trigger=35_000,
        priority_mode="WEALTH_COMPOUND",
    ),

    # 4. Tiburón Anti-Líderes (Doctrina Depredadora Top 3)
    "PREDATOR_ANTILEADER": StrategyArchetype(
        archetype_id="PREDATOR_ANTILEADER",
        name="Tiburón Depredador Anti-Top 3",
        description="Monitoriza las finanzas de los líderes. Clausula sus piezas clave cuando entran en deuda o quedan desprotegidos.",
        lifecycle_driven=True,
        target_roster_size=15,
        clause_ratio_cap=2.30,
        clause_min_avg_pts=4.9,
        clause_min_delta_xp=0.60,
        clause_cash_reserve_pct=0.85,
        protect_pillars_absolute=True,
        shield_rival_threat_pct=0.25,
        auction_overbid_factor=1.020,
        speculative_hold_days_max=3,
        max_simultaneous_bids=3,
        clause_policy="AGGRESSIVE",
        clause_timing_mode="IMMEDIATE",
        core_bid_aggression=1.06,
        priority_mode="VORP_SNIPER",
    ),

    # 5. Especialista en Parones y Compounding
    "COMPOUND_BREAK_MASTER": StrategyArchetype(
        archetype_id="COMPOUND_BREAK_MASTER",
        name="Apalancamiento de Parones FIFA",
        description="Construye una ventaja patrimonial gigante aprovechando días entre jornadas y parones de 14 días para comprar a quien quiera.",
        lifecycle_driven=True,
        target_roster_size=19,
        clause_ratio_cap=2.00,
        clause_min_avg_pts=4.6,
        clause_min_delta_xp=0.70,
        clause_cash_reserve_pct=0.50,
        protect_pillars_absolute=True,
        shield_rival_threat_pct=0.15,
        auction_overbid_factor=1.010,
        speculative_hold_days_max=5,
        max_simultaneous_bids=5,
        clause_policy="SURGICAL_SCARCITY",
        efr_weight=1.22,
        min_efr_trigger=35_000,
        priority_mode="WEALTH_COMPOUND",
    ),

    # 6. Depredador Híbrido Adaptativo por Ranking (Rank-Adaptive Predator)
    "HYBRID_RANK_PREDATOR": StrategyArchetype(
        archetype_id="HYBRID_RANK_PREDATOR",
        name="Depredador Híbrido Adaptativo por Posición",
        description="Combina compounding rápido en H1 con adaptación causal por postura (PROTECT_LEAD / TACTICAL_PRESS / ASYMMETRIC_CHASER) y sniping Value-Compound.",
        lifecycle_driven=True,
        target_roster_size=16,
        clause_ratio_cap=2.25,
        clause_min_avg_pts=4.7,
        clause_min_delta_xp=0.65,
        clause_cash_reserve_pct=0.78,
        protect_pillars_absolute=True,
        shield_rival_threat_pct=0.22,
        auction_overbid_factor=1.022,
        speculative_hold_days_max=3,
        max_simultaneous_bids=5,
        clause_policy="VALUE_COMPOUND_VORP",
        rank_adaptive=True,
        clause_timing_mode="HYBRID",
        credit_utilization_aggression=1.08,
        hold_decay_penalty_eur=95_000,
        efr_weight=1.18,
        min_efr_trigger=35_000,
        core_bid_aggression=1.10,
        spec_bid_aggression=1.06,
        xp_horizon_rounds=8.0,
        priority_mode="BALANCED",
    ),

    # 7. Buitre de Liquidez Pro (High-Velocity EFR + Underpriced Clauses)
    "LIQUIDITY_VULTURE_PRO": StrategyArchetype(
        archetype_id="LIQUIDITY_VULTURE_PRO",
        name="Buitre de Liquidez Pro (EFR + Gangas)",
        description="Maximiza velocidad de rotación de capital (+EFR diario con decaimiento estricto) para comprar galácticos por subasta y cláusulas con descuento real.",
        lifecycle_driven=True,
        target_roster_size=18,
        clause_ratio_cap=1.95,
        clause_min_avg_pts=4.4,
        clause_min_delta_xp=0.55,
        clause_cash_reserve_pct=0.60,
        protect_pillars_absolute=True,
        shield_rival_threat_pct=0.14,
        auction_overbid_factor=1.012,
        speculative_hold_days_max=3,
        max_simultaneous_bids=6,
        clause_policy="VALUE_COMPOUND_VORP",
        rank_adaptive=True,
        clause_timing_mode="HYBRID",
        credit_utilization_aggression=1.10,
        hold_decay_penalty_eur=115_000,
        efr_weight=1.35,
        min_efr_trigger=32_000,
        core_bid_aggression=1.05,
        spec_bid_aggression=1.12,
        xp_horizon_rounds=7.5,
        priority_mode="WEALTH_COMPOUND",
    ),

    # 8. Quant Galáctico Core v2 (Elite Tier-1 Concentrator)
    "QUANT_GALACTIC_CORE_V2": StrategyArchetype(
        archetype_id="QUANT_GALACTIC_CORE_V2",
        name="Quant Galáctico Core v2 (Élite Concentrada)",
        description="Paga primas agresivas por saltos de +1.0 xP en el XI, amortización de 9 jornadas sobre Tier-1, plantilla concentrada y blindajes inteligentes.",
        lifecycle_driven=True,
        target_roster_size=14,
        clause_ratio_cap=2.45,
        clause_min_avg_pts=5.0,
        clause_min_delta_xp=0.90,
        clause_cash_reserve_pct=0.88,
        protect_pillars_absolute=True,
        shield_rival_threat_pct=0.28,
        auction_overbid_factor=1.032,
        speculative_hold_days_max=2,
        max_simultaneous_bids=4,
        clause_policy="COMPETITIVE_VORP",
        rank_adaptive=True,
        clause_timing_mode="PRE_LOCK_SURGICAL",
        credit_utilization_aggression=1.12,
        hold_decay_penalty_eur=105_000,
        efr_weight=1.05,
        min_efr_trigger=42_000,
        core_bid_aggression=1.18,
        spec_bid_aggression=0.96,
        xp_horizon_rounds=9.0,
        priority_mode="XI_MAX",
    ),

    # 9. Centinela Defensivo de Régimen (Low-Variance Protector)
    "REGIME_SENTINEL_SHIELD": StrategyArchetype(
        archetype_id="REGIME_SENTINEL_SHIELD",
        name="Centinela de Régimen y Blindaje Defensivo",
        description="Diseñado para proteger ventaja o minimizar drawdowns en escasez severa: blindajes elevados, reservas de seguridad y cero sobrepujas especulativas.",
        lifecycle_driven=True,
        target_roster_size=15,
        clause_ratio_cap=2.10,
        clause_min_avg_pts=4.8,
        clause_min_delta_xp=0.85,
        clause_cash_reserve_pct=0.70,
        protect_pillars_absolute=True,
        shield_rival_threat_pct=0.35,
        auction_overbid_factor=1.008,
        speculative_hold_days_max=3,
        max_simultaneous_bids=4,
        clause_policy="SURGICAL_SCARCITY",
        rank_adaptive=True,
        clause_timing_mode="PRE_LOCK_SURGICAL",
        credit_utilization_aggression=0.85,
        hold_decay_penalty_eur=75_000,
        efr_weight=0.95,
        min_efr_trigger=45_000,
        core_bid_aggression=1.04,
        spec_bid_aggression=0.92,
        xp_horizon_rounds=7.5,
        reserve_buffer_eur=400_000,
        priority_mode="XI_MAX",
    ),

    # 10. Perseguidor Asimétrico Máximo (High-Conviction Catch-Up)
    "ASYMMETRIC_CHASER_MAX": StrategyArchetype(
        archetype_id="ASYMMETRIC_CHASER_MAX",
        name="Perseguidor Asimétrico Máximo (Catch-Up)",
        description="Máxima agresividad en apalancamiento limpio, pujas por diferenciadores del XI y clausulazos inmediatos para remontar desventajas amplias.",
        lifecycle_driven=True,
        target_roster_size=17,
        clause_ratio_cap=2.35,
        clause_min_avg_pts=4.5,
        clause_min_delta_xp=0.50,
        clause_cash_reserve_pct=0.85,
        protect_pillars_absolute=True,
        shield_rival_threat_pct=0.12,
        auction_overbid_factor=1.028,
        speculative_hold_days_max=3,
        max_simultaneous_bids=6,
        clause_policy="AGGRESSIVE",
        rank_adaptive=True,
        clause_timing_mode="IMMEDIATE",
        credit_utilization_aggression=1.20,
        hold_decay_penalty_eur=100_000,
        efr_weight=1.22,
        min_efr_trigger=32_000,
        core_bid_aggression=1.16,
        spec_bid_aggression=1.14,
        xp_horizon_rounds=8.0,
        priority_mode="VORP_SNIPER",
    ),

    # 11. Arbitrador de Tempo y Compounding (Fast-Turn Wealth Engine)
    "TEMPO_ARB_COMPOUNDER": StrategyArchetype(
        archetype_id="TEMPO_ARB_COMPOUNDER",
        name="Arbitrador de Tempo y Compounding Ultra-Rápido",
        description="Rotación implacable de flips estancados tras 48h, multiplicador alto sobre EFR + mejoras Value-Compound para dominar H1 y escalar en H2.",
        lifecycle_driven=True,
        target_roster_size=20,
        clause_ratio_cap=2.15,
        clause_min_avg_pts=4.6,
        clause_min_delta_xp=0.60,
        clause_cash_reserve_pct=0.68,
        protect_pillars_absolute=True,
        shield_rival_threat_pct=0.18,
        auction_overbid_factor=1.015,
        speculative_hold_days_max=2,
        max_simultaneous_bids=6,
        clause_policy="VALUE_COMPOUND_VORP",
        rank_adaptive=True,
        clause_timing_mode="HYBRID",
        credit_utilization_aggression=1.14,
        hold_decay_penalty_eur=140_000,
        efr_weight=1.30,
        min_efr_trigger=30_000,
        core_bid_aggression=1.08,
        spec_bid_aggression=1.12,
        xp_horizon_rounds=7.5,
        priority_mode="WEALTH_COMPOUND",
    ),
}
