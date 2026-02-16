# Changelog

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

### Notes
- Интеграция Avito зависит от корректных endpoint URL/параметров в `.env`.
- На этапе MVP авто/прочие категории не обслуживаются.
