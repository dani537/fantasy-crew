"""
Pre-Decision LLM Strategic Supervisor (`gemini-4-hydrogen`).
Fires ONLY on meaningful macro/league triggers (round settlement, international break,
pre-clause-lock window, or new Franchise-Core/Starter injury shock) to calibrate
bounded strategic posture adjustments BEFORE the MILP `PortfolioOptimizer` executes.

Safety Guarantees:
  1. Cannot bypass Biwenger rules (`<=5` sales, `48h` clause lock, kickoff solvency `>=0€`).
  2. Outputs strict schema-validated & numerically clamped deltas (`validate_and_clamp_overrides`).
  3. Persists last strategic review to `Memoria_Estrategica` (or fallback cache) so intraday
     runs do not waste tokens on duplicate calls when league regime has not changed.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


SUPERVISOR_ENTRY_ID = "STRATEGIC_SUPERVISOR_STATE"

DEFAULT_SUPERVISOR_STATE: Dict[str, Any] = {
    "last_review_date": "",
    "last_trigger": "",
    "last_rounds_settled": -1,
    "last_injured_core_ids": [],
    "overrides": {
        "posture_override": "AUTO",
        "bid_aggression_delta": 0.0,
        "overbid_cap_delta": 0.0,
        "clause_alpha_delta_eur": 0,
        "speculation_vs_points_tilt": 0.0,
        "shield_aggression_delta": 0.0,
        "credit_aggression_delta": 0.0,
        "clause_timing_preference": "AUTO",
        "priority_positions": [],
        "strategic_rationale": "",
    },
}


def _clip(v: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, float(v))))


def _parse_json_object(raw_text: str) -> Optional[Dict[str, Any]]:
    if not raw_text:
        return None
    txt = str(raw_text).strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```(?:json)?\s*", "", txt, flags=re.IGNORECASE)
        txt = re.sub(r"\s*```$", "", txt)
    try:
        parsed = json.loads(txt)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        m = re.search(r"\{.*\}", txt, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(0))
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                return None
    return None


def validate_and_clamp_overrides(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Strictly validates and clamps all strategic deltas produced by the LLM supervisor
    so malformed outputs or extreme values can never compromise solvency or MILP integrity.
    """
    if not isinstance(raw, dict):
        return dict(DEFAULT_SUPERVISOR_STATE["overrides"])

    def _safe_float(key: str, default: float = 0.0) -> float:
        try:
            return float(raw.get(key, default))
        except Exception:
            return default

    posture = str(raw.get("posture_override", "AUTO") or "AUTO").strip().upper()
    if posture not in {"AUTO", "PROTECT_LEAD", "TACTICAL_PRESS", "ASYMMETRIC_CHASER"}:
        posture = "AUTO"

    timing = str(raw.get("clause_timing_preference", "AUTO") or "AUTO").strip().upper()
    if timing not in {"AUTO", "IMMEDIATE", "PRE_LOCK_SURGICAL", "HYBRID"}:
        timing = "AUTO"

    raw_pos = raw.get("priority_positions", [])
    if isinstance(raw_pos, str):
        raw_pos = [p.strip() for p in raw_pos.split(",") if p.strip()]
    pos_clean: List[str] = []
    if isinstance(raw_pos, (list, tuple)):
        for p in raw_pos:
            up = str(p).strip().upper()
            if up in {"GK", "DF", "MF", "FW"} and up not in pos_clean:
                pos_clean.append(up)
            if len(pos_clean) >= 2:
                break

    rationale = str(raw.get("strategic_rationale", "") or "").strip()
    if len(rationale) > 480:
        cut = rationale[:480].rsplit(" ", 1)[0].rstrip(",;:")
        rationale = (cut + "...") if cut else rationale[:480]

    return {
        "posture_override": posture,
        "bid_aggression_delta": round(_clip(_safe_float("bid_aggression_delta", 0.0), -0.12, 0.15), 4),
        "overbid_cap_delta": round(_clip(_safe_float("overbid_cap_delta", 0.0), -0.10, 0.18), 4),
        "clause_alpha_delta_eur": int(_clip(_safe_float("clause_alpha_delta_eur", 0.0), -1_000_000.0, 1_500_000.0)),
        "speculation_vs_points_tilt": round(_clip(_safe_float("speculation_vs_points_tilt", 0.0), -0.15, 0.15), 4),
        "shield_aggression_delta": round(_clip(_safe_float("shield_aggression_delta", 0.0), -0.15, 0.15), 4),
        "credit_aggression_delta": round(_clip(_safe_float("credit_aggression_delta", 0.0), -0.15, 0.15), 4),
        "clause_timing_preference": timing,
        "priority_positions": pos_clean,
        "strategic_rationale": rationale,
    }


