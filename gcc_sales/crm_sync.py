"""Reliable, signed NabuCRM lead intake for Nabu AI Sales Engine.

Matches NabuCRM docs/crm-intake.md. No network request occurs on import.
Run this manually or from a scheduled worker; credentials are never stored here.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from gcc_sales.core import SalesStore, stamp, utcnow

CRM_URL = "https://crm.nabuxai.com/api/v1/crm/intake"
APP_ID = "sales-agent"


def make_payload(lead: dict) -> dict:
    """The CRM intake contract: not consent to SMS/email or investment advice."""
    return {
        "event_id": "salesagent-" + lead["id"],
        "event": "lead",
        "occurred_at": lead["created_at"],
        "person": {
            "name": lead["name"],
            "phone": lead["phone_e164"],
            "country": lead["country"],
            "external_id": lead["id"],
        },
        "source_url": "https://crm.nabuxai.com/",
        "consent": {"email": False, "sms": False, "basis": "phone_only_separate_consent"},
        "metadata": {
            "brand_id": lead["brand_id"], "language": lead["language"],
            "source": "nabu-ai-sales-engine",
        },
    }


def signed_request(payload: dict, secret: str, timestamp: int | None = None) -> tuple[bytes, dict]:
    if len(secret) < 16:
        raise ValueError("NABUGATE_SECRET must be configured before syncing")
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ts = str(int(time.time() if timestamp is None else timestamp))
    sig = hmac.new(secret.encode(), (APP_ID + ":" + ts + ":").encode() + body,
                   hashlib.sha256).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-NabuGate-App-Id": APP_ID,
        "X-NabuGate-Timestamp": ts,
        "X-NabuGate-Signature": sig,
    }


class CrmDispatcher:
    def __init__(self, store: SalesStore, secret: str, url: str = CRM_URL,
                 transport: httpx.BaseTransport | None = None):
        if not secret or len(secret) < 16:
            raise ValueError("NABUGATE_SECRET is required")
        if not url.startswith("https://"):
            raise ValueError("The CRM intake endpoint must use HTTPS")
        self.store = store
        self.secret = secret
        self.url = url
        self.transport = transport
        with self.store.db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS crm_sync_delivery (
                    lead_id TEXT PRIMARY KEY REFERENCES leads(id),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    delivered_at TEXT,
                    next_attempt_at TEXT,
                    permanent_failure INTEGER NOT NULL DEFAULT 0,
                    last_http_status INTEGER,
                    last_error TEXT
                )
            """)

    def pending(self, max_batch: int = 50, now: datetime | None = None) -> list[dict]:
        current = stamp(now or utcnow())
        with self.store.db() as db:
            rows = db.execute("""
                SELECT leads.* FROM leads LEFT JOIN crm_sync_delivery cd
                  ON leads.id=cd.lead_id
                WHERE cd.delivered_at IS NULL
                  AND COALESCE(cd.permanent_failure,0)=0
                  AND (cd.next_attempt_at IS NULL OR cd.next_attempt_at <= ?)
                ORDER BY leads.created_at ASC LIMIT ?
            """, (current, min(100, max(1, max_batch)))).fetchall()
        return [dict(row) for row in rows]

    def send(self, lead: dict) -> dict:
        body, headers = signed_request(make_payload(lead), self.secret)
        status: int | None = None
        error: str | None = None
        retry_after: int | None = None
        try:
            with httpx.Client(timeout=10, transport=self.transport, follow_redirects=False) as client:
                response = client.post(self.url, content=body, headers=headers)
                status = response.status_code
                if status == 429:
                    delay = response.headers.get("Retry-After", "")
                    retry_after = int(delay) if delay.isdigit() else None
                if status not in (200, 201):
                    error = "CRM HTTP " + str(status)
        except httpx.RequestError as exc:
            error = type(exc).__name__
        good = status in (200, 201)
        permanent = status is not None and 400 <= status < 500 and status != 429
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT attempts FROM crm_sync_delivery WHERE lead_id=?",
                (lead["id"],)).fetchone()
            attempts = (prior["attempts"] if prior else 0) + 1
            wait = retry_after or min(21600, 60 * 2 ** min(attempts - 1, 8))
            until = stamp(utcnow() + timedelta(seconds=wait))
            db.execute("""
                INSERT INTO crm_sync_delivery
                (lead_id, attempts, delivered_at, next_attempt_at,
                 permanent_failure, last_http_status, last_error)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(lead_id) DO UPDATE SET
                   attempts=excluded.attempts,
                   delivered_at=excluded.delivered_at,
                   next_attempt_at=excluded.next_attempt_at,
                   permanent_failure=excluded.permanent_failure,
                   last_http_status=excluded.last_http_status,
                   last_error=excluded.last_error
            """, (lead["id"], attempts, stamp(utcnow()) if good else None,
                  None if good or permanent else until, int(permanent), status, error))
            db.execute("COMMIT")
        return {"lead_id": lead["id"], "delivered": good,
                "permanent_failure": permanent, "http_status": status,
                "error": error}

    def run_once(self, limit: int = 50) -> list[dict]:
        return [self.send(lead) for lead in self.pending(limit)]


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Deliver GCC sales leads to NabuCRM")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    store = SalesStore(os.getenv("NABU_SALES_DB", "data/gcc_sales.sqlite3"))
    if args.dry_run:
        with store.db() as db:
            count = db.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        print(json.dumps({"dry_run": True, "lead_count": count,
                          "sent": 0}, ensure_ascii=False))
        return
    worker = CrmDispatcher(store, os.getenv("NABUGATE_SECRET", ""),
                           url=os.getenv("NABU_CRM_INTAKE_URL", CRM_URL))
    results = worker.run_once(args.limit)
    print(json.dumps({"attempted": len(results),
                      "delivered": sum(r["delivered"] for r in results),
                      "errors": sum(not r["delivered"] for r in results)}))


if __name__ == "__main__":
    main()
