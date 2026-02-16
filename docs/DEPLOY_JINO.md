# Deploy On Jino VPS

Дата: 2026-02-16

## 1. Что нужно в панели Jino

1. Нужен именно VPS (Linux с root-доступом), не обычный shared-хостинг.
2. Создайте VPS с Ubuntu 22.04/24.04.
3. Подготовьте домен/поддомен для API бота.

## 2. Подключение к серверу

```bash
ssh root@<SERVER_IP>
```

## 3. Установка Docker и Compose Plugin

Используйте официальный гайд Jino по Docker для Ubuntu.

После установки проверьте:

```bash
docker --version
docker compose version
```

## 4. Копирование проекта на сервер

```bash
cd /opt
git clone <YOUR_REPO_URL> Bot
cd /opt/Bot
```

## 5. Подготовка `.env` для VPS

```bash
cp .env.example .env
```

Обязательные правки:

1. Все API-ключи (`OPENAI`, `AVITO`, `TELEGRAM`).
2. `DATABASE_URL`:
   - `postgresql+psycopg://avito_ai:avito_ai_change_me@db:5432/avito_ai`
3. `POLLING_ENABLED=true`

## 6. Миграция локальной SQLite в PostgreSQL (опционально)

Если нужно перенести текущую историю/лиды:

1. С локального ПК отправьте файл БД:

```bash
scp /Users/home/Documents/Bot/data/app.db root@<SERVER_IP>:/opt/Bot/data/app.db
```

2. На сервере поднимите только PostgreSQL:

```bash
cd /opt/Bot
docker compose -f deploy/docker-compose.vps.yml up -d db
```

3. Запустите миграцию:

```bash
docker compose -f deploy/docker-compose.vps.yml run --rm app \
  python3 /app/scripts/migrate_sqlite_to_postgres.py \
  --src sqlite:////app/data/app.db \
  --dst postgresql+psycopg://avito_ai:avito_ai_change_me@db:5432/avito_ai \
  --replace
```

## 7. Запуск в проде

```bash
cd /opt/Bot
docker compose -f deploy/docker-compose.vps.yml up -d --build
```

Проверьте:

```bash
docker compose -f deploy/docker-compose.vps.yml ps
curl -skS https://<YOUR_DOMAIN>/healthz
```

## 8. HTTPS и домен

Вариант A (рекомендован): используйте встроенное HTTP(S) проксирование в панели Jino на сервис бота.

Вариант B: используйте Caddy из `deploy/docker-compose.vps.yml` и настройте `deploy/Caddyfile` под ваш домен.

## 9. Проверка самообучения

```bash
cd /opt/Bot
docker compose -f deploy/docker-compose.vps.yml run --rm app python3 -m app.cli learning-status
docker compose -f deploy/docker-compose.vps.yml run --rm app python3 -m app.cli learn-once
```

## 10. Полезные команды

```bash
cd /opt/Bot
docker compose -f deploy/docker-compose.vps.yml logs -f app
docker compose -f deploy/docker-compose.vps.yml logs -f db
docker compose -f deploy/docker-compose.vps.yml restart app
```

## 11. Известные runtime-блокеры (зафиксировано 2026-02-16)

1. Если OpenAI с VPS возвращает:
   - `{"error":{"code":"unsupported_country_region_territory",...}}`
   - это означает, что текущий egress IP сервера не поддерживается OpenAI.
2. Варианты решения:
   - смена VPS/egress на поддерживаемый регион;
   - использование OpenAI-совместимого endpoint через `OPENAI_BASE_URL` + ключ провайдера.
3. Для части чатов Avito history может вернуть `402 Payment Required` (по подписке API мессенджера);
   - сервис автоматически пытается fallback endpoint, но при ограничении API может быть доступно только последнее сообщение.
