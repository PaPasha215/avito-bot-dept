from __future__ import annotations

import json
import logging

import httpx

from app.types import ClassificationResult, Domain

logger = logging.getLogger(__name__)


class OpenAIClient:
    def __init__(self, api_key: str | None, base_url: str, model: str, timeout_seconds: int = 30):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._client = httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        self._client.close()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _chat_completion(self, messages: list[dict], temperature: float = 0.2, max_tokens: int = 400) -> str:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        url = f"{self.base_url}/chat/completions"
        resp = self._client.post(
            url,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    def classify_domain(self, ad_title: str, recent_messages: list[str]) -> ClassificationResult:
        if not self.enabled:
            return ClassificationResult(domain=Domain.UNKNOWN, confidence=0.0, reason="openai_disabled")

        prompt = (
            "Классифицируй домен диалога для Авито: REAL_ESTATE, AUTO, OTHER, UNKNOWN. "
            "Ответ строго JSON: {\"domain\":\"...\",\"confidence\":0.0,\"reason\":\"...\"}."
        )
        user = {
            "ad_title": ad_title,
            "recent_messages": recent_messages[-8:],
        }

        raw = self._chat_completion(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            temperature=0,
            max_tokens=180,
        )

        try:
            parsed = json.loads(raw)
            domain = Domain(parsed.get("domain", "UNKNOWN"))
            confidence = float(parsed.get("confidence", 0.0))
            reason = str(parsed.get("reason", "no_reason"))
            return ClassificationResult(domain=domain, confidence=max(0.0, min(1.0, confidence)), reason=reason)
        except Exception:
            logger.warning("Failed to parse domain classification response: %s", raw)
            return ClassificationResult(domain=Domain.UNKNOWN, confidence=0.0, reason="parse_error")

    def generate_reply(self, system_prompt: str, chat_history: list[dict]) -> str:
        if not self.enabled:
            return "Здравствуйте! Уточню детали у менеджера и вернусь с точным ответом. Подскажите, пожалуйста, номер телефона или мессенджер?"

        trimmed = chat_history[-12:]
        messages = [{"role": "system", "content": system_prompt}] + trimmed
        answer = self._chat_completion(messages=messages, temperature=0.3, max_tokens=260)
        return answer.strip()

    def summarize_lead(self, ad_title: str, contact_raw: str, transcript: list[str]) -> str:
        if not self.enabled:
            joined = " | ".join(transcript[-4:])
            return f"Интерес к объявлению '{ad_title}'. Контакт: {contact_raw}. Контекст: {joined[:800]}"

        prompt = (
            "Сделай короткое summary лида (3-5 строк) для менеджера. "
            "Укажи намерение клиента, важные условия, риски и что спросить на звонке."
        )
        content = {
            "ad_title": ad_title,
            "contact": contact_raw,
            "transcript": transcript[-10:],
        }
        return self._chat_completion(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
            ],
            temperature=0.2,
            max_tokens=260,
        )
