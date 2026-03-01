from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import Settings
from app.models import EventLog
from app.services.heartbeat import BotHealthHeartbeatService


class DummyTelegram:
    enabled = True

    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str) -> None:
        self.messages.append((chat_id, text))


def test_heartbeat_sends_ok_message_when_due(db_session):
    telegram = DummyTelegram()
    settings = Settings(
        polling_enabled=False,
        bot_health_enabled=True,
        bot_health_interval_minutes=30,
        bot_health_chat_id="-7001",
        bot_health_send_ok_messages=True,
        bot_health_daily_report_enabled=False,
    )
    service = BotHealthHeartbeatService(settings=settings, telegram_client=telegram)

    db_session.add(
        EventLog(
            source="avito",
            event_type="incoming_message",
            idempotency_key="avito:test-1",
            payload_hash="x",
            status="PROCESSED",
            created_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()

    service.run_if_due(db_session)

    assert len(telegram.messages) == 1
    assert telegram.messages[0][0] == "-7001"
    assert "Heartbeat: OK" in telegram.messages[0][1]


def test_notify_poll_failure_is_throttled(db_session):
    telegram = DummyTelegram()
    settings = Settings(
        polling_enabled=False,
        bot_health_enabled=True,
        bot_health_alert_repeat_minutes=60,
        bot_health_chat_id="-7001",
    )
    service = BotHealthHeartbeatService(settings=settings, telegram_client=telegram)

    service.notify_poll_failure(db_session, "403 Forbidden")
    service.notify_poll_failure(db_session, "403 Forbidden")

    assert len(telegram.messages) == 1
    assert "poller ошибка" in telegram.messages[0][1]
