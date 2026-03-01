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
    app.state.container = SimpleNamespace(settings=settings)

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

    funnel = client.get("/api/cabinet/funnel")
    assert funnel.status_code == 200
    assert funnel.json()["total"] == 1

    leads = client.get("/api/cabinet/leads")
    assert leads.status_code == 200
    body = leads.json()
    assert body["total"] == 1
    assert body["items"][0]["source"] == "youla"
    lead_id = body["items"][0]["id"]

    update = client.patch(f"/api/cabinet/leads/{lead_id}/status", json={"status": "PAID"})
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
    )
    assert new_task.status_code == 200
    task_id = new_task.json()["id"]
    assert new_task.json()["status"] == "OPEN"

    done_task = client.patch(f"/api/cabinet/tasks/{task_id}", json={"status": "DONE"})
    assert done_task.status_code == 200
    assert done_task.json()["status"] == "DONE"


def test_cabinet_rejects_default_session_secret():
    session_factory = _build_session_factory()
    settings = Settings(
        polling_enabled=False,
        dashboard_owner_username="owner",
        dashboard_owner_password="owner-pass",
    )
    app = _build_app(settings=settings, session_factory=session_factory)
    client = TestClient(app)

    login = client.post("/api/cabinet/login", json={"username": "owner", "password": "owner-pass"})
    assert login.status_code == 503
    assert "session secret" in login.json()["detail"]
