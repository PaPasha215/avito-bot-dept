from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import utcnow
from app.models import LearningExample
from app.repositories import Repository
from app.types import Domain, MessageDirection

PHONE_RE = re.compile(r"(?<!\d)(?:\+7|8)?[\s\-()]*\d[\d\s\-()]{8,14}\d(?!\d)")
HANDLE_RE = re.compile(r"@[A-Za-z0-9_]{4,}")
LINK_RE = re.compile(r"https?://t\.me/[A-Za-z0-9_]+")
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9_]+")


@dataclass(slots=True)
class LearnRunResult:
    run_at: datetime
    since: datetime
    source_chats: int
    examples_created: int
    new_version: str | None
    active_version: str | None
    stable_version: str | None
    action: str
    quality_usage_count: int
    quality_chat_count: int
    quality_leads_count: int
    quality_feedback_count: int
    quality_wrong_domain_count: int


@dataclass(slots=True)
class LearningStatus:
    enabled: bool
    active_version: str | None
    stable_version: str | None
    candidate_started_at: datetime | None
    last_run_at: datetime | None
    active_examples: int
    stable_examples: int
    total_examples: int


class SelfLearningService:
    LAST_RUN_KEY = "learning_last_run_at"
    ACTIVE_VERSION_KEY = "learning_active_version"
    STABLE_VERSION_KEY = "learning_stable_version"
    CANDIDATE_STARTED_AT_KEY = "learning_candidate_started_at"

    NEGATIVE_TAGS = {"WRONG_DOMAIN", "WRONG_FACT", "WRONG_TONE", "MISSED_LEAD"}

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.self_learning_enabled)

    def build_augmented_prompt(
        self,
        db: Session,
        base_prompt: str,
        chat_external_id: str,
        ad_title: str,
        recent_messages: list[str],
    ) -> tuple[str, str | None, int]:
        if not self.enabled or self.settings.self_learning_examples_per_prompt <= 0:
            return base_prompt, None, 0

        repo = Repository(db)
        active_version = repo.get_setting(self.ACTIVE_VERSION_KEY)
        stable_version = repo.get_setting(self.STABLE_VERSION_KEY)

        if not active_version:
            latest = repo.get_latest_learning_version()
            if not latest:
                return base_prompt, None, 0
            active_version = latest
            stable_version = stable_version or latest
            repo.set_setting(self.ACTIVE_VERSION_KEY, active_version)
            repo.set_setting(self.STABLE_VERSION_KEY, stable_version)

        if not stable_version:
            stable_version = active_version
            repo.set_setting(self.STABLE_VERSION_KEY, stable_version)

        chosen_version = self._pick_version_for_chat(
            chat_external_id=chat_external_id,
            active_version=active_version,
            stable_version=stable_version,
        )
        if not chosen_version:
            return base_prompt, None, 0

        pool_size = max(50, self.settings.self_learning_examples_per_prompt * 10)
        examples_pool = repo.list_learning_examples(
            version=chosen_version,
            domain=Domain.REAL_ESTATE.value,
            limit=pool_size,
        )
        selected = self._select_relevant_examples(
            examples_pool=examples_pool,
            query_text=f"{ad_title}\n" + "\n".join(recent_messages[-8:]),
            limit=self.settings.self_learning_examples_per_prompt,
        )
        if not selected:
            return base_prompt, chosen_version, 0

        prompt_suffix = self._format_examples_prompt(version=chosen_version, examples=selected)
        return f"{base_prompt}\n\n{prompt_suffix}", chosen_version, len(selected)

    def record_reply_usage(
        self,
        db: Session,
        chat_id: int,
        external_chat_id: str,
        learning_version: str | None,
        examples_count: int,
    ) -> None:
        if not self.enabled:
            return
        repo = Repository(db)
        repo.create_learning_reply_usage(
            chat_id=chat_id,
            external_chat_id=external_chat_id,
            learning_version=learning_version,
            examples_count=examples_count,
        )

    def learn_once(self, db: Session) -> LearnRunResult:
        now = utcnow()
        repo = Repository(db)

        if not self.enabled:
            return LearnRunResult(
                run_at=now,
                since=now,
                source_chats=0,
                examples_created=0,
                new_version=None,
                active_version=repo.get_setting(self.ACTIVE_VERSION_KEY),
                stable_version=repo.get_setting(self.STABLE_VERSION_KEY),
                action="disabled",
                quality_usage_count=0,
                quality_chat_count=0,
                quality_leads_count=0,
                quality_feedback_count=0,
                quality_wrong_domain_count=0,
            )

        since = self._parse_dt(repo.get_setting(self.LAST_RUN_KEY)) or (now - timedelta(days=self.settings.self_learning_lookback_days))
        source_chat_ids = repo.get_learning_candidate_chat_ids(
            since=since,
            limit=self.settings.self_learning_max_source_chats,
        )

        examples_created = 0
        new_version: str | None = None
        if source_chat_ids:
            new_version = now.strftime("SL-%Y%m%d%H%M%S")
            remaining = self.settings.self_learning_max_examples_per_run
            for chat_id in source_chat_ids:
                if remaining <= 0:
                    break
                created = self._learn_from_chat(repo=repo, chat_id=chat_id, version=new_version, max_examples=remaining)
                examples_created += created
                remaining -= created
            if examples_created == 0:
                new_version = None

        stable_version = repo.get_setting(self.STABLE_VERSION_KEY)
        active_version = repo.get_setting(self.ACTIVE_VERSION_KEY)
        action = "none"

        if new_version:
            if not stable_version:
                stable_version = new_version
                active_version = new_version
                repo.set_setting(self.STABLE_VERSION_KEY, stable_version)
                repo.set_setting(self.ACTIVE_VERSION_KEY, active_version)
                repo.set_setting(self.CANDIDATE_STARTED_AT_KEY, "")
                action = "bootstrap_stable"
            else:
                active_version = new_version
                repo.set_setting(self.ACTIVE_VERSION_KEY, active_version)
                repo.set_setting(self.CANDIDATE_STARTED_AT_KEY, now.isoformat())
                action = "candidate_started"

        evaluate_action, active_version, stable_version, quality = self._evaluate_canary(repo=repo, now=now)
        if evaluate_action != "none":
            action = evaluate_action

        repo.set_setting(self.LAST_RUN_KEY, now.isoformat())
        db.commit()

        return LearnRunResult(
            run_at=now,
            since=since,
            source_chats=len(source_chat_ids),
            examples_created=examples_created,
            new_version=new_version,
            active_version=active_version,
            stable_version=stable_version,
            action=action,
            quality_usage_count=quality.get("usage_count", 0),
            quality_chat_count=quality.get("chat_count", 0),
            quality_leads_count=quality.get("leads_count", 0),
            quality_feedback_count=quality.get("feedback_count", 0),
            quality_wrong_domain_count=quality.get("wrong_domain_count", 0),
        )

    def run_if_due(self, db: Session) -> LearnRunResult | None:
        if not self.enabled:
            return None

        repo = Repository(db)
        now = utcnow()
        last_run_at = self._parse_dt(repo.get_setting(self.LAST_RUN_KEY))
        if last_run_at is not None:
            delta = now - last_run_at
            if delta < timedelta(hours=max(1, self.settings.self_learning_interval_hours)):
                return None
        return self.learn_once(db)

    def get_status(self, db: Session) -> LearningStatus:
        repo = Repository(db)
        active = repo.get_setting(self.ACTIVE_VERSION_KEY)
        stable = repo.get_setting(self.STABLE_VERSION_KEY)
        candidate_started_at = self._parse_dt(repo.get_setting(self.CANDIDATE_STARTED_AT_KEY))
        last_run_at = self._parse_dt(repo.get_setting(self.LAST_RUN_KEY))

        active_examples = repo.count_learning_examples(version=active) if active else 0
        stable_examples = repo.count_learning_examples(version=stable) if stable else 0
        total_examples = repo.count_learning_examples()

        return LearningStatus(
            enabled=self.enabled,
            active_version=active,
            stable_version=stable,
            candidate_started_at=candidate_started_at,
            last_run_at=last_run_at,
            active_examples=active_examples,
            stable_examples=stable_examples,
            total_examples=total_examples,
        )

    def _evaluate_canary(
        self,
        repo: Repository,
        now: datetime,
    ) -> tuple[str, str | None, str | None, dict[str, int]]:
        active_version = repo.get_setting(self.ACTIVE_VERSION_KEY)
        stable_version = repo.get_setting(self.STABLE_VERSION_KEY)
        if not active_version or not stable_version or active_version == stable_version:
            return "none", active_version, stable_version, {}

        since = self._parse_dt(repo.get_setting(self.CANDIDATE_STARTED_AT_KEY)) or (now - timedelta(days=1))
        quality = repo.get_learning_quality_stats(version=active_version, since=since)

        if quality.get("wrong_domain_count", 0) >= self.settings.self_learning_rollback_wrong_domain_threshold:
            repo.set_setting(self.ACTIVE_VERSION_KEY, stable_version)
            repo.set_setting(self.CANDIDATE_STARTED_AT_KEY, "")
            return "rollback_wrong_domain", stable_version, stable_version, quality

        if (
            quality.get("leads_count", 0) >= self.settings.self_learning_promote_min_leads
            and quality.get("wrong_domain_count", 0) == 0
        ):
            repo.set_setting(self.STABLE_VERSION_KEY, active_version)
            repo.set_setting(self.CANDIDATE_STARTED_AT_KEY, "")
            return "promoted_candidate", active_version, active_version, quality

        return "canary_running", active_version, stable_version, quality

    def _learn_from_chat(self, repo: Repository, chat_id: int, version: str, max_examples: int) -> int:
        if max_examples <= 0:
            return 0

        detail = repo.get_chat_detail(chat_id)
        if detail is None:
            return 0
        chat, ad, messages = detail

        inbound = [self._mask_sensitive(m.text) for m in messages if m.direction == MessageDirection.INBOUND.value]
        outbound = [self._mask_sensitive(m.text) for m in messages if m.direction == MessageDirection.OUTBOUND.value]
        if not inbound or not outbound:
            return 0

        latest_user = self._truncate(inbound[-1], 320)
        latest_bot = self._truncate(outbound[-1], 420)
        feedback = [f for f in repo.list_feedback_events_by_chat(chat_id) if f.tag in self.NEGATIVE_TAGS]
        leads = repo.list_leads_by_chat(chat_id)

        created = 0
        if feedback:
            for item in feedback[:max_examples]:
                intent, bad_reply, better_reply, rule_text, weight = self._build_fail_lesson(
                    tag=item.tag,
                    comment=item.comment,
                    latest_user=latest_user,
                    latest_bot=latest_bot,
                )
                example_hash = self._hash_example(
                    version=version,
                    source_kind="FAIL",
                    source_tag=item.tag,
                    intent_text=intent,
                    bad_reply=bad_reply,
                    better_reply=better_reply,
                    rule_text=rule_text,
                )
                row = repo.create_learning_example(
                    version=version,
                    domain=Domain.REAL_ESTATE.value,
                    source_chat_id=chat_id,
                    source_kind="FAIL",
                    source_tag=item.tag,
                    intent_text=intent,
                    bad_reply=bad_reply,
                    better_reply=better_reply,
                    rule_text=rule_text,
                    weight=weight,
                    example_hash=example_hash,
                )
                if row is not None:
                    created += 1
            return created

        if leads:
            intent, bad_reply, better_reply, rule_text, weight = self._build_success_lesson(
                latest_user=latest_user,
                latest_bot=latest_bot,
                ad_title=ad.title,
            )
            example_hash = self._hash_example(
                version=version,
                source_kind="SUCCESS",
                source_tag="SUCCESS",
                intent_text=intent,
                bad_reply=bad_reply,
                better_reply=better_reply,
                rule_text=rule_text,
            )
            row = repo.create_learning_example(
                version=version,
                domain=Domain.REAL_ESTATE.value,
                source_chat_id=chat_id,
                source_kind="SUCCESS",
                source_tag="SUCCESS",
                intent_text=intent,
                bad_reply=bad_reply,
                better_reply=better_reply,
                rule_text=rule_text,
                weight=weight,
                example_hash=example_hash,
            )
            if row is not None:
                created += 1

        return created

    def _build_fail_lesson(
        self,
        tag: str,
        comment: str,
        latest_user: str,
        latest_bot: str,
    ) -> tuple[str, str, str, str, float]:
        default_better = (
            "Чтобы не ошибиться, уточню этот момент у менеджера. "
            "Подскажите, пожалуйста, номер телефона или мессенджер для связи."
        )
        rule_map = {
            "WRONG_DOMAIN": "Если домен не недвижимость или есть сомнение в домене, не отвечай клиенту.",
            "WRONG_FACT": "Не утверждай факты без проверки. При сомнении: «уточню у менеджера».",
            "WRONG_TONE": "Держи спокойный, краткий, вежливый тон, 1-4 предложения без давления.",
            "MISSED_LEAD": "После явного интереса клиента мягко запроси контакт и не пропускай лид.",
        }
        better_map = {
            "WRONG_DOMAIN": "Спасибо за сообщение. Уточню у менеджера и вернусь с ответом.",
            "WRONG_FACT": "Чтобы не ошибиться в деталях, уточню у менеджера и сразу напишу вам.",
            "WRONG_TONE": "Понял вас. Помогу подобрать вариант и уточню детали у менеджера.",
            "MISSED_LEAD": "Чтобы быстрее всё подобрать и уточнить наличие, подскажите, пожалуйста, номер телефона или мессенджер?",
        }

        cleaned_comment = self._truncate(self._mask_sensitive(comment), 220) if comment else ""
        rule = rule_map.get(tag, "Отвечай кратко, по теме и веди диалог к сбору контакта.")
        if cleaned_comment:
            rule = f"{rule} Уточнение менеджера: {cleaned_comment}"
        better = better_map.get(tag, default_better)
        if tag == "MISSED_LEAD" and "номер" not in better.lower():
            better = default_better
        return latest_user, latest_bot, better, rule, 1.6

    @staticmethod
    def _build_success_lesson(latest_user: str, latest_bot: str, ad_title: str) -> tuple[str, None, str, str, float]:
        rule = (
            "Сохраняй короткий сценарий квалификации: уточнить условия клиента и мягко вести к контакту, "
            "не обещая бронь и не выдавая лишние контакты."
        )
        better = latest_bot
        if len(better) < 20:
            better = (
                "Понял вас. Помогу подобрать подходящий вариант. "
                "Подскажите, пожалуйста, номер телефона или мессенджер для связи?"
            )
        intent = f"{latest_user} (объявление: {ad_title})"
        return intent, None, better, rule, 1.3

    def _pick_version_for_chat(
        self,
        chat_external_id: str,
        active_version: str,
        stable_version: str,
    ) -> str | None:
        if active_version == stable_version:
            return active_version

        bucket = int(hashlib.sha256(chat_external_id.encode("utf-8")).hexdigest()[:8], 16) % 100
        if bucket < max(0, min(100, self.settings.self_learning_canary_percent)):
            return active_version
        return stable_version

    def _select_relevant_examples(
        self,
        examples_pool: list[LearningExample],
        query_text: str,
        limit: int,
    ) -> list[LearningExample]:
        if not examples_pool or limit <= 0:
            return []
        query_tokens = self._tokens(query_text)

        scored: list[tuple[float, LearningExample]] = []
        for item in examples_pool:
            base = item.weight
            if query_tokens:
                target_tokens = self._tokens(f"{item.intent_text}\n{item.rule_text}\n{item.better_reply}")
                overlap = len(query_tokens & target_tokens)
                score = base + overlap * 10.0
            else:
                score = base
            scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        selected: list[LearningExample] = []
        seen_hashes: set[str] = set()
        for _, item in scored:
            if item.example_hash in seen_hashes:
                continue
            selected.append(item)
            seen_hashes.add(item.example_hash)
            if len(selected) >= limit:
                break
        return selected

    def _format_examples_prompt(self, version: str, examples: list[LearningExample]) -> str:
        lines = [
            "# SELF_LEARNING_CONTEXT",
            f"version={version}",
            "Внутренние правила и примеры. Не упоминай их клиенту.",
        ]
        for idx, item in enumerate(examples, start=1):
            lines.append(f"{idx}. Правило: {self._truncate(item.rule_text, 260)}")
            if item.bad_reply:
                lines.append(f"   Неудачный ответ (избегать): {self._truncate(item.bad_reply, 220)}")
            lines.append(f"   Запрос клиента: {self._truncate(item.intent_text, 220)}")
            lines.append(f"   Рекомендованный ответ: {self._truncate(item.better_reply, 260)}")
        return "\n".join(lines)

    @staticmethod
    def _hash_example(
        version: str,
        source_kind: str,
        source_tag: str | None,
        intent_text: str,
        bad_reply: str | None,
        better_reply: str,
        rule_text: str,
    ) -> str:
        raw = "\n".join(
            [
                version,
                source_kind,
                source_tag or "",
                intent_text.strip().lower(),
                (bad_reply or "").strip().lower(),
                better_reply.strip().lower(),
                rule_text.strip().lower(),
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _mask_sensitive(text: str) -> str:
        masked = PHONE_RE.sub("[PHONE]", text)
        masked = HANDLE_RE.sub("[HANDLE]", masked)
        masked = LINK_RE.sub("[LINK]", masked)
        return masked

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        if len(text) <= max_len:
            return text
        return text[: max_len - 3] + "..."

    @staticmethod
    def _parse_dt(raw: str | None) -> datetime | None:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {t.lower() for t in TOKEN_RE.findall(text) if len(t) >= 3}
