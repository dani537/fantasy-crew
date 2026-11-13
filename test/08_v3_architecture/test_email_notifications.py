"""
Unit tests for HTML email rendering, JSON-to-Markdown normalization,
inline animated GIF generation, and MIMEMultipart('related') CID delivery.
"""
import io
import json
import os
import tempfile
import unittest
from email import message_from_string
from unittest.mock import MagicMock, patch

from PIL import Image

from src.tools.notifications.email_visuals import (
    build_animated_hero_gif,
    build_animated_pitch_gif,
)
from src.tools.notifications.notifier import NotificationHub


def _sample_cycle_summary() -> dict:
    return {
        "date": "2026-11-13",
        "trigger_phase": "MORNING_FULL",
        "mode": "DRY_RUN",
        "dry_run": True,
        "team_name": "Demo FC",
        "league_name": "Liga Demo",
        "is_solvent": True,
        "target_jornada_num": 13,
        "starting_cash": 9_250_000,
        "projected_cash": 11_400_000,
        "autonomous_clause_cap_eur": 25_000_000,
        "phase_context": {
            "phase": "OPEN_TRADING",
            "next_kickoff_ts": "2026-11-20 21:00:00",
        },
        "strategy_posture": {
            "posture": "ASYMMETRIC_CHASER",
            "my_rank": 10,
            "total_managers": 15,
            "gap_to_leader": -185.0,
            "rationale": "Compounding activo para recortar puntos.",
        },
        "supervisor_report": {
            "triggered": False,
            "llm_called": False,
            "trigger": "CACHED_POSTURE",
            "model_used": "gemini-4-hydrogen",
            "overrides": {
                "priority_positions": ["FW", "MF"],
                "strategic_rationale": "Priorizar pegada inmediata en delantera y medio.",
            },
        },
        "plan": {
            "clauses_open": True,
            "scarcity_summary": {"free_ge_5m": 6, "free_ge_10m": 1},
            "all_sales": [
                {"nombre": "Jugador Venta", "sale_price": 2_150_000, "reason": "NPV_OPTIMAL_DISPOSAL", "requires_human_approval": True}
            ],
            "market_bids": [
                {"nombre": "Jugador Puja", "position": "MF", "market_price": 3_400_000, "amount": 3_750_000, "delta_xp_xi": 2.4, "win_prob_est": 0.91, "regime": "CORE_XI_UPGRADE"}
            ],
            "preventive_listings": [
                {"nombre": "Jugador Flip", "asking_price": 1_800_000, "listing_reason": "DISPOSAL_NPV"}
            ],
            "lineup": {
                "formation": "4-4-2",
                "expected_points": 41.2,
                "slots": [
                    {"player_id": 1, "nombre": "Remiro", "linea": "GK", "xp": 3.4},
                    {"player_id": 2, "nombre": "Vivian", "linea": "DF", "xp": 3.1},
                    {"player_id": 3, "nombre": "Kounde", "linea": "DF", "xp": 3.5},
                    {"player_id": 4, "nombre": "Pau Cubarsi", "linea": "DF", "xp": 3.0},
                    {"player_id": 5, "nombre": "Mingueza", "linea": "DF", "xp": 2.9},
                    {"player_id": 6, "nombre": "Baena", "linea": "MF", "xp": 4.2},
                    {"player_id": 7, "nombre": "Pedri", "linea": "MF", "xp": 4.5},
                    {"player_id": 8, "nombre": "Valverde", "linea": "MF", "xp": 4.1},
                    {"player_id": 9, "nombre": "Sancet", "linea": "MF", "xp": 3.7},
                    {"player_id": 10, "nombre": "Lamine Yamal", "linea": "FW", "xp": 4.9},
                    {"player_id": 11, "nombre": "Mbappe", "linea": "FW", "xp": 3.9},
                ],
            },
        },
        "briefing": json.dumps(
            {
                "sesion": "MORNING_FULL (2026-11-13) | DRY_RUN: true",
                "1_diagnostico_financiero_fase_y_escasez": [
                    "Liquidez: Caja positiva y solvencia garantizada.",
                    "Postura: ASYMMETRIC_CHASER activa.",
                ],
            },
            ensure_ascii=False,
        ),
        "easy_summary": json.dumps(
            {
                "resumen": [
                    "• Hoy hemos mantenido el dinero en positivo y preparado una puja.",
                    "• El once titular queda guardado en 4-4-2 con 41.2 puntos esperados.",
                ]
            },
            ensure_ascii=False,
        ),
    }


