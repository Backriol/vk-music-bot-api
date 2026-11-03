# 🎧 VK Music Bot API

Backend API для Telegram Mini App, позволяющий искать и скачивать музыку из ВКонтакте.

## 🚀 Возможности

- 🔍 **Поиск треков** с обложками альбомов
- ⬇️ **Скачивание MP3** напрямую с серверов VK
- 🎯 **Персональные рекомендации** на основе истории прослушиваний
- 🔐 **Безопасная авторизация** через Telegram WebApp InitData
- 📊 **История прослушиваний** для каждого пользователя
- 🤖 **Встроенный Telegram-бот**: поиск, отправка MP3 прямо в чат, рекомендации

## 📋 Требования

- Python 3.11+
- MongoDB
- FFmpeg (для конвертации аудио)

## 🛠 Установка

1. **Клонируйте репозиторий**

```bash
git clone <your-repo-url>
cd music-bot
```

2. **Создайте виртуальное окружение**

```bash
python3 -m venv venv
source venv/bin/activate  # На Windows: venv\Scripts\activate
```

3. **Установите зависимости**

```bash
pip install -r requirements.txt
```

4. **Настройте переменные окружения**

```bash
cp .env.example .env
# Отредактируйте .env и добавьте свои токены
```

5. **Запустите MongoDB**

```bash
# macOS (через Homebrew)
brew services start mongodb-community

# Linux
sudo systemctl start mongod

# Docker
docker run -d -p 27017:27017 mongo
```

## 🎮 Запуск

### Development режим

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Production режим

```bash
# с --workers > 1 выключите встроенного бота (RUN_BOT=false) и запустите его отдельно: python -m app.bot
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

## 🤖 Telegram-бот

Бот (aiogram 3) живёт в `app/bot/` и по умолчанию запускается вместе с API
(`RUN_BOT=true`). Он использует те же `VKService` и MongoDB, поэтому история
прослушиваний и рекомендации общие для бота и Mini App.

Что умеет:

- текстовое сообщение → список треков с пагинацией;
- нажатие на трек → MP3 в чат (HLS-потоки `m3u8` собираются через ffmpeg);
- `/recommend` → подборка по последнему прослушанному исполнителю;
- если задан `WEBAPP_URL`, показывает кнопку «🎧 Открыть плеер» и menu button Mini App.

Отправленные файлы кешируются по `file_id` (коллекция `tg_audio`), поэтому
повторная отправка трека мгновенная.

**Запуск отдельно от API** (обязательно при `uvicorn --workers > 1`, иначе
несколько процессов будут одновременно опрашивать Telegram):

```bash
# в .env: RUN_BOT=false
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
python -m app.bot
```

## 📚 Документация API

После запуска доступна по адресам:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## 🔑 Получение токенов

### Telegram Bot Token

1. Напишите [@BotFather](https://t.me/BotFather)
2. Создайте нового бота командой `/newbot`
3. Скопируйте полученный токен в `.env`

### VK Token

Приложения на vkhost.github.io (VK Admin, Kate Mobile) VK заблокировал
(`application is blocked`). Получите токен по логину и паролю:

```bash
python get_vk_token.py
```

Скрипт спросит логин, пароль и код подтверждения, проверит доступ к музыке
и выведет строки `VK_TOKEN=...` и `VK_USER_AGENT=...` для `.env`.

## 📁 Структура проекта

```
music-bot/
├── app/
│   ├── bot/            # Telegram-бот (aiogram)
│   ├── core/           # Конфигурация и база данных
│   ├── models/         # Pydantic схемы
│   ├── routers/        # API эндпоинты
│   ├── services/       # Бизнес-логика
│   └── main.py         # Точка входа
├── .env                # Переменные окружения (не в git)
├── .env.example        # Шаблон для .env
└── requirements.txt    # Зависимости
```

## 🌐 API Endpoints

### 🔐 Authentication

- `POST /api/auth/login` - Авторизация через Telegram
- `POST /api/auth/history` - Добавить трек в историю

### 🎵 Music

- `GET /api/music/search?q={query}` - Поиск треков
- `GET /api/music/download/{track_id}` - Скачать MP3
- `GET /api/music/recommendations` - Получить рекомендации

## 🐛 Troubleshooting

### MongoDB не подключается

```bash
# Проверьте статус
brew services list  # macOS
sudo systemctl status mongod  # Linux

# Проверьте порт
lsof -i :27017
```

### VK API возвращает пустые результаты

- Убедитесь, что используете **VK Admin** токен
- Проверьте, что токен не истек
- Попробуйте получить новый токен

## 📝 License

MIT

## 👨‍💻 Support

Telegram: [@traftret](https://t.me/traftret)

# VK-Music-Bot-API
