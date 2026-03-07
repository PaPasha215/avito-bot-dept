from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import get_db, router
from app.core.config import Settings
from app.models import Base
from app.repositories import Repository
from app.types import MessageDirection


def _build_app(settings: Settings, session_factory) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    prompt_service = SimpleNamespace(
        get_real_estate_prompt=lambda db: SimpleNamespace(
            key="real_estate",
            version="REAL_ESTATE_PROMPT_V1",
            text="x" * 40,
            updated_at=None,
        ),
        update_real_estate_prompt=lambda db, version, text: SimpleNamespace(
            key="real_estate",
            version=version,
            text=text,
            updated_at=None,
        ),
    )
    learning_service = SimpleNamespace(
        get_status=lambda db: SimpleNamespace(
            enabled=True,
            active_version="SL-1",
            stable_version="SL-1",
            candidate_started_at=None,
            last_run_at=None,
            active_examples=1,
            stable_examples=1,
            total_examples=1,
        )
    )
    app.state.container = SimpleNamespace(
        settings=settings,
        prompt_service=prompt_service,
        self_learning_service=learning_service,
    )

    def override_get_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    return app


def _build_session_factory():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _seed_lead(session_factory):
    with session_factory() as db_session:
        repo = Repository(db_session)
        ad = repo.upsert_ad(
            external_ad_id="youla:ad-1",
            title="Комната 18 м2",
            category="REAL_ESTATE",
            raw_category="Недвижимость",
            url=None,
        )
        chat = repo.upsert_chat(external_chat_id="youla:chat-1", ad_id=ad.id, customer_name="Иван")
        lead = repo.create_lead(
            chat_id=chat.id,
            contact_raw="+7 999 111-22-33",
            contact_normalized="+79991112233",
            summary="Нужна комната на месяц, бюджет до 20к",
        )
        assert lead is not None
        repo.save_message(
            chat_id=chat.id,
            direction=MessageDirection.INBOUND,
            text="Здравствуйте, есть свободные места?",
            external_message_id="m1",
            payload_json=None,
        )
        db_session.commit()


def test_cabinet_login_and_me():
    session_factory = _build_session_factory()
    settings = Settings(
        polling_enabled=False,
        dashboard_owner_username="owner",
        dashboard_owner_password="owner-pass",
        dashboard_session_secret="test-secret",
    )
    app = _build_app(settings=settings, session_factory=session_factory)
    client = TestClient(app)

    login = client.post("/api/cabinet/login", json={"username": "owner", "password": "owner-pass"})
    assert login.status_code == 200
    payload = login.json()
    assert payload["ok"] is True
    assert payload["role"] == "owner"

    me = client.get("/api/cabinet/me")
    assert me.status_code == 200
    assert me.json()["username"] == "owner"

    cabinet = client.get("/cabinet")
    assert cabinet.status_code == 200
    html = cabinet.text
    assert 'class="stage-funnel"' in html
    assert 'const statusValues = ["NEW","IN_PROGRESS","PAYMENT_PENDING","PAID","CALL_NEEDED","LOST"]' in html
    assert 'NEW: "Новый лид"' in html
    assert 'PAYMENT_PENDING: "Договорился о встрече"' in html
    assert 'PAID: "Заселил"' in html
    assert '{code: "", label: "Все этапы", count: total}' in html

    protected = client.get("/api/chats")
    assert protected.status_code == 200


def test_cabinet_leads_and_status_update():
    session_factory = _build_session_factory()
    _seed_lead(session_factory)
    settings = Settings(
        polling_enabled=False,
        dashboard_owner_username="owner",
        dashboard_owner_password="owner-pass",
        dashboard_session_secret="test-secret",
    )
    app = _build_app(settings=settings, session_factory=session_factory)
    client = TestClient(app)

    unauthorized = client.get("/api/cabinet/leads")
    assert unauthorized.status_code == 401

    login = client.post("/api/cabinet/login", json={"username": "owner", "password": "owner-pass"})
    assert login.status_code == 200
    csrf = client.cookies.get("rk_cab_csrf")
    assert csrf

    funnel = client.get("/api/cabinet/funnel")
    assert funnel.status_code == 200
    assert funnel.json()["total"] == 1

    leads = client.get("/api/cabinet/leads")
    assert leads.status_code == 200
    body = leads.json()
    assert body["total"] == 1
    assert body["items"][0]["source"] == "youla"
    lead_id = body["items"][0]["id"]

    update = client.patch(
        f"/api/cabinet/leads/{lead_id}/status",
        json={"status": "PAID"},
        headers={"X-CSRF-Token": csrf},
    )
    assert update.status_code == 200
    assert update.json()["status"] == "PAID"

    detail = client.get(f"/api/cabinet/leads/{lead_id}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["lead"]["id"] == lead_id
    assert len(detail_payload["messages"]) == 1
    assert detail_payload["tasks"] == []

    new_task = client.post(
        f"/api/cabinet/leads/{lead_id}/tasks",
        json={"title": "Перезвонить клиенту"},
        headers={"X-CSRF-Token": csrf},
    )
    assert new_task.status_code == 200
    task_id = new_task.json()["id"]
    assert new_task.json()["status"] == "OPEN"

    done_task = client.patch(
        f"/api/cabinet/tasks/{task_id}",
        json={"status": "DONE"},
        headers={"X-CSRF-Token": csrf},
    )
    assert done_task.status_code == 200
    assert done_task.json()["status"] == "DONE"


def test_cabinet_rejects_default_session_secret():
    session_factory = _build_session_factory()
    settings = Settings(
        polling_enabled=False,
        dashboard_owner_username="owner",
        dashboard_owner_password="owner-pass",
        dashboard_session_secret="change-this-dashboard-secret",
    )
    app = _build_app(settings=settings, session_factory=session_factory)
    client = TestClient(app)

    login = client.post("/api/cabinet/login", json={"username": "owner", "password": "owner-pass"})
    assert login.status_code == 503
    assert "session secret" in login.json()["detail"]


def test_cabinet_state_change_requires_csrf():
    session_factory = _build_session_factory()
    _seed_lead(session_factory)
    settings = Settings(
        polling_enabled=False,
        dashboard_owner_username="owner",
        dashboard_owner_password="owner-pass",
        dashboard_session_secret="test-secret",
    )
    app = _build_app(settings=settings, session_factory=session_factory)
    client = TestClient(app)

    login = client.post("/api/cabinet/login", json={"username": "owner", "password": "owner-pass"})
    assert login.status_code == 200

    leads = client.get("/api/cabinet/leads")
    lead_id = leads.json()["items"][0]["id"]
    update = client.patch(f"/api/cabinet/leads/{lead_id}/status", json={"status": "PAID"})
    assert update.status_code == 403


def test_public_sensitive_api_requires_auth():
    session_factory = _build_session_factory()
    settings = Settings(
        polling_enabled=False,
        dashboard_owner_username="owner",
        dashboard_owner_password="owner-pass",
        dashboard_session_secret="test-secret",
    )
    app = _build_app(settings=settings, session_factory=session_factory)
    client = TestClient(app)

    assert client.get("/api/chats").status_code == 401
    assert client.get("/api/leads").status_code == 401
    assert client.get("/api/learning/status").status_code == 401
    assert client.get("/api/prompts/real-estate").status_code == 401
