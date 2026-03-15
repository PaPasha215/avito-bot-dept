# План v1.1: усиление качества без риска для продакшна

Дата: 2026-03-01

## Цель

Улучшить качество ответов и конверсию в лиды без ломки стабильного рабочего контура Avito/Youla/Telegram/CRM.

## Принципы

- Никаких "больших" изменений в едином prompt без offline-проверки.
- Любое обучение только через контролируемые данные и canary.
- Новые механизмы включаются флагами и имеют rollback.
- Сначала метрики и quality gates, потом ML.

## Этап 1. Стабилизация качества

1. Ввести quality scorecard по дням:
   - reply rate
   - unanswered SLA
   - lead rate
   - contact capture rate
   - wrong-domain rate
   - CRM delivery success rate
   - Youla follow-up conversion
2. Добавить обязательные feedback-теги для операторов:
   - GOOD_REPLY
   - WRONG_DOMAIN
   - WRONG_FACT
   - WRONG_TONE
   - MISSED_LEAD
3. Перед отправкой ответа ввести cheap quality-check:
   - ответ по теме объявления
   - нет ложных обещаний
   - нет утечки контактов/адреса без условий
   - не более одного вопроса

## Этап 2. Управляемое самообучение

1. Учить только на подтвержденных кейсах:
   - успешные лиды
   - ручные корректировки менеджера
   - негативный feedback с исправленным ответом
2. Хранить lessons раздельно:
   - silent rules
   - tone rules
   - pricing rules
   - lead capture rules
   - marketplace-specific rules
3. Добавить минимальные offline-проверки для candidate-version:
   - не ухудшает wrong-domain
   - не увеличивает ignored real leads
   - не снижает lead rate на canary

## Этап 3. Retrieval-first вместо раннего fine-tuning

1. Искать похожие диалоги по:
   - типу объекта
   - сценарию клиента
   - маркетплейсу
   - стадии диалога
2. Подмешивать 1-2 лучших похожих кейса в prompt вместо широкого общего контекста.
3. Вести отдельную оценку, какие examples реально дали лид.

## Этап 4. Осторожный ML

ML имеет смысл только после накопления хорошего размеченного датасета.

Условия старта:

- не меньше 500-1000 размеченных сценариев;
- есть gold labels для quality;
- есть offline eval набор;
- есть canary и rollback.

Приоритетный ML-путь:

1. Сначала classifier/ranker для сценариев.
2. Потом model-based quality checker.
3. Fine-tune генерации только в последнюю очередь.

## Что не делать в v1.1

- Не включать автодообучение без human feedback.
- Не заменять rule-based guardrails "умной" генерацией.
- Не соединять продовый prompt и сырые данные обучения без canary.
- Не принимать рост лидов без контроля wrong-domain и wrong-fact.
