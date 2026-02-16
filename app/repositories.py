from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256

from sqlalchemy import and_, delete, desc, func, select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models import (
    Ad,
    BotReply,
    Chat,
    EventLog,
    FeedbackEvent,
    Lead,
    Message,
    Prompt,
    RoutingDecision,
    Setting,
)
from app.types import ChatState, MessageDirection


@dataclass(slots=True)
class PersistedChatContext:
    ad: Ad
    chat: Chat


class Repository:
    def __init__(self, db: Session):
        self.db = db

    def upsert_ad(self, external_ad_id: str, title: str, category: str | None, raw_category: str | None, url: str | None) -> Ad:
        ad = self.db.scalar(select(Ad).where(Ad.external_ad_id == external_ad_id))
        if ad is None:
            ad = Ad(
                external_ad_id=external_ad_id,
                title=title,
                category=category,
                raw_category=raw_category,
                url=url,
            )
            self.db.add(ad)
            self.db.flush()
            return ad

        ad.title = title
        ad.category = category
        ad.raw_category = raw_category
        ad.url = url
        ad.updated_at = utcnow()
        self.db.flush()
        return ad

    def upsert_chat(self, external_chat_id: str, ad_id: int, customer_name: str | None) -> Chat:
        chat = self.db.scalar(select(Chat).where(Chat.external_chat_id == external_chat_id))
        if chat is None:
            chat = Chat(
                external_chat_id=external_chat_id,
                ad_id=ad_id,
                customer_name=customer_name,
                state=ChatState.NEW.value,
            )
            self.db.add(chat)
            self.db.flush()
            return chat

        chat.ad_id = ad_id
        if customer_name:
            chat.customer_name = customer_name
        chat.updated_at = utcnow()
        self.db.flush()
        return chat

    def save_message(
        self,
        chat_id: int,
        direction: MessageDirection,
        text: str,
        external_message_id: str | None,
        payload_json: str | None,
    ) -> Message | None:
        if external_message_id:
            exists = self.db.scalar(select(Message).where(Message.external_message_id == external_message_id))
            if exists is not None:
                return None

        msg = Message(
            chat_id=chat_id,
            direction=direction.value,
            text=text,
            external_message_id=external_message_id,
            payload_json=payload_json,
        )
        self.db.add(msg)

        chat = self.db.get(Chat, chat_id)
        if chat:
            chat.last_message_at = utcnow()
        self.db.flush()
        return msg

    def get_recent_messages(self, chat_id: int, limit: int = 10) -> list[Message]:
        rows = self.db.scalars(
            select(Message)
            .where(Message.chat_id == chat_id)
            .order_by(desc(Message.created_at))
            .limit(limit)
        ).all()
        return list(reversed(rows))

    def create_routing_decision(
        self,
        chat_id: int,
        domain: str,
        confidence: float,
        decision: str,
        reason: str,
    ) -> RoutingDecision:
        item = RoutingDecision(
            chat_id=chat_id,
            domain=domain,
            confidence=confidence,
            decision=decision,
            reason=reason,
        )
        self.db.add(item)

        chat = self.db.get(Chat, chat_id)
        if chat:
            chat.domain = domain
            if decision == "IGNORE_SILENT":
                chat.state = ChatState.IGNORED_OUT_OF_SCOPE.value
            elif chat.state == ChatState.NEW.value:
                chat.state = ChatState.QUALIFYING.value

        self.db.flush()
        return item

    def set_chat_state(self, chat_id: int, state: ChatState) -> None:
        chat = self.db.get(Chat, chat_id)
        if chat:
            chat.state = state.value
            chat.updated_at = utcnow()
            self.db.flush()

    def create_bot_reply(self, chat_id: int, prompt_version: str, text: str, status: str, error: str | None = None) -> BotReply:
        item = BotReply(
            chat_id=chat_id,
            prompt_version=prompt_version,
            text=text,
            status=status,
            error=error,
        )
        self.db.add(item)
        self.db.flush()
        return item

    def get_lead_by_contact(self, chat_id: int, contact_normalized: str) -> Lead | None:
        return self.db.scalar(
            select(Lead).where(
                and_(
                    Lead.chat_id == chat_id,
                    Lead.contact_normalized == contact_normalized,
                )
            )
        )

    def create_lead(
        self,
        chat_id: int,
        contact_raw: str,
        contact_normalized: str,
        summary: str,
    ) -> Lead | None:
        existing = self.get_lead_by_contact(chat_id=chat_id, contact_normalized=contact_normalized)
        if existing is not None:
            return None

        lead = Lead(
            chat_id=chat_id,
            contact_raw=contact_raw,
            contact_normalized=contact_normalized,
            summary=summary,
        )
        self.db.add(lead)
        self.db.flush()
        return lead

    def mark_lead_sent(self, lead_id: int) -> None:
        lead = self.db.get(Lead, lead_id)
        if lead:
            lead.sent_to_tg_at = utcnow()
            self.db.flush()

    def get_or_create_prompt(self, key: str, default_version: str, default_text: str) -> Prompt:
        prompt = self.db.scalar(select(Prompt).where(Prompt.key == key))
        if prompt:
            return prompt

        prompt = Prompt(key=key, version=default_version, text=default_text)
        self.db.add(prompt)
        self.db.flush()
        return prompt

    def update_prompt(self, key: str, version: str, text: str) -> Prompt:
        prompt = self.db.scalar(select(Prompt).where(Prompt.key == key))
        if prompt is None:
            prompt = Prompt(key=key, version=version, text=text)
            self.db.add(prompt)
            self.db.flush()
            return prompt

        prompt.version = version
        prompt.text = text
        prompt.updated_at = utcnow()
        self.db.flush()
        return prompt

    def get_setting(self, key: str) -> str | None:
        value = self.db.scalar(select(Setting.value).where(Setting.key == key))
        return value

    def set_setting(self, key: str, value: str) -> None:
        setting = self.db.scalar(select(Setting).where(Setting.key == key))
        if setting is None:
            self.db.add(Setting(key=key, value=value))
        else:
            setting.value = value
            setting.updated_at = utcnow()
        self.db.flush()

    def start_event(self, source: str, event_type: str, idempotency_key: str, payload: str) -> EventLog | None:
        payload_hash = sha256(payload.encode("utf-8")).hexdigest()
        event = self.db.scalar(select(EventLog).where(EventLog.idempotency_key == idempotency_key))

        if event and event.status == "PROCESSED":
            return None

        if event is None:
            event = EventLog(
                source=source,
                event_type=event_type,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                status="RECEIVED",
            )
            self.db.add(event)
        else:
            event.status = "RECEIVED"
            event.error_message = None
            event.payload_hash = payload_hash
            event.updated_at = utcnow()

        self.db.flush()
        return event

    def mark_event_processed(self, event_id: int) -> None:
        row = self.db.get(EventLog, event_id)
        if row:
            row.status = "PROCESSED"
            row.error_message = None
            row.updated_at = utcnow()
            self.db.flush()

    def mark_event_failed(self, event_id: int, message: str) -> None:
        row = self.db.get(EventLog, event_id)
        if row:
            row.status = "FAILED"
            row.error_message = message[:2000]
            row.updated_at = utcnow()
            self.db.flush()

    def create_feedback_event(self, chat_id: int | None, tag: str, comment: str) -> FeedbackEvent:
        item = FeedbackEvent(chat_id=chat_id, tag=tag, comment=comment)
        self.db.add(item)
        self.db.flush()
        return item

    def list_chats(self, limit: int = 100, offset: int = 0) -> list[tuple[Chat, Ad]]:
        rows = self.db.execute(
            select(Chat, Ad)
            .join(Ad, Chat.ad_id == Ad.id)
            .order_by(desc(Chat.updated_at))
            .limit(limit)
            .offset(offset)
        ).all()
        return rows

    def get_chat_detail(self, chat_id: int) -> tuple[Chat, Ad, list[Message]] | None:
        row = self.db.execute(select(Chat, Ad).join(Ad, Chat.ad_id == Ad.id).where(Chat.id == chat_id)).first()
        if row is None:
            return None

        chat, ad = row
        messages = self.db.scalars(select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at.asc())).all()
        return chat, ad, list(messages)

    def get_chat_by_external_id(self, external_chat_id: str) -> Chat | None:
        return self.db.scalar(select(Chat).where(Chat.external_chat_id == external_chat_id))

    def list_leads(self, limit: int = 100, offset: int = 0) -> list[Lead]:
        return self.db.scalars(
            select(Lead).order_by(desc(Lead.created_at)).limit(limit).offset(offset)
        ).all()

    def list_ignored_logs(self, limit: int = 100, offset: int = 0) -> list[RoutingDecision]:
        return self.db.scalars(
            select(RoutingDecision)
            .where(RoutingDecision.decision == "IGNORE_SILENT")
            .order_by(desc(RoutingDecision.created_at))
            .limit(limit)
            .offset(offset)
        ).all()

    def cleanup_retention(self, retention_days: int) -> dict[str, int]:
        cutoff = utcnow() - timedelta(days=retention_days)
        stats: dict[str, int] = {}

        for model_name, model in [
            ("messages", Message),
            ("bot_replies", BotReply),
            ("routing_decisions", RoutingDecision),
            ("events_log", EventLog),
            ("feedback_events", FeedbackEvent),
            ("leads", Lead),
        ]:
            result = self.db.execute(delete(model).where(model.created_at < cutoff))
            stats[model_name] = result.rowcount or 0

        orphan_chats = self.db.scalars(
            select(Chat.id)
            .outerjoin(Message, Message.chat_id == Chat.id)
            .outerjoin(Lead, Lead.chat_id == Chat.id)
            .group_by(Chat.id)
            .having(and_(func.count(Message.id) == 0, func.count(Lead.id) == 0))
        ).all()

        if orphan_chats:
            result = self.db.execute(delete(Chat).where(Chat.id.in_(orphan_chats)))
            stats["chats"] = result.rowcount or 0
        else:
            stats["chats"] = 0

        self.db.flush()
        return stats
