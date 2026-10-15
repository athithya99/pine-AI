import os

os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://pinesphere:pinesphere@localhost:5432/pinesphere_test"),
)
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use")
os.environ.setdefault("CELERY_EAGER", "true")
os.environ.setdefault("ENABLE_SIMULATOR", "true")
os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test-verify")
import tempfile  # noqa: E402

os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp(prefix="ps-media-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.database import SessionLocal, engine  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Role, Tenant, User  # noqa: E402
from app.services.tenants import seed_tenant_defaults  # noqa: E402

PASSWORD = "Passw0rd!"


@pytest.fixture(scope="session", autouse=True)
def schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def clean_db():
    yield
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s


class Company:
    """A tenant with one user per role, each with a ready auth header."""

    def __init__(self, client: TestClient, name: str, prefix: str):
        with SessionLocal() as s:
            t = Tenant(name=name, state="Tamil Nadu")
            s.add(t)
            s.flush()
            seed_tenant_defaults(s, t)
            self.tenant_id = t.id
            self.users: dict[Role, User] = {}
            for i, role in enumerate((Role.owner, Role.admin, Role.sales, Role.accounts), start=1):
                u = User(tenant_id=t.id, name=f"{prefix} {role.value}", email=f"{role.value}@{prefix}-demo.in",
                         mobile=f"+9198{prefix_num(prefix)}{i:05d}", password_hash=hash_password(PASSWORD), role=role)
                s.add(u)
                self.users[role] = u
            s.commit()
        self.client = client
        self.headers = {role: login(client, f"{role.value}@{prefix}-demo.in") for role in self.users}

    def h(self, role: Role) -> dict:
        return self.headers[role]


def prefix_num(prefix: str) -> str:
    return {"alpha": "111", "beta": "222"}.get(prefix, "333")


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def company_a(client):
    return Company(client, "Alpha Traders", "alpha")


@pytest.fixture
def company_b(client):
    return Company(client, "Beta Distributors", "beta")
