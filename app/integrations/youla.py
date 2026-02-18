from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class YoulaClient:
    def __init__(
        self,
        enabled: bool,
        api_base: str | None,
        api_token: str | None,
        timeout_seconds: int = 20,
    ):
        self._enabled_flag = enabled
        self.api_base = (api_base or "").rstrip("/")
        self.api_token = api_token
        self._client = httpx.Client(timeout=timeout_seconds)

    @property
    def enabled(self) -> bool:
        return bool(self._enabled_flag and self.api_base and self.api_token)

    def close(self) -> None:
        self._client.close()

    def _headers(self) -> dict[str, str]:
        if not self.api_token:
            raise RuntimeError("YOULA_API_TOKEN is not configured")
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    def send_message(
        self,
        sender_id: str,
        recipient_id: str,
        product_id: str,
        text: str,
        images: list[dict] | None = None,
    ) -> dict:
        if not self.enabled:
            raise RuntimeError("Youla client is not enabled")
        payload: dict = {
            "sender_id": sender_id,
            "recipient_id": recipient_id,
            "product_id": product_id,
            "text": text,
        }
        if images:
            payload["images"] = images

        url = f"{self.api_base}/messages"
        resp = self._client.post(url, headers=self._headers(), json=payload)
        resp.raise_for_status()
        return resp.json() if resp.text else {}

