from __future__ import annotations

from dataclasses import dataclass
import logging

import httpx

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CrmIngestResult:
    created: bool
    lead_id: int | None
    raw: dict


@dataclass(slots=True)
class CrmTelegramActionResult:
    ok: bool
    status: str
    status_label: str
    manager_name: str
    assigned: bool
    raw: dict


class CrmIngestClient:
    def __init__(
        self,
        enabled: bool,
        ingest_url: str | None,
        ingest_token: str | None,
        timeout_seconds: int = 8,
    ):
        self._enabled_flag = bool(enabled)
        self.ingest_url = (ingest_url or "").strip()
        self.ingest_token = (ingest_token or "").strip()
        self._client = httpx.Client(timeout=timeout_seconds)

    @property
    def enabled(self) -> bool:
        return bool(self._enabled_flag and self.ingest_url and self.ingest_token)

    def close(self) -> None:
        self._client.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-ingest-token": self.ingest_token,
        }

    def ingest(
        self,
        *,
        source: str,
        external_chat_id: str,
        external_lead_id: str | None = None,
        event_id: str | None = None,
        client_name: str | None = None,
        phone: str | None = None,
        object_name: str | None = None,
        object_url: str | None = None,
        status: str | None = None,
        note: str | None = None,
        message_body: str | None = None,
        message_direction: str = "IN",
        message_channel: str | None = None,
    ) -> CrmIngestResult | None:
        if not self.enabled:
            return None

        payload: dict[str, str] = {
            "source": (source or "").strip().lower(),
            "external_chat_id": (external_chat_id or "").strip(),
        }
        if external_lead_id:
            payload["external_lead_id"] = str(external_lead_id).strip()
        if event_id:
            payload["event_id"] = str(event_id).strip()
        if client_name:
            payload["client_name"] = str(client_name).strip()
        if phone:
            payload["phone"] = str(phone).strip()
        if object_name:
            payload["object_name"] = str(object_name).strip()
        if object_url:
            payload["object_url"] = str(object_url).strip()
        if status:
            payload["status"] = str(status).strip().upper()
        if note:
            payload["note"] = str(note).strip()
        if message_body:
            payload["message_body"] = str(message_body).strip()
            payload["message_direction"] = str(message_direction or "IN").strip().upper()
            payload["message_channel"] = str(message_channel or source).strip().lower()

        response = self._client.post(self.ingest_url, headers=self._headers(), json=payload)
        response.raise_for_status()

        data = response.json() if response.text else {}
        lead_raw = data.get("lead") if isinstance(data, dict) else None
        lead_id = None
        if isinstance(lead_raw, dict):
            raw_id = lead_raw.get("id")
            if isinstance(raw_id, int):
                lead_id = raw_id
            elif isinstance(raw_id, str) and raw_id.isdigit():
                lead_id = int(raw_id)
        created = bool(data.get("created")) if isinstance(data, dict) else False
        return CrmIngestResult(created=created, lead_id=lead_id, raw=data if isinstance(data, dict) else {})

    def telegram_action(
        self,
        *,
        lead_id: int,
        action: str,
        actor_tg_id: str,
        actor_username: str | None = None,
        actor_name: str | None = None,
        message_chat_id: str | None = None,
        message_id: str | None = None,
    ) -> CrmTelegramActionResult | None:
        if not self.enabled:
            return None

        base_url = self.ingest_url.rsplit("/", 1)[0]
        action_url = f"{base_url}/telegram-action"
        payload: dict[str, str | int] = {
            "lead_id": int(lead_id),
            "action": str(action or "").strip().lower(),
            "actor_tg_id": str(actor_tg_id or "").strip(),
        }
        if actor_username:
            payload["actor_username"] = str(actor_username).strip()
        if actor_name:
            payload["actor_name"] = str(actor_name).strip()
        if message_chat_id:
            payload["message_chat_id"] = str(message_chat_id).strip()
        if message_id:
            payload["message_id"] = str(message_id).strip()

        response = self._client.post(action_url, headers=self._headers(), json=payload)
        data = response.json() if response.text else {}
        if response.status_code == 409:
            return CrmTelegramActionResult(
                ok=False,
                status=str(data.get("status") or ""),
                status_label=str(data.get("error") or ""),
                manager_name=str(data.get("manager_name") or ""),
                assigned=False,
                raw=data if isinstance(data, dict) else {},
            )
        response.raise_for_status()
        lead = data.get("lead") if isinstance(data, dict) else {}
        return CrmTelegramActionResult(
            ok=bool(data.get("ok")) if isinstance(data, dict) else False,
            status=str(lead.get("status") or data.get("status") or ""),
            status_label=str(data.get("status_label") or ""),
            manager_name=str(data.get("manager_name") or lead.get("manager_name") or ""),
            assigned=bool(data.get("assigned")) if isinstance(data, dict) else False,
            raw=data if isinstance(data, dict) else {},
        )