class TestEmailNotificationsAndVisuals(unittest.TestCase):
    def test_animated_gifs_build_valid_multiframe_streams(self):
        summary = _sample_cycle_summary()
        hero_bytes = build_animated_hero_gif(
            summary=summary,
            team_name=summary["team_name"],
            league_name=summary["league_name"],
            trigger_label=summary["trigger_phase"],
        )
        pitch_bytes = build_animated_pitch_gif(
            lineup=summary["plan"]["lineup"],
            jornada_num=summary["target_jornada_num"],
        )

        self.assertIsNotNone(hero_bytes)
        self.assertIsNotNone(pitch_bytes)
        self.assertTrue(hero_bytes.startswith(b"GIF8"))
        self.assertTrue(pitch_bytes.startswith(b"GIF8"))
        self.assertLess(len(hero_bytes), 250_000)
        self.assertLess(len(pitch_bytes), 250_000)

        with Image.open(io.BytesIO(hero_bytes)) as im_hero:
            self.assertGreater(getattr(im_hero, "n_frames", 1), 1)
            self.assertEqual(im_hero.size, (680, 176))
        with Image.open(io.BytesIO(pitch_bytes)) as im_pitch:
            self.assertGreater(getattr(im_pitch, "n_frames", 1), 1)
            self.assertEqual(im_pitch.size, (680, 276))

    def test_json_llm_responses_render_as_clean_html_not_raw_braces(self):
        summary = _sample_cycle_summary()
        html_out = NotificationHub._build_email_html(
            summary=summary,
            pending=[],
            team_name=summary["team_name"],
            league_name=summary["league_name"],
            trigger_label="MORNING_FULL",
            phase_name="OPEN_TRADING",
            inline_cids={"hero_banner", "tactical_pitch"},
        )
        self.assertIn("cid:hero_banner", html_out)
        self.assertIn("cid:tactical_pitch", html_out)
        self.assertIn("Directriz Estratégica Vigente", html_out)
        self.assertIn("1. Diagnóstico Financiero, Fase y Escasez", html_out)
        self.assertIn("Jugador Venta", html_out)
        self.assertNotIn("&quot;resumen&quot;", html_out)
        self.assertNotIn("diagnostico_financiero_fase_y_escasez", html_out)
        self.assertNotIn("<p style='margin:0 0 8px 0;color:#e2e8f0;font-size:13.5px;line-height:1.62;'>{</p>", html_out)

    def test_dispatch_cycle_report_attaches_inline_cids_in_mime_related(self):
        summary = _sample_cycle_summary()
        sent_messages = []

        mock_smtp_instance = MagicMock()
        mock_smtp_instance.sendmail.side_effect = lambda sender, rcpts, raw_msg: sent_messages.append(raw_msg)
        mock_smtp_cm = MagicMock()
        mock_smtp_cm.__enter__.return_value = mock_smtp_instance

        from types import SimpleNamespace

        fake_creds = SimpleNamespace(
            GMAIL_ADDRESS="bot@example.com",
            GMAIL_PASSWORD="app-secret",
            NOTIFY_EMAIL_TO="user@example.com",
            TELEGRAM_BOT_TOKEN="",
            TELEGRAM_ALLOWED_CHAT_ID="",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "notifications.jsonl")
            hub = NotificationHub(log_path=log_path)

            with (
                patch("src.tools.notifications.notifier.Credentials", fake_creds),
                patch("src.tools.notifications.notifier.smtplib.SMTP_SSL", return_value=mock_smtp_cm),
            ):
                res = hub.dispatch_cycle_report(summary, pending_decisions=[], trigger_label="MORNING_FULL")

        self.assertTrue(res["email"]["sent"])
        self.assertEqual(len(sent_messages), 1)
        parsed = message_from_string(sent_messages[0])
        self.assertEqual(parsed.get_content_type(), "multipart/related")
        cids = []
        content_types = []
        for part in parsed.walk():
            content_types.append(part.get_content_type())
            cid = part.get("Content-ID")
            if cid:
                cids.append(cid)
        self.assertIn("multipart/alternative", content_types)
        self.assertIn("text/plain", content_types)
        self.assertIn("text/html", content_types)
        self.assertIn("<hero_banner>", cids)
        self.assertIn("<tactical_pitch>", cids)

    def test_rival_negotiations_card_and_buyer_badge_render_in_email_html(self):
        summary = _sample_cycle_summary()
        summary["plan"]["all_sales"][0].update({
            "buyer_name": "RivalBuyer",
            "is_rival_buyer": True,
            "machine_offer_amount": 1_850_000,
            "rival_premium_eur": 300_000,
        })
        summary["rival_negotiations"] = {
            "incoming_rival_offers": [
                {
                    "offer_id": 111,
                    "player_id": 1083,
                    "nombre": "Jugador Venta",
                    "from_name": "RivalBuyer",
                    "amount": 2_150_000,
                    "market_price": 1_900_000,
                    "machine_amount": 1_850_000,
                    "rival_premium_eur": 300_000,
                    "over_vm_pct": 13.2,
                    "action_label": "✅ Oferta aceptada (NPV_SELL > NPV_HOLD)",
                }
            ],
            "counter_offers": [
                {
                    "offer_id": 222,
                    "player_id": 1952,
                    "nombre": "Lateral Caro",
                    "from_name": "RivalSeller",
                    "amount": 5_666_667,
                    "market_price": 4_280_000,
                    "subida_24h": -120_000,
                    "over_vm_pct": 32.4,
                    "verdict_label": "⛔ Sobreprecio (+32.4% vs VM) — Conviene rechazar",
                }
            ],
            "outgoing_bids": [
                {
                    "offer_id": 333,
                    "player_id": 6837,
                    "nombre": "Medio Abierto",
                    "to_name": "OtroRival",
                    "amount": 4_940_000,
                    "over_vm_pct": 0.8,
                    "status_label": "Estancada (6d · bloquea margen)",
                }
            ],
            "total_locked_credit_eur": 4_940_000,
        }

        html_out = NotificationHub._build_email_html(
            summary=summary,
            pending=[],
            team_name=summary["team_name"],
            league_name=summary["league_name"],
            trigger_label="MORNING_FULL",
            phase_name="OPEN_TRADING",
            inline_cids=set(),
        )
        self.assertIn("Negociaciones y Ofertas con Rivales", html_out)
        self.assertIn("RivalBuyer", html_out)
        self.assertIn("+300,000 € vs Máquina", html_out)
        self.assertIn("Lateral Caro", html_out)
        self.assertIn("Contraofertas Recibidas", html_out)
        self.assertIn("/cancelar_puja 333", html_out)

    def test_portfolio_optimizer_never_self_bids_on_own_sold_players(self):
        import pandas as pd
        from src.tools.strategy_engine.portfolio_optimizer import PortfolioOptimizer

        own_pid = 1083
        free_pid = 9999
        squad_rows = []
        # Minimal valid 12-man squad including an underperforming own player eligible for sale
        pos_list = ["Portero"] + ["Defensa"] * 4 + ["Centrocampista"] * 4 + ["Delantero"] * 3
        for idx in range(12):
            pid = own_pid if idx == 5 else (1000 + idx)
            squad_rows.append({
                "player_id": pid,
                "nombre": f"Squad_{pid}",
                "posicion": pos_list[idx],
                "equipo": f"Team_{idx}",
                "estado": "ok",
                "precio": 3_460_000 if pid == own_pid else 2_000_000,
                "subida_24h": -80_000 if pid == own_pid else 20_000,
                "precio_compra": 3_130_000 if pid == own_pid else 1_800_000,
                "acquired_date": "2026-10-01",
                "clausula_actual": 6_920_000,
                "propietario_liga": "Mi Equipo",
            })
        squad_df = pd.DataFrame(squad_rows)

        free_row = {
            "player_id": free_pid,
            "nombre": "Fichaje Libre",
            "posicion": "Centrocampista",
            "equipo": "Team_X",
            "estado": "ok",
            "precio": 3_200_000,
            "subida_24h": 60_000,
            "precio_compra": 0,
            "acquired_date": "",
            "clausula_actual": 0,
            "propietario_liga": "Mercado",
        }
        df_market_all = pd.concat([squad_df, pd.DataFrame([free_row])], ignore_index=True)

        xp_rows = []
        for idx, r in df_market_all.iterrows():
            pid = int(r["player_id"])
            xp_val = 2.0 if pid == own_pid else (6.2 if pid == free_pid else 4.0)
            xp_rows.append({
                "player_id": pid,
                "nombre": r["nombre"],
                "posicion": r["posicion"],
                "xp_total": xp_val,
                "xp_healthy": xp_val,
                "p_start": 0.90,
                "is_available": True,
            })
        xp_table = pd.DataFrame(xp_rows)

        # Even if market listings mislabeled our own listed player as seller_name="Mercado",
        # PortfolioOptimizer must NEVER re-bid on own original squad IDs!
        listings_df = pd.DataFrame([
            {"player_id": own_pid, "jugador": f"Squad_{own_pid}", "seller_name": "Mercado", "precio_mercado_dia": 3_460_000, "ask_price": 3_560_000},
            {"player_id": free_pid, "jugador": "Fichaje Libre", "seller_name": "Mercado", "precio_mercado_dia": 3_200_000, "ask_price": 3_200_000},
        ])

        opt = PortfolioOptimizer()
        plan = opt.generate_daily_plan(
            current_date="2026-11-13",
            my_manager_name="Mi Equipo",
            current_cash=9_000_000.0,
            squad_df=squad_df,
            df_market_all=df_market_all,
            market_listings_df=listings_df,
            xp_table=xp_table,
            efr_map={own_pid: -150_000.0, free_pid: 180_000.0},
            active_offers_map={own_pid: 3_800_000.0},
            is_kickoff_day=False,
            days_to_kickoff=7,
        )
        bid_pids = {int(b["player_id"]) for b in (plan.get("market_bids") or [])}
        self.assertNotIn(own_pid, bid_pids)

    def test_auction_model_skips_overpriced_rival_showcase_listings(self):
        from src.tools.strategy_engine.auction_model import AuctionBidModel

        model = AuctionBidModel()
        res = model.compute_optimal_bid(
            player_id=37488,
            market_price=3_110_000,
            asking_price=6_666_666,
            subida_24h=80_000,
            efr_72h=80_000,
            delta_xp_xi=3.93,
            p_start=0.90,
            seller_is_rival=True,
            rival_seller_in_debt=True,
            idle_cash_eur=10_000_000,
            overbid_cap_mult=1.34,
        )
        self.assertFalse(res["should_bid"])
        self.assertEqual(res["regime"], "SKIP_OVERPRICED_RIVAL")

    def test_rival_negotiation_cleanup_frees_credit_before_clause_and_bid(self):
        from unittest.mock import MagicMock
        from src.tools.strategy_engine.autopilot import AutopilotRunner
        from src.tools.strategy_engine.league_rules import RoundPhaseContext
        import pandas as pd

        runner = AutopilotRunner.__new__(AutopilotRunner)
        runner.mode = "FULL_AUTO"
        runner.dry_run = True
        runner.trigger_phase = "MORNING_FULL"
        runner.rules = MagicMock(squad_max_players=22)

        market_actions = MagicMock()
        market_actions.reject_offer.side_effect = lambda oid: {"status": "DRY_RUN_SIMULATED", "action": "reject", "id": oid}
        market_actions.cancel_offer.side_effect = lambda oid: {"status": "DRY_RUN_SIMULATED", "action": "cancel", "id": oid}

        ctx = {
            "squad_df": pd.DataFrame([{"player_id": 1}]),
            "rival_incoming_offers": [],
            "incoming_counter_offers": [
                {
                    "offer_id": 9001,
                    "player_id": 1952,
                    "nombre": "Cancelo Test",
                    "from_name": "RivalTest",
                    "amount": 5_666_667,
                    "market_price": 4_280_000,
                    "subida_24h": -120_000,
                    "age_hours": 3.0,
                }
            ],
            "outgoing_bids": [
                {
                    "offer_id": 8001,
                    "player_id": 1952,
                    "nombre": "Cancelo Test",
                    "to_name": "RivalTest",
                    "amount": 5_500_000,
                    "market_price": 4_280_000,
                    "age_hours": 36.0,
                },
                {
                    "offer_id": 8002,
                    "player_id": 6837,
                    "nombre": "Soler Test",
                    "to_name": "OtroRival",
                    "amount": 4_900_000,
                    "market_price": 4_900_000,
                    "age_hours": 144.0,
                },
            ],
        }
        approved_plan = {"all_sales": [], "routine_sales": [], "lineup": {"slots": []}}
        xp_table = pd.DataFrame([{"player_id": 1952, "xp_best": 5.0, "best_line": "DF", "is_available": True}])

        rn = runner._evaluate_rival_negotiations(ctx=ctx, approved_plan=approved_plan, xp_table=xp_table)
        phase_ctx = RoundPhaseContext(
            now_ts=pd.Timestamp("2026-11-15 10:00:00"),
            phase_name="OPEN_TRADING",
            target_jornada_num=13,
            active_in_progress_jornada=None,
            next_kickoff_ts=pd.Timestamp("2026-11-20 21:00:00"),
            clause_lock_ts=pd.Timestamp("2026-11-18 21:00:00"),
            active_round_end_ts=None,
            hours_to_kickoff=120.0,
            days_to_kickoff=5,
            hours_to_clause_lock=72.0,
            is_kickoff_day=False,
            must_be_solvent_now=False,
            clauses_open=True,
            can_use_credit_leverage=True,
            is_international_break=False,
            is_schedule_placeholder=False,
        )
        exec_results = []
        queued = []
        cleanup = runner._execute_rival_negotiations_cleanup(
            rival_negotiations=rn,
            ctx=ctx,
            approved_plan=approved_plan,
            market_actions=market_actions,
            phase_ctx=phase_ctx,
            base_max_bid=5_000_000,
            execution_results=exec_results,
            queued_decisions=queued,
        )
        self.assertEqual(cleanup["freed_credit_eur"], 10_400_000)
        self.assertEqual(rn["freed_credit_eur"], 10_400_000)
        self.assertEqual(rn["remaining_locked_credit_eur"], 0)
        market_actions.reject_offer.assert_called_once_with(9001)
        self.assertEqual(market_actions.cancel_offer.call_count, 2)

    def test_guardrails_projected_cash_tracks_auto_vs_semi_auto_sales(self):
        from src.tools.coach_analytic.guardrails import validate_autopilot_plan_v2

        plan = {
            "starting_cash": 9_000_000,
            "clauses_open": True,
            "all_sales": [
                {"player_id": 1, "nombre": "RoutineA", "sale_price": 2_000_000, "requires_human_approval": False},
                {"player_id": 2, "nombre": "ImportantB", "sale_price": 4_000_000, "requires_human_approval": True},
            ],
            "routine_sales": [],
            "solvency_sales": [],
            "clause_snipes": [],
            "market_bids": [],
            "defensive_shields": [],
            "preventive_listings": [],
        }
        semi = validate_autopilot_plan_v2(plan, squad_value=50_000_000, unlocked_collateral=10_000_000, auto_execute_important_sales=False)
        full = validate_autopilot_plan_v2(plan, squad_value=50_000_000, unlocked_collateral=10_000_000, auto_execute_important_sales=True)
        self.assertEqual(semi["projected_cash"], 11_000_000)
        self.assertEqual(semi["projected_cash_with_pending_sales"], 15_000_000)
        self.assertEqual(full["projected_cash"], 15_000_000)

    def test_league_bulletin_renders_clauses_deals_standings_and_medical_html(self):
        summary = _sample_cycle_summary()
        summary["rival_negotiations"] = {
            "incoming_rival_offers": [],
            "counter_offers": [],
            "outgoing_bids": [],
            "freed_credit_eur": 10_400_000,
            "effective_available_max_bid": 20_000_000,
        }
        summary["league_bulletin"] = {
            "standings_top": [
                {"rank": 1, "name": "LiderFC", "points": 640, "is_me": False},
                {"rank": 2, "name": "RivalTop", "points": 620, "is_me": False},
                {"rank": 3, "name": "TercerFC", "points": 590, "is_me": False},
            ],
            "my_standings": {
                "rank": 10,
                "total_managers": 12,
                "points": 469,
                "leader_name": "LiderFC",
                "points_to_leader": 171,
                "next_rival_above": "NovatoFC",
                "points_to_next": 12,
            },
            "latest_round": {
                "round_name": "Jornada 12",
                "top_scorer_name": "RivalTop",
                "top_points": 68,
                "top_bonus": 4_250_000,
                "my_points": 42,
            },
            "clauses": [
                {
                    "nombre": "El Hilali",
                    "pos": "DF",
                    "from_name": "Equipo Alpha",
                    "to_name": "RivalUnit",
                    "amount": 6_560_000,
                    "involves_me": True,
                }
            ],
            "manager_deals": [
                {
                    "deal_type": "MARKET_AUCTION",
                    "nombre": "Óskarsson",
                    "from_name": "Mercado",
                    "to_name": "OtroManager",
                    "amount": 4_789_056,
                    "bid_count": 3,
                }
            ],
            "clause_shields": [
                {"user_name": "RivalUnit", "nombre": "Ez Abde", "release_clause": 18_000_000}
            ],
            "position_changes": [
                {"nombre": "Jugador X", "from_pos": "DF", "to_pos": "MF"}
            ],
            "injury_alerts": [
                {"nombre": "Estrella Baja", "team": "Club Test", "status": "injured", "price": 12_000_000}
            ],
            "market_risers": [{"nombre": "SubidonFC", "subida_24h": 210_000}],
            "market_fallers": [{"nombre": "BajonFC", "subida_24h": -190_000}],
        }
        html_out = NotificationHub._build_email_html(
            summary=summary,
            pending=[],
            team_name=summary["team_name"],
            league_name=summary["league_name"],
            trigger_label="MORNING_FULL",
            phase_name="OPEN_TRADING",
            inline_cids=set(),
        )
        self.assertIn("Boletín de la Liga — Últimas Noticias", html_out)
        self.assertIn("TE AFECTA", html_out)
        self.assertIn("El Hilali", html_out)
        self.assertIn("6,560,000 €", html_out)
        self.assertIn("superó 2 pujas", html_out)
        self.assertIn("Radar LaLiga", html_out)
        plain_out = NotificationHub._build_plain_text(summary, [], summary["team_name"], summary["league_name"], "MORNING_FULL", "OPEN_TRADING")
        self.assertIn("Boletín de Noticias de tu Liga", plain_out)
        self.assertIn("Clausulazo: RivalUnit pagó 6,560,000 € por El Hilali", plain_out)


if __name__ == "__main__":
    unittest.main()
