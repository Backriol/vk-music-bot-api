"""
Получение MP3-файла трека для отправки в Telegram.

VK отдаёт либо прямую ссылку на .mp3, либо HLS-плейлист (index.m3u8).
Прямой MP3 скачиваем через aiohttp, HLS собираем в MP3 через ffmpeg.
"""
import asyncio
import shutil

import aiohttp

from app.core.config import settings

# Лимит Bot API на загрузку файлов — 50 МБ
MAX_FILE_SIZE = 50 * 1024 * 1024
DOWNLOAD_TIMEOUT = 120


class AudioDownloadError(Exception):
    pass


def is_hls(url: str) -> bool:
    return ".m3u8" in url


async def _download_direct(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
    headers = {"User-Agent": settings.vk_user_agent}
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise AudioDownloadError(f"Сервер вернул HTTP {resp.status}")
                data = bytearray()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    data.extend(chunk)
                    if len(data) > MAX_FILE_SIZE:
                        raise AudioDownloadError("Файл больше 50 МБ")
                return bytes(data)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise AudioDownloadError(f"Сеть: {type(e).__name__}") from e


async def _run_ffmpeg(url: str, codec_args: list[str]) -> bytes:
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-user_agent", settings.vk_user_agent,
        "-i", url,
        "-vn", *codec_args,
        "-f", "mp3", "pipe:1",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), DOWNLOAD_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise AudioDownloadError("ffmpeg: превышено время ожидания")
    if proc.returncode != 0 or not stdout:
        raise AudioDownloadError(f"ffmpeg: {stderr.decode(errors='ignore').strip()[:300]}")
    if len(stdout) > MAX_FILE_SIZE:
        raise AudioDownloadError("Файл больше 50 МБ")
    return stdout


async def _download_hls(url: str) -> bytes:
    if not shutil.which("ffmpeg"):
        raise AudioDownloadError("Для HLS-треков нужен установленный ffmpeg")
    try:
        # Сегменты VK обычно уже MP3 — копируем поток без перекодирования
        return await _run_ffmpeg(url, ["-c:a", "copy"])
    except AudioDownloadError:
        # Если поток не MP3 — перекодируем
        return await _run_ffmpeg(url, ["-c:a", "libmp3lame", "-b:a", "192k"])


async def fetch_mp3(url: str) -> bytes:
    if is_hls(url):
        return await _download_hls(url)
    return await _download_direct(url)
