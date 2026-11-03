"""
Источник музыки: Яндекс Музыка (библиотека yandex-music).

Интерфейс совпадает с VKService: search_tracks() и get_audio_url(),
поэтому бот и API работают с любым источником одинаково.

ID трека в нашем формате: "<trackId>_<albumId>" (подходит под ту же
проверку, что и VK-шные ID). Для Яндекса превращается в "trackId:albumId".
"""
import asyncio
from dataclasses import dataclass
from typing import Optional

from yandex_music import ClientAsync

from app.core.config import settings


@dataclass
class YandexSong:
    title: str
    artist: str
    duration: int
    url: str


def _to_ym_id(track_id: str) -> str:
    tid, _, album = track_id.partition("_")
    return f"{tid}:{album}" if album and album != "0" else tid


def _to_our_id(track) -> str:
    album_id = track.albums[0].id if track.albums else 0
    return f"{track.id}_{album_id}"


def _cover(track) -> Optional[str]:
    uri = track.cover_uri or (track.albums[0].cover_uri if track.albums else None)
    return f"https://{uri.replace('%%', '400x400')}" if uri else None


def _artist(track) -> str:
    return ", ".join(a.name for a in (track.artists or []) if a.name) or "Unknown"


class YandexService:
    def __init__(self) -> None:
        self._client: Optional[ClientAsync] = None
        self._lock = asyncio.Lock()

    async def client(self) -> ClientAsync:
        async with self._lock:
            if self._client is None:
                self._client = await ClientAsync(settings.yandex_token).init()
            return self._client

    async def search_tracks(self, query: str, limit: int = 20):
        try:
            client = await self.client()
            result = await client.search(query, type_="track")
        except Exception as e:
            print(f"Yandex Music error: {e}")
            return []

        items = (result.tracks.results if result and result.tracks else []) or []
        tracks = []
        for t in items:
            if t.available is False:
                continue
            track_id = _to_our_id(t)
            tracks.append({
                "id": track_id,
                "title": t.title or "",
                "artist": _artist(t),
                "duration": (t.duration_ms or 0) // 1000,
                "cover_url": _cover(t),
                "url_api": f"/api/music/download/{track_id}",
            })
            if len(tracks) >= limit:
                break
        print(f"✅ Found {len(tracks)} tracks for query: {query}")
        return tracks

    async def get_audio_url(self, track_id: str) -> Optional[YandexSong]:
        try:
            client = await self.client()
            found = await client.tracks([_to_ym_id(track_id)])
            if not found:
                return None
            track = found[0]
            infos = await track.get_download_info_async(get_direct_links=True)
        except Exception as e:
            print(f"Yandex Music error: {e}")
            return None

        mp3 = [i for i in infos or [] if i.codec == "mp3" and i.direct_link]
        if not mp3:
            return None
        # Полные версии важнее превью, затем — лучший битрейт
        best = max(mp3, key=lambda i: (not i.preview, i.bitrate_in_kbps))
        return YandexSong(
            title=track.title or "",
            artist=_artist(track),
            duration=(track.duration_ms or 0) // 1000,
            url=best.direct_link,
        )


yandex_service = YandexService()
