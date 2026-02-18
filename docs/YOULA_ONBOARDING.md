# Youla Onboarding Plan (MVP)

Дата: 2026-02-18

## Цель
Подключить Youla как дополнительный канал недвижимости с теми же правилами диалога, что и в Avito-контуре.

## Текущее состояние
- В проекте Youla пока не активирован.
- Получены ссылки на Swagger:
  - https://partner-api.youla.ru/swagger/rapi
  - https://partner-api.youla.ru/swagger/ui
- Добавлен config-ready слой:
  - `YOULA_ENABLED`
  - `YOULA_MODE` (`feed_only` / `chat_api`)
  - `YOULA_API_BASE`
  - `YOULA_ACCOUNT_ID`
  - `YOULA_API_TOKEN`
  - `YOULA_UPDATES_URL`
  - `YOULA_SEND_MESSAGE_URL_TEMPLATE`
- Добавлена команда readiness-проверки:
  - `make validate-youla-readiness`

## Этапы запуска

### Этап 0 — Доступы и правовые рамки
1. Отправить запрос в поддержку Youla:
   - использовать шаблон `docs/YOULA_SUPPORT_REQUEST_TEMPLATE.md`.
2. Получить официальные docs + подтверждение правил автоответа.
3. Подтвердить, что чат API доступен именно для вашего аккаунта.
4. Проверить доступ к Swagger JSON:
   - `rapi` должен открываться без блокировки и отдавать спецификацию.

### Этап 1 — Режим `feed_only` (быстрый старт)
1. Использовать Youla как канал входящего трафика/объявлений.
2. Лиды обрабатывать в основном контуре (Avito + Telegram CRM-процесс).
3. KPI:
   - стабильный поток заявок;
   - контроль конверсии по карточкам.

### Этап 2 — Режим `chat_api` (после подтверждения API)
1. Включить `YOULA_ENABLED=true`, `YOULA_MODE=chat_api`.
2. Заполнить Youla env-переменные.
3. Прогнать `make validate-youla-readiness`.
4. Реализовать `YoulaAdapter` по контракту событий:
   - fetch updates;
   - get chat context;
   - send message.
5. Подключить canary (10-20% диалогов) и наблюдение.

## Правила качества (те же, что в Avito)
1. Отвечаем только по недвижимости.
2. В нецелевых категориях — `IGNORE_SILENT`.
3. Если клиент явно пишет по заселению — без повтора "актуально ли".
4. Цель диалога — квалификация + контакт.
5. Лиды в Telegram в формате summary + контакт.

## Команды
```bash
make validate-youla-readiness
make validate-env
make poll-once
```

## Что нужно от владельца (вы)
1. Ответ поддержки Youla по API.
2. Документация endpoint'ов.
3. Тестовые/боевые ключи.
4. Подтверждение, что автоответы разрешены в вашем типе аккаунта.
