from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
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
    LearningExample,
    LearningReplyUsage,
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

    def list_feedback_events_by_chat(self, chat_id: int) -> list[FeedbackEvent]:
        return self.db.scalars(
            select(FeedbackEvent)
            .where(FeedbackEvent.chat_id == chat_id)
            .order_by(FeedbackEvent.created_at.asc())
        ).all()

    def list_leads_by_chat(self, chat_id: int) -> list[Lead]:
        return self.db.scalars(
            select(Lead)
            .where(Lead.chat_id == chat_id)
            .order_by(Lead.created_at.asc())
        ).all()

    def get_learning_candidate_chat_ids(self, since: datetime, limit: int = 200) -> list[int]:
        lead_rows = self.db.execute(
            select(Lead.chat_id, func.max(Lead.created_at))
            .where(Lead.created_at >= since)
            .group_by(Lead.chat_id)
        ).all()
        feedback_rows = self.db.execute(
            select(FeedbackEvent.chat_id, func.max(FeedbackEvent.created_at))
            .where(
                and_(
                    FeedbackEvent.chat_id.is_not(None),
                    FeedbackEvent.created_at >= since,
                )
            )
            .group_by(FeedbackEvent.chat_id)
        ).all()

        latest_by_chat: dict[int, datetime] = {}
        for chat_id, created_at in [*lead_rows, *feedback_rows]:
            if chat_id is None or created_at is None:
                continue
            previous = latest_by_chat.get(chat_id)
            if previous is None or created_at > previous:
                latest_by_chat[chat_id] = created_at

        ordered = sorted(latest_by_chat.items(), key=lambda x: x[1], reverse=True)
        return [chat_id for chat_id, _ in ordered[:limit]]

    def create_learning_example(
        self,
        version: str,
        domain: str,
        source_chat_id: int | None,
        source_kind: str,
        source_tag: str | None,
        intent_text: str,
        bad_reply: str | None,
        better_reply: str,
        rule_text: str,
        weight: float,
        example_hash: str,
    ) -> LearningExample | None:
        existing = self.db.scalar(select(LearningExample).where(LearningExample.example_hash == example_hash))
        if existing is not None:
            return None

        item = LearningExample(
            version=version,
            domain=domain,
            source_chat_id=source_chat_id,
            source_kind=source_kind,
            source_tag=source_tag,
            intent_text=intent_text,
            bad_reply=bad_reply,
            better_reply=better_reply,
            rule_text=rule_text,
            weight=weight,
            example_hash=example_hash,
        )
        self.db.add(item)
        self.db.flush()
        return item

    def list_learning_examples(self, version: str, domain: str, limit: int = 200) -> list[LearningExample]:
        return self.db.scalars(
            select(LearningExample)
            .where(
                and_(
                    LearningExample.enabled.is_(True),
                    LearningExample.version == version,
                    LearningExample.domain == domain,
                )
            )
            .order_by(desc(LearningExample.weight), desc(LearningExample.created_at))
            .limit(limit)
        ).all()

    def get_latest_learning_version(self) -> str | None:
        return self.db.scalar(
            select(LearningExample.version)
            .where(LearningExample.enabled.is_(True))
            .order_by(desc(LearningExample.created_at))
            .limit(1)
        )

    def count_learning_examples(self, version: str | None = None) -> int:
        stmt = select(func.count(LearningExample.id)).where(LearningExample.enabled.is_(True))
        if version:
            stmt = stmt.where(LearningExample.version == version)
        return int(self.db.scalar(stmt) or 0)

    def create_learning_reply_usage(
        self,
        chat_id: int,
        external_chat_id: str,
        learning_version: str | None,
        examples_count: int,
    ) -> LearningReplyUsage:
        item = LearningReplyUsage(
            chat_id=chat_id,
            external_chat_id=external_chat_id,
            learning_version=learning_version,
            examples_count=examples_count,
        )
        self.db.add(item)
        self.db.flush()
        return item

    def get_learning_quality_stats(self, version: str, since: datetime) -> dict[str, int]:
        usage_rows = self.db.scalars(
            select(LearningReplyUsage.chat_id)
            .where(
                and_(
                    LearningReplyUsage.learning_version == version,
                    LearningReplyUsage.created_at >= since,
                    LearningReplyUsage.examples_count > 0,
                )
            )
        ).all()
        chat_ids = sorted(set(usage_rows))
        usage_count = len(usage_rows)
        if not chat_ids:
            return {
                "usage_count": usage_count,
                "chat_count": 0,
                "leads_count": 0,
                "feedback_count": 0,
                "wrong_domain_count": 0,
            }

        leads_count = self.db.scalar(
            select(func.count(Lead.id)).where(
                and_(
                    Lead.chat_id.in_(chat_ids),
                    Lead.created_at >= since,
                )
            )
        ) or 0
        feedback_count = self.db.scalar(
            select(func.count(FeedbackEvent.id)).where(
                and_(
                    FeedbackEvent.chat_id.in_(chat_ids),
                    FeedbackEvent.created_at >= since,
                )
            )
        ) or 0
        wrong_domain_count = self.db.scalar(
            select(func.count(FeedbackEvent.id)).where(
                and_(
                    FeedbackEvent.chat_id.in_(chat_ids),
                    FeedbackEvent.created_at >= since,
                    FeedbackEvent.tag == "WRONG_DOMAIN",
                )
            )
        ) or 0

        return {
            "usage_count": int(usage_count),
            "chat_count": len(chat_ids),
            "leads_count": int(leads_count),
            "feedback_count": int(feedback_count),
            "wrong_domain_count": int(wrong_domain_count),
        }

    def get_chat_diagnostics(
        self,
        external_chat_id: str,
        message_limit: int = 200,
        event_limit: int = 200,
    ) -> dict | None:
        row = self.db.execute(select(Chat, Ad).join(Ad, Chat.ad_id == Ad.id).where(Chat.external_chat_id == external_chat_id)).first()
        if row is None:
            return None

        chat, ad = row

        messages = self.db.scalars(
            select(Message)
            .where(Message.chat_id == chat.id)
            .order_by(Message.created_at.asc())
            .limit(message_limit)
        ).all()
        routing_decisions = self.db.scalars(
            select(RoutingDecision)
            .where(RoutingDecision.chat_id == chat.id)
            .order_by(RoutingDecision.created_at.asc())
        ).all()
        bot_replies = self.db.scalars(
            select(BotReply)
            .where(BotReply.chat_id == chat.id)
            .order_by(BotReply.created_at.asc())
        ).all()
        leads = self.db.scalars(
            select(Lead)
            .where(Lead.chat_id == chat.id)
            .order_by(Lead.created_at.asc())
        ).all()
        feedback_events = self.db.scalars(
            select(FeedbackEvent)
            .where(FeedbackEvent.chat_id == chat.id)
            .order_by(FeedbackEvent.created_at.asc())
        ).all()
        event_logs = self.db.scalars(
            select(EventLog)
            .where(
                and_(
                    EventLog.source == "avito",
                    EventLog.idempotency_key.like(f"avito:{external_chat_id}:%"),
                )
            )
            .order_by(EventLog.created_at.asc())
            .limit(event_limit)
        ).all()

        inbound_count = self.db.scalar(
            select(func.count(Message.id)).where(
                and_(
                    Message.chat_id == chat.id,
                    Message.direction == MessageDirection.INBOUND.value,
                )
            )
        ) or 0
        outbound_count = self.db.scalar(
            select(func.count(Message.id)).where(
                and_(
                    Message.chat_id == chat.id,
                    Message.direction == MessageDirection.OUTBOUND.value,
                )
            )
        ) or 0

        return {
            "chat": chat,
            "ad": ad,
            "messages": list(messages),
            "routing_decisions": list(routing_decisions),
            "bot_replies": list(bot_replies),
            "leads": list(leads),
            "feedback_events": list(feedback_events),
            "event_logs": list(event_logs),
            "inbound_count": int(inbound_count),
            "outbound_count": int(outbound_count),
        }

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
            ("learning_reply_usage", LearningReplyUsage),
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
