# Changelog

## 2026-03-01 - prod sync checkpoint

### Stable state
- Локальный workspace синхронизирован с фактическим продовым кодом на VPS.
- Прод-контур на VPS поднят и отвечает, `app` контейнер пересобран и запущен успешно.
- Ежедневная Telegram-сводка переведена на операционные метрики бота:
  - `ANOMALY`
  - `Неотвеченные`
  - `Новые контакты`
- Ручной форс `stats-report-once` на проде выполнен успешно, Telegram вернул `HTTP 200`.
- Последняя подтвержденная отправка daily report зафиксирована в БД:
  - `stats_report_last_run_at=2026-03-01T08:50:46.304862+00:00`
- Дублирующий daily-report из heartbeat отключен на проде, срочные alert'ы оставлены только для необработанных входящих и ошибок poller.

### Added
- Синхронизированы в локальный репозиторий продовые файлы, которых раньше не было локально:
  - `app/integrations/youla.py`
  - `app/integrations/crm_ingest.py`
  - `app/services/heartbeat.py`
  - `app/tests/test_cabinet.py`
  - `app/tests/test_heartbeat.py`

### Changed
- `StatsReportingService` больше не отправляет weekly analytics по объявлениям, а формирует короткую ежедневную операционную сводку по данным БД бота.
- `app/cli.py` (`stats-report-once`) возвращает новые поля:
  - `anomaly_count`
  - `unanswered_count`
  - `new_contacts_count`
- Локальный `.env` снова соответствует текущей схеме `Settings`, `validate-env` проходит штатно.

## 2026-02-17 - MVP v1.0 checkpoint

### Stable state
- Прод-контур на VPS работает (`healthz=ok`, polling активен).
- Автоответы по недвижимости включены, non-target категории в `IGNORE_SILENT`.
- Задержка ответа включена: `REPLY_DELAY_SECONDS=6`.
- Лиды в Telegram и stats-отчет в Telegram работают.
- Точка зафиксирована для быстрого rollback.

## 2026-02-18 - v1.0.2

### Changed
- Диалоговая логика:
  - для явных запросов на заселение бот продолжает квалификацию без повторного вопроса "актуально ли";
  - добавлены адресные правила посуточного размещения:
    - только Куйбышева 30, Мамина-Сибиряка 132, Ботаническая 30;
    - по остальным объявлениям бот ведет в помесячный сценарий;
  - добавлен шаблонный ответ по посуточной цене в хостелах с ориентиром (`от 700 ₽/сутки`) и дисклеймером про уточнение у менеджера;
  - добавлен анти-абьюз фильтр: оскорбления/угрозы/обвинения -> `IGNORE_SILENT`.
- Документация:
  - добавлено отдельное ТЗ по двухуровневому Telegram-меню: `docs/TELEGRAM_MENU_2LEVEL_TZ.md`.
  - добавлен `docs/CONNECTOR_READINESS_CHECKLIST.md` с чеклистом и приоритизацией новых площадок.
  - добавлены документы старта Youla:
    - `docs/YOULA_ONBOARDING.md`
    - `docs/YOULA_SUPPORT_REQUEST_TEMPLATE.md`
  - зафиксированы Youla Swagger-ссылки для валидации (`partner-api.youla.ru/swagger/rapi`, `partner-api.youla.ru/swagger/ui`).
- Конфиг/CLI:
  - добавлены env-параметры `YOULA_*` в `.env.example`;
  - добавлена команда `make validate-youla-readiness` (`python3 -m app.cli validate-youla-readiness`).
- Youla integration v0.1:
  - добавлен `app/integrations/youla.py` (`POST /messages`, Bearer auth);
  - добавлен `POST /webhooks/youla` для событий `Ce-Type: message.incom`;
  - `MessageProcessor` стал source-aware (отправка в Avito/Youla по `event.marketplace`);
  - добавлен тест `app/tests/test_youla_webhook.py`.

## 2026-02-17 - v1.0.1

### Changed
- Диалоговая логика:
  - если клиент уже явно формулирует запрос на заселение, бот не задает повторный вопрос «актуально ли», а продолжает квалификацию;
  - добавлен guardrail против deflect-ответов вида «уточню у менеджера» без попытки собрать контакт.
- Weekly-отчет:
  - отправка только по расписанию (понедельник 09:00, локальный TZ);
  - период отчета: предыдущая календарная неделя;
  - отчет полностью на русском;
  - в отчет включаются только объявления недвижимости.

## 2026-02-17 - v0.1.1

