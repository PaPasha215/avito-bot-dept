from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import Settings
from app.models import Lead, RoutingDecision
from app.repositories import Repository
from app.services.classifier import DomainClassifier
from app.services.cleanup import RetentionService
from app.services.lead_detector import LeadDetector
from app.services.processor import MessageProcessor
from app.services.prompt_service import PromptService
from app.services.router import RouterService
from app.types import AdContext, IncomingEvent, MessageDirection


class DummyAvito:
    def __init__(self):
        self.sent_messages: list[tuple[str, str]] = []

    def fetch_updates(self, cursor):
        return [], cursor

    def send_message(self, chat_id: str, text: str):
        self.sent_messages.append((chat_id, text))


class DummyOpenAI:
    enabled = False

    def __init__(self):
        self.reply_calls = 0
        self.last_system_prompt = ""
        self.last_chat_history: list[dict] = []

    def generate_reply(self, system_prompt: str, chat_history: list[dict]) -> str:
        self.reply_calls += 1
        self.last_system_prompt = system_prompt
        self.last_chat_history = chat_history
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
        youla_client=None,
        telegram_client=telegram,
        router_service=router,
        prompt_service=PromptService(settings=settings),
        self_learning_service=None,
        stats_reporting_service=None,
        lead_detector=LeadDetector(),
        retention_service=RetentionService(),
    )
    return processor, avito, telegram, openai


def test_processor_ignores_auto_category(db_session):
    settings = Settings(polling_enabled=False, telegram_leads_chat_id="-1001")
    processor, avito, telegram, _ = build_processor(settings)

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
    processor, avito, telegram, openai = build_processor(settings)

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
    assert openai.reply_calls == 1
    assert "Заголовок объявления: Койко-место" in openai.last_system_prompt

    leads = db_session.query(Lead).all()
    assert len(leads) == 1
    assert leads[0].contact_normalized == "+79992182468"


def test_processor_clarifies_short_budget_range(db_session):
    settings = Settings(polling_enabled=False, telegram_leads_chat_id="-1001")
    processor, avito, telegram, openai = build_processor(settings)
    repo = Repository(db_session)

    ad = repo.upsert_ad(
        external_ad_id="ad-budget",
        title="Койко-место 15 м2",
        category="REAL_ESTATE",
        raw_category="Недвижимость",
        url=None,
    )
    chat = repo.upsert_chat(external_chat_id="chat-budget", ad_id=ad.id, customer_name="Антон")
    repo.save_message(
        chat_id=chat.id,
        direction=MessageDirection.OUTBOUND,
        text="Подскажите, пожалуйста, какой у вас бюджет в месяц?",
        external_message_id=None,
        payload_json=None,
    )
    db_session.commit()

    event = IncomingEvent(
        event_id="evt-budget-1",
        chat_id="chat-budget",
        message_id="msg-budget-1",
        sender_type="user",
        text="8-12",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(ad_id="ad-budget", title="Койко-место 15 м2", category="Недвижимость"),
        customer_name="Антон",
    )

    outcome = processor._process_event(db_session, event)
    assert outcome == "replied"
    assert telegram.leads_sent == 0
    assert openai.reply_calls == 0
    assert avito.sent_messages[-1][1] == "Простите, вы имеете в виду 8–12 тысяч рублей за проживание в месяц?"


def test_processor_handles_call_me_request_without_direct_number(db_session):
    settings = Settings(polling_enabled=False, telegram_leads_chat_id="-1001")
    processor, avito, telegram, openai = build_processor(settings)

    event = IncomingEvent(
        event_id="evt-call-1",
        chat_id="chat-call",
        message_id="msg-call-1",
        sender_type="user",
        text="Говорите, куда вам набрать",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(ad_id="ad-call", title="Койко-место в центре", category="Недвижимость"),
        customer_name="Антон",
    )

    outcome = processor._process_event(db_session, event)
    assert outcome == "replied"
    assert telegram.leads_sent == 0
    assert openai.reply_calls == 0
    reply = avito.sent_messages[-1][1].lower()
    assert "многоканаль" in reply
    assert "оставьте" in reply
    assert "+7 922 128-56-86" not in reply


def test_processor_first_reply_for_explicit_availability_query_continues_qualification(db_session):
    settings = Settings(polling_enabled=False, telegram_leads_chat_id="-1001")
    processor, avito, telegram, openai = build_processor(settings)

    event = IncomingEvent(
        event_id="evt-soft-1",
        chat_id="chat-soft",
        message_id="msg-soft-1",
        sender_type="user",
        text="Есть свободные места?",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(ad_id="ad-soft", title="Койко-место 20 м2", category="Недвижимость"),
        customer_name="Александр",
    )

    outcome = processor._process_event(db_session, event)
    assert outcome == "replied"
    assert telegram.leads_sent == 0
    assert openai.reply_calls == 0

    reply = avito.sent_messages[-1][1].lower()
    assert "актуален" not in reply
    assert "на какие даты" in reply


