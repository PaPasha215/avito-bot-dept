from __future__ import annotations

from app.integrations.openai_client import OpenAIClient
from app.types import ClassificationResult, Domain

REAL_ESTATE_KEYWORDS = {
    "хостел",
    "гостиниц",
    "гостиница",
    "квартира",
    "койко",
    "койко-место",
    "проживание",
    "заселение",
    "сутки",
    "месяц",
    "комната",
    "семейный номер",
}
AUTO_KEYWORDS = {
    "авто",
    "автомобиль",
    "машина",
    "двигатель",
    "двс",
    "расход",
    "масло",
    "пробег",
    "vin",
    "л.с",
    "коробка",
    "infiniti",
}


class DomainClassifier:
    def __init__(self, openai_client: OpenAIClient | None = None):
        self.openai_client = openai_client

    def classify(self, ad_title: str, recent_messages: list[str]) -> ClassificationResult:
        text = (ad_title + "\n" + "\n".join(recent_messages)).lower()
        real_score = sum(1 for token in REAL_ESTATE_KEYWORDS if token in text)
        auto_score = sum(1 for token in AUTO_KEYWORDS if token in text)

        if real_score == 0 and auto_score == 0:
            local = ClassificationResult(domain=Domain.UNKNOWN, confidence=0.0, reason="no_keywords")
        elif real_score > auto_score:
            confidence = min(0.95, 0.6 + 0.07 * (real_score - auto_score))
            local = ClassificationResult(domain=Domain.REAL_ESTATE, confidence=confidence, reason="keyword_real_estate")
        elif auto_score > real_score:
            confidence = min(0.95, 0.6 + 0.07 * (auto_score - real_score))
            local = ClassificationResult(domain=Domain.AUTO, confidence=confidence, reason="keyword_auto")
        else:
            local = ClassificationResult(domain=Domain.OTHER, confidence=0.5, reason="keyword_tie")

        if local.confidence >= 0.8:
            return local

        if self.openai_client and self.openai_client.enabled:
            llm_result = self.openai_client.classify_domain(ad_title=ad_title, recent_messages=recent_messages)
            if llm_result.confidence >= local.confidence:
                return llm_result

        return local
