from fastapi import APIRouter, HTTPException, Query, Path
from fastapi.responses import RedirectResponse
from app.models.schemas import SearchResponse, Track
from app.services.source import music_service
from urllib.parse import unquote
import re

router = APIRouter(
    prefix="/music",
    tags=["🎵 Music"],
)

@router.get("/search", response_model=SearchResponse)
async def search(q: str = Query(..., description="Search query (artist, song title, or both)", example="Макс Корж")):
    """
    🔍 **Search for music tracks in VK**
    """
    if not q:
        raise HTTPException(status_code=400, detail="Empty query")
    
    tracks = await music_service.search_tracks(q, limit=20)
    return {"items": tracks}

@router.get("/download/{track_id}")
async def download(
    track_id: str = Path(..., description="Track ID in format 'ownerId_trackId'", example="371745449_456392423")
):
    """
    ⬇️ **Get direct musical link (Redirect)**
    
    Redirects to the direct VK audio URL (MP3 or HLS).
    This avoids downloading the file to the local server and fixes HLS segment errors.
    """
    # Валидация track_id (должен быть в формате owner_id_audio_id)
    if not re.match(r'^-?\d+_\d+$', track_id):
        # Игнорируем запросы сегментов .ts или левые ID
        raise HTTPException(status_code=400, detail="Invalid track ID format")

    song = await music_service.get_audio_url(track_id)
    
    if not song or not song.url:
        raise HTTPException(status_code=404, detail="Track not found or restricted")
        
    return RedirectResponse(url=song.url)
    
@router.get("/recommendations", response_model=SearchResponse)
async def recommendations(
    track_id: str = Query(None, description="Track ID to base recommendations on", example="371745449_456392423"),
    query: str = Query(None, description="Search query for recommendations", example="Макс Корж"),
    limit: int = Query(20, description="Maximum number of recommendations", ge=1, le=50)
):
    """
    🎯 **Get personalized music recommendations**
    
    Returns recommended tracks based on:
    - A specific track (via `track_id`)
    - A search query (via `query`)
    - Popular tracks (if neither is provided)
    
    **Parameters:**
    - `track_id` (optional): Get recommendations similar to this track
    - `query` (optional): Search for recommendations matching this query
    - `limit`: Number of tracks to return (1-50, default: 20)
    
    **Returns:**
    - List of recommended tracks with full metadata
    
    **Examples:**
    ```
    GET /api/music/recommendations?track_id=371745449_456392423
    GET /api/music/recommendations?query=Макс Корж&limit=10
    GET /api/music/recommendations (returns popular tracks)
    ```
    """
    if query:
        tracks = await music_service.search_tracks(query, limit)
    elif track_id:
        # Находим артиста по ID трека и ищем его песни
        song = await music_service.get_audio_url(track_id)
        if song:
            tracks = await music_service.search_tracks(song.artist, limit)
            # Убираем сам трек из выдачи
            tracks = [t for t in tracks if t['id'] != track_id]
        else:
            tracks = []
    else:
        # Fallback на популярное если ничего не задано
        tracks = await music_service.search_tracks("Top 100", limit)
        
    return {"items": tracks}
