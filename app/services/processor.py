from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import SessionLocal
from app.integrations.avito import AvitoClient
from app.integrations.crm_ingest import CrmIngestClient
from app.integrations.openai_client import OpenAIClient
from app.integrations.telegram import TelegramClient
from app.integrations.youla import YoulaClient
from app.repositories import Repository
from app.services.cleanup import RetentionService
from app.services.heartbeat import BotHealthHeartbeatService
from app.services.lead_detector import LeadDetector
from app.services.prompt_service import PromptService
from app.services.retry import with_retry
from app.services.router import RouterService
from app.services.self_learning import SelfLearningService
from app.services.stats_reporting import StatsReportingService
from app.types import ChatDecision, ChatState, Domain, IncomingEvent, LeadStatus, MessageDirection

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProcessingStats:
    polled_events: int = 0
    processed_events: int = 0
    ignored_events: int = 0
    replied_events: int = 0
    leads_created: int = 0


class MessageProcessor:
    LATE_REPLY_THRESHOLD_SECONDS = 15 * 60
    YOULA_FAVORITE_FOLLOWUP_1_DELAY = timedelta(hours=24)
    YOULA_FAVORITE_FOLLOWUP_2_DELAY = timedelta(hours=24)
    YOULA_SILENT_CHAT_FOLLOWUP_1_DELAY = timedelta(hours=24)
    YOULA_SILENT_CHAT_FOLLOWUP_2_DELAY = timedelta(hours=24)
    CURSOR_KEY = "avito_cursor"
    CURRENCY_RE = re.compile(r"(?:₽|руб(?:\.|ля|лей)?|(?:^|[\s.,;:()\-])р(?:$|[\s.,;:()\-]))", re.IGNORECASE)
    BUDGET_WORD_RE = re.compile(
        r"(бюджет|стоим|цена|платить|сколько\s+планируете|в\s*месяц|за\s*месяц|мес\.?)",
        re.IGNORECASE,
    )
    SHORT_RANGE_RE = re.compile(
        r"(?:от\s*)?(\d{1,2})\s*(?:₽|руб(?:\.|ля|лей)?|р|к|тыс(?:яч)?)?\s*(?:-|–|—|до)\s*(\d{1,2})\s*(?:₽|руб(?:\.|ля|лей)?|р|к|тыс(?:яч)?)?\b",
        re.IGNORECASE,
    )
    COMPACT_NUMBER_RE = re.compile(r"(?<!\d)(\d{3,4})(?!\d)")
    SETTLEMENT_INTENT_RE = re.compile(
        r"(хочу\s+засел|засел|свободн|снять|аренд|койко|комнат|хостел|квартир|места|заехать|на\s+\d{1,2}\.?[./-]\d{1,2})",
        re.IGNORECASE,
    )
    DATE_INTENT_RE = re.compile(
        r"(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?|сегодня|завтра|послезавтра|воскресенье|понедельник|вторник|среда|четверг|пятница|суббота|на\s+неделе)",
        re.IGNORECASE,
    )
    PEOPLE_INTENT_RE = re.compile(
        r"(\b\d+\s*(?:чел|человек|гост|чел\.)\b|двое|трое|четверо|пара|семья)",
        re.IGNORECASE,
    )
    FULL_ROOM_RE = re.compile(
        r"(всю\s+комнат|оба\s+койк|целиком|полностью\s+комнат)",
        re.IGNORECASE,
    )
    SHORT_STAY_RE = re.compile(
        r"(посуточ|сутк|на\s*\d+\s*(?:дн|дня|дней|ноч|недел)|"
        r"на\s*(?:день|неделю|выходные|воскресенье|субботу|пятницу)|"
        r"в\s*(?:воскресенье|субботу|пятницу)|сегодня|завтра)",
        re.IGNORECASE,
    )
    PRICE_INTENT_RE = re.compile(
        r"(цена|стоим|сколько|поч[её]м|за\s*\d+\s*(?:сут|дн|дня|дней|месяц|мес))",
        re.IGNORECASE,
    )
    ABUSE_OR_THREAT_RE = re.compile(
        r"(мошенн|обман|развод|урод|идиот|дебил|твар|сук|бля|пидор|"
        r"угрож|в суд|прокуратур|полици|жалоб|требую|верните\s+деньги)",
        re.IGNORECASE,
    )
    AVAILABILITY_RE = re.compile(
        r"(еще|ещё|ещ[её])\s+сда|свободн|есть\s+мест|актуальн|сдается|сдаётся",
        re.IGNORECASE,
    )
    FOREIGNER_RE = re.compile(r"(иностран|иностранец|иностранцев|мигрант|нерезидент)", re.IGNORECASE)
    LEGAL_ADDRESS_RE = re.compile(
        r"(юридическ\w*\s+адрес|регистрац\w+\s+(?:ано|ооо|ип|организац)|"
        r"зарегистрир\w+\s+организац|адрес\s+для\s+регистрац)",
        re.IGNORECASE,
    )
    NON_HOUSING_USE_RE = re.compile(
        r"(место\s+для\s+работы|под\s+офис|как\s+офис|офис|коворкинг|рабочее\s+место)",
        re.IGNORECASE,
    )
    HOSTEL_MARKERS = (
        "куйбышева 30",
        "куйбышева, 30",
        "мамина сибиряка 132",
        "мамина-сибиряка 132",
        "мамина сибиряка, 132",
        "ботаническая 30",
        "ботаническая, 30",
        "сити е",
        "сити-е",
        "в гостях у бабуси",
        "уютное местечко",
    )

    def __init__(
        self,
        settings: Settings,
        avito_client: AvitoClient,
        openai_client: OpenAIClient,
        telegram_client: TelegramClient,
        router_service: RouterService,
        prompt_service: PromptService,
        lead_detector: LeadDetector,
        retention_service: RetentionService,
        youla_client: YoulaClient | None = None,
        self_learning_service: SelfLearningService | None = None,
        stats_reporting_service: StatsReportingService | None = None,
        heartbeat_service: BotHealthHeartbeatService | None = None,
        crm_ingest_client: CrmIngestClient | None = None,
    ):
        self.settings = settings
        self.avito_client = avito_client
        self.openai_client = openai_client
        self.youla_client = youla_client
        self.telegram_client = telegram_client
        self.router_service = router_service
        self.prompt_service = prompt_service
        self.self_learning_service = self_learning_service
        self.stats_reporting_service = stats_reporting_service
        self.heartbeat_service = heartbeat_service
        self.lead_detector = lead_detector
        self.retention_service = retention_service
        self.crm_ingest_client = crm_ingest_client

    def process_updates_once(self) -> ProcessingStats:
        stats = ProcessingStats()
        poll_error: str | None = None

        with SessionLocal() as db:
            repo = Repository(db)
            cursor = repo.get_setting(self.CURSOR_KEY)

            events: list[IncomingEvent] = []
            next_cursor = cursor
            try:
                events, next_cursor = with_retry(
                    lambda: self.avito_client.fetch_updates(cursor=cursor),
                    name="avito_fetch_updates",
                    attempts=3,
                )
            except Exception as exc:  # noqa: BLE001
                poll_error = str(exc)
                logger.exception("Avito poll failed, continue with housekeeping: %s", exc)
            stats.polled_events = len(events)

            latest_inbound_event_by_chat: dict[str, str] = {}
            for event in events:
                if event.sender_type in {"user", "client", "buyer", "inbound"}:
                    latest_inbound_event_by_chat[event.chat_id] = event.event_id

            for event in events:
                try:
                    allow_reply = latest_inbound_event_by_chat.get(event.chat_id) == event.event_id
                    outcome = self._process_event(db=db, event=event, allow_reply=allow_reply)
                    if outcome == "ignored":
                        stats.ignored_events += 1
                    elif outcome == "replied":
                        stats.replied_events += 1
                    elif outcome == "processed":
                        stats.processed_events += 1
                    elif outcome == "lead":
                        stats.leads_created += 1
                        stats.replied_events += 1
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Event processing failed for chat %s: %s", event.chat_id, exc)
                    db.rollback()

            if next_cursor and next_cursor != cursor:
                repo.set_setting(self.CURSOR_KEY, next_cursor)

            db.commit()

        with SessionLocal() as cleanup_db:
            self.retention_service.run_if_due(cleanup_db, retention_days=self.settings.retention_days)
            if self.self_learning_service is not None:
                try:
                    learn_result = self.self_learning_service.run_if_due(cleanup_db)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Self-learning cycle failed: %s", exc)
                else:
                    if learn_result is not None:
                        logger.info(
                            "Self-learning cycle done: action=%s active=%s stable=%s created=%s source_chats=%s",
                            learn_result.action,
                            learn_result.active_version,
                            learn_result.stable_version,
                            learn_result.examples_created,
                            learn_result.source_chats,
                        )
            if self.stats_reporting_service is not None:
                try:
                    report_result = self.stats_reporting_service.run_if_due(cleanup_db)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Stats reporting cycle failed: %s", exc)
                else:
                    if report_result is not None:
                        logger.info(
                            "Stats report cycle done: sent=%s target=%s anomaly=%s unanswered=%s new_contacts=%s",
                            report_result.sent,
                            report_result.target_chat_id,
                            report_result.anomaly_count,
                            report_result.unanswered_count,
                            report_result.new_contacts_count,
                        )
            if self.youla_client is not None and self.youla_client.enabled:
                try:
                    sent = self._run_youla_housekeeping(cleanup_db)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Youla housekeeping cycle failed: %s", exc)
                else:
                    if sent:
                        logger.info("Youla housekeeping sent messages=%s", sent)
            if self.heartbeat_service is not None:
                try:
                    if poll_error:
                        self.heartbeat_service.notify_poll_failure(cleanup_db, poll_error)
                    self.heartbeat_service.run_if_due(cleanup_db)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Heartbeat cycle failed: %s", exc)
            cleanup_db.commit()

        return stats

    def _process_event(self, db: Session, event: IncomingEvent, allow_reply: bool = True) -> str:
        repo = Repository(db)
        payload = json.dumps(
            {
                "marketplace": event.marketplace,
                "event_id": event.event_id,
                "chat_id": event.chat_id,
                "message_id": event.message_id,
                "text": event.text,
                "ad_id": event.ad_context.ad_id,
                "ad_title": event.ad_context.title,
                "ad_category": event.ad_context.category,
                "sender_id": event.sender_id,
                "recipient_id": event.recipient_id,
                "product_id": event.product_id,
            },
            ensure_ascii=False,
        )

        source = (event.marketplace or "avito").strip().lower()
        idempotency_key = f"{source}:{event.event_id}"
        event_log = repo.start_event(
            source=source,
            event_type="incoming_message",
            idempotency_key=idempotency_key,
            payload=payload,
        )
        if event_log is None:
            db.commit()
            return "processed"

        try:
            if event.sender_type not in {"user", "client", "buyer", "inbound"}:
                repo.mark_event_processed(event_log.id)
                db.commit()
                return "processed"

            if source == "youla":
                self._enrich_youla_ad_context(event)

            ad = repo.upsert_ad(
                external_ad_id=event.ad_context.ad_id,
                title=event.ad_context.title,
                category=self._normalize_ad_category(event.ad_context.category),
                raw_category=event.ad_context.category,
                url=event.ad_context.url,
            )
            chat = repo.upsert_chat(
                external_chat_id=event.chat_id,
                ad_id=ad.id,
                customer_name=event.customer_name,
            )

            persisted_message = repo.save_message(
                chat_id=chat.id,
                direction=MessageDirection.INBOUND,
                text=event.text,
                external_message_id=event.message_id,
                payload_json=payload,
                created_at=event.created_at,
            )
            if persisted_message is None:
                repo.mark_event_processed(event_log.id)
                db.commit()
                return "processed"

            if self._contains_abuse_or_threat(event.text):
                repo.create_routing_decision(
                    chat_id=chat.id,
                    domain=Domain.OTHER.value,
                    confidence=1.0,
                    decision=ChatDecision.IGNORE_SILENT.value,
                    reason="ABUSE_OR_THREAT",
                )
                repo.set_chat_state(chat.id, ChatState.IGNORED_OUT_OF_SCOPE)
                repo.mark_event_processed(event_log.id)
                db.commit()
                return "ignored"

            recent = repo.get_recent_messages(chat.id, limit=self.settings.max_recent_messages_for_classification)
            recent_texts = [m.text for m in recent]

            routing = self.router_service.route(
                ad_category_raw=ad.raw_category or ad.category,
                ad_title=ad.title,
                recent_messages=recent_texts,
            )

            repo.create_routing_decision(
                chat_id=chat.id,
                domain=routing.domain.value,
                confidence=routing.confidence,
                decision=routing.decision.value,
                reason=routing.reason,
            )

            if routing.decision == ChatDecision.IGNORE_SILENT:
                repo.set_chat_state(chat.id, ChatState.IGNORED_OUT_OF_SCOPE)
                repo.mark_event_processed(event_log.id)
                db.commit()
                return "ignored"

            if not allow_reply:
                self._sync_crm_message(
                    source=source,
                    event=event,
                    chat=chat,
                    ad=ad,
                    text=event.text,
                    direction="IN",
                )
                repo.mark_event_processed(event_log.id)
                db.commit()
                return "processed"

            history_messages = repo.get_recent_messages(chat.id, limit=self.settings.max_recent_messages_for_reply)
            rule_based_reply = self._build_rule_based_reply(
                ad_title=ad.title,
                ad_category=ad.raw_category or ad.category,
                history_messages=history_messages,
                marketplace=source,
            )
            prompt = self.prompt_service.get_real_estate_prompt(db)
            learning_version: str | None = None
            learning_examples_count = 0
            if rule_based_reply is not None:
                response_text = self._trim_sentences(rule_based_reply, max_sentences=4)
            else:
                chat_history = [
                    {
                        "role": "user" if m.direction == MessageDirection.INBOUND.value else "assistant",
                        "content": m.text,
                    }
                    for m in history_messages
                ]
                effective_prompt_text = self._build_runtime_prompt(
                    base_prompt=prompt.text,
                    ad_title=ad.title,
                    ad_category=ad.raw_category or ad.category,
                    history_messages=history_messages,
                )
                if self.self_learning_service is not None:
                    try:
                        effective_prompt_text, learning_version, learning_examples_count = self.self_learning_service.build_augmented_prompt(
                            db=db,
                            base_prompt=effective_prompt_text,
                            chat_external_id=chat.external_chat_id,
                            ad_title=ad.title,
                            recent_messages=recent_texts,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Self-learning prompt augmentation failed for chat %s: %s", event.chat_id, exc)

                response_text = with_retry(
                    lambda: self.openai_client.generate_reply(effective_prompt_text, chat_history),
                    name="openai_generate_reply",
                    attempts=3,
                )
                response_text = self._trim_sentences(response_text, max_sentences=4)

            with_retry(
                lambda: self._send_with_delay(event=event, text=response_text),
                name=f"{source}_send_message",
                attempts=3,
            )

            repo.create_bot_reply(
                chat_id=chat.id,
                prompt_version=prompt.version,
                text=response_text,
                status="SENT",
            )
            repo.save_message(
                chat_id=chat.id,
                direction=MessageDirection.OUTBOUND,
                text=response_text,
                external_message_id=None,
                payload_json=None,
            )
            repo.set_chat_state(chat.id, ChatState.QUALIFYING)
            if self.self_learning_service is not None:
                try:
                    self.self_learning_service.record_reply_usage(
                        db=db,
                        chat_id=chat.id,
                        external_chat_id=chat.external_chat_id,
                        learning_version=learning_version,
                        examples_count=learning_examples_count,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Failed to store self-learning usage for chat %s: %s", event.chat_id, exc)

            contact = self._extract_contact(history_messages)
            lead_created = False
            lead = None
            summary = ""
            raw = ""
            normalized = ""
            if contact:
                try:
                    raw, normalized = contact
                    existing = repo.get_lead_by_contact(chat.id, normalized)
                    if existing is None:
                        transcript = [
                            f"{'Кл' if m.direction == MessageDirection.INBOUND.value else 'ИИ'}: {m.text}"
                            for m in history_messages[-8:]
                        ]
                        summary = with_retry(
                            lambda: self.openai_client.summarize_lead(
                                ad_title=ad.title,
                                contact_raw=raw,
                                transcript=transcript,
                            ),
                            name="openai_summarize_lead",
                            attempts=2,
                        )
                        lead = repo.create_lead(
                            chat_id=chat.id,
                            contact_raw=raw,
                            contact_normalized=normalized,
                            summary=summary,
                        )
                        if lead:
                            repo.set_chat_state(chat.id, ChatState.CONTACT_RECEIVED)
                            lead_created = True
                            try:
                                self._send_lead_to_telegram(
                                    lead_id=lead.id,
                                    ad_title=ad.title,
                                    ad_url=ad.url,
                                    marketplace=source,
                                    chat_external_id=chat.external_chat_id,
                                    customer_name=chat.customer_name,
                                    contact_raw=raw,
                                    summary=summary,
                                    context_lines=transcript,
                                )
                            except Exception as exc:  # noqa: BLE001
                                logger.exception(
                                    "Lead %s created, but Telegram send failed for chat %s: %s",
                                    lead.id,
                                    event.chat_id,
                                    exc,
                                )
                            else:
                                repo.mark_lead_sent(lead.id)
                                repo.set_chat_state(chat.id, ChatState.TRANSFERRED_TO_MANAGER)
                except Exception as exc:  # noqa: BLE001
                    # Lead pipeline must not fail whole event after reply was sent to Avito.
                    logger.exception("Lead pipeline failed for chat %s: %s", event.chat_id, exc)

            crm_result = self._sync_crm_message(
                source=source,
                event=event,
                chat=chat,
                ad=ad,
                text=event.text,
                direction="IN",
            )
            self._notify_new_crm_dialog(source=source, event=event, ad=ad, crm_result=crm_result)
            self._sync_crm_message(
                source=source,
                event=event,
                chat=chat,
                ad=ad,
                text=response_text,
                direction="OUT",
            )
            if lead_created and lead is not None:
                self._sync_crm_lead_contact(
                    source=source,
                    event=event,
                    chat=chat,
                    ad=ad,
                    contact_raw=raw,
                    contact_normalized=normalized,
                    summary=summary,
                )

            repo.mark_event_processed(event_log.id)
            db.commit()
            return "lead" if lead_created else "replied"

        except Exception as exc:  # noqa: BLE001
            repo.mark_event_failed(event_log.id, str(exc))
            db.commit()
            raise

    def _send_lead_to_telegram(
        self,
        lead_id: int,
        ad_title: str,
        ad_url: str | None,
        marketplace: str,
        chat_external_id: str,
        customer_name: str | None,
        contact_raw: str,
        summary: str,
        context_lines: list[str],
    ) -> None:
        if not self.settings.telegram_leads_chat_id:
            logger.warning("Lead created but TELEGRAM_LEADS_CHAT_ID is empty")
            return

        with_retry(
            lambda: self.telegram_client.send_lead_card(
                leads_chat_id=self.settings.telegram_leads_chat_id,
                lead_id=lead_id,
                ad_title=ad_title,
                ad_url=ad_url,
                marketplace=marketplace,
                chat_url=self._build_marketplace_chat_url(marketplace=marketplace, external_chat_id=chat_external_id),
                external_chat_id=chat_external_id,
                customer_name=customer_name,
                contact_raw=contact_raw,
                summary=summary,
                context_lines=context_lines,
                status=LeadStatus.NEW,
            ),
            name="telegram_send_lead",
            attempts=3,
        )

    def _sync_crm_message(
        self,
        *,
        source: str,
        event: IncomingEvent,
        chat,
        ad,
        text: str,
        direction: str,
    ):
        if self.crm_ingest_client is None or not self.crm_ingest_client.enabled:
            return None
        try:
            return self.crm_ingest_client.ingest(
                source=source,
                external_chat_id=chat.external_chat_id,
                external_lead_id=ad.external_ad_id,
                event_id=f"{event.event_id}:{direction.lower()}",
                client_name=chat.customer_name,
                object_name=ad.title,
                object_url=ad.url,
                message_body=text,
                message_direction=direction,
                message_channel=source,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "CRM ingest message failed source=%s chat=%s event=%s direction=%s: %s",
                source,
                chat.external_chat_id,
                event.event_id,
                direction,
                exc,
            )
            return None

    def _sync_crm_lead_contact(
        self,
        *,
        source: str,
        event: IncomingEvent,
        chat,
        ad,
        contact_raw: str,
        contact_normalized: str,
        summary: str,
    ) -> None:
        if self.crm_ingest_client is None or not self.crm_ingest_client.enabled:
            return
        status = "CALL"
        try:
            self.crm_ingest_client.ingest(
                source=source,
                external_chat_id=chat.external_chat_id,
                external_lead_id=ad.external_ad_id,
                event_id=f"{event.event_id}:lead",
                client_name=chat.customer_name,
                phone=contact_normalized or contact_raw,
                object_name=ad.title,
                object_url=ad.url,
                status=status,
                note=summary,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "CRM ingest lead sync failed source=%s chat=%s event=%s: %s",
                source,
                chat.external_chat_id,
                event.event_id,
                exc,
            )

    def _notify_new_crm_dialog(self, *, source: str, event: IncomingEvent, ad, crm_result) -> None:
        if not self.settings.crm_ingest_notify_telegram_enabled:
            return
        if crm_result is None or not bool(getattr(crm_result, "created", False)):
            return

        target_chat_id = self.settings.crm_ingest_notify_chat_id or self.settings.telegram_leads_chat_id
        if not target_chat_id:
            return

        ad_title = ad.title if ad and getattr(ad, "title", None) else event.ad_context.title
        ad_url = (ad.url if ad and getattr(ad, "url", None) else None) or event.ad_context.url
        source_label = self._marketplace_label(source)
        chat_url = self._build_marketplace_chat_url(marketplace=source, external_chat_id=event.chat_id)
        lead_id = getattr(crm_result, "lead_id", None)
        lines = [
            f"Новый CRM-диалог #{lead_id}:" if lead_id else "Новый CRM-диалог:",
            "",
            ad_title,
        ]
        lines.append(f"чат ID: {event.chat_id}")
        if chat_url:
            lines.extend(["Чат", chat_url])
        if ad_url:
            lines.extend(["Объявление", ad_url])
        lines.append(f"Источник: {source_label}")
        message = "\n".join(lines)
        try:
            self.telegram_client.send_message(chat_id=target_chat_id, text=message)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CRM new-dialog telegram notify failed chat=%s source=%s: %s", target_chat_id, source, exc)

    def _extract_contact(self, messages: list) -> tuple[str, str] | None:
        for message in reversed(messages):
            if message.direction != MessageDirection.INBOUND.value:
                continue
            found = self.lead_detector.extract_contact(message.text)
            if found:
                return found
        return None

    @staticmethod
    def _trim_sentences(text: str, max_sentences: int = 4) -> str:
        parts = re.split(r"(?<=[.!?])\s+", text.strip())
        if len(parts) <= max_sentences:
            return text.strip()
        return " ".join(parts[:max_sentences]).strip()

    @staticmethod
    def _normalize_ad_category(raw_category: str | None) -> str | None:
        if not raw_category:
            return None
        value = raw_category.strip().lower()
        if value in {"real_estate", "realestate", "недвижимость"}:
            return "REAL_ESTATE"
        if value in {"auto", "авто", "автомобили"}:
            return "AUTO"
        if value in {"other", "прочее"}:
            return "OTHER"
        if any(token in value for token in ["недвиж", "кварт", "хостел", "комнат", "жиль", "аренд"]):
            return "REAL_ESTATE"
        if any(token in value for token in ["авто", "машин", "транспорт", "автомоб"]):
            return "AUTO"
        return "OTHER"

    def process_incoming_event(self, db: Session, event: IncomingEvent, allow_reply: bool = True) -> str:
        return self._process_event(db=db, event=event, allow_reply=allow_reply)

    def _send_with_delay(self, event: IncomingEvent, text: str) -> None:
        delay = max(0, int(self.settings.reply_delay_seconds))
        if delay > 0:
            time.sleep(delay)
        marketplace = (event.marketplace or "avito").strip().lower()
        if marketplace == "youla":
            if self.youla_client is None or not self.youla_client.enabled:
                raise RuntimeError("Youla client is not configured")
            sender_id = event.recipient_id or self.settings.youla_account_id
            recipient_id = event.sender_id
            product_id = event.product_id or event.ad_context.ad_id
            if not sender_id or not recipient_id or not product_id:
                raise RuntimeError("Youla send requires sender_id, recipient_id and product_id")
            self.youla_client.send_message(
                sender_id=sender_id,
                recipient_id=recipient_id,
                product_id=product_id,
                text=text,
            )
            return
        self.avito_client.send_message(chat_id=event.chat_id, text=text)

    def _build_rule_based_reply(
        self,
        ad_title: str,
        ad_category: str | None,
        history_messages: list,
        marketplace: str = "avito",
    ) -> str | None:
        latest_inbound = self._latest_inbound_text(history_messages)
        if not latest_inbound:
            return None

        if self._is_favorite_message(latest_inbound):
            return self._favorite_opener_text()

        if self._is_missed_call_notification(latest_inbound):
            return self._missed_call_reply_text()

        if self._is_service_offer_message(latest_inbound):
            return self._service_offer_reply_text()

        offtopic_reply = self._offtopic_real_estate_reply_text(latest_inbound)
        if offtopic_reply is not None:
            return offtopic_reply

        if self._should_send_delay_apology(history_messages):
            return (
                "Извините за долгое ожидание ответа. "
                "Подскажите, пожалуйста, вопрос по заселению еще актуален?"
            )

        has_contact = self._extract_contact(history_messages) is not None
        is_hostel_listing = self._is_hostel_listing(ad_title=ad_title, ad_category=ad_category)
        short_stay_request = self._is_short_stay_request(latest_inbound)

        if self._is_call_back_request(latest_inbound):
            if has_contact:
                return (
                    "Контакт вижу, спасибо. У нас многоканальная линия, поэтому напрямую до менеджера дозвониться нельзя. "
                    "Менеджер Сергей сам позвонит вам в ближайшее время."
                )
            return (
                "У нас многоканальная линия, поэтому напрямую до менеджера дозвониться нельзя. "
                "Оставьте, пожалуйста, номер телефона или мессенджер, и менеджер Сергей сам свяжется с вами."
            )

        if short_stay_request and not is_hostel_listing and not has_contact:
            return (
                "По этому объявлению размещение доступно только на месяц. "
                "Подскажите, пожалуйста, рассматриваете помесячное заселение?"
            )

        if short_stay_request and is_hostel_listing and self._is_price_request(latest_inbound) and not has_contact:
            return (
                "По этому хостелу возможно посуточное размещение. "
                "Ориентир по койко-месту — от 700 ₽ за сутки, точная стоимость зависит от срока и загрузки. "
                "Подскажите, пожалуйста, на какие даты планируете заезд?"
            )

        budget_range = self._detect_short_budget_range_thousands(latest_inbound, history_messages)
        if budget_range is not None and not has_contact:
            left, right = budget_range
            if left == right:
                return f"Простите, вы имеете в виду {left} тысяч рублей за проживание в месяц?"
            return f"Простите, вы имеете в виду {left}–{right} тысяч рублей за проживание в месяц?"

        explicit_intent = self._has_explicit_settlement_intent(latest_inbound)
        availability_question = self._is_availability_question(latest_inbound)
        if explicit_intent and not has_contact and self._is_deflective_last_outbound(history_messages):
            followup = self._build_qualification_followup(latest_inbound)
            return f"Понял вас. {followup}"

        if self._is_first_bot_reply(history_messages) and not has_contact:
            if self._is_foreigner_question(latest_inbound):
                return self._foreigner_question_reply_text()
            if availability_question:
                availability_prefix = self._availability_acknowledgement(ad_title=ad_title, ad_category=ad_category)
                followup = self._build_qualification_followup(latest_inbound)
                return f"{availability_prefix} {followup}"
            if explicit_intent:
                followup = self._build_qualification_followup(latest_inbound)
                return f"Здравствуйте! {followup}"
            focus = self._detect_ad_focus(ad_title=ad_title, ad_category=ad_category)
            if focus:
                return f"Здравствуйте! Помогу по объявлению о {focus}. Подскажите, пожалуйста, вопрос заселения сейчас актуален?"
            return "Здравствуйте! Подскажите, пожалуйста, вопрос заселения сейчас актуален?"

        return None

    def _build_runtime_prompt(
        self,
        base_prompt: str,
        ad_title: str,
        ad_category: str | None,
        history_messages: list,
    ) -> str:
        focus = self._detect_ad_focus(ad_title=ad_title, ad_category=ad_category) or "проживании"
        is_first_reply = self._is_first_bot_reply(history_messages)
        latest_inbound = self._latest_inbound_text(history_messages)
        explicit_intent = self._has_explicit_settlement_intent(latest_inbound)

        stage_rule = (
            "Это первый ответ и клиент уже сформулировал запрос на заселение: не спрашивай заново про актуальность, сразу продолжай квалификацию."
            if is_first_reply and explicit_intent
            else (
                "Это первый ответ в чате: сначала поприветствуй и задай только один вопрос про актуальность заселения."
                if is_first_reply
                else "Это продолжение диалога: задавай только один следующий уточняющий вопрос и не делай длинных сообщений."
            )
        )

        runtime_rules = (
            "\n\n# Runtime Rules (обязательно)\n"
            f"- Заголовок объявления: {ad_title}\n"
            f"- Категория объявления: {ad_category or 'UNKNOWN'}\n"
            f"- Основной фокус текущего чата: {focus}\n"
            "- Тон общения: дружелюбный, без давления.\n"
            "- Отвечай только в контексте этого объявления.\n"
            "- Если объявление про койко-место, не переключайся на другие форматы без прямого запроса клиента.\n"
            "- Три хостела, где возможно посуточное размещение: Куйбышева 30 (Сити Е), Мамина-Сибиряка 132 (В гостях у бабуси), Ботаническая 30 (Уютное местечко).\n"
            "- В хостелах при вопросе о посуточной цене давай ориентир от 700 ₽/сутки за койко-место и уточняй, что точную стоимость подтверждает менеджер.\n"
            "- По остальным адресам предлагай помесячное размещение (не обещай посуточно).\n"
            "- Цены в чате давай как ориентир: от/примерно, без жестких гарантий.\n"
            "- Если тип размещения неочевиден, задай один короткий уточняющий вопрос.\n"
            "- Не задавай более одного вопроса в одном сообщении.\n"
            "- Если клиент уже явно просит заселение/наличие/даты, не переспрашивай «актуально ли».\n"
            "- Если клиент спрашивает про наличие, сначала прямо ответь, что объявление актуально, и только потом задай следующий уточняющий вопрос.\n"
            "- Если клиент спрашивает цену, срок, иностранцев или формат размещения, отвечай по сути вопроса, а не шаблонной фразой.\n"
            "- Никогда не пиши «в ближайшее время мы с вами свяжемся» или похожие фразы вместо содержательного ответа.\n"
            "- Если сообщение не про заселение (юридический адрес, услуги, офис/место для работы), не уводи диалог в квалификацию по заселению.\n"
            "- Не завершай диалог фразой «уточню у менеджера» без попытки собрать недостающие данные и контакт.\n"
            "- В каждом релевантном диалоге цель: получить контакт клиента (телефон или мессенджер).\n"
            f"- {stage_rule}\n"
        )
        return f"{base_prompt}{runtime_rules}"

    def _latest_inbound_text(self, history_messages: list) -> str:
        for message in reversed(history_messages):
            if message.direction == MessageDirection.INBOUND.value:
                return message.text.strip()
        return ""

    @staticmethod
    def _normalize_to_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _should_send_delay_apology(self, history_messages: list) -> bool:
        # Find inbound messages that appeared after the last bot outbound.
        last_outbound_index = -1
        for idx, message in enumerate(history_messages):
            if message.direction == MessageDirection.OUTBOUND.value:
                last_outbound_index = idx

        pending_inbounds = [
            message
            for message in history_messages[last_outbound_index + 1 :]
            if message.direction == MessageDirection.INBOUND.value
        ]
        if not pending_inbounds:
            return False

        first_pending_at = self._normalize_to_utc(getattr(pending_inbounds[0], "created_at", None))
        if first_pending_at is None:
            return False

        delay_seconds = (datetime.now(timezone.utc) - first_pending_at).total_seconds()
        return delay_seconds >= self.LATE_REPLY_THRESHOLD_SECONDS

    def _last_outbound_text(self, history_messages: list) -> str:
        for message in reversed(history_messages):
            if message.direction == MessageDirection.OUTBOUND.value:
                return message.text.strip()
        return ""

    def _is_first_bot_reply(self, history_messages: list) -> bool:
        outbound_count = sum(1 for message in history_messages if message.direction == MessageDirection.OUTBOUND.value)
        inbound_count = sum(1 for message in history_messages if message.direction == MessageDirection.INBOUND.value)
        return inbound_count >= 1 and outbound_count == 0

    @staticmethod
    def _is_call_back_request(text: str) -> bool:
        normalized = " ".join(text.lower().split())
        return bool(
            "куда вам набрать" in normalized
            or "куда набрать" in normalized
            or "куда вам позвонить" in normalized
            or "куда позвонить" in normalized
            or "вам набрать" in normalized
            or "вам позвонить" in normalized
        )

    def _detect_short_budget_range_thousands(
        self,
        latest_inbound_text: str,
        history_messages: list,
    ) -> tuple[int, int] | None:
        text = " ".join(latest_inbound_text.lower().split())
        if not text:
            return None

        has_currency = bool(self.CURRENCY_RE.search(text))
        has_budget_words = bool(self.BUDGET_WORD_RE.search(text))
        asked_budget_recently = self._asked_budget_recently(history_messages)

        budget_context = has_currency or has_budget_words or asked_budget_recently
        if not budget_context:
            return None

        range_match = self.SHORT_RANGE_RE.search(text)
        if range_match:
            left = int(range_match.group(1))
            right = int(range_match.group(2))
            if 1 <= left <= 24 and 1 <= right <= 24:
                return tuple(sorted((left, right)))

        if asked_budget_recently and self._is_numeric_budget_reply(text):
            compact_match = self.COMPACT_NUMBER_RE.search(text)
            if compact_match:
                compact = self._split_compact_short_budget(compact_match.group(1))
                if compact:
                    return tuple(sorted(compact))

            single_match = re.fullmatch(r"\d{1,2}", text)
            if single_match:
                value = int(single_match.group(0))
                if 1 <= value <= 24:
                    return value, value

        if has_currency or has_budget_words:
            compact_match = self.COMPACT_NUMBER_RE.search(text)
            if compact_match:
                compact = self._split_compact_short_budget(compact_match.group(1))
                if compact:
                    return tuple(sorted(compact))

        return None

    def _asked_budget_recently(self, history_messages: list) -> bool:
        last_outbound = self._last_outbound_text(history_messages).lower()
        if not last_outbound:
            return False
        return bool(
            "бюджет" in last_outbound
            or "сколько планируете платить" in last_outbound
            or "какой у вас бюджет" in last_outbound
            or "стоимость" in last_outbound
            or "цена" in last_outbound
        )

    @staticmethod
    def _is_numeric_budget_reply(text: str) -> bool:
        compact = text.replace(" ", "")
        if re.fullmatch(r"\d{1,4}", compact):
            return True
        if re.fullmatch(r"(?:от)?\d{1,2}(?:-|–|—|до)\d{1,2}", compact):
            return True
        if re.fullmatch(r"(?:от)?\d{1,2}(?:р|руб|₽)?(?:-|–|—|до)\d{1,2}(?:р|руб|₽)?", compact):
            return True
        return False

    @staticmethod
    def _split_compact_short_budget(token: str) -> tuple[int, int] | None:
        if not token.isdigit() or len(token) not in {3, 4}:
            return None
        candidates: list[tuple[int, int]] = []
        for split_index in range(1, len(token)):
            left_chunk = token[:split_index]
            right_chunk = token[split_index:]
            if len(left_chunk) > 2 or len(right_chunk) > 2:
                continue
            left = int(left_chunk)
            right = int(right_chunk)
            if 1 <= left <= 24 and 1 <= right <= 24:
                candidates.append((left, right))
        if not candidates:
            return None
        candidates.sort(key=lambda pair: (abs(pair[0] - pair[1]), max(pair[0], pair[1])))
        return candidates[0]

    @staticmethod
    def _detect_ad_focus(ad_title: str, ad_category: str | None) -> str | None:
        combined = f"{ad_title} {ad_category or ''}".lower()
        if "койко" in combined:
            return "койко-месте"
        if "комнат" in combined:
            return "комнате"
        if "квартир" in combined:
            return "квартире"
        if "хостел" in combined or "гостиниц" in combined or "гостиница" in combined:
            return "проживании в хостеле"
        if "семейн" in combined:
            return "семейном номере"
        return None

    @staticmethod
    def _availability_acknowledgement(ad_title: str, ad_category: str | None) -> str:
        focus = MessageProcessor._detect_ad_focus(ad_title=ad_title, ad_category=ad_category)
        if focus == "комнате":
            return "Да, эта комната сейчас сдается."
        if focus == "койко-месте":
            return "Да, это койко-место сейчас доступно."
        if focus == "квартире":
            return "Да, эта квартира сейчас сдается."
        if focus == "проживании в хостеле":
            return "Да, размещение в хостеле сейчас актуально."
        return "Да, объявление сейчас актуально."

    @staticmethod
    def _foreigner_question_reply_text() -> str:
        return "Подскажите, пожалуйста, для какого гражданства и на какой срок ищете проживание?"

    def _has_explicit_settlement_intent(self, text: str) -> bool:
        return bool(text and self.SETTLEMENT_INTENT_RE.search(text))

    def _is_availability_question(self, text: str) -> bool:
        return bool(text and self.AVAILABILITY_RE.search(text))

    def _is_foreigner_question(self, text: str) -> bool:
        return bool(text and self.FOREIGNER_RE.search(text))

    def _offtopic_real_estate_reply_text(self, text: str) -> str | None:
        normalized = " ".join((text or "").lower().split())
        if not normalized:
            return None
        if self.LEGAL_ADDRESS_RE.search(normalized):
            return self._legal_address_decline_text()
        if self.NON_HOUSING_USE_RE.search(normalized):
            return self._non_housing_use_decline_text()
        return None

    @staticmethod
    def _legal_address_decline_text() -> str:
        return (
            "К сожалению, мы не предоставляем юридические адреса и не регистрируем организации по нашим адресам. "
            "Если вам нужно проживание, я помогу подобрать вариант размещения."
        )

    @staticmethod
    def _non_housing_use_decline_text() -> str:
        return (
            "К сожалению, мы не сдаем комнаты как офис или место для работы. "
            "Если вам нужно именно проживание, я помогу подобрать подходящий вариант."
        )

    def _has_date_signal(self, text: str) -> bool:
        return bool(text and self.DATE_INTENT_RE.search(text))

    def _has_people_signal(self, text: str) -> bool:
        return bool(text and self.PEOPLE_INTENT_RE.search(text))

    def _has_budget_signal(self, text: str) -> bool:
        return bool(
            text
            and (
                self.CURRENCY_RE.search(text)
                or re.search(r"\b\d{3,6}\b", text)
                or re.search(r"\d{1,6}\s*(?:₽|р|руб(?:\.|ля|лей)?)", text, re.IGNORECASE)
                or re.search(r"\d{1,3}\s*к\b", text, re.IGNORECASE)
            )
        )

    def _is_short_stay_request(self, text: str) -> bool:
        return bool(text and self.SHORT_STAY_RE.search(text))

    def _is_price_request(self, text: str) -> bool:
        if not text:
            return False
        normalized = " ".join(text.lower().split())
        return bool(
            self.PRICE_INTENT_RE.search(normalized)
            or ("за " in normalized and self._has_budget_signal(normalized))
        )

    def _contains_abuse_or_threat(self, text: str) -> bool:
        return bool(text and self.ABUSE_OR_THREAT_RE.search(text))

    def _is_hostel_listing(self, ad_title: str, ad_category: str | None) -> bool:
        combined = f"{ad_title} {ad_category or ''}".lower()
        if any(marker in combined for marker in self.HOSTEL_MARKERS):
            return True
        if ("за сутки" in combined or "посуточ" in combined or "сутки" in combined) and "койко" in combined:
            return True
        return any(token in combined for token in ("хостел", "гостиниц", "гостиница", "семейн"))

    def _build_qualification_followup(self, text: str) -> str:
        normalized = " ".join((text or "").lower().split())
        wants_full_room = bool(self.FULL_ROOM_RE.search(normalized))
        has_date = self._has_date_signal(normalized)
        has_people = self._has_people_signal(normalized)
        has_budget = self._has_budget_signal(normalized)

        if not has_date:
            return "Подскажите, пожалуйста, на какие даты планируете заселение?"
        if not has_people:
            return "Подскажите, пожалуйста, сколько человек планируете разместить?"
        if not has_budget:
            return "Подскажите, пожалуйста, какой ориентир по бюджету у вас на сутки или на месяц?"
        if wants_full_room:
            return (
                "Принял, рассматриваете размещение всей комнаты. "
                "Чтобы сразу зафиксировать запрос у менеджера, оставьте, пожалуйста, номер телефона или мессенджер."
            )
        return "Чтобы быстрее подобрать вариант и подтвердить наличие, оставьте, пожалуйста, номер телефона или мессенджер."

    def _is_deflective_last_outbound(self, history_messages: list) -> bool:
        last_outbound = self._last_outbound_text(history_messages).lower()
        if not last_outbound:
            return False
        return bool(
            "уточню у менеджера" in last_outbound
            or "ожидайте" in last_outbound
            or "скоро свяжусь" in last_outbound
            or "скоро с вами свяжусь" in last_outbound
        )

    @staticmethod
    def _is_missed_call_notification(text: str) -> bool:
        normalized = " ".join((text or "").lower().split())
        return bool(
            "пропущенный звонок" in normalized
            or "пропущен звонок" in normalized
            or "входящий звонок" in normalized
            or "не удалось дозвониться" in normalized
            or "missed call" in normalized
        )

    @staticmethod
    def _missed_call_reply_text() -> str:
        return (
            "Добрый день, простите, но пока мы не можем тут принимать входящие звонки, "
            "но если вы оставите свой номер телефона, то мы обязательно вам перезвоним."
        )

    @staticmethod
    def _is_favorite_message(text: str) -> bool:
        normalized = " ".join((text or "").lower().split())
        return "добавил объявление в избранное" in normalized or "добавил в избранное" in normalized

    @staticmethod
    def _favorite_opener_text() -> str:
        return (
            "Здравствуйте! Спасибо за интерес к объявлению. "
            "Подскажите, пожалуйста, вопрос по заселению сейчас актуален?"
        )

    @staticmethod
    def _marketplace_label(marketplace: str | None) -> str:
        value = (marketplace or "").strip().lower()
        if value == "youla":
            return "Youla"
        if value == "avito":
            return "Avito"
        return (marketplace or "Источник").strip() or "Источник"

    def _build_marketplace_chat_url(self, *, marketplace: str | None, external_chat_id: str | None) -> str | None:
        value = (marketplace or "").strip().lower()
        chat = (external_chat_id or "").strip()
        if not chat:
            return None
        if value == "youla":
            raw_chat_id = chat.split(":", 1)[1] if ":" in chat else chat
            template = getattr(self.settings, "youla_chat_url_template", None) or "https://youla.ru/web-chat/{chat_id}"
            try:
                return template.format(chat_id=raw_chat_id)
            except Exception:  # noqa: BLE001
                logger.warning("Invalid YOULA_CHAT_URL_TEMPLATE=%s", template)
                return None
        return None

    def _enrich_youla_ad_context(self, event: IncomingEvent) -> None:
        if self.youla_client is None or not self.youla_client.enabled:
            return
        if not event.product_id:
            return

        title = (event.ad_context.title or "").strip()
        needs_title = not title or title.lower().startswith("youla product ")
        needs_url = not bool((event.ad_context.url or "").strip())
        if not needs_title and not needs_url:
            return

        try:
            product = self.youla_client.get_product(event.product_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Youla product enrichment failed product_id=%s: %s", event.product_id, exc)
            return

        if needs_title:
            product_name = (product.get("name") or "").strip()
            if product_name:
                event.ad_context.title = product_name
        if needs_url:
            raw_url = (product.get("url") or "").strip()
            short_url = (product.get("short_url") or "").strip()
            product_url = raw_url or short_url
            if raw_url.startswith("/"):
                product_url = f"https://youla.ru{raw_url}"
            if product_url:
                event.ad_context.url = product_url

    @staticmethod
    def _is_youla_favorite_message(text: str) -> bool:
        return MessageProcessor._is_favorite_message(text)

    @staticmethod
    def _youla_favorite_opener_text() -> str:
        return MessageProcessor._favorite_opener_text()

    @staticmethod
    def _youla_favorite_followup_day1_text() -> str:
        return "Здравствуйте! Подскажите, пожалуйста, вам еще актуально заселение?"

    @staticmethod
    def _youla_favorite_followup_day2_text() -> str:
        return (
            "Добрый день! К сожалению, не получили от вас ответа по заселению. "
            "Если вопрос еще актуален, напишите, пожалуйста, и я помогу с подбором."
        )

    @staticmethod
    def _is_service_offer_message(text: str) -> bool:
        normalized = " ".join((text or "").lower().split())
        if not normalized:
            return False
        keywords = (
            "предлагаю услуги",
            "услуги мастера",
            "мастер на час",
            "сантехник",
            "электрик",
            "ремонт стиральных машин",
            "ремонт холодильников",
            "клининг",
            "уборка",
        )
        return any(keyword in normalized for keyword in keywords)

    @staticmethod
    def _service_offer_reply_text() -> str:
        return (
            "Спасибо за ваше предложение. "
            "Я передам эту информацию менеджеру, и если нашей компании это будет интересно, он с вами свяжется. "
            "Можете оставить номер телефона или ссылку для связи."
        )

    def _run_youla_housekeeping(self, db: Session) -> int:
        if self.youla_client is None or not self.youla_client.enabled:
            return 0
        repo = Repository(db)
        sent_count = 0
        offset = 0
        batch_size = 200
        max_sends = max(1, int(self.settings.youla_housekeeping_max_sends_per_run))

        while True:
            if sent_count >= max_sends:
                logger.info("Youla housekeeping reached per-run limit=%s", max_sends)
                break
            batch = repo.list_youla_housekeeping_chats(limit=batch_size, offset=offset)
            if not batch:
                break

            for chat, ad in batch:
                if repo.list_leads_by_chat(chat.id):
                    continue

                detail = repo.get_chat_detail(chat.id)
                if detail is None:
                    continue
                _, _, history_messages = detail
                if not history_messages:
                    continue

                sent = self._maybe_send_youla_pending_reply(repo=repo, chat=chat, ad=ad, history_messages=history_messages)
                if not sent:
                    sent = self._maybe_send_youla_favorite_nudges(repo=repo, chat=chat, ad=ad, history_messages=history_messages)
                if not sent:
                    sent = self._maybe_send_youla_silent_chat_nudges(repo=repo, chat=chat, ad=ad, history_messages=history_messages)

                if sent:
                    db.commit()
                    sent_count += 1
                    if sent_count >= max_sends:
                        logger.info("Youla housekeeping reached per-run limit=%s", max_sends)
                        break

            offset += len(batch)

        return sent_count

    def _maybe_send_youla_pending_reply(self, *, repo: Repository, chat, ad, history_messages: list) -> bool:
        last_outbound_index = -1
        for idx, message in enumerate(history_messages):
            if message.direction == MessageDirection.OUTBOUND.value:
                last_outbound_index = idx

        pending_inbounds = [
            message
            for message in history_messages[last_outbound_index + 1 :]
            if message.direction == MessageDirection.INBOUND.value
        ]
        if not pending_inbounds:
            return False

        first_pending = pending_inbounds[0]
        first_pending_at = self._normalize_to_utc(getattr(first_pending, "created_at", None))
        if first_pending_at is None:
            return False
        if (datetime.now(timezone.utc) - first_pending_at).total_seconds() < self.LATE_REPLY_THRESHOLD_SECONDS:
            return False

        if self._is_youla_favorite_message(first_pending.text):
            text = self._youla_favorite_opener_text()
            reason = "YOULA_FAVORITE_OPENER"
        else:
            text = (
                "Извините за долгое ожидание ответа. "
                "Подскажите, пожалуйста, вопрос по заселению еще актуален?"
            )
            reason = "YOULA_DELAY_APOLOGY"
        return self._send_youla_proactive_message(
            repo=repo,
            chat=chat,
            ad=ad,
            history_messages=history_messages,
            text=text,
            reason=reason,
        )

    def _maybe_send_youla_favorite_nudges(self, *, repo: Repository, chat, ad, history_messages: list) -> bool:
        favorite_index = -1
        for idx, message in enumerate(history_messages):
            if message.direction != MessageDirection.INBOUND.value:
                continue
            if self._is_youla_favorite_message(message.text):
                favorite_index = idx
        if favorite_index < 0:
            return False

        after_favorite = history_messages[favorite_index + 1 :]
        if not after_favorite:
            return False

        # If a real client message already arrived after the favorite event, regular processing should continue without nudges.
        if any(
            msg.direction == MessageDirection.INBOUND.value and not self._is_youla_favorite_message(msg.text)
            for msg in after_favorite
        ):
            return False

        outbounds_after_favorite = [msg for msg in after_favorite if msg.direction == MessageDirection.OUTBOUND.value]
        if not outbounds_after_favorite:
            return False

        day1_text = self._youla_favorite_followup_day1_text()
        day2_text = self._youla_favorite_followup_day2_text()
        sent_day1 = next((m for m in outbounds_after_favorite if (m.text or "").strip() == day1_text), None)
        sent_day2 = next((m for m in outbounds_after_favorite if (m.text or "").strip() == day2_text), None)

        now_utc = datetime.now(timezone.utc)
        baseline = self._normalize_to_utc(getattr(outbounds_after_favorite[0], "created_at", None))
        if baseline is None:
            return False

        if sent_day1 is None and sent_day2 is None:
            if now_utc - baseline >= self.YOULA_FAVORITE_FOLLOWUP_1_DELAY:
                return self._send_youla_proactive_message(
                    repo=repo,
                    chat=chat,
                    ad=ad,
                    history_messages=history_messages,
                    text=day1_text,
                    reason="YOULA_FAVORITE_FOLLOWUP_DAY1",
                )
            return False

        if sent_day2 is not None:
            return False

        sent_day1_at = self._normalize_to_utc(getattr(sent_day1, "created_at", None))
        if sent_day1_at is None:
            return False
        if now_utc - sent_day1_at < self.YOULA_FAVORITE_FOLLOWUP_2_DELAY:
            return False
        return self._send_youla_proactive_message(
            repo=repo,
            chat=chat,
            ad=ad,
            history_messages=history_messages,
            text=day2_text,
            reason="YOULA_FAVORITE_FOLLOWUP_DAY2",
        )

    @staticmethod
    def _youla_silent_chat_followup_day1_text() -> str:
        return "Здравствуйте! Подскажите, пожалуйста, вам еще актуально заселение?"

    @staticmethod
    def _youla_silent_chat_followup_day2_text() -> str:
        return (
            "Добрый день! К сожалению, не получили от вас ответа по заселению. "
            "Надеемся, у вас все в порядке, и в следующий раз нам удастся с вами помочь."
        )

    def _maybe_send_youla_silent_chat_nudges(self, *, repo: Repository, chat, ad, history_messages: list) -> bool:
        if not history_messages:
            return False

        last_inbound_index = -1
        last_outbound_index = -1
        for idx, message in enumerate(history_messages):
            if message.direction == MessageDirection.INBOUND.value:
                last_inbound_index = idx
            elif message.direction == MessageDirection.OUTBOUND.value:
                last_outbound_index = idx

        if last_outbound_index < 0:
            return False
        if last_outbound_index < last_inbound_index:
            return False

        last_inbound = history_messages[last_inbound_index] if last_inbound_index >= 0 else None
        if last_inbound is not None and self._is_youla_favorite_message(last_inbound.text):
            return False

        after_last_inbound = history_messages[last_inbound_index + 1 :] if last_inbound_index >= 0 else history_messages
        outbounds_after_last_inbound = [msg for msg in after_last_inbound if msg.direction == MessageDirection.OUTBOUND.value]
        if not outbounds_after_last_inbound:
            return False

        day1_text = self._youla_silent_chat_followup_day1_text()
        day2_text = self._youla_silent_chat_followup_day2_text()
        sent_day1 = next((m for m in outbounds_after_last_inbound if (m.text or "").strip() == day1_text), None)
        sent_day2 = next((m for m in outbounds_after_last_inbound if (m.text or "").strip() == day2_text), None)
        if sent_day2 is not None:
            return False

        baseline = self._normalize_to_utc(getattr(outbounds_after_last_inbound[0], "created_at", None))
        if baseline is None:
            return False

        now_utc = datetime.now(timezone.utc)
        if sent_day1 is None:
            if now_utc - baseline < self.YOULA_SILENT_CHAT_FOLLOWUP_1_DELAY:
                return False
            return self._send_youla_proactive_message(
                repo=repo,
                chat=chat,
                ad=ad,
                history_messages=history_messages,
                text=day1_text,
                reason="YOULA_SILENT_CHAT_FOLLOWUP_DAY1",
            )

        sent_day1_at = self._normalize_to_utc(getattr(sent_day1, "created_at", None))
        if sent_day1_at is None:
            return False
        if now_utc - sent_day1_at < self.YOULA_SILENT_CHAT_FOLLOWUP_2_DELAY:
            return False
        return self._send_youla_proactive_message(
            repo=repo,
            chat=chat,
            ad=ad,
            history_messages=history_messages,
            text=day2_text,
            reason="YOULA_SILENT_CHAT_FOLLOWUP_DAY2",
        )

    def _send_youla_proactive_message(
        self,
        *,
        repo: Repository,
        chat,
        ad,
        history_messages: list,
        text: str,
        reason: str,
    ) -> bool:
        if self.youla_client is None or not self.youla_client.enabled:
            return False

        latest_outbound = self._last_outbound_text(history_messages)
        if latest_outbound.strip() == text.strip():
            logger.info("Youla proactive send skipped: duplicate outbound chat=%s reason=%s", chat.external_chat_id, reason)
            return False

        transport = self._extract_youla_transport(history_messages)
        if transport is None:
            logger.warning("Youla proactive send skipped: missing transport chat=%s reason=%s", chat.external_chat_id, reason)
            return False
        if any(len(transport[key]) < 24 for key in ("sender_id", "recipient_id", "product_id")):
            repo.create_bot_reply(
                chat_id=chat.id,
                prompt_version="YOULA_SYSTEM_FOLLOWUPS_V1",
                text=text,
                status="FAILED",
                error="YOULA_INVALID_TRANSPORT_IDS",
            )
            repo.set_chat_state(chat.id, ChatState.IGNORED_OUT_OF_SCOPE)
            logger.warning(
                "Youla proactive send suppressed (invalid ids) chat=%s sender_id=%s recipient_id=%s product_id=%s reason=%s",
                chat.external_chat_id,
                transport["sender_id"],
                transport["recipient_id"],
                transport["product_id"],
                reason,
            )
            return True

        delay = max(0, int(self.settings.reply_delay_seconds))
        if delay > 0:
            time.sleep(delay)

        try:
            with_retry(
                lambda: self.youla_client.send_message(
                    sender_id=transport["sender_id"],
                    recipient_id=transport["recipient_id"],
                    product_id=transport["product_id"],
                    text=text,
                ),
                name="youla_send_message",
                attempts=3,
            )
        except httpx.HTTPStatusError as exc:
            body = ""
            try:
                body = (exc.response.text or "").lower()
            except Exception:  # noqa: BLE001
                body = ""
            if exc.response is not None and exc.response.status_code == 422 and (
                ("product_id" in body and "not found" in body)
                or ('"field":"product_id"' in body)
                or ('"field":"sender_id"' in body)
                or ('"field":"recipient_id"' in body)
            ):
                repo.create_bot_reply(
                    chat_id=chat.id,
                    prompt_version="YOULA_SYSTEM_FOLLOWUPS_V1",
                    text=text,
                    status="FAILED",
                    error="YOULA_INVALID_IDS_OR_PRODUCT",
                )
                repo.set_chat_state(chat.id, ChatState.IGNORED_OUT_OF_SCOPE)
                logger.warning(
                    "Youla proactive send suppressed (invalid ids/product) chat=%s product_id=%s reason=%s",
                    chat.external_chat_id,
                    transport["product_id"],
                    reason,
                )
                return True
            raise

        repo.create_bot_reply(
            chat_id=chat.id,
            prompt_version="YOULA_SYSTEM_FOLLOWUPS_V1",
            text=text,
            status="SENT",
        )
        repo.save_message(
            chat_id=chat.id,
            direction=MessageDirection.OUTBOUND,
            text=text,
            external_message_id=None,
            payload_json=json.dumps({"marketplace": "youla", "proactive_reason": reason}, ensure_ascii=False),
        )
        repo.set_chat_state(chat.id, ChatState.QUALIFYING)
        logger.info("Youla proactive send done chat=%s reason=%s", chat.external_chat_id, reason)
        return True

    def _extract_youla_transport(self, history_messages: list) -> dict[str, str] | None:
        for message in reversed(history_messages):
            if message.direction != MessageDirection.INBOUND.value:
                continue
            payload_raw = getattr(message, "payload_json", None)
            if not payload_raw:
                continue
            try:
                payload = json.loads(payload_raw)
            except Exception:  # noqa: BLE001
                continue
            if str(payload.get("marketplace", "")).lower() != "youla":
                continue
            sender_id = str(payload.get("recipient_id") or "").strip()
            recipient_id = str(payload.get("sender_id") or "").strip()
            product_id = str(payload.get("product_id") or "").strip()
            if sender_id and recipient_id and product_id:
                return {
                    "sender_id": sender_id,
                    "recipient_id": recipient_id,
                    "product_id": product_id,
                }
        return None
