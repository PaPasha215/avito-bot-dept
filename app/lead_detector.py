from __future__ import annotations

import re
from dataclasses import dataclass

PHONE_RE = re.compile(r"(?<!\d)\+?\d[\d\s\-()]{3,22}\d(?!\d)")
HANDLE_RE = re.compile(r"@[A-Za-z0-9_]{4,}")
LINK_RE = re.compile(r"https?://t\.me/[A-Za-z0-9_]+")
CONTACT_HINT_RE = re.compile(
    r"(номер|тел(?:ефон)?|контакт|звон|ватсап|whatsapp|wa\b|tg\b|телеграм|пишите|связ[а-я]*|мой)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ContactMatch:
    raw: str
    normalized: str
    contact_type: str
    is_valid: bool
    needs_manager_review: bool = False
    is_anomaly: bool = False
    reason: str = ""


class LeadDetector:
    def extract_contact(self, text: str) -> tuple[str, str] | None:
        found = self.extract_contact_details(text)
        if found and found.is_valid:
            return found.raw, found.normalized
        return None

    def extract_contact_details(self, text: str) -> ContactMatch | None:
        for regex in (HANDLE_RE, LINK_RE):
            match = regex.search(text)
            if match:
                raw = match.group(0)
                return ContactMatch(
                    raw=raw,
                    normalized=self._normalize_handle(raw),
                    contact_type="TELEGRAM",
                    is_valid=True,
                )

        for match in PHONE_RE.finditer(text):
            candidate = match.group(0)
            details = self._normalize_phone(candidate=candidate, full_text=text)
            if details is not None:
                return details
        return None

    @staticmethod
    def _normalize_handle(raw: str) -> str:
        cleaned = raw.strip().lower()
        if cleaned.startswith("https://t.me/"):
            return "@" + cleaned.removeprefix("https://t.me/")
        return cleaned

    def _normalize_phone(self, *, candidate: str, full_text: str) -> ContactMatch | None:
        raw = candidate.strip()
        digits = re.sub(r"\D", "", raw)
        if not digits:
            return None

        has_plus_prefix = raw.startswith("+")

        if len(digits) == 10:
            return ContactMatch(
                raw=raw,
                normalized=f"+7{digits}",
                contact_type="PHONE",
                is_valid=True,
            )

        if len(digits) == 11 and digits[0] in {"7", "8"}:
            return ContactMatch(
                raw=raw,
                normalized=f"+7{digits[1:]}",
                contact_type="PHONE",
                is_valid=True,
            )

        if has_plus_prefix and 11 <= len(digits) <= 15:
            return ContactMatch(
                raw=raw,
                normalized=f"+{digits}",
                contact_type="PHONE",
                is_valid=True,
            )

        if not self._looks_like_contact_intent(full_text=full_text, candidate=raw):
            return None

        if len(digits) > 10:
            return ContactMatch(
                raw=raw,
                normalized="",
                contact_type="PHONE",
                is_valid=False,
                needs_manager_review=True,
                is_anomaly=False,
                reason="long_phone_needs_review",
            )

        return ContactMatch(
            raw=raw,
            normalized="",
            contact_type="PHONE",
            is_valid=False,
            needs_manager_review=True,
            is_anomaly=True,
            reason="short_phone_anomaly",
        )

    @staticmethod
    def _looks_like_contact_intent(*, full_text: str, candidate: str) -> bool:
        compact = (full_text or "").strip().lower()
        if CONTACT_HINT_RE.search(compact):
            return True
        remainder = compact.replace(candidate.lower(), " ").strip()
        remainder = re.sub(r"[\s,.;:!?\-()]+", "", remainder)
        return len(remainder) <= 12
