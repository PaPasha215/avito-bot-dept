# Avito AI Assistant (MVP v1.1)

Локальный сервис на FastAPI для автоответов в Avito по недвижимости, с игнорированием нецелевых категорий и отправкой лидов в Telegram.

## Основные функции MVP

- Автоответ по категории `REAL_ESTATE`
- Полный игнор чатов из `AUTO/OTHER`
- Классификация по контексту объявления и диалога
- Детекция контакта (телефон/мессенджер)
- Отправка карточки лида в Telegram
- Self-learning v1 (примеры из успешных/ошибочных диалогов)
- Weekly Telegram-отчет по статистике объявлений (Пн 09:00, предыдущая неделя)
- Детерминированные guardrails:
  - короткий бюджет (`8-12`, `8р-12р`, `812`) -> уточнение в тысячах/месяц;
  - "куда вам набрать" -> ответ без выдачи прямого номера;
  - если клиент уже явно пишет про заселение/наличие/даты, бот сразу продолжает квалификацию (без повторного "актуально?");
  - посуточка только для трех хостелов (Куйбышева 30, Мамина-Сибиряка 132, Ботаническая 30), для остальных объявлений — помесячно;
  - сообщения с оскорблениями/угрозами/обвинениями уходят в `IGNORE_SILENT`.
- Логи роутинга и истории сообщений
- Ретеншн 90 дней

## Быстрый запуск

1. Создайте файл `.env` на основе `.env.example`
2. Установите зависимости:

```bash
pip install -e .
```

3. Запустите сервис:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Либо через helper-script:

```bash
./scripts/run_local.sh
```

## Обязательные переменные `.env` для рабочего контура

- `OPENAI_API_KEY`
- `AVITO_CLIENT_ID`
- `AVITO_CLIENT_SECRET`
- `AVITO_USER_ID`
- `AVITO_UPDATES_URL`
- `AVITO_SEND_MESSAGE_URL_TEMPLATE`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_LEADS_CHAT_ID`

Дополнительно рекомендуется:

- `TELEGRAM_WEBHOOK_SECRET`
- `AVITO_CHAT_CONTEXT_URL_TEMPLATE`
- `AVITO_MESSAGES_URL_TEMPLATE`
- `AVITO_MESSAGES_FALLBACK_URL_TEMPLATE`
- `SELF_LEARNING_*` (параметры canary/rollback/promotion)
- `DATABASE_URL` (PostgreSQL на VPS)

## Docker

```bash
docker compose up --build
```

## Deploy On Jino VPS

Пошаговый гайд: `docs/DEPLOY_JINO.md`

## Полезные команды

```bash
make validate-env
make validate-youla-readiness
make poll-once
make learn-once
make learning-status
make stats-report-once
make chat-diagnostics CHAT_ID=<external_chat_id>
make run
```

Подробный формат через CLI:
`python3 -m app.cli chat-diagnostics --external-chat-id <external_chat_id> --limit 200`
Для Youla readiness:
`python3 -m app.cli validate-youla-readiness`

## Основные endpoints

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

## Важно

Реальные URL Avito API могут отличаться в зависимости от формата доступа. В `.env` все URL вынесены в конфиг (`AVITO_UPDATES_URL`, `AVITO_SEND_MESSAGE_URL_TEMPLATE`, и т.д.), чтобы адаптация не требовала изменения кода.

Для текущей конфигурации Avito используется:
- чтение чатов: `GET /messenger/v2/accounts/{user_id}/chats`
- чтение истории для burst-догрузки: `GET /messenger/v3/.../messages` с fallback на `GET /messenger/v1/.../messages`
- отправка сообщений: `POST /messenger/v1/accounts/{user_id}/chats/{chat_id}/messages`
- payload текстового сообщения: `{"type":"text","message":{"text":"..."}}`

Официальный источник схемы messenger:
- `https://developers.avito.ru/web/1/openapi/info/messenger`

Если Telegram возвращает `Bad Request: chat not found`, нужно:
1. Добавить бота в целевой чат.
2. Дать боту право писать.
3. Проверить правильность `TELEGRAM_LEADS_CHAT_ID`.

## Self-learning v1

- Автоматический режим:
  - при запущенном poller self-learning цикл запускается автоматически раз в `SELF_LEARNING_INTERVAL_HOURS` (по умолчанию 24 часа).
- Ночной цикл обучения:
  - `make learn-once`
- Источник обучения:
  - `SUCCESS`: чаты с лидом и без негативного feedback.
  - `FAIL`: чаты с тегами `/feedback` (`WRONG_DOMAIN`, `WRONG_FACT`, `WRONG_TONE`, `MISSED_LEAD`).
- Применение в ответах:
  - в системный промпт подмешиваются 2-5 релевантных примеров.
- Canary + rollback:
  - новая версия примеров идет как candidate;
  - если фиксируется `WRONG_DOMAIN` выше порога, active версия откатывается на stable;
  - если кандидат набирает минимум лидов без `WRONG_DOMAIN`, кандидат промоутится в stable.

Для внутренней разметки ошибок в Telegram поддерживается команда:
`/feedback <external_chat_id> <TAG> <comment>`
где `TAG` один из: `WRONG_DOMAIN`, `WRONG_FACT`, `WRONG_TONE`, `MISSED_LEAD`.
