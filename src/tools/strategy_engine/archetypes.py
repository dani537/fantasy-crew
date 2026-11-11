"""
Coherent Quantitative Strategy Archetypes (`archetypes.py`)
==========================================================
Defines 5 high-level, mathematically coherent strategy philosophies grounded
in sports analytics and game theory, rather than unconstrained random grid search.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional


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


STRATEGY_ARCHETYPES: Dict[str, StrategyArchetype] = {
    # 1. Adaptativa Integral por Ciclo de Vida (Estrategia Principal Inteligente)
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
    ),

    # 2. Concentración de Galácticos (Doctrina Superestrellas)
    "GALACTICOS_CORE": StrategyArchetype(
        archetype_id="GALACTICOS_CORE",
        name="Concentración de Superestrellas (Galácticos)",
        description="Plantilla ultracorta (13-14). Ahorra caja masiva para clausular solo a los mejores de la liga (media >= 5.5). Cero cromos medianos.",
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
    ),

    # 3. Moneyball Cuantitativo (Eficiencia Coste / Rendimiento)
    "MONEYBALL_QUANT": StrategyArchetype(
        archetype_id="MONEYBALL_QUANT",
        name="Moneyball Cuantitativo (Eficiencia Pts / M€)",
        description="Busca futbolistas con alto xP infravalorados por precio (<6M, ratio <=1.90x). Rota activos en pico de valor y reinvierte en perlas.",
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
    ),

    # 4. Tiburón Anti-Líderes (Doctrina Depredadora Top 3)
    "PREDATOR_ANTILEADER": StrategyArchetype(
        archetype_id="PREDATOR_ANTILEADER",
        name="Tiburón Depredador Anti-Top 3",
        description="Monitoriza las finanzas de Birra München y RusoPoderoso. Clausula sus piezas clave cuando entran en deuda o quedan desprotegidos.",
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
    ),

    # 5. Especialista en Parones y Compounding
    "COMPOUND_BREAK_MASTER": StrategyArchetype(
        archetype_id="COMPOUND_BREAK_MASTER",
        name="Apalancamiento de Parones FIFA",
        description="Construye una ventaja patrimonial gigante (+30M) aprovechando días entre jornadas y parones de 14 días para comprar a quien quiera.",
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
    ),
}
