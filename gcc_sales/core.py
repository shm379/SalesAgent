"""Consent-first GCC sales CRM and scheduling core; no PSTN calls."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

PREFIXES = {"OM": "+968", "AE": "+971", "SA": "+966", "QA": "+974", "KW": "+965", "BH": "+973"}
ZONES = {"OM": "Asia/Muscat", "AE": "Asia/Dubai", "SA": "Asia/Riyadh",
         "QA": "Asia/Qatar", "KW": "Asia/Kuwait", "BH": "Asia/Bahrain"}
VALID_STAGES = {"new", "contacted", "qualified", "meeting_booked", "won", "lost"}
E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Datetime must include a timezone offset")
    return result.astimezone(timezone.utc)


def stamp(value: datetime) -> str:
    return aware(value).isoformat(timespec="seconds")


def country_for_phone(number: str) -> str:
    if not E164.fullmatch(number):
        raise ValueError("Phone must be in E.164 format")
    for country, prefix in PREFIXES.items():
        if number.startswith(prefix):
            return country
    raise ValueError("Only OM, AE, SA, QA, KW and BH are supported")


class SalesStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        with self.db() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS brands (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    allowed_countries TEXT NOT NULL DEFAULT '[]',
                    compliance_reference TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS leads (
                    id TEXT PRIMARY KEY, brand_id TEXT NOT NULL REFERENCES brands(id),
                    name TEXT NOT NULL, phone_e164 TEXT NOT NULL, country TEXT NOT NULL,
                    language TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'new',
                    consent_reference TEXT, consent_at TEXT,
                    opted_out INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(brand_id, phone_e164)
                );
                CREATE TABLE IF NOT EXISTS call_events (
                    id TEXT PRIMARY KEY, brand_id TEXT NOT NULL REFERENCES brands(id),
                    lead_id TEXT NOT NULL REFERENCES leads(id),
                    status TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meetings (
                    id TEXT PRIMARY KEY, brand_id TEXT NOT NULL REFERENCES brands(id),
                    lead_id TEXT NOT NULL REFERENCES leads(id),
                    rep_id TEXT NOT NULL,
                    start_utc TEXT NOT NULL, end_utc TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_meetings_slot
                    ON meetings(brand_id, rep_id, status, start_utc, end_utc);
                CREATE TABLE IF NOT EXISTS audit (
                    id TEXT PRIMARY KEY, brand_id TEXT NOT NULL,
                    event TEXT NOT NULL, subject_id TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
        finally:
            connection.close()

    def _audit(self, c, brand_id: str, event: str, subject_id: str) -> None:
        c.execute("INSERT INTO audit VALUES (?,?,?,?,?)",
                  (uuid4().hex, brand_id, event, subject_id, stamp(utcnow())))

    def create_brand(self, brand_id: str, name: str) -> dict:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,48}", brand_id):
            raise ValueError("brand_id: 2-49 lowercase letters, digits, - or _")
        if not name.strip():
            raise ValueError("Brand name is required")
        with self.db() as c:
            c.execute("INSERT INTO brands(id,name,created_at) VALUES (?,?,?)",
                      (brand_id, name.strip(), stamp(utcnow())))
            self._audit(c, brand_id, "brand_created", brand_id)
        return self.get_brand(brand_id)

    def get_brand(self, brand_id: str) -> dict:
        with self.db() as c:
            row = c.execute("SELECT * FROM brands WHERE id=?", (brand_id,)).fetchone()
        if row is None:
            raise LookupError("Brand not found")
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        result["allowed_countries"] = json.loads(result["allowed_countries"])
        return result

    def authorize_brand(self, brand_id: str, enabled: bool,
                        countries: list[str], compliance_reference: str) -> dict:
        """Administrative attestation only; this is NOT a legal clearance."""
        countries = sorted(set(countries))
        if any(country not in PREFIXES for country in countries):
            raise ValueError("Unsupported country")
        if enabled and (not countries or len(compliance_reference.strip()) < 5):
            raise ValueError("Enabled brands need countries and a compliance evidence reference")
        self.get_brand(brand_id)
        with self.db() as c:
            c.execute(
                "UPDATE brands SET enabled=?,allowed_countries=?,compliance_reference=? WHERE id=?",
                (int(enabled), json.dumps(countries), compliance_reference.strip(), brand_id))
            self._audit(c, brand_id, "outbound_authorization_updated", brand_id)
        return self.get_brand(brand_id)

    def upsert_lead(self, brand_id: str, name: str, phone_e164: str,
                    language: str = "ar", consent_reference: str | None = None,
                    consent_at: str | None = None) -> dict:
        self.get_brand(brand_id)
        country = country_for_phone(phone_e164)
        if not name.strip():
            raise ValueError("Lead name is required")
        if language not in ("ar", "en", "fa"):
            raise ValueError("Language must be ar, en or fa")
        if bool(consent_reference) != bool(consent_at):
            raise ValueError("Consent reference and timestamp must be provided together")
        consent_utc = stamp(aware(consent_at)) if consent_at else None
        if consent_utc and aware(consent_utc) > utcnow():
            raise ValueError("Consent timestamp cannot be in the future")
        if consent_reference and len(consent_reference.strip()) < 5:
            raise ValueError("Consent reference must identify documentary evidence")
        now = stamp(utcnow())
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute(
                "SELECT id,opted_out FROM leads WHERE brand_id=? AND phone_e164=?",
                (brand_id, phone_e164)).fetchone()
            if existing:
                lead_id = existing["id"]
                c.execute("UPDATE leads SET name=?,language=?,updated_at=? WHERE id=?",
                          (name.strip(), language, now, lead_id))
                if consent_utc and not existing["opted_out"]:
                    c.execute(
                        "UPDATE leads SET consent_reference=?,consent_at=? WHERE id=?",
                        (consent_reference.strip(), consent_utc, lead_id))
                event = "lead_updated"
            else:
                lead_id = uuid4().hex
                c.execute(
                    "INSERT INTO leads(id,brand_id,name,phone_e164,country,language,"
                    "consent_reference,consent_at,created_at,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (lead_id, brand_id, name.strip(), phone_e164, country, language,
                     consent_reference.strip() if consent_reference else None,
                     consent_utc, now, now))
                event = "lead_created"
            self._audit(c, brand_id, event, lead_id)
            c.execute("COMMIT")
        return self.get_lead(lead_id)

    def get_lead(self, lead_id: str) -> dict:
        with self.db() as c:
            row = c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if row is None:
            raise LookupError("Lead not found")
        result = dict(row)
        result["opted_out"] = bool(result["opted_out"])
        return result

    def opt_out(self, lead_id: str) -> dict:
        lead = self.get_lead(lead_id)
        with self.db() as c:
            c.execute("UPDATE leads SET opted_out=1,updated_at=? WHERE id=?",
                      (stamp(utcnow()), lead_id))
            self._audit(c, lead["brand_id"], "do_not_contact", lead_id)
        return self.get_lead(lead_id)

    def set_stage(self, lead_id: str, stage: str) -> dict:
        if stage not in VALID_STAGES:
            raise ValueError("Unsupported sales stage")
        lead = self.get_lead(lead_id)
        with self.db() as c:
            c.execute("UPDATE leads SET stage=?,updated_at=? WHERE id=?",
                      (stage, stamp(utcnow()), lead_id))
            self._audit(c, lead["brand_id"], "stage_" + stage, lead_id)
        return self.get_lead(lead_id)

    def call_eligibility(self, lead_id: str, at: datetime | None = None) -> dict:
        now = aware(at or utcnow())
        lead = self.get_lead(lead_id)
        brand = self.get_brand(lead["brand_id"])
        reasons = []
        if lead["opted_out"]:
            reasons.append("do_not_contact")
        if not lead["consent_reference"] or not lead["consent_at"]:
            reasons.append("no_documented_marketing_consent")
        if not brand["enabled"]:
            reasons.append("brand_outbound_disabled")
        if lead["country"] not in brand["allowed_countries"]:
            reasons.append("country_not_authorized")
        if not brand["compliance_reference"]:
            reasons.append("no_operator_compliance_record")
        local = now.astimezone(ZoneInfo(ZONES[lead["country"]]))
        # Oman TRA: promotional calls 08:00-21:00. Else 09:00-18:00 is only
        # an internal default, not a statement of legal permission.
        start, end = (8, 21) if lead["country"] == "OM" else (9, 18)
        if not start <= local.hour < end:
            reasons.append("outside_contact_hours")
        with self.db() as c:
            recent = c.execute(
                "SELECT COUNT(*) FROM call_events WHERE lead_id=? AND status='dialed'"
                " AND created_at>=?",
                (lead_id, stamp(now - timedelta(hours=24)))).fetchone()[0]
        if recent >= 1:
            reasons.append("contact_frequency_cap")
        return {"lead_id": lead_id, "country": lead["country"],
                "local_time": local.isoformat(timespec="seconds"),
                "eligible": not reasons, "reasons": reasons,
                "note": "Readiness only. No telephone call is made."}

    def simulate_call(self, lead_id: str, at: datetime | None = None) -> dict:
        check = self.call_eligibility(lead_id, at=at)
        if not check["eligible"]:
            return {"status": "blocked", **check}
        lead = self.get_lead(lead_id)
        call_id = uuid4().hex
        with self.db() as c:
            c.execute("INSERT INTO call_events VALUES(?,?,?,?,?,?)",
                      (call_id, lead["brand_id"], lead_id, "simulated",
                       "DRY RUN: no network connection, no call placed", stamp(utcnow())))
            self._audit(c, lead["brand_id"], "call_simulated", call_id)
        return {"call_id": call_id, "status": "simulated", "network_called": False, **check}

    def book_meeting(self, lead_id: str, rep_id: str,
                     start: str, end: str) -> dict:
        lead = self.get_lead(lead_id)
        begin, finish = aware(start), aware(end)
        if finish <= begin or finish - begin > timedelta(hours=4):
            raise ValueError("Meeting must last >0 and <=4 hours")
        if begin < utcnow():
            raise ValueError("Meeting must be in the future")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{2,64}", rep_id):
            raise ValueError("Invalid sales representative ID")
        meeting_id = uuid4().hex
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            exists = c.execute(
                "SELECT id FROM meetings WHERE brand_id=? AND rep_id=? "
                "AND status='confirmed' AND start_utc<? AND end_utc>? LIMIT 1",
                (lead["brand_id"], rep_id, stamp(finish), stamp(begin))).fetchone()
            if exists:
                raise ValueError("Representative has an overlapping confirmed meeting")
            c.execute("INSERT INTO meetings VALUES(?,?,?,?,?,?,?,?)",
                      (meeting_id, lead["brand_id"], lead_id, rep_id,
                       stamp(begin), stamp(finish), "confirmed", stamp(utcnow())))
            c.execute("UPDATE leads SET stage='meeting_booked',updated_at=? WHERE id=?",
                      (stamp(utcnow()), lead_id))
            self._audit(c, lead["brand_id"], "meeting_booked", meeting_id)
            c.execute("COMMIT")
        return {"meeting_id": meeting_id, "brand_id": lead["brand_id"],
                "lead_id": lead_id, "rep_id": rep_id,
                "start_utc": stamp(begin), "end_utc": stamp(finish),
                "status": "confirmed", "external_calendar_synced": False}

    def metrics(self, brand_id: str) -> dict:
        self.get_brand(brand_id)
        with self.db() as c:
            leads = c.execute("SELECT COUNT(*) FROM leads WHERE brand_id=?",
                              (brand_id,)).fetchone()[0]
            booked = c.execute(
                "SELECT COUNT(*) FROM meetings WHERE brand_id=? AND status='confirmed'",
                (brand_id,)).fetchone()[0]
            simulated = c.execute(
                "SELECT COUNT(*) FROM call_events WHERE brand_id=? AND status='simulated'",
                (brand_id,)).fetchone()[0]
            won = c.execute(
                "SELECT COUNT(*) FROM leads WHERE brand_id=? AND stage='won'",
                (brand_id,)).fetchone()[0]
        return {"brand_id": brand_id, "leads": leads,
                "meetings_confirmed": booked, "simulated_calls": simulated,
                "won_leads": won}
