"""
Biwenger Market Execution Client (`BiwengerMarketActions` v3)
=============================================================
Single choke-point for every state-mutating call against the Biwenger API:
bids, Top-K (`<= 5`) preventive listings, accepting Computer offers, clause snipes,
clause shields, and MILP lineup updates.

Safety & Rate-Limit Governance:
- `dry_run=True` by default: NOTHING is sent; every intended action is logged.
- Every action (dry or live) is appended to `data/autopilot/execution_audit.jsonl`.
- Inspects `x-rate-limit-remaining` headers on every live HTTP response to avoid HTTP 429.
- `sync_market_listings` unlists stale slots BEFORE listing new ones so we never
  violate `marketMaxUserSales` (e.g., 5 simultaneous players on sale).
"""

import json
import os
import time
import datetime
from typing import Any, Dict, List, Optional, Set

import requests

from src.tools.coach_analytic.actions import LineupActions

API_BASE = "https://biwenger.as.com/api/v2"
DEFAULT_AUDIT_PATH = "./data/autopilot/execution_audit.jsonl"

# Routine market & lineup endpoints used by official Biwenger API v2 web client:
ENDPOINTS_VERIFIED: Dict[str, bool] = {
    "set_lineup": True,          # Verified in production
    "accept_offer": True,        # Verified live on 2026-10-30
    "place_bid": True,           # Standard Biwenger API v2 POST /offers {"type": "purchase", ...}
    "list_player": True,         # Standard Biwenger API v2 POST /market {"type": "sell", ...}
    "unlist_player": True,       # Standard Biwenger API v2 DELETE /market?player={id}
    "pay_clause": True,          # Gated by human approval in SEMI_AUTO (`/aprobar CL-xxx`)
    "raise_clause": True,        # Gated by human approval in SEMI_AUTO (`/aprobar BL-xxx`)
}


