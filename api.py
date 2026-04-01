import httpx
import logging
import asyncio

logger = logging.getLogger(__name__)

# ============================================
# SUMBER API 1 (PRIORITAS): iDrama API
# ============================================
BASE_IDRAMA = "https://idrama.dramabos.my.id"

# ============================================
# SUMBER API 2 (FALLBACK): Vigloo API
# ============================================
BASE_VIGLOO = "https://drakula.dramabos.my.id/api/vigloo"
AUTH_CODE = "A8D6AB170F7B89F2182561D3B32F390D"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}

# ============================================
# iDrama API Functions (Sesi 1 - Utama)
# ============================================

async def idrama_get_detail(drama_id: str):
    """Ambil detail drama dari iDrama API (sesi 1)."""
    url = f"{BASE_IDRAMA}/drama/{drama_id}"
    params = {"lang": "id", "code": AUTH_CODE}
    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
        try:
            res = await client.get(url, params=params)
            if res.status_code == 200:
                data = res.json()
                payload = data.get("data") or data
                if not payload:
                    return None

                title = payload.get("title") or payload.get("name") or ""
                intro = payload.get("description") or payload.get("intro") or payload.get("synopsis") or ""
                poster = payload.get("poster") or payload.get("cover") or payload.get("thumbnail") or ""
                ep_count = payload.get("episodeCount") or payload.get("totalEp") or 0

                return {
                    "_source": "idrama",
                    "id": str(drama_id),
                    "title": title,
                    "intro": intro,
                    "poster": poster,
                    "episodeCount": ep_count,
                    "_raw": payload
                }
        except Exception as e:
            logger.error(f"iDrama detail error {drama_id}: {e}")
    return None

async def idrama_get_episodes(drama_id: str, detail=None):
    """Ambil semua episode dari iDrama API (sesi 1)."""
    if not detail:
        detail = await idrama_get_detail(drama_id)
    if not detail:
        return []

    raw = detail.get("_raw", {})
    episodes_raw = raw.get("episodes") or []

    # Jika sudah ada di payload detail
    if episodes_raw:
        eps = []
        for ep in episodes_raw:
            ep_num = ep.get("ep") or ep.get("episode") or ep.get("episodeNumber") or ep.get("index")
            video_id = ep.get("id") or ep.get("videoId")
            if ep_num and video_id:
                eps.append({
                    "_source": "idrama",
                    "dramaId": str(drama_id),
                    "ep": int(ep_num),
                    "episode": int(ep_num),
                    "videoId": video_id
                })
        if eps:
            return sorted(eps, key=lambda x: x["episode"])

    return []

async def idrama_get_stream(drama_id: str, ep: int):
    """Dapatkan URL streaming episode dari iDrama API (sesi 1)."""
    url = f"{BASE_IDRAMA}/unlock/{drama_id}/{ep}"
    params = {"lang": "id", "code": AUTH_CODE}
    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
        try:
            res = await client.get(url, params=params)
            if res.status_code == 200:
                data = res.json()
                payload = data.get("data") or data
                m3u8 = (payload.get("url") or payload.get("m3u8") or
                        payload.get("stream") or payload.get("hls") or "")
                subtitle = (payload.get("subtitle") or payload.get("vtt") or
                           payload.get("subtitleUrl") or "")
                return {"m3u8": m3u8, "subtitle": subtitle}
        except Exception as e:
            logger.error(f"iDrama stream error {drama_id} ep{ep}: {e}")
    return None

async def idrama_get_latest(pages=1, **kwargs):
    """Ambil daftar drama terbaru dari iDrama API (sesi 1)."""
    all_dramas = []
    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
        for page in range(1, pages + 1):
            try:
                url = f"{BASE_IDRAMA}/home"
                params = {"lang": "id", "page": page}
                res = await client.get(url, params=params, timeout=15)
                if res.status_code == 200:
                    data = res.json()
                    items = (data.get("data") or data.get("payloads") or
                             data.get("list") or data.get("dramas") or [])
                    # Coba nested structure
                    if isinstance(items, dict):
                        items = items.get("payloads") or items.get("list") or []
                    for item in items:
                        prog = item.get("program") or item
                        drama_id = str(prog.get("id") or prog.get("dramaId") or "")
                        if not drama_id:
                            continue
                        title = prog.get("title") or prog.get("name") or ""
                        all_dramas.append({
                            "_source": "idrama",
                            "id": drama_id,
                            "title": title,
                            "bookName": title,
                            "poster": prog.get("poster") or prog.get("cover") or "",
                        })
            except Exception as e:
                logger.error(f"iDrama home page {page} error: {e}")
    return all_dramas


# ============================================
# Vigloo API Functions (Sesi 2 - Fallback)
# ============================================

