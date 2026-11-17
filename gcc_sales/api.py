"""Admin-only REST API for the self-hosted GCC sales pilot."""
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from gcc_sales.core import SalesStore

app = FastAPI(title="Nabu AI Sales Engine — GCC Pilot", version="0.1.0",
              description="Consent-first CRM, call readiness, simulated calls, meetings.")


def require_admin(x_admin_token: str | None = Header(default=None)):
    expected = os.getenv("NABU_SALES_ADMIN_TOKEN", "")
    if len(expected) < 24:
        raise HTTPException(503, "Configure a random admin token (24+ characters)")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(401, "Unauthorized")


def store() -> SalesStore:
    return SalesStore(os.getenv("NABU_SALES_DB", str(Path("data") / "gcc_sales.sqlite3")))


def run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Record already exists or violates a constraint") from exc


class BrandIn(BaseModel):
    brand_id: str
    name: str


class AuthorizationIn(BaseModel):
    enabled: bool = False
    allowed_countries: list[Literal["OM", "AE", "SA", "QA", "KW", "BH"]] = []
    compliance_reference: str = ""


class LeadIn(BaseModel):
    brand_id: str
    name: str
    phone_e164: str
    language: Literal["ar", "en", "fa"] = "ar"
    consent_reference: str | None = None
    consent_at: str | None = None


class StageIn(BaseModel):
    stage: Literal["new", "contacted", "qualified", "meeting_booked", "won", "lost"]


class MeetingIn(BaseModel):
    lead_id: str
    rep_id: str
    start: str
    end: str


@app.get("/health")
def health():
    return {"status": "ok", "live_dialing": False}


@app.post("/v1/brands", dependencies=[Depends(require_admin)])
def create_brand(data: BrandIn):
    return run(store().create_brand, data.brand_id, data.name)


@app.get("/v1/brands/{brand_id}", dependencies=[Depends(require_admin)])
def get_brand(brand_id: str):
    return run(store().get_brand, brand_id)


@app.put("/v1/brands/{brand_id}/authorization", dependencies=[Depends(require_admin)])
def authorize(brand_id: str, data: AuthorizationIn):
    return run(store().authorize_brand, brand_id, data.enabled,
               data.allowed_countries, data.compliance_reference)


@app.post("/v1/leads", dependencies=[Depends(require_admin)])
def upsert_lead(data: LeadIn):
    return run(store().upsert_lead, **data.model_dump())


@app.get("/v1/leads/{lead_id}", dependencies=[Depends(require_admin)])
def get_lead(lead_id: str):
    return run(store().get_lead, lead_id)


@app.post("/v1/leads/{lead_id}/opt-out", dependencies=[Depends(require_admin)])
def opt_out(lead_id: str):
    return run(store().opt_out, lead_id)


@app.patch("/v1/leads/{lead_id}/stage", dependencies=[Depends(require_admin)])
def set_stage(lead_id: str, data: StageIn):
    return run(store().set_stage, lead_id, data.stage)


@app.get("/v1/leads/{lead_id}/call-readiness", dependencies=[Depends(require_admin)])
def call_readiness(lead_id: str):
    return run(store().call_eligibility, lead_id)


@app.post("/v1/leads/{lead_id}/simulate-call", dependencies=[Depends(require_admin)])
def simulate_call(lead_id: str):
    return run(store().simulate_call, lead_id)


@app.post("/v1/meetings", dependencies=[Depends(require_admin)])
def book_meeting(data: MeetingIn):
    return run(store().book_meeting, **data.model_dump())


@app.get("/v1/brands/{brand_id}/metrics", dependencies=[Depends(require_admin)])
def metrics(brand_id: str):
    return run(store().metrics, brand_id)
