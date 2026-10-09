# Налаштування сервера для Telegram-бота The Tender

Інструкція для сисадміна. Сервер: **RHEL 10**, Docker **rootless** від користувача
`tgdeploy`. Спершу розгортаємо лише **test**; **prod** — так само, пізніше (крок 10).

Принципи ізоляції від сайту та Viber-бота:
- усе працює в Docker-контейнерах rootless-Docker користувача `tgdeploy`;
- власна PostgreSQL у контейнері (іменований Docker volume), БД сайту не використовується;
- у `docker-compose.yml` немає монтування каталогів сервера, `privileged`, `network_mode: host`;
- ліміти CPU/RAM на кожен контейнер (налаштовуються в `.env`);
- порти лише на `127.0.0.1`: test — **8083**, prod — **8082**; назовні — тільки через nginx;
- деплой змінює тільки `/opt/thetender-telegram`.

## Уже зроблено
- [x] Docker (rootless) встановлено
- [x] Користувач `tgdeploy`, каталог `/opt/thetender-telegram`
- [x] Зв'язок з api.telegram.org: IPv4 працює (302), IPv6 немає — бот працює через IPv4

---

## 1. Доступ сервера до репозиторію

Deploy key (read-only) `tgdeploy@thetender` додано в GitHub-репозиторій.

```bash
sudo -iu tgdeploy
ssh -T git@github.com        # має привітати репозиторій
git clone git@github.com:Thetender/Telegram_bot.git /opt/thetender-telegram/test
```

## 2. Ліміти ресурсів у rootless Docker

Обмеження CPU у rootless-режимі працюють, лише якщо systemd делегує користувачам
контролер `cpu` (пам'ять і pids делегуються за замовчуванням). Перевірка:

```bash
sudo -iu tgdeploy
cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers
```

Якщо у виводі немає `cpu`:

```bash
sudo mkdir -p /etc/systemd/system/user@.service.d
printf '[Service]\nDelegate=cpu cpuset io memory pids\n' | sudo tee /etc/systemd/system/user@.service.d/delegate.conf
sudo systemctl daemon-reload
# далі перезапустити сесію/юніт користувача tgdeploy (або сервер у вікно обслуговування)
```

Якщо делегування вмикати не хочете — у `.env` встановіть `APP_CPUS=0` та `DB_CPUS=0`
(0 = без обмеження CPU); ліміти пам'яті при цьому залишаються.

## 3. Файл налаштувань `.env` для test

```bash
cd /opt/thetender-telegram/test
cp .env.example .env
chmod 600 .env
openssl rand -hex 32   # -> TELEGRAM_WEBHOOK_SECRET
openssl rand -hex 24   # -> POSTGRES_PASSWORD
nano .env
```

Заповнити:
- `ENVIRONMENT=test`, `BOT_MODE=webhook`, `APP_PORT=8083`
- `PUBLIC_BASE_URL=https://tg-test.thetender.com.ua`
- `TELEGRAM_BOT_TOKEN=` — токен **тестового** бота (передасть власник продукту особисто)
- `TELEGRAM_WEBHOOK_SECRET=`, `POSTGRES_PASSWORD=` — згенеровані вище значення
- `THETENDER_BASE_URL=https://sandbox.mxuser.com`, `THETENDER_API_KEY=` — можна пізніше

## 4. DNS

A-записи на `159.200.246.70`:
- `tg-test.thetender.com.ua` — потрібен зараз
- `tg.thetender.com.ua` — можна одразу або перед запуском prod

## 5. nginx і SSL (RHEL)

На RHEL конфіги nginx лежать у `/etc/nginx/conf.d/`. Приклад:
`docker/nginx/thetender-telegram.conf.example` (поки використовуйте лише TEST-блоки).

```bash
sudo certbot certonly --nginx -d tg-test.thetender.com.ua
sudo cp /opt/thetender-telegram/test/docker/nginx/thetender-telegram.conf.example \
        /etc/nginx/conf.d/thetender-telegram.conf
sudo nano /etc/nginx/conf.d/thetender-telegram.conf   # прибрати PROD-блоки до появи сертифіката tg.
sudo nginx -t && sudo systemctl reload nginx
```

SELinux: nginx має право проксувати на локальний порт. Якщо nginx уже проксує
на сокет-сервер сайту, усе налаштовано; інакше — `sudo setsebool -P httpd_can_network_connect 1`.

## 6. Перший запуск test

**Перший запуск — з гілки `iteration-0-1`** (код Iteration 0–1 ще на погодженні у
власника продукту; після погодження він потрапить у `main`):

```bash
sudo -iu tgdeploy
cd /opt/thetender-telegram/test
scripts/deploy.sh test origin/iteration-0-1
```

Скрипт робить `git fetch`, перемикається на вказану версію, збирає й запускає
контейнери (`docker compose -p tg-test up -d --build`) і чекає `/health`.
Очікуваний результат: `Healthy: {"status":"ok",...}`.
Зовнішня перевірка: `https://tg-test.thetender.com.ua/health`.

Після погодження ітерації всі наступні деплої test — з `main`:
`scripts/deploy.sh test origin/main` (або автоматично, крок 9).

## 7. Призначення адміністратора

Після того як власник продукту зареєструється в тестовому боті:

```bash
cd /opt/thetender-telegram/test
docker compose -p tg-test exec app python -m app.cli grant-admin --phone +380XXXXXXXXX
```

## 8. Бекапи

```bash
sudo -iu tgdeploy crontab -e
15 3 * * * /opt/thetender-telegram/test/scripts/backup_db.sh test >> /opt/thetender-telegram/backup.log 2>&1
```

Дампи — у `/opt/thetender-telegram/backups/<env>` (14 днів).

## 9. Автодеплой з GitHub Actions (після запуску test)

Workflow `.github/workflows/deploy.yml`:
- порт SSH береться із секрету `DEPLOY_PORT` (у вас `2222`);
- workflow **не викликає** скрипт із репозиторію: він лише надсилає по SSH рядок
  `"<env> <git-ref>"` (наприклад `test origin/main`). Ключ на сервері прив'язаний до
  вашого фіксованого скрипта (`command="..."` в `authorized_keys`), який читає
  `$SSH_ORIGINAL_COMMAND`, перевіряє його і виконує розгортання.

Мінімальна вимога до фіксованого скрипта: дозволити лише `test` або `prod` і git-ref
у форматі `origin/main` або тег `vX.Y.Z`, далі ті самі кроки, що й `scripts/deploy.sh`.

Секрети в GitHub (**Settings → Secrets and variables → Actions**):
`DEPLOY_HOST`, `DEPLOY_PORT`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`;
змінна `DEPLOY_ENABLED=true`. Деталі узгоджуємо після запуску test.

## 10. Prod (пізніше, перед запуском)

Ті самі кроки з каталогом `/opt/thetender-telegram/prod`, `APP_PORT=8082`,
`ENVIRONMENT=prod`, доменом `tg.thetender.com.ua`, **новим** токеном основного бота
та production-ключем The Tender API. Деплой prod — лише вручну, з тегом релізу.
