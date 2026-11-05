@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Музыкальный бот

echo ============================================================
echo  Музыкальный бот ВКонтакте для Telegram
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [!] Python не найден.
    echo     Установите его с https://www.python.org/downloads/
    echo     и при установке ОБЯЗАТЕЛЬНО поставьте галочку
    echo     "Add python.exe to PATH". Потом запустите этот файл снова.
    echo.
    pause
    exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo [1/3] Первый запуск: устанавливаю нужные компоненты.
    echo       Это займёт пару минут...
    python -m venv venv
    if errorlevel 1 goto :fail
)

venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :fail

if not exist ".env" (
    echo.
    echo [2/3] Настройка
    venv\Scripts\python.exe setup_env.py
    if errorlevel 1 goto :fail
)

venv\Scripts\python.exe ensure_settings.py

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo.
    echo [i] ffmpeg не найден. Большинство песен будет работать и без него,
    echo     но некоторые не отправятся. Установить можно командой:
    echo     winget install Gyan.FFmpeg
    echo.
)

echo.
echo [3/3] Бот запущен! Откройте его в Telegram и напишите название песни.
echo       Чтобы остановить бота - закройте это окно.
echo.
venv\Scripts\python.exe -m app.bot
echo.
echo Бот остановлен.
pause
exit /b 0

:fail
echo.
echo [!] Что-то пошло не так. Сделайте снимок экрана этого окна
echo     и покажите его Claude.
pause
exit /b 1
