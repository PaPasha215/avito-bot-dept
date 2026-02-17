from __future__ import annotations

import logging

import httpx

from app.types import LeadStatus

logger = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self, bot_token: str | None, api_base: str = "https://api.telegram.org"):
        self.bot_token = bot_token
        self.api_base = api_base.rstrip("/")
        self._client = httpx.Client(timeout=20)

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token)

    def close(self) -> None:
        self._client.close()

    def _method_url(self, method: str) -> str:
        if not self.bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        return f"{self.api_base}/bot{self.bot_token}/{method}"

    def send_message(self, chat_id: str, text: str) -> None:
        if not self.enabled:
            logger.warning("Telegram disabled, message skipped")
            return
        resp = self._client.post(self._method_url("sendMessage"), json={"chat_id": chat_id, "text": text})
        if resp.is_error:
            detail = resp.text
            try:
                payload = resp.json()
                if isinstance(payload, dict):
                    detail = str(payload.get("description") or payload)
            except ValueError:
                pass
            raise httpx.HTTPStatusError(
                f"Telegram sendMessage failed ({resp.status_code}): {detail}",
                request=resp.request,
                response=resp,
            )

    def send_lead_card(
        self,
        leads_chat_id: str,
        lead_id: int,
        ad_title: str,
        ad_url: str | None,
        customer_name: str | None,
        contact_raw: str,
        summary: str,
        context_lines: list[str],
        status: LeadStatus = LeadStatus.NEW,
    ) -> None:
        summary_compact = self._trim_text(summary, limit=700)
        lines = [
            f"Лид Avito #{lead_id}",
            "",
            f"Объявление: {ad_title}",
        ]
        if ad_url:
            lines.append(f"Ссылка: {ad_url}")
        if customer_name:
            lines.append(f"Имя: {customer_name}")
        lines.extend(
            [
                f"Контакт: {contact_raw}",
                f"Статус: {status.value}",
                "",
                "Summary:",
                summary_compact,
                "",
                "Последние реплики:",
            ]
        )
        for item in context_lines[-3:]:
            lines.append(f"- {self._trim_text(item, limit=220)}")

        self.send_message(leads_chat_id, "\n".join(lines))

    @staticmethod
    def _trim_text(text: str, limit: int) -> str:
        compact = " ".join((text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1].rstrip() + "…"