async def get_drama_detail(book_id: str):
    """
    Ambil detail drama.
    Coba iDrama dulu (sesi 1), fallback ke Vigloo (sesi 2).
    """
    # Sesi 1: Coba iDrama
    idrama_result = await idrama_get_detail(book_id)
    if idrama_result and idrama_result.get("title"):
        logger.info(f"[iDrama] ✅ Detail berhasil: {idrama_result.get('title')}")
        return idrama_result

    # Sesi 2: Fallback ke Vigloo
    logger.info(f"[iDrama] ⚠️ Gagal, fallback ke Vigloo untuk {book_id}")
    url = f"{BASE_VIGLOO}/drama/{book_id}"
    params = {"lang": "id", "code": AUTH_CODE}

    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
        try:
            response = await client.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                if data.get("success") and "data" in data and "payload" in data["data"]:
                    payload = data["data"]["payload"]
                    detail = payload.get("program") or payload
                    seasons = payload.get("seasons", [])
                    if not seasons and "seasons" in detail:
                        seasons = detail["seasons"]
                    if seasons:
                        detail["seasonId"] = seasons[0].get("id")
                    detail["_source"] = "vigloo"
                    detail["title"] = detail.get("title") or detail.get("subTitle") or ""
                    detail["intro"] = detail.get("description") or detail.get("synopsis") or ""
                    poster = detail.get("thumbnailExpanded")
                    if not poster and detail.get("thumbnails"):
                        poster = detail["thumbnails"][0].get("url")
                    detail["poster"] = poster or ""
                    return detail
        except Exception as e:
            logger.error(f"Vigloo detail error {book_id}: {e}")

    return None

async def get_all_episodes(book_id: str, detail=None):
    """
    Ambil semua episode.
    Coba iDrama dulu (sesi 1), fallback ke Vigloo (sesi 2).
    """
    if not detail:
        detail = await get_drama_detail(book_id)
    if not detail:
        return []

    source = detail.get("_source", "vigloo")

    # Sesi 1: Dari iDrama
    if source == "idrama":
        eps = await idrama_get_episodes(book_id, detail)
        if eps:
            logger.info(f"[iDrama] ✅ {len(eps)} episode ditemukan")
            return eps
        logger.info(f"[iDrama] ⚠️ Tidak ada episode, fallback ke Vigloo")

    # Sesi 2: Vigloo
    season_id = detail.get("seasonId")
    if not season_id:
        return []

    url = f"{BASE_VIGLOO}/drama/{book_id}/season/{season_id}/episodes"
    params = {"lang": "id", "code": AUTH_CODE}

    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
        try:
            response = await client.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                payloads = data.get("data", {}).get("payloads", [])
                eps = []
                for i, p in enumerate(payloads):
                    extra = p.get("extra") or p
                    ep_num = extra.get("episodeNumber") or extra.get("ep") or (i + 1)
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
            logger.error(f"Vigloo episodes error {book_id}: {e}")

    return []

async def get_latest_dramas(pages=1, **kwargs):
    """
    Ambil daftar drama terbaru.
    Coba iDrama dulu (sesi 1), gabungkan dengan Vigloo (sesi 2).
    """
    all_dramas = []
    seen_ids = set()

    # Sesi 1: iDrama (prioritas utama)
    idrama_dramas = await idrama_get_latest(pages=pages)
    for d in idrama_dramas:
        did = str(d.get("id", ""))
        if did and did not in seen_ids:
            seen_ids.add(did)
            all_dramas.append(d)

    logger.info(f"[iDrama] {len(all_dramas)} drama ditemukan")

    # Sesi 2: Vigloo (tambahan / fallback)
    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
        for page in range(1, pages + 1):
            url = f"{BASE_VIGLOO}/browse"
            params = {"lang": "id", "code": AUTH_CODE, "page": page}
            browse_type = kwargs.get("types") or kwargs.get("type")
            if browse_type:
                params["type"] = browse_type[0] if isinstance(browse_type, list) else browse_type
            try:
                res = await client.get(url, params=params, timeout=15)
                if res.status_code == 200:
                    data = res.json()
                    payloads = data.get("data", {}).get("payloads", [])
                    for p in payloads:
                        prog = p.get("program") or p
                        did = str(prog.get("id") or "")
                        if did and did not in seen_ids:
                            seen_ids.add(did)
                            prog["_source"] = "vigloo"
                            prog["bookName"] = prog.get("title")
                            all_dramas.append(prog)
            except Exception as e:
                logger.error(f"Vigloo browse page {page} error: {e}")

    logger.info(f"[Total] {len(all_dramas)} drama (iDrama + Vigloo)")
    return all_dramas
