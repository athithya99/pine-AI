import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import webhooks
from app.api.v1 import ai, assistant, auth, campaigns, catalog, customers, invoices, orders, payments, tenants, users, whatsapp
from app.core.config import settings
from app.core.database import SessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(
    title=f"{settings.APP_NAME} API",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API = "/api/v1"
for r in (auth.router, tenants.router, users.router, customers.router, catalog.router, orders.router, invoices.router, payments.router, whatsapp.router, ai.router, campaigns.router, assistant.router):
    app.include_router(r, prefix=API)
app.include_router(webhooks.router)


@app.get("/api/health", tags=["Health"])
def health():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok"}
