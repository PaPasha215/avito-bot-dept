from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import Settings
from app.repositories import Repository
from app.services.stats_reporting import StatsReportingService


class DummyAvito:
    def get_item_analytics(self, **kwargs):
        return {
            "result": {
                "groupings": [
                    {
                        "id": 1001,
                        "metrics": [
                            {"slug": "views", "value": 200},
                            {"slug": "contacts", "value": 10},
                            {"slug": "viewsToContactsConversion", "value": 5.0},
                            {"slug": "favorites", "value": 15},
                            {"slug": "impressions", "value": 400},
                            {"slug": "impressionsToViewsConversion", "value": 50.0},
                        ],
                    },
                    {
                        "id": 1002,
                        "metrics": [
                            {"slug": "views", "value": 120},
                            {"slug": "contacts", "value": 2},
                            {"slug": "viewsToContactsConversion", "value": 1.66},
                            {"slug": "favorites", "value": 9},
                            {"slug": "impressions", "value": 500},
                            {"slug": "impressionsToViewsConversion", "value": 24.0},
                        ],
                    },
                ]
            }
        }

    def get_account_item(self, item_id):
        return {
            "status": "active",
            "url": f"https://www.avito.ru/ekaterinburg/komnaty/koyko-mesto_20_m_{item_id}",
        }


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
    settings = Settings(
        polling_enabled=False,
        stats_reporting_enabled=True,
        telegram_stats_chat_id="-100500",
        stats_report_min_views_for_conversion=30,
        stats_report_low_conversion_threshold=3.5,
        stats_report_timezone="UTC",
    )
    tg = DummyTelegram()
    service = StatsReportingService(
        settings=settings,
        avito_client=DummyAvito(),
        telegram_client=tg,
    )

    result = service.report_once(db_session)
    assert result.sent is True
    assert result.target_chat_id == "-100500"
    assert len(tg.messages) == 1
    payload = tg.messages[0][1]
    assert "Топ по охвату" in payload
    assert "Кандидаты на переработку" in payload
    assert "#1002" in payload
    assert "просмотры" in payload
    assert "конверсия" in payload


def test_stats_report_run_if_due_weekly_schedule(db_session):
    fixed_now = datetime(2026, 2, 16, 9, 5, tzinfo=timezone.utc)  # Monday
    settings = Settings(
        polling_enabled=False,
        stats_reporting_enabled=True,
        telegram_stats_chat_id="-100500",
        stats_report_weekday=1,
        stats_report_hour=9,
        stats_report_minute=0,
        stats_report_timezone="UTC",
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
