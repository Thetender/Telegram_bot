# Налаштування сервера для Telegram-бота The Tender

Інструкція для сисадміна. Виконується один раз. Спершу розгортаємо лише
**test**-середовище; **prod** — так само, пізніше (крок 9).

Принципи ізоляції від сайту та Viber-бота:
- усе працює в Docker-контейнерах, у системі встановлюється лише Docker;
- власна PostgreSQL у контейнері, БД сайту не використовується;
- ліміти CPU/RAM на кожен контейнер (налаштовуються в `.env`);
- контейнери слухають тільки `127.0.0.1`, назовні — лише через ваш nginx;
- деплой змінює тільки `/opt/thetender-telegram`.

> Якщо щось у цій інструкції не відповідає вашому серверу (Apache замість nginx,
> інший спосіб видачі SSL тощо) — напишіть, адаптуємо.

---

## 1. Docker (якщо ще не встановлено)

Офіційний репозиторій Docker для Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
docker --version && docker compose version
```

## 2. Користувач для деплою і каталог

```bash
sudo adduser --disabled-password --gecos "" tgdeploy
sudo usermod -aG docker tgdeploy
sudo mkdir -p /opt/thetender-telegram
sudo chown tgdeploy:tgdeploy /opt/thetender-telegram
```

> Група `docker` фактично дає права, близькі до root. Якщо це неприйнятно —
> напишіть, запропонуємо варіант з обмеженим sudo на один скрипт.

## 3. Доступ сервера до репозиторію (read-only deploy key)

```bash
sudo -iu tgdeploy
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_github -C "tgdeploy@thetender"
cat >> ~/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/id_github
  IdentitiesOnly yes
EOF
cat ~/.ssh/id_github.pub
```

Вміст `id_github.pub` додати в GitHub: репозиторій **Thetender/Telegram_bot →
Settings → Deploy keys → Add deploy key**, назва `server`, галочку «Allow write
access» **не ставити**.

```bash
ssh -T git@github.com   # відповісти "yes"; має привітати репозиторій
git clone git@github.com:Thetender/Telegram_bot.git /opt/thetender-telegram/test
```

## 4. Файл налаштувань `.env` для test

```bash
cd /opt/thetender-telegram/test
cp .env.example .env
chmod 600 .env
openssl rand -hex 32   # -> TELEGRAM_WEBHOOK_SECRET
openssl rand -hex 24   # -> POSTGRES_PASSWORD
nano .env
```

Заповнити:
- `ENVIRONMENT=test`, `BOT_MODE=webhook`, `APP_PORT=8081`
- `PUBLIC_BASE_URL=https://tg-test.thetender.com.ua`
- `TELEGRAM_BOT_TOKEN=` — токен **тестового** бота (передасть власник продукту
  особисто, не через месенджер/пошту)
- `TELEGRAM_WEBHOOK_SECRET=`, `POSTGRES_PASSWORD=` — згенеровані вище значення
- `THETENDER_BASE_URL=https://sandbox.mxuser.com`, `THETENDER_API_KEY=` — sandbox-ключ (можна пізніше)

## 5. DNS

A-записи на IP цього сервера:
- `tg-test.thetender.com.ua`
- `tg.thetender.com.ua` (для prod, можна одразу)

## 6. nginx і SSL

Приклад конфігу: `docker/nginx/thetender-telegram.conf.example`.

```bash
# спершу сертифікат (nginx має обслуговувати порт 80 для цих доменів)
sudo certbot certonly --nginx -d tg-test.thetender.com.ua
sudo cp /opt/thetender-telegram/test/docker/nginx/thetender-telegram.conf.example \
        /etc/nginx/sites-available/thetender-telegram.conf
sudo nano /etc/nginx/sites-available/thetender-telegram.conf   # прибрати PROD-блоки, доки немає сертифіката для tg.
sudo ln -s /etc/nginx/sites-available/thetender-telegram.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

`nginx -t` перевіряє конфіг перед застосуванням — сайт не постраждає від помилки.

## 7. Перевірка зв'язку з Telegram (IPv4/IPv6)

```bash
curl -4 -sS -o /dev/null -w "IPv4: %{http_code}\n" https://api.telegram.org
curl -6 -sS -o /dev/null -w "IPv6: %{http_code}\n" https://api.telegram.org
```

Достатньо, щоб працював хоча б один (очікується код 302 або 200). Результат
надішліть нам.

## 8. Перший запуск test

```bash
sudo -iu tgdeploy
cd /opt/thetender-telegram/test
scripts/deploy.sh test origin/main
```

Очікуваний результат: `Healthy: {"status":"ok",...}`. Перевірка ззовні:
`https://tg-test.thetender.com.ua/health`.

Після того як власник продукту зареєструється в тестовому боті (кнопка
«Поділитися телефоном»), призначити його адміністратором:

```bash
docker compose -p tg-test exec app python -m app.cli grant-admin --phone +380XXXXXXXXX
```

## 9. Бекапи

```bash
sudo -iu tgdeploy crontab -e
# додати рядок:
15 3 * * * /opt/thetender-telegram/test/scripts/backup_db.sh test >> /opt/thetender-telegram/backup.log 2>&1
```

Бекапи зберігаються в `/opt/thetender-telegram/backups/<env>` (14 днів). Якщо є
зовнішнє сховище (Hetzner Storage Box) — додамо копіювання туди.

## 10. Автоматичний деплой з GitHub (після того як test запрацював)

```bash
sudo -iu tgdeploy
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_actions -C "github-actions-deploy"
cat ~/.ssh/id_actions.pub >> ~/.ssh/authorized_keys
cat ~/.ssh/id_actions          # приватний ключ -> секрет DEPLOY_SSH_KEY
ssh-keyscan -H <IP_сервера>    # -> секрет DEPLOY_KNOWN_HOSTS
```

У GitHub: **Settings → Secrets and variables → Actions**:
- Secrets: `DEPLOY_HOST` (IP), `DEPLOY_USER` (`tgdeploy`), `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`
- Variables: `DEPLOY_ENABLED` = `true`

Після цього кожне схвалене оновлення (merge у `main`) автоматично
розгортається в test. Prod — лише вручну.

## 11. Prod (пізніше, перед запуском)

Ті самі кроки 3–9 з каталогом `/opt/thetender-telegram/prod`, `APP_PORT=8082`,
`ENVIRONMENT=prod`, доменом `tg.thetender.com.ua`, **новим** токеном основного
бота та production-ключем The Tender API.
