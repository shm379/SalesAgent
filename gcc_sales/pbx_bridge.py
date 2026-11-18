"""Signed bridge for Asterisk/PBX call lifecycle events into existing NabuCRM.

This emits metadata only and NEVER originates a PSTN call. Actual dialling must
be gated by SalesStore.call_eligibility and an operator-approved SIP trunk.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

import httpx

STATES = {"started", "answered", "ended"}


def make_pbx_event(call_id: str, event: str, from_number: str, to_number: str,
                   extension: str | None = None, duration: int | None = None,
                   recording_url: str | None = None) -> dict:
    if not 1 <= len(call_id.strip()) <= 120:
        raise ValueError("Call ID is required, up to 120 chars")
    if event not in STATES:
        raise ValueError("Unsupported PBX event")
    if not from_number.strip() or not to_number.strip():
        raise ValueError("Both call endpoints must be specified")
    if duration is not None and (event != "ended" or not 0 <= duration <= 86400):
        raise ValueError("Duration is valid only on ended events")
    if recording_url is not None and (event != "ended" or not recording_url.startswith("https://")):
        raise ValueError("Recording URL must be HTTPS and on an ended event")
    data = {
        "event": event,
        "call_id": call_id.strip(),
        "direction": "out",
        "from": from_number.strip(),
        "to": to_number.strip(),
    }
    if extension:
        data["extension"] = extension.strip()
    if duration is not None:
        data["duration"] = duration
    if recording_url is not None:
        data["recording_url"] = recording_url
    return data


def sign_pbx_event(payload: dict, secret: str, timestamp: int | None = None) -> tuple[bytes, dict]:
    if not secret or len(secret) < 16:
        raise ValueError("PBX webhook secret is missing")
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ts = str(int(time.time() if timestamp is None else timestamp))
    digest = hmac.new(secret.encode(), ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return raw, {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Nabu-Timestamp": ts,
        "X-Nabu-Signature": "sha256=" + digest,
    }


def post_pbx_event(payload: dict, url: str, secret: str,
                   transport: httpx.BaseTransport | None = None) -> int:
    """Called by future SIP call worker, never as a side effect of import."""
    if not url.startswith("https://"):
        raise ValueError("PBX webhook URL must use HTTPS")
    raw, headers = sign_pbx_event(payload, secret)
    with httpx.Client(timeout=10, follow_redirects=False, transport=transport) as client:
        response = client.post(url, content=raw, headers=headers)
    response.raise_for_status()
    return response.status_code
