from __future__ import annotations

from collections.abc import Generator
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.api.main import app
from app.core.payment import PRO_PRICE_CENTS
from app.core.security import hash_password, verify_password
from app.db.base import Base
from app.db import models  # noqa: F401


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db() -> Generator[Session, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as tc:
        yield tc

    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


def test_password_hashing() -> None:
    salt, pw_hash = hash_password("senhaSegura123")
    assert verify_password("senhaSegura123", salt, pw_hash) is True
    assert verify_password("senhaErrada", salt, pw_hash) is False


def test_auth_registration_and_login(client: TestClient) -> None:
    # 1. Register new user
    reg_res = client.post(
        "/api/v1/auth/register",
        json={"email": "capitao_teste@mvl.com", "password": "mypassword123"},
    )
    assert reg_res.status_code == 201
    reg_data = reg_res.json()
    assert reg_data["ok"] is True
    assert reg_data["user"]["plan_tier"] == "free"
    token = reg_data["token"]
    assert token

    # 2. Get current user with Bearer token
    me_res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["email"] == "capitao_teste@mvl.com"
    assert me_data["plan_tier"] == "free"

    # 3. Track a club
    tc_res = client.post(
        "/api/v1/auth/track-club",
        json={"club_id": "682077", "club_name": "MVL ES"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert tc_res.status_code == 201
    assert tc_res.json()["ok"] is True

    # 4. Verify tracked clubs in /me
    me_after = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert len(me_after.json()["tracked_clubs"]) == 1
    assert me_after.json()["tracked_clubs"][0]["club_id"] == "682077"

    # 5. Login
    login_res = client.post(
        "/api/v1/auth/login",
        json={"email": "capitao_teste@mvl.com", "password": "mypassword123"},
    )
    assert login_res.status_code == 200
    assert login_res.json()["ok"] is True


def test_public_ea_cached_endpoint(client: TestClient) -> None:
    res = client.get("/api/v1/public/ea/clubs/682077")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["club"]["club_id"] == "682077"
    assert "fetched_at" in data["club"]


def test_checkout_pix_and_webhook_upgrade(client: TestClient) -> None:
    # Register
    reg_res = client.post(
        "/api/v1/auth/register",
        json={"email": "comprador_pro@mvl.com", "password": "senhaPro123"},
    )
    token = reg_res.json()["token"]

    # Generate PIX checkout
    checkout_res = client.post(
        "/api/v1/checkout/pix",
        json={"plan_tier": "pro", "gateway": "mock"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert checkout_res.status_code == 200
    order_data = checkout_res.json()
    order_id = order_data["order_id"]
    assert order_data["status"] == "pending"
    assert order_data["amount_cents"] == PRO_PRICE_CENTS

    # Check order status before payment
    status_res = client.get(f"/api/v1/checkout/order/{order_id}/status")
    assert status_res.status_code == 200
    assert status_res.json()["status"] == "pending"

    # Simulate payment
    sim_res = client.post(f"/api/v1/checkout/order/{order_id}/simulate-pay")
    assert sim_res.status_code == 200
    assert sim_res.json()["status"] == "paid"
    assert sim_res.json()["user_plan"] == "pro"

    # Check /me to confirm user was promoted to PRO
    me_res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.status_code == 200
    assert me_res.json()["plan_tier"] == "pro"


def test_landing_page_and_dashboard_routing(client: TestClient) -> None:
    # 1. Visitante anonimo em / acessa o Hub de Decisao
    res = client.get("/")
    assert res.status_code == 200
    assert "UPZIN" in res.text
    assert "Autoplay" in res.text
    assert "Scouts" in res.text

    # 2. Rota explicita /landing
    landing_res = client.get("/landing")
    assert landing_res.status_code == 200
    assert "piloto automático" in landing_res.text.lower()

    # 3. Pagina dedicada /autoplay
    autoplay_res = client.get("/autoplay")
    assert autoplay_res.status_code == 200
    assert "Autoplay" in autoplay_res.text
    assert "PlayStation" in autoplay_res.text or "PS5" in autoplay_res.text

    # 4. Pagina dedicada /scouts
    scouts_res = client.get("/scouts")
    assert scouts_res.status_code == 200
    assert "Scouts" in scouts_res.text
    assert "EA" in scouts_res.text

    # 5. Rota /dashboard
    dash_res = client.get("/dashboard")
    assert dash_res.status_code == 200


def test_dashboard_20_matches_rule(client: TestClient) -> None:
    # 1. Register a Free user
    reg_res = client.post(
        "/api/v1/auth/register",
        json={"email": "usuario_free@mvl.com", "password": "freeUser123"},
    )
    token = reg_res.json()["token"]

    # 2. Access dashboard / and /partidas with session cookie
    client.cookies.set("fc27_session", token)
    home_res = client.get("/")
    assert home_res.status_code == 200
    assert "usuario_free@mvl.com" in home_res.text
    assert "Meus Clubes" in home_res.text

    matches_res = client.get("/partidas")
    assert matches_res.status_code == 200

    # 3. Test plans page
    plans_res = client.get("/planos")
    assert plans_res.status_code == 200
    assert "Plano PRO" in plans_res.text

    # 4. Test tracked clubs page
    tc_page = client.get("/meus-clubes")
    assert tc_page.status_code == 200

    # 5. Add club via HTML form
    add_res = client.post("/meus-clubes/adicionar", data={"club_id": "12345", "club_name": "Clube Teste"})
    assert add_res.status_code in (200, 303)

    # 6. Test explore page
    exp_res = client.get("/explorar?club_id=12345")
    assert exp_res.status_code == 200
    assert "12345" in exp_res.text


def test_payment_webhook_asaas(client: TestClient) -> None:
    # 1. Register
    reg = client.post(
        "/api/v1/auth/register",
        json={"email": "asaas_user@mvl.com", "password": "password123"},
    )
    token = reg.json()["token"]

    # 2. Checkout
    order_res = client.post(
        "/api/v1/checkout/pix",
        json={"plan_tier": "pro", "gateway": "asaas"},
        headers={"Authorization": f"Bearer {token}"},
    )
    order_id = order_res.json()["order_id"]

    # 3. Trigger Asaas Webhook
    webhook_res = client.post(
        "/api/v1/payments/webhook",
        json={
            "event": "PAYMENT_RECEIVED",
            "payment": {
                "id": "pay_asaas_999",
                "externalReference": order_id,
            },
        },
    )
    assert webhook_res.status_code == 200
    assert webhook_res.json()["processed"] == "asaas"

    # 4. Confirm user upgraded
    me_res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.json()["plan_tier"] == "pro"


def test_search_club_by_name_and_user_onboarding(client: TestClient) -> None:
    # 1. API pública de busca por nome
    search_res = client.get("/api/v1/public/ea/search?q=MVL")
    assert search_res.status_code == 200
    search_data = search_res.json()
    assert search_data["ok"] is True
    assert search_data["count"] > 0
    assert any("MVL" in c["name"].upper() for c in search_data["clubs"])

    # 2. Cadastro de novo usuario sem clube
    reg = client.post("/cadastro", data={"email": "capitao_novo_clube@upzin.com", "password": "senhaSegura123"}, follow_redirects=False)
    assert reg.status_code == 303
    assert "onboarding=1" in reg.headers.get("location")
    session_token = reg.cookies.get("fc27_session")

    # 3. Usuario novo sem clube ao acessar /dashboard deve ser direcionado para /meus-clubes (nao vê MVL)
    dash_redirect = client.get("/dashboard", cookies={"fc27_session": session_token}, follow_redirects=False)
    assert dash_redirect.status_code == 303
    assert "meus-clubes" in dash_redirect.headers.get("location")

    # 4. Busca por nome diretamente na tela /meus-clubes
    mc_page = client.get("/meus-clubes?q=MVL", cookies={"fc27_session": session_token})
    assert mc_page.status_code == 200
    assert "MVL" in mc_page.text

    # 5. Adiciona o clube encontrado
    first_club = search_data["clubs"][0]
    add_res = client.post(
        "/meus-clubes/adicionar",
        data={"club_id": first_club["club_id"], "club_name": first_club["name"]},
        cookies={"fc27_session": session_token},
        follow_redirects=False,
    )
    assert add_res.status_code == 303
    assert f"club_id={first_club['club_id']}" in add_res.headers.get("location")
