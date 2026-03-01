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
        try:
            resp = self._client.post(url, headers=self._headers(), json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = ""
            try:
                body = exc.response.text
            except Exception:
                body = ""
            logger.error(
                "Youla send_message failed: status=%s url=%s body=%s payload_keys=%s",
                exc.response.status_code,
                url,
                body[:500],
                list(payload.keys()),
            )
            raise
        except httpx.HTTPError:
            logger.exception("Youla send_message request error: url=%s", url)
            raise
        return resp.json() if resp.text else {}

    def get_product(self, product_id: str) -> dict:
        if not self.enabled:
            raise RuntimeError("Youla client is not enabled")
        url = f"{self.api_base}/products/{product_id}"
        try:
            resp = self._client.get(url, headers=self._headers())
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = ""
            try:
                body = exc.response.text
            except Exception:
                body = ""
            logger.warning(
                "Youla get_product failed: status=%s product_id=%s body=%s",
                exc.response.status_code,
                product_id,
                body[:500],
            )
            raise
        except httpx.HTTPError:
            logger.exception("Youla get_product request error: product_id=%s", product_id)
            raise

        payload = resp.json() if resp.text else {}
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                return data
        return {}
