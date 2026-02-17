from __future__ import annotations

import json
import logging
import re
import time
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
from app.services.self_learning import SelfLearningService
from app.services.stats_reporting import StatsReportingService
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

    def __init__(
        self,
        settings: Settings,
        avito_client: AvitoClient,
        openai_client: OpenAIClient,
        telegram_client: TelegramClient,
        router_service: RouterService,
        prompt_service: PromptService,
        self_learning_service: SelfLearningService | None,
        stats_reporting_service: StatsReportingService | None,
        lead_detector: LeadDetector,
        retention_service: RetentionService,
    ):
        self.settings = settings
        self.avito_client = avito_client
        self.openai_client = openai_client
        self.telegram_client = telegram_client
        self.router_service = router_service
        self.prompt_service = prompt_service
        self.self_learning_service = self_learning_service
        self.stats_reporting_service = stats_reporting_service
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
                            "Stats report cycle done: sent=%s target=%s items=%s reach=%s conversion=%s low_conversion=%s",
                            report_result.sent,
                            report_result.target_chat_id,
                            report_result.items_total,
                            report_result.top_reach_count,
                            report_result.top_conversion_count,
                            report_result.low_conversion_count,
                        )

        return stats

    def _process_event(self, db: Session, event: IncomingEvent, allow_reply: bool = True) -> str:
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

            if not allow_reply:
                repo.mark_event_processed(event_log.id)
                db.commit()
                return "processed"

            history_messages = repo.get_recent_messages(chat.id, limit=self.settings.max_recent_messages_for_reply)
            rule_based_reply = self._build_rule_based_reply(
                ad_title=ad.title,
                ad_category=ad.raw_category or ad.category,
                history_messages=history_messages,
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
                lambda: self._send_with_delay(chat_id=event.chat_id, text=response_text),
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

    def _send_with_delay(self, chat_id: str, text: str) -> None:
        delay = max(0, int(self.settings.reply_delay_seconds))
        if delay > 0:
            time.sleep(delay)
        self.avito_client.send_message(chat_id=chat_id, text=text)

    def _build_rule_based_reply(
        self,
        ad_title: str,
        ad_category: str | None,
        history_messages: list,
    ) -> str | None:
        latest_inbound = self._latest_inbound_text(history_messages)
        if not latest_inbound:
            return None

        has_contact = self._extract_contact(history_messages) is not None
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

        budget_range = self._detect_short_budget_range_thousands(latest_inbound, history_messages)
        if budget_range is not None and not has_contact:
            left, right = budget_range
            if left == right:
                return f"Простите, вы имеете в виду {left} тысяч рублей за проживание в месяц?"
            return f"Простите, вы имеете в виду {left}–{right} тысяч рублей за проживание в месяц?"

        explicit_intent = self._has_explicit_settlement_intent(latest_inbound)
        if explicit_intent and not has_contact and self._is_deflective_last_outbound(history_messages):
            followup = self._build_qualification_followup(latest_inbound)
            return f"Понял вас. {followup}"

        if self._is_first_bot_reply(history_messages) and not has_contact:
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
            "- Отвечай только в контексте этого объявления.\n"
            "- Если объявление про койко-место, не переключайся на другие форматы без прямого запроса клиента.\n"
            "- Если тип размещения неочевиден, задай один короткий уточняющий вопрос.\n"
            "- Не задавай более одного вопроса в одном сообщении.\n"
            "- Если клиент уже явно просит заселение/наличие/даты, не переспрашивай «актуально ли».\n"
            "- Не завершай диалог фразой «уточню у менеджера» без попытки собрать недостающие данные и контакт.\n"
            f"- {stage_rule}\n"
        )
        return f"{base_prompt}{runtime_rules}"

    def _latest_inbound_text(self, history_messages: list) -> str:
        for message in reversed(history_messages):
            if message.direction == MessageDirection.INBOUND.value:
                return message.text.strip()
        return ""

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

    def _has_explicit_settlement_intent(self, text: str) -> bool:
        return bool(text and self.SETTLEMENT_INTENT_RE.search(text))

    def _has_date_signal(self, text: str) -> bool:
        return bool(text and self.DATE_INTENT_RE.search(text))

    def _has_people_signal(self, text: str) -> bool:
        return bool(text and self.PEOPLE_INTENT_RE.search(text))

    def _has_budget_signal(self, text: str) -> bool:
        return bool(text and (self.CURRENCY_RE.search(text) or re.search(r"\b\d{3,6}\b", text)))

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
