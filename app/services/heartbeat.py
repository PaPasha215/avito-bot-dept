from __future__ import annotations

import logging
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.models import Chat, EventLog, Lead, Message
from app.repositories import Repository
from app.types import MessageDirection

logger = logging.getLogger(__name__)


class BotHealthHeartbeatService:
    HEARTBEAT_KEY = "bot_health:last_heartbeat_at"
    DAILY_REPORT_KEY = "bot_health:last_daily_report_at"
    ALERT_PREFIX = "bot_health:last_alert_at:"

    def __init__(self, settings: Settings, telegram_client):
        self.settings = settings
        self.telegram_client = telegram_client

    def run_if_due(self, db: Session) -> None:
        if not self.settings.bot_health_enabled:
            return
        target_chat = self.settings.bot_health_chat_id or self.settings.telegram_leads_chat_id
        if not target_chat or not self.telegram_client.enabled:
            return

        repo = Repository(db)
        now = datetime.now(timezone.utc)
        last_heartbeat = self._read_dt(repo.get_setting(self.HEARTBEAT_KEY))
        interval = max(5, int(self.settings.bot_health_interval_minutes))
        if last_heartbeat is not None and (now - last_heartbeat) < timedelta(minutes=interval):
            return

        avito_30m = self._count_recent_events(db, source="avito", minutes=30)
        pending_favorites = self._count_pending_favorite_chats(db)
        unanswered_avito, unanswered_youla = self._count_unanswered_chats(db)

        if self.settings.bot_health_send_ok_messages:
            text = (
                "Heartbeat: OK\n"
                f"- Avito events /30m: {avito_30m}\n"
                f"- Unanswered chats: Avito {unanswered_avito}, Youla {unanswered_youla}\n"
                f"- Pending favorite chats: {pending_favorites}"
            )
            self.telegram_client.send_message(chat_id=target_chat, text=text)
        repo.set_setting(self.HEARTBEAT_KEY, now.isoformat())

        if unanswered_avito > 0 or unanswered_youla > 0:
            threshold = max(5, int(self.settings.bot_health_unanswered_minutes))
            self._send_alert_with_throttle(
                repo=repo,
                code="unanswered_chats",
                text=(
                    "ALERT: есть входящие диалоги без ответа "
                    f"дольше {threshold} минут. Avito: {unanswered_avito}, Youla: {unanswered_youla}."
                ),
                target_chat_id=target_chat,
            )
        self._send_daily_report_if_due(
            db=db,
            repo=repo,
            target_chat_id=target_chat,
            pending_favorites=pending_favorites,
            unanswered_avito=unanswered_avito,
            unanswered_youla=unanswered_youla,
        )

    def notify_poll_failure(self, db: Session, reason: str) -> None:
        if not self.settings.bot_health_enabled:
            return
        target_chat = self.settings.bot_health_chat_id or self.settings.telegram_leads_chat_id
        if not target_chat or not self.telegram_client.enabled:
            return
        repo = Repository(db)
        self._send_alert_with_throttle(
            repo=repo,
            code="poll_failure",
            text=f"ALERT: poller ошибка: {reason[:400]}",
            target_chat_id=target_chat,
        )

    def _count_recent_events(self, db: Session, *, source: str, minutes: int) -> int:
        since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        return int(
            db.scalar(
                select(func.count(EventLog.id)).where(
                    EventLog.source == source,
                    EventLog.created_at >= since,
                )
            )
            or 0
        )

    def _count_pending_favorite_chats(self, db: Session) -> int:
        repo = Repository(db)
        limit = max(20, int(self.settings.bot_health_check_chat_limit))
        threshold = max(5, int(self.settings.bot_health_pending_favorite_minutes))
        now = datetime.now(timezone.utc)
        count = 0

        for chat, ad in repo.list_chats(limit=limit):
            if (ad.category or "").upper() != "REAL_ESTATE":
                continue
            detail = repo.get_chat_detail(chat.id)
            if detail is None:
                continue
            _, _, history = detail
            favorite_idx = -1
            for idx, msg in enumerate(history):
                if msg.direction != MessageDirection.INBOUND.value:
                    continue
                text = (msg.text or "").lower()
                if "добавил объявление в избранное" in text or "добавил в избранное" in text:
                    favorite_idx = idx
            if favorite_idx < 0:
                continue
            after = history[favorite_idx + 1 :]
            if any(msg.direction == MessageDirection.INBOUND.value and "избранное" not in (msg.text or "").lower() for msg in after):
                continue
            has_outbound = any(msg.direction == MessageDirection.OUTBOUND.value for msg in after)
            if has_outbound:
                continue
            favorite_at = history[favorite_idx].created_at
            if favorite_at is None:
                continue
            if favorite_at.tzinfo is None:
                favorite_at = favorite_at.replace(tzinfo=timezone.utc)
            if (now - favorite_at) >= timedelta(minutes=threshold):
                count += 1
        return count

    def _count_unanswered_chats(self, db: Session) -> tuple[int, int]:
        threshold = datetime.now(timezone.utc) - timedelta(minutes=max(5, int(self.settings.bot_health_unanswered_minutes)))
        unanswered_avito = 0
        unanswered_youla = 0
        chat_rows = db.execute(
            select(Chat.id, Chat.external_chat_id)
            .where(Chat.state != "IGNORED_OUT_OF_SCOPE")
        ).all()
        for chat_id, external_chat_id in chat_rows:
            last_inbound_at = db.scalar(
                select(func.max(Message.created_at)).where(
                    Message.chat_id == chat_id,
                    Message.direction == MessageDirection.INBOUND.value,
                )
            )
            if last_inbound_at is None or last_inbound_at > threshold:
                continue
            last_outbound_at = db.scalar(
                select(func.max(Message.created_at)).where(
                    Message.chat_id == chat_id,
                    Message.direction == MessageDirection.OUTBOUND.value,
                )
            )
            if last_outbound_at is not None and last_outbound_at >= last_inbound_at:
                continue
            if str(external_chat_id or "").startswith("youla:"):
                unanswered_youla += 1
            else:
                unanswered_avito += 1
        return unanswered_avito, unanswered_youla

    def _count_leads_created_since(self, db: Session, since: datetime) -> int:
        return int(db.scalar(select(func.count(Lead.id)).where(Lead.created_at >= since)) or 0)

    def _count_youla_proactive_sent_since(self, db: Session, since: datetime) -> int:
        rows = db.scalars(
            select(Message.payload_json).where(
                Message.direction == MessageDirection.OUTBOUND.value,
                Message.created_at >= since,
                Message.payload_json.is_not(None),
            )
        ).all()
        count = 0
        for payload_raw in rows:
            try:
                payload = json.loads(payload_raw or "")
            except Exception:  # noqa: BLE001
                continue
            if str(payload.get("marketplace", "")).lower() != "youla":
                continue
            if str(payload.get("proactive_reason", "")).strip():
                count += 1
        return count

    def _count_youla_proactive_queue(self, db: Session) -> int:
        repo = Repository(db)
        count = 0
        now = datetime.now(timezone.utc)
        favorite_threshold = timedelta(minutes=max(5, int(self.settings.bot_health_pending_favorite_minutes)))
        silent_day2_threshold = timedelta(hours=24)
        offset = 0
        batch_size = 200

        while True:
            batch = repo.list_youla_housekeeping_chats(limit=batch_size, offset=offset)
            if not batch:
                break
            for chat, _ in batch:
                if repo.list_leads_by_chat(chat.id):
                    continue
                detail = repo.get_chat_detail(chat.id)
                if detail is None:
                    continue
                _, _, history = detail
                if not history:
                    continue

                favorite_idx = -1
                for idx, msg in enumerate(history):
                    if msg.direction == MessageDirection.INBOUND.value and self._is_favorite_message(msg.text or ""):
                        favorite_idx = idx
                if favorite_idx >= 0:
                    after = history[favorite_idx + 1 :]
                    if not any(msg.direction == MessageDirection.OUTBOUND.value for msg in after):
                        favorite_at = self._as_utc(history[favorite_idx].created_at)
                        if favorite_at is not None and (now - favorite_at) >= favorite_threshold:
                            count += 1
                            continue

                last_inbound = self._last_message(history, MessageDirection.INBOUND.value)
                last_outbound = self._last_message(history, MessageDirection.OUTBOUND.value)
                if last_inbound is None or last_outbound is None:
                    continue
                last_inbound_at = self._as_utc(last_inbound.created_at)
                last_outbound_at = self._as_utc(last_outbound.created_at)
                if last_inbound_at is None or last_outbound_at is None:
                    continue
                if last_outbound_at < last_inbound_at:
                    continue
                if self._is_favorite_message(last_inbound.text or ""):
                    continue
                if self._last_outbound_text(history) == "Добрый день! К сожалению, не получили от вас ответа по заселению. Надеемся, у вас все в порядке, и в следующий раз нам удастся с вами помочь.":
                    continue
                if (now - last_outbound_at) >= silent_day2_threshold:
                    count += 1
            offset += len(batch)
        return count

    def _send_daily_report_if_due(
        self,
        *,
        db: Session,
        repo: Repository,
        target_chat_id: str,
        pending_favorites: int,
        unanswered_avito: int,
        unanswered_youla: int,
    ) -> None:
        if not self.settings.bot_health_daily_report_enabled:
            return
        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(self._report_tz())
        scheduled_at = now_local.replace(
            hour=max(0, min(23, int(self.settings.bot_health_daily_report_hour))),
            minute=max(0, min(59, int(self.settings.bot_health_daily_report_minute))),
            second=0,
            microsecond=0,
        )
        if now_local < scheduled_at:
            return

        last_run = self._read_dt(repo.get_setting(self.DAILY_REPORT_KEY))
        if last_run is not None and last_run.astimezone(self._report_tz()).date() == now_local.date():
            return

        day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        day_start_utc = day_start_local.astimezone(timezone.utc)
        leads_today = self._count_leads_created_since(db, day_start_utc)
        youla_proactive_today = self._count_youla_proactive_sent_since(db, day_start_utc)
        youla_queue_left = self._count_youla_proactive_queue(db)
        text = (
            "Ежедневный отчет бота\n"
            f"- Лидов собрано сегодня: {leads_today}\n"
            f"- Youla proactive отправлено сегодня: {youla_proactive_today}\n"
            f"- Очередь Youla follow-up: {youla_queue_left}\n"
            f"- Неотвеченные чаты сейчас: Avito {unanswered_avito}, Youla {unanswered_youla}\n"
            f"- Pending favorite сейчас: {pending_favorites}"
        )
        self.telegram_client.send_message(chat_id=target_chat_id, text=text)
        repo.set_setting(self.DAILY_REPORT_KEY, now_utc.isoformat())

    def _send_alert_with_throttle(self, *, repo: Repository, code: str, text: str, target_chat_id: str) -> None:
        key = f"{self.ALERT_PREFIX}{code}"
        now = datetime.now(timezone.utc)
        last_alert = self._read_dt(repo.get_setting(key))
        repeat_min = max(10, int(self.settings.bot_health_alert_repeat_minutes))
        if last_alert is not None and (now - last_alert) < timedelta(minutes=repeat_min):
            return
        self.telegram_client.send_message(chat_id=target_chat_id, text=text)
        repo.set_setting(key, now.isoformat())

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _last_message(history: list[Message], direction: str) -> Message | None:
        for msg in reversed(history):
            if msg.direction == direction:
                return msg
        return None

    @staticmethod
    def _last_outbound_text(history: list[Message]) -> str:
        msg = BotHealthHeartbeatService._last_message(history, MessageDirection.OUTBOUND.value)
        return (msg.text or "").strip() if msg is not None else ""

    @staticmethod
    def _is_favorite_message(text: str) -> bool:
        normalized = " ".join((text or "").lower().split())
        return "добавил объявление в избранное" in normalized or "добавил в избранное" in normalized

    def _report_tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.settings.bot_health_daily_report_timezone)
        except Exception:  # noqa: BLE001
            return ZoneInfo("UTC")

    @staticmethod
    def _read_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            logger.warning("Invalid datetime setting value: %s", value)
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
