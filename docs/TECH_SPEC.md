# Technical Specification v1.1

Дата: 2026-02-16

## Стек
- Python 3.12
- FastAPI
- SQLAlchemy 2
- SQLite (локально), PostgreSQL (VPS)
- Docker Compose

## Компоненты
1. `AvitoClient`: polling входящих сообщений + отправка ответов.
2. `RouterService`: решение `REPLY` или `IGNORE_SILENT`.
3. `DomainClassifier`: классификация домена (эвристики + OpenAI fallback).
4. `OpenAIClient`: генерация ответа и summary.
5. `LeadDetector`: извлечение контакта.
6. `TelegramClient`: отправка карточек лидов.
7. `MessageProcessor`: оркестрация полного цикла.
8. `Poller`: периодический запуск цикла каждые 10 секунд.
9. `SelfLearningService`: генерация обучающих примеров + canary/promotion/rollback.
10. `StatsReportingService`: агрегирует статистику объявлений через Avito `stats/v2` и отправляет weekly Telegram-отчет по расписанию.

## Avito API (актуализировано 2026-02-16)
- Список API берется из `https://developers.avito.ru/web/1/openapi/list`.
- Swagger мессенджера: `https://developers.avito.ru/web/1/openapi/info/messenger`.
- Чтение чатов: `GET /messenger/v2/accounts/{user_id}/chats`.
- Догрузка истории для burst-сообщений: `GET /messenger/v3/accounts/{user_id}/chats/{chat_id}/messages` с fallback на `GET /messenger/v1/.../messages`.
- Отправка ответа: `POST /messenger/v1/accounts/{user_id}/chats/{chat_id}/messages`.
- Формат текстового сообщения:
  - `{"type":"text","message":{"text":"..."}}`

## Основной поток
1. Poller получает batch событий Avito.
2. Каждое событие проходит idempotency check (`events_log`).
3. Для входящего user-сообщения сохраняется `ad/chat/message`.
4. Router принимает решение:
   - `IGNORE_SILENT` для нецелевых категорий/уверенного non-real-estate.
   - `REPLY` для real-estate ветки.
   - при burst нескольких входящих подряд в одном чате за цикл poller отправляется один ответ (по последнему сообщению), а все входящие сохраняются в историю.
5. При `REPLY`:
   - сначала применяются детерминированные guardrails (без LLM) для типовых сценариев:
     - короткий бюджет (`8-12`, `8р-12р`, `812`) -> уточнение "тысяч рублей в месяц";
     - "куда вам набрать" -> ответ про многоканальную линию + запрос контакта;
     - если клиент уже явно пишет про заселение/наличие/даты, бот сразу продолжает квалификацию (без повтора "актуально?");
     - short-stay допускается только для 3 хостелов (Куйбышева 30, Мамина-Сибиряка 132, Ботаническая 30), иначе бот переводит в помесячный сценарий;
     - токсичные сообщения (оскорбления/угрозы/обвинения) -> `IGNORE_SILENT`;
   - если guardrail не сработал, генерируется ответ OpenAI;
   - в системный промпт добавляется runtime-контекст объявления (`ad_title/ad_category`) и фокус текущего диалога;
   - в системный промпт подмешиваются релевантные self-learning примеры;
   - ответ отправляется в Avito;
   - сохраняется в `bot_replies/messages`.
6. Если найден контакт:
   - создается лид (дедуп по `chat_id + contact_normalized`);
   - отправляется карточка в Telegram.
   - при ошибке summary/Telegram основной event не фейлится, чтобы избежать дублей ответа в Avito.
7. Фоновая аналитика:
   - `StatsReportingService.run_if_due()` запускается после poll-цикла;
   - при наступлении расписания (понедельник 09:00, локальный TZ) отправляет 1 отчет за неделю;
   - период отчета: предыдущая календарная неделя (пн-вс);
   - данные: `stats/v2` + мета item (`core/v1/accounts/{user_id}/items/{item_id}/`);
   - фильтрация: только объявления недвижимости;
   - отправляет отчет в Telegram на русском языке.

## API
- `GET /healthz`
- `GET /api/learning/status`
- `POST /webhooks/telegram`
- `GET /api/chats`
- `GET /api/chats/{id}`
- `GET /api/chats/external/{external_chat_id}/diagnostics`
- `GET /api/leads`
- `GET /api/logs/ignored`
- `GET /api/prompts/real-estate`
- `PUT /api/prompts/real-estate`

## CLI
- `python3 -m app.cli stats-report-once` — одноразовая отправка отчета по объявлениям в Telegram.

## Схема БД
- `ads`
- `chats`
- `messages`
- `bot_replies`
- `leads`
- `routing_decisions`
- `feedback_events`
- `events_log`
- `learning_examples`
- `learning_reply_usage`
- `settings`
- `prompts`

## Self-learning cycle
0. При активном poller цикл запускается автоматически не чаще раза в `SELF_LEARNING_INTERVAL_HOURS` (default 24).
1. `learn-once` выбирает чаты с новыми лидами и/или feedback-тегами.
2. На основе диалогов формируются примеры:
   - `SUCCESS`: intent + хороший ответ + правило.
   - `FAIL`: intent + неудачный ответ + улучшенный ответ + правило.
3. Примеры пишутся в `learning_examples` с новой `version`.
4. `active_version` переводится в candidate (если уже есть stable).
5. Во время ответов сервис выбирает active/stable по canary-проценту.
6. В `learning_reply_usage` учитываются только ответы, где реально применены примеры (`examples_count > 0`).
7. По KPI candidate:
   - rollback при `WRONG_DOMAIN` выше порога;
   - promotion в stable при достаточном числе лидов и отсутствии `WRONG_DOMAIN`.

## Retention
- Период хранения: 90 дней.
- Очистка выполняется сервисом retention не чаще раза в 24 часа.

## Data migration utility
- Скрипт: `/Users/home/Documents/Bot/scripts/migrate_sqlite_to_postgres.py`
- Назначение: одноразовый перенос данных из локальной SQLite в PostgreSQL (VPS).

## Runtime ограничения (Jino VPS, 2026-02-16)
1. С текущего Jino egress OpenAI может отвечать `403 unsupported_country_region_territory`.
2. Для production в таком случае обязателен внешний OpenAI-совместимый endpoint через `OPENAI_BASE_URL` или смена VPS-региона.
3. Avito history API может быть частично ограничен подпиской (`402 Payment Required`), поэтому burst-инжест работает best-effort через fallback.

## UX/операционные правила Telegram (обновлено 2026-02-17)
1. Лид-карточки отправляются в компактном формате:
   - summary ограничивается по длине;
   - в карточке показываются последние 3 реплики.
2. В MVP не используется перегруженная inline-клавиатура для лид-карточек.
3. Отдельное ТЗ по рабочему интерфейсу менеджеров: `docs/TELEGRAM_MENU_2LEVEL_TZ.md`.
