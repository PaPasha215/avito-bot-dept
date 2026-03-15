from __future__ import annotations

from app.core.config import Settings
from app.repositories import Repository
from app.services.self_learning import SelfLearningService
from app.types import Domain, MessageDirection


def test_learn_once_creates_success_examples(db_session):
    repo = Repository(db_session)
    ad = repo.upsert_ad("ad-sl-1", "Комната, Екатеринбург", "REAL_ESTATE", "Недвижимость", None)
    chat = repo.upsert_chat("chat-sl-1", ad.id, "Иван")
    repo.save_message(
        chat_id=chat.id,
        direction=MessageDirection.INBOUND,
        text="Нужна комната на месяц",
        external_message_id="sl-msg-1",
        payload_json="{}",
    )
    repo.save_message(
        chat_id=chat.id,
        direction=MessageDirection.OUTBOUND,
        text="Подскажите, пожалуйста, номер телефона или мессенджер?",
        external_message_id=None,
        payload_json=None,
    )
    repo.create_lead(
        chat_id=chat.id,
        contact_raw="+79990000000",
        contact_normalized="+79990000000",
        summary="Клиент готов к звонку",
    )
    db_session.commit()

    service = SelfLearningService(
        Settings(
            polling_enabled=False,
            self_learning_enabled=True,
            self_learning_promote_min_leads=1,
            self_learning_examples_per_prompt=2,
        )
    )
    result = service.learn_once(db_session)

    assert result.examples_created >= 1
    assert result.active_version is not None

    examples = repo.list_learning_examples(
        version=result.active_version,
        domain=Domain.REAL_ESTATE.value,
        limit=10,
    )
    assert len(examples) >= 1


def test_build_augmented_prompt_uses_learning_examples(db_session):
    repo = Repository(db_session)
    version = "SL-TEST-0001"
    repo.create_learning_example(
        version=version,
        domain=Domain.REAL_ESTATE.value,
        source_chat_id=None,
        source_kind="SUCCESS",
        source_tag="SUCCESS",
        intent_text="Нужна комната на месяц до 20000",
        bad_reply=None,
        better_reply="Понял вас. Подскажите, пожалуйста, номер телефона или мессенджер?",
        rule_text="Уточняй бюджет/срок и веди к контакту коротким сообщением.",
        weight=1.2,
        example_hash="hash-sl-test-0001",
    )
    repo.set_setting("learning_active_version", version)
    repo.set_setting("learning_stable_version", version)
    db_session.commit()

    service = SelfLearningService(
        Settings(
            polling_enabled=False,
            self_learning_enabled=True,
            self_learning_examples_per_prompt=2,
        )
    )

    prompt, used_version, used_count = service.build_augmented_prompt(
        db=db_session,
        base_prompt="BASE_PROMPT",
        chat_external_id="chat-sl-x",
        ad_title="Комната 20 м2",
        recent_messages=["Нужна комната на месяц", "Бюджет 20000"],
    )

    assert used_version == version
    assert used_count == 1
    assert "SELF_LEARNING_CONTEXT" in prompt


def test_wrong_domain_fail_lesson_keeps_silent_recommendation():
    service = SelfLearningService(
        Settings(
            polling_enabled=False,
            self_learning_enabled=True,
        )
    )

    intent, bad_reply, better_reply, rule_text, _ = service._build_fail_lesson(
        tag="WRONG_DOMAIN",
        comment="Это был нерелевантный диалог",
        latest_user="Сколько расход масла?",
        latest_bot="Здравствуйте! Подскажите номер телефона.",
    )

    assert intent == "Сколько расход масла?"
    assert bad_reply == "Здравствуйте! Подскажите номер телефона."
    assert "не отвечай клиенту" in rule_text
    assert better_reply == ""
