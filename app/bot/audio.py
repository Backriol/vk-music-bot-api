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


# ---------- Отрывок песни для игры «Угадай мелодию» ----------

_BITRATES = {  # MPEG-1 Layer III и MPEG-2/2.5 Layer III, кбит/с
    1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0],
    2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
}


def _frame_bitrate(h: bytes) -> int:
    """Битрейт кадра MP3 по 4 байтам заголовка, 0 — если это не заголовок кадра."""
    if len(h) < 4 or h[0] != 0xFF or (h[1] & 0xE0) != 0xE0:
        return 0
    version = (h[1] >> 3) & 0b11      # 3 — MPEG-1, 2 — MPEG-2, 0 — MPEG-2.5, 1 — запрещено
    layer = (h[1] >> 1) & 0b11        # 1 — Layer III
    bitrate_idx = (h[2] >> 4) & 0x0F
    rate_idx = (h[2] >> 2) & 0b11
    if version == 1 or layer != 1 or bitrate_idx in (0, 15) or rate_idx == 3:
        return 0
    return _BITRATES[1 if version == 3 else 2][bitrate_idx]


def make_clip(mp3: bytes, seconds: int = 15, start_ratio: float = 0.35) -> bytes:
    """
    Вырезает кусок MP3 примерно с середины песни (там обычно припев).
    Начало файла с тегами (название, исполнитель, обложка) отбрасывается,
    поэтому Telegram не покажет, что это за песня.
    """
    start = 0
    if mp3[:3] == b"ID3" and len(mp3) > 10:
        size = (mp3[6] << 21) | (mp3[7] << 14) | (mp3[8] << 7) | mp3[9]
        start = 10 + size + (10 if mp3[5] & 0x10 else 0)
    body_end = len(mp3) - (128 if mp3[-128:-125] == b"TAG" else 0)
    pos = start + int((body_end - start) * start_ratio)
    # Ищем ближайший настоящий кадр: два подряд корректных заголовка
    while pos < body_end - 4:
        kbps = _frame_bitrate(mp3[pos:pos + 4])
        if kbps:
            frame_len = 144 * kbps * 1000 // 44100  # приблизительно, нужно лишь для проверки
            nxt = mp3[pos + frame_len: pos + frame_len + 4] if frame_len > 0 else b""
            if any(_frame_bitrate(mp3[pos + frame_len + d: pos + frame_len + d + 4]) for d in (0, 1)) or not nxt:
                return mp3[pos:min(body_end, pos + kbps * 1000 // 8 * seconds)]
        pos += 1
    return mp3[start:start + 192 * 1000 // 8 * seconds]
