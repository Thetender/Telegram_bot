# The Tender — Telegram bot та Admin Dashboard

Telegram-бот для пошуку аукціонів, моніторингів і консультацій The Tender,
а також веб-адмінка. Працює паралельно з Viber-ботом і не змінює його.

Опис API майданчика: `docs/thetender_api_telegram.txt`.

Специфікація: 4 фінальні документи (Functional Requirements v1.4, UI/UX Flows v1.4,
Architecture & Data Model v1.4, API Mapping & Acceptance Criteria v1.2).

## Стек

Python 3.13 · FastAPI · aiogram 3 · PostgreSQL 16 · SQLAlchemy 2 + Alembic ·
React (Dashboard) · Docker Compose. Без Redis/Celery: черги та стан зберігаються
в PostgreSQL.

## Ітерації

| # | Що входить | Статус |
|---|---|---|
| 0 | Каркас, БД, міграції, health, Docker, CI, автодеплой | ✅ готово до тесту на сервері |
| 1 | Реєстрація телефоном, юр. повідомлення, головне меню, /menu, Допомога, маркетингові налаштування, запит на видалення даних, журнал подій | ✅ готово до тесту на сервері |
| 2 | Пошук аукціонів, чернетка, снапшоти, пагінація, трекінг кліків | ✅ готово до тесту (гілка `iteration-2`) |
| 3 | Консультації (клієнт і менеджер) | — |
| 4 | Admin Dashboard: вхід, Огляд, Користувачі, Консультації, ролі | — |
| 5 | Моніторинги та сповіщення (webhook, черга доставки) | — |
| 6 | Юридичні версії, повторне підтвердження, обробка видалення даних | — |
| 7 | Розсилки | — |
| 8 | Запуск у production | — |

## Структура

```
backend/
  app/
    api/            FastAPI: /health, Telegram webhook (далі: The Tender webhook, redirect, Dashboard API)
    bot/            aiogram: хендлери, клавіатури, тексти, FSM-сховище в PostgreSQL
    services/       бізнес-логіка (користувачі, події, юр. документи, приватність)
    integrations/   клієнт The Tender API + демо-режим (THETENDER_MOCK)
    workers/        воркер доставки повідомлень (Iteration 5)
    models/         моделі SQLAlchemy
    cli.py          службові команди (перший адмін, юр. документи)
  migrations/       Alembic
  tests/            тести на справжній PostgreSQL
dashboard/          React (Iteration 4)
docker/             Dockerfile, приклад конфігу nginx
scripts/            деплой, бекап, відновлення
docs/SERVER_SETUP.md  інструкція для сисадміна
```

## Локальна розробка

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp ../.env.example ../.env   # ENVIRONMENT=local, BOT_MODE=polling, токен ТЕСТОВОГО бота
export $(grep -v '^#' ../.env | xargs) DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/thetender_tg
alembic upgrade head
python -m app.polling        # бот у режимі long polling
```

Тести (потрібен PostgreSQL):

```bash
TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/tg_test pytest -q
```

## Середовища

| | test | prod |
|---|---|---|
| Бот | окремий тестовий бот | основний бот (новий токен) |
| Адреса | https://tg-test.thetender.com.ua | https://tg.thetender.com.ua |
| The Tender API | https://sandbox.mxuser.com | https://thetender.com.ua |
| Каталог на сервері | /opt/thetender-telegram/test | /opt/thetender-telegram/prod |
| Порт (лише 127.0.0.1) | 8083 | 8082 |
| Деплой | автоматично після merge в `main` | вручну, з тегом релізу |

Test-середовище технічно не може звернутися до production The Tender API
(перевірка при старті).

## Службові команди

```bash
cd /opt/thetender-telegram/test
docker compose -p tg-test exec app python -m app.cli grant-admin --phone +380671234567
docker compose -p tg-test exec app python -m app.cli list-admins
docker compose -p tg-test exec app python -m app.cli publish-legal \
  --type TERMS --version 1.0 --url https://thetender.com.ua/terms --stored-ref terms-v1.0.pdf
```

## Технічні рішення за замовчуванням (дозволено специфікацією)

- Telegram → сервіс: webhook із секретом `X-Telegram-Bot-Api-Secret-Token`;
  локально — long polling.
- Кожен Telegram `update_id` записується в БД до обробки; повтори ігноруються.
- Стан розмов (FSM) зберігається в PostgreSQL (`bot_sessions`), переживає перезапуски.
- Телефон нормалізується до формату E.164 (`+380…`).
- Під час реєстрації фіксується ознайомлення з активними версіями Умов і Політики.
- Першого адміністратора призначає CLI-команда `grant-admin`.
- Демо-режим `THETENDER_MOCK=true` (лише test): вигадані аукціони, щоб тестувати пошук до отримання sandbox-ключа. У prod заборонений.
- Посилання на аукціони йдуть через підписаний редірект `/r/<token>` (лічильник кліків AUCTION_OPENED); HEAD-запити та прев'ю-боти не рахуються.
- Регіони — фіксований список із 27 назв з документа API; у REST передаються через кому.
- Тексти розділів «Допомога» — тимчасові заглушки в `backend/app/bot/texts.py`.
