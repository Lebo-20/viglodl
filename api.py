import httpx
import logging
import asyncio

logger = logging.getLogger(__name__)

BASE_VIGLOO = "https://drakula.dramabos.my.id/api/vigloo"
AUTH_CODE = "A8D6AB170F7B89F2182561D3B32F390D"

async def get_drama_detail(book_id: str):
    """Fetch drama detail from Vigloo API."""
    url = f"{BASE_VIGLOO}/drama/{book_id}"
    params = {"lang": "id", "code": AUTH_CODE}
    
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                if data.get("success") and "data" in data and "payload" in data["data"]:
                    payload = data["data"]["payload"]
                    
                    # Some endpoints might nest it under 'program', others (like detail) have it directly in 'payload'
                    detail = payload.get("program") or payload
                    
                    seasons = payload.get("seasons", [])
                    if not seasons and "seasons" in detail:
                        seasons = detail["seasons"]
                        
                    if seasons:
                        detail["seasonId"] = seasons[0].get("id")
                        
                    detail["_source"] = "vigloo"
                    detail["title"] = detail.get("title") or detail.get("subTitle") or ""
                    detail["intro"] = detail.get("description") or detail.get("synopsis") or ""
                    
                    # Poster handling
                    poster = detail.get("thumbnailExpanded")
                    if not poster and detail.get("thumbnails"):
                        poster = detail["thumbnails"][0].get("url")
                    detail["poster"] = poster or ""
                    
                    return detail
        except Exception as e:
            logger.error(f"Error fetching vigloo detail {book_id}: {e}")
            
    return None

async def get_all_episodes(book_id: str, detail=None):
    """Fetch all episodes for a drama from Vigloo API."""
    if not detail:
        detail = await get_drama_detail(book_id)
        
    if not detail:
        return []
        
    season_id = detail.get("seasonId")
    if not season_id:
        return []
            
    url = f"{BASE_VIGLOO}/drama/{book_id}/season/{season_id}/episodes"
    params = {"lang": "id", "code": AUTH_CODE}
    
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                payloads = data.get("data", {}).get("payloads", [])
                eps = []
                for i, p in enumerate(payloads):
                    # In some Vigloo responses, episode info is in 'extra'
                    extra = p.get("extra") or p
                    ep_num = extra.get("episodeNumber") or extra.get("ep") or extra.get("episodeIndex") or (i + 1)
                    
                    # Video ID might be nested in 'program' for some endpoints
                    video_id = p.get("id")
                    if not video_id and "program" in p:
                        video_id = p["program"].get("id")
                        
                    if video_id and ep_num is not None:
                        eps.append({
                            "_source": "vigloo",
                            "seasonId": season_id,
                            "ep": ep_num,
                            "videoId": video_id,
                            "episode": ep_num
                        })
                eps.sort(key=lambda x: int(x["episode"]))
                return eps
        except Exception as e:
            logger.error(f"Error fetching vigloo episodes {book_id}: {e}")
                
    return []

async def get_latest_dramas(pages=1, **kwargs):
    """Fetch latest dramas from Vigloo browse API."""
    all_dramas = []
    async with httpx.AsyncClient(timeout=30) as client:
        for page in range(1, pages + 1):
            url = f"{BASE_VIGLOO}/browse"
            params = {"lang": "id", "code": AUTH_CODE, "page": page}
            
            # Add type if provided (e.g. populersearch)
            browse_type = kwargs.get("types") or kwargs.get("type")
            if browse_type:
                # API usually expects a single type string
                if isinstance(browse_type, list) and browse_type:
                    params["type"] = browse_type[0]
                else:
                    params["type"] = browse_type
                    
            try:
                res = await client.get(url, params=params, timeout=15)
                if res.status_code == 200:
                    data = res.json()
                    payloads = data.get("data", {}).get("payloads", [])
                    for p in payloads:
                        # For browse, it is usually nested in 'program'
                        prog = p.get("program") or p
                        prog["_source"] = "vigloo"
                        prog["id"] = prog.get("id")
                        prog["bookName"] = prog.get("title")
                        all_dramas.append(prog)
            except Exception as e:
                logger.error(f"Vigloo browse page {page} error: {e}")
    return all_dramas
