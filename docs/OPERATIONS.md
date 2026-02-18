# Operations Runbook

## Локальный запуск
1. Скопировать `.env.example` в `.env`.
2. Указать ключи Avito/OpenAI/Telegram.
3. Проверить env: `make validate-env`.
4. Запустить: `docker compose up --build` или `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

### Одноразовый цикл poller для диагностики
- `make poll-once`
- Для полного разбора одного чата:
  - `make chat-diagnostics CHAT_ID=<external_chat_id>`
- Для цикла самообучения:
  - `make learn-once`
- Для просмотра статуса самообучения:
  - `make learning-status`
- Для одноразового отчета по статистике объявлений:
  - `make stats-report-once`
- Автозапуск:
  - self-learning также запускается автоматически из poller раз в `SELF_LEARNING_INTERVAL_HOURS`.
- Для переноса данных SQLite -> PostgreSQL:
  - `make migrate-sqlite-to-postgres DST=<postgresql_url>`

## Проверка health
- `GET /healthz` должен вернуть `status=ok`.

## Что проверять ежедневно
1. `GET /api/logs/ignored` — нет ли ложных игноров.
2. `GET /api/leads` — лиды появляются и не дублируются.
3. Логи сервиса — нет ли повторяющихся ошибок Avito/OpenAI/Telegram.
4. После `learn-once` проверить настройки в БД:
   - `learning_active_version`
   - `learning_stable_version`
   - `learning_candidate_started_at`
   - либо запросом `GET /api/learning/status`.

## Self-learning: контроль canary
- Candidate запускается автоматически после появления новой версии примеров.
- Rollback условие:
  - `WRONG_DOMAIN` по candidate >= `SELF_LEARNING_ROLLBACK_WRONG_DOMAIN_THRESHOLD`.
- Promotion условие:
  - лидов по candidate >= `SELF_LEARNING_PROMOTE_MIN_LEADS`;
  - `WRONG_DOMAIN` по candidate = 0.

## Типовая проблема Avito (400 при отправке)
- Симптом: `POST /messenger/v1/accounts/{user_id}/chats/{chat_id}/messages` возвращает `400`.
- Проверенный рабочий payload для текста:
  - `{"type":"text","message":{"text":"..."}}`
- Проверенный источник схемы:
  - `https://developers.avito.ru/web/1/openapi/info/messenger`

## Типовая проблема Avito (пропуск одного из быстрых сообщений)
- Симптом: клиент отправил несколько сообщений подряд, в БД видно только последнее.
- Текущее поведение:
  1. Polling берет чаты из `v2/chats`.
  2. Для inbound `last_message` сервис пытается догрузить историю через `v3/messages`.
  3. Если `v3` недоступен (402/404/405), используется fallback `v1/messages`.
  4. Если оба endpoint не дают историю, сохраняется только `last_message` (ограничение API доступа).
- Что проверить:
  1. Логи `make poll-once` на предмет `402 Payment Required` по `.../messages`.
  2. Наличие реального сообщения в Avito API (не только в клиентском UI).
  3. Поступило ли сообщение после времени последнего успешного poll.

## Типовая проблема Telegram (400 chat not found)
- Симптом: Telegram API возвращает `Bad Request: chat not found`.
- Причина: бот не добавлен в чат или указан неверный `TELEGRAM_LEADS_CHAT_ID`.
- Проверка:
  1. Добавить бота в целевой чат/группу.
  2. Выдать боту право писать сообщения.
  3. Уточнить корректный `chat_id` (через `getUpdates` после сообщения боту/в группе).

## Telegram webhook
- Endpoint: `POST /webhooks/telegram`
- Рекомендуется включить `TELEGRAM_WEBHOOK_SECRET`.

## Youla webhook (chat_api)
- Endpoint: `POST /webhooks/youla`
- Обрабатывается только событие: `Ce-Type: message.incom`
- Авторизация исходящих в Youla:
  - `Authorization: Bearer <YOULA_API_TOKEN>`
- Перед включением:
  1. `YOULA_ENABLED=true`
  2. `YOULA_MODE=chat_api`
  3. `YOULA_API_BASE=https://partner-api.youla.ru`
  4. `YOULA_API_TOKEN=<token>`
  5. (опционально) `YOULA_WEBHOOK_SECRET=<secret>`

## Команда обратной связи
В Telegram можно отправить:
`/feedback <external_chat_id> <TAG> <comment>`

