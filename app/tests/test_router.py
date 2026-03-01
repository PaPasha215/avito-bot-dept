from __future__ import annotations

from app.services.classifier import DomainClassifier
from app.services.router import RouterService
from app.types import ChatDecision, Domain


def test_ignore_auto_category():
    router = RouterService(classifier=DomainClassifier(), confidence_threshold=0.8)
    result = router.route(
        ad_category_raw="Автомобили",
        ad_title="Infiniti FX45 2007",
        recent_messages=["Здравствуйте какой расход масла"],
    )
    assert result.decision == ChatDecision.IGNORE_SILENT
    assert result.domain == Domain.AUTO


def test_reply_real_estate_category():
    router = RouterService(classifier=DomainClassifier(), confidence_threshold=0.8)
    result = router.route(
        ad_category_raw="Недвижимость",
        ad_title="Койко-место в центре",
        recent_messages=["Нужно заселение на месяц"],
    )
    assert result.decision == ChatDecision.REPLY
    assert result.domain == Domain.REAL_ESTATE


def test_ignore_offtopic_auto_inside_real_estate_chat():
    router = RouterService(classifier=DomainClassifier(), confidence_threshold=0.8)
    result = router.route(
        ad_category_raw="Недвижимость",
        ad_title="Койко-место в центре",
        recent_messages=["Здравствуйте, какой расход масла и пробег у машины?"],
    )
    assert result.decision == ChatDecision.IGNORE_SILENT
    assert result.domain == Domain.AUTO


def test_ignore_non_housing_use_inside_real_estate_chat():
    router = RouterService(classifier=DomainClassifier(), confidence_threshold=0.8)
    result = router.route(
        ad_category_raw="Недвижимость",
        ad_title="Комната 18 м2",
        recent_messages=["Нужен юридический адрес под офис для ООО"],
    )
    assert result.decision == ChatDecision.IGNORE_SILENT
    assert result.domain == Domain.OTHER


def test_ignore_unknown_when_low_confidence():
    router = RouterService(classifier=DomainClassifier(), confidence_threshold=0.8)
    result = router.route(
        ad_category_raw=None,
        ad_title="Объявление",
        recent_messages=["Привет"],
    )
    assert result.decision == ChatDecision.IGNORE_SILENT
