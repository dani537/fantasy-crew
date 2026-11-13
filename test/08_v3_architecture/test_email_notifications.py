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


if __name__ == "__main__":
    unittest.main()