Поддерживаемые `TAG`:
- `WRONG_DOMAIN`
- `WRONG_FACT`
- `WRONG_TONE`
- `MISSED_LEAD`

## Точечные поведенческие правки (обновлено 2026-02-17)
Сервис применяет часть правил до LLM:
1. Короткий бюджет (`8-12`, `8р-12р`, `812`) -> бот уточняет, что это тысячи рублей в месяц.
2. Сообщения вида "куда вам набрать" -> бот просит контакт для обратного звонка и не выдает прямой номер менеджера.
3. Если клиент уже явно пишет про заселение/наличие/даты, бот не переспрашивает "актуально ли", а продолжает квалификацию.
4. Посуточный сценарий:
   - доступен только для хостелов: Куйбышева 30, Мамина-Сибиряка 132, Ботаническая 30;
   - для остальных объявлений бот предлагает помесячное размещение.
5. Сообщения с оскорблениями/угрозами/обвинениями обрабатываются как `IGNORE_SILENT`.

Если нужно поменять эти правила, правка вносится в код:
- `/Users/home/Documents/Bot/app/services/processor.py`
и затем деплой обычным способом.

## Отдельный UI-контур Telegram
- Для упрощения рабочего интерфейса менеджеров действует отдельное ТЗ:
  - `/Users/home/Documents/Bot/docs/TELEGRAM_MENU_2LEVEL_TZ.md`

## Weekly-отчет по статистике объявлений
1. Включить в `.env`:
   - `STATS_REPORTING_ENABLED=true`
   - `TELEGRAM_STATS_CHAT_ID=<id целевого чата>` (или fallback на `TELEGRAM_QA_CHAT_ID` / `TELEGRAM_LEADS_CHAT_ID`)
2. Базовые параметры:
   - `STATS_REPORT_WEEKDAY=1` (понедельник)
   - `STATS_REPORT_HOUR=9`
   - `STATS_REPORT_MINUTE=0`
   - `STATS_REPORT_TIMEZONE=Asia/Yekaterinburg`
   - `STATS_REPORT_MIN_VIEWS_FOR_CONVERSION=30`
   - `STATS_REPORT_LOW_CONVERSION_THRESHOLD=3.5`
3. Проверка вручную:
   - `make stats-report-once`
4. В автомате отчет запускается в процессе poller один раз в неделю по расписанию.

## Перенос на VPS (Beget)
1. Создать сервер и установить Docker/Compose.
2. Перенести проект и `.env`.
3. Поменять `DATABASE_URL` на PostgreSQL.
4. Настроить reverse proxy + TLS.
5. Поднять сервис и проверить `/healthz`.

### Готовые шаблоны в проекте
- `/Users/home/Documents/Bot/deploy/docker-compose.vps.yml`
- `/Users/home/Documents/Bot/deploy/Caddyfile`
- `/Users/home/Documents/Bot/docs/DEPLOY_JINO.md`

## Типовая проблема OpenAI на VPS (403 unsupported_country_region_territory)
- Симптом:
  - в логах `app` повторяется `HTTPStatusError 403` на `https://api.openai.com/v1/chat/completions`;
  - в теле ошибки `unsupported_country_region_territory`.
- Причина:
  - регион/egress IP VPS не поддерживается OpenAI.
- Диагностика:
  1. `docker compose -f deploy/docker-compose.vps.yml logs --tail=100 app`
  2. Проверить ручным вызовом из контейнера:
     - `docker compose -f deploy/docker-compose.vps.yml exec -T app python3 -c "import os,httpx; h={'Authorization':'Bearer '+os.getenv('OPENAI_API_KEY',''),'Content-Type':'application/json'}; p={'model':os.getenv('OPENAI_MODEL','gpt-4o-mini'),'messages':[{'role':'user','content':'ping'}],'max_tokens':5}; r=httpx.post('https://api.openai.com/v1/chat/completions',headers=h,json=p,timeout=20); print(r.status_code); print(r.text)"`
- Решение:
  1. Либо сменить egress/регион VPS на поддерживаемый OpenAI.
  2. Либо использовать OpenAI-совместимый endpoint через `OPENAI_BASE_URL` и отдельный ключ провайдера.
  3. После правки `.env`:
     - `docker compose -f deploy/docker-compose.vps.yml up -d --force-recreate app`
     - проверить логи и тестовый диалог.
