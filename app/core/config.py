from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_REAL_ESTATE_PROMPT = """# 🧠 ЕДИНЫЙ ПРОМПТ ДЛЯ CHATGPT В AVITO
## Сеть домашних гостиниц и хостелов, Екатеринбург

Ты — виртуальный ассистент сети домашних гостиниц и хостелов в Екатеринбурге.
Работаешь в чате Avito 24/7.

Цель:
1. Подобрать подходящий вариант проживания
2. Предложить альтернативы
3. Собрать контакт (телефон/мессенджер)
4. Передать клиента менеджеру Сергею

Ключевые правила:
- Всегда различай хостел и квартиру-койко-место
- Сначала отвечай в контексте текущего объявления, не уходи в другие форматы
- Для трех хостелов доступно посуточное размещение:
  - Куйбышева, 30 («Сити Е»)
  - Мамина-Сибиряка, 132 («В гостях у бабуси»)
  - Ботаническая, 30 («Уютное местечко»)
- По остальным адресам предлагай размещение на месяц
- Не обещай бронь
- Не давай адрес/контакты без условия
- Не запрашивай контакт повторно, если уже есть
- Если сомневаешься: "Уточню у менеджера", но сначала собери недостающие данные и контакт
- Не задавай больше одного вопроса в одном сообщении
- Если клиент спрашивает, сдается ли комната или есть ли места, сначала прямо ответь, что объявление актуально, и только потом задай следующий вопрос
- Если клиент спрашивает про цену, срок, иностранцев или формат размещения, отвечай по сути вопроса, а не шаблонной фразой
- Никогда не пиши "в ближайшее время мы свяжемся" вместо содержательного ответа
- Если сообщение не про проживание (услуги, юридический адрес, офис, место для работы), вежливо ответь по сути и не уводи диалог в заселение
- Если клиент прямо пишет по заселению (наличие/даты/срок), продолжай квалификацию без паузы
- Если клиент уже явно описал запрос на заселение (наличие, даты, формат, бюджет), не спрашивай заново про актуальность, продолжай квалификацию
- Если клиент пишет короткий бюджет (например 8-12, 8р-12р, 812), уточни, что это тысячи в месяц
- Если спрашивают цену, давай только ориентир (от/примерно) и добавляй, что точную цену подтвердит менеджер
- Цель каждого релевантного диалога: получить контакт клиента

Стиль:
- Вежливо, спокойно, коротко
- 1-4 предложения
- Русский язык
- До 1 эмодзи
- Дружелюбный тон
"""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

    app_name: str = "Avito AI Assistant"
    environment: str = "dev"
    log_level: str = "INFO"

    database_url: str = "sqlite:///./data/app.db"
    polling_enabled: bool = True
    poll_interval_seconds: int = 10
    retention_days: int = 90

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4.1-mini"
    openai_timeout_seconds: int = 30
    classifier_confidence_threshold: float = 0.80
    max_recent_messages_for_classification: int = 6
    max_recent_messages_for_reply: int = 10
    reply_delay_seconds: int = 0
    self_learning_enabled: bool = True
    self_learning_lookback_days: int = 7
    self_learning_interval_hours: int = 24
    self_learning_max_source_chats: int = 300
    self_learning_max_examples_per_run: int = 500
    self_learning_examples_per_prompt: int = 3
    self_learning_canary_percent: int = 20
    self_learning_rollback_wrong_domain_threshold: int = 1
    self_learning_promote_min_leads: int = 3
    stats_reporting_enabled: bool = False
    stats_report_interval_hours: int = 24
    stats_report_lookback_days: int = 14
    stats_report_limit: int = 60
    stats_report_item_detail_limit: int = 20
    stats_report_min_views_for_conversion: int = 30
    stats_report_low_conversion_threshold: float = 3.5
    stats_report_weekday: int = 1
    stats_report_hour: int = 9
    stats_report_minute: int = 0
    stats_report_timezone: str = "Asia/Yekaterinburg"
    bot_health_enabled: bool = True
    bot_health_interval_minutes: int = 30
    bot_health_alert_repeat_minutes: int = 60
    bot_health_send_ok_messages: bool = False
    bot_health_chat_id: str | None = None
    bot_health_youla_silence_minutes: int = 180
    bot_health_unanswered_minutes: int = 30
    bot_health_pending_favorite_minutes: int = 30
    bot_health_check_chat_limit: int = 120
    bot_health_daily_report_enabled: bool = True
    bot_health_daily_report_hour: int = 10
    bot_health_daily_report_minute: int = 0
    bot_health_daily_report_timezone: str = "Asia/Yekaterinburg"

    avito_client_id: str | None = None
    avito_client_secret: str | None = None
    avito_user_id: str | None = None
    avito_token_url: str = "https://api.avito.ru/token"
    avito_updates_url: str | None = None
    avito_chat_context_url_template: str | None = None
    avito_send_message_url_template: str | None = None
    avito_messages_url_template: str | None = "https://api.avito.ru/messenger/v3/accounts/{user_id}/chats/{chat_id}/messages/"
    avito_messages_fallback_url_template: str | None = "https://api.avito.ru/messenger/v1/accounts/{user_id}/chats/{chat_id}/messages/"
    avito_poll_limit: int = 50
    avito_request_timeout_seconds: int = 20

    youla_enabled: bool = False
    youla_mode: str = "feed_only"  # feed_only | chat_api
    youla_api_base: str | None = "https://partner-api.youla.ru"
    youla_api_token: str | None = None
    youla_account_id: str | None = None
    youla_request_timeout_seconds: int = 20
    youla_webhook_secret: str | None = None
    youla_force_real_estate: bool = True
    youla_chat_url_template: str = "https://youla.ru/web-chat/{chat_id}"
    youla_housekeeping_max_sends_per_run: int = 10

    telegram_bot_token: str | None = None
    telegram_api_base: str = "https://api.telegram.org"
    telegram_leads_chat_id: str | None = None
    telegram_qa_chat_id: str | None = None
    telegram_stats_chat_id: str | None = None
    telegram_webhook_secret: str | None = None

    crm_ingest_enabled: bool = False
    crm_ingest_url: str | None = None
    crm_ingest_token: str | None = None
    crm_ingest_timeout_seconds: int = 8
    crm_ingest_notify_telegram_enabled: bool = False
    crm_ingest_notify_chat_id: str | None = None

    real_estate_prompt_version: str = "REAL_ESTATE_PROMPT_V1_4"
    real_estate_prompt_default: str = DEFAULT_REAL_ESTATE_PROMPT

    dashboard_owner_username: str | None = None
    dashboard_owner_password: str | None = None
    dashboard_manager_username: str | None = None
    dashboard_manager_password: str | None = None
    dashboard_session_secret: str = "change-this-dashboard-secret"
    dashboard_session_ttl_hours: int = 24

    def dashboard_uses_default_session_secret(self) -> bool:
        return self.dashboard_session_secret.strip() == "change-this-dashboard-secret"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
