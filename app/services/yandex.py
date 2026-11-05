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


def _to_dicts(items, limit: int) -> list[dict]:
    tracks = []
    seen = set()
    for t in items:
        if t is None or t.available is False:
            continue
        track_id = _to_our_id(t)
        if track_id in seen:
            continue
        seen.add(track_id)
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
    return tracks


# Характер волны (настройка «diversity» в Яндекс Музыке)
WAVE_MODES = {
    "default": "🎲 Обычная",
    "favorite": "❤️ Любимое",
    "discover": "✨ Незнакомое",
    "popular": "🔥 Популярное",
}


# Настроение волны: настройка moodEnergy или отдельная станция занятия
MOODS = {
    "active": "🔥 Бодрое",
    "fun": "😊 Весёлое",
    "calm": "😌 Спокойное",
    "sad": "😢 Грустное",
    "sport": "🏃 Для спорта",
    "sleep": "😴 Для сна",
}
# Как найти станцию занятия в списке станций Яндекса и чем заменить, если не нашлась
ACTIVITY_WORDS = {"sport": ("спорт", "тренир", "бег"), "sleep": ("сон", "засып", "сна")}
ACTIVITY_FALLBACK = {"sport": "active", "sleep": "calm"}


class YandexService:
    """
    Все методы принимают token: аккаунт Яндекса, от имени которого делать запрос.
    None — основной аккаунт бота (YANDEX_TOKEN из .env).
    """

    def __init__(self) -> None:
        self._clients: dict[str, ClientAsync] = {}
        self._lock = asyncio.Lock()
        self._activities: Optional[dict[str, str]] = None

    async def client(self, token: Optional[str] = None) -> ClientAsync:
        token = token or settings.yandex_token
        async with self._lock:
            if token not in self._clients:
                self._clients[token] = await ClientAsync(token).init()
            return self._clients[token]

    def forget(self, token: str) -> None:
        self._clients.pop(token, None)

    async def account_info(self, token: str) -> dict:
        """Проверка токена пользователя: имя аккаунта и наличие Плюса."""
        client = await ClientAsync(token).init()
        me = client.me
        account = me.account if me else None
        return {
            "login": (account.login or account.display_name or "") if account else "",
            "has_plus": bool(me and me.plus and me.plus.has_plus),
        }

    async def search_tracks(self, query: str, limit: int = 20, token: Optional[str] = None):
        try:
            client = await self.client(token)
            result = await client.search(query, type_="track")
        except Exception as e:
            print(f"Yandex Music error: {e}")
            return []

        items = (result.tracks.results if result and result.tracks else []) or []
        tracks = _to_dicts(items, limit)
        print(f"✅ Found {len(tracks)} tracks for query: {query}")
        return tracks

    async def chart_tracks(self, limit: int = 50, token: Optional[str] = None) -> list[dict]:
        """Чарт Яндекс Музыки (Россия)."""
        try:
            client = await self.client(token)
            info = await client.chart("russia")
            shorts = info.chart.tracks if info and info.chart and info.chart.tracks else []
            items = [ts.track for ts in shorts if ts.track]
            if not items and shorts:  # треки пришли без подробностей — догружаем
                items = await client.tracks([ts.track_id for ts in shorts[:limit]])
        except Exception as e:
            print(f"Yandex Music chart error: {e}")
            return []
        return _to_dicts(items, limit)

    async def _activity_seed(self, mood: str) -> Optional[str]:
        """Ищет станцию «для спорта» / «для сна» среди станций Яндекса (один раз)."""
        if self._activities is None:
            self._activities = {}
            try:
                client = await self.client()
                for res in await client.rotor_stations_list(language="ru") or []:
                    st = res.station
                    if not st or not st.id or st.id.type != "activity":
                        continue
                    name = (st.name or "").lower()
                    for key, words in ACTIVITY_WORDS.items():
                        if key not in self._activities and any(w in name for w in words):
                            self._activities[key] = f"{st.id.type}:{st.id.tag}"
            except Exception as e:
                print(f"Yandex Music stations error: {e}")
        return self._activities.get(mood)

    async def my_mix(self, limit: int = 30, state: Optional[dict] = None, new: bool = True,
                     token: Optional[str] = None, seeds: Optional[list[str]] = None,
                     incognito: bool = False, mood: Optional[str] = None) -> tuple[list[dict], dict]:
        """
        Волна через сессии ротора — так же, как в приложении Яндекс Музыки.

        seeds — на чём строить волну: по умолчанию «Моя волна» аккаунта (user:onyourwave),
        либо, например, ["track:123", "track:456"] — волна по любимым песням человека.
        incognito — не записывать прослушивания в историю аккаунта.
        state хранит id сессии, уже присланные треки (played) и режим (mode);
        played передаётся Яндексу, чтобы он не повторял песни.
        Возвращает (треки, новый state).
        """
        state = dict(state or {})
        played: list[str] = list(state.get("played", []))
        mode = state.get("mode", "default")
        base = list(seeds or ["user:onyourwave"])
        extra = [f"settingDiversity:{mode}"] if mode in WAVE_MODES and mode != "default" else []
        if mood in ACTIVITY_WORDS:
            station = await self._activity_seed(mood)
            if station:
                base = [station]  # волна станции «для спорта» / «для сна»
            else:
                extra.append(f"settingMoodEnergy:{ACTIVITY_FALLBACK[mood]}")
        elif mood in MOODS:
            extra.append(f"settingMoodEnergy:{mood}")

        items, ids = [], set(played)

        def take(sequence) -> int:
            added = 0
            for seq in sequence or []:
                t = seq.track
                if t is None or t.available is False:
                    continue
                key = _to_ym_id(_to_our_id(t))
                if key in ids:
                    continue
                ids.add(key)
                items.append(t)
                added += 1
            return added

        def queue() -> list[str]:
            return (played + [_to_ym_id(_to_our_id(t)) for t in items])[-300:]

        try:
            client = await self.client(token)
            session_id = (None if new or state.get("seeds") != base or state.get("mood") != mood
                          else state.get("session_id"))
            if session_id is None:
                session_id, first = await self._new_session(client, base, extra, queue(), incognito)
                if session_id is None:
                    raise RuntimeError("сессия волны не создана")
                take(first)
            for _ in range(8):  # Яндекс отдаёт по несколько песен за запрос
                if len(items) >= limit:
                    break
                res = await client.rotor_session_tracks(session_id, queue=queue())
                if res is None or res.unknown_session or res.terminated:
                    session_id, first = await self._new_session(client, base, extra, queue(), incognito)
                    if session_id is None:
                        break
                    take(first)
                    continue
                if not take(res.sequence):
                    break
        except Exception as e:
            print(f"Yandex Music wave session error: {e} — пробую старый способ")
            try:
                client = await self.client(token)
                q = played[-1].split(":")[0] if played else None
                for _ in range(6):
                    res = await client.rotor_station_tracks(base[0], queue=q)
                    if not take(res.sequence if res else []):
                        break
                    q = items[-1].id
                    if len(items) >= limit:
                        break
            except Exception as e2:
                print(f"Yandex Music wave error: {e2}")
            session_id = None

        tracks = _to_dicts(items, limit)
        played = (played + [_to_ym_id(t["id"]) for t in tracks])[-500:]
        return tracks, {"session_id": session_id, "played": played, "mode": mode,
                        "seeds": base, "mood": mood}

    async def _new_session(self, client, base: list[str], extra: list[str],
                           played: list[str], incognito: bool):
        """Возвращает (id сессии, первая порция треков) или (None, [])."""
        session, last_error = None, None
        # Сначала все сиды сразу; если Яндекс не примет такой набор — проще
        attempts = [[*base, *extra]]
        if len(base) > 1:
            attempts.append([base[0], *extra])
        if extra:
            attempts.append(list(base))  # без настроек характера и настроения
        for seeds in attempts:
            try:
                session = await client.rotor_session_new(
                    seeds=seeds, queue=played[-300:], include_tracks_in_response=True,
                    allow_explicit=True, incognito=incognito or None,
                )
                if session and session.radio_session_id:
                    break
            except Exception as e:
                last_error = e
                session = None
        if session is None or not session.radio_session_id:
            if last_error is not None:
                raise last_error
            return None, []
        try:
            await client.rotor_session_feedback_radio_started(
                session.radio_session_id, batch_id=session.batch_id, from_="telegram-bot",
            )
        except Exception:
            pass
        return session.radio_session_id, session.sequence or []

    async def get_audio_url(self, track_id: str, token: Optional[str] = None) -> Optional[YandexSong]:
        try:
            client = await self.client(token)
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