### Added
- Детерминированные guardrails в `MessageProcessor`:
  - уточнение короткого бюджета (`8-12`, `8р-12р`, `812`) как "тысяч рублей в месяц";
  - отдельный ответ на "куда вам набрать" без выдачи прямого номера менеджера;
  - мягкий первый ответ в новом чате (приветствие + вопрос об актуальности заселения).
- Runtime-контекст объявления в промпте генерации:
  - явные `ad_title/ad_category` и фокус по типу объявления (в т.ч. "койко-место").
- Тесты для новых сценариев в `app/tests/test_processor.py`.
- Daily stats reporting:
  - новый `StatsReportingService` с автоциклом через poller;
  - ручной запуск `stats-report-once` (CLI + `make stats-report-once`);
  - новый тест `app/tests/test_stats_reporting.py`.
- AvitoClient methods for analytics:
  - `get_item_analytics` (`/stats/v2/accounts/{user_id}/items`);
  - `get_account_item` (`/core/v1/accounts/{user_id}/items/{item_id}/`).
- Новые env-настройки `STATS_REPORT_*` и `TELEGRAM_STATS_CHAT_ID`.

### Changed
- Обновлен default `REAL_ESTATE` prompt до версии `REAL_ESTATE_PROMPT_V1_2`.
- Telegram lead-card сделана компактнее:
  - summary сокращается;
  - в карточке только последние 3 реплики контекста.

## 2026-02-16 - v0.1.0

### Added
- Базовый FastAPI сервис для Avito AI Assistant.
- Poller каждые 10 секунд.
- Роутинг `REPLY` / `IGNORE_SILENT`.
- Классификация домена по контексту объявления и текста диалога.
- Автоответ через OpenAI.
- Детекция лидов по контакту.
- Отправка лид-карточек в Telegram.
- Webhook Telegram для feedback-тегов.
- API для чатов, лидов, игнор-логов и промпта.
- Документация: SUMMARY, TECH_SPEC, DECISIONS, OPERATIONS.
- Dockerfile и docker-compose.
- VPS-шаблоны: Caddy + PostgreSQL compose.
- Базовые тесты роутера и лид-детектора.
- CLI и Makefile для validate-env/poll-once/run.
- Diagnostic endpoint `GET /api/chats/external/{external_chat_id}/diagnostics` for incident triage.
- CLI command `chat-diagnostics` and Make target `make chat-diagnostics CHAT_ID=...`.
- Self-learning v1:
  - table `learning_examples` for learned rules/replies;
  - table `learning_reply_usage` for tracking applied learning version;
  - CLI commands `learn-once`, `learning-status`;
  - API endpoint `GET /api/learning/status`;
  - auto-run cycle from poller by configurable interval.
- Jino VPS deployment support:
  - guide `docs/DEPLOY_JINO.md`;
  - SQLite -> PostgreSQL migration script `scripts/migrate_sqlite_to_postgres.py`;
  - Make target `migrate-sqlite-to-postgres`.

### Changed
- Avito polling switched to `GET /messenger/v2/accounts/{user_id}/chats` with normalization of `last_message`.
- Added chat-history fallback for burst ingestion:
  - primary `GET /messenger/v3/.../messages`
  - fallback `GET /messenger/v1/.../messages` when v3 returns 402/404/405.
- Added retry guard: deterministic 4xx errors (except 429) are not retried.
- Avito text send payload aligned with official OpenAPI messenger schema:
  - `{"type":"text","message":{"text":"..."}}`
- Lead pipeline hardened: failures on summary/Telegram no longer fail whole event after reply was sent to Avito.
- Telegram errors now include API `description` in exception text for faster diagnosis.
- Message generation now supports prompt augmentation with learned examples (few-shot context).
- Canary learning rollout added (`active/stable`) with KPI-based rollback/promotion.

### Notes
- Интеграция Avito зависит от корректных endpoint URL/параметров в `.env`.
- На этапе MVP авто/прочие категории не обслуживаются.
- Подтверждено по runtime (16 Feb 2026): Avito send endpoint отвечает `200 OK` после обновления payload.
- Подтверждено по runtime (16 Feb 2026, Jino VPS):
  - прод-контур `app+db+caddy` поднят на домене `avito-bot-prod.woo.autos`;
  - TLS выпущен автоматически;
  - SQLite -> PostgreSQL миграция выполнена (280 строк);
  - выявлен блокер: OpenAI c VPS возвращает `403 unsupported_country_region_territory`.
- Подтверждено по runtime (17 Feb 2026, Jino VPS):
  - LLM-маршрут переключен на OpenRouter (`OPENAI_BASE_URL=https://openrouter.ai/api/v1`);
  - модель: `openai/gpt-4o-mini`;
  - polling включен обратно;
  - прямой тест `chat/completions` с VPS вернул `HTTP 200`.
