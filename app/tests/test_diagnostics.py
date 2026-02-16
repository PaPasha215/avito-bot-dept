from __future__ import annotations

from app.repositories import Repository
from app.types import MessageDirection


def test_chat_diagnostics_aggregates_related_entities(db_session):
    repo = Repository(db_session)

    ad = repo.upsert_ad(
        external_ad_id="ad-100",
        title="Комната 15 м2",
        category="REAL_ESTATE",
        raw_category="Недвижимость",
        url="https://example.com/ad-100",
    )
    chat = repo.upsert_chat(external_chat_id="chat-ext-100", ad_id=ad.id, customer_name="Иван")

    repo.save_message(
        chat_id=chat.id,
        direction=MessageDirection.INBOUND,
        text="Здравствуйте",
        external_message_id="msg-in-1",
        payload_json="{}",
    )
    repo.save_message(
        chat_id=chat.id,
        direction=MessageDirection.OUTBOUND,
        text="Добрый день",
        external_message_id=None,
        payload_json=None,
    )
    repo.create_routing_decision(
        chat_id=chat.id,
        domain="REAL_ESTATE",
        confidence=0.95,
        decision="REPLY",
        reason="real_estate_flow",
    )
    repo.create_bot_reply(
        chat_id=chat.id,
        prompt_version="REAL_ESTATE_PROMPT_V1",
        text="Добрый день",
        status="SENT",
    )
    repo.create_lead(
        chat_id=chat.id,
        contact_raw="+79990001122",
        contact_normalized="+79990001122",
        summary="Клиент заинтересован",
    )
    repo.create_feedback_event(chat_id=chat.id, tag="WRONG_TONE", comment="слишком формально")

    event = repo.start_event(
        source="avito",
        event_type="incoming_message",
        idempotency_key="avito:chat-ext-100:msg-in-1",
        payload="{}",
    )
    assert event is not None
    repo.mark_event_processed(event.id)

    unrelated = repo.start_event(
        source="avito",
        event_type="incoming_message",
        idempotency_key="avito:another-chat:msg-in-1",
        payload="{}",
    )
    assert unrelated is not None
    repo.mark_event_processed(unrelated.id)
    db_session.commit()

    diagnostics = repo.get_chat_diagnostics("chat-ext-100", message_limit=100, event_limit=100)

    assert diagnostics is not None
    assert diagnostics["chat"].external_chat_id == "chat-ext-100"
    assert diagnostics["inbound_count"] == 1
    assert diagnostics["outbound_count"] == 1
    assert len(diagnostics["messages"]) == 2
    assert len(diagnostics["routing_decisions"]) == 1
    assert len(diagnostics["bot_replies"]) == 1
    assert len(diagnostics["leads"]) == 1
    assert len(diagnostics["feedback_events"]) == 1
    assert len(diagnostics["event_logs"]) == 1
    assert diagnostics["event_logs"][0].idempotency_key == "avito:chat-ext-100:msg-in-1"