def test_processor_first_reply_skips_redundant_actuality_for_explicit_settlement_intent(db_session):
    settings = Settings(polling_enabled=False, telegram_leads_chat_id="-1001")
    processor, avito, telegram, openai = build_processor(settings)

    event = IncomingEvent(
        event_id="evt-intent-1",
        chat_id="chat-intent",
        message_id="msg-intent-1",
        sender_type="user",
        text="Здравствуйте, можно снять всю комнату на воскресенье за 800р?",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(ad_id="ad-intent", title="Койко-место 20 м2", category="Недвижимость"),
        customer_name="Сергей",
    )

    outcome = processor._process_event(db_session, event)
    assert outcome == "replied"
    assert telegram.leads_sent == 0
    assert openai.reply_calls == 0

    reply = avito.sent_messages[-1][1].lower()
    assert "актуален" not in reply
    assert "сколько человек" in reply


def test_processor_monthly_listing_rejects_short_stay(db_session):
    settings = Settings(polling_enabled=False, telegram_leads_chat_id="-1001")
    processor, avito, telegram, openai = build_processor(settings)

    event = IncomingEvent(
        event_id="evt-monthly-1",
        chat_id="chat-monthly",
        message_id="msg-monthly-1",
        sender_type="user",
        text="Можно на сутки или на выходные?",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(ad_id="ad-monthly", title="Койко-место 20 м2, Пехотинцев 7", category="Недвижимость"),
        customer_name="Никита",
    )

    outcome = processor._process_event(db_session, event)
    assert outcome == "replied"
    assert telegram.leads_sent == 0
    assert openai.reply_calls == 0

    reply = avito.sent_messages[-1][1].lower()
    assert "только на месяц" in reply
    assert "помесячное заселение" in reply


def test_processor_hostel_short_stay_price_uses_approximate_daily_rate(db_session):
    settings = Settings(polling_enabled=False, telegram_leads_chat_id="-1001")
    processor, avito, telegram, openai = build_processor(settings)

    event = IncomingEvent(
        event_id="evt-hostel-1",
        chat_id="chat-hostel",
        message_id="msg-hostel-1",
        sender_type="user",
        text="Можно на воскресенье снять всю комнату за 800р?",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(
            ad_id="ad-hostel",
            title="Койко-место, Куйбышева 30, Сити Е",
            category="Недвижимость",
        ),
        customer_name="Сергей",
    )

    outcome = processor._process_event(db_session, event)
    assert outcome == "replied"
    assert telegram.leads_sent == 0
    assert openai.reply_calls == 0

    reply = avito.sent_messages[-1][1].lower()
    assert "от 700" in reply
    assert "точная стоимость" in reply
    assert "на какие даты" in reply


def test_processor_short_stay_listing_with_daily_price_in_title_treated_as_hostel(db_session):
    settings = Settings(polling_enabled=False, telegram_leads_chat_id="-1001")
    processor, avito, telegram, openai = build_processor(settings)

    event = IncomingEvent(
        event_id="evt-hostel-2",
        chat_id="chat-hostel-2",
        message_id="msg-hostel-2",
        sender_type="user",
        text="Можно на сутки, какая цена?",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(
            ad_id="ad-hostel-2",
            title="Койко-место 20 м2, 400 ₽ за сутки",
            category="Недвижимость",
        ),
        customer_name="Сергей",
    )

    outcome = processor._process_event(db_session, event)
    assert outcome == "replied"
    assert telegram.leads_sent == 0
    assert openai.reply_calls == 0

    reply = avito.sent_messages[-1][1].lower()
    assert "от 700" in reply
    assert "на какие даты" in reply


def test_processor_ignores_abuse_or_threat_messages(db_session):
    settings = Settings(polling_enabled=False, telegram_leads_chat_id="-1001")
    processor, avito, telegram, openai = build_processor(settings)

    event = IncomingEvent(
        event_id="evt-abuse-1",
        chat_id="chat-abuse",
        message_id="msg-abuse-1",
        sender_type="user",
        text="Вы мошенники, я подам жалобу в прокуратуру",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(ad_id="ad-abuse", title="Койко-место 20 м2", category="Недвижимость"),
        customer_name="Тест",
    )

    outcome = processor._process_event(db_session, event)
    assert outcome == "ignored"
    assert avito.sent_messages == []
    assert telegram.leads_sent == 0
    assert openai.reply_calls == 0

    decisions = db_session.query(RoutingDecision).filter(RoutingDecision.chat_id.isnot(None)).all()
    assert decisions[-1].decision == "IGNORE_SILENT"
    assert decisions[-1].reason == "ABUSE_OR_THREAT"
