# Avito AI Assistant (MVP v1.1)

Локальный сервис на FastAPI для автоответов в Avito по недвижимости, с игнорированием нецелевых категорий и отправкой лидов в Telegram.

## Основные функции MVP

- Автоответ по категории `REAL_ESTATE`
- Полный игнор чатов из `AUTO/OTHER`
- Классификация по контексту объявления и диалога
- Детекция контакта (телефон/мессенджер)
- Отправка карточки лида в Telegram
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

## Обязательные переменные `.env` для рабочего контура

- `OPENAI_API_KEY`
- `AVITO_CLIENT_ID`
- `AVITO_CLIENT_SECRET`
- `AVITO_UPDATES_URL`
- `AVITO_SEND_MESSAGE_URL_TEMPLATE`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_LEADS_CHAT_ID`

Дополнительно рекомендуется:

- `TELEGRAM_WEBHOOK_SECRET`
- `AVITO_CHAT_CONTEXT_URL_TEMPLATE`
- `DATABASE_URL` (PostgreSQL на VPS)

## Docker

```bash
docker compose up --build
```

## Основные endpoints

- `GET /healthz`
- `POST /webhooks/telegram`
- `GET /api/chats`
- `GET /api/chats/{id}`
- `GET /api/leads`
- `GET /api/logs/ignored`
- `GET /api/prompts/real-estate`
- `PUT /api/prompts/real-estate`

## Важно

Реальные URL Avito API могут отличаться в зависимости от формата доступа. В `.env` все URL вынесены в конфиг (`AVITO_UPDATES_URL`, `AVITO_SEND_MESSAGE_URL_TEMPLATE`, и т.д.), чтобы адаптация не требовала изменения кода.

Для внутренней разметки ошибок в Telegram поддерживается команда:
`/feedback <external_chat_id> <TAG> <comment>`
где `TAG` один из: `WRONG_DOMAIN`, `WRONG_FACT`, `WRONG_TONE`, `MISSED_LEAD`.
