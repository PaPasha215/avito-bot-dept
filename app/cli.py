from __future__ import annotations

import argparse
import json

from app.container import AppContainer
from app.core.config import get_settings
from app.core.logging import configure_logging


def cmd_validate_env() -> int:
    settings = get_settings()

    required = {
        "OPENAI_API_KEY": settings.openai_api_key,
        "AVITO_CLIENT_ID": settings.avito_client_id,
        "AVITO_CLIENT_SECRET": settings.avito_client_secret,
        "AVITO_UPDATES_URL": settings.avito_updates_url,
        "AVITO_SEND_MESSAGE_URL_TEMPLATE": settings.avito_send_message_url_template,
        "TELEGRAM_BOT_TOKEN": settings.telegram_bot_token,
        "TELEGRAM_LEADS_CHAT_ID": settings.telegram_leads_chat_id,
    }

    missing = [k for k, v in required.items() if not v]
    if missing:
        print("Missing required env vars:")
        for key in missing:
            print(f"- {key}")
        return 2

    print("Environment config looks good.")
    return 0


def cmd_poll_once() -> int:
    base_settings = get_settings()
    settings = base_settings.model_copy(update={"polling_enabled": False})
    configure_logging(settings.log_level)

    container = AppContainer(settings=settings)
    try:
        container.startup()
        stats = container.processor.process_updates_once()
        print(
            json.dumps(
                {
                    "polled_events": stats.polled_events,
                    "processed_events": stats.processed_events,
                    "ignored_events": stats.ignored_events,
                    "replied_events": stats.replied_events,
                    "leads_created": stats.leads_created,
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        container.shutdown()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Avito AI Assistant CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate-env", help="Validate required environment variables")
    sub.add_parser("poll-once", help="Run one Avito polling cycle")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "validate-env":
        return cmd_validate_env()
    if args.command == "poll-once":
        return cmd_poll_once()

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
