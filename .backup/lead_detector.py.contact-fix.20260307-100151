from __future__ import annotations

import re

PHONE_RE = re.compile(r"(?<!\d)(?:\+7|8)?[\s\-()]*\d[\d\s\-()]{8,14}\d(?!\d)")
HANDLE_RE = re.compile(r"@[A-Za-z0-9_]{4,}")
LINK_RE = re.compile(r"https?://t\.me/[A-Za-z0-9_]+")


class LeadDetector:
    def extract_contact(self, text: str) -> tuple[str, str] | None:
        for regex in (PHONE_RE, HANDLE_RE, LINK_RE):
            match = regex.search(text)
            if match:
                raw = match.group(0)
                return raw, self._normalize(raw)
        return None

    @staticmethod
    def _normalize(raw: str) -> str:
        cleaned = raw.strip().lower()
        if cleaned.startswith("https://t.me/"):
            cleaned = "@" + cleaned.removeprefix("https://t.me/")
        if cleaned.startswith("8") and cleaned[1:].isdigit() and len(cleaned) == 11:
            cleaned = "+7" + cleaned[1:]

        digits = re.sub(r"\D", "", cleaned)
        if len(digits) >= 10:
            if digits.startswith("8") and len(digits) == 11:
                digits = "7" + digits[1:]
            if not digits.startswith("+"):
                return "+" + digits
        return cleaned
