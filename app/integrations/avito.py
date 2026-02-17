from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

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
        messages_url_template: str | None,
        messages_fallback_url_template: str | None,
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
        self.messages_url_template = messages_url_template
        self.messages_fallback_url_template = messages_fallback_url_template
        self.timeout_seconds = timeout_seconds
        self.poll_limit = poll_limit

        self._client = httpx.Client(timeout=timeout_seconds)
        self._token: str | None = None
        self._token_expiry_ts: float = 0
        self._history_unavailable_chats: set[str] = set()

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
        updates_url = self._resolve_url_template(self.updates_url)

        params: dict[str, Any] = {"limit": self.poll_limit}
        if cursor:
            params["cursor"] = cursor

        resp = self._client.get(updates_url, headers=self._headers(), params=params)
        if resp.status_code == 401:
            self._token = None
            resp = self._client.get(updates_url, headers=self._headers(), params=params)
        resp.raise_for_status()

        data = resp.json()
        if isinstance(data, dict) and isinstance(data.get("chats"), list):
            normalized = self._normalize_chats_response(data.get("chats") or [])
            return normalized, cursor

        raw_events = data.get("events") or data.get("items") or []
        next_cursor = data.get("cursor") or data.get("next_cursor") or cursor

        normalized: list[IncomingEvent] = []
        for item in raw_events:
            evt = self._normalize_event(item)
            if evt is not None:
                normalized.append(evt)

        return normalized, next_cursor

    def _normalize_chats_response(self, chats: list[dict]) -> list[IncomingEvent]:
        normalized: list[IncomingEvent] = []
        for chat in chats:
            burst_events = self._normalize_inbound_burst(chat)
            if burst_events:
                normalized.extend(burst_events)
                continue
            evt = self._normalize_chat_item(chat)
            if evt is not None:
                normalized.append(evt)
        return normalized

    def _normalize_inbound_burst(self, chat: dict) -> list[IncomingEvent] | None:
        if not self.messages_url_template:
            return None

        chat_id_raw = chat.get("id")
        if not chat_id_raw:
            return None
        chat_id = str(chat_id_raw)
        if chat_id in self._history_unavailable_chats:
            return None

        last_message = chat.get("last_message") if isinstance(chat.get("last_message"), dict) else {}
        direction = str(last_message.get("direction") or "").lower()
        if direction != "in":
            return None

        try:
            payload = self._fetch_chat_messages(chat_id=chat_id, limit=10)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {402, 405}:
                # Account-level/API-plan limitation for this chat; avoid repeated noisy calls.
                self._history_unavailable_chats.add(chat_id)
            logger.warning("Failed to fetch message burst for chat %s: %s", chat_id, exc)
            return None

        messages = payload.get("messages") if isinstance(payload, dict) else None
        if not isinstance(messages, list) or not messages:
            return None

        burst: list[IncomingEvent] = []
        # API returns newest-first. We take contiguous inbound messages from the end-user.
        for message in messages:
            if not isinstance(message, dict):
                continue
            msg_direction = str(message.get("direction") or "").lower()
            if msg_direction != "in":
                if burst:
                    break
                continue
            evt = self._normalize_chat_history_message(chat=chat, message=message)
            if evt is not None:
                burst.append(evt)

        if not burst:
            return None

        # Processor expects natural order for chat history accumulation.
        burst.reverse()
        return burst

    def _normalize_chat_history_message(self, chat: dict, message: dict) -> IncomingEvent | None:
        chat_id_raw = chat.get("id")
        if not chat_id_raw:
            return None
        chat_id = str(chat_id_raw)

        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        text = str(content.get("text") or "").strip()
        if not text:
            return None

        msg_type = str(message.get("type") or "").lower()
        if msg_type == "system":
            return None

        message_id = str(message.get("id") or "")
        if not message_id:
            message_id = hashlib.sha256(f"{chat_id}:{text}".encode("utf-8")).hexdigest()[:32]

        created_at = self._parse_datetime(message.get("created") or chat.get("updated") or chat.get("created"))
        event_id = f"{chat_id}:{message_id}"

        context = chat.get("context") if isinstance(chat.get("context"), dict) else {}
        context_value = context.get("value") if isinstance(context.get("value"), dict) else {}
        ad_id = str(context_value.get("id") or "unknown")
        ad_title = str(context_value.get("title") or "Объявление")
        ad_category = context_value.get("category") or context_value.get("category_name")
        ad_url = context_value.get("url")

        customer_name: str | None = None
        users = chat.get("users") if isinstance(chat.get("users"), list) else []
        owner_id = int(self.user_id) if (self.user_id and self.user_id.isdigit()) else None
        for user in users:
            if not isinstance(user, dict):
                continue
            uid = user.get("id")
            if owner_id is not None and uid == owner_id:
                continue
            name = user.get("name")
            if name:
                customer_name = str(name)
                break

        return IncomingEvent(
            event_id=event_id,
            chat_id=chat_id,
            message_id=message_id,
            sender_type="user",
            text=text,
            created_at=created_at,
            ad_context=AdContext(
                ad_id=ad_id,
                title=ad_title,
                category=str(ad_category) if ad_category is not None else None,
                url=str(ad_url) if ad_url is not None else None,
            ),
            customer_name=customer_name,
        )

    def _fetch_chat_messages(self, chat_id: str, limit: int = 10) -> dict[str, Any]:
        if not self.messages_url_template:
            return {}
        templates = [self.messages_url_template]
        if self.messages_fallback_url_template:
            templates.append(self.messages_fallback_url_template)

        last_exc: Exception | None = None
        seen_urls: set[str] = set()
        for template in templates:
            url = self._resolve_url_template(template, chat_id=chat_id)
            if url in seen_urls:
                continue
            seen_urls.add(url)

            try:
                resp = self._client.get(url, headers=self._headers(), params={"limit": limit})
                if resp.status_code == 401:
                    self._token = None
                    resp = self._client.get(url, headers=self._headers(), params={"limit": limit})

                if resp.is_error:
                    detail = resp.text[:256]
                    logger.info(
                        "Chat history endpoint returned %s for chat %s on %s: %s",
                        resp.status_code,
                        chat_id,
                        url,
                        detail,
                    )
                    resp.raise_for_status()

                data = resp.json()
                if isinstance(data, dict):
                    return data
                return {}
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                # Try next endpoint template only for endpoint/access shape issues.
                if exc.response.status_code in {402, 404, 405}:
                    continue
                raise

        if last_exc:
            raise last_exc
        return {}

    def _normalize_chat_item(self, chat: dict) -> IncomingEvent | None:
        chat_id_raw = chat.get("id")
        if not chat_id_raw:
            return None
        chat_id = str(chat_id_raw)

        context = chat.get("context") if isinstance(chat.get("context"), dict) else {}
        context_value = context.get("value") if isinstance(context.get("value"), dict) else {}
        last_message = chat.get("last_message") if isinstance(chat.get("last_message"), dict) else {}

        content = last_message.get("content") if isinstance(last_message.get("content"), dict) else {}
        text = str(content.get("text") or "").strip()
        if not text:
            return None

        # Avito inserts system messages for accounts without messenger API subscription.
        msg_type = str(last_message.get("type") or "").lower()
        if msg_type == "system":
            return None

        direction = str(last_message.get("direction") or "").lower()
        sender_type = "user" if direction == "in" else "owner"

        message_id = str(last_message.get("id") or "")
        if not message_id:
            message_id = hashlib.sha256(f"{chat_id}:{text}".encode("utf-8")).hexdigest()[:32]
        event_id = f"{chat_id}:{message_id}"

        created_at = self._parse_datetime(last_message.get("created") or chat.get("updated") or chat.get("created"))

        ad_id = str(context_value.get("id") or "unknown")
        ad_title = str(context_value.get("title") or "Объявление")
        ad_category = context_value.get("category") or context_value.get("category_name")
        ad_url = context_value.get("url")

        customer_name: str | None = None
        users = chat.get("users") if isinstance(chat.get("users"), list) else []
        owner_id = int(self.user_id) if (self.user_id and self.user_id.isdigit()) else None
        for user in users:
            if not isinstance(user, dict):
                continue
            uid = user.get("id")
            if owner_id is not None and uid == owner_id:
                continue
            name = user.get("name")
            if name:
                customer_name = str(name)
                break

        return IncomingEvent(
            event_id=event_id,
            chat_id=chat_id,
            message_id=message_id,
            sender_type=sender_type,
            text=text,
            created_at=created_at,
            ad_context=AdContext(
                ad_id=ad_id,
                title=ad_title,
                category=str(ad_category) if ad_category is not None else None,
                url=str(ad_url) if ad_url is not None else None,
            ),
            customer_name=customer_name,
        )

    def get_chat_context(self, chat_id: str) -> dict:
        if not self.chat_context_url_template:
            return {}
        url = self._resolve_url_template(self.chat_context_url_template, chat_id=chat_id)
        resp = self._client.get(url, headers=self._headers())
        if resp.status_code == 401:
            self._token = None
            resp = self._client.get(url, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def send_message(self, chat_id: str, text: str) -> None:
        if not self.send_message_url_template:
            raise RuntimeError("AVITO_SEND_MESSAGE_URL_TEMPLATE is not configured")
        url = self._resolve_url_template(self.send_message_url_template, chat_id=chat_id)
        payload = {"type": "text", "message": {"text": text}}
        resp = self._client.post(url, headers=self._headers(), json=payload)
        if resp.status_code == 401:
            self._token = None
            resp = self._client.post(url, headers=self._headers(), json=payload)
        resp.raise_for_status()

    def get_item_analytics(
        self,
        date_from: str,
        date_to: str,
        metrics: list[str],
        grouping: str = "item",
        limit: int = 50,
        offset: int = 0,
        sort: dict[str, str] | None = None,
        category_ids: list[int] | None = None,
    ) -> dict:
        if not self.user_id:
            raise RuntimeError("AVITO_USER_ID is not configured")
        payload: dict[str, Any] = {
            "dateFrom": date_from,
            "dateTo": date_to,
            "metrics": metrics,
            "grouping": grouping,
            "limit": max(1, min(limit, 1000)),
            "offset": max(0, offset),
        }
        if sort:
            payload["sort"] = sort
        if category_ids:
            payload["filter"] = {"categoryIDs": category_ids}

        url = f"https://api.avito.ru/stats/v2/accounts/{self.user_id}/items"
        resp = self._client.post(url, headers=self._headers(), json=payload)
        if resp.status_code == 401:
            self._token = None
            resp = self._client.post(url, headers=self._headers(), json=payload)
        resp.raise_for_status()
        return resp.json()

    def get_account_item(self, item_id: int | str) -> dict:
        if not self.user_id:
            raise RuntimeError("AVITO_USER_ID is not configured")
        url = f"https://api.avito.ru/core/v1/accounts/{self.user_id}/items/{item_id}/"
        resp = self._client.get(url, headers=self._headers())
        if resp.status_code == 401:
            self._token = None
            resp = self._client.get(url, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def _resolve_url_template(self, template: str, chat_id: str | None = None) -> str:
        user_id = self.user_id or ""
        resolved = template.replace("USER_ID", user_id)
        values = {
            "user_id": user_id,
            "chat_id": chat_id or "",
        }
        try:
            return resolved.format(**values)
        except KeyError:
            return resolved

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
