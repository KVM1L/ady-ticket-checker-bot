# ady-ticket-bot

Следит за наличием билетов Bakı ⇄ Tbilisi на [ticket.ady.az](https://ticket.ady.az/)
и присылает уведомление в Telegram при изменениях на ближайшие два месяца
(в любую из сторон):

- 🚆 появилась новая дата с билетами;
- 💰 изменилась цена на уже найденную дату;
- ❌ дата пропала из продажи (билеты распроданы).

Уведомление шлётся только при реальном изменении — если между проверками
ничего не поменялось, бот молчит.

## Как это работает

На сайте форма поиска — Vue-приложение. Когда в полях "Haradan"/"Haraya"
выбраны станции, сайт сам (ещё до нажатия "Axtar") дергает свой API
`POST /ticket-api/get_trip_dates` и подсвечивает в календаре даты, на которые
есть билеты, вместе с ценой. Именно этот запрос и его ответ мы читаем —
бот заполняет только Haradan/Haraya через настоящий браузер (Playwright)
и никогда не нажимает "Axtar".

Каждый такой запрос требует токен invisible reCAPTCHA, который сайт
генерирует сам в браузере — поэтому дергать API напрямую (через `requests`)
не получится, нужен настоящий (пусть и headless) браузер.

## Установка

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows (Git Bash) / на Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium
cp .env.example .env
```

Заполните `.env`:

- `TELEGRAM_BOT_TOKEN` — создайте бота через [@BotFather](https://t.me/BotFather), скопируйте токен.
- `TELEGRAM_CHAT_ID` — id чата/пользователя, куда слать уведомления. Проще всего
  написать своему боту любое сообщение, затем открыть
  `https://api.telegram.org/bot<TOKEN>/getUpdates` и взять `chat.id` оттуда.
- `POLL_INTERVAL_MINUTES` / `POLL_JITTER_MINUTES` — как часто опрашивать (по
  умолчанию раз в ~5 минут, со случайным разбросом +/-1 минута, чтобы не
  долбить сайт строго по таймеру). Учтите: чем чаще опрос, тем больше
  headless-браузер грузит сайт под Cloudflare — при проблемах с блокировкой
  первым делом увеличьте это значение.
- `LOOKAHEAD_DAYS` — горизонт в днях (по умолчанию 60 — те самые "два месяца").

## Запуск

Разовая проверка (без цикла, удобно для первого теста):

```bash
python -m ady_ticket_bot.main --once
```

Бесконечный цикл (то, что должно крутиться на сервере):

```bash
python -m ady_ticket_bot.main
```

Лог пишется в `data/ady_ticket_bot.log`, состояние уже увиденных дат — в
`data/state.json` (чтобы не слать одно и то же уведомление повторно).
Профиль браузера (cookies) хранится в `data/browser_profile` — так
Cloudflare реже видит "нового" посетителя при каждом опросе.

## Работа в фоне на сервере (systemd)

```bash
sudo useradd --system --create-home ady-bot
sudo mkdir -p /opt/ady-ticket-bot
sudo cp -r . /opt/ady-ticket-bot
cd /opt/ady-ticket-bot
sudo -u ady-bot python -m venv .venv
sudo -u ady-bot .venv/bin/pip install -r requirements.txt
sudo -u ady-bot .venv/bin/python -m playwright install --with-deps chromium
sudo cp deploy/ady-ticket-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ady-ticket-bot
sudo journalctl -u ady-ticket-bot -f
```

## Если Playwright не может скачать Chromium (гео-блок CDN)

`playwright install` качает браузер с `cdn.playwright.dev`, и в некоторых
странах/у некоторых хостеров этот CDN отвечает `403 Access denied ... not
available in your location`. Обход — использовать системный Google Chrome
вместо бандла Playwright:

```bash
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb
sudo apt-get install -y /tmp/chrome.deb   # тянет зависимости из уже настроенных репозиториев
```

Затем в `.env` укажите:

```
PLAYWRIGHT_CHANNEL=chrome
```

Бот подхватит системный Chrome вместо попытки скачать свой Chromium.

## Заметки

- Маршрут (Bakı ⇄ Tbilisi, id станций 232/170) захардкожен в
  `ady_ticket_bot/config.py` — поменяйте `BAKU`/`TBILISI`/`ROUTES`, если
  понадобится следить за другим направлением.
- Если верстка/API сайта изменится и селекторы перестанут находить элементы,
  бот залогирует ошибку и повторит попытку на следующем цикле — сам себя не
  уронит, но и уведомления слать перестанет, пока не поправите селекторы в
  `browser.py`.
