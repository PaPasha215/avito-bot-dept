from __future__ import annotations

from app.services.classifier import DomainClassifier
from app.types import ChatDecision, Domain, RoutingResult


class RouterService:
    RECENT_AUTO_KEYWORDS = {
        "авто",
        "автомобиль",
        "машин",
        "двигател",
        "двс",
        "масло",
        "расход",
        "пробег",
        "vin",
        "коробк",
        "infiniti",
    }
    RECENT_OFFTOPIC_KEYWORDS = {
        "юридическ",
        "регистрац",
        "ооо",
        "ип",
        "офис",
        "коворкинг",
        "рабочее место",
        "услуги продвижения",
        "реклама",
        "таргет",
        "настрою",
    }
    RECENT_REAL_ESTATE_KEYWORDS = {
        "хостел",
        "гостиниц",
        "комнат",
        "койко",
        "засел",
        "снять",
        "аренд",
        "прожив",
        "место",
        "сутк",
        "месяц",
    }

    def __init__(self, classifier: DomainClassifier, confidence_threshold: float = 0.8):
        self.classifier = classifier
        self.confidence_threshold = confidence_threshold

    def route(self, ad_category_raw: str | None, ad_title: str, recent_messages: list[str]) -> RoutingResult:
        category_domain = self._category_to_domain(ad_category_raw, ad_title)
        recent_override = self._recent_messages_override(recent_messages)

        # Hard guardrail for known non-real-estate categories.
        if category_domain in {Domain.AUTO, Domain.OTHER}:
            return RoutingResult(
                decision=ChatDecision.IGNORE_SILENT,
                domain=category_domain,
                confidence=1.0,
                reason="ad_category_non_real_estate",
            )

        # If ad context is explicitly real-estate, trust listing metadata.
        # This prevents false silences on short/neutral user messages.
        if category_domain == Domain.REAL_ESTATE:
            if recent_override is not None:
                return recent_override
            return RoutingResult(
                decision=ChatDecision.REPLY,
                domain=Domain.REAL_ESTATE,
                confidence=1.0,
                reason="ad_category_real_estate",
            )

        dialog_cls = self.classifier.classify(ad_title=ad_title, recent_messages=recent_messages)

        # If chat context strongly looks non-real-estate, keep silent.
        if dialog_cls.domain in {Domain.AUTO, Domain.OTHER} and dialog_cls.confidence >= self.confidence_threshold:
            return RoutingResult(
                decision=ChatDecision.IGNORE_SILENT,
                domain=dialog_cls.domain,
                confidence=dialog_cls.confidence,
                reason="dialog_non_real_estate",
            )

        # If ad meta is unknown, allow only confident real-estate classification.
        if category_domain == Domain.UNKNOWN:
            if dialog_cls.domain != Domain.REAL_ESTATE or dialog_cls.confidence < self.confidence_threshold:
                return RoutingResult(
                    decision=ChatDecision.IGNORE_SILENT,
                    domain=dialog_cls.domain,
                    confidence=dialog_cls.confidence,
                    reason="uncertain_domain",
                )

        # In category REAL_ESTATE or confident dialog classification, reply.
        chosen_confidence = max(0.8, dialog_cls.confidence)
        return RoutingResult(
            decision=ChatDecision.REPLY,
            domain=Domain.REAL_ESTATE,
            confidence=chosen_confidence,
            reason="real_estate_flow",
        )

    @staticmethod
    def _category_to_domain(ad_category_raw: str | None, ad_title: str) -> Domain:
        combined = f"{ad_category_raw or ''} {ad_title}".lower()

        if any(keyword in combined for keyword in ["авто", "машин", "автомоб", "транспорт"]):
            return Domain.AUTO

        if any(
            keyword in combined
            for keyword in [
                "недвиж",
                "хостел",
                "гостиниц",
                "квартир",
                "комнат",
                "койко",
                "жиль",
                "аренд",
                "прожив",
            ]
        ):
            return Domain.REAL_ESTATE

        if ad_category_raw:
            return Domain.OTHER
        return Domain.UNKNOWN

    def _recent_messages_override(self, recent_messages: list[str]) -> RoutingResult | None:
        text = "\n".join(msg.strip().lower() for msg in recent_messages if msg).strip()
        if not text:
            return None

        has_real_estate_signal = any(keyword in text for keyword in self.RECENT_REAL_ESTATE_KEYWORDS)
        if has_real_estate_signal:
            return None

        if any(keyword in text for keyword in self.RECENT_AUTO_KEYWORDS):
            return RoutingResult(
                decision=ChatDecision.IGNORE_SILENT,
                domain=Domain.AUTO,
                confidence=0.95,
                reason="recent_messages_auto_override",
            )

        if any(keyword in text for keyword in self.RECENT_OFFTOPIC_KEYWORDS):
            return RoutingResult(
                decision=ChatDecision.IGNORE_SILENT,
                domain=Domain.OTHER,
                confidence=0.9,
                reason="recent_messages_other_override",
            )

        return None
