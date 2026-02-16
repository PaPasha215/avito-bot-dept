# Operations Runbook

## Локальный запуск
1. Скопировать `.env.example` в `.env`.
2. Указать ключи Avito/OpenAI/Telegram.
3. Запустить: `docker compose up --build` или `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

## Проверка health
- `GET /healthz` должен вернуть `status=ok`.

## Что проверять ежедневно
1. `GET /api/logs/ignored` — нет ли ложных игноров.
2. `GET /api/leads` — лиды появляются и не дублируются.
3. Логи сервиса — нет ли повторяющихся ошибок Avito/OpenAI/Telegram.

## Telegram webhook
- Endpoint: `POST /webhooks/telegram`
- Рекомендуется включить `TELEGRAM_WEBHOOK_SECRET`.

## Команда обратной связи
В Telegram можно отправить:
`/feedback <external_chat_id> <TAG> <comment>`

Поддерживаемые `TAG`:
- `WRONG_DOMAIN`
- `WRONG_FACT`
- `WRONG_TONE`
- `MISSED_LEAD`

## Перенос на VPS (Beget)
1. Создать сервер и установить Docker/Compose.
2. Перенести проект и `.env`.
3. Поменять `DATABASE_URL` на PostgreSQL.
4. Настроить reverse proxy + TLS.
5. Поднять сервис и проверить `/healthz`.

### Готовые шаблоны в проекте
- `/Users/home/Documents/Bot/deploy/docker-compose.vps.yml`
- `/Users/home/Documents/Bot/deploy/Caddyfile`
