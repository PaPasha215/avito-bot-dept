from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import utcnow
from app.integrations.telegram import TelegramClient
from app.repositories import Repository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StatsReportRunResult:
    report_date: str
    sent: bool
    target_chat_id: str | None
    anomaly_count: int
    unanswered_count: int
    new_contacts_count: int


class StatsReportingService:
    LAST_RUN_KEY = "stats_report_last_run_at"

    def __init__(self, settings: Settings, avito_client, telegram_client: TelegramClient):
        self.settings = settings
        self.telegram_client = telegram_client

    @property
    def enabled(self) -> bool:
        return bool(self.settings.stats_reporting_enabled)

    def run_if_due(self, db: Session) -> StatsReportRunResult | None:
        if not self.enabled:
            return None

        repo = Repository(db)
        now = utcnow()
        last_run = self._parse_dt(repo.get_setting(self.LAST_RUN_KEY))
        if last_run is not None and (now - last_run) < timedelta(hours=max(1, self.settings.stats_report_interval_hours)):
            return None

        result = self.report_once(db)
        repo.set_setting(self.LAST_RUN_KEY, now.isoformat())
        db.commit()
        return result

    def report_once(self, db: Session) -> StatsReportRunResult:
        target_chat_id = self._resolve_target_chat_id()
        if not target_chat_id:
            logger.info("Stats report skipped: target Telegram chat is not configured")
            return StatsReportRunResult(
                report_date=utcnow().date().isoformat(),
                sent=False,
                target_chat_id=None,
                anomaly_count=0,
                unanswered_count=0,
                new_contacts_count=0,
            )

        repo = Repository(db)
        report_ended_at = utcnow()
        report_started_at = report_ended_at - timedelta(hours=max(1, self.settings.stats_report_interval_hours))
        metrics = repo.get_operational_report_metrics(
            since=report_started_at,
            until=report_ended_at,
        )
        text = self._build_report_text(
            report_started_at=report_started_at,
            report_ended_at=report_ended_at,
            anomaly_count=metrics["anomaly_count"],
            unanswered_count=metrics["unanswered_count"],
            new_contacts_count=metrics["new_contacts_count"],
        )
        self.telegram_client.send_message(target_chat_id, text)
        return StatsReportRunResult(
            report_date=report_ended_at.date().isoformat(),
            sent=True,
            target_chat_id=target_chat_id,
            anomaly_count=metrics["anomaly_count"],
            unanswered_count=metrics["unanswered_count"],
            new_contacts_count=metrics["new_contacts_count"],
        )

    def _resolve_target_chat_id(self) -> str | None:
        return self.settings.telegram_stats_chat_id or self.settings.telegram_qa_chat_id or self.settings.telegram_leads_chat_id

    def _build_report_text(
        self,
        report_started_at: datetime,
        report_ended_at: datetime,
        anomaly_count: int,
        unanswered_count: int,
        new_contacts_count: int,
    ) -> str:
        lines = [
            "Ежедневная сводка бота",
            f"Период: {report_started_at.strftime('%Y-%m-%d %H:%M')} - {report_ended_at.strftime('%Y-%m-%d %H:%M')}",
            "",
            f"ANOMALY: {anomaly_count}",
            f"Неотвеченные: {unanswered_count}",
            f"Новые контакты: {new_contacts_count}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
