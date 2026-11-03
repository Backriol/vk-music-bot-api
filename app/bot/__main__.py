"""Запуск бота отдельным процессом: python -m app.bot"""
import asyncio

from app.bot.runner import run_standalone

if __name__ == "__main__":
    asyncio.run(run_standalone())
