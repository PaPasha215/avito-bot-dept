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

## Основной поток
1. Poller получает batch событий Avito.
2. Каждое событие проходит idempotency check (`events_log`).
3. Для входящего user-сообщения сохраняется `ad/chat/message`.
4. Router принимает решение:
   - `IGNORE_SILENT` для нецелевых категорий/уверенного non-real-estate.
   - `REPLY` для real-estate ветки.
5. При `REPLY`:
   - генерируется ответ OpenAI;
   - ответ отправляется в Avito;
   - сохраняется в `bot_replies/messages`.
6. Если найден контакт:
   - создается лид (дедуп по `chat_id + contact_normalized`);
   - отправляется карточка в Telegram.

## API
- `GET /healthz`
- `POST /webhooks/telegram`
- `GET /api/chats`
- `GET /api/chats/{id}`
- `GET /api/leads`
- `GET /api/logs/ignored`
- `GET /api/prompts/real-estate`
- `PUT /api/prompts/real-estate`

## Схема БД
- `ads`
- `chats`
- `messages`
- `bot_replies`
- `leads`
- `routing_decisions`
- `feedback_events`
- `events_log`
- `settings`
- `prompts`

## Retention
- Период хранения: 90 дней.
- Очистка выполняется сервисом retention не чаще раза в 24 часа.
