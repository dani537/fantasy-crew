"""
90-Second Plug-and-Play Setup Wizard (`python -m src.setup_wizard`)
===================================================================
Makes onboarding for ANY Biwenger player effortlessly easy:
- `python -m src.setup_wizard`: Interactive CLI assistant that logs into Biwenger,
  lists your leagues, auto-discovers all official rules (`scoreID`, `clauseRanges`,
  `clauseRoundDisabledHours`, `marketMaxUserSales`, `maximumBid`), configures Email
  & Telegram (even auto-detecting your `TELEGRAM_ALLOWED_CHAT_ID`!), and writes `.env`.
- `python -m src.setup_wizard --check`: Non-interactive health & configuration audit.
"""

import os
import json
import argparse
import getpass
from typing import Dict, Any, List
import requests

from src.config import (
    Credentials,
    GeneralSettings,
    StrategySettings,
    GoogleSheetsConfig,
    resolve_active_user_identity,
)
from src.tools.strategy_engine.league_rules import LeagueRulesEngine


API_BASE = "https://biwenger.as.com/api/v2"


def run_health_check() -> bool:
    """Audits current `.env`, auto-discovered league rules, notifications, and database readiness."""
    print("=" * 72)
    print("🩺 FANTASY CHAMPION v3 — DIAGNÓSTICO DE CONFIGURACIÓN MULTI-USUARIO")
    print("=" * 72)

    missing = Credentials.validate(require_llm=False)
    ident = resolve_active_user_identity()
    rules = LeagueRulesEngine.load()

    print(f"👤 Mánager Detectado : {ident.get('team_name')} (team_id={ident.get('team_id')})")
    print(f"🏆 Liga Detectada    : {ident.get('league_name')} (league_id={ident.get('league_id')}, scoreID={rules.score_id})")
    print(f"💶 Saldo Caché/Vivo  : {int(ident['balance']):,} €" if ident.get("balance") is not None else "💶 Saldo Caché/Vivo  : Pendiente de 1ª sincronización")
    print("-" * 72)
    print("⚖️  REGLAS OFICIALES DE LIGA AUTODESCUBIERTAS:")
    print(f"   • Línea de crédito permitida      : {int(rules.credit_limit_ratio * 100)}% del valor de plantilla")
    print(f"   • Ventas simultáneas máx. usuario : {rules.market_max_user_sales} jugadores (marketMaxUserSales)")
    print(f"   • Jugadores libres diarios mercado: {rules.market_free_slots} huecos (marketSize)")
    print(f"   • Bloqueo cláusulas pre-jornada   : {rules.clause_disabled_hours}h antes del 1er partido")
    print(f"   • Inmunidad cláusula post-compra  : {rules.clause_activation_delay_days} días")
    print(f"   • Ratio blindaje de cláusula      : 1 € caja = +{rules.clause_increment_ratio:g} € cláusula (reembolso {int(rules.clause_decrement_refund_pct*100)}%)")
    print(f"   • Multiposición activa            : {'Sí' if rules.lineup_multipos else 'No'} | Prima/punto: {rules.bonus_per_point_eur:,} €")
    print("-" * 72)
    print("🤖 GOBERNANZA AUTOPILOT & CANALES:")
    print(f"   • Modo Autopilot                  : {StrategySettings.AUTOPILOT_MODE} (DryRun={GeneralSettings.DRY_RUN})")
    if StrategySettings.AUTOPILOT_MODE == "FULL_AUTO":
        print("   • Autonomía de Ejecución          : ⚡ 100% AUTÓNOMO bajo Guardrails v3 (Once, Pujas, Ventas, Solvencia, Cláusulas y Blindajes)")
    elif StrategySettings.AUTOPILOT_MODE == "SEMI_AUTO":
        print(f"   • Pujas autónomas en SEMI_AUTO    : {'✅ Sí (notificando)' if StrategySettings.AUTONOMOUS_BIDS_IN_SEMI_AUTO else '❌ No'}")
        print(f"   • Ventas menores autónomas        : {'✅ Sí (< ' + format(StrategySettings.IMPORTANT_SALE_MIN_EUR, ',') + ' €)' if StrategySettings.AUTONOMOUS_ROUTINE_SALES_IN_SEMI_AUTO else '❌ No'}")
        print("   • Cláusulas y Ventas Importantes  : 🛡️ SIEMPRE confirmación humana (/aprobar en Telegram / Sheets)")
    else:
        print("   • Autonomía de Ejecución          : 🧪 DRY_RUN (solo simulación, 0 mutaciones en Biwenger)")
    print(f"   • Proveedor IA (LLM)              : {GeneralSettings.LLM_PROVIDER} ({GeneralSettings.LLM_MODEL_DAILY}) -> {'✅ Configurado' if Credentials.get_llm_api_key() else '⚠️ Sin API Key (usará briefing sintético)'}")
    print(f"   • Notificaciones Email HTML       : {'✅ Activo (' + Credentials.NOTIFY_EMAIL_TO + ')' if (Credentials.GMAIL_ADDRESS and Credentials.GMAIL_PASSWORD) else '⚪ Opcional no configurado'}")
    print(f"   • Bot Telegram Bidireccional      : {'✅ Activo (chat_id=' + Credentials.TELEGRAM_ALLOWED_CHAT_ID + ')' if (Credentials.TELEGRAM_BOT_TOKEN and Credentials.TELEGRAM_ALLOWED_CHAT_ID) else ('🟡 Token presente, falta TELEGRAM_ALLOWED_CHAT_ID' if Credentials.TELEGRAM_BOT_TOKEN else '⚪ Opcional no configurado')}")
    print(f"   • Google Sheets Cloud Sync        : {'✅ Activo (' + GoogleSheetsConfig.SHEET_ID[:10] + '...)' if GoogleSheetsConfig.ENABLED else '⚪ Modo 100% Local SQLite (Google Sheets opcional)'}")
    print("=" * 72)

    if missing:
        print(f"❌ Faltan credenciales obligatorias de Biwenger: {', '.join(missing)}")
        print("👉 Ejecuta `python -m src.setup_wizard` para configurarlo en 1 minuto.")
        return False

    print("✅ Configuración lista para operar.")
    return True


