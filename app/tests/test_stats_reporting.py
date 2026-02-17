from __future__ import annotations

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
            "url": f"https://www.avito.ru/item/{item_id}",
        }


class DummyTelegram:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str):
        self.messages.append((chat_id, text))


def test_stats_report_once_sends_message(db_session):
    settings = Settings(
        polling_enabled=False,
        stats_reporting_enabled=True,
        telegram_stats_chat_id="-100500",
        stats_report_min_views_for_conversion=30,
        stats_report_low_conversion_threshold=3.5,
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


def test_stats_report_run_if_due_respects_interval(db_session):
    settings = Settings(
        polling_enabled=False,
        stats_reporting_enabled=True,
        telegram_stats_chat_id="-100500",
        stats_report_interval_hours=24,
    )
    tg = DummyTelegram()
    service = StatsReportingService(
        settings=settings,
        avito_client=DummyAvito(),
        telegram_client=tg,
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
