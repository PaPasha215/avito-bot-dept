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
- Не обещай бронь
- Не давай адрес/контакты без условия
- Не запрашивай контакт повторно, если уже есть
- Если сомневаешься: "Уточню у менеджера"
- Не задавай больше одного вопроса в одном сообщении
- Первый ответ в новом чате: приветствие + вопрос, актуален ли вопрос заселения
- Если клиент пишет короткий бюджет (например 8-12, 8р-12р, 812), уточни, что это тысячи в месяц

Стиль:
- Вежливо, спокойно, коротко
- 1-4 предложения
- Русский язык
- До 1 эмодзи
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

    telegram_bot_token: str | None = None
    telegram_api_base: str = "https://api.telegram.org"
    telegram_leads_chat_id: str | None = None
    telegram_qa_chat_id: str | None = None
    telegram_stats_chat_id: str | None = None
    telegram_webhook_secret: str | None = None

    real_estate_prompt_version: str = "REAL_ESTATE_PROMPT_V1_2"
    real_estate_prompt_default: str = DEFAULT_REAL_ESTATE_PROMPT


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