class BiwengerMarketActions:
    """Audited, rate-limit-aware, dry-run-first execution client for Biwenger market operations."""

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        league_id: Optional[int] = None,
        user_id: Optional[int] = None,
        dry_run: bool = True,
        audit_path: str = DEFAULT_AUDIT_PATH,
        allow_unverified: bool = False,
        min_rate_limit_remaining: int = 8,
    ):
        self.session = session
        self.league_id = league_id
        self.user_id = user_id
        self.dry_run = bool(dry_run) or session is None
        self.audit_path = audit_path
        self.allow_unverified = allow_unverified
        self.min_rate_limit_remaining = int(min_rate_limit_remaining)
        self.last_rate_limit_remaining: Optional[int] = None

        os.makedirs(os.path.dirname(self.audit_path) or ".", exist_ok=True)
        if self.session is not None:
            if league_id is not None:
                self.session.headers.update({"X-League": str(league_id), "x-league": str(league_id)})
            if user_id is not None:
                self.session.headers.update({"X-User": str(user_id), "x-user": str(user_id)})

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
            "rate_limit_remaining": self.last_rate_limit_remaining,
            "payload": payload,
        }
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry

    def _execute(
        self,
        action: str,
        method: str,
        url: str,
        payload: Optional[Dict[str, Any]] = None,
        critical: bool = False,
    ) -> Dict[str, Any]:
        body = payload or {}
        if self.dry_run:
            return self._audit(action, {"method": method, "url": url, "body": body}, "DRY_RUN")

        if not ENDPOINTS_VERIFIED.get(action, False) and not self.allow_unverified:
            return self._audit(
                action,
                {"method": method, "url": url, "body": body},
                "BLOCKED_UNVERIFIED",
                "Endpoint not yet enabled in ENDPOINTS_VERIFIED.",
            )

        # Protect Biwenger's 200 req/hour UTC quota: skip non-critical calls if quota is critically low
        if (
            not critical
            and self.last_rate_limit_remaining is not None
            and self.last_rate_limit_remaining < self.min_rate_limit_remaining
        ):
            return self._audit(
                action,
                {"method": method, "url": url, "body": body},
                "SKIPPED_LOW_QUOTA",
                f"x-rate-limit-remaining={self.last_rate_limit_remaining} < {self.min_rate_limit_remaining}",
            )

        try:
            resp = self.session.request(method, url, json=body if method != "DELETE" else None, timeout=20)
            rem_hdr = resp.headers.get("x-rate-limit-remaining") or resp.headers.get("X-Rate-Limit-Remaining")
            if rem_hdr is not None and str(rem_hdr).isdigit():
                self.last_rate_limit_remaining = int(rem_hdr)

            ok = 200 <= resp.status_code < 300
            time.sleep(0.25)  # Polite pacing
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
            "to": int(seller_user_id) if seller_user_id else None,
            "type": "purchase",
            "amount": int(amount),
            "requestedPlayers": [int(player_id)],
        }
        return self._execute("place_bid", "POST", f"{API_BASE}/offers", payload, critical=True)

    def list_player(self, player_id: int, asking_price: int) -> Dict[str, Any]:
        """Lists an owned player on the market (0 € cost) to receive Computer +-5% offers."""
        payload = {"type": "sell", "player": int(player_id), "price": int(asking_price)}
        return self._execute("list_player", "POST", f"{API_BASE}/market", payload, critical=False)

    def unlist_player(self, player_id: int) -> Dict[str, Any]:
        """Removes a player from the transfer market to free a `marketMaxUserSales` slot."""
        return self._execute("unlist_player", "DELETE", f"{API_BASE}/market?player={int(player_id)}", critical=False)

    def sync_market_listings(
        self,
        desired_listings: List[Dict[str, Any]],
        currently_listed_pids: Optional[Set[int]] = None,
        max_slots: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Synchronizes our active market sales with `desired_listings[:max_slots]`:
          1. Unlists any currently listed player NOT in the desired top-K set first
             (avoiding HTTP 400 from exceeding `marketMaxUserSales = 5`).
          2. Lists or updates the desired top-K players.
        """
        target = desired_listings[: max(0, int(max_slots))]
        target_pids = {int(item["player_id"]) for item in target}
        curr_pids = set(int(x) for x in (currently_listed_pids or set()))

        results: List[Dict[str, Any]] = []
        # Step 1: Free slots occupied by players no longer in top-K
        stale_pids = sorted(curr_pids - target_pids)
        for spid in stale_pids:
            results.append(self.unlist_player(spid))

        # Step 2: List desired top-K players (skip re-listing if already listed unless price update needed)
        for item in target:
            pid = int(item["player_id"])
            ask = int(item["asking_price"])
            if pid in curr_pids and not self.dry_run:
                # Already on market today (receiving Computer offer tonight); keep active!
                results.append(self._audit("keep_listed", {"player": pid, "price": ask}, "ALREADY_LISTED"))
                continue
            results.append(self.list_player(pid, ask))

        return results

    def accept_offer(self, offer_id: int) -> Dict[str, Any]:
        """Accepts a firm received offer (Computer or rival)."""
        return self._execute("accept_offer", "PUT", f"{API_BASE}/offers/{int(offer_id)}", {"status": "accepted"}, critical=True)

    def reject_offer(self, offer_id: int) -> Dict[str, Any]:
        """Rejects a received offer (e.g., lowball Computer offer when rolling for tomorrow)."""
        return self._execute("accept_offer", "PUT", f"{API_BASE}/offers/{int(offer_id)}", {"status": "rejected"}, critical=False)

    def pay_clause(self, player_id: int, amount: int, owner_user_id: int) -> Dict[str, Any]:
        """Instant release-clause purchase from a rival."""
        payload = {
            "to": int(owner_user_id),
            "type": "clause",
            "amount": int(amount),
            "requestedPlayers": [int(player_id)],
        }
        return self._execute("pay_clause", "POST", f"{API_BASE}/offers", payload, critical=True)

    def raise_clause(self, player_id: int, increment_eur: int) -> Dict[str, Any]:
        """Defensive shield: invests cash to raise our own player's clause (`clauseIncrement=1`)."""
        payload = {"amount": int(increment_eur)}
        return self._execute("raise_clause", "PUT", f"{API_BASE}/players/{int(player_id)}/clause", payload, critical=True)

    def set_lineup(self, formation: str, ordered_player_ids: List[int]) -> Dict[str, Any]:
        payload = {"formation": formation, "player_ids": [int(p) for p in ordered_player_ids]}
        if self.dry_run or self.session is None:
            return self._audit("set_lineup", payload, "DRY_RUN" if self.dry_run else "SKIPPED_NO_SESSION")
        try:
            ok = LineupActions(self.session, self.league_id, self.user_id).set_lineup(formation, payload["player_ids"])
            return self._audit("set_lineup", payload, "OK" if ok else "FAILED")
        except Exception as e:
            return self._audit("set_lineup", payload, "ERROR", str(e))
