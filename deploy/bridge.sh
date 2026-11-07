#!/usr/bin/env bash
# «Мост» к Яндекс Музыке через сервер в России.
#
# Запускать на сервере, где работает бот (например, в Германии):
#   curl -fsSL https://raw.githubusercontent.com/Backriol/vk-music-bot-api/feature/telegram-bot/deploy/bridge.sh -o bridge.sh
#   sudo bash bridge.sh
#
# Что делает:
#   1. спрашивает IP российского сервера и (один раз) его пароль root;
#   2. ставит там маленький посредник tinyproxy, закрытый от интернета;
#   3. поднимает отсюда зашифрованный SSH-туннель к нему (сам переподключается);
#   4. включает в боте YANDEX_PROXY — через мост идут только запросы к Яндексу.
# Повторный запуск безопасен (можно сменить российский сервер).
set -euo pipefail

DIR=/opt/musicbot
KEY=/root/.ssh/yandex_bridge
CONF=/etc/musicbot-bridge.env
PORT=8888

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Запустите так: sudo bash bridge.sh"; exit 1; }
[ -d "$DIR" ] || { echo "Сначала установите бота: sudo bash install.sh"; exit 1; }

OLD_IP=""
[ -f "$CONF" ] && . "$CONF" && OLD_IP="${RU_IP:-}"
read -r -p "IP-адрес сервера в России${OLD_IP:+ [$OLD_IP]}: " RU_IP </dev/tty
RU_IP="${RU_IP:-$OLD_IP}"
[ -n "$RU_IP" ] || { echo "Нужен IP-адрес."; exit 1; }
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "root@$RU_IP")

say "1/4 Связываю серверы (один раз попросит пароль российского сервера)"
mkdir -p /root/.ssh && chmod 700 /root/.ssh
[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -f "$KEY" -C "musicbot-bridge"
if ! "${SSH[@]}" -o BatchMode=yes true 2>/dev/null; then
    echo "Введите пароль root российского сервера (при вводе не отображается):"
    ssh-copy-id -i "$KEY.pub" -o StrictHostKeyChecking=accept-new "root@$RU_IP" </dev/tty
fi
"${SSH[@]}" -o BatchMode=yes true || { warn "Не удалось войти на $RU_IP по ключу."; exit 1; }
echo "Серверы связаны."

say "2/4 Настраиваю посредник на российском сервере"
"${SSH[@]}" "bash -s" <<REMOTE
set -e
export DEBIAN_FRONTEND=noninteractive
if ! curl -s -o /dev/null --max-time 15 https://api.music.yandex.net; then
    echo "[!] С российского сервера тоже не видно Яндекс Музыку"; exit 2
fi
command -v tinyproxy >/dev/null || { apt-get update -qq; apt-get install -y -qq tinyproxy >/dev/null; }
C=/etc/tinyproxy/tinyproxy.conf
sed -i -E 's/^#?Port .*/Port $PORT/; s/^#?Listen .*/Listen 127.0.0.1/' "\$C"
grep -q '^Listen ' "\$C" || echo 'Listen 127.0.0.1' >> "\$C"
sed -i -E '/^Allow /d' "\$C"
echo 'Allow 127.0.0.1' >> "\$C"
systemctl enable tinyproxy >/dev/null 2>&1 || true
systemctl restart tinyproxy
echo "Посредник работает (виден только самому серверу)."
REMOTE

say "3/4 Поднимаю туннель"
printf 'RU_IP=%s\n' "$RU_IP" > "$CONF"
cat > /etc/systemd/system/yandex-bridge.service <<UNIT
[Unit]
Description=SSH tunnel to Yandex Music proxy in Russia
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/ssh -i $KEY -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=accept-new -L 127.0.0.1:$PORT:127.0.0.1:$PORT root@$RU_IP
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
mkdir -p /etc/systemd/system/musicbot.service.d
cat > /etc/systemd/system/musicbot.service.d/bridge.conf <<UNIT
[Unit]
After=yandex-bridge.service
Wants=yandex-bridge.service
UNIT
systemctl daemon-reload
systemctl enable yandex-bridge >/dev/null 2>&1
systemctl restart yandex-bridge
for _ in $(seq 1 15); do
    curl -s -o /dev/null --max-time 10 -x "http://127.0.0.1:$PORT" https://api.music.yandex.net && break
    sleep 1
done
if curl -s -o /dev/null --max-time 10 -x "http://127.0.0.1:$PORT" https://api.music.yandex.net; then
    echo "Туннель работает."
else
    warn "Туннель не заработал:"; journalctl -u yandex-bridge -n 10 --no-pager; exit 1
fi

say "4/4 Включаю мост в боте"
cd "$DIR"
if grep -q '^YANDEX_PROXY=' .env; then
    sed -i "s|^YANDEX_PROXY=.*|YANDEX_PROXY=http://127.0.0.1:$PORT|" .env
else
    printf '\nYANDEX_PROXY=http://127.0.0.1:%s\n' "$PORT" >> .env
fi
chown musicbot:musicbot .env
systemctl restart musicbot
sudo -u musicbot "$DIR/venv/bin/python" check_yandex.py && say "Готово! Яндекс Музыка работает через мост." \
    || warn "Мост поднят, но проверка Яндекса не прошла — пришлите этот текст Claude."
