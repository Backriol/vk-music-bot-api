#!/usr/bin/env bash
# Установка и обновление музыкального бота на сервере Ubuntu 22.04/24.04.
#
#   curl -fsSL https://raw.githubusercontent.com/Backriol/vk-music-bot-api/feature/telegram-bot/deploy/install.sh -o install.sh
#   sudo bash install.sh
#
# Что делает:
#   1. ставит Python, ffmpeg, git;
#   2. пробует поставить MongoDB (если её серверы недоступны — бот работает с файлом bot_data.json);
#   3. скачивает бота в /opt/musicbot и ставит зависимости;
#   4. забирает .env и bot_data.json из домашней папки (если вы их туда загрузили) или запускает настройку;
#   5. включает автозапуск: бот сам поднимается после сбоя и перезагрузки сервера.
# Повторный запуск обновляет бота до свежей версии и перезапускает его — данные не теряются.
set -euo pipefail

REPO="${REPO:-https://github.com/Backriol/vk-music-bot-api}"
BRANCH="${BRANCH:-feature/telegram-bot}"
DIR=/opt/musicbot
SERVICE=musicbot
HOME_DIR="${SUDO_USER:+$(getent passwd "$SUDO_USER" | cut -d: -f6)}"
HOME_DIR="${HOME_DIR:-$HOME}"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
    echo "Запустите от имени администратора: sudo bash install.sh"
    exit 1
fi

say "1/6 Устанавливаю Python, ffmpeg и git"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ffmpeg git curl gnupg ca-certificates >/dev/null

say "2/6 Устанавливаю MongoDB"
MONGO_OK=0
if command -v mongod >/dev/null 2>&1; then
    MONGO_OK=1
else
    . /etc/os-release
    if curl -fsSL --max-time 30 https://www.mongodb.org/static/pgp/server-8.0.asc \
            | gpg --batch --yes -o /usr/share/keyrings/mongodb-server-8.0.gpg --dearmor 2>/dev/null; then
        echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] https://repo.mongodb.org/apt/ubuntu ${VERSION_CODENAME}/mongodb-org/8.0 multiverse" \
            > /etc/apt/sources.list.d/mongodb-org-8.0.list
        if apt-get update -qq && apt-get install -y -qq mongodb-org >/dev/null; then
            MONGO_OK=1
        else
            rm -f /etc/apt/sources.list.d/mongodb-org-8.0.list
            apt-get update -qq || true
        fi
    fi
fi
if [ "$MONGO_OK" = 1 ]; then
    # Небольшой сервер: ограничиваем память MongoDB, чтобы хватило и боту
    if [ -f /etc/mongod.conf ] && ! grep -q cacheSizeGB /etc/mongod.conf; then
        sed -i 's/^storage:/storage:\n  wiredTiger:\n    engineConfig:\n      cacheSizeGB: 0.25/' /etc/mongod.conf
    fi
    systemctl enable --now mongod >/dev/null 2>&1 || true
    for _ in $(seq 1 30); do
        mongosh --quiet --eval 'db.runCommand({ping:1}).ok' >/dev/null 2>&1 && break
        sleep 1
    done
    if mongosh --quiet --eval 'db.runCommand({ping:1}).ok' >/dev/null 2>&1; then
        echo "MongoDB работает."
    else
        warn "MongoDB установилась, но не запустилась (возможно, процессор сервера не поддерживает AVX)."
        MONGO_OK=0
    fi
else
    warn "Серверы MongoDB недоступны с этого сервера. Бот будет хранить данные в файле bot_data.json."
fi

say "3/6 Скачиваю бота в $DIR"
id -u musicbot >/dev/null 2>&1 || useradd --system --home-dir "$DIR" --shell /usr/sbin/nologin musicbot
if [ -d "$DIR/.git" ]; then
    git -C "$DIR" fetch -q --depth 1 origin "$BRANCH"
    git -C "$DIR" reset -q --hard FETCH_HEAD
else
    mkdir -p "$DIR"
    git clone -q --depth 1 -b "$BRANCH" "$REPO" "$DIR.tmp"
    cp -a "$DIR.tmp/." "$DIR/"
    rm -rf "$DIR.tmp"
fi

say "4/6 Устанавливаю зависимости (пару минут)"
[ -x "$DIR/venv/bin/python" ] || python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install -q --disable-pip-version-check -r "$DIR/requirements.txt"

say "5/6 Настройки"
for f in .env bot_data.json; do
    if [ -f "$HOME_DIR/$f" ] && [ ! -f "$DIR/$f" ]; then
        mv "$HOME_DIR/$f" "$DIR/$f"
        echo "Перенёс $f из $HOME_DIR"
    fi
done
cd "$DIR"
if : </dev/tty 2>/dev/null; then
    if [ ! -f .env ]; then
        echo "Файла .env нет — запускаю настройку. Ответьте на вопросы:"
        venv/bin/python setup_env.py </dev/tty
    fi
    venv/bin/python ensure_settings.py </dev/tty
elif [ ! -f .env ]; then
    warn "Нет файла .env и некому ответить на вопросы. Загрузите .env и запустите скрипт ещё раз."
    exit 1
fi
# Подключаем MongoDB (или оставляем файл, если MongoDB не поставилась)
if [ "$MONGO_OK" = 1 ]; then
    if grep -q '^MONGO_URL=' .env; then
        sed -i 's|^MONGO_URL=.*|MONGO_URL=mongodb://127.0.0.1:27017|' .env
    else
        printf '\nMONGO_URL=mongodb://127.0.0.1:27017\n' >> .env
    fi
fi
chown -R musicbot:musicbot "$DIR"
chmod 600 .env

say "6/6 Включаю автозапуск"
cat > /etc/systemd/system/$SERVICE.service <<UNIT
[Unit]
Description=Telegram music bot
After=network-online.target mongod.service
Wants=network-online.target

[Service]
User=musicbot
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python -m app.bot
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable $SERVICE >/dev/null 2>&1
systemctl restart $SERVICE
sleep 8

if systemctl is-active --quiet $SERVICE; then
    say "Готово! Бот работает круглосуточно."
else
    warn "Бот не запустился. Последние строки журнала:"
fi
journalctl -u $SERVICE -n 15 --no-pager || true
cat <<'HELP'

Полезные команды:
  journalctl -u musicbot -f        — смотреть, что делает бот (выход: Ctrl+C)
  systemctl restart musicbot       — перезапустить бота
  sudo bash install.sh             — обновить бота до новой версии
HELP
