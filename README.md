# Pinesphere AI Commerce

WhatsApp-first AI order-to-cash platform for B2B sellers (brands, distributors, wholesalers).
This repository holds the MVP described in the SRS.

## Status

| Milestone | Modules | State |
| --- | --- | --- |
| Foundation | M1 Tenants, users, roles · M2 Customer master · M3 Catalog and pricing | Done, tested |
| WhatsApp channel | M4 WhatsApp channel: webhook, inbox, templates, 24-hour window, opt-out, simulator | Done, tested |
| Orders | M6 Order management: status flow, credit check, approvals, buyer updates, Excel import/export | Done, tested |
| AI ordering | M5 AI order capture: intents, catalog matching, quotes, corrections, photos, voice notes, AI log | Done, tested with the development AI; real provider needs an API key |
| Invoices, collections, AI | M7 Invoices and ledger · M8 Collections · M9 Campaigns · M10 Assistant and dashboard | Planned |

## Stack

- **Backend:** Python 3.13, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2
- **Database:** PostgreSQL 16
- **Jobs:** Celery + Redis (used from M4 onward)
- **Frontend:** React 19 + TypeScript, Vite, Tailwind CSS v4
- **Deploy:** Docker Compose; Nginx serves the frontend and proxies `/api`

## Project layout

```
backend/
  app/
    api/v1/        auth, tenants, users, customers, catalog, whatsapp, orders, ai (routers)
    core/          config, database, security (JWT, OTP), deps (auth + roles), validators
    models/        SQLAlchemy models (every tenant table has tenant_id)
    schemas/       Pydantic request/response models
    services/      pricing engine, order flow, Excel import/export, audit log, notifications, media storage
      whatsapp/    Cloud API client + simulator, inbound webhook processing, outbound sending rules
      ai/          AI providers (mock, Anthropic, OpenAI), speech-to-text, product matcher, WhatsApp assistant
    api/webhooks.py  public webhook endpoint (signature-verified)
    worker/        Celery app and background tasks
    cli.py         create-superadmin, seed-demo
  alembic/         migrations
  tests/           pytest suite (105 tests, runs against PostgreSQL)
frontend/
  src/
    lib/           API client (token refresh), auth context, types
    components/    layout, drawer, import dialog, shared UI
    pages/         Login, Dashboard, Inbox, Customers, Products, Price lists, Schemes, Team, WhatsApp, Company
docker-compose.yml
```

## Run locally (without Docker)

Needs Python 3.12+, Node 20+, PostgreSQL 16, Redis.

```bash
# database
createuser -P pinesphere          # password: pinesphere
createdb -O pinesphere pinesphere

# backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env               # set SECRET_KEY
alembic upgrade head
python -m app.cli seed-demo        # optional demo company
uvicorn app.main:app --reload      # http://localhost:8000/api/docs

# frontend (new terminal)
cd frontend
npm install
npm run dev                        # http://localhost:5173 (proxies /api to :8000)
```

Demo logins after `seed-demo` (password `Demo@1234`): `owner@demotraders.in`, `sales@demotraders.in`, `accounts@demotraders.in`.

Platform super admin: `python -m app.cli create-superadmin --name "..." --email ... --password ...`

## Run with Docker

```bash
cp backend/.env.example backend/.env   # set SECRET_KEY
docker compose up --build
docker compose exec backend python -m app.cli seed-demo
```

App: http://localhost:8080 · API docs: http://localhost:8000/api/docs

## Tests

```bash
createdb -O pinesphere pinesphere_test
cd backend && pytest
```

Tests cover the SRS acceptance criteria for M1–M6, including cross-tenant isolation, session revocation on
deactivation, Sales-only-sees-own-customers, the 1,000-row import with 10 bad rows, price priority
(customer price → price list → base), expired schemes, best-single-scheme selection, and out-of-stock flags.

## Key rules implemented

- **Tenant isolation:** every query filters on `tenant_id`; other tenants' records return 404.
- **Roles:** Owner, Admin, Sales, Accounts (+ platform Super admin), enforced in the API. Sales see only assigned
  customers and cannot change price list, credit terms or assignment.
- **Sessions:** 15-minute access token + 7-day refresh token. Deactivating a user or changing their role ends their
  sessions immediately (token version).
- **OTP login:** max 5 requests per number per 15 minutes; codes expire in 5 minutes; 5 wrong tries lock a code.
- **Audit log:** create/update/delete on users, customers, products, prices and schemes, with old and new values.
- **Pricing:** customer-specific price, else price list, else base price; then the best single active scheme
  (never stacked; dates are inclusive and evaluated in IST); then GST.
- **GST rates:** configurable master per company, seeded with 0, 5, 18 and 40% (slabs in force since 22 Sept 2025).
- **Excel imports:** valid rows are saved, invalid rows are skipped and reported with row number and reason.
  Products upsert by SKU.
- **WhatsApp consent:** opt-in date and source stored per customer; used by M4/M8/M9 to block sends.

## WhatsApp (M4)

**Simulator mode (default for development).** Settings → WhatsApp → Simulator → Save. In the Inbox, "Simulate buyer"
sends a text, photo, voice note or PDF as if a buyer messaged you, through the same pipeline real webhooks use. New
templates can be approved or rejected from the WhatsApp page.

