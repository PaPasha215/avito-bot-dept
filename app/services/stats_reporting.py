from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import utcnow
from app.integrations.avito import AvitoClient
from app.integrations.telegram import TelegramClient
from app.repositories import Repository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StatsReportRunResult:
    report_date: str
    sent: bool
    target_chat_id: str | None
    items_total: int
    top_reach_count: int
    top_conversion_count: int
    low_conversion_count: int


@dataclass(slots=True)
class StatsItem:
    item_id: int
    url: str | None
    status: str | None
    views: float
    contacts: float
    conversion: float
    favorites: float
    impressions: float
    impressions_to_views: float


class StatsReportingService:
    LAST_RUN_KEY = "stats_report_last_run_at"

    def __init__(self, settings: Settings, avito_client: AvitoClient, telegram_client: TelegramClient):
        self.settings = settings
        self.avito_client = avito_client
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
                items_total=0,
                top_reach_count=0,
                top_conversion_count=0,
                low_conversion_count=0,
            )

        report_date_to = utcnow().date()
        report_date_from = report_date_to - timedelta(days=max(1, self.settings.stats_report_lookback_days - 1))
        raw = self.avito_client.get_item_analytics(
            date_from=report_date_from.isoformat(),
            date_to=report_date_to.isoformat(),
            metrics=[
                "views",
                "contacts",
                "viewsToContactsConversion",
                "favorites",
                "impressions",
                "impressionsToViewsConversion",
                "contactsMessenger",
                "contactsShowPhone",
            ],
            grouping="item",
            limit=max(10, min(self.settings.stats_report_limit, 200)),
            offset=0,
            sort={"key": "views", "order": "desc"},
        )
        items = self._parse_items(raw)
        items = self._enrich_items(items)

        text, top_reach_count, top_conv_count, low_conv_count = self._build_report_text(
            items=items,
            date_from=report_date_from,
            date_to=report_date_to,
        )
        self.telegram_client.send_message(target_chat_id, text)
        return StatsReportRunResult(
            report_date=report_date_to.isoformat(),
            sent=True,
            target_chat_id=target_chat_id,
            items_total=len(items),
            top_reach_count=top_reach_count,
            top_conversion_count=top_conv_count,
            low_conversion_count=low_conv_count,
        )

    def _resolve_target_chat_id(self) -> str | None:
        return self.settings.telegram_stats_chat_id or self.settings.telegram_qa_chat_id or self.settings.telegram_leads_chat_id

    def _parse_items(self, payload: dict) -> list[StatsItem]:
        result = payload.get("result") if isinstance(payload, dict) else {}
        groupings = result.get("groupings") if isinstance(result, dict) else []
        items: list[StatsItem] = []
        for group in groupings or []:
            if not isinstance(group, dict):
                continue
            item_id = group.get("id")
            if item_id is None:
                continue
            metrics_raw = group.get("metrics") if isinstance(group.get("metrics"), list) else []
            metrics = {m.get("slug"): m.get("value") for m in metrics_raw if isinstance(m, dict)}
            items.append(
                StatsItem(
                    item_id=int(item_id),
                    url=None,
                    status=None,
                    views=float(metrics.get("views") or 0),
                    contacts=float(metrics.get("contacts") or 0),
                    conversion=float(metrics.get("viewsToContactsConversion") or 0),
                    favorites=float(metrics.get("favorites") or 0),
                    impressions=float(metrics.get("impressions") or 0),
                    impressions_to_views=float(metrics.get("impressionsToViewsConversion") or 0),
                )
            )
        return items

    def _enrich_items(self, items: list[StatsItem]) -> list[StatsItem]:
        enriched: list[StatsItem] = []
        for item in items[: self.settings.stats_report_item_detail_limit]:
            try:
                meta = self.avito_client.get_account_item(item.item_id)
            except Exception as exc:  # noqa: BLE001
                logger.info("Item details skipped for %s: %s", item.item_id, exc)
                enriched.append(item)
                continue
            item.url = str(meta.get("url")) if meta.get("url") else None
            item.status = str(meta.get("status")) if meta.get("status") else None
            enriched.append(item)
        if len(items) > len(enriched):
            enriched.extend(items[len(enriched):])
        return enriched

    def _build_report_text(
        self,
        items: list[StatsItem],
        date_from: datetime.date,
        date_to: datetime.date,
    ) -> tuple[str, int, int, int]:
        reach = sorted(items, key=lambda x: x.views, reverse=True)[:5]
        conv_candidates = [x for x in items if x.views >= self.settings.stats_report_min_views_for_conversion]
        conversion = sorted(conv_candidates, key=lambda x: x.conversion, reverse=True)[:5]
        low_conv = sorted(
            [x for x in conv_candidates if x.conversion <= self.settings.stats_report_low_conversion_threshold],
            key=lambda x: (-x.views, x.conversion),
        )[:5]

        lines = [
            "Отчет Avito: объявления",
            f"Период: {date_from.isoformat()} — {date_to.isoformat()}",
            f"Объявлений в выборке: {len(items)}",
            "",
            "1) Топ по охвату (views):",
        ]
        if not reach:
            lines.append("- Нет данных")
        for idx, item in enumerate(reach, start=1):
            lines.append(
                f"{idx}. #{item.item_id} | views {int(item.views)} | contacts {int(item.contacts)} | "
                f"conv {item.conversion:.2f}% | fav {int(item.favorites)} | status {item.status or '-'}"
            )
            if item.url:
                lines.append(f"   {item.url}")

        lines.extend(["", "2) Топ по конверсии (при достаточном трафике):"])
        if not conversion:
            lines.append("- Нет данных")
        for idx, item in enumerate(conversion, start=1):
            lines.append(
                f"{idx}. #{item.item_id} | conv {item.conversion:.2f}% | views {int(item.views)} | contacts {int(item.contacts)}"
            )
            if item.url:
                lines.append(f"   {item.url}")

        lines.extend(["", "3) Кандидаты на переработку (много views, низкая conv):"])
        if not low_conv:
            lines.append("- Нет явных кандидатов")
        for idx, item in enumerate(low_conv, start=1):
            lines.append(
                f"{idx}. #{item.item_id} | views {int(item.views)} | conv {item.conversion:.2f}% | contacts {int(item.contacts)}"
            )
            if item.url:
                lines.append(f"   {item.url}")

        lines.extend(
            [
                "",
                "Рекомендации на сегодня:",
                "- Для карточек из блока 3: переписать 1-2 первые строки описания с ценой, сроком и форматом заселения.",
                "- Проверить главное фото и заголовок: формат + выгода + цена.",
                "- Для лидеров из блока 2 сделать 2-3 похожих варианта объявлений (A/B заголовки).",
            ]
        )
        return "\n".join(lines), len(reach), len(conversion), len(low_conv)

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
