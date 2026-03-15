from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.models import EventLog
from app.repositories import Repository
from app.types import ChatState, MessageDirection
from app.services.stats_reporting import StatsReportingService


class DummyAvito:
    pass


class DummyTelegram:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str):
        self.messages.append((chat_id, text))


class FixedNowStatsReportingService(StatsReportingService):
    def __init__(self, *args, fixed_now_utc: datetime, **kwargs):
        super().__init__(*args, **kwargs)
        self._fixed_now_utc = fixed_now_utc

    def _now_utc(self) -> datetime:
        return self._fixed_now_utc


def test_stats_report_once_sends_message(db_session):
    repo = Repository(db_session)
    now = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

    ad = repo.upsert_ad("ad-1", "Комната 18 м2", "REAL_ESTATE", "Недвижимость", None)
    chat = repo.upsert_chat("chat-1", ad.id, "Иван")
    repo.save_message(
        chat_id=chat.id,
        direction=MessageDirection.INBOUND,
        text="Здравствуйте, есть свободные места?",
        external_message_id="msg-1",
        payload_json="{}",
        created_at=now - timedelta(hours=2),
    )
    repo.create_lead(
        chat_id=chat.id,
        contact_raw="+79990000000",
        contact_normalized="+79990000000",
        summary="Клиент просит перезвонить",
    )
    db_session.add(
        EventLog(
            source="avito",
            event_type="incoming_message",
            idempotency_key="avito:evt-1",
            payload_hash="hash-1",
            status="FAILED",
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
        )
    )
    db_session.commit()

    settings = Settings(
        polling_enabled=False,
        stats_reporting_enabled=True,
        telegram_stats_chat_id="-100500",
        stats_report_interval_hours=24,
    )
    tg = DummyTelegram()
    service = FixedNowStatsReportingService(
        settings=settings,
        avito_client=DummyAvito(),
        telegram_client=tg,
        fixed_now_utc=now,
    )

    result = service.report_once(db_session)
    assert result.sent is True
    assert result.target_chat_id == "-100500"
    assert result.anomaly_count == 1
    assert result.unanswered_count == 1
    assert result.new_contacts_count == 1
    assert len(tg.messages) == 1
    payload = tg.messages[0][1]
    assert "Ежедневная сводка бота" in payload
    assert "ANOMALY: 1" in payload
    assert "Неотвеченные: 1" in payload
    assert "Новые контакты: 1" in payload


def test_stats_report_run_if_due_respects_interval(db_session):
    fixed_now = datetime(2026, 3, 1, 9, 5, tzinfo=timezone.utc)
    settings = Settings(
        polling_enabled=False,
        stats_reporting_enabled=True,
        telegram_stats_chat_id="-100500",
        stats_report_interval_hours=24,
    )
    tg = DummyTelegram()
    service = FixedNowStatsReportingService(
        settings=settings,
        avito_client=DummyAvito(),
        telegram_client=tg,
        fixed_now_utc=fixed_now,
    )

    first = service.run_if_due(db_session)
    assert first is not None
    assert first.sent is True
    assert len(tg.messages) == 1

    second = service.run_if_due(db_session)
    assert second is None
    assert len(tg.messages) == 1

    repo = Repository(db_session)
    assert repo.get_setting(service.LAST_RUN_KEY) is not None


def test_stats_report_does_not_mark_run_when_target_chat_missing(db_session):
    fixed_now = datetime(2026, 3, 1, 9, 5, tzinfo=timezone.utc)
    settings = Settings(
        polling_enabled=False,
        stats_reporting_enabled=True,
        stats_report_interval_hours=24,
        telegram_stats_chat_id="",
        telegram_qa_chat_id="",
        telegram_leads_chat_id="",
    )
    tg = DummyTelegram()
    service = FixedNowStatsReportingService(
        settings=settings,
        avito_client=DummyAvito(),
        telegram_client=tg,
        fixed_now_utc=fixed_now,
    )

    result = service.run_if_due(db_session)
    assert result is not None
    assert result.sent is False
    assert tg.messages == []

    repo = Repository(db_session)
    assert repo.get_setting(service.LAST_RUN_KEY) is None
