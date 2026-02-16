from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import SessionLocal
from app.integrations.avito import AvitoClient
from app.integrations.openai_client import OpenAIClient
from app.integrations.telegram import TelegramClient
from app.repositories import Repository
from app.services.cleanup import RetentionService
from app.services.lead_detector import LeadDetector
from app.services.prompt_service import PromptService
from app.services.retry import with_retry
from app.services.router import RouterService
from app.types import ChatDecision, ChatState, IncomingEvent, LeadStatus, MessageDirection

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProcessingStats:
    polled_events: int = 0
    processed_events: int = 0
    ignored_events: int = 0
    replied_events: int = 0
    leads_created: int = 0


class MessageProcessor:
    CURSOR_KEY = "avito_cursor"

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
    ):
        self.settings = settings
        self.avito_client = avito_client
        self.openai_client = openai_client
        self.telegram_client = telegram_client
        self.router_service = router_service
        self.prompt_service = prompt_service
        self.lead_detector = lead_detector
        self.retention_service = retention_service

    def process_updates_once(self) -> ProcessingStats:
        stats = ProcessingStats()

        with SessionLocal() as db:
            repo = Repository(db)
            cursor = repo.get_setting(self.CURSOR_KEY)

            events, next_cursor = with_retry(
                lambda: self.avito_client.fetch_updates(cursor=cursor),
                name="avito_fetch_updates",
                attempts=3,
            )
            stats.polled_events = len(events)

            for event in events:
                try:
                    outcome = self._process_event(db=db, event=event)
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

        return stats

    def _process_event(self, db: Session, event: IncomingEvent) -> str:
        repo = Repository(db)
        payload = json.dumps(
            {
                "event_id": event.event_id,
                "chat_id": event.chat_id,
                "message_id": event.message_id,
                "text": event.text,
                "ad_id": event.ad_context.ad_id,
                "ad_title": event.ad_context.title,
                "ad_category": event.ad_context.category,
            },
            ensure_ascii=False,
        )

        idempotency_key = f"avito:{event.event_id}"
        event_log = repo.start_event(
            source="avito",
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

            repo.save_message(
                chat_id=chat.id,
                direction=MessageDirection.INBOUND,
                text=event.text,
                external_message_id=event.message_id,
                payload_json=payload,
            )

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

            prompt = self.prompt_service.get_real_estate_prompt(db)
            history_messages = repo.get_recent_messages(chat.id, limit=self.settings.max_recent_messages_for_reply)
            chat_history = [
                {
                    "role": "user" if m.direction == MessageDirection.INBOUND.value else "assistant",
                    "content": m.text,
                }
                for m in history_messages
            ]

            response_text = with_retry(
                lambda: self.openai_client.generate_reply(prompt.text, chat_history),
                name="openai_generate_reply",
                attempts=3,
            )
            response_text = self._trim_sentences(response_text, max_sentences=4)

            with_retry(
                lambda: self.avito_client.send_message(chat_id=event.chat_id, text=response_text),
                name="avito_send_message",
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

            contact = self._extract_contact(history_messages)
            lead_created = False
            if contact:
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
                        self._send_lead_to_telegram(
                            lead_id=lead.id,
                            ad_title=ad.title,
                            ad_url=ad.url,
                            customer_name=chat.customer_name,
                            contact_raw=raw,
                            summary=summary,
                            context_lines=transcript,
                        )
                        repo.mark_lead_sent(lead.id)
                        repo.set_chat_state(chat.id, ChatState.TRANSFERRED_TO_MANAGER)
                        lead_created = True

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
                customer_name=customer_name,
                contact_raw=contact_raw,
                summary=summary,
                context_lines=context_lines,
                status=LeadStatus.NEW,
            ),
            name="telegram_send_lead",
            attempts=3,
        )

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