class LLMStrategicSupervisor:
    """
    Event-driven strategic meta-controller powered by `gemini-4-hydrogen` (`daily` tier).
    """

    def __init__(self, llm_client: Any = None, tier: str = "daily"):
        self.llm_client = llm_client
        self.tier = tier

    @staticmethod
    def load_persisted_state(memory_ledger: Any = None) -> Dict[str, Any]:
        state = json.loads(json.dumps(DEFAULT_SUPERVISOR_STATE))
        if memory_ledger is None or not hasattr(memory_ledger, "_connect"):
            return state
        conn = memory_ledger._connect()
        try:
            row = conn.execute(
                "SELECT notas_estrategicas FROM Memoria_Estrategica WHERE entry_id=?",
                (SUPERVISOR_ENTRY_ID,),
            ).fetchone()
            if row and row[0]:
                parsed = json.loads(str(row[0]))
                if isinstance(parsed, dict):
                    state.update(parsed)
                    state["overrides"] = validate_and_clamp_overrides(parsed.get("overrides"))
        except Exception:
            pass
        finally:
            if hasattr(memory_ledger, "_close_if_disk"):
                memory_ledger._close_if_disk(conn)
        return state

    @staticmethod
    def save_persisted_state(state: Dict[str, Any], memory_ledger: Any = None, jornada_objetivo: int = 0) -> None:
        if memory_ledger is None or not hasattr(memory_ledger, "_connect"):
            return
        now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ov = state.get("overrides") or {}
        conn = memory_ledger._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO Memoria_Estrategica
                (entry_id, ts, jornada_objetivo, fase, regimen_mercado, plan_deuda_colateral,
                 objetivos_prioritarios, notas_estrategicas, origen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    SUPERVISOR_ENTRY_ID,
                    now_s,
                    int(jornada_objetivo),
                    str(state.get("last_trigger", "SUPERVISOR")),
                    str(ov.get("posture_override", "AUTO")),
                    f"timing={ov.get('clause_timing_preference', 'AUTO')}",
                    ",".join(ov.get("priority_positions") or []),
                    json.dumps(state, ensure_ascii=False),
                    "LLM_SUPERVISOR",
                ),
            )
            conn.commit()
        except Exception:
            pass
        finally:
            if hasattr(memory_ledger, "_close_if_disk"):
                memory_ledger._close_if_disk(conn)

    @staticmethod
    def detect_trigger(
        current_date: str,
        rounds_settled: int,
        days_to_kickoff: int,
        hours_to_clause_lock: Optional[float],
        clauses_open: bool,
        new_core_injury_ids: List[int],
        prev_state: Dict[str, Any],
        force: bool = False,
    ) -> Tuple[bool, str]:
        """
        Determines whether a strategic LLM consultation is justified right now.
        Avoids redundant intraday invocations while responding immediately to shocks.
        """
        if force:
            return True, "MANUAL_FORCE"

        last_date = str(prev_state.get("last_review_date", "") or "")
        last_rounds = int(prev_state.get("last_rounds_settled", -1))
        prev_inj = set(int(x) for x in (prev_state.get("last_injured_core_ids") or []))
        cur_inj = set(int(x) for x in (new_core_injury_ids or []))
        unseen_injuries = cur_inj - prev_inj

        if unseen_injuries:
            return True, f"KEY_INJURY_SHOCK({len(unseen_injuries)})"

        if rounds_settled > last_rounds and rounds_settled >= 1:
            return True, "POST_ROUND_SETTLEMENT"

        days_since_last = 999
        if last_date:
            try:
                days_since_last = (datetime.strptime(current_date[:10], "%Y-%m-%d") - datetime.strptime(last_date[:10], "%Y-%m-%d")).days
            except Exception:
                days_since_last = 999

        if clauses_open and hours_to_clause_lock is not None and 0.0 < float(hours_to_clause_lock) <= 48.0 and days_since_last >= 2:
            return True, "PRE_CLAUSE_LOCK"

        if days_to_kickoff >= 8 and days_since_last >= 3:
            return True, "INTERNATIONAL_BREAK"

        if days_since_last >= 5:
            return True, "PERIODIC_5D_REVIEW"

        return False, "CACHED_POSTURE"

    def evaluate_or_reuse(
        self,
        current_date: str,
        standings_snapshot: Dict[str, Any],
        posture_modifiers: Dict[str, Any],
        scarcity_summary: Dict[str, Any],
        squad_injuries_core: List[Dict[str, Any]],
        days_to_kickoff: int = 3,
        hours_to_clause_lock: Optional[float] = None,
        clauses_open: bool = True,
        target_jornada_num: int = 0,
        memory_ledger: Any = None,
        warehouse: Any = None,  # alias backward-compatible with memory_ledger
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Reuses persisted bounded overrides unless a strategic trigger fires; on trigger,
        queries `gemini-4-hydrogen` via `LLMClient` to produce calibrated bounded deltas.
        """
        ledger = memory_ledger or warehouse
        prev_state = self.load_persisted_state(memory_ledger=ledger)
        rounds_settled = int(standings_snapshot.get("rounds_settled", 0))
        core_inj_ids = [int(x.get("player_id", 0)) for x in (squad_injuries_core or []) if x.get("player_id")]

        should_call, trigger = self.detect_trigger(
            current_date=current_date,
            rounds_settled=rounds_settled,
            days_to_kickoff=days_to_kickoff,
            hours_to_clause_lock=hours_to_clause_lock,
            clauses_open=clauses_open,
            new_core_injury_ids=core_inj_ids,
            prev_state=prev_state,
            force=force,
        )

        if not should_call or self.llm_client is None:
            cached = validate_and_clamp_overrides(prev_state.get("overrides"))
            return {
                "triggered": False,
                "llm_called": False,
                "trigger": trigger if not should_call else f"{trigger}_NO_LLM",
                "model_used": None,
                "overrides": cached,
            }

        prompt = self._build_prompt(
            current_date=current_date,
            trigger=trigger,
            standings_snapshot=standings_snapshot,
            posture_modifiers=posture_modifiers,
            scarcity_summary=scarcity_summary,
            squad_injuries_core=squad_injuries_core,
            days_to_kickoff=days_to_kickoff,
            hours_to_clause_lock=hours_to_clause_lock,
            clauses_open=clauses_open,
        )
        system_inst = (
            "Eres el Director Cuantitativo de Fantasy Champion (Biwenger LaLiga). "
            "Devuelve EXCLUSIVAMENTE un objeto JSON válido calibrando ajustes acotados "
            "de postura competitiva según posición en tabla, brecha al líder, escasez del mercado "
            "y lesiones estructurales. Nunca propongas acciones ilegales ni saltos extremos."
        )

        model_used: Optional[str] = None
        parsed_raw: Optional[Dict[str, Any]] = None
        try:
            if hasattr(self.llm_client, "complete_json"):
                resp = self.llm_client.complete_json(prompt=prompt, tier=self.tier, system_instruction=system_inst)
                if isinstance(resp, dict):
                    model_used = resp.get("model")
                    parsed_raw = resp.get("data") if isinstance(resp.get("data"), dict) else resp
            else:
                res = self.llm_client.chat_completion(
                    messages=[
                        {"role": "system", "content": system_inst},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    max_tokens=2200,
                    timeout=45,
                    response_format={"type": "json_object"},
                )
                model_used = (res.get("model") or res.get("model_used")) if isinstance(res, dict) else None
                if isinstance(res, dict) and res.get("success"):
                    parsed_raw = _parse_json_object(res.get("content", ""))
        except Exception:
            parsed_raw = None

        if parsed_raw is None:
            cached = validate_and_clamp_overrides(prev_state.get("overrides"))
            return {
                "triggered": True,
                "llm_called": False,
                "trigger": f"{trigger}_FALLBACK",
                "model_used": model_used,
                "overrides": cached,
            }

        clamped = validate_and_clamp_overrides(parsed_raw)
        new_state = {
            "last_review_date": current_date[:10],
            "last_trigger": trigger,
            "last_rounds_settled": rounds_settled,
            "last_injured_core_ids": sorted(set(core_inj_ids)),
            "overrides": clamped,
        }
        self.save_persisted_state(new_state, memory_ledger=ledger, jornada_objetivo=target_jornada_num)

        return {
            "triggered": True,
            "llm_called": True,
            "trigger": trigger,
            "model_used": model_used,
            "overrides": clamped,
        }

    @staticmethod
    def _build_prompt(
        current_date: str,
        trigger: str,
        standings_snapshot: Dict[str, Any],
        posture_modifiers: Dict[str, Any],
        scarcity_summary: Dict[str, Any],
        squad_injuries_core: List[Dict[str, Any]],
        days_to_kickoff: int,
        hours_to_clause_lock: Optional[float],
        clauses_open: bool,
    ) -> str:
        context_blob = {
            "fecha": current_date[:10],
            "trigger": trigger,
            "clasificacion": standings_snapshot,
            "postura_cuantitativa_base": {
                "posture": posture_modifiers.get("posture"),
                "urgency_index": posture_modifiers.get("urgency_index"),
                "bid_aggression_mult": posture_modifiers.get("bid_aggression_mult"),
                "overbid_cap_mult": posture_modifiers.get("overbid_cap_mult"),
                "credit_usage_mult": posture_modifiers.get("credit_usage_mult"),
            },
            "escasez_mercado": scarcity_summary,
            "lesiones_nucleo_propio": squad_injuries_core[:6],
            "calendario": {
                "dias_para_jornada": days_to_kickoff,
                "clausulas_abiertas": clauses_open,
                "horas_para_bloqueo_clausulas": hours_to_clause_lock,
            },
        }
        return (
            "Contexto competitivo actual (estrictamente causal):\n"
            + json.dumps(context_blob, ensure_ascii=False, indent=2)
            + "\n\nDevuelve un JSON con exactamente estas claves:\n"
            "{\n"
            '  "posture_override": "AUTO" | "PROTECT_LEAD" | "TACTICAL_PRESS" | "ASYMMETRIC_CHASER",\n'
            '  "bid_aggression_delta": float entre -0.12 y +0.15,\n'
            '  "overbid_cap_delta": float entre -0.10 y +0.18,\n'
            '  "clause_alpha_delta_eur": int entre -1000000 y +1500000,\n'
            '  "speculation_vs_points_tilt": float entre -0.15 y +0.15 (>0 prioriza puntos XI, <0 prioriza patrimonio/EFR),\n'
            '  "shield_aggression_delta": float entre -0.15 y +0.15 (>0 blinda más),\n'
            '  "credit_aggression_delta": float entre -0.15 y +0.15,\n'
            '  "clause_timing_preference": "AUTO" | "IMMEDIATE" | "PRE_LOCK_SURGICAL" | "HYBRID",\n'
            '  "priority_positions": lista de hasta 2 entre ["GK","DF","MF","FW"],\n'
            '  "strategic_rationale": "máx 2 frases explicando el porqué táctico"\n'
            "}"
        )