def run_interactive_wizard() -> None:
    print("=" * 72)
    print("🚀 ASISTENTE DE CONFIGURACIÓN RÁPIDA — FANTASY CHAMPION v3")
    print("   Configura tu liga Biwenger en 90 segundos (cero código, cero líos).")
    print("=" * 72)

    env_values: Dict[str, str] = {}

    # 1. Biwenger Login
    print("\n[Paso 1/4] Credenciales de Biwenger")
    email = input("  • Email de tu cuenta Biwenger: ").strip()
    pwd = getpass.getpass("  • Contraseña de Biwenger (no se muestra al escribir): ").strip()
    token = ""
    if not email or not pwd:
        token = input("  • (Alternativa) Pega tu BIWENGER_TOKEN Bearer: ").strip()
    else:
        env_values["BIWENGER_USERNAME"] = email
        env_values["BIWENGER_PASSWORD"] = pwd

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "X-Lang": "es",
        "X-Version": "629",
    })

    if email and pwd:
        r_login = session.post(f"{API_BASE}/auth/login", json={"email": email, "password": pwd}, timeout=15)
        if r_login.status_code == 200 and r_login.json().get("token"):
            token = r_login.json()["token"]
            print("  ✅ Login en Biwenger correcto.")
        else:
            print(f"  ⚠️ No se pudo iniciar sesión automáticamente (HTTP {r_login.status_code}).")
            token = input("  • Si tienes un BIWENGER_TOKEN manual, pégalo aquí (o Enter para continuar): ").strip()

    if token:
        env_values["BIWENGER_TOKEN"] = token
        session.headers.update({"Authorization": f"Bearer {token}"})
        r_acc = session.get(f"{API_BASE}/account", timeout=15)
        if r_acc.status_code == 200:
            leagues: List[Dict[str, Any]] = (r_acc.json().get("data") or {}).get("leagues") or []
            if leagues:
                print("\n  🏆 Ligas encontradas en tu cuenta:")
                for idx, lg in enumerate(leagues, 1):
                    usr = lg.get("user") or {}
                    print(f"     [{idx}] {lg.get('name')} (ID Liga: {lg.get('id')} | Tu Equipo: {usr.get('name')} | ID Equipo: {usr.get('id')})")
                choice = input(f"  • Elige el número de liga [1-{len(leagues)}] (Enter = 1): ").strip()
                c_idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(leagues) else 0
                picked = leagues[c_idx]
                p_user = picked.get("user") or {}
                lid = int(picked.get("id", 0))
                uid = int(p_user.get("id", 0))
                env_values["BIWENGER_LEAGUE_ID"] = str(lid)
                env_values["BIWENGER_USER_ID"] = str(uid)

                # Fetch & cache full league rules automatically!
                session.headers.update({"X-League": str(lid), "X-User": str(uid)})
                r_set = session.get(f"{API_BASE}/league/{lid}?include=all,-lastAccess&fields=*,standings,settings", timeout=15)
                if r_set.status_code == 200:
                    lg_full = r_set.json().get("data") or {}
                    os.makedirs("./data/raw", exist_ok=True)
                    with open("./data/raw/live_league_settings.json", "w", encoding="utf-8") as f:
                        json.dump(lg_full, f, ensure_ascii=False, indent=2)
                    with open("./data/raw/live_league_profile.json", "w", encoding="utf-8") as fp:
                        json.dump({
                            "league_id": lid,
                            "league_name": picked.get("name", ""),
                            "team_id": uid,
                            "team_name": p_user.get("name", ""),
                            "score_id": lg_full.get("scoreID", 5),
                        }, fp, ensure_ascii=False, indent=2)
                    parsed_rules = LeagueRulesEngine.from_api_payload(lg_full, apply_env_overrides=False)
                    env_values["SCORE_TYPE"] = str(parsed_rules.score_id)
                    print(
                        f"  ✅ Reglas autodescubiertas para '{picked.get('name')}': "
                        f"scoreID={parsed_rules.score_id}, máxVentas={parsed_rules.market_max_user_sales}, "
                        f"bloqueoCláusulas={parsed_rules.clause_disabled_hours}h, crédito={int(parsed_rules.credit_limit_ratio*100)}%."
                    )

    # 2. AI Key
    print("\n[Paso 2/4] Clave de Inteligencia Artificial (para Briefing y Chat de Telegram)")
    gemini_key = input("  • GEMINI_API_KEY (gratis en Google AI Studio, recomendado - Enter para saltar): ").strip()
    if gemini_key:
        env_values["GEMINI_API_KEY"] = gemini_key
    else:
        or_key = input("  • OPENROUTER_API_KEY (opcional - Enter para saltar): ").strip()
        if or_key:
            env_values["OPENROUTER_API_KEY"] = or_key

    # 3. Email & Telegram
    print("\n[Paso 3/4] Notificaciones Matutinas (Email HTML + Bot de Telegram)")
    gmail = input("  • Tu correo Gmail para recibir informes diarios (opcional, Enter para saltar): ").strip()
    if gmail:
        gpwd = getpass.getpass("  • Contraseña de Aplicación de Gmail (16 letras de Google App Passwords): ").strip()
        env_values["GMAIL_ADDRESS"] = gmail
        env_values["NOTIFY_EMAIL_TO"] = gmail
        if gpwd:
            env_values["GMAIL_PASSWORD"] = gpwd

    tg_token = input("  • TELEGRAM_BOT_TOKEN de @BotFather (opcional, Enter para saltar): ").strip()
    if tg_token:
        env_values["TELEGRAM_BOT_TOKEN"] = tg_token
        tg_chat = input("  • Tu TELEGRAM_ALLOWED_CHAT_ID (si no lo sabes, envía 'Hola' a tu bot ahora y pulsa Enter): ").strip()
        if not tg_chat:
            try:
                r_upd = requests.get(f"https://api.telegram.org/bot{tg_token}/getUpdates", timeout=10)
                res_list = (r_upd.json().get("result") or []) if r_upd.status_code == 200 else []
                if res_list:
                    last_msg = res_list[-1].get("message") or {}
                    tg_chat = str((last_msg.get("chat") or {}).get("id", "")).strip()
                    if tg_chat:
                        print(f"  ✅ ¡Chat ID autodetectado!: {tg_chat}")
            except Exception:
                pass
        if tg_chat:
            env_values["TELEGRAM_ALLOWED_CHAT_ID"] = tg_chat

    # 4. Mode selection
    print("\n[Paso 4/4] Nivel de Autonomía")
    print("  • SEMI_AUTO (Recomendado): El bot gestiona once, escaparate Top-5, pujas y solvencia pre-jornada,")
    print("    pero SIEMPRE te pide confirmación para cláusulas y ventas importantes.")
    print("  • DRY_RUN: Solo simula e informa sin tocar nada en Biwenger.")
    mode_in = input("  • Elige modo [SEMI_AUTO / DRY_RUN / FULL_AUTO] (Enter = SEMI_AUTO): ").strip().upper()
    env_values["AUTOPILOT_MODE"] = mode_in if mode_in in ("SEMI_AUTO", "DRY_RUN", "FULL_AUTO") else "SEMI_AUTO"

    # Write .env preserving any unprompted existing keys if desired
    lines = [
        "# Generado automáticamente por `python -m src.setup_wizard` (Fantasy Champion v3)",
    ]
    for k, v in env_values.items():
        lines.append(f'{k}="{v}"')

    env_path = ".env"
    if os.path.exists(env_path):
        overwrite = input("\n⚠️ Ya existe un archivo `.env`. ¿Deseas sobrescribirlo? [s/N]: ").strip().lower()
        if overwrite not in ("s", "si", "sí", "y", "yes"):
            env_path = ".env.generated"
            print(f"ℹ️ Guardando en `{env_path}` para no pisar tu `.env` actual.")

    with open(env_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n🎉 ¡Listo! Configuración guardada en `{env_path}`.")
    print("👉 Prueba tu primera simulación segura con: `python -m src.tools.strategy_engine.autopilot --mode DRY_RUN`")


def main():
    parser = argparse.ArgumentParser(description="Fantasy Champion v3 — Setup Wizard & Health Check")
    parser.add_argument("--check", action="store_true", help="Run non-interactive configuration & rules audit")
    args = parser.parse_args()

    if args.check:
        ok = run_health_check()
        raise SystemExit(0 if ok else 1)
    run_interactive_wizard()


if __name__ == "__main__":
    main()
