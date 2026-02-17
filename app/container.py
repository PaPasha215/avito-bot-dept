from __future__ import annotations

import logging

from app.core.config import Settings
from app.db import SessionLocal, init_db
from app.integrations.avito import AvitoClient
from app.integrations.openai_client import OpenAIClient
from app.integrations.telegram import TelegramClient
from app.services.classifier import DomainClassifier
from app.services.cleanup import RetentionService
from app.services.lead_detector import LeadDetector
from app.services.poller import Poller
from app.services.processor import MessageProcessor
from app.services.prompt_service import PromptService
from app.services.router import RouterService
from app.services.self_learning import SelfLearningService
from app.services.stats_reporting import StatsReportingService

logger = logging.getLogger(__name__)


class AppContainer:
    def __init__(self, settings: Settings):
        self.settings = settings

        self.avito_client = AvitoClient(
            client_id=settings.avito_client_id,
            client_secret=settings.avito_client_secret,
            user_id=settings.avito_user_id,
            token_url=settings.avito_token_url,
            updates_url=settings.avito_updates_url,
            send_message_url_template=settings.avito_send_message_url_template,
            chat_context_url_template=settings.avito_chat_context_url_template,
            messages_url_template=settings.avito_messages_url_template,
            messages_fallback_url_template=settings.avito_messages_fallback_url_template,
            timeout_seconds=settings.avito_request_timeout_seconds,
            poll_limit=settings.avito_poll_limit,
        )
        self.openai_client = OpenAIClient(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.openai_model,
            timeout_seconds=settings.openai_timeout_seconds,
        )
        self.telegram_client = TelegramClient(
            bot_token=settings.telegram_bot_token,
            api_base=settings.telegram_api_base,
        )

        self.prompt_service = PromptService(settings=settings)
        self.self_learning_service = SelfLearningService(settings=settings)
        self.stats_reporting_service = StatsReportingService(
            settings=settings,
            avito_client=self.avito_client,
            telegram_client=self.telegram_client,
        )
        classifier = DomainClassifier(openai_client=self.openai_client)
        router = RouterService(classifier=classifier, confidence_threshold=settings.classifier_confidence_threshold)

        self.processor = MessageProcessor(
            settings=settings,
            avito_client=self.avito_client,
            openai_client=self.openai_client,
            telegram_client=self.telegram_client,
            router_service=router,
            prompt_service=self.prompt_service,
            self_learning_service=self.self_learning_service,
            stats_reporting_service=self.stats_reporting_service,
            lead_detector=LeadDetector(),
            retention_service=RetentionService(),
        )

        self.poller = Poller(self.processor, interval_seconds=settings.poll_interval_seconds)

    def startup(self) -> None:
        init_db()

        with SessionLocal() as db:
            self.prompt_service.get_real_estate_prompt(db)
            db.commit()

        if self.settings.polling_enabled:
            logger.info("Polling enabled")
            self.poller.start()
        else:
            logger.info("Polling disabled")

    def shutdown(self) -> None:
        self.poller.stop()
        self.avito_client.close()
        self.openai_client.close()
        self.telegram_client.close()
