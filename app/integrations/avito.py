from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import httpx

from app.types import AdContext, IncomingEvent

logger = logging.getLogger(__name__)


class AvitoClient:
    def __init__(
        self,
        client_id: str | None,
        client_secret: str | None,
        user_id: str | None,
        token_url: str,
        updates_url: str | None,
        send_message_url_template: str | None,
        chat_context_url_template: str | None,
        timeout_seconds: int = 20,
        poll_limit: int = 50,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.user_id = user_id
        self.token_url = token_url
        self.updates_url = updates_url
        self.send_message_url_template = send_message_url_template
        self.chat_context_url_template = chat_context_url_template
        self.timeout_seconds = timeout_seconds
        self.poll_limit = poll_limit

        self._client = httpx.Client(timeout=timeout_seconds)
        self._token: str | None = None
        self._token_expiry_ts: float = 0

    def close(self) -> None:
        self._client.close()

    @property
    def enabled(self) -> bool:
        return bool(self.updates_url and self.send_message_url_template)

    def _get_token(self) -> str:
        if self._token:
            return self._token

        if not (self.client_id and self.client_secret):
            raise RuntimeError("Avito credentials are not configured")

        resp = self._client.post(
            self.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        payload = resp.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("Avito token response has no access_token")
        self._token = token
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    def fetch_updates(self, cursor: str | None) -> tuple[list[IncomingEvent], str | None]:
        if not self.updates_url:
            return [], cursor

        params = {"limit": self.poll_limit}
        if cursor:
            params["cursor"] = cursor

        resp = self._client.get(self.updates_url, headers=self._headers(), params=params)
        if resp.status_code == 401:
            self._token = None
            resp = self._client.get(self.updates_url, headers=self._headers(), params=params)
        resp.raise_for_status()

        data = resp.json()
        raw_events = data.get("events") or data.get("items") or []
        next_cursor = data.get("cursor") or data.get("next_cursor") or cursor

        normalized: list[IncomingEvent] = []
        for item in raw_events:
            evt = self._normalize_event(item)
            if evt is not None:
                normalized.append(evt)

        return normalized, next_cursor

    def get_chat_context(self, chat_id: str) -> dict:
        if not self.chat_context_url_template:
            return {}
        url = self.chat_context_url_template.format(chat_id=chat_id, user_id=self.user_id or "")
        resp = self._client.get(url, headers=self._headers())
        if resp.status_code == 401:
            self._token = None
            resp = self._client.get(url, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def send_message(self, chat_id: str, text: str) -> None:
        if not self.send_message_url_template:
            raise RuntimeError("AVITO_SEND_MESSAGE_URL_TEMPLATE is not configured")
        url = self.send_message_url_template.format(chat_id=chat_id, user_id=self.user_id or "")
        resp = self._client.post(url, headers=self._headers(), json={"message": text, "text": text})
        if resp.status_code == 401:
            self._token = None
            resp = self._client.post(url, headers=self._headers(), json={"message": text, "text": text})
        resp.raise_for_status()

    def _normalize_event(self, item: dict) -> IncomingEvent | None:
        message_block = item.get("message") if isinstance(item.get("message"), dict) else {}
        ad_block = item.get("ad") or item.get("item") or item.get("context", {}).get("ad") or {}
        chat_block = item.get("chat") or {}
        user_block = item.get("user") or item.get("sender") or {}

        text = (
            message_block.get("text")
            or item.get("text")
            or item.get("message_text")
            or ""
        )
        if not text:
            return None

        chat_id = (
            str(item.get("chat_id") or chat_block.get("id") or message_block.get("chat_id") or "")
        )
        if not chat_id:
            return None

        message_id = str(message_block.get("id") or item.get("message_id") or "")
        if not message_id:
            message_id = hashlib.sha256(f"{chat_id}:{text}".encode("utf-8")).hexdigest()[:32]

        event_id = str(item.get("event_id") or item.get("id") or message_id)
        sender_type = str(
            item.get("sender_type")
            or message_block.get("sender_type")
            or user_block.get("type")
            or "user"
        ).lower()

        created_raw = item.get("created_at") or message_block.get("created_at") or item.get("timestamp")
        created_at = self._parse_datetime(created_raw)

        ad_id = str(ad_block.get("id") or item.get("ad_id") or chat_block.get("ad_id") or "unknown")
        ad_title = str(ad_block.get("title") or item.get("ad_title") or "Объявление")
        ad_category = ad_block.get("category") or ad_block.get("category_name") or item.get("ad_category")
        ad_url = ad_block.get("url") or item.get("ad_url")

        customer_name = user_block.get("name") or item.get("customer_name")

        return IncomingEvent(
            event_id=event_id,
            chat_id=chat_id,
            message_id=message_id,
            sender_type=sender_type,
            text=text.strip(),
            created_at=created_at,
            ad_context=AdContext(
                ad_id=ad_id,
                title=ad_title,
                category=str(ad_category) if ad_category is not None else None,
                url=str(ad_url) if ad_url is not None else None,
            ),
            customer_name=str(customer_name) if customer_name else None,
        )

    @staticmethod
    def _parse_datetime(value: object) -> datetime:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        if isinstance(value, str) and value:
            try:
                normalized = value.replace("Z", "+00:00")
                return datetime.fromisoformat(normalized)
            except ValueError:
                pass
        return datetime.now(timezone.utc)
