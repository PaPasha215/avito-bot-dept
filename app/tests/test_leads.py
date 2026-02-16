from __future__ import annotations

from app.repositories import Repository
from app.services.lead_detector import LeadDetector


def test_extract_phone():
    detector = LeadDetector()
    found = detector.extract_contact("Мой номер +7 (999) 218-24-68, пишите")
    assert found is not None
    raw, normalized = found
    assert raw.startswith("+7")
    assert normalized == "+79992182468"


def test_deduplicate_lead(db_session):
    repo = Repository(db_session)
    ad = repo.upsert_ad("ad-1", "Койко место", "REAL_ESTATE", "Недвижимость", None)
    chat = repo.upsert_chat("chat-1", ad.id, "Иван")

    first = repo.create_lead(chat.id, "+79992182468", "+79992182468", "summary")
    second = repo.create_lead(chat.id, "+79992182468", "+79992182468", "summary")

    assert first is not None
    assert second is None