**Live number (Meta Cloud API).**
1. In the Meta app (WhatsApp → API Setup) copy the WhatsApp Business Account ID, Phone number ID and access token,
   and the App secret from App settings → Basic.
2. Settings → WhatsApp → Live number, paste them, Save, then **Test connection**.
3. Meta must reach the webhook over public HTTPS. For local testing run a tunnel, e.g.
   `cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000`, then in the Meta app → WhatsApp →
   Configuration set Callback URL to `https://<tunnel-host>/api/webhooks/whatsapp`, Verify token to the
   `WHATSAPP_VERIFY_TOKEN` value in `backend/.env`, and subscribe to the `messages` field.

Rules enforced: free-form messages only within 24 hours of the buyer's last message (otherwise an approved template);
business-initiated templates only to opted-in buyers; STOP opts a buyer out (START opts back in); unknown numbers
become "lead" customers; duplicate webhooks are ignored; status never moves backwards; temporary Meta errors are
retried up to 3 times; media is copied to our storage on arrival.

**Background jobs.** With `CELERY_EAGER=true` jobs run inline, so Redis and the worker are optional in development.
In production set it to `false` and run `celery -A app.worker.celery_app worker -l info`.

## Orders (M6)

One status flow for every order: Draft → Quoted → Confirmed → Approved → Invoiced → Dispatched → Delivered, with
On hold, Expired and Cancelled. Any other move is rejected by the API (409).

- **Numbers:** `ORD/26-27/00001`, unique per company and restarting each financial year. Prefix and quote validity
  (default 24 hours) are in Settings → Company.
- **Quotes on WhatsApp:** the quote goes out with Confirm / Edit / Cancel buttons. Confirm moves the order to
  Confirmed; Edit tells the buyer staff will revise it; Cancel cancels it. Staff can also confirm on the buyer's
  instruction (call or visit).
- **Credit check on Confirm:** if outstanding + order total is above the customer's credit limit, or an item is out of
  stock, the order goes On hold. Only Owner/Admin can approve (with a remark) or reject it. A credit limit of 0 means
  no limit. Until the M7 ledger exists, outstanding = the customer's opening balance + approved/invoiced orders.
- **Edits:** allowed until Approved; each edit is audited, bumps the revision, and sends the buyer a revised quote to
  confirm again.
- **Buyer messages:** each buyer-facing status change sends exactly one WhatsApp message. Inside the 24-hour window
  it's a normal message; outside it uses the approved `order_update` template if there is one. If neither is
  possible the order still moves and the reason shows on the order timeline.
- **"Where is my order?":** buyers asking about their orders get the status of their last 5 (when the bot is on for
  that chat).
- **Invoiced:** M6 records the invoice number and invoiced quantity per line (lower than ordered = short supply). PDF
  invoices come with M7.
- **Excel:** import (one row per item, `order_ref` groups rows into an order; creates drafts) and export (orders +
  lines, with the list filters).
- **Quote expiry:** a Celery beat job runs every 10 minutes (`celery -A app.worker.celery_app beat -l info`). A buyer
  tapping Confirm on an expired quote is told it has expired.

## AI order capture (M5)

A buyer's WhatsApp message (typed list, photo of a list, or voice note) is read by AI, matched to the catalog and
answered with a priced quote with Confirm / Edit / Cancel buttons. The AI never confirms an order and never sets a
price: matching is checked against the catalog in our code and prices come from the pricing engine.

- **Intents:** order, change to the open quote ("make atta 20", "remove item 2", "add 5 soap"), order status, price
  enquiry, greeting. Ledger, payment, complaint and anything else stay in the inbox for staff.
- **Matching:** SKU, name and aliases with a confidence score. Items below the company's threshold (Settings →
  Company, default 0.80) or without a quantity are not added; the quote lists them and asks the buyer. Staff can
  save the buyer's wording as a product alias from the order page so it matches next time.
- **Failures:** if the AI errors or times out, the message stays in the inbox and the buyer gets one
  acknowledgement (not repeated within 30 minutes).
- **AI log:** every call is stored with input, output, model, tokens, cost and time (Settings → AI usage).
- **Switching provider** (`backend/.env`, restart after changing):

  | Setting | Development (default) | Real AI |
  | --- | --- | --- |
  | `AI_PROVIDER` | `mock` (built-in rules: typed orders only, no key) | `anthropic` or `openai` |
  | `AI_API_KEY` | blank | the company's API key |
  | `AI_MODEL_FAST` / `AI_MODEL_SMART` | | optional; defaults `claude-haiku-5-5` / `claude-sonnet-5-5` or `gpt-5.4-mini` / `gpt-5.4` |
  | `STT_PROVIDER` / `STT_API_KEY` | `none` | `openai` for voice notes (key optional when `AI_PROVIDER=openai`) |

  Text messages use the fast model; photos use the smart one.

## Not yet connected

- OTP and password-reset messages are written to the server log (`app/services/notify.py`). Connect an SMS
  provider or a WhatsApp authentication template before go-live.
