from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import Settings
from app.models import Lead, RoutingDecision
from app.services.classifier import DomainClassifier
from app.services.cleanup import RetentionService
from app.services.lead_detector import LeadDetector
from app.services.processor import MessageProcessor
from app.services.prompt_service import PromptService
from app.services.router import RouterService
from app.types import AdContext, IncomingEvent


class DummyAvito:
    def __init__(self):
        self.sent_messages: list[tuple[str, str]] = []

    def fetch_updates(self, cursor):
        return [], cursor

    def send_message(self, chat_id: str, text: str):
        self.sent_messages.append((chat_id, text))


class DummyOpenAI:
    enabled = False

    def generate_reply(self, system_prompt: str, chat_history: list[dict]) -> str:
        return "Здравствуйте! Подскажите, пожалуйста, номер телефона или мессенджер?"

    def summarize_lead(self, ad_title: str, contact_raw: str, transcript: list[str]) -> str:
        return "Клиент заинтересован, передать менеджеру."


class DummyTelegram:
    def __init__(self):
        self.leads_sent = 0

    def send_lead_card(self, **kwargs):
        self.leads_sent += 1


def build_processor(settings: Settings):
    avito = DummyAvito()
    openai = DummyOpenAI()
    telegram = DummyTelegram()
    router = RouterService(classifier=DomainClassifier(), confidence_threshold=0.8)
    processor = MessageProcessor(
        settings=settings,
        avito_client=avito,
        openai_client=openai,
        telegram_client=telegram,
        router_service=router,
        prompt_service=PromptService(settings=settings),
        lead_detector=LeadDetector(),
        retention_service=RetentionService(),
    )
    return processor, avito, telegram


def test_processor_ignores_auto_category(db_session):
    settings = Settings(polling_enabled=False, telegram_leads_chat_id="-1001")
    processor, avito, telegram = build_processor(settings)

    event = IncomingEvent(
        event_id="evt-1",
        chat_id="chat-auto",
        message_id="msg-1",
        sender_type="user",
        text="Здравствуйте, какой расход масла?",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(ad_id="ad-auto", title="Infiniti FX45", category="Автомобили"),
        customer_name="Евгений",
    )

    outcome = processor._process_event(db_session, event)
    assert outcome == "ignored"
    assert avito.sent_messages == []
    assert telegram.leads_sent == 0

    decisions = db_session.query(RoutingDecision).all()
    assert len(decisions) == 1
    assert decisions[0].decision == "IGNORE_SILENT"


def test_processor_creates_lead_on_contact(db_session):
    settings = Settings(polling_enabled=False, telegram_leads_chat_id="-1001")
    processor, avito, telegram = build_processor(settings)

    event = IncomingEvent(
        event_id="evt-2",
        chat_id="chat-real",
        message_id="msg-2",
        sender_type="user",
        text="Здравствуйте, нужен хостел на неделю. Мой номер +7 999 218-24-68",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(ad_id="ad-real", title="Койко-место", category="Недвижимость"),
        customer_name="Иван",
    )

    outcome = processor._process_event(db_session, event)
    assert outcome == "lead"
    assert len(avito.sent_messages) == 1
    assert telegram.leads_sent == 1

    leads = db_session.query(Lead).all()
    assert len(leads) == 1
    assert leads[0].contact_normalized == "+79992182468"
