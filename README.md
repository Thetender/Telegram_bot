# The Tender — Telegram bot та Admin Dashboard

Telegram-бот для пошуку аукціонів, моніторингів і консультацій The Tender,
а також веб-адмінка. Працює паралельно з Viber-ботом і не змінює його.

Опис API майданчика: `docs/thetender_api_telegram.txt`.

Специфікація: 4 фінальні документи (Functional Requirements v1.4, UI/UX Flows v1.4,
Architecture & Data Model v1.4, API Mapping & Acceptance Criteria v1.2).

## Стек

Python 3.13 · FastAPI · aiogram 3 · PostgreSQL 16 · SQLAlchemy 2 + Alembic ·
серверні HTML-сторінки (Jinja2) для адмінки · Docker Compose. Без Redis/Celery: черги та стан зберігаються
в PostgreSQL.

## Ітерації

| # | Що входить | Статус |
|---|---|---|
| 0 | Каркас, БД, міграції, health, Docker, CI, автодеплой | ✅ готово до тесту на сервері |
| 1 | Реєстрація телефоном, юр. повідомлення, головне меню, /menu, Допомога, маркетингові налаштування, запит на видалення даних, журнал подій | ✅ готово до тесту на сервері |
| 2 | Пошук аукціонів, чернетка, снапшоти, пагінація, трекінг кліків | ✅ прийнято |
| 3 | Консультації (клієнт і менеджер) | ✅ прийнято |
| 4 | Admin Dashboard: вхід через Telegram, Огляд, Користувачі, Консультації, адміністратори/менеджери, юридичні документи, блокування доступу | ✅ прийнято |
| 5 | Моніторинги (створення, список, редагування, видалення) та сповіщення про нові аукціони (webhook, черга доставки) | 🧪 на тестуванні (гілка `iteration-5`) |
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
    admin/          веб-адмінка /admin (сторінки, вхід через Telegram, сесії)
    cli.py          службові команди (перший адмін, юр. документи)
  migrations/       Alembic
  tests/            тести на справжній PostgreSQL
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
| The Tender API | https://thetender.com.ua (тимчасово, `ALLOW_PRODUCTION_API_IN_TEST=true`) | https://thetender.com.ua |
| Вебхук моніторингів для The Tender | https://tg-test.thetender.com.ua/thetender/webhook | https://tg.thetender.com.ua/thetender/webhook |
| Каталог на сервері | /opt/thetender-telegram/test | /opt/thetender-telegram/prod |
| Порт (лише 127.0.0.1) | 8083 | 8082 |
| Деплой | автоматично після merge в `main` | вручну, з тегом релізу |

Test-середовище звертається до production The Tender API лише з явним
дозволом `ALLOW_PRODUCTION_API_IN_TEST=true` (перевірка при старті).

## Службові команди

```bash
cd /opt/thetender-telegram/test
docker compose -p tg-test exec app python -m app.cli grant-admin --phone +380671234567
docker compose -p tg-test exec app python -m app.cli grant-manager --phone +380671234567
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
- Консультації: одна відкрита заявка на клієнта (частковий унікальний індекс), захоплення менеджером — атомарний UPDATE «перший виграє»; без жодного менеджера, якому вдалося надіслати, заявка йде адмінам із увімкненими системними повідомленнями. Вихідні — субота/неділя за Києвом. Повторна доставка невдалих повідомлень менеджерам — у воркері Iteration 5.
- Адмінка — серверні HTML-сторінки (FastAPI + Jinja2) замість окремого React-застосунку: для 5 розділів простіше, безпечніше (httpOnly-сесія, CSRF, без окремого API) і без збірки фронтенду. Вхід — Telegram Login Widget; у @BotFather для бота треба вказати домен (Bot Settings → Domain → `tg.thetender.com.ua`).
- Тексти розділів «Допомога» — тимчасові заглушки в `backend/app/bot/texts.py`.
- Моніторинги: The Tender — джерело правди; локальна таблиця `monitorings` — індекс для
  адмінки й розсилок. Список у боті щоразу береться з API. Оновлення — повна заміна
  (усі поля, незадані = null). Створення не повторюється наосліп: при втраті відповіді
  бот звіряється зі списком моніторингів користувача.
- Редагування моніторингу — окрема чернетка (`monitoring_edit_drafts`), незавершений
  пошук не зачіпається; зміни застосовуються лише кнопкою «💾 Зберегти зміни».
- Вебхук The Tender: `POST /thetender/webhook?auth=<ключ Telegram API>`. Ключ перевіряється,
  у логах значення `auth` замінюється на `***`. Подія зберігається в
  `notification_deliveries` з `UNIQUE(canonical_auction_id, telegram_user_id)`
  (`canonical_auction_id` = `auction_data.number`) і відповідь 200 повертається одразу;
  повтори від The Tender безпечні.
- Воркер доставки працює в тому самому процесі: черга в PostgreSQL (`FOR UPDATE SKIP
  LOCKED`, оренда 5 хв для рядків, що «зависли»), ≈20 повідомлень/с загалом і 1/с на чат,
  пауза на `retry_after` при 429, до 8 спроб з наростаючою паузою (до 1 год) при
  тимчасових помилках. Невідомі, видалені й заблоковані адміністратором користувачі —
  SKIPPED (моніторинги на сайті зберігаються); якщо користувач заблокував бота — FAILED і
  позначка «недоступний».
