from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from app.core.config import Settings


class DummyProcessor:
    def __init__(self):
        self.events = []

    def process_incoming_event(self, db, event, allow_reply: bool = True):
        self.events.append((event, allow_reply))
        return "processed"


def _build_app(settings: Settings, processor: DummyProcessor) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.container = SimpleNamespace(settings=settings, processor=processor)
    return app


def test_youla_webhook_message_incom_is_transformed_to_incoming_event():
    settings = Settings(polling_enabled=False, youla_enabled=True, youla_force_real_estate=True)
    processor = DummyProcessor()
    app = _build_app(settings=settings, processor=processor)
    client = TestClient(app)

    payload = {
        "id": "605cd81b1af4961fe85df344",
        "chat_id": "605cd81b1af4961fe85df342",
        "sender_id": "5c78f0a10eae8d155122334a",
        "recipient_id": "604fecb29e708822f201af22",
        "message": "Здравствуйте, есть свободные места?",
        "product_id": "605cd4eadc65e212030b9453",
    }
    response = client.post(
        "/webhooks/youla",
        json=payload,
        headers={
            "Ce-Type": "message.incom",
            "Ce-Id": "07c3004c-4400-49ff-8a4e-96706efb2868",
            "Ce-Time": "2021-03-25T18:40:01.252912Z",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(processor.events) == 1

    event, allow_reply = processor.events[0]
    assert allow_reply is True
    assert event.marketplace == "youla"
    assert event.chat_id == "youla:605cd81b1af4961fe85df342"
    assert event.message_id == "youla:605cd81b1af4961fe85df344"
    assert event.sender_id == "5c78f0a10eae8d155122334a"
    assert event.recipient_id == "604fecb29e708822f201af22"
    assert event.product_id == "605cd4eadc65e212030b9453"
    assert event.ad_context.ad_id == "youla:605cd4eadc65e212030b9453"
    assert event.ad_context.category == "Недвижимость"


def test_youla_webhook_non_message_event_is_ignored():
    settings = Settings(polling_enabled=False, youla_enabled=True)
    processor = DummyProcessor()
    app = _build_app(settings=settings, processor=processor)
    client = TestClient(app)

    response = client.post(
        "/webhooks/youla",
        json={"product_id": "x"},
        headers={"Ce-Type": "product.archive"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert processor.events == []

