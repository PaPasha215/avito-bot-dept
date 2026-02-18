from __future__ import annotations

import argparse
import json
from datetime import datetime

from app.container import AppContainer
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db import SessionLocal
from app.repositories import Repository


def cmd_validate_env() -> int:
    settings = get_settings()

    required = {
        "OPENAI_API_KEY": settings.openai_api_key,
        "AVITO_CLIENT_ID": settings.avito_client_id,
        "AVITO_CLIENT_SECRET": settings.avito_client_secret,
        "AVITO_USER_ID": settings.avito_user_id,
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


def cmd_validate_youla_readiness() -> int:
    settings = get_settings()

    if not settings.youla_enabled:
        print("YOULA is disabled (YOULA_ENABLED=false).")
        print("Set YOULA_ENABLED=true when Youla access is ready.")
        return 0

    required = {
        "YOULA_MODE": settings.youla_mode,
        "YOULA_API_BASE": settings.youla_api_base,
        "YOULA_ACCOUNT_ID": settings.youla_account_id,
        "YOULA_API_TOKEN": settings.youla_api_token,
    }
    if (settings.youla_mode or "").strip().lower() == "chat_api":
        required["YOULA_UPDATES_URL"] = settings.youla_updates_url
        required["YOULA_SEND_MESSAGE_URL_TEMPLATE"] = settings.youla_send_message_url_template

    missing = [k for k, v in required.items() if not v]
    if missing:
        print("Youla readiness is NOT complete. Missing required env vars:")
        for key in missing:
            print(f"- {key}")
        return 2

    print("Youla readiness env looks good.")
    print(f"Mode: {settings.youla_mode}")
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


def _to_jsonable(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def cmd_chat_diagnostics(external_chat_id: str, limit: int) -> int:
    base_settings = get_settings()
    settings = base_settings.model_copy(update={"polling_enabled": False})
    configure_logging(settings.log_level)

    container = AppContainer(settings=settings)
    try:
        container.startup()
        with SessionLocal() as db:
            repo = Repository(db)
            data = repo.get_chat_diagnostics(external_chat_id=external_chat_id, message_limit=limit, event_limit=limit)

        if data is None:
            print(json.dumps({"error": "chat_not_found", "external_chat_id": external_chat_id}, ensure_ascii=False))
            return 1

        chat = data["chat"]
        ad = data["ad"]
        payload = {
            "chat_id": chat.id,
            "external_chat_id": chat.external_chat_id,
            "ad": {
                "external_ad_id": ad.external_ad_id,
                "title": ad.title,
                "category": ad.category,
                "raw_category": ad.raw_category,
                "url": ad.url,
            },
            "state": chat.state,
            "domain": chat.domain,
            "customer_name": chat.customer_name,
            "inbound_count": data["inbound_count"],
            "outbound_count": data["outbound_count"],
            "messages": [
                {
                    "id": m.id,
                    "direction": m.direction,
                    "external_message_id": m.external_message_id,
                    "text": m.text,
                    "created_at": m.created_at,
                }
                for m in data["messages"]
            ],
            "routing_decisions": [
                {
                    "id": item.id,
                    "domain": item.domain,
                    "confidence": item.confidence,
                    "decision": item.decision,
                    "reason": item.reason,
                    "created_at": item.created_at,
                }
                for item in data["routing_decisions"]
            ],
            "bot_replies": [
                {
                    "id": item.id,
                    "prompt_version": item.prompt_version,
                    "status": item.status,
                    "error": item.error,
                    "sent_at": item.sent_at,
                    "text": item.text,
                }
                for item in data["bot_replies"]
            ],
            "leads": [
                {
                    "id": item.id,
                    "contact_raw": item.contact_raw,
                    "contact_normalized": item.contact_normalized,
                    "summary": item.summary,
                    "status": item.status,
                    "sent_to_tg_at": item.sent_to_tg_at,
                    "created_at": item.created_at,
                }
                for item in data["leads"]
            ],
            "feedback_events": [
                {
                    "id": item.id,
                    "tag": item.tag,
                    "comment": item.comment,
                    "created_at": item.created_at,
                }
                for item in data["feedback_events"]
            ],
            "event_logs": [
                {
                    "id": item.id,
                    "source": item.source,
                    "event_type": item.event_type,
                    "idempotency_key": item.idempotency_key,
                    "status": item.status,
                    "error_message": item.error_message,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
                for item in data["event_logs"]
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, default=_to_jsonable))
        return 0
    finally:
        container.shutdown()


def cmd_learn_once() -> int:
    base_settings = get_settings()
    settings = base_settings.model_copy(update={"polling_enabled": False})
    configure_logging(settings.log_level)

    container = AppContainer(settings=settings)
    try:
        container.startup()
        with SessionLocal() as db:
            result = container.self_learning_service.learn_once(db)
        print(
            json.dumps(
                {
                    "run_at": result.run_at,
                    "since": result.since,
                    "source_chats": result.source_chats,
                    "examples_created": result.examples_created,
                    "new_version": result.new_version,
                    "active_version": result.active_version,
                    "stable_version": result.stable_version,
                    "action": result.action,
                    "quality_usage_count": result.quality_usage_count,
                    "quality_chat_count": result.quality_chat_count,
                    "quality_leads_count": result.quality_leads_count,
                    "quality_feedback_count": result.quality_feedback_count,
                    "quality_wrong_domain_count": result.quality_wrong_domain_count,
                },
                ensure_ascii=False,
                default=_to_jsonable,
            )
        )
        return 0
    finally:
        container.shutdown()


def cmd_learning_status() -> int:
    base_settings = get_settings()
    settings = base_settings.model_copy(update={"polling_enabled": False})
    configure_logging(settings.log_level)

    container = AppContainer(settings=settings)
    try:
        container.startup()
        with SessionLocal() as db:
            status = container.self_learning_service.get_status(db)
        print(
            json.dumps(
                {
                    "enabled": status.enabled,
                    "active_version": status.active_version,
                    "stable_version": status.stable_version,
                    "candidate_started_at": status.candidate_started_at,
                    "last_run_at": status.last_run_at,
                    "active_examples": status.active_examples,
                    "stable_examples": status.stable_examples,
                    "total_examples": status.total_examples,
                },
                ensure_ascii=False,
                default=_to_jsonable,
            )
        )
        return 0
    finally:
        container.shutdown()


def cmd_stats_report_once() -> int:
    base_settings = get_settings()
    settings = base_settings.model_copy(update={"polling_enabled": False})
    configure_logging(settings.log_level)

    container = AppContainer(settings=settings)
    try:
        container.startup()
        with SessionLocal() as db:
            result = container.stats_reporting_service.report_once(db)
            db.commit()
        print(
            json.dumps(
                {
                    "report_date": result.report_date,
                    "sent": result.sent,
                    "target_chat_id": result.target_chat_id,
                    "items_total": result.items_total,
                    "top_reach_count": result.top_reach_count,
                    "top_conversion_count": result.top_conversion_count,
                    "low_conversion_count": result.low_conversion_count,
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
    sub.add_parser("validate-youla-readiness", help="Validate Youla integration environment readiness")
    sub.add_parser("poll-once", help="Run one Avito polling cycle")
    sub.add_parser("learn-once", help="Run one self-learning cycle")
    sub.add_parser("learning-status", help="Print self-learning status")
    sub.add_parser("stats-report-once", help="Send one analytics report to Telegram")
    chat_diag = sub.add_parser("chat-diagnostics", help="Print full diagnostics for one chat")
    chat_diag.add_argument("--external-chat-id", required=True)
    chat_diag.add_argument("--limit", type=int, default=200)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "validate-env":
        return cmd_validate_env()
    if args.command == "validate-youla-readiness":
        return cmd_validate_youla_readiness()
    if args.command == "poll-once":
        return cmd_poll_once()
    if args.command == "learn-once":
        return cmd_learn_once()
    if args.command == "learning-status":
        return cmd_learning_status()
    if args.command == "stats-report-once":
        return cmd_stats_report_once()
    if args.command == "chat-diagnostics":
        return cmd_chat_diagnostics(external_chat_id=args.external_chat_id, limit=args.limit)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
