"""M1 acceptance criteria and role rules."""

from app.core.security import hash_password
from app.models import Role, User
from app.services import notify
from tests.conftest import PASSWORD, login

API = "/api/v1"


def make_customer(client, headers, number="+919876500001", **extra):
    r = client.post(f"{API}/customers", headers=headers,
                    json={"business_name": "Test Shop", "whatsapp_number": number, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_login_and_me(client, company_a):
    r = client.get(f"{API}/auth/me", headers=company_a.h(Role.owner))
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "owner"
    assert body["tenant"]["name"] == "Alpha Traders"


def test_wrong_password_rejected(client, company_a):
    r = client.post(f"{API}/auth/login", json={"email": "owner@alpha-demo.in", "password": "nope"})
    assert r.status_code == 401


def test_cross_tenant_access_returns_404(client, company_a, company_b):
    """AC: a user of tenant A gets 404 for any record ID of tenant B."""
    cust_b = make_customer(client, company_b.h(Role.owner))
    r = client.get(f"{API}/customers/{cust_b['id']}", headers=company_a.h(Role.owner))
    assert r.status_code == 404
    r = client.patch(f"{API}/customers/{cust_b['id']}", headers=company_a.h(Role.owner),
                     json={"business_name": "Hijacked"})
    assert r.status_code == 404
    listing = client.get(f"{API}/customers", headers=company_a.h(Role.owner)).json()
    assert listing["total"] == 0
    user_b = company_b.users[Role.sales]
    assert client.get(f"{API}/users/{user_b.id}", headers=company_a.h(Role.owner)).status_code == 404


def test_deactivated_user_session_stops(client, company_a):
    """AC: a deactivated user cannot log in and their existing session stops working."""
    sales = company_a.users[Role.sales]
    sales_headers = company_a.h(Role.sales)
    assert client.get(f"{API}/auth/me", headers=sales_headers).status_code == 200

    r = client.patch(f"{API}/users/{sales.id}", headers=company_a.h(Role.admin), json={"is_active": False})
    assert r.status_code == 200

    assert client.get(f"{API}/auth/me", headers=sales_headers).status_code == 401
    r = client.post(f"{API}/auth/login", json={"email": "sales@alpha-demo.in", "password": PASSWORD})
    assert r.status_code == 401


def test_sales_sees_only_assigned_customers(client, company_a):
    """AC: a Sales user sees only assigned customers in every list and search."""
    owner = company_a.h(Role.owner)
    sales_id = str(company_a.users[Role.sales].id)
    mine = make_customer(client, owner, "+919876500001", assigned_user_id=sales_id)
    other = make_customer(client, owner, "+919876500002")

    sales = company_a.h(Role.sales)
    listing = client.get(f"{API}/customers", headers=sales).json()
    assert [c["id"] for c in listing["items"]] == [mine["id"]]
    assert client.get(f"{API}/customers?q=Test", headers=sales).json()["total"] == 1
    assert client.get(f"{API}/customers/{other['id']}", headers=sales).status_code == 404
    # accounts sees all
    assert client.get(f"{API}/customers", headers=company_a.h(Role.accounts)).json()["total"] == 2


def test_sales_cannot_change_credit_terms(client, company_a):
    owner = company_a.h(Role.owner)
    c = make_customer(client, owner, assigned_user_id=str(company_a.users[Role.sales].id))
    sales = company_a.h(Role.sales)
    assert client.patch(f"{API}/customers/{c['id']}", headers=sales, json={"contact_person": "Ravi"}).status_code == 200
    assert client.patch(f"{API}/customers/{c['id']}", headers=sales, json={"credit_limit": 999999}).status_code == 403


def test_role_permissions(client, company_a):
    # sales and accounts cannot manage users or create customers
    for role in (Role.sales, Role.accounts):
        assert client.get(f"{API}/users", headers=company_a.h(role)).status_code == 403
        r = client.post(f"{API}/customers", headers=company_a.h(role),
                        json={"business_name": "X Shop", "whatsapp_number": "9876500009"})
        assert r.status_code == 403


def test_admin_cannot_touch_owner(client, company_a):
    owner = company_a.users[Role.owner]
    admin = company_a.h(Role.admin)
    assert client.patch(f"{API}/users/{owner.id}", headers=admin, json={"is_active": False}).status_code == 403
    r = client.post(f"{API}/users", headers=admin,
                    json={"name": "New Owner", "email": "o2@alpha-demo.in", "password": "Secret123", "role": "owner"})
    assert r.status_code == 403


def test_last_owner_cannot_be_removed(client, company_a, db):
    second_owner = User(tenant_id=company_a.tenant_id, name="Owner 2", email="o2@alpha-demo.in",
                        password_hash=hash_password(PASSWORD), role=Role.owner)
    db.add(second_owner)
    db.commit()
    h2 = login(client, "o2@alpha-demo.in")
    owner = company_a.users[Role.owner]
    assert client.patch(f"{API}/users/{owner.id}", headers=h2, json={"is_active": False}).status_code == 200
    # now Owner 2 is the only active owner and cannot be demoted by anyone
    other_admin_view = client.patch(f"{API}/users/{second_owner.id}", headers=company_a.h(Role.admin),
                                    json={"role": "admin"})
    assert other_admin_view.status_code == 403


def test_create_user_and_audit_log(client, company_a):
    owner = company_a.h(Role.owner)
    r = client.post(f"{API}/users", headers=owner,
                    json={"name": "Kavi", "mobile": "9876543210", "role": "sales"})
    assert r.status_code == 201, r.text
    assert r.json()["mobile"] == "+919876543210"
    logs = client.get(f"{API}/audit-logs?entity=user", headers=owner).json()
    assert logs and logs[0]["action"] == "create" and logs[0]["entity_id"] == r.json()["id"]


def test_duplicate_user_email_rejected(client, company_a, company_b):
    r = client.post(f"{API}/users", headers=company_b.h(Role.owner),
                    json={"name": "Dup", "email": "sales@alpha-demo.in", "password": "Secret123", "role": "sales"})
    assert r.status_code == 409


def test_otp_login(client, company_a, monkeypatch):
    sent = {}
    monkeypatch.setattr(notify, "send_otp", lambda mobile, code: sent.update(mobile=mobile, code=code))
    mobile = company_a.users[Role.sales].mobile
    assert client.post(f"{API}/auth/otp/request", json={"mobile": mobile}).status_code == 202
    assert sent["mobile"] == mobile

    bad = client.post(f"{API}/auth/otp/verify", json={"mobile": mobile, "code": "000000" if sent["code"] != "000000" else "111111"})
    assert bad.status_code == 401
    ok = client.post(f"{API}/auth/otp/verify", json={"mobile": mobile, "code": sent["code"]})
    assert ok.status_code == 200
    # code is single use
    again = client.post(f"{API}/auth/otp/verify", json={"mobile": mobile, "code": sent["code"]})
    assert again.status_code == 401


def test_otp_rate_limit(client, company_a, monkeypatch):
    """FR-M1-07: max 5 OTP requests per 15 minutes per number."""
    monkeypatch.setattr(notify, "send_otp", lambda *a: None)
    mobile = company_a.users[Role.sales].mobile
    for _ in range(5):
        assert client.post(f"{API}/auth/otp/request", json={"mobile": mobile}).status_code == 202
    assert client.post(f"{API}/auth/otp/request", json={"mobile": mobile}).status_code == 429


def test_refresh_token(client, company_a):
    r = client.post(f"{API}/auth/login", json={"email": "owner@alpha-demo.in", "password": PASSWORD}).json()
    new = client.post(f"{API}/auth/refresh", json={"refresh_token": r["refresh_token"]})
    assert new.status_code == 200
    # access token cannot be used as a refresh token
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": r["access_token"]}).status_code == 401


def test_password_reset(client, company_a, monkeypatch):
    sent = {}
    monkeypatch.setattr(notify, "send_password_reset", lambda email, token: sent.update(token=token))
    client.post(f"{API}/auth/password-reset/request", json={"email": "accounts@alpha-demo.in"})
    r = client.post(f"{API}/auth/password-reset/confirm", json={"token": sent["token"], "new_password": "NewPass123"})
    assert r.status_code == 204
    assert client.post(f"{API}/auth/login", json={"email": "accounts@alpha-demo.in", "password": "NewPass123"}).status_code == 200
    # link is single use
    r = client.post(f"{API}/auth/password-reset/confirm", json={"token": sent["token"], "new_password": "Another123"})
    assert r.status_code == 400


def test_suspended_tenant_blocked(client, company_a, db):
    sa = User(name="Platform", email="root@pinesphere-demo.in", password_hash=hash_password(PASSWORD),
              role=Role.super_admin)
    db.add(sa)
    db.commit()
    root = login(client, "root@pinesphere-demo.in")
    assert client.post(f"{API}/admin/tenants/{company_a.tenant_id}/suspend", headers=root).status_code == 200
    assert client.get(f"{API}/customers", headers=company_a.h(Role.owner)).status_code == 403
    assert client.post(f"{API}/auth/login", json={"email": "owner@alpha-demo.in", "password": PASSWORD}).status_code == 403
    assert client.post(f"{API}/admin/tenants/{company_a.tenant_id}/reactivate", headers=root).status_code == 200
    assert client.get(f"{API}/customers", headers=company_a.h(Role.owner)).status_code == 200


def test_super_admin_creates_tenant(client, db):
    db.add(User(name="Platform", email="root@pinesphere-demo.in", password_hash=hash_password(PASSWORD),
                role=Role.super_admin))
    db.commit()
    root = login(client, "root@pinesphere-demo.in")
    r = client.post(f"{API}/admin/tenants", headers=root, json={
        "name": "Gamma Foods", "gstin": "33AABCG1234H1Z5", "owner_name": "Gopal",
        "owner_email": "gopal@gamma-demo.in", "owner_password": "Gamma1234"})
    assert r.status_code == 201, r.text
    owner = login(client, "gopal@gamma-demo.in", "Gamma1234")
    rates = client.get(f"{API}/gst-rates", headers=owner).json()
    assert sorted(float(x["rate"]) for x in rates) == [0, 5, 18, 40]
    # company users cannot reach platform endpoints
    assert client.get(f"{API}/admin/tenants", headers=owner).status_code == 403
