"""Выбор источника музыки: Яндекс Музыка (если задан YANDEX_TOKEN) или ВКонтакте."""
from app.core.config import settings


def _pick():
    source = (settings.music_source or ("yandex" if settings.yandex_token else "vk")).lower()
    if source == "yandex":
        from app.services.yandex import yandex_service
        return yandex_service
    from app.services.vk import vk_service
    return vk_service


music_service = _pick()
