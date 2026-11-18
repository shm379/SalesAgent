# Nabu AI Sales Engine — Oman & GCC pilot

An isolated self-hosted CRM/API module inside SalesAgent. The pre-existing
Telegram assistant and its entrypoint (main.py) remain untouched.

## Implemented
- Multi-brand CRM with E.164 mobile numbers in OM, AE, SA, QA, KW, BH.
- Lead stage tracking, documentation of marketing consent, persistent opt-out,
  operator/compliance attestation and audit logs.
- Pre-call eligibility: local-time rules; Oman 08:00–21:00, conservative
  internal default of 09:00–18:00 for other GCC countries (NOT legal permission).
- Dry-run calls only; no telephony connection. Blocked leads cannot be simulated.
- Atomic internal meeting booking (prevents overlapping representative slots).
- Brand metrics and admin-key-protected API routes.

## Run locally

From the repository root (Python 3.11+):

    pip install -r gcc_sales/requirements.txt
    export NABU_SALES_ADMIN_TOKEN='replace-with-random-secret-at-least-24-chars'
    export NABU_SALES_DB='./data/gcc_sales.sqlite3'
    uvicorn gcc_sales.api:app --host 127.0.0.1 --port 8787

Use http://127.0.0.1:8787/docs to explore endpoints. All /v1/* requests
require X-Admin-Token; GET /health is public. Do not expose the API publicly
without HTTPS, network controls and credential management.

## Workflow

1. POST /v1/brands with brand_id and name.
2. POST /v1/leads with brand_id, name, phone_e164, language and optional
   consent_reference + consent_at (time with zone offset).
3. After verifying legal/operator permissions, PUT /v1/brands/{id}/authorization
   with allowed_countries, compliance_reference, and enabled=true.
4. GET /v1/leads/{lead_id}/call-readiness returns eligibility and blockers.
5. POST /v1/leads/{lead_id}/simulate-call produces an audit event only.
6. POST /v1/meetings with lead_id, rep_id, start and end ISO timestamps
   books an internal meeting (NOT synchronized to external calendars).
7. GET /v1/brands/{id}/metrics reports leads, bookings, won and simulations.

Example lead payload:
    {"brand_id":"webinar_oman","name":"Test","phone_e164":"+96891234567",
     "language":"ar","consent_reference":"signed-form:abc",
     "consent_at":"2026-11-01T11:00:00+04:00"}

## Tests

    python -m unittest discover -s gcc_sales/tests -v

## Not yet delivered

Actual calls, SIP trunk, LiveKit agent, Arabic voice models, speech recognition,
NabuOS and website integrations, external calendar synchronization, outbound
campaign queue, recordings, human transfers, and the admin dashboard.

Before real dialing: verify operator/TRA compliance, per-destination legal
approval, written and recorded consent evidence, recording notice, encryption,
strong tenant isolation and data retention, provider callbacks, retries and
daily frequency limits. An admin attestation field alone is not proof of
regulatory clearance.

Official Oman TRA rules (August 2026):
https://tra.gov.om/legal-framework/tifarsei1h1kpc0kprkq6qf9

## NabuCRM sync adapter (new)

The service now supports the **existing** NabuCRM signed intake contract, with
one stable idempotent event per lead and a local retry ledger. NabuCRM must
have the `sales-agent` identifier in `config/gate.php`; this is added in a
separate NabuCRM draft PR. Credentials and deployment are not included.

    export NABUGATE_SECRET='same-secret-as-NabuCRM'
    python -m gcc_sales.crm_sync --dry-run
    python -m gcc_sales.crm_sync --limit 50

Run the delivery command from a scheduled service in production. It signs
each attempt with a fresh timestamp, retries transient failures and does
not retry bad signatures. **This sync creates a CRM intake lead only**; it
does not automatically update NabuCRM's pipeline stage, place calls, book
a real external calendar event or claim marketing consent for email/SMS.
Calling consent remains exclusively enforced by the GCC SalesStore.

The CRM service must be configured to receive intake and have the same
NABUGATE_SECRET. A source_url is not invented when the source is unknown.
