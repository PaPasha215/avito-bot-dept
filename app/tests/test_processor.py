from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
        self.messages_sent: list[tuple[str, str]] = []

    def send_lead_card(self, **kwargs):
        self.leads_sent += 1

    def send_message(self, chat_id: str, text: str):
        self.messages_sent.append((chat_id, text))


class DummyYoula:
    enabled = True

    def __init__(self):
        self.sent_messages: list[dict] = []
        self.products: dict[str, dict] = {}

    def send_message(self, sender_id: str, recipient_id: str, product_id: str, text: str, images=None):
        self.sent_messages.append(
            {
                "sender_id": sender_id,
                "recipient_id": recipient_id,
                "product_id": product_id,
                "text": text,
            }
        )
        return {"id": "m1", "chat_id": "c1"}

    def get_product(self, product_id: str) -> dict:
        return self.products.get(product_id, {})


class DummyCrmIngest:
    def __init__(self):
        self.enabled = True
        self.calls: list[dict] = []
        self._created_once = False

    def ingest(self, **payload):
        self.calls.append(payload)
        created = not self._created_once
        self._created_once = True
        return type("IngestResult", (), {"created": created, "lead_id": 1, "raw": {"created": created}})()


def build_processor(settings: Settings, crm_ingest_client=None, youla_client=None):
    avito = DummyAvito()
    openai = DummyOpenAI()
    telegram = DummyTelegram()
    router = RouterService(classifier=DomainClassifier(), confidence_threshold=0.8)
    processor = MessageProcessor(
        settings=settings,
        avito_client=avito,
        openai_client=openai,
        youla_client=youla_client,
        telegram_client=telegram,
        router_service=router,
        prompt_service=PromptService(settings=settings),
        self_learning_service=None,
        stats_reporting_service=None,
        heartbeat_service=None,
        lead_detector=LeadDetector(),
        retention_service=RetentionService(),
        crm_ingest_client=crm_ingest_client,
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


def test_processor_syncs_avito_dialog_to_crm_ingest_without_duplicates(db_session):
    settings = Settings(polling_enabled=False, telegram_leads_chat_id="-1001", crm_ingest_enabled=True)
    crm_ingest = DummyCrmIngest()
    processor, avito, telegram, _ = build_processor(settings, crm_ingest_client=crm_ingest)

    event = IncomingEvent(
        event_id="evt-crm-1",
        chat_id="chat-crm-1",
        message_id="msg-crm-1",
        sender_type="user",
        text="Здравствуйте, нужен хостел. Мой номер +7 999 218-24-68",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(ad_id="ad-crm-1", title="Койко-место, центр", category="Недвижимость"),
        customer_name="Иван",
        marketplace="avito",
    )

    outcome = processor._process_event(db_session, event)
    assert outcome == "lead"
    assert len(avito.sent_messages) == 1
    assert telegram.leads_sent == 1

    # 1) inbound message, 2) outbound bot reply, 3) lead/contact enrichment
    assert len(crm_ingest.calls) == 3
    assert crm_ingest.calls[0]["message_direction"] == "IN"
    assert crm_ingest.calls[1]["message_direction"] == "OUT"
    assert "message_body" not in crm_ingest.calls[2]
    assert crm_ingest.calls[2]["status"] == "CALL"


def test_processor_notifies_manager_on_new_crm_dialog_once(db_session):
    settings = Settings(
        polling_enabled=False,
        crm_ingest_enabled=True,
        crm_ingest_notify_telegram_enabled=True,
        crm_ingest_notify_chat_id="-2001",
    )
    crm_ingest = DummyCrmIngest()
    processor, _, telegram, _ = build_processor(settings, crm_ingest_client=crm_ingest)

    event1 = IncomingEvent(
        event_id="evt-crm-notify-1",
        chat_id="chat-crm-notify",
        message_id="msg-crm-notify-1",
        sender_type="user",
        text="Здравствуйте, актуально?",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(ad_id="ad-crm-notify", title="Комната 18м2", category="Недвижимость"),
        customer_name="Кирилл",
        marketplace="avito",
    )
    event2 = IncomingEvent(
        event_id="evt-crm-notify-2",
        chat_id="chat-crm-notify",
        message_id="msg-crm-notify-2",
        sender_type="user",
        text="Можно сегодня посмотреть?",
        created_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        ad_context=AdContext(ad_id="ad-crm-notify", title="Комната 18м2", category="Недвижимость"),
        customer_name="Кирилл",
        marketplace="avito",
    )

    processor._process_event(db_session, event1)
    processor._process_event(db_session, event2)

    # created=True only for first ingest create, so telegram notify should be single-shot
    assert len(telegram.messages_sent) == 1
    chat_id, text = telegram.messages_sent[0]
    assert chat_id == "-2001"
    assert "Новый CRM-диалог" in text


def test_processor_notifies_manager_on_new_youla_crm_dialog_with_chat_link(db_session):
    settings = Settings(
        polling_enabled=False,
        crm_ingest_enabled=True,
        crm_ingest_notify_telegram_enabled=True,
        crm_ingest_notify_chat_id="-2001",
    )
    crm_ingest = DummyCrmIngest()
    youla = DummyYoula()
    processor, _, telegram, _ = build_processor(settings, crm_ingest_client=crm_ingest, youla_client=youla)

    event = IncomingEvent(
        event_id="evt-youla-crm-notify-1",
        chat_id="youla:chat-123",
        message_id="youla:msg-1",
        sender_type="user",
        text="Здравствуйте",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(
            ad_id="youla:prod-1",
            title="Комната 18 м2",
            category="Недвижимость",
            url="https://youla.ru/some-ad",
        ),
        marketplace="youla",
        sender_id="buyer-1",
        recipient_id="seller-1",
        product_id="prod-1",
    )

    processor._process_event(db_session, event)

    assert len(telegram.messages_sent) == 1
    _, text = telegram.messages_sent[0]
    assert "Новый CRM-диалог" in text
    assert "Чат" in text
    assert "https://youla.ru/web-chat/chat-123" in text
    assert "Объявление" in text
    assert "https://youla.ru/some-ad" in text


def test_processor_enriches_youla_product_title_and_absolute_url_for_crm(db_session):
    settings = Settings(polling_enabled=False, crm_ingest_enabled=True)
    crm_ingest = DummyCrmIngest()
    youla = DummyYoula()
    youla.products["664397540fc27db739041b14"] = {
        "name": "Комната, 18 м²",
        "url": "/ekaterinburg/nedvijimost/arenda-komnati-posutochno/komnata-18-m2-664397540fc27db739041b14",
        "short_url": "https://youla.io/p664397540fc27db739041b14",
    }
    processor, _, _, _ = build_processor(settings, crm_ingest_client=crm_ingest, youla_client=youla)

    event = IncomingEvent(
        event_id="evt-youla-enrich-1",
        chat_id="youla:chat-enrich-1",
        message_id="youla:msg-enrich-1",
        sender_type="user",
        text="Здравствуйте, есть свободные места?",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(
            ad_id="youla:664397540fc27db739041b14",
            title="Youla product 664397540fc27db739041b14",
            category="Недвижимость",
            url=None,
        ),
        marketplace="youla",
        sender_id="buyer-1",
        recipient_id="seller-1",
        product_id="664397540fc27db739041b14",
    )

    outcome = processor._process_event(db_session, event)
    assert outcome in {"replied", "lead", "processed"}
    # inbound sync call is first
    assert crm_ingest.calls
    inbound_call = crm_ingest.calls[0]
    assert inbound_call["object_name"] == "Комната, 18 м²"
    assert inbound_call["object_url"].startswith("https://youla.ru/ekaterinburg/")


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


def test_processor_youla_favorite_event_sends_warm_opener(db_session):
    settings = Settings(polling_enabled=False, telegram_leads_chat_id="-1001")
    youla = DummyYoula()
    processor, avito, telegram, openai = build_processor(settings, youla_client=youla)

    event = IncomingEvent(
        event_id="youla:fav-1",
        chat_id="youla:chat-fav-1",
        message_id="youla:msg-fav-1",
        sender_type="user",
        text="Покупатель добавил объявление в избранное.",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(ad_id="youla:prod-fav-1", title="Комната 16 м2", category="Недвижимость"),
        marketplace="youla",
        sender_id="buyer-1",
        recipient_id="seller-1",
        product_id="prod-fav-1",
    )

    outcome = processor._process_event(db_session, event)
    assert outcome == "replied"
    assert avito.sent_messages == []
    assert telegram.leads_sent == 0
    assert openai.reply_calls == 0
    assert len(youla.sent_messages) == 1
    assert "вопрос по заселению сейчас актуален" in youla.sent_messages[0]["text"].lower()


def test_processor_avito_favorite_event_sends_warm_opener(db_session):
    settings = Settings(polling_enabled=False, telegram_leads_chat_id="-1001")
    processor, avito, telegram, openai = build_processor(settings)

    event = IncomingEvent(
        event_id="avito:fav-1",
        chat_id="avito:chat-fav-1",
        message_id="avito:msg-fav-1",
        sender_type="user",
        text="Покупатель добавил объявление в избранное.",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(ad_id="avito:ad-fav-1", title="Комната 20 м2", category="Недвижимость"),
        marketplace="avito",
    )

    outcome = processor._process_event(db_session, event)
    assert outcome == "replied"
    assert len(avito.sent_messages) == 1
    assert telegram.leads_sent == 0
    assert openai.reply_calls == 0
    assert "вопрос по заселению сейчас актуален" in avito.sent_messages[0][1].lower()


def test_processor_replies_to_missed_call_notification(db_session):
    settings = Settings(polling_enabled=False, telegram_leads_chat_id="-1001")
    processor, avito, telegram, openai = build_processor(settings)

    event = IncomingEvent(
        event_id="evt-missed-call-1",
        chat_id="chat-missed-call",
        message_id="msg-missed-call-1",
        sender_type="user",
        text="Пропущенный звонок",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(ad_id="ad-missed-call", title="Койко-место", category="Недвижимость"),
        customer_name="Антон",
    )

    outcome = processor._process_event(db_session, event)
    assert outcome == "replied"
    assert telegram.leads_sent == 0
    assert openai.reply_calls == 0
    assert avito.sent_messages[-1][1] == (
        "Добрый день, простите, но пока мы не можем тут принимать входящие звонки, "
        "но если вы оставите свой номер телефона, то мы обязательно вам перезвоним."
    )


def test_processor_youla_service_offer_gets_supplier_question(db_session):
    settings = Settings(polling_enabled=False, telegram_leads_chat_id="-1001")
    youla = DummyYoula()
    processor, avito, telegram, openai = build_processor(settings, youla_client=youla)

    event = IncomingEvent(
        event_id="youla:svc-1",
        chat_id="youla:chat-svc-1",
        message_id="youla:msg-svc-1",
        sender_type="user",
        text="Здравствуйте, предлагаю услуги мастера на час. Сантехника, электрика.",
        created_at=datetime.now(timezone.utc),
        ad_context=AdContext(ad_id="youla:prod-svc-1", title="Комната 18 м2", category="Недвижимость"),
        marketplace="youla",
        sender_id="buyer-1",
        recipient_id="seller-1",
        product_id="prod-svc-1",
    )

    outcome = processor._process_event(db_session, event)
    assert outcome == "replied"
    assert avito.sent_messages == []
    assert telegram.leads_sent == 0
    assert openai.reply_calls == 0
    assert len(youla.sent_messages) == 1
    text = youla.sent_messages[0]["text"].lower()
    assert "расскажите подробнее" in text
    assert "оставить контактные данные" in text
    assert "вопрос по заселению" not in text


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


def test_processor_apologizes_for_long_wait_and_rechecks_actuality(db_session):
    settings = Settings(polling_enabled=False, telegram_leads_chat_id="-1001")
    processor, avito, telegram, openai = build_processor(settings)

    event = IncomingEvent(
        event_id="evt-delay-1",
        chat_id="chat-delay",
        message_id="msg-delay-1",
        sender_type="user",
        text="Подскажите, пожалуйста, когда можно заселиться?",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=40),
        ad_context=AdContext(ad_id="ad-delay", title="Койко-место 18 м2", category="Недвижимость"),
        customer_name="Кирилл",
    )

    outcome = processor._process_event(db_session, event)
    assert outcome == "replied"
    assert telegram.leads_sent == 0
    assert openai.reply_calls == 0

    reply = avito.sent_messages[-1][1].lower()
    assert "извините за долгое ожидание" in reply
    assert "вопрос по заселению еще актуален" in reply


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
