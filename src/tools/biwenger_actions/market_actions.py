"""
Biwenger Market Execution Client (`BiwengerMarketActions`)
==========================================================
Single choke-point for every state-mutating call against the Biwenger API:
bids, preventive listings, accepting Computer offers, clause snipes, clause
shields and lineup updates.

Safety model:
- `dry_run=True` by default: NOTHING is sent; every intended action is logged.
- Every action (dry or live) is appended to `data/autopilot/execution_audit.jsonl`.
- Live calls honour `AUTOPILOT_MODE` permissions decided by the caller (`AutopilotRunner`).

IMPORTANT: Biwenger has no public API. The write endpoints below follow the
payloads used by the official web client as documented by the community. They
MUST be validated once in a supervised live session (`SEMI_AUTO`, single action)
before switching to `FULL_AUTO`. `ENDPOINTS_VERIFIED` tracks which ones have been
confirmed against this league.
"""

import json
import os
import datetime
from typing import Any, Dict, List, Optional

import requests

from src.tools.coach_analytic.actions import LineupActions

API_BASE = "https://biwenger.as.com/api/v2"
DEFAULT_AUDIT_PATH = "./data/autopilot/execution_audit.jsonl"

# Flip to True per endpoint after a supervised live confirmation.
ENDPOINTS_VERIFIED: Dict[str, bool] = {
    "set_lineup": True,          # Used in production since the 006 project
    "place_bid": False,
    "list_player": False,
    "unlist_player": False,
    "accept_offer": True,        # Verified live on 2026-10-30
    "pay_clause": False,
    "raise_clause": False,
}


class BiwengerMarketActions:
    """Audited, dry-run-first execution client for Biwenger market operations."""

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        league_id: Optional[int] = None,
        user_id: Optional[int] = None,
        dry_run: bool = True,
        audit_path: str = DEFAULT_AUDIT_PATH,
        allow_unverified: bool = False,
    ):
        self.session = session
        self.league_id = league_id
        self.user_id = user_id
        self.dry_run = bool(dry_run) or session is None
        self.audit_path = audit_path
        self.allow_unverified = allow_unverified
        os.makedirs(os.path.dirname(self.audit_path) or ".", exist_ok=True)
        if self.session is not None:
            if league_id is not None:
                self.session.headers.update({"X-League": str(league_id)})
            if user_id is not None:
                self.session.headers.update({"X-User": str(user_id)})

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _audit(self, action: str, payload: Dict[str, Any], status: str, detail: str = "") -> Dict[str, Any]:
        entry = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "action": action,
            "dry_run": self.dry_run,
            "status": status,
            "detail": detail,
            "payload": payload,
        }
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry

    def _execute(self, action: str, method: str, url: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body = payload or {}
        if self.dry_run:
            return self._audit(action, {"method": method, "url": url, "body": body}, "DRY_RUN")
        if not ENDPOINTS_VERIFIED.get(action, False) and not self.allow_unverified:
            return self._audit(
                action,
                {"method": method, "url": url, "body": body},
                "BLOCKED_UNVERIFIED",
                "Endpoint not yet confirmed live; run once supervised and set ENDPOINTS_VERIFIED.",
            )
        try:
            resp = self.session.request(method, url, json=body if method != "DELETE" else None, timeout=20)
            ok = 200 <= resp.status_code < 300
            return self._audit(
                action,
                {"method": method, "url": url, "body": body},
                "OK" if ok else f"HTTP_{resp.status_code}",
                resp.text[:500],
            )
        except requests.RequestException as e:
            return self._audit(action, {"method": method, "url": url, "body": body}, "ERROR", str(e))

    # ------------------------------------------------------------------ #
    # Public actions
    # ------------------------------------------------------------------ #
    def place_bid(self, player_id: int, amount: int, seller_user_id: Optional[int] = None) -> Dict[str, Any]:
        """Sealed bid on a Computer listing (`to=None`) or direct offer to a rival (`to=user_id`)."""
        payload = {
            "to": seller_user_id,
            "type": "purchase",
            "amount": int(amount),
            "requestedPlayers": [int(player_id)],
        }
        return self._execute("place_bid", "POST", f"{API_BASE}/offers", payload)

    def list_player(self, player_id: int, asking_price: int) -> Dict[str, Any]:
        """Lists an owned player on the market (0 € cost) to receive Computer +-5% offers."""
        payload = {"type": "sell", "player": int(player_id), "price": int(asking_price)}
        return self._execute("list_player", "POST", f"{API_BASE}/market", payload)

    def unlist_player(self, player_id: int) -> Dict[str, Any]:
        return self._execute("unlist_player", "DELETE", f"{API_BASE}/market?player={int(player_id)}")

    def accept_offer(self, offer_id: int) -> Dict[str, Any]:
        """Accepts a firm received offer (Computer or rival)."""
        return self._execute("accept_offer", "PUT", f"{API_BASE}/offers/{int(offer_id)}", {"status": "accepted"})

    def pay_clause(self, player_id: int, amount: int, owner_user_id: int) -> Dict[str, Any]:
        """Instant release-clause purchase from a rival."""
        payload = {
            "to": int(owner_user_id),
            "type": "clause",
            "amount": int(amount),
            "requestedPlayers": [int(player_id)],
        }
        return self._execute("pay_clause", "POST", f"{API_BASE}/offers", payload)

    def raise_clause(self, player_id: int, increment_eur: int) -> Dict[str, Any]:
        """Defensive shield: invests cash to raise our own player's clause (`clauseIncrement=1`)."""
        payload = {"amount": int(increment_eur)}
        return self._execute("raise_clause", "PUT", f"{API_BASE}/players/{int(player_id)}/clause", payload)

    def set_lineup(self, formation: str, ordered_player_ids: List[int]) -> Dict[str, Any]:
        payload = {"formation": formation, "player_ids": [int(p) for p in ordered_player_ids]}
        if self.dry_run:
            return self._audit("set_lineup", payload, "DRY_RUN")
        ok = LineupActions(self.session, self.league_id, self.user_id).set_lineup(formation, payload["player_ids"])
        return self._audit("set_lineup", payload, "OK" if ok else "FAILED")
